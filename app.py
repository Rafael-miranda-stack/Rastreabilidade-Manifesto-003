import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor
import requests
import json
import time
import pandas as pd
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

load_dotenv()

# ============================================
# CONFIGURAÇÕES
# ============================================

st.set_page_config(
    page_title="Rastreabilidade - Manifesto 003",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Credenciais Omie
APP_KEY = st.secrets.get("APP_KEY", os.getenv("APP_KEY", ""))
APP_SECRET = st.secrets.get("APP_SECRET", os.getenv("APP_SECRET", ""))

# Database Neon
DATABASE_URL = os.getenv("DATABASE_URL", st.secrets.get("DATABASE_URL", ""))

ontem = datetime.now() - timedelta(days=10)
ontem_formatado = ontem.strftime("%d/%m/%Y")

# ============================================
# CONEXÃO NEON
# ============================================

@st.cache_resource
def get_db_connection():
    """Conecta ao Neon"""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        st.error(f"❌ Erro ao conectar ao Neon: {e}")
        return None

def buscar_lote_validade(sku):
    """Busca lote e validade do SKU no Neon"""
    try:
        conn = get_db_connection()
        if not conn:
            return None, None
        
        cur = conn.cursor(cursor_factory=RealDictCursor)
        query = "SELECT lote, validade FROM tblotematriz WHERE sku = %s"
        cur.execute(query, (sku,))
        resultado = cur.fetchone()
        cur.close()
        
        if resultado:
            return resultado['lote'], resultado['validade']
        return None, None
    except:
        return None, None

# ============================================
# API OMIE
# ============================================

class RateLimiter:
    def __init__(self):
        self.last_call = {}
        self.call_count = {}
        self.window_start = {}
    
    def wait_if_needed(self, method):
        now = datetime.now()
        if method not in self.last_call:
            self.last_call[method] = now
            self.call_count[method] = 0
            self.window_start[method] = now
            return
        
        if (now - self.window_start[method]).total_seconds() >= 60:
            self.call_count[method] = 0
            self.window_start[method] = now
        
        time_since_last = (now - self.last_call[method]).total_seconds()
        if time_since_last < 0.8:
            time.sleep(0.8 - time_since_last)
        
        self.last_call[method] = datetime.now()
        self.call_count[method] += 1

_rate_limiter = RateLimiter()

def omie_call(url, payload, method_name):
    """Faz chamada à API Omie"""
    try:
        _rate_limiter.wait_if_needed(method_name)
        response = requests.post(url, json=payload, timeout=30)
        return response.json()
    except:
        return {}

def ListarClientes(cnpj):
    """Lista clientes por CNPJ"""
    URL = "https://app.omie.com.br/api/v1/geral/clientes/"
    payload = {
        "call": "ListarClientes",
        "app_key": APP_KEY,
        "app_secret": APP_SECRET,
        "param": [{
            "pagina": 1,
            "registros_por_pagina": 50,
            "clientesFiltro": {"cnpj_cpf": cnpj},
            "exibir_obs": "N"
        }]
    }
    
    retorno = omie_call(URL, payload, "ListarClientes")
    clientes = retorno.get("clientes_cadastro", [])
    
    if clientes:
        cliente = clientes[0]
        return cliente.get("codigo_cliente_omie"), cliente.get("razao_social")
    return None, None

def ListarRemessas(codigo_cliente):
    """Lista remessas não faturadas"""
    URL = "https://app.omie.com.br/api/v1/produtos/remessa/"
    remessas_dict = {}
    pagina = 1
    
    for _ in range(5):  # Máx 5 páginas
        payload = {
            "call": "ListarRemessas",
            "app_key": APP_KEY,
            "app_secret": APP_SECRET,
            "param": [{
                "nPagina": pagina,
                "nRegistrosPorPagina": 100,
                "cExibirDetalhes": "N",
                "nIdCliente": codigo_cliente,
                "dtAltDe": ontem_formatado
            }]
        }
        
        retorno = omie_call(URL, payload, "ListarRemessas")
        
        if isinstance(retorno, list) and "CODIGO" in retorno[0]:
            break
        
        remessas = retorno.get("remessas", [])
        if not remessas:
            break
        
        for remessa in remessas:
            cabec = remessa.get("cabec", {})
            numero = cabec.get("cNumeroRemessa")
            codigo = cabec.get("nCodRem")
            faturada = cabec.get("faturada")
            if numero and codigo and faturada == "N":
                remessas_dict[str(numero)] = codigo
        
        total_paginas = retorno.get("nTotPaginas", 1)
        if pagina >= total_paginas:
            break
        pagina += 1
    
    return remessas_dict

def ConsultarRemessas(codigo_remessa):
    """Consulta detalhes da remessa"""
    URL = "https://app.omie.com.br/api/v1/produtos/remessa/"
    payload = {
        "call": "ConsultarRemessa",
        "app_key": APP_KEY,
        "app_secret": APP_SECRET,
        "param": [{"nCodRem": codigo_remessa}]
    }
    
    return omie_call(URL, payload, "ConsultarRemessa")

def AlterarRemessa(nCodRem, volume, produtos, nCodCli):
    """Altera remessa no Omie"""
    URL = "https://app.omie.com.br/api/v1/produtos/remessa/"
    payload = {
        "call": "AlterarRemessa",
        "app_key": APP_KEY,
        "app_secret": APP_SECRET,
        "param": [{
            "cabec": {"nCodRem": nCodRem, "nCodCli": nCodCli},
            "frete": {"nQtdVol": volume},
            "produtos": produtos
        }]
    }
    
    return omie_call(URL, payload, "AlterarRemessa")

# ============================================
# INTERFACE STREAMLIT
# ============================================

st.markdown("# 📦 Rastreabilidade - Manifesto 003")
st.markdown("**Sistema de controle de lotes e validades**")

st.divider()

# Seção 1: Buscar Cliente
st.markdown("## 1️⃣ Buscar Cliente")

col1, col2 = st.columns([3, 1])

with col1:
    cnpj_input = st.text_input("Digite o CNPJ do cliente:", placeholder="12.345.678/0001-90")

with col2:
    buscar_btn = st.button("🔍 Buscar", use_container_width=True)

codigo_cliente = None
razao_social = None

if buscar_btn and cnpj_input:
    with st.spinner("Buscando cliente..."):
        codigo_cliente, razao_social = ListarClientes(cnpj_input)
    
    if codigo_cliente:
        st.success(f"✅ Cliente encontrado: **{razao_social}**")
        st.session_state.codigo_cliente = codigo_cliente
        st.session_state.razao_social = razao_social
    else:
        st.error("❌ Cliente não encontrado")

if "codigo_cliente" in st.session_state:
    codigo_cliente = st.session_state.codigo_cliente
    razao_social = st.session_state.razao_social
    
    st.markdown(f"**Cliente selecionado:** {razao_social}")
    
    st.divider()
    
    # Seção 2: Listar Remessas
    st.markdown("## 2️⃣ Remessas Não Faturadas")
    
    with st.spinner("Carregando remessas..."):
        remessas = ListarRemessas(codigo_cliente)
    
    if remessas:
        remessa_options = {f"Remessa {num}": cod for num, cod in remessas.items()}
        remessa_selecionada = st.selectbox(
            "Selecione uma remessa:",
            list(remessa_options.keys())
        )
        
        codigo_remessa = remessa_options[remessa_selecionada]
        st.session_state.codigo_remessa = codigo_remessa
        
        st.divider()
        
        # Seção 3: Consultar Produtos da Remessa
        st.markdown("## 3️⃣ Produtos da Remessa")
        
        with st.spinner("Carregando produtos..."):
            remessa_data = ConsultarRemessas(codigo_remessa)
        
        if "remessa" in remessa_data and "itens" in remessa_data["remessa"]:
            itens = remessa_data["remessa"]["itens"]
            
            # Processa dados
            produtos_info = []
            for item in itens:
                codigo_produto = item.get("nCodProd")
                descricao = item.get("cDescrProduto", "")
                quantidade = item.get("nQtde", 0)
                
                # Busca SKU (vamos usar código do produto como SKU por enquanto)
                sku = str(codigo_produto)
                
                # Busca lote/validade no Neon
                lote, validade = buscar_lote_validade(sku)
                
                produtos_info.append({
                    "codigo": codigo_produto,
                    "descricao": descricao,
                    "sku": sku,
                    "lote": lote or "",
                    "validade": validade or "",
                    "quantidade": quantidade
                })
            
            # Exibe produtos em cards
            for idx, produto in enumerate(produtos_info):
                with st.container(border=True):
                    col1, col2, col3 = st.columns(3)
                    
                    with col1:
                        st.markdown(f"**SKU:** {produto['sku']}")
                        st.markdown(f"**Desc:** {produto['descricao'][:30]}...")
                    
                    with col2:
                        st.markdown(f"**Lote:** {produto['lote']}")
                        st.markdown(f"**Validade:** {produto['validade']}")
                    
                    with col3:
                        st.markdown(f"**Qtd:** {produto['quantidade']}")
            
            st.divider()
            
            # Seção 4: Quantidade de Caixas
            st.markdown("## 4️⃣ Detalhes de Envio")
            
            qtd_caixas = st.number_input(
                "Quantidade de caixas:",
                min_value=1,
                value=1,
                step=1
            )
            
            st.divider()
            
            # Seção 5: Salvar
            st.markdown("## 5️⃣ Confirmar Envio")
            
            if st.button("✅ SALVAR DADOS", use_container_width=True, type="primary"):
                # Prepara dados para Omie
                produtos_omie = []
                for produto in produtos_info:
                    produtos_omie.append({
                        "nCodProd": produto['codigo'],
                        "nQtde": produto['quantidade']
                    })
                
                with st.spinner("Enviando para Omie..."):
                    resultado = AlterarRemessa(
                        codigo_remessa,
                        qtd_caixas,
                        produtos_omie,
                        codigo_cliente
                    )
                
                if resultado and not isinstance(resultado, list):
                    st.success("✅ Remessa alterada com sucesso!")
                    st.json(resultado)
                else:
                    st.error("❌ Erro ao alterar remessa")
                    st.json(resultado)
        else:
            st.warning("⚠️ Remessa vazia ou sem itens")
    else:
        st.warning("⚠️ Nenhuma remessa não faturada encontrada")

st.divider()

# Footer
st.markdown("""
---
**Sistema de Rastreabilidade - Manifesto 003**
- Dados de lote/validade: **Neon Database**
- Remessas: **Omie ERP**
- Atualizado: *Real-time*
""")

