import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
import time

st.set_page_config(page_title="Automação AMHP", layout="wide")

def iniciar_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    # Identidade visual de navegador comum
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service("/usr/bin/chromedriver")
    try:
        return webdriver.Chrome(service=service, options=options)
    except:
        return webdriver.Chrome(options=options)

st.title("🏥 Automação AMHP")

# Pegando dados dos Secrets
USUARIO = st.secrets["credentials"]["usuario"]
SENHA = st.secrets["credentials"]["senha"]

if st.button("🚀 Iniciar Acesso ao TISS"):
    driver = iniciar_driver()
    if driver:
        try:
            with st.status("Realizando acesso...", expanded=True) as status:
                
                # 1. LOGIN NO PORTAL
                st.write("🌐 Abrindo portal principal...")
                driver.get("https://portal.amhp.com.br/")
                wait = WebDriverWait(driver, 30)

                st.write("🔑 Preenchendo login...")
                campo_login = wait.until(EC.element_to_be_clickable((By.ID, "input-9")))
                campo_login.send_keys(USUARIO)
                
                campo_senha = driver.find_element(By.ID, "input-12")
                campo_senha.send_keys(SENHA)
                campo_senha.send_keys(Keys.ENTER)

                # 2. ESPERA O DASHBOARD CARREGAR
                st.write("⏳ Aguardando carregamento do portal logado...")
                time.sleep(12) 

                # 3. CLIQUE NO BOTÃO AMHPTISS (USANDO O CÓDIGO QUE VOCÊ PASSOU)
                st.write("🖱️ Localizando botão AMHPTISS...")
                try:
                    # Buscamos especificamente pela classe e texto que você enviou
                    botao_tiss = wait.until(EC.element_to_be_clickable(
                        (By.XPATH, "//button[contains(@class, 'botao-sombreado') and contains(., 'AMHPTISS')]")
                    ))
                    st.write("✅ Botão AMHPTISS encontrado! Clicando...")
                    driver.execute_script("arguments[0].click();", botao_tiss)
                    
                except Exception as e:
                    st.warning("Não achei o botão pela classe. Tentando busca geral por texto...")
                    botao_alt = driver.find_element(By.XPATH, "//button[contains(., 'AMHPTISS')]")
                    driver.execute_script("arguments[0].click();", botao_alt)

                # 4. AGUARDAR TRANSIÇÃO DE SISTEMA
                st.write("🔄 Transferindo sessão para o TISS...")
                time.sleep(10)

                # Se o sistema abrir em uma nova aba, trocamos para ela
                if len(driver.window_handles) > 1:
                    driver.switch_to.window(driver.window_handles[1])

                # 5. VERIFICAÇÃO FINAL
                url_final = driver.current_url
                st.write(f"📍 Chegamos em: {url_final}")
                
                driver.save_screenshot("captura_tiss.png")
                st.image("captura_tiss.png", caption="Tela atual do AMHPTISS")

                if "amhptiss" in url_final.lower():
                    st.success("✅ SUCESSO! Você está dentro do AMHPTISS.")
                    st.balloons()
                else:
                    st.error("❌ O redirecionamento falhou. O sistema parou fora do TISS.")

                status.update(label="Fluxo Finalizado", state="complete", expanded=False)

        except Exception as e:
            st.error(f"🚨 Erro: {e}")
            driver.save_screenshot("erro_final.png")
            st.image("erro_final.png")
        finally:
            driver.quit()
