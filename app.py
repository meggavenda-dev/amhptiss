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
import io

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="AMHP Data Analytics", layout="wide")

if 'db_consolidado' not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame()

DOWNLOAD_DIR = os.path.join(os.getcwd(), "temp_downloads")
if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)

# --- FUNÇÃO DE LEITURA BINÁRIA (A CORREÇÃO) ---
def processar_xls_binario(caminho_arquivo, status_nome, neg_nome):
    try:
        # O xlrd 2.0.1+ só lê .xls antigos se forçar a abertura
        # O AMHP gera um arquivo que o Pandas não entende direto, então usamos o xlrd
        import xlrd
        
        workbook = xlrd.open_workbook(caminho_arquivo, logfile=open(os.devnull, 'w'))
        sheet = workbook.sheet_by_index(0)
        
        data = []
        for row_idx in range(sheet.nrows):
            data.append(sheet.row_values(row_idx))
        
        df_bruto = pd.DataFrame(data)

        # Localiza a linha do cabeçalho procurando pela palavra "Atendimento"
        indice_cabecalho = -1
        for i, row in df_bruto.iterrows():
            if "Atendimento" in str(row.values):
                indice_cabecalho = i
                break
        
        if indice_cabecalho == -1: return False

        # Define o cabeçalho e limpa o DataFrame
        df = df_bruto.iloc[indice_cabecalho+1:].copy()
        df.columns = df_bruto.iloc[indice_cabecalho]
        
        # Limpeza de colunas e linhas vazias
        df = df.loc[:, df.columns.notnull()]
        df = df.dropna(how='all', axis=1).dropna(how='all', axis=0)
        
        # Adiciona metadados
        df['Filtro_Status'] = status_nome
        df['Filtro_Negociacao'] = neg_nome
        
        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df], ignore_index=True)
        return True
    except Exception as e:
        st.error(f"Erro ao ler arquivo binário: {e}")
        return False

# --- NAVEGADOR ---
def iniciar_driver():
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    prefs = {"download.default_directory": DOWNLOAD_DIR, "download.prompt_for_download": False}
    opts.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(options=opts)

# --- INTERFACE ---
st.title("🏥 Extrator AMHP - Versão XLS Legacy")

if st.button("🚀 Iniciar Captura"):
    driver = iniciar_driver()
    try:
        with st.status("Acessando AMHP...", expanded=True) as s:
            wait = WebDriverWait(driver, 45)
            driver.get("https://portal.amhp.com.br/")
            
            # Login (Secrets)
            wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            
            time.sleep(10)
            btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
            driver.execute_script("arguments[0].click();", btn_tiss)
            
            time.sleep(10)
            driver.switch_to.window(driver.window_handles[-1])

            # Navegação e Filtros (Simplificado para o teste)
            driver.execute_script("document.getElementById('IrPara').click();")
            time.sleep(2)
            wait.until(EC.presence_of_element_located((By.XPATH, "//span[text()='Consultório']"))).click()
            wait.until(EC.presence_of_element_located((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))).click()
            
            # Buscar e Exportar
            st.write("🔍 Buscando dados...")
            driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input"))
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
            
            driver.execute_script("document.getElementById('ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox').click();")
            time.sleep(2)
            driver.execute_script("document.getElementById('ctl00_MainContent_rbtImprimirAtendimentos_input').click();")
            
            # Iframe Export
            time.sleep(15)
            if len(driver.find_elements(By.TAG_NAME, "iframe")) > 0: driver.switch_to.frame(0)
            
            select = Select(wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList"))))
            select.select_by_value("XLS")
            driver.execute_script("document.getElementById('ReportView_ReportToolbar_ExportGr_Export').click();")
            
            st.write("📥 Baixando...")
            time.sleep(15)

            # Processamento
            arquivos = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.xls')]
            if arquivos:
                recente = max(arquivos, key=os.path.getctime)
                if processar_xls_binario(recente, "300", "Direto"):
                    st.success("✅ Dados binários convertidos com sucesso!")
                os.remove(recente)
            
            s.update(label="Concluído!", state="complete")
    except Exception as e:
        st.error(f"Erro crítico: {e}")
    finally:
        driver.quit()

# --- ÁREA DE DOWNLOAD ---
if not st.session_state.db_consolidado.empty:
    st.divider()
    st.dataframe(st.session_state.db_consolidado)
    csv = st.session_state.db_consolidado.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')
    st.download_button("💾 Baixar Tudo (Excel)", csv, "relatorio_final.csv", "text/csv")
