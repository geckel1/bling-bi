import streamlit as st
import requests
import base64
import datetime
import csv
import os
import time
import json
import io
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
from supabase import create_client, Client
from cryptography.fernet import Fernet

# Inicialização e Configurações
load_dotenv()
CLIENT_ID = os.getenv("BLING_CLIENT_ID")
CLIENT_SECRET = os.getenv("BLING_CLIENT_SECRET")
REDIRECT_URI = os.getenv("STREAMLIT_REDIRECT_URI")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")


if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Erro: Credenciais do Supabase não encontradas no arquivo .env")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- FUNÇÕES DE PERSISTÊNCIA NA NUVEM (SUPABASE) ---
def get_state(key_name):
    try:
        res = supabase.table('bling_state').select('data').eq('key', key_name).execute()
        if res.data and len(res.data) > 0:
            return res.data[0]['data']
    except Exception as e:
        st.sidebar.error(f"Erro ao ler banco de dados: {e}")
    return None

# --- FUNÇÕES DE CRIPTOGRAFIA ---
from cryptography.fernet import Fernet
KEY = os.getenv("SECRET_KEY_CRYPTO").encode()
cipher_suite = Fernet(KEY)

def save_state(key_name, data_dict_or_bytes):
    try:
        # Se for bytes (criptografado), converte para string base64 para o Supabase aceitar
        if isinstance(data_dict_or_bytes, bytes):
            data_to_save = base64.b64encode(data_dict_or_bytes).decode('utf-8')
        else:
            data_to_save = data_dict_or_bytes
            
        supabase.table('bling_state').upsert({'key': key_name, 'data': data_to_save}).execute()
    except Exception as e:
        st.sidebar.error(f"Erro ao gravar no banco: {e}")

# ==========================================
# GERENCIAMENTO DE TOKENS (BLING v3)
# ==========================================
def renovar_token_automaticamente(refresh_token):
    auth_base64 = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode('utf-8')).decode('utf-8')
    headers = {"Authorization": f"Basic {auth_base64}", "Content-Type": "application/x-www-form-urlencoded"}
    payload = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    
    response = requests.post("https://www.bling.com.br/Api/v3/oauth/token", headers=headers, data=payload)
    if response.status_code == 200:
        dados = response.json()
        dados_criptografados = cipher_suite.encrypt(dados)
        save_state('tokens', dados_criptografados)
        return dados['access_token']
    return None

def obter_token_valido():
    res = get_state('tokens')
    if not res: return None
    
    try:
        # Se o que veio do banco for uma string, converte de volta para bytes
        if isinstance(res, str):
            encrypted_data = base64.b64decode(res.encode('utf-8'))
        else:
            return None # Dados em formato inesperado
            
        dados_decifrados = cipher_suite.decrypt(encrypted_data)
        tokens_salvos = json.loads(dados_decifrados.decode())
        
        # Teste de validade...
        headers = {"Authorization": f"Bearer {tokens_salvos['access_token']}"}
        if requests.get("https://www.bling.com.br/Api/v3/pedidos/vendas?limite=1", headers=headers).status_code == 200:
            return tokens_salvos['access_token']
        else:
            return renovar_token_automaticamente(tokens_salvos['refresh_token'])
    except Exception as e:
        return None

# Interface Gráfica (Streamlit)
st.set_page_config(page_title="Bling BI - Curva ABC", page_icon="📊", layout="wide")
st.title("📊 Painel Executivo - Análise de Curva ABC por Modelo")

# Sidebar - Controle de Autenticação
st.sidebar.header("🔑 Conexão Bling API v3")
token_atual = obter_token_valido()

# --- LOGICA DE LOGIN AUTOMATICO ---
query_params = st.query_params
if "code" in query_params and not token_atual:
    try:
        codigo = query_params["code"]
        auth_base64 = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode('utf-8')).decode('utf-8')
        headers = {"Authorization": f"Basic {auth_base64}", "Content-Type": "application/x-www-form-urlencoded"}
        payload = {"grant_type": "authorization_code", "code": codigo}
        
        resp = requests.post("https://www.bling.com.br/Api/v3/oauth/token", headers=headers, data=payload)
        if resp.status_code == 200:
            # Criptografa, converte para bytes e depois para string base64
            dados_criptografados = cipher_suite.encrypt(json.dumps(resp.json()).encode())
            save_state('tokens', dados_criptografados)
            st.sidebar.success("🎉 Autenticado! Atualize a página.")
            st.rerun()
        else:
            st.sidebar.error("Erro ao validar código no Bling.")
    except Exception:
        st.sidebar.error("URL inválida.")
    # ... aqui entra a lógica de POST para o Bling ...
    # Se der certo:
    st.success("Autenticado com sucesso!")
    st.query_params.clear() # Limpa a URL
    st.rerun()

if token_atual:
    st.sidebar.success("✅ Conectado com Sucesso à API!")
