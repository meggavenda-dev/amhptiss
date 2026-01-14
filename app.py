import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

# --- CONFIGURAÇÃO DA INTERFACE ---
st.set_page_config(page_title="Automação AMHPTISS", page_icon="🏥", layout="wide")

def iniciar_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--ignore-certificate-errors")
    
    # Caminho do driver no Streamlit Cloud (via packages.txt)
    try:
        service = Service("/usr/bin/chromedriver")
        return webdriver.Chrome(service=service, options=options)
    except:
        # Fallback para execução local
        return webdriver.Chrome(options=options)

# --- TÍTULO E VERIFICAÇÃO ---
st.title("🏥 Automação de Relatórios AMHP")
st.markdown("---")

if "credentials" not in st.secrets:
    st.error("❌ Erro: Secrets não configurados.")
    st.stop()

USUARIO = st.secrets["credentials"]["usuario"]
SENHA = st.secrets["credentials"]["senha"]

if st.button("🚀 Iniciar Automação e Acessar TISS"):
    driver = iniciar_driver()
    
    if driver:
        try:
            with st.status("Executando fluxo de acesso...", expanded=True) as status:
                
                # PASSO 1: Portal de Login
                st.write("🌐 Acessando o portal de login...")
                driver.get("https://portal.amhp.com.br/")
                wait = WebDriverWait(driver, 30)
                
                # PASSO 2: Preenchimento
                st.write("🔑 Inserindo credenciais...")
                campo_login = wait.until(EC.presence_of_element_located((By.ID, "input-9")))
                campo_login.send_keys(USUARIO)
                
                campo_senha = driver.find_element(By.ID, "input-12")
                campo_senha.send_keys(SENHA)
                
                # PASSO 3: Clique no Botão
                st.write("🖱️ Localizando botão 'Entrar'...")
                time.sleep(2) # Pausa técnica para o framework carregar o clique
                
                # Busca flexível por qualquer elemento com o texto 'Entrar'
                botao = wait.until(EC.element_to_be_clickable(
                    (By.XPATH, "//*[contains(text(), 'Entrar')] | //*[contains(text(), 'ENTRAR')]")
                ))
                driver.execute_script("arguments[0].click();", botao)
                
                # PASSO 4: Aguardar Processamento da Sessão
                st.write("⏳ Login disparado. Aguardando processamento da sessão (10s)...")
                time.sleep(10) 
                
                # PASSO 5: Acesso ao AMHPTISS
                st.write("📂 Solicitando página do AMHPTISS...")
                driver.get("https://amhptiss.amhp.com.br/Default.aspx")
                
                # PASSO 6: Verificação com Captura de Tela
                time.sleep(5)
                url_final = driver.current_url
                
                if "Default.aspx" in url_final or "Home" in driver.title:
                    st.success("✅ Login realizado com sucesso no AMHPTISS!")
                    st.balloons()
                    
                    # Tira print para sabermos o que o robô está vendo
                    driver.save_screenshot("tela_logada.png")
                    st.image("tela_logada.png", caption="Visão atual do sistema logado")
                    
                    st.info("💡 Analise a imagem acima. Se você estiver na Home, podemos prosseguir com os menus do relatório.")
                else:
                    st.warning(f"⚠️ Redirecionamento inconclisivo. URL atual: {url_final}")
                    driver.save_screenshot("debug_acesso.png")
                    st.image("debug_acesso.png", caption="Tela de captura (Debug)")
                
                status.update(label="Fluxo Finalizado", state="complete", expanded=False)

        except Exception as e:
            st.error(f"🚨 Erro crítico: {e}")
            try:
                driver.save_screenshot("erro_fatal.png")
                st.image("erro_fatal.png", caption="Estado da tela no momento do erro")
            except: pass
        finally:
            driver.quit()

# --- RODAPÉ ---
st.markdown("---")
st.caption("Desenvolvido para automação de faturamento médico - AMHP")
