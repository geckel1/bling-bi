import requests
import base64
import datetime
import csv
import os
import time
import json
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv

# Carrega credenciais do .env
load_dotenv()
CLIENT_ID = os.getenv("BLING_CLIENT_ID")
CLIENT_SECRET = os.getenv("BLING_CLIENT_SECRET")
REDIRECT_URI = "https://www.google.com" 

TOKEN_FILE = "tokens.json"
CACHE_MODELOS_FILE = "cache_modelos.json"
CACHE_PEDIDOS_FILE = "cache_pedidos.json"

if not CLIENT_ID or not CLIENT_SECRET:
    raise ValueError("ERRO: As credenciais não foram encontradas no arquivo .env.")

# ==========================================
# 1. GERENCIAMENTO DE TOKENS
# ==========================================
def carregar_tokens():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'r') as f: return json.load(f)
    return None

def salvar_tokens(access_token, refresh_token):
    with open(TOKEN_FILE, 'w') as f: json.dump({"access_token": access_token, "refresh_token": refresh_token}, f)

def renovar_token_automaticamente(refresh_token):
    print("🔄 Access Token expirado. Renovando nos bastidores...")
    auth_base64 = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode('utf-8')).decode('utf-8')
    headers = {"Authorization": f"Basic {auth_base64}", "Content-Type": "application/x-www-form-urlencoded"}
    payload = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    
    response = requests.post("https://www.bling.com.br/Api/v3/oauth/token", headers=headers, data=payload)
    if response.status_code == 200:
        dados = response.json()
        salvar_tokens(dados['access_token'], dados['refresh_token'])
        print("✅ Token renovado com sucesso!")
        return dados['access_token']
    return None

def obter_tokens():
    print("--- PASSO 1: VERIFICAÇÃO DE AUTENTICAÇÃO ---")
    tokens_salvos = carregar_tokens()
    if tokens_salvos:
        headers_teste = {"Authorization": f"Bearer {tokens_salvos['access_token']}"}
        teste_resp = requests.get("https://www.bling.com.br/Api/v3/pedidos/vendas?limite=1", headers=headers_teste)
        if teste_resp.status_code == 200:
            print("✅ Token ainda é válido! Pulando autenticação.")
            return tokens_salvos['access_token']
        elif teste_resp.status_code == 401:
            novo_token = renovar_token_automaticamente(tokens_salvos['refresh_token'])
            if novo_token: return novo_token

    print("⚠️ Autenticação manual necessária.")
    url_auth = f"https://www.bling.com.br/Api/v3/oauth/authorize?response_type=code&client_id={CLIENT_ID}&state=python123&redirect_uri={REDIRECT_URI}&scope=pedidos.read produtos.read"
    print(f"1. Clique neste link e autorize: {url_auth}")
    url_retorno = input("2. Cole AQUI a URL do Google:> ")
    codigo = parse_qs(urlparse(url_retorno).query)['code'][0]

    auth_base64 = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode('utf-8')).decode('utf-8')
    headers = {"Authorization": f"Basic {auth_base64}", "Content-Type": "application/x-www-form-urlencoded"}
    payload = {"grant_type": "authorization_code", "code": codigo}
    
    response = requests.post("https://www.bling.com.br/Api/v3/oauth/token", headers=headers, data=payload)
    if response.status_code == 200:
        dados = response.json()
        salvar_tokens(dados['access_token'], dados['refresh_token'])
        print("✅ Autenticação concluída com sucesso!")
        return dados['access_token']
    else:
        print("❌ Erro na autenticação manual.")
        return None