else:
    st.sidebar.warning("⚠️ Autenticação Requerida")
    # O uso de '+' separa os escopos corretamente sem quebrar a URL
    escopos = "pedidos.read+produtos.read"
    url_auth = f"https://www.bling.com.br/Api/v3/oauth/authorize?response_type=code&client_id={CLIENT_ID}&state=python123&redirect_uri={REDIRECT_URI}&scope={escopos}"
    st.sidebar.markdown(f"[👉 Clique aqui para Autorizar no Bling]({url_auth})")

# Corpo Principal
dias_analise = st.slider("Período de Análise (Dias de histórico de vendas):", min_value=30, max_value=365, value=90)

if st.button("🚀 Executar Sincronização e Gerar Relatórios", disabled=not token_atual):
    from urllib.parse import quote
    
    # 1. Sincronizar Catálogo de Produtos
    headers = {"Authorization": f"Bearer {token_atual}"}
    cache_modelos_db = get_state('cache_modelos') or {"ultima_sincronizacao": None, "produtos": {}, "nomes_por_id": {}}
    
    ultima_sync_prod = cache_modelos_db.get("ultima_sincronizacao")
    cache_skus = cache_modelos_db.get("produtos", {})
    nomes_por_id = cache_modelos_db.get("nomes_por_id", {})
    
    status_log = st.empty()
    status_log.info("🔄 Sincronizando catálogo de produtos...")
    
    url_base_prod = "https://www.bling.com.br/Api/v3/produtos"
    filtro_prod = f"&criterio=5&dataAlteracaoInicial={quote(ultima_sync_prod)}&dataAlteracaoFinal={quote(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}" if ultima_sync_prod else "&criterio=5"
    
    pagina = 1
    variacoes = []

    status_log = st.empty()
    barra_progresso = st.progress(0)
    
    status_log.info("🔄 Iniciando sincronização de produtos...")
    
    while True:
        url = f"{url_base_prod}?pagina={pagina}&limite=100{filtro_prod}"
        res = requests.get(url, headers=headers)
        if res.status_code != 200: break
        dados = res.json().get('data', [])
        if not dados: break
        
        for p in dados:
            p_id = str(p.get('id'))
            sku = p.get('codigo', '')
            nome = p.get('nome', '')
            formato = p.get('formato', 'S')
            nomes_por_id[p_id] = nome
            if formato == 'V':
                id_pai = str(p.get('variacao', {}).get('produtoPai', {}).get('id'))
                variacoes.append({'sku': sku, 'id_pai': id_pai, 'nome': nome})
            else:
                if sku: cache_skus[sku] = nome
        time.sleep(0.35)
        pagina += 1
        status_log.info(f"Analisando página {pagina} de produtos...")
        barra_progresso.progress(min(pagina * 5, 100)) # Ajuste o multiplicador conforme seu volume
        
    for v in variacoes:
        if v['sku']: cache_skus[v['sku']] = nomes_por_id.get(v['id_pai'], v['nome'])
        
    save_state('cache_modelos', {"ultima_sincronizacao": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "produtos": cache_skus, "nomes_por_id": nomes_por_id})
    
    # 2. Sincronizar Pedidos Incremental
    status_log.info("🔄 Sincronizando histórico de vendas do Bling...")
    cache_pedidos_db = get_state('cache_pedidos') or {"ultima_sincronizacao": None, "pedidos": {}}
    ultima_sync_ped = cache_pedidos_db.get("ultima_sincronizacao")
    pedidos_salvos = cache_pedidos_db.get("pedidos", {})
    
    url_base_ped = "https://www.bling.com.br/Api/v3/pedidos/vendas"
    if ultima_sync_ped:
        filtro_ped = f"&dataAlteracaoInicial={quote(ultima_sync_ped)}&dataAlteracaoFinal={quote(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}"
    else:
        filtro_ped = f"&dataInicial={(datetime.datetime.now() - datetime.timedelta(days=dias_analise)).strftime('%Y-%m-%d')}"
        
    pagina = 1
    status_log.info("🔄 Sincronizando histórico de vendas...")
    response_init = requests.get(f"{url_base_ped}?limite=1", headers=headers)
    total_registros = response_init.json().get('metadata', {}).get('totalRegistros', 100)
    while True:
        progresso_atual = (pagina * 100) / (total_registros / 100)
        barra_progresso.progress(int(progresso_atual))
        url = f"{url_base_ped}?pagina={pagina}&limite=100{filtro_ped}"
        res = requests.get(url, headers=headers)
        if res.status_code != 200: break
        pedidos_api = res.json().get('data', [])
        if not pedidos_api: break
        
        for p in pedidos_api:
            id_pedido = str(p.get('id'))
            status_log.info(f"Processando Pedido ID: {id_pedido}...")
            situacao = str(p.get('situacao', {}).get('valor', 'N/A'))
            data_pedido = p.get('data', 'N/A')
            
            res_det = requests.get(f"{url_base_ped}/{id_pedido}", headers=headers)
            if res_det.status_code == 200:
                itens = res_det.json().get('data', {}).get('itens', [])
                itens_proc = []
                for item in itens:
                    sku = item.get('codigo', 'S/ SKU')
                    
                    # BUSCA NO CACHE
                    # Se o SKU estiver no cache, ele trará o nome do PAI cadastrado no Passo 2
                    modelo_pai = cache_skus.get(sku, "Produto sem nome no cache")
                    
                    try:
                        qtde, preco = float(item.get('quantidade', 0)), float(item.get('valor', 0))
                    except:
                        qtde, preco = 0.0, 0.0
                        
                    # Aqui usamos 'modelo_pai'
                    itens_proc.append({
                        "sku": sku, 
                        "modelo_pai": modelo_pai, 
                        "variacao": item.get('descricao', 'Sem nome'), 
                        "qtde": qtde, 
                        "preco": preco, 
                        "total": round(qtde * preco, 2)
                    })
                pedidos_salvos[id_pedido] = {"data": data_pedido, "situacao": situacao, "itens": itens_proc}
            time.sleep(0.35)
        pagina += 1
        
    save_state('cache_pedidos', {"ultima_sincronizacao": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "pedidos": pedidos_salvos})
    
    ## 3. Processamento de Relatórios (Definição segura)
    status_log.info("⚙️ Calculando matriz matemática da Curva ABC...")
    barra_progresso.progress(80)
    
    # DEFINA OS OBJETOS AQUI, FORA DO IF
    output_base = io.StringIO()
    writer_base = csv.writer(output_base, delimiter=';')
    writer_base.writerow(["Data", "ID Pedido", "SKU", "Modelo (Produto Pai)", "Variação Vendida", "Quantidade", "Preço Unitário", "Total Item", "Situação Pedido"])
    
    faturamento_por_modelo = {}
    quantidade_por_modelo = {}
    
    # Lista para salvar o CSV com os nomes dos PAIS
    pedidos_csv = [] 
    
    for id_ped, info in pedidos_salvos.items():
        sit = info['situacao']
        for item in info['itens']:
            # AQUI ESTÁ A MÁGICA: 
            # Sempre usamos o 'modelo_pai' que já foi definido lá no Passo 2
            nome_agrupado = item['modelo_pai'] 
            
            pedidos_csv.append([info['data'], id_ped, item['sku'], nome_agrupado, item['variacao'], item['qtde'], item['preco'], item['total'], sit])
            
            if str(sit).lower() != "cancelado" and str(sit) != "12":
                faturamento_por_modelo[nome_agrupado] = faturamento_por_modelo.get(nome_agrupado, 0.0) + item['total']
                quantidade_por_modelo[nome_agrupado] = quantidade_por_modelo.get(nome_agrupado, 0.0) + item['qtde']
                
    total_geral = sum(faturamento_por_modelo.values())
    
    if total_geral > 0:
        # 1. PREPARAÇÃO DOS DADOS
        modelos_ord = sorted(faturamento_por_modelo.items(), key=lambda x: x[1], reverse=True)
        acumulado = 0.0
        
        output_abc = io.StringIO()
        writer_abc = csv.writer(output_abc, delimiter=';')
        writer_abc.writerow(["Modelo (Produto Pai)", "Qtd Vendida", "Faturamento Total", "% Participação", "% Acumulada", "Classe ABC"])
        
        for modelo, fat in modelos_ord:
            acumulado += fat
            pct_part = (fat / total_geral) * 100
            pct_acum = (acumulado / total_geral) * 100
            classe = "A" if pct_acum <= 80.0 else ("B" if pct_acum <= 95.0 else "C")
            writer_abc.writerow([
                modelo, int(quantidade_por_modelo.get(modelo, 0)),
                f"R$ {fat:,.2f}".replace(",", "v").replace(".", ",").replace("v", "."),
                f"{pct_part:.2f}%".replace(".", ","), f"{pct_acum:.2f}%".replace(".", ","), classe
            ])
            
        status_log.empty()
        st.success("🎉 Processamento Concluído com Sucesso!")
        barra_progresso.progress(100)
        status_log.success("✅ Processamento Concluído!")
        time.sleep(1) # Deixa a mensagem brilhar por um segundo
        barra_progresso.empty() # Remove a barra da tela
        
        # 2. RENDERIZAÇÃO DOS BOTÕES (Dentro do IF, aqui eles existem!)
        col1, col2 = st.columns(2)
        with col1:
            st.download_button(label="📥 Descarregar Tabela Curva ABC Pronta", data=output_abc.getvalue().encode('utf-8-sig'), file_name="relatorio_curva_abc_modelos.csv", mime="text/csv")
        with col2:
            st.download_button(label="📥 Descarregar Base de Vendas Completa", data=output_base.getvalue().encode('utf-8-sig'), file_name="base_vendas_detalhada.csv", mime="text/csv")
    else:
        status_log.error("Nenhuma venda válida encontrada no período.")