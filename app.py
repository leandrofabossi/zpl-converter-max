from flask import Flask, render_template, request, send_file, jsonify
import os
import time
import io
import re
import zipfile
import requests
import uuid  # <--- O SEGREDO DOS NOMES ÚNICOS
from pypdf import PdfWriter

app = Flask(__name__)
app.secret_key = "zpl_max_seguro_final"

# --- CONFIGURAÇÕES ---
UPLOAD_FOLDER = 'uploads'
DOWNLOAD_FOLDER = 'downloads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

URL_API = 'http://api.labelary.com/v1/printers/8dpmm/labels/4x6/0/'
HEADERS = {'Accept': 'application/pdf'}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/convert', methods=['POST'])
def convert():
    # Variáveis para limpeza posterior
    caminho_entrada = None
    
    try:
        texto_colado = request.form.get('zpl_code')
        arquivo = request.files.get('file')
        conteudo = ""
        
        # Gera um ID único para este cliente (ninguém mais terá este ID)
        id_unico = str(uuid.uuid4())
        filename_saida = f"Etiquetas_{id_unico}.pdf"

        # 1. LEITURA SEGURA
        if arquivo and arquivo.filename != '':
            # Salva com o ID único, nunca com o nome original
            extensao = os.path.splitext(arquivo.filename)[1]
            nome_seguro = f"{id_unico}{extensao}"
            caminho_entrada = os.path.join(UPLOAD_FOLDER, nome_seguro)
            
            arquivo.save(caminho_entrada)
            
            if caminho_entrada.lower().endswith('.zip'):
                try:
                    with zipfile.ZipFile(caminho_entrada, 'r') as z:
                        alvo = next((f for f in z.namelist() if f.lower().endswith(('.txt','.zpl'))), z.namelist()[0])
                        conteudo = z.read(alvo).decode('utf-8', errors='ignore')
                except:
                    return jsonify({'error': 'Arquivo ZIP inválido.'}), 400
            else:
                try:
                    with open(caminho_entrada, 'r', encoding='utf-8') as f: conteudo = f.read()
                except:
                    with open(caminho_entrada, 'r', encoding='latin-1') as f: conteudo = f.read()
        
        elif texto_colado:
            conteudo = texto_colado
        else:
            return jsonify({'error': 'Nenhum conteúdo recebido.'}), 400

        if len(conteudo) < 10:
            return jsonify({'error': 'Conteúdo muito curto.'}), 400

        # 2. PREPARAÇÃO
        lista_envio = logica_hibrida_corrigida(conteudo)
        if not lista_envio:
            return jsonify({'error': 'Nenhuma etiqueta ZPL encontrada.'}), 400

        # 3. CONVERSÃO
        merger = PdfWriter()
        sucesso_count = 0
        
        for zpl in lista_envio:
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
            caminho_final = os.path.join(DOWNLOAD_FOLDER, filename_saida)
            
            with open(caminho_final, "wb") as f:
                merger.write(f)
            
            # Limpeza: Apaga o arquivo de entrada para não encher o disco
            if caminho_entrada and os.path.exists(caminho_entrada):
                os.remove(caminho_entrada)

            return jsonify({'success': True, 'redirect': f'/download/{filename_saida}'})
        else:
            return jsonify({'error': 'Falha ao converter as etiquetas.'}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download/<filename>')
def download_file(filename):
    # Envia o arquivo e depois pode apagar (opcional em sistemas robustos, aqui mantemos simples)
    return send_file(os.path.join(DOWNLOAD_FOLDER, filename), as_attachment=True)

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
            if i+1 < len(validos):
                final.append(p1 + "\n" + validos[i+1])
            else:
                final.append(p1)
        return final
    else:
        return lista_etiquetas

if __name__ == "__main__":
    app.run(debug=True)