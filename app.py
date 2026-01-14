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
import re
import shutil
import pdfplumber

# --- CONFIGURAÇÃO ---
DOWNLOAD_TEMPORARIO = os.path.join(os.getcwd(), "temp_downloads")

def preparar_ambiente():
    if os.path.exists(DOWNLOAD_TEMPORARIO):
        shutil.rmtree(DOWNLOAD_TEMPORARIO)
    os.makedirs(DOWNLOAD_TEMPORARIO, exist_ok=True)
    if 'db_consolidado' not in st.session_state:
        st.session_state.db_consolidado = pd.DataFrame()

def processar_pdf_amhp(caminho_pdf):
    try:
        dados_lista = []
        with pdfplumber.open(caminho_pdf) as pdf:
            for pagina in pdf.pages:
                tabela = pagina.extract_table()
                if tabela:
                    df_temp = pd.DataFrame(tabela[1:], columns=tabela[0])
                    dados_lista.append(df_temp)
        if not dados_lista: return False
        df_final = pd.concat(dados_lista, ignore_index=True)
        df_final.columns = [str(c).replace('\n', ' ').strip() for c in df_final.columns]
        df_final = df_final.applymap(lambda x: re.sub(r'[^\x20-\x7E\xA0-\xFF]', '', str(x)) if pd.notnull(x) else x)
        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df_final], ignore_index=True)
        return True
    except Exception as e:
        st.error(f"Erro PDF: {e}")
        return False

def iniciar_driver():
    options = Options()
    options.add_argument("--headless") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    prefs = {
        "download.default_directory": DOWNLOAD_TEMPORARIO,
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True 
    }
    options.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(options=options)

st.title("🏥 Consolidador AMHP - Anti-Erro Mode")

data_ini = st.text_input("Data Inicial", value="01/01/2026")
data_fim = st.text_input("Data Final", value="13/01/2026")

if st.button("🚀 Executar Captura"):
    preparar_ambiente()
    driver = iniciar_driver()
    wait = WebDriverWait(driver, 45) # Aumentado para 45s
    
    try:
        with st.status("Processando...", expanded=True) as status:
            # 1. LOGIN
            driver.get("https://portal.amhp.com.br/")
            wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            
            # 2. TRANSIÇÃO
            time.sleep(15) # Tempo extra para o dashboard
            st.write("🔄 Entrando no TISS...")
            btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
            driver.execute_script("arguments[0].click();", btn_tiss)
            
            # Espera carregar a nova aba e troca
            wait.until(lambda d: len(d.window_handles) > 1)
            driver.switch_to.window(driver.window_handles[-1])
            
            # 3. NAVEGAÇÃO INTERNA
            st.write("📂 Navegando menus...")
            wait.until(EC.presence_of_element_located((By.ID, "IrPara")))
            # Pequena pausa para garantir que o menu JS está pronto
            time.sleep(3)
            driver.execute_script("document.getElementById('IrPara').click();")
            
            cons = wait.until(EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Consultório')]")))
            driver.execute_script("arguments[0].click();", cons)
            
            atend = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']")))
            driver.execute_script("arguments[0].click();", atend)
            
            # 4. FILTROS
            st.write("📝 Aplicando filtros...")
            wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput"))).send_keys(data_ini + Keys.TAB)
            driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(data_fim + Keys.TAB)
            
            btn_buscar = driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input")
            driver.execute_script("arguments[0].click();", btn_buscar)
            
            # 5. EXPORTAÇÃO (Onde o erro costuma ocorrer)
            st.write("🔍 Gerando Relatório...")
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
            time.sleep(2)
            driver.execute_script("document.getElementById('ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox').click();")
            time.sleep(2)
            driver.execute_script("document.getElementById('ctl00_MainContent_rbtImprimirAtendimentos_input').click();")
            
            # 6. MANIPULAÇÃO DO IFRAME (Crucial para evitar o Stacktrace)
            time.sleep(15)
            # Em vez de apenas switch_to.frame(0), vamos esperar o iframe existir
            wait.until(EC.frame_to_be_available_and_switch_to_it((By.TAG_NAME, "iframe")))
            
            st.write("💾 Selecionando formato PDF...")
            dropdown = wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
            Select(dropdown).select_by_value("PDF")
            time.sleep(2)
            
            btn_export = wait.until(EC.element_to_be_clickable((By.ID, "ReportView_ReportToolbar_ExportGr_Export")))
            driver.execute_script("arguments[0].click();", btn_export)
            
            st.write("📥 Baixando e processando...")
            time.sleep(25)

            # 7. FINALIZAÇÃO
            arquivos = [os.path.join(DOWNLOAD_TEMPORARIO, f) for f in os.listdir(DOWNLOAD_TEMPORARIO) if f.endswith('.pdf')]
            if arquivos:
                recente = max(arquivos, key=os.path.getctime)
                processar_pdf_amhp(recente)
                st.success("✅ Captura concluída com sucesso!")
            else:
                st.error("Arquivo não encontrado. Verifique se há dados no período.")
                
            status.update(label="Fim do Processo", state="complete")

    except Exception as e:
        st.error(f"Erro detectado: {e}")
        driver.save_screenshot("erro_log.png")
        st.image("erro_log.png")
    finally:
        driver.quit()

# --- EXIBIÇÃO ---
if not st.session_state.db_consolidado.empty:
    st.divider()
    st.dataframe(st.session_state.db_consolidado)
    csv = st.session_state.db_consolidado.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')
    st.download_button("💾 Baixar Tudo", csv, "consolidado.csv", "text/csv")
