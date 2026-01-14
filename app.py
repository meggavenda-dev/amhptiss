import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

# ... (Configuração do driver igual ao exemplo anterior)

if st.button("Executar Automação"):
    driver = iniciar_driver()
    try:
        wait = WebDriverWait(driver, 20)
        
        # 1. Acessar o Portal
        driver.get("https://portal.amhp.com.br/")
        
        # 2. Inserir Login
        campo_login = wait.until(EC.presence_of_element_located((By.ID, "input-9")))
        campo_login.send_keys(usuario)
        
        # 3. Inserir Senha (ID confirmado: input-12)
        campo_senha = driver.find_element(By.ID, "input-12")
        campo_senha.send_keys(senha)
        
        # 4. Clicar no Botão Entrar
        # Usaremos o texto do botão já que o ID pode mudar
        botao_entrar = driver.find_element(By.XPATH, "//button[contains(., 'Entrar')]")
        botao_entrar.click()
        
        st.info("Login realizado. Aguardando redirecionamento...")
        
        # 5. Navegar para o AMHPTISS
        # Espera o login ser processado antes de mudar de URL
        time.sleep(5) 
        driver.get("https://amhptiss.amhp.com.br/Default.aspx")
        
        # Verificação de sucesso
        if "Default.aspx" in driver.current_url:
            st.success("Acesso ao AMHPTISS confirmado!")
        else:
            st.warning("Pode ter ocorrido um erro no login. Verifique as credenciais.")

    except Exception as e:
        st.error(f"Erro: {e}")
    finally:
        driver.quit()
