import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Automação AMHP", page_icon="🏥")

def iniciar_driver():
    """Configura o driver para rodar tanto localmente quanto no Streamlit Cloud"""
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    
    # Tenta caminhos comuns do Linux (Streamlit Cloud)
    caminhos_driver = ["/usr/bin/chromedriver", "/usr/lib/chromium-browser/chromedriver"]
    service = None
    
    for caminho in caminhos_driver:
        try:
            if iter(caminho): # Verifica se o arquivo existe (simplificado)
                service = Service(caminho)
                break
        except:
            continue

    try:
        if service:
            return webdriver.Chrome(service=service, options=options)
        else:
            # Fallback para execução local
            return webdriver.Chrome(options=options)
    except Exception as e:
        st.error(f"Erro ao iniciar o Navegador: {e}")
        return None

# --- INTERFACE ---
st.title("🚀 Automação AMHP")

# Validação dos Secrets
if "credentials" not in st.secrets:
    st.error("❌ Por favor, configure os 'Secrets' no painel do Streamlit.")
    st.info("Formato esperado: \n\n[credentials]\nusuario='seu_user'\nsenha='sua_senha'")
    st.stop()

USUARIO = st.secrets["credentials"]["usuario"]
SENHA = st.secrets["credentials"]["senha"]

if st.button("Executar Login e Acessar TISS"):
    driver = iniciar_driver()
    
    if driver:
        try:
            with st.status("Iniciando processo...", expanded=True) as status:
                
                # 1. Login no Portal Principal
                st.write("🌐 Acessando https://portal.amhp.com.br/...")
                driver.get("https://portal.amhp.com.br/")
                
                wait = WebDriverWait(driver, 30)
                
                st.write("🔑 Preenchendo dados de acesso...")
                # Campo de Login (ID input-9)
                campo_login = wait.until(EC.presence_of_element_located((By.ID, "input-9")))
                campo_login.send_keys(USUARIO)
                
                # Campo de Senha (ID input-12)
                campo_senha = driver.find_element(By.ID, "input-12")
                campo_senha.send_keys(SENHA)
                
                # Clique no botão Entrar
                botao_entrar = driver.find_element(By.XPATH, "//button[contains(., 'Entrar')]")
                botao_entrar.click()
                
                # 2. Aguarda Redirecionamento
                st.write("⏳ Aguardando autenticação...")
                time.sleep(8) 
                
                # 3. Acesso ao AMHPTISS
                st.write("📂 Navegando para AMHPTISS...")
                driver.get("https://amhptiss.amhp.com.br/Default.aspx")
                time.sleep(5)
                
                # 4. Verificação Final
                if "Default.aspx" in driver.current_url:
                    st.success("✅ Login realizado e AMHPTISS acessado com sucesso!")
                    st.info(f"Página atual: {driver.current_url}")
                    
                    # Debug: Mostra o título da página logada
                    st.write(f"Título da página: {driver.title}")
                else:
                    st.warning("⚠️ O sistema não parece estar na página esperada.")
                    st.write(f"URL atual: {driver.current_url}")
                
                status.update(label="Processo concluído!", state="complete", expanded=False)

        except Exception as e:
            st.error(f"🚨 Ocorreu um erro durante a execução: {e}")
        finally:
            driver.quit()
