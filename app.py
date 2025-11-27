from flask import Flask, render_template, request, send_file, jsonify
import os
import time
import io
import re
import zipfile
import requests
import threading
import uuid
from pypdf import PdfWriter

app = Flask(__name__)
app.secret_key = "zpl_max_pro_v2"

# --- CONFIGURAÇÕES ---
UPLOAD_FOLDER = 'uploads'
DOWNLOAD_FOLDER = 'downloads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

URL_API = 'http://api.labelary.com/v1/printers/8dpmm/labels/4x6/0/'
HEADERS = {'Accept': 'application/pdf'}

# Dicionário para guardar o progresso de cada usuário
# Ex: { 'id_unico': { 'status': 'convertendo', 'atual': 1, 'total': 10, 'arquivo': '...' } }
TASKS = {}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start', methods=['POST'])
def start_conversion():
    """Recebe o arquivo e inicia o trabalho em segundo plano"""
    texto_colado = request.form.get('zpl_code')
    arquivo = request.files.get('file')
    conteudo = ""
    filename_base = "convertido"

    # 1. LEITURA
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
            try:
                with open(filepath, 'r', encoding='utf-8') as f: conteudo = f.read()
            except:
                with open(filepath, 'r', encoding='latin-1') as f: conteudo = f.read()
    
    elif texto_colado:
        conteudo = texto_colado
    else:
        return jsonify({'error': 'Nada enviado'}), 400

    if len(conteudo) < 10:
        return jsonify({'error': 'Conteúdo inválido'}), 400

    # 2. PREPARAÇÃO
    lista_envio = logica_hibrida_corrigida(conteudo)
    if not lista_envio:
        return jsonify({'error': 'Nenhuma etiqueta encontrada'}), 400

    # 3. INICIA A THREAD (TRABALHO EM SEGUNDO PLANO)
    task_id = str(uuid.uuid4())
    TASKS[task_id] = {
        'status': 'iniciando',
        'atual': 0,
        'total': len(lista_envio),
        'filename': filename_base
    }

    # Dispara o robô para trabalhar enquanto o site responde
    thread = threading.Thread(target=processar_background, args=(task_id, lista_envio, filename_base))
    thread.start()

    return jsonify({'task_id': task_id})

@app.route('/status/<task_id>')
def check_status(task_id):
    """O site consulta essa rota para saber como está"""
    task = TASKS.get(task_id)
    if task:
        return jsonify(task)
    return jsonify({'error': 'Tarefa não encontrada'}), 404

@app.route('/download/<filename>')
def download_file(filename):
    return send_file(os.path.join(DOWNLOAD_FOLDER, filename), as_attachment=True)

def processar_background(task_id, lista_envio, filename_base):
    """A função que trabalha duro sem travar o site"""
    merger = PdfWriter()
    sucesso_count = 0
    total = len(lista_envio)

    for i, zpl in enumerate(lista_envio):
        # Atualiza o status global
        TASKS[task_id]['status'] = 'convertendo'
        TASKS[task_id]['atual'] = i + 1
        
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

    if sucesso_count > 0:
        ts = int(time.time())
        nome_final = f"{filename_base}_{ts}.pdf"
        caminho_final = os.path.join(DOWNLOAD_FOLDER, nome_final)
        
        with open(caminho_final, "wb") as f:
            merger.write(f)
        
        TASKS[task_id]['status'] = 'concluido'
        TASKS[task_id]['download_url'] = f"/download/{nome_final}"
    else:
        TASKS[task_id]['status'] = 'erro'
        TASKS[task_id]['msg'] = 'Falha na conversão.'

def logica_hibrida_corrigida(conteudo):
    lista_etiquetas = re.findall(r'(\^XA.*?\^XZ)', conteudo, re.DOTALL)
    if not lista_etiquetas: return []
    primeira = lista_etiquetas[0]
    
    if "^GFA" in primeira or ("~DGR" in primeira and "^XA" in primeira):
        return lista_etiquetas
    elif "~DGR" in conteudo:
        partes = conteudo.split('^XZ')
        validos = [p + "^XZ" for p in partes if len(p.strip()) > 5]
        final = []
        for i in range(0, len(validos), 2):
            p1 = validos[i]
            if i+1 < len(validos): final.append(p1 + "\n" + validos[i+1])
            else: final.append(p1)
        return final
    else:
        return lista_etiquetas

if __name__ == "__main__":
    app.run(debug=True, threaded=True)