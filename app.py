import os
import requests
from bs4 import BeautifulSoup
import urllib3
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import csv
import io
from flask import Flask, jsonify, send_from_directory, Response

# Desativa o aviso de certificado "Não seguro" do HTTPS da impressora
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# Adicione os IPs da sua rede aqui
IMPRESSORAS = [
    "10.80.8.50",
    "10.80.8.51",
    "10.80.8.52",
    "10.80.8.53",
    "10.80.8.54",
    "10.80.8.55",
    "10.80.8.56",
    "10.80.8.57",
    "10.80.8.58",
    "10.80.8.59",
    "10.80.8.60",
    "10.80.8.61",
    "10.80.8.62",
    "10.80.8.67",
    "10.80.8.70",
    "10.80.8.74",
    "10.80.8.75",
]

# MAPEAMENTO DE DIRETORIAS (Preencha conforme a sua rede)
MAPA_DIRETORIAS = {
    "10.80.8.50": "DEA",
    "10.80.8.51": "DAF - CACC",
    "10.80.8.52": "DAF - Assessoria (Color)",
    "10.80.8.53": "DJUR",
    "10.80.8.54": "DAF - CTPF",
    "10.80.8.55": "Presidência",
    "10.80.8.56": "Presidência",
    "10.80.8.57": "Comunicação / NAE",
    "10.80.8.58": "NPI",
    "10.80.8.59": "DAF - Assessoria",
    "10.80.8.60": "DGP",
    "10.80.8.61": "Protocolo",
    "10.80.8.62": "DGP",
    "10.80.8.67": "Integridade",
    "10.80.8.70": "DOP",
    "10.80.8.74": "DGOVI",
    "10.80.8.75": "DGOVI",
}

DB_HOST = os.getenv("DB_HOST", "10.80.8.127")
DB_NAME = os.getenv("DB_NAME", "db_impressoras")
DB_USER = os.getenv("DB_USER", "admin_impressoras")
DB_PASS = os.getenv("DB_PASS", "Rios@ude1234")
DB_PORT = os.getenv("DB_PORT", "5001")

def limpar_numero(texto):
    if not texto or texto == "N/A": return None
    try: return int("".join([c for c in texto.split(".")[0] if c.isdigit()]))
    except: return None

def extrair_valor_por_cor(soup, cor_ingles):
    for div in soup.find_all('div'):
        classes = div.get('class', [])
        if 'gauge' in classes and cor_ingles in classes:
            span = div.find('span')
            if span: return span.text.strip()
    return "N/A"

def extrair_dados_impressora(ip):
    resultado = {
        "ip": ip, "apelido": "Sem Apelido", "status": "Offline",
        "numero_serie": "N/A", "total_impressoes": "N/A", "consumivel_preto": "N/A", 
        "kit_adf": "N/A", "diretoria": MAPA_DIRETORIAS.get(ip, "Geral")
    }
    try:
        resp_usage = requests.get(f"https://{ip}/hp/device/InternalPages/Index?id=UsagePage", verify=False, timeout=15)
        soup_usage = BeautifulSoup(resp_usage.text, 'html.parser')
        
        elem_serie = soup_usage.find('strong', id="UsagePage.DeviceInformation.DeviceSerialNumber")
        if elem_serie: resultado["numero_serie"] = elem_serie.text.strip()
            
        elem_total = soup_usage.find('td', id="UsagePage.EquivalentImpressionsTable.Total.Total")
        if elem_total: resultado["total_impressoes"] = elem_total.text.strip()
            
        elem_apelido = soup_usage.find('strong', id="UsagePage.DeviceInformation.DeviceName")
        if elem_apelido: resultado["apelido"] = elem_apelido.text.strip()

        resp_status = requests.get(f"https://{ip}/hp/device/DeviceStatus/Index", verify=False, timeout=15)
        soup_status = BeautifulSoup(resp_status.text, 'html.parser')
        
        resultado["consumivel_preto"] = extrair_valor_por_cor(soup_status, "Black")
        resultado["kit_adf"] = extrair_valor_por_cor(soup_status, "Gray") 
        resultado["status"] = "Online"
    except:
        pass
    return resultado

