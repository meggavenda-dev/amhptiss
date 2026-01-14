import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
import time

st.set_page_config(page_title="Automação AMHPTISS", layout="wide")

def iniciar_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    # Tenta mascarar o uso de automação
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service("/usr/bin/chromedriver")
    try:
        return webdriver.Chrome(service=service, options=options)
    except:
        return webdriver.Chrome(options=options)

st.title("🏥 Acesso AMHPTISS")

USUARIO = st.secrets["credentials"]["usuario"]
SENHA = st.secrets["credentials"]["senha"]

if st.button("🚀 Iniciar Acesso"):
    driver = iniciar_driver()
    if driver:
        try:
            with st.status("Autenticando...", expanded=True) as status:
                
                # 1. Login
                st.write("🌐 Abrindo portal...")
                driver.get("https://portal.amhp.com.br/")
                wait = WebDriverWait(driver, 30)

                st.write("🔑 Digitante credenciais...")
                campo_login = wait.until(EC.element_to_be_clickable((By.ID, "input-9")))
                campo_login.send_keys(USUARIO)
                
                campo_senha = driver.find_element(By.ID, "input-12")
                campo_senha.send_keys(SENHA)
                campo_senha.send_keys(Keys.ENTER)

                # 2. ESPERA CRUCIAL
                st.write("⏳ Aguardando consolidação da sessão no portal principal...")
                time.sleep(15) 

                # 3. VERIFICAÇÃO DE COOKIES E ACESSO AO TISS
                st.write("📂 Solicitando AMHPTISS...")
                
                # Antes de dar o GET, vamos limpar qualquer redirecionamento pendente
                driver.execute_script("window.location.href = 'https://amhptiss.amhp.com.br/Default.aspx'")
                
                # Espera o sistema TISS carregar (ele é lento)
                time.sleep(12)

                # 4. RESULTADO
                url_final = driver.current_url
                st.write(f"📍 URL final: {url_final}")
                
                # Se cair no site institucional, tentamos uma última vez
                if "www.amhp.com.br" in url_final:
                    st.warning("⚠️ Redirecionado para o site institucional. Tentando re-acesso direto...")
                    driver.get("https://amhptiss.amhp.com.br/Default.aspx")
                    time.sleep(10)
                    url_final = driver.current_url

                driver.save_screenshot("resultado.png")
                st.image("resultado.png", caption="Tela atual do navegador")

                if "amhptiss" in url_final.lower():
                    st.success("✅ Logado com sucesso no AMHPTISS!")
                    st.balloons()
                else:
                    st.error("❌ Não foi possível manter a sessão ativa.")

                status.update(label="Fim", state="complete", expanded=False)

        except Exception as e:
            st.error(f"🚨 Erro: {e}")
            driver.save_screenshot("erro.png")
            st.image("erro.png")
        finally:
            driver.quit()
