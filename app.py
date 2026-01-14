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

st.title("🏥 Gerador de Relatórios AMHP")

# --- ENTRADA DE DADOS DO USUÁRIO ---
col1, col2 = st.columns(2)
with col1:
    data_inicio = st.text_input("📅 Data Inicial", placeholder="DD/MM/AAAA")
with col2:
    data_fim = st.text_input("📅 Data Final", placeholder="DD/MM/AAAA")

USUARIO = st.secrets["credentials"]["usuario"]
SENHA = st.secrets["credentials"]["senha"]

if st.button("🚀 Gerar Relatório"):
    if not data_inicio or not data_fim:
        st.error("Por favor, informe as datas inicial e final.")
    else:
        driver = iniciar_driver()
        if driver:
            try:
                with st.status("Executando automação...", expanded=True) as status:
                    
                    # 1. LOGIN E NAVEGAÇÃO (Mantendo o que já funciona)
                    st.write("🔐 Autenticando...")
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

                    # Fechar informativo
                    try:
                        btn_fechar = WebDriverWait(driver, 7).until(EC.element_to_be_clickable((By.ID, "fechar-informativo")))
                        driver.execute_script("arguments[0].click();", btn_fechar)
                    except: pass

                    # 2. NAVEGAÇÃO ATÉ A TELA
                    st.write("📂 Navegando para Atendimentos Realizados...")
                    wait.until(EC.element_to_be_clickable((By.ID, "IrPara"))).click()
                    time.sleep(1)
                    wait.until(EC.element_to_be_clickable((By.XPATH, "//span[@class='rtIn' and contains(text(), 'Consultório')]"))).click()
                    time.sleep(1)
                    wait.until(EC.element_to_be_clickable((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))).click()
                    time.sleep(5)

                    # 3. PREENCHIMENTO DOS FILTROS (Lógica Telerik)
                    st.write("📝 Preenchendo filtros do relatório...")

                    # Filtro Negociação: Direto
                    negocio = wait.until(EC.element_to_be_clickable((By.ID, "ctl00_MainContent_rcbTipoNegociacao_Input")))
                    negocio.clear()
                    negocio.send_keys("Direto")
                    time.sleep(1)
                    negocio.send_keys(Keys.ENTER)

                    # Filtro Status: 300 - Pronto para Processamento
                    status_campo = wait.until(EC.element_to_be_clickable((By.ID, "ctl00_MainContent_rcbStatus_Input")))
                    status_campo.clear()
                    status_campo.send_keys("300 - Pronto para Processamento")
                    time.sleep(1)
                    status_campo.send_keys(Keys.ENTER)

                    # Data Início
                    st.write(f"📅 Definindo período: {data_inicio} até {data_fim}")
                    dt_ini = driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput")
                    dt_ini.clear()
                    dt_ini.send_keys(data_inicio)

                    # Data Fim
                    dt_fim = driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput")
                    dt_fim.clear()
                    dt_fim.send_keys(data_fim)
                    time.sleep(1)

                    # 4. BOTÃO BUSCAR
                    st.write("🔍 Clicando em Buscar...")
                    btn_buscar = driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input")
                    driver.execute_script("arguments[0].click();", btn_buscar)
                    
                    # Espera a busca processar
                    st.write("⏳ Processando busca...")
                    time.sleep(8)

                    # 5. VERIFICAÇÃO
                    driver.save_screenshot("resultado_busca.png")
                    st.image("resultado_busca.png", caption="Resultado da Busca")
                    st.success("Busca finalizada! Verifique na imagem se os dados apareceram.")

                    status.update(label="Busca concluída!", state="complete", expanded=False)

            except Exception as e:
                st.error(f"🚨 Erro: {e}")
                driver.save_screenshot("erro_filtro.png")
                st.image("erro_filtro.png")
            finally:
                driver.quit()
