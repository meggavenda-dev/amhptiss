import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

st.set_page_config(page_title="Automação AMHP", page_icon="🏥")

def iniciar_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    # Ignora erros de certificado e logs desnecessários
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--log-level=3")
    
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

if st.button("Iniciar"):
    driver = iniciar_driver()
    if driver:
        try:
            with st.status("Processando...", expanded=True) as status:
                st.write("🌍 Acessando portal...")
                driver.get("https://portal.amhp.com.br/")
                wait = WebDriverWait(driver, 30)

                # Preenchimento
                st.write("🔑 Preenchendo dados...")
                campo_login = wait.until(EC.presence_of_element_located((By.ID, "input-9")))
                campo_login.send_keys(USUARIO)
                
                campo_senha = driver.find_element(By.ID, "input-12")
                campo_senha.send_keys(SENHA)

                # CLIQUE NO BOTÃO - NOVA ESTRATÉGIA
                st.write("🖱️ Localizando botão de acesso...")
                time.sleep(2) # Pausa para renderização do Vuetify
                
                # Procura por qualquer elemento que tenha o texto "Entrar" (independente de ser maiúsculo/minúsculo)
                # O XPath abaixo busca o texto exato ou contido em qualquer tag
                try:
                    botao = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[contains(text(), 'Entrar')] | //*[contains(text(), 'ENTRAR')]")))
                    st.write("✅ Botão encontrado, clicando...")
                    driver.execute_script("arguments[0].scrollIntoView(true);", botao)
                    driver.execute_script("arguments[0].click();", botao)
                except:
                    # Se falhar, tenta clicar na classe padrão de botões do portal
                    st.write("⚠️ Tentando seletor alternativo...")
                    botao_alt = driver.find_element(By.CSS_SELECTOR, ".v-btn")
                    driver.execute_script("arguments[0].click();", botao_alt)

                st.write("⏳ Aguardando login...")
                time.sleep(10)

                st.write("📂 Tentando acessar AMHPTISS...")
                driver.get("https://amhptiss.amhp.com.br/Default.aspx")
                time.sleep(5)

                if "Default.aspx" in driver.current_url:
                    st.success("✅ Login realizado com sucesso!")
                else:
                    st.warning("Página atual: " + driver.current_url)
                    driver.save_screenshot("erro.png")
                    st.image("erro.png", caption="O que o robô está vendo agora")

                status.update(label="Concluído!", state="complete", expanded=False)

        except Exception as e:
            st.error(f"🚨 Erro: {e}")
            try:
                driver.save_screenshot("falha_fatal.png")
                st.image("falha_fatal.png")
            except: pass
        finally:
            driver.quit()
