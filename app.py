import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

# Configuração da página Streamlit
st.set_page_config(page_title="Automação AMHP", layout="centered")

def configurar_driver():
    options = Options()
    options.add_argument("--headless") # Roda sem abrir janela (necessário para nuvem)
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    
    # Gerencia a instalação do driver automaticamente
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    return driver

st.title("🚀 Gerador de Relatórios AMHP")
st.markdown("Insira seus dados para acessar o portal e o AMHPTISS.")

# Form de Login
with st.form("login_form"):
    user_input = st.text_input("Usuário / CPF")
    pass_input = st.text_input("Senha", type="password")
    submit_button = st.form_submit_button("Iniciar Automação")

if submit_button:
    if not user_input or not pass_input:
        st.error("Por favor, preencha todos os campos.")
    else:
        driver = configurar_driver()
        try:
            with st.status("Executando passos...", expanded=True) as status:
                # Passo 1: Login no Portal Principal
                st.write("Acessando portal.amhp.com.br...")
                driver.get("https://portal.amhp.com.br/")
                
                wait = WebDriverWait(driver, 20)
                
                st.write("Inserindo credenciais...")
                campo_login = wait.until(EC.presence_of_element_located((By.ID, "input-9")))
                campo_login.send_keys(user_input)
                
                campo_senha = driver.find_element(By.ID, "input-12")
                campo_senha.send_keys(pass_input)
                
                botao_entrar = driver.find_element(By.XPATH, "//button[contains(., 'Entrar')]")
                botao_entrar.click()
                
                # Passo 2: Transição
                st.write("Aguardando autenticação...")
                time.sleep(5) 
                
                # Passo 3: Acesso ao AMHPTISS
                st.write("Navegando para AMHPTISS...")
                driver.get("https://amhptiss.amhp.com.br/Default.aspx")
                
                # Verificação final
                if "Default.aspx" in driver.current_url:
                    st.success("Logado com sucesso no sistema TISS!")
                    # Aqui você continuará com a lógica do relatório
                else:
                    st.error("Falha ao atingir a página final. Verifique o login.")
                
                status.update(label="Processo Concluído!", state="complete", expanded=False)

        except Exception as e:
            st.error(f"Ocorreu um erro: {e}")
        finally:
            driver.quit()
