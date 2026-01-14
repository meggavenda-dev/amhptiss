import streamlit as st
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
import time
import os
import shutil

# --- CONFIGURAÇÃO DE CAMINHOS ---
# Correção: No Streamlit Cloud, usamos apenas caminhos relativos ao projeto
DOWNLOAD_TEMPORARIO = os.path.join(os.getcwd(), "temp_downloads")

def preparar_ambiente():
    if os.path.exists(DOWNLOAD_TEMPORARIO):
        shutil.rmtree(DOWNLOAD_TEMPORARIO)
    os.makedirs(DOWNLOAD_TEMPORARIO, exist_ok=True)
    if 'db_consolidado' not in st.session_state:
        st.session_state.db_consolidado = pd.DataFrame()

def iniciar_driver():
    options = Options()
    options.add_argument("--headless") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    prefs = {
        "download.default_directory": DOWNLOAD_TEMPORARIO,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    options.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(options=options)

st.title("🏥 Exportador AMHP - Processador de Dados")

col1, col2 = st.columns(2)
with col1: data_ini = st.text_input("📅 Data Inicial", value="01/01/2026")
with col2: data_fim = st.text_input("📅 Data Final", value="13/01/2026")

if st.button("🚀 Iniciar Processo"):
    preparar_ambiente()
    driver = iniciar_driver()
    
    try:
        with st.status("Trabalhando...", expanded=True) as status:
            wait = WebDriverWait(driver, 35)
            
            # 1. LOGIN
            driver.get("https://portal.amhp.com.br/")
            wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            time.sleep(12)
            
            # 2. ENTRAR NO AMHPTISS
            st.write("🔄 Acessando TISS...")
            btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
            driver.execute_script("arguments[0].click();", btn_tiss)
            time.sleep(10)
            if len(driver.window_handles) > 1: driver.switch_to.window(driver.window_handles[-1])

            # 3. LIMPEZA E NAVEGAÇÃO
            driver.execute_script("document.querySelectorAll('center, .loading, .overlay, #fechar-informativo').forEach(el => el.remove());")
            
            ir_para = wait.until(EC.element_to_be_clickable((By.ID, "IrPara")))
            driver.execute_script("arguments[0].click();", ir_para)
            time.sleep(2)

            consultorio = wait.until(EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Consultório')]")))
            driver.execute_script("arguments[0].click();", consultorio)
            
            atendimentos = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']")))
            driver.execute_script("arguments[0].click();", atendimentos)
            time.sleep(5)

            # 4. FILTROS
            st.write("📝 Filtrando...")
            # (Mantendo sua lógica de preenchimento de datas)
            driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput").send_keys(data_ini + Keys.TAB)
            driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(data_fim + Keys.TAB)

            # 5. BUSCAR E EXPORTAR
            btn_buscar = driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input")
            driver.execute_script("arguments[0].click();", btn_buscar)
            
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
            driver.execute_script("document.getElementById('ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox').click();")
            time.sleep(3)
            driver.execute_script("document.getElementById('ctl00_MainContent_rbtImprimirAtendimentos_input').click();")
            
            time.sleep(15)
            # Switch to Frame para exportar
            wait.until(EC.frame_to_be_available_and_switch_to_it((By.TAG_NAME, "iframe")))
            
            dropdown = wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
            Select(dropdown).select_by_value("XLS")
            driver.execute_script("document.getElementById('ReportView_ReportToolbar_ExportGr_Export').click();")
            
            st.write("📥 Baixando arquivo...")
            time.sleep(20)

            # 6. LEITURA DOS DADOS (O que faltava)
            arquivos = [os.path.join(DOWNLOAD_TEMPORARIO, f) for f in os.listdir(DOWNLOAD_TEMPORARIO)]
            if arquivos:
                recente = max(arquivos, key=os.path.getctime)
                # Lendo o XLS baixado
                df_novo = pd.read_excel(recente)
                st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df_novo], ignore_index=True)
                st.success("✅ Dados capturados e adicionados ao banco!")
            else:
                st.error("Arquivo não foi baixado corretamente.")

            status.update(label="Processo Concluído!", state="complete")

    except Exception as e:
        st.error(f"Erro: {e}")
    finally:
        driver.quit()

# --- EXIBIÇÃO DO BANCO ---
if not st.session_state.db_consolidado.empty:
    st.divider()
    st.subheader("📊 Dados Acumulados")
    st.dataframe(st.session_state.db_consolidado)
    
    csv = st.session_state.db_consolidado.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')
    st.download_button("💾 Baixar Tudo (CSV)", csv, "consolidado_amhp.csv", "text/csv")
