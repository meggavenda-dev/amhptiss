import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Automação AMHP", page_icon="🏥", layout="wide")

def iniciar_driver():
    """Configura o driver para o ambiente Streamlit Cloud (Debian Bookworm)"""
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    
    # Caminho padrão no Streamlit Cloud após instalar chromium-driver no packages.txt
    service = Service("/usr/bin/chromedriver")
    
    try:
        return webdriver.Chrome(service=service, options=options)
    except Exception:
        # Fallback para execução local em Windows/Mac
        return webdriver.Chrome(options=options)

# --- INTERFACE ---
st.title("🚀 Automação de Login AMHP")

# Validação dos Secrets
if "credentials" not in st.secrets:
    st.error("❌ Configure os 'Secrets' no painel do Streamlit (Manage App > Settings > Secrets).")
    st.code("[credentials]\nusuario='seu_usuario'\nsenha='sua_senha'")
    st.stop()

USUARIO = st.secrets["credentials"]["usuario"]
SENHA = st.secrets["credentials"]["senha"]

if st.button("🚀 Iniciar Automação"):
    driver = iniciar_driver()
    
    if driver:
        try:
            with st.status("Executando passos...", expanded=True) as status:
                
                # 1. ACESSO AO PORTAL
                st.write("🌍 Acessando o portal principal...")
                driver.get("https://portal.amhp.com.br/")
                wait = WebDriverWait(driver, 30)
                
                # 2. PREENCHIMENTO DE LOGIN
                st.write("🔑 Preenchendo login e senha...")
                campo_login = wait.until(EC.presence_of_element_located((By.ID, "input-9")))
                campo_login.send_keys(USUARIO)
                
                campo_senha = driver.find_element(By.ID, "input-12")
                campo_senha.send_keys(SENHA)
                
                # 3. CLIQUE NO BOTÃO ENTRAR (MÉTODO ROBUSTO)
                st.write("🖱️ Clicando no botão Entrar...")
                try:
                    # Busca o botão pelo texto contido nele (independente de ser button, span ou div)
                    botao_entrar = wait.until(EC.element_to_be_clickable(
                        (By.XPATH, "//*[contains(text(), 'Entrar')]")
                    ))
                    # Força o clique via JavaScript (mais garantido)
                    driver.execute_script("arguments[0].click();", botao_entrar)
                except:
                    # Fallback caso o XPATH falhe
                    botao_fallback = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
                    driver.execute_script("arguments[0].click();", botao_fallback)
                
                # 4. TRANSIÇÃO
                st.write("⏳ Aguardando autenticação e redirecionamento...")
                time.sleep(10) # Tempo maior para garantir o login
                
                # 5. ACESSO AO AMHPTISS
                st.write("📂 Acessando AMHPTISS...")
                driver.get("https://amhptiss.amhp.com.br/Default.aspx")
                time.sleep(5)
                
                # 6. VERIFICAÇÃO FINAL
                if "Default.aspx" in driver.current_url:
                    st.success("✅ Login realizado com sucesso no AMHPTISS!")
                    st.info(f"Página Atual: {driver.current_url}")
                else:
                    st.warning("⚠️ Não foi possível confirmar o redirecionamento.")
                    # Captura tela para debug
                    driver.save_screenshot("debug_tela.png")
                    st.image("debug_tela.png", caption="Última tela visualizada pelo robô")
                
                status.update(label="Processo finalizado!", state="complete", expanded=False)

        except Exception as e:
            st.error(f"🚨 Erro durante a execução: {e}")
            # Em caso de erro, tenta mostrar onde o robô parou
            try:
                driver.save_screenshot("erro_fatal.png")
                st.image("erro_fatal.png", caption="Tela do erro")
            except:
                pass
        finally:
            driver.quit()
