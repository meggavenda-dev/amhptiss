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

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="AMHP Data Analytics", layout="wide")

if 'db_consolidado' not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame()

DOWNLOAD_DIR = os.path.join(os.getcwd(), "temp_downloads")

def preparar_ambiente():
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR)

# --- FUNÇÃO DE LEITURA (XLRD) ---
def processar_xls_amhp(caminho_arquivo, status_nome, neg_nome):
    try:
        import xlrd
        workbook = xlrd.open_workbook(caminho_arquivo)
        sheet = workbook.sheet_by_index(0)
        dados = [sheet.row_values(i) for i in range(sheet.nrows)]
        df_bruto = pd.DataFrame(dados)

        idx = -1
        for i, row in df_bruto.iterrows():
            if "Atendimento" in str(row.values) and "Guia" in str(row.values):
                idx = i
                break
        
        if idx == -1: return False

        df = df_bruto.iloc[idx+1:].copy()
        df.columns = df_bruto.iloc[idx]
        df = df.loc[:, df.columns.notnull()].dropna(how='all', axis=0)
        
        # Limpeza de caracteres ilegais para o CSV
        df = df.applymap(lambda x: re.sub(r'[^\x20-\x7E\xA0-\xFF]', '', str(x)) if pd.notnull(x) else x)
        
        df['Filtro_Status'] = status_nome
        df['Filtro_Negociacao'] = neg_nome
        
        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df], ignore_index=True)
        return True
    except Exception as e:
        st.error(f"Erro na leitura do arquivo: {e}")
        return False

# --- CONFIGURAÇÃO DO DRIVER ---
def configurar_driver():
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    prefs = {"download.default_directory": DOWNLOAD_DIR, "download.prompt_for_download": False}
    opts.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(options=opts)

# --- INTERFACE ---
st.title("🏥 Consolidador AMHP - Estabilidade Máxima")

with st.sidebar:
    st.header("Datas")
    d_ini = st.date_input("Data Inicial", value=pd.to_datetime("2026-01-01"))
    d_fim = st.date_input("Data Final", value=pd.to_datetime("2026-01-13"))

if st.button("🚀 Executar Automação"):
    preparar_ambiente()
    driver = configurar_driver()
    wait = WebDriverWait(driver, 45) # Timeout estendido
    
    try:
        with st.status("Processando...", expanded=True) as s:
            # 1. Login
            driver.get("https://portal.amhp.com.br/")
            wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            
            time.sleep(12)
            
            # 2. Clique no sistema TISS
            st.write("🔗 Abrindo AMHPTISS...")
            btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
            driver.execute_script("arguments[0].click();", btn_tiss)
            
            # --- CRUCIAL: GESTÃO DE JANELAS ---
            # Espera até que uma nova aba seja aberta e muda o foco
            wait.until(lambda d: len(d.window_handles) > 1)
            driver.switch_to.window(driver.window_handles[-1])
            st.write("✔️ Foco alterado para a aba do relatório.")
            time.sleep(5)

            # 3. Navegação (Usando JS para evitar interceptação)
            driver.execute_script("document.getElementById('IrPara').click();")
            time.sleep(2)
            btn_cons = wait.until(EC.presence_of_element_located((By.XPATH, "//span[text()='Consultório']")))
            driver.execute_script("arguments[0].click();", btn_cons)
            
            link_atend = wait.until(EC.presence_of_element_located((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']")))
            driver.execute_script("arguments[0].click();", link_atend)
            
            # 4. Filtros de Data
            st.write("📅 Aplicando filtros...")
            wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput"))).send_keys(d_ini.strftime("%d/%m/%Y") + Keys.TAB)
            driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(d_fim.strftime("%d/%m/%Y") + Keys.TAB)
            
            driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input"))
            
            # 5. Exportação
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
            driver.execute_script("document.getElementById('ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox').click();")
            time.sleep(3)
            driver.execute_script("document.getElementById('ctl00_MainContent_rbtImprimirAtendimentos_input').click();")
            
            # 6. Iframe de Download
            time.sleep(15)
            if len(driver.find_elements(By.TAG_NAME, "iframe")) > 0:
                driver.switch_to.frame(0)
            
            ddl = Select(wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList"))))
            ddl.select_by_value("XLS")
            time.sleep(2)
            driver.execute_script("document.getElementById('ReportView_ReportToolbar_ExportGr_Export').click();")
            
            st.write("📥 Aguardando download...")
            time.sleep(25)

            # 7. Processamento
            arquivos = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.xls')]
            if arquivos:
                recente = max(arquivos, key=os.path.getctime)
                if processar_xls_amhp(recente, "300", "Direto"):
                    st.success("✅ Dados extraídos!")
                os.remove(recente)
            else:
                st.error("O arquivo não foi detectado na pasta de download.")

            s.update(label="Concluído!", state="complete")
            
    except Exception as e:
        st.error(f"Erro Crítico: {e}")
    finally:
        driver.quit()

# --- ÁREA DE DOWNLOAD ---
if not st.session_state.db_consolidado.empty:
    st.divider()
    st.dataframe(st.session_state.db_consolidado)
    csv = st.session_state.db_consolidado.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')
    st.download_button("💾 Baixar Relatório Final", csv, "consolidado.csv", "text/csv")
    if st.button("🗑️ Resetar"):
        st.session_state.db_consolidado = pd.DataFrame()
        st.rerun()