# ==========================================
# 2. SINCRONIZAÇÃO INCREMENTAL DO CATÁLOGO
# ==========================================
def carregar_cache_modelos():
    if os.path.exists(CACHE_MODELOS_FILE):
        with open(CACHE_MODELOS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Adicionamos 'nomes_por_id' para a memória persistente
            if "produtos" not in data:
                return {"ultima_sincronizacao": None, "produtos": data, "nomes_por_id": {}}
            if "nomes_por_id" not in data:
                data["nomes_por_id"] = {}
            return data
    return {"ultima_sincronizacao": None, "produtos": {}, "nomes_por_id": {}}

def salvar_cache_modelos(cache_completo):
    with open(CACHE_MODELOS_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache_completo, f, ensure_ascii=False, indent=2)

def sincronizar_catalogo_produtos(headers, cache_completo):
    from urllib.parse import quote # Importação de segurança para formatar a URL
    
    ultima_sync = cache_completo.get("ultima_sincronizacao")
    cache_skus = cache_completo.get("produtos", {})
    nomes_por_id = cache_completo.get("nomes_por_id", {})
    
    print("\n--- PASSO 2: SINCRONIZANDO CATÁLOGO DE PRODUTOS ---")
    url_base = "https://www.bling.com.br/Api/v3/produtos"
    
    if ultima_sync:
        agora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Transforma o espaço em %20 e o ':' em %3A garantindo a leitura do Bling
        data_ini_enc = quote(ultima_sync)
        data_fim_enc = quote(agora)
        
        # Enviamos Inicial e Final juntos para o Bling não ter desculpas
        filtro_data = f"&criterio=5&dataAlteracaoInicial={data_ini_enc}&dataAlteracaoFinal={data_fim_enc}"
        print(f"🔄 Cache encontrado! Puxando atualizações desde {ultima_sync}...")
    else:
        filtro_data = "&criterio=5"
        print("📥 Puxando catálogo COMPLETO pela primeira vez...")

    pagina = 1
    variacoes = []
    novos_ou_alterados = 0

    while True:
        url = f"{url_base}?pagina={pagina}&limite=100{filtro_data}"
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200: break
            
        dados = response.json().get('data', [])
        if not dados: break
            
        print(f"Analisando página {pagina} de produtos...")
        for p in dados:
            novos_ou_alterados += 1
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

    for v in variacoes:
        sku = v['sku']
        id_pai = v['id_pai']
        if sku:
            cache_skus[sku] = nomes_por_id.get(id_pai, v['nome'])

    # Atualiza a data para a próxima vez
    novo_sync = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    salvar_cache_modelos({"ultima_sincronizacao": novo_sync, "produtos": cache_skus, "nomes_por_id": nomes_por_id})
    print(f"✅ Catálogo pronto. Total de SKUs mapeados: {len(cache_skus)}")
    return cache_skus

# ==========================================
# 3. EXTRAÇÃO DE PEDIDOS (INCREMENTAL / UPSERT) E CURVA ABC
# ==========================================
def carregar_cache_pedidos():
    if os.path.exists(CACHE_PEDIDOS_FILE):
        with open(CACHE_PEDIDOS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if "pedidos" not in data:
                return {"ultima_sincronizacao": None, "pedidos": data}
            return data
    return {"ultima_sincronizacao": None, "pedidos": {}}

def salvar_cache_pedidos(cache_completo):
    with open(CACHE_PEDIDOS_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache_completo, f, ensure_ascii=False, indent=2)

def exportar_dados_vendas(access_token, cache_skus, dias=90):
    from urllib.parse import quote
    print("\n--- PASSO 3: SINCRONIZANDO PEDIDOS (INCREMENTAL) ---")
    cache_completo = carregar_cache_pedidos()
    ultima_sync = cache_completo.get("ultima_sincronizacao")
    pedidos_salvos = cache_completo.get("pedidos", {})
    
    headers = {"Authorization": f"Bearer {access_token}"}
    url_base_pedidos = "https://www.bling.com.br/Api/v3/pedidos/vendas"
    
    if ultima_sync:
        agora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data_ini_enc = quote(ultima_sync)
        data_fim_enc = quote(agora)
        
        filtro_url = f"&dataAlteracaoInicial={data_ini_enc}&dataAlteracaoFinal={data_fim_enc}"
        print(f"🔄 Cache local encontrado! Buscando pedidos alterados desde {ultima_sync}...")
    else:
        data_inicio = (datetime.datetime.now() - datetime.timedelta(days=dias)).strftime("%Y-%m-%d")
        filtro_url = f"&dataInicial={data_inicio}"
        print(f"📥 Baixando histórico inicial de pedidos a partir de {data_inicio}...")

    pagina = 1
    novos_ou_alterados = 0

    while True: 
        url_lista = f"{url_base_pedidos}?pagina={pagina}&limite=100{filtro_url}"
        response = requests.get(url_lista, headers=headers)
        
        if response.status_code != 200: break
            
        pedidos_api = response.json().get('data', [])
        if not pedidos_api: break
            
        print(f"Analisando atualizações na Página {pagina} ({len(pedidos_api)} pedidos encontrados)...")
        
        for p in pedidos_api:
            id_pedido = str(p.get('id'))
            situacao = str(p.get('situacao', {}).get('valor', 'N/A'))
            data_pedido = p.get('data', 'N/A')
            
            url_detalhe = f"https://www.bling.com.br/Api/v3/pedidos/vendas/{id_pedido}"
            resp_detalhe = requests.get(url_detalhe, headers=headers)
            
            if resp_detalhe.status_code == 200:
                itens = resp_detalhe.json().get('data', {}).get('itens', [])
                itens_processados = []
                
                for item in itens:
                    sku = item.get('codigo', 'S/ SKU')
                    variacao_vendida = item.get('descricao') or item.get('nome') or 'Produto sem nome'
                    modelo_pai = cache_skus.get(sku, variacao_vendida)
                    
                    try:
                        qtde = float(item.get('quantidade', 0))
                        preco = float(item.get('valor', 0))
                    except ValueError:
                        qtde, preco = 0.0, 0.0
                        
                    itens_processados.append({
                        "sku": sku,
                        "modelo_pai": modelo_pai,
                        "variacao": variacao_vendida,
                        "qtde": qtde,
                        "preco": preco,
                        "total": round(qtde * preco, 2)
                    })
                
                pedidos_salvos[id_pedido] = {
                    "data": data_pedido,
                    "situacao": situacao,
                    "itens": itens_processados
                }
                novos_ou_alterados += 1
            
            time.sleep(0.35)
        pagina += 1

    agora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    salvar_cache_pedidos({"ultima_sincronizacao": agora, "pedidos": pedidos_salvos})
    
    if novos_ou_alterados > 0:
        print(f"✅ Download concluído! {novos_ou_alterados} pedidos novos/alterados sincronizados.")
    else:
        print("⚡ Nenhum pedido novo ou alterado desde a última verificação. Usando base local.")

    # ----------------------------------------------------
    # 3.2 GERAÇÃO DOS ARQUIVOS CSV DIRETAMENTE DA MEMÓRIA
    # ----------------------------------------------------
    print("\n--- PASSO 4: GERANDO RELATÓRIOS E CURVA ABC ---")
    arquivo_base = "base_vendas_detalhada.csv"
    arquivo_abc = "relatorio_curva_abc_modelos.csv"
    
    faturamento_por_modelo = {}
    quantidade_por_modelo = {}
    total_linhas_base = 0
    
    # Grava a Base Completa
    with open(arquivo_base, mode='w', newline='', encoding='utf-8-sig') as file_base:
        writer_base = csv.writer(file_base, delimiter=';')
        writer_base.writerow(["Data", "ID Pedido", "SKU", "Modelo (Produto Pai)", "Variação Vendida", "Quantidade", "Preço Unitário", "Total Item", "Situação Pedido"])
        
        for id_ped, info in pedidos_salvos.items():
            situacao_atual = info['situacao']
            data_ped = info['data']
            
            for item in info['itens']:
                writer_base.writerow([
                    data_ped, id_ped, item['sku'], item['modelo_pai'], 
                    item['variacao'], item['qtde'], item['preco'], item['total'], situacao_atual
                ])
                total_linhas_base += 1
                
                # Cálculos em tempo real para a Curva ABC (Ignorando cancelados)
                if str(situacao_atual).lower() != "cancelado" and str(situacao_atual) != "12":
                    faturamento_por_modelo[item['modelo_pai']] = faturamento_por_modelo.get(item['modelo_pai'], 0.0) + item['total']
                    quantidade_por_modelo[item['modelo_pai']] = quantidade_por_modelo.get(item['modelo_pai'], 0.0) + item['qtde']

    # Grava a Curva ABC
    total_geral_faturamento = sum(faturamento_por_modelo.values())
    
    if total_geral_faturamento > 0:
        modelos_ordenados = sorted(faturamento_por_modelo.items(), key=lambda x: x[1], reverse=True)
        acumulado_faturamento = 0.0
        linhas_relatorio_abc = []
        
        for modelo, faturamento in modelos_ordenados:
            acumulado_faturamento += faturamento
            pct_participacao = (faturamento / total_geral_faturamento) * 100
            pct_acumulada = (acumulado_faturamento / total_geral_faturamento) * 100
            
            if pct_acumulada <= 80.00:
                classe = "A"
            elif pct_acumulada <= 95.00:
                classe = "B"
            else:
                classe = "C"
                
            qtd_total = quantidade_por_modelo.get(modelo, 0.0)
            linhas_relatorio_abc.append([
                modelo, int(qtd_total),
                f"R$ {faturamento:,.2f}".replace(",", "v").replace(".", ",").replace("v", "."),
                f"{pct_participacao:.2f}%".replace(".", ","),
                f"{pct_acumulada:.2f}%".replace(".", ","),
                classe
            ])
        
        with open(arquivo_abc, mode='w', newline='', encoding='utf-8-sig') as file_abc:
            writer_abc = csv.writer(file_abc, delimiter=';')
            writer_abc.writerow(["Modelo (Produto Pai)", "Qtd Vendida", "Faturamento Total", "% Participação", "% Acumulada", "Classe ABC"])
            writer_abc.writerows(linhas_relatorio_abc)
            
        print(f"🎉 SUCESSO! Base Detalhada gerada com {total_linhas_base} linhas.")
        print(f"🎉 Curva ABC calculada com perfeição e salva em '{arquivo_abc}'.")
    else:
        print("⚠️ Faturamento válido zerado no período analisado. Planilha ABC não gerada.")

# ==========================================
# EXECUÇÃO DO SCRIPT
# ==========================================
if __name__ == "__main__":
    token = obter_tokens()
    if token:
        headers = {"Authorization": f"Bearer {token}"}
        cache_completo = carregar_cache_modelos()
        cache_mapeado = sincronizar_catalogo_produtos(headers, cache_completo)
        exportar_dados_vendas(token, cache_mapeado, dias=90)