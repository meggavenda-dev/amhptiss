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
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service("/usr/bin/chromedriver")
    try:
        return webdriver.Chrome(service=service, options=options)
    except:
        return webdriver.Chrome(options=options)

st.title("🏥 Automação AMHP - Atendimentos")

USUARIO = st.secrets["credentials"]["usuario"]
SENHA = st.secrets["credentials"]["senha"]

if st.button("🚀 Iniciar Relatório"):
    driver = iniciar_driver()
    if driver:
        try:
            with st.status("Iniciando processo...", expanded=True) as status:
                
                # 1. LOGIN E TRANSIÇÃO
                st.write("🌐 Fazendo login no Portal...")
                driver.get("https://portal.amhp.com.br/")
                wait = WebDriverWait(driver, 30)
                
                wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(USUARIO)
                driver.find_element(By.ID, "input-12").send_keys(SENHA + Keys.ENTER)

                time.sleep(12) 
                
                st.write("🖱️ Acessando AMHPTISS...")
                botao_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
                driver.execute_script("arguments[0].click();", botao_tiss)
                
                time.sleep(8)
                if len(driver.window_handles) > 1:
                    driver.switch_to.window(driver.window_handles[1])

                # 2. TRATAR INFORMATIVO
                try:
                    btn_fechar = WebDriverWait(driver, 7).until(EC.element_to_be_clickable((By.ID, "fechar-informativo")))
                    driver.execute_script("arguments[0].click();", btn_fechar)
                    st.write("✅ Informativo fechado.")
                except:
                    st.write("ℹ️ Sem informativo.")

                # 3. NAVEGAÇÃO DETALHADA
                st.write("📂 Navegando: Ir Para > Consultório > Atendimentos...")

                # Passo A: Clicar em "Ir Para"
                ir_para = wait.until(EC.element_to_be_clickable((By.ID, "IrPara")))
                driver.execute_script("arguments[0].click();", ir_para)
                time.sleep(2)

                # Passo B: Clicar em "Consultório" (Usando a classe rtIn que você passou)
                consultorio = wait.until(EC.element_to_be_clickable((By.XPATH, "//span[@class='rtIn' and contains(text(), 'Consultório')]")))
                driver.execute_script("arguments[0].click();", consultorio)
                time.sleep(2)

                # Passo C: Clicar em "Atendimentos Realizados"
                atendimentos = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']")))
                driver.execute_script("arguments[0].click();", atendimentos)
                
                # 4. FINALIZAÇÃO
                st.write("⏳ Carregando tela de relatório...")
                time.sleep(7)
                
                st.success(f"📍 Chegamos! Página: {driver.title}")
                driver.save_screenshot("tela_final.png")
                st.image("tela_final.png", caption="Tela de Atendimentos Realizados")

                status.update(label="Navegação Concluída!", state="complete", expanded=False)

        except Exception as e:
            st.error(f"🚨 Erro na navegação: {e}")
            driver.save_screenshot("erro_nav.png")
            st.image("erro_nav.png")
        finally:
            driver.quit()
