import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
import time

st.set_page_config(page_title="Automação AMHP", page_icon="🏥")

def iniciar_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    # Tenta evitar detecção
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    service = Service("/usr/bin/chromedriver")
    try:
        return webdriver.Chrome(service=service, options=options)
    except:
        return webdriver.Chrome(options=options)

st.title("🚀 Automação AMHP")

if "credentials" not in st.secrets:
    st.error("Configure os Secrets!")
    st.stop()

USUARIO = st.secrets["credentials"]["usuario"]
SENHA = st.secrets["credentials"]["senha"]

if st.button("Executar Acesso"):
    driver = iniciar_driver()
    if driver:
        try:
            with st.status("Autenticando no Portal...", expanded=True) as status:
                
                st.write("🌍 Acessando página inicial...")
                driver.get("https://portal.amhp.com.br/")
                wait = WebDriverWait(driver, 30)

                # Espera o campo de login e clica nele antes de digitar
                st.write("🔑 Inserindo credenciais...")
                campo_login = wait.until(EC.element_to_be_clickable((By.ID, "input-9")))
                driver.execute_script("arguments[0].click();", campo_login)
                campo_login.send_keys(USUARIO)
                
                campo_senha = driver.find_element(By.ID, "input-12")
                driver.execute_script("arguments[0].click();", campo_senha)
                campo_senha.send_keys(SENHA)
                
                # Em vez de procurar o botão, vamos dar "ENTER" no campo de senha
                # Muitas vezes é mais seguro que clicar em botões dinâmicos
                st.write("🖱️ Enviando formulário...")
                campo_senha.send_keys(Keys.ENTER)

                st.write("⏳ Aguardando validação (15s)...")
                time.sleep(15)

                # Tenta forçar a ida para o TISS
                st.write("📂 Acessando AMHPTISS...")
                driver.get("https://amhptiss.amhp.com.br/Default.aspx")
                time.sleep(10)

                # Verifica onde paramos
                url_final = driver.current_url
                st.write(f"📍 Finalizamos em: {url_final}")
                
                driver.save_screenshot("captura_final.png")
                st.image("captura_final.png", caption="Visão do Robô")

                if "amhptiss" in url_final.lower():
                    st.success("✅ Login Concluído!")
                else:
                    st.warning("O redirecionamento não foi para a página interna.")

                status.update(label="Fim do Processo", state="complete", expanded=False)

        except Exception as e:
            st.error(f"🚨 Erro: {e}")
            try:
                driver.save_screenshot("erro_stack.png")
                st.image("erro_stack.png")
            except: pass
        finally:
            driver.quit()
