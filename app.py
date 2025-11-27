from flask import Flask, render_template, request, send_file, jsonify, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import os
import time
import io
import re
import zipfile
import requests
from pypdf import PdfWriter
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "segredo_zpl_max_final"

# --- CONFIGURAÇÕES DB ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# --- MODELO USUÁRIO ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)

with app.app_context():
    db.create_all()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- CONFIGURAÇÕES GERAIS ---
UPLOAD_FOLDER = 'uploads'
DOWNLOAD_FOLDER = 'downloads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

URL_API = 'http://api.labelary.com/v1/printers/8dpmm/labels/4x6/0/'
HEADERS = {'Accept': 'application/pdf'}

# --- MEMÓRIA DE PROGRESSO ---
PROGRESSO_POR_USUARIO = {}

# === ROTAS DE AUTH ===
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
            new_user = User(username=username, password=generate_password_hash(password, method='pbkdf2:sha256'))
            db.session.add(new_user)
            db.session.commit()
            return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# === ROTAS DO SISTEMA ===

@app.route('/')
@login_required
def index():
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
    global PROGRESSO_POR_USUARIO
    user_id = current_user.id
    
    try:
        PROGRESSO_POR_USUARIO[user_id] = {'status': 'lendo', 'atual': 0, 'total': 0}
        
        texto_colado = request.form.get('zpl_code')
        arquivo = request.files.get('file')
        conteudo = ""
        filename_base = "convertido"

        if arquivo and arquivo.filename != '':
            filename_base = os.path.splitext(arquivo.filename)[0]
            filepath = os.path.join(UPLOAD_FOLDER, arquivo.filename)
            arquivo.save(filepath)
            
            if arquivo.filename.lower().endswith('.zip'):
                try:
                    with zipfile.ZipFile(filepath, 'r') as z:
                        alvo = next((f for f in z.namelist() if f.lower().endswith(('.txt','.zpl'))), z.namelist()[0])
                        conteudo = z.read(alvo).decode('utf-8', errors='ignore')
                except:
                    return jsonify({'error': 'ZIP inválido'}), 400
            else:
                # --- CORREÇÃO DE SINTAXE AQUI ---
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        conteudo = f.read()
                except:
                    with open(filepath, 'r', encoding='latin-1') as f:
                        conteudo = f.read()
                        
        elif texto_colado:
            conteudo = texto_colado
        else:
            return jsonify({'error': 'Nada recebido'}), 400

        if len(conteudo) < 10:
            return jsonify({'error': 'Conteúdo curto'}), 400

        PROGRESSO_POR_USUARIO[user_id]['status'] = 'analisando'
        lista_envio = logica_hibrida_corrigida(conteudo)
        
        total = len(lista_envio)
        if total == 0:
            return jsonify({'error': 'Nenhuma etiqueta encontrada'}), 400

        PROGRESSO_POR_USUARIO[user_id] = {'status': 'convertendo', 'atual': 0, 'total': total}

        merger = PdfWriter()
        sucesso_count = 0
        
        for i, zpl in enumerate(lista_envio):
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
            nome_final = f"{filename_base}_{ts}.pdf"
            path = os.path.join(DOWNLOAD_FOLDER, nome_final)
            with open(path, "wb") as f:
                merger.write(f)
            
            PROGRESSO_POR_USUARIO[user_id]['status'] = 'concluido'
            return jsonify({'success': True, 'redirect': f'/download/{nome_final}'})
        else:
            PROGRESSO_POR_USUARIO[user_id]['status'] = 'erro'
            return jsonify({'error': 'Falha na API'}), 500

    except Exception as e:
        PROGRESSO_POR_USUARIO[user_id]['status'] = 'erro'
        return jsonify({'error': str(e)}), 500

@app.route('/download/<filename>')
@login_required
def download_file(filename):
    return send_file(os.path.join(DOWNLOAD_FOLDER, filename), as_attachment=True)

def logica_hibrida_corrigida(conteudo):
    lista = re.findall(r'(\^XA.*?\^XZ)', conteudo, re.DOTALL)
    if not lista: return []
    primeira = lista[0]
    
    if "^GFA" in primeira or ("~DGR" in primeira and "^XA" in primeira):
        return lista
    elif "~DGR" in conteudo:
        partes = conteudo.split('^XZ')
        validos = [p + "^XZ" for p in partes if len(p.strip()) > 5]
        final = []
        for i in range(0, len(validos), 2):
            p1 = validos[i]
            if i+1 < len(validos):
                final.append(p1 + "\n" + validos[i+1])
            else:
                final.append(p1)
        return final
    return lista

if __name__ == "__main__":
    app.run(debug=True, threaded=True)