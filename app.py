from flask import Flask, render_template, request, send_file, jsonify, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import os
import time
import io
import re
import zipfile
import requests
import mercadopago
from pypdf import PdfWriter
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "zpl_max_money_pro"

# --- MERCADO PAGO CONFIG ---
sdk = mercadopago.SDK("APP_USR-e97cc02f-0008-40aa-8339-d5e6d3ff6f4c")

# --- BANCO DE DADOS ---
database_url = os.environ.get('DATABASE_URL', 'sqlite:///database.db')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    is_paid = db.Column(db.Boolean, default=False)

with app.app_context():
    db.create_all()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

UPLOAD_FOLDER = 'uploads'
DOWNLOAD_FOLDER = 'downloads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

URL_API = 'http://api.labelary.com/v1/printers/8dpmm/labels/4x6/0/'
HEADERS = {'Accept': 'application/pdf'}
PROGRESSO_POR_USUARIO = {}

@app.route('/comprar')
@login_required
def comprar():
    preference_data = {
        "items": [{"title": "ZPL Max Premium", "quantity": 1, "unit_price": 29.90, "currency_id": "BRL"}],
        "back_urls": {
            "success": request.url_root,
            "failure": request.url_root,
            "pending": request.url_root
        },
        "auto_return": "approved",
        "external_reference": str(current_user.id)
    }
    preference_response = sdk.preference().create(preference_data)
    return redirect(preference_response["response"]["init_point"])

@app.route('/webhook', methods=['POST'])
def webhook():
    topic = request.args.get('topic')
    id = request.args.get('id')
    if topic == 'payment':
        payment_info = sdk.payment().get(id)
        if payment_info["response"]['status'] == 'approved':
            user_id = payment_info["response"]['external_reference']
            user = User.query.get(int(user_id))
            if user:
                user.is_paid = True
                db.session.commit()
    return jsonify({"status": "ok"}), 200

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('index'))
        else: 
            flash('Dados incorretos.')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if User.query.filter_by(username=username).first(): 
            flash('Usuário já existe.')
        else:
            new_user = User(username=username, password=generate_password_hash(password, method='pbkdf2:sha256'), is_paid=False)
            db.session.add(new_user)
            db.session.commit()
            return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    if not current_user.is_paid: 
        return render_template('pagamento.html')
    return render_template('index.html')

@app.route('/progress')
@login_required
def progress():
    user_id = current_user.id
    dados = PROGRESSO_POR_USUARIO.get(user_id, {'status': 'parado', 'atual': 0, 'total': 0})
    return jsonify(dados)

@app.route('/convert', methods=['POST'])
@login_required
def convert():
    if not current_user.is_paid: 
        return jsonify({'error': 'Pagamento pendente.'}), 403
        
    global PROGRESSO_POR_USUARIO
    user_id = current_user.id
    
    try:
        PROGRESSO_POR_USUARIO[user_id] = {'status': 'lendo', 'atual': 0, 'total': 0}
        texto = request.form.get('zpl_code')
        arquivo = request.files.get('file')
        conteudo = ""
        filename_base = "convertido"

        if arquivo and arquivo.filename != '':
            filename_base = os.path.splitext(arquivo.filename)[0]
            path = os.path.join(UPLOAD_FOLDER, arquivo.filename)
            arquivo.save(path)
            
            if arquivo.filename.lower().endswith('.zip'):
                try:
                    with zipfile.ZipFile(path, 'r') as z:
                        alvo = next((f for f in z.namelist() if f.lower().endswith(('.txt','.zpl'))), z.namelist()[0])
                        conteudo = z.read(alvo).decode('utf-8', errors='ignore')
                except: 
                    return jsonify({'error': 'ZIP inválido'}), 400
            else:
                # --- BLOCO CORRIGIDO E EXPANDIDO ---
                try: 
                    with open(path, 'r', encoding='utf-8') as f: 
                        conteudo = f.read()
                except: 
                    with open(path, 'r', encoding='latin-1') as f: 
                        conteudo = f.read()
                # -----------------------------------
                        
        elif texto: 
            conteudo = texto
        else: 
            return jsonify({'error': 'Vazio'}), 400
        
        if len(conteudo) < 10: 
            return jsonify({'error': 'Curto'}), 400

        PROGRESSO_POR_USUARIO[user_id]['status'] = 'analisando'
        lista = logica_hibrida(conteudo)
        total = len(lista)
        
        if total == 0: 
            return jsonify({'error': 'Sem etiquetas'}), 400
        
        PROGRESSO_POR_USUARIO[user_id] = {'status': 'convertendo', 'atual': 0, 'total': total}
        merger = PdfWriter()
        sucesso_count = 0
        
        for i, zpl in enumerate(lista):
            PROGRESSO_POR_USUARIO[user_id]['atual'] = i + 1
            tentativas = 0
            while tentativas < 3:
                try:
                    r = requests.post(URL_API, headers=HEADERS, data=zpl)
                    if r.status_code == 200:
                        merger.append(io.BytesIO(r.content))
                        sucesso_count += 1
                        break
                    elif r.status_code == 429: 
                        time.sleep(3)
                except: 
                    time.sleep(2)
                tentativas += 1
            time.sleep(0.5)
        
        PROGRESSO_POR_USUARIO[user_id]['status'] = 'finalizando'
        
        if sucesso_count > 0:
            ts = int(time.time())
            nome = f"{filename_base}_{ts}.pdf"
            with open(os.path.join(DOWNLOAD_FOLDER, nome), "wb") as f: 
                merger.write(f)
            
            PROGRESSO_POR_USUARIO[user_id]['status'] = 'concluido'
            return jsonify({'success': True, 'redirect': f'/download/{nome}'})
        else:
            PROGRESSO_POR_USUARIO[user_id]['status'] = 'erro'
            return jsonify({'error': 'Falha API'}), 500

    except Exception as e: 
        return jsonify({'error': str(e)}), 500

@app.route('/download/<filename>')
@login_required
def download_file(filename):
    return send_file(os.path.join(DOWNLOAD_FOLDER, filename), as_attachment=True)

def logica_hibrida(conteudo):
    lista = re.findall(r'(\^XA.*?\^XZ)', conteudo, re.DOTALL)
    if not lista: return []
    p1 = lista[0]
    
    if "^GFA" in p1 or ("~DGR" in p1 and "^XA" in p1): 
        return lista
    elif "~DGR" in conteudo: