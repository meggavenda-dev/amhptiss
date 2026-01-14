import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
import time

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Automação AMHP", layout="wide")

def iniciar_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    # Máscara de navegador real
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service("/usr/bin/chromedriver")
    try:
        return webdriver.Chrome(service=service, options=options)
    except:
        return webdriver.Chrome(options=options)

# --- INTERFACE STREAMLIT ---
st.title("🏥 Gerador de Relatórios AMHP")
st.markdown("Preencha as datas e clique em gerar para buscar os atendimentos.")

col1, col2 = st.columns(2)
with col1:
    data_inicio_input = st.text_input("📅 Data Inicial", value="01/01/2026", placeholder="DD/MM/AAAA")
with col2:
    data_fim_input = st.text_input("📅 Data Final", value="13/01/2026", placeholder="DD/MM/AAAA")

# Credenciais dos Secrets
USUARIO = st.secrets["credentials"]["usuario"]
SENHA = st.secrets["credentials"]["senha"]

if st.button("🚀 Gerar Relatório"):
    driver = iniciar_driver()
    if driver:
        try:
            with st.status("Processando automação...", expanded=True) as status:
                wait = WebDriverWait(driver, 40)
                
                # 1. LOGIN NO PORTAL
                st.write("🔐 Acessando e autenticando no portal...")
                driver.get("https://portal.amhp.com.br/")
                
                campo_u = wait.until(EC.element_to_be_clickable((By.ID, "input-9")))
                campo_u.send_keys(USUARIO)
                
                campo_s = driver.find_element(By.ID, "input-12")
                campo_s.send_keys(SENHA + Keys.ENTER)
                time.sleep(12) 
                
                # 2. ENTRAR NO AMHPTISS
                st.write("🖱️ Entrando no sistema AMHPTISS...")
                btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
                driver.execute_script("arguments[0].click();", btn_tiss)
                time.sleep(10)
                
                # Troca de aba se necessário
                if len(driver.window_handles) > 1:
                    driver.switch_to.window(driver.window_handles[1])

                # Fechar informativo pop-up
                try:
                    btn_fechar = wait.until(EC.element_to_be_clickable((By.ID, "fechar-informativo")))
                    driver.execute_script("arguments[0].click();", btn_fechar)
                    st.write("✅ Informativo fechado.")
                except: pass

                # 3. NAVEGAÇÃO NO MENU
                st.write("📂 Navegando: Ir Para > Consultório > Atendimentos...")
                wait.until(EC.element_to_be_clickable((By.ID, "IrPara"))).click()
                time.sleep(1)
                wait.until(EC.element_to_be_clickable((By.XPATH, "//span[@class='rtIn' and contains(text(), 'Consultório')]"))).click()
                time.sleep(1)
                wait.until(EC.element_to_be_clickable((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))).click()
                time.sleep(5)

                # 4. PREENCHIMENTO DOS FILTROS
                st.write("📝 Aplicando filtros de negociação e status...")
                
                # Negociação: Direto
                neg = wait.until(EC.element_to_be_clickable((By.ID, "ctl00_MainContent_rcbTipoNegociacao_Input")))
                neg.click()
                driver.execute_script("arguments[0].value = '';", neg)
                neg.send_keys("Direto" + Keys.ENTER)
                time.sleep(2)

                # Status: 300
                stat = wait.until(EC.element_to_be_clickable((By.ID, "ctl00_MainContent_rcbStatus_Input")))
                stat.click()
                driver.execute_script("arguments[0].value = '';", stat)
                stat.send_keys("300 - Pronto para Processamento" + Keys.ENTER)
                time.sleep(2)

                # DATAS (Correção para campos Telerik)
                st.write(f"📅 Preenchendo datas: {data_inicio_input} a {data_fim_input}")
                
                def forcar_data(id_campo, valor):
                    el = driver.find_element(By.ID, id_campo)
                    el.click()
                    driver.execute_script("arguments[0].value = '';", el)
                    el.send_keys(valor)
                    el.send_keys(Keys.TAB)
                    time.sleep(1)

                forcar_data("ctl00_MainContent_rdpDigitacaoDataInicio_dateInput", data_inicio_input)
                forcar_data("ctl00_MainContent_rdpDigitacaoDataFim_dateInput", data_fim_input)

                # 5. BUSCAR
                st.write("🔍 Buscando resultados...")
                btn_buscar = driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input")
                driver.execute_script("arguments[0].click();", btn_buscar)
                
                # Espera o carregamento da tabela (Grid)
                st.write("⏳ O sistema está processando o relatório...")
                try:
                    # Aguarda até 60s pela presença da tabela de dados
                    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable, #ctl00_MainContent_gvAtendimentos")))
                    st.write("✅ Relatório gerado com sucesso!")
                except:
                    st.write("⚠️ O tempo de resposta foi alto, capturando tela atual.")

                # Finalização e Print
                time.sleep(2)
                driver.save_screenshot("relatorio_final.png")
                st.image("relatorio_final.png", caption="Resultado da Busca no AMHPTISS")
                
                status.update(label="Processo concluído!", state="complete", expanded=False)

        except Exception as e:
            st.error(f"🚨 Ocorreu um erro: {e}")
            driver.save_screenshot("erro_detalhado.png")
            st.image("erro_detalhado.png")
        finally:
            driver.quit()
