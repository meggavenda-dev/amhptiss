import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import os

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Automação AMHP", page_icon="🏥")

def iniciar_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.binary_location = "/usr/bin/chromium" # Caminho padrão no Streamlit Cloud
    
    # No Streamlit Cloud, o driver é instalado via packages.txt e fica no PATH
    # Não precisamos do ChromeDriverManager aqui para o servidor
    try:
        driver = webdriver.Chrome(options=options)
    except Exception:
        # Fallback caso esteja rodando localmente (precisará do driver no PATH)
        driver = webdriver.Chrome(options=options)
        
    return driver

st.title("🚀 Automação AMHP")

# Verificação de Secrets
if "credentials" not in st.secrets:
    st.error("❌ Configure os Secrets no painel do Streamlit: [credentials] usuario='' senha=''")
    st.stop()

USUARIO = st.secrets["credentials"]["usuario"]
SENHA = st.secrets["credentials"]["senha"]

if st.button("Iniciar Relatório"):
    driver = None
    try:
        driver = iniciar_driver()
        with st.status("Executando...", expanded=True) as status:
            
            # 1. Login
            st.write("🔗 Acessando portal...")
            driver.get("https://portal.amhp.com.br/")
            wait = WebDriverWait(driver, 25)
            
            st.write("🔑 Realizando login...")
            campo_login = wait.until(EC.presence_of_element_located((By.ID, "input-9")))
            campo_login.send_keys(USUARIO)
            
            campo_senha = driver.find_element(By.ID, "input-12")
            campo_senha.send_keys(SENHA)
            
            botao_entrar = driver.find_element(By.XPATH, "//button[contains(., 'Entrar')]")
            botao_entrar.click()
            
            # 2. Transição
            time.sleep(6)
            
            # 3. Acesso ao TISS
            st.write("📂 Entrando no AMHPTISS...")
            driver.get("https://amhptiss.amhp.com.br/Default.aspx")
            time.sleep(5)
            
            if "Default.aspx" in driver.current_url:
                st.success("✅ Logado com sucesso!")
                st.write(f"URL: {driver.current_url}")
            else:
                st.warning("⚠️ O redirecionamento falhou. Verifique as credenciais.")
                
            status.update(label="Concluído!", state="complete", expanded=False)

    except Exception as e:
        st.error(f"🚨 Erro: {e}")
    finally:
        if driver:
            driver.quit()