# --- FUNÇÃO PRINCIPAL DE VARREDURA ---
def executar_varredura():
    dados_finais = [extrair_dados_impressora(ip) for ip in IMPRESSORAS]
    
    # Salva o JSON temporário
    with open('dados_impressoras.json', 'w', encoding='utf-8') as f:
        json.dump(dados_finais, f, ensure_ascii=False, indent=4)
        
    # Salva no Postgres e gera relatórios
    try:
        # A CORREÇÃO ESTÁ AQUI: Adicionámos o cursor_factory=RealDictCursor no final
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS, port=DB_PORT, cursor_factory=RealDictCursor)
        cur = conn.cursor()
        
        for imp in dados_finais:
            paginas = limpar_numero(imp["total_impressoes"])
            toner = limpar_numero(imp["consumivel_preto"])
            cur.execute("""
                INSERT INTO historico_impressoras (ip, apelido, status, numero_serie, total_impressoes, consumivel_preto, diretoria)
                VALUES (%s, %s, %s, %s, %s, %s, %s);
            """, (imp["ip"], imp["apelido"], imp["status"], imp["numero_serie"], paginas, toner, imp["diretoria"]))
        conn.commit()
        
        # Recalcula os relatórios após inserir os dados novos
        cur.execute("SELECT DISTINCT ON (ip) apelido, ip, diretoria, total_impressoes as total_atual, consumivel_preto as toner_atual FROM historico_impressoras WHERE status = 'Online' ORDER BY ip, data_registro DESC;")
        maquinas = cur.fetchall()
        
        cur.execute("SELECT diretoria, SUM(paginas) as total_impresso FROM (SELECT diretoria, ip, MAX(total_impressoes) - MIN(total_impressoes) as paginas FROM historico_impressoras WHERE data_registro >= NOW() - INTERVAL '7 days' AND total_impressoes IS NOT NULL GROUP BY diretoria, ip) as deltas GROUP BY diretoria ORDER BY total_impresso DESC;")
        uso_diretoria = cur.fetchall()
        
        cur.execute("WITH diarias AS (SELECT date_trunc('day', data_registro) as dia, SUM(total_impressoes) as total_dia FROM (SELECT DISTINCT ON (ip, date_trunc('day', data_registro)) total_impressoes, data_registro FROM historico_impressoras ORDER BY ip, date_trunc('day', data_registro), data_registro DESC) sub GROUP BY dia) SELECT to_char(dia, 'DD/MM') as data_label, total_dia - LAG(total_dia) OVER (ORDER BY dia) as delta_dia FROM diarias ORDER BY dia DESC LIMIT 7;")
        sazonalidade = cur.fetchall()
        
        with open('relatorio_impressoras.json', 'w', encoding='utf-8') as f:
            json.dump({"maquinas": maquinas, "uso_diretoria": uso_diretoria, "sazonalidade": sazonalidade[::-1]}, f, ensure_ascii=False, indent=4, default=str)
            
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Erro BD: {e}")

# ==========================================
# ROTAS DA API (FLASK)
# ==========================================

# 1. Rota para carregar o HTML
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

# 2. Rotas para entregar os arquivos JSON
@app.route('/api/dados')
def get_dados():
    return send_from_directory('.', 'dados_impressoras.json')

@app.route('/api/relatorios')
def get_relatorios():
    return send_from_directory('.', 'relatorio_impressoras.json')

# 3. Rota que OBRIGA o Python a ir nas impressoras na hora
@app.route('/api/atualizar', methods=['POST'])
def forcar_atualizacao():
    executar_varredura()
    return jsonify({"status": "sucesso", "mensagem": "Dados atualizados com sucesso!"})

# 4. NOVA ROTA: Extrair Relatório em CSV (Abre direto no Excel)
@app.route('/api/exportar')
def exportar_csv():
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS, port=DB_PORT)
        cur = conn.cursor()
        cur.execute("SELECT id, to_char(data_registro, 'DD/MM/YYYY HH24:MI:SS'), ip, apelido, diretoria, status, numero_serie, total_impressoes, consumivel_preto FROM historico_impressoras ORDER BY data_registro DESC;")
        linhas = cur.fetchall()
        cur.close()
        conn.close()

        # Monta o arquivo CSV na memória (Usamos ponto e vírgula para o Excel brasileiro entender as colunas)
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';')
        writer.writerow(['ID', 'Data/Hora', 'IP', 'Apelido', 'Diretoria', 'Status', 'Série', 'Total de Impressões', 'Toner Preto (%)'])
        for linha in linhas:
            writer.writerow(linha)
            
        # Retorna como arquivo para download (codificado em UTF-8 com BOM para o Excel ler acentos)
        return Response(
            u'\ufeff' + output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment;filename=historico_impressoras.csv"}
        )
    except Exception as e:
        return f"Erro ao gerar exportação: {e}", 500

if __name__ == '__main__':
    # Roda a primeira varredura ao ligar o servidor para não ficar vazio
    if not os.path.exists('dados_impressoras.json'):
        executar_varredura()
    app.run(host='0.0.0.0', port=80)