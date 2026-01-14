import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Automação AMHP", page_icon="🏥", layout="centered")

# --- FUNÇÃO PARA CONFIGURAR O NAVEGADOR ---
def iniciar_driver():
    options = Options()
    options.add_argument("--headless")  # Roda sem interface gráfica (obrigatório para nuvem)
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    # Gerencia a instalação do driver automaticamente
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    return driver

# --- INTERFACE DO USUÁRIO ---
st.title("🚀 Automação de Relatórios AMHP")
st.info("Este programa realiza o login automático e acessa o portal AMHPTISS.")

# Recuperando credenciais dos Secrets
try:
    USUARIO = st.secrets["credentials"]["usuario"]
    SENHA = st.secrets["credentials"]["senha"]
except KeyError:
    st.error("⚠️ Erro: Credenciais não encontradas nos Secrets do Streamlit.")
    st.stop()

if st.button("Iniciar Processamento"):
    driver = iniciar_driver()
    
    try:
        with st.status("Executando automação...", expanded=True) as status:
            
            # PASSO 1: Acessar Portal Principal
            st.write("🌍 Acessando o portal AMHP...")
            driver.get("https://portal.amhp.com.br/")
            wait = WebDriverWait(driver, 25)
            
            # PASSO 2: Realizar Login
            st.write("🔑 Inserindo credenciais...")
            
            # Localiza campo de login (ID input-9)
            campo_login = wait.until(EC.presence_of_element_located((By.ID, "input-9")))
            campo_login.send_keys(USUARIO)
            
            # Localiza campo de senha (ID input-12)
            campo_senha = driver.find_element(By.ID, "input-12")
            campo_senha.send_keys(SENHA)
            
            # Clica no botão Entrar
            botao_entrar = driver.find_element(By.XPATH, "//button[contains(., 'Entrar')]")
            botao_entrar.click()
            
            # PASSO 3: Aguardar Autenticação
            st.write("⏳ Aguardando processamento do login...")
            time.sleep(7) # Tempo de segurança para o redirecionamento do portal
            
            # PASSO 4: Navegar para AMHPTISS
            st.write("📂 Acessando AMHPTISS...")
            driver.get("https://amhptiss.amhp.com.br/Default.aspx")
            
            # Pequena espera para carregar a página ASPX
            time.sleep(5)
            
            # PASSO 5: Verificação de Sucesso
            url_atual = driver.current_url
            if "amhptiss" in url_atual.lower():
                st.success("✅ Sucesso! Você está dentro do AMHPTISS.")
                st.write(f"**Página atual:** {url_atual}")
                # Aqui você poderá adicionar os próximos cliques para gerar o relatório
            else:
                st.error("❌ Falha no redirecionamento. Verifique se o login foi bem-sucedido.")
                # Tira um print caso dê erro para ajudar no debug
                driver.save_screenshot("erro_login.png")
                st.image("erro_login.png", caption="Tela de erro capturada")

            status.update(label="Processo Finalizado!", state="complete", expanded=False)

    except Exception as e:
        st.error(f"🚨 Ocorreu um erro inesperado: {e}")
    
    finally:
        driver.quit()
