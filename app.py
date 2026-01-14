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
st.set_page_config(page_title="AMHP Analytics PRO", layout="wide")

if 'db_consolidado' not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame()

DOWNLOAD_DIR = os.path.join(os.getcwd(), "temp_downloads")

def limpar_pasta():
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR)

# --- LEITURA BINÁRIA XLS ---
def processar_xls_amhp(caminho_arquivo, status_nome, neg_nome):
    try:
        import xlrd
        workbook = xlrd.open_workbook(caminho_arquivo)
        sheet = workbook.sheet_by_index(0)
        dados = [sheet.row_values(i) for i in range(sheet.nrows)]
        df_temp = pd.DataFrame(dados)

        idx = -1
        for i, linha in df_temp.iterrows():
            if "Atendimento" in str(linha.values) and "Guia" in str(linha.values):
                idx = i
                break
        
        if idx == -1: return False

        df = df_temp.iloc[idx+1:].copy()
        df.columns = df_temp.iloc[idx]
        df = df.loc[:, df.columns.notnull()].dropna(how='all', axis=0)
        
        # Sanitização para evitar o erro IllegalCharacter
        df = df.applymap(lambda x: re.sub(r'[^\x20-\x7E\xA0-\xFF]', '', str(x)) if pd.notnull(x) else x)
        
        df['Filtro_Status'] = status_nome
        df['Filtro_Negociacao'] = neg_nome
        
        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df], ignore_index=True)
        return True
    except Exception as e:
        st.error(f"Erro ao ler ficheiro: {e}")
        return False

# --- MOTOR DO ROBÔ ---
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
    st.header("Datas do Relatório")
    d_ini = st.date_input("Início", value=pd.to_datetime("2026-01-01"))
    d_fim = st.date_input("Fim", value=pd.to_datetime("2026-01-13"))

if st.button("🚀 Iniciar Captura"):
    limpar_pasta()
    driver = configurar_driver()
    wait = WebDriverWait(driver, 40)
    
    try:
        with st.status("Robô em execução...", expanded=True) as s:
            # 1. Login
            driver.get("https://portal.amhp.com.br/")
            wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            
            # 2. Transição para AMHPTISS
            time.sleep(10)
            btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
            driver.execute_script("arguments[0].click();", btn_tiss)
            
            # --- CORREÇÃO DO STACKTRACE: TROCA DE JANELA ---
            wait.until(lambda d: len(d.window_handles) > 1)
            driver.switch_to.window(driver.window_handles[-1])
            st.write("🔄 Foco alterado para a aba do sistema...")
            time.sleep(5)

            # 3. Navegação Interna
            driver.execute_script("document.getElementById('IrPara').click();")
            time.sleep(2)
            wait.until(EC.presence_of_element_located((By.XPATH, "//span[text()='Consultório']"))).click()
            wait.until(EC.presence_of_element_located((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))).click()
            
            # 4. Filtros
            wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput"))).send_keys(d_ini.strftime("%d/%m/%Y") + Keys.TAB)
            driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(d_fim.strftime("%d/%m/%Y") + Keys.TAB)
            driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input"))
            
            # 5. Exportação
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
            driver.execute_script("document.getElementById('ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox').click();")
            time.sleep(3)
            driver.execute_script("document.getElementById('ctl00_MainContent_rbtImprimirAtendimentos_input').click();")
            
            # 6. Iframe e Download
            time.sleep(15)
            if len(driver.find_elements(By.TAG_NAME, "iframe")) > 0:
                driver.switch_to.frame(0)
            
            select = Select(wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList"))))
            select.select_by_value("XLS")
            time.sleep(2)
            driver.execute_script("document.getElementById('ReportView_ReportToolbar_ExportGr_Export').click();")
            
            st.write("📥 Aguardando conclusão do download...")
            time.sleep(20)

            # 7. Processamento
            ficheiros = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.xls')]
            if ficheiros:
                recente = max(ficheiros, key=os.path.getctime)
                if processar_xls_amhp(recente, "300", "Direto"):
                    st.success("✅ Relatório consolidado!")
                os.remove(recente)
            else:
                st.error("Erro: O ficheiro não foi gerado a tempo.")

            s.update(label="Processo terminado!", state="complete")
            
    except Exception as e:
        st.error(f"Erro Crítico: {e}")
    finally:
        driver.quit()

# --- EXIBIÇÃO ---
if not st.session_state.db_consolidado.empty:
    st.divider()
    st.dataframe(st.session_state.db_consolidado)
    csv = st.session_state.db_consolidado.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')
    st.download_button("💾 Descarregar Base Completa", csv, "consolidado.csv", "text/csv")
    if st.button("🗑️ Limpar"):
        st.session_state.db_consolidado = pd.DataFrame()
        st.rerun()
