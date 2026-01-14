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
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    # Identifica o robô como um navegador real para evitar redirecionamento para o site institucional
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    try:
        service = Service("/usr/bin/chromedriver")
        return webdriver.Chrome(service=service, options=options)
    except:
        return webdriver.Chrome(options=options)

st.title("🚀 Automação AMHP - Acesso Direto")

if "credentials" not in st.secrets:
    st.error("Configure os Secrets no Streamlit!")
    st.stop()

USUARIO = st.secrets["credentials"]["usuario"]
SENHA = st.secrets["credentials"]["senha"]

if st.button("Iniciar Acesso ao AMHPTISS"):
    driver = iniciar_driver()
    if driver:
        try:
            with st.status("Realizando login...", expanded=True) as status:
                
                # PASSO 1: LOGIN
                st.write("🌍 Acessando portal de login...")
                driver.get("https://portal.amhp.com.br/")
                wait = WebDriverWait(driver, 30)

                st.write("🔑 Preenchendo dados...")
                campo_login = wait.until(EC.presence_of_element_located((By.ID, "input-9")))
                campo_login.send_keys(USUARIO)
                
                campo_senha = driver.find_element(By.ID, "input-12")
                campo_senha.send_keys(SENHA)

                # PASSO 2: CLIQUE E ESPERA DE TRANSIÇÃO
                st.write("🖱️ Clicando em Entrar...")
                botao = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[contains(text(), 'Entrar')]")))
                driver.execute_script("arguments[0].click();", botao)
                
                # Aqui está o segredo: esperar a URL mudar para algo que NÃO seja a página de login
                # mas antes de forçar o link do TISS
                st.write("⏳ Aguardando validação da sessão...")
                time.sleep(15) 

                # PASSO 3: NAVEGAÇÃO PARA O TISS
                st.write("📂 Solicitando AMHPTISS...")
                # Forçamos a URL mas mantendo os cookies da sessão anterior
                driver.get("https://amhptiss.amhp.com.br/Default.aspx")
                
                # Espera extra para o sistema ASPX carregar
                time.sleep(10)

                # PASSO 4: VERIFICAÇÃO DE RESULTADO
                url_final = driver.current_url
                st.write(f"📍 URL alcançada: {url_final}")

                if "amhptiss" in url_final.lower() and "Default.aspx" in url_final:
                    st.success("✅ Logado com sucesso no AMHPTISS!")
                    st.balloons()
                else:
                    st.warning("⚠️ O sistema redirecionou para fora do ambiente esperado.")
                
                # Tira print do resultado final para análise
                driver.save_screenshot("resultado_final.png")
                st.image("resultado_final.png", caption="Tela capturada pelo robô")

                status.update(label="Processo Concluído", state="complete", expanded=False)

        except Exception as e:
            st.error(f"🚨 Erro: {e}")
            try:
                driver.save_screenshot("erro_fatal.png")
                st.image("erro_fatal.png")
            except: pass
        finally:
            driver.quit()
