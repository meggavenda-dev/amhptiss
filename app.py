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
st.set_page_config(page_title="AMHP PDF Analytics", layout="wide")

if 'db_consolidado' not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame() [cite: 1]

DOWNLOAD_DIR = os.path.join(os.getcwd(), "temp_downloads")

def preparar_pasta():
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR) [cite: 1]

# --- MOTOR DE LEITURA DE PDF ---
def processar_pdf_amhp(caminho_pdf, status_nome, neg_nome):
    try:
        dados_lista = []
        with pdfplumber.open(caminho_pdf) as pdf:
            for pagina in pdf.pages:
                tabela = pagina.extract_table()
                if tabela:
                    # Usa a primeira linha da tabela como cabeçalho
                    df_temp = pd.DataFrame(tabela[1:], columns=tabela[0])
                    dados_lista.append(df_temp)
        
        if not dados_lista: return False
        
        df_final = pd.concat(dados_lista, ignore_index=True)
        
        # Limpeza de nomes e caracteres (Sanitização)
        df_final.columns = [str(c).replace('\n', ' ') for c in df_final.columns]
        df_final = df_final.applymap(lambda x: re.sub(r'[^\x20-\x7E\xA0-\xFF]', '', str(x)) if pd.notnull(x) else x) [cite: 1, 18]
        
        df_final['Filtro_Status'] = status_nome
        df_final['Filtro_Negociacao'] = neg_nome
        
        # Consolida no banco de dados temporário
        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df_final], ignore_index=True)
        return True
    except Exception as e:
        st.error(f"Erro ao processar PDF: {e}")
        return False

# --- CONFIGURAÇÃO DO SELENIUM ---
def iniciar_driver():
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    
    prefs = {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True # Garante que o PDF seja baixado, não aberto
    }
    opts.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(options=opts)

# --- INTERFACE ---
st.title("🏥 Consolidador AMHP - Fluxo PDF")

with st.sidebar:
    st.header("Datas do Relatório")
    d_ini = st.text_input("Data Inicial", value="01/01/2026")
    d_fim = st.text_input("Data Final", value="13/01/2026")

if st.button("🚀 Iniciar Captura (Acrobat PDF)"):
    preparar_pasta()
    driver = iniciar_driver()
    wait = WebDriverWait(driver, 45)
    
    try:
        with st.status("Robô em execução...", expanded=True) as status:
            # 1. Login
            driver.get("https://portal.amhp.com.br/")
            wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            
            # 2. Entrar no AMHPTISS e trocar de aba
            time.sleep(12)
            btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
            driver.execute_script("arguments[0].click();", btn_tiss)
            
            wait.until(lambda d: len(d.window_handles) > 1)
            driver.switch_to.window(driver.window_handles[-1]) [cite: 1, 2]
            
            # 3. Limpeza de Overlays e Navegação
            st.write("🧹 Limpando pop-ups e abrindo menus...")
            driver.execute_script("document.querySelectorAll('center, .loading, .overlay, #fechar-informativo').forEach(el => el.remove());")
            
            wait.until(EC.presence_of_element_located((By.ID, "IrPara"))).click()
            time.sleep(2)
            wait.until(EC.presence_of_element_located((By.XPATH, "//span[contains(text(), 'Consultório')]"))).click()
            wait.until(EC.presence_of_element_located((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))).click()
            
            # 4. Filtros
            wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput"))).send_keys(d_ini + Keys.TAB)
            driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(d_fim + Keys.TAB)
            driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input"))
            
            # 5. Exportação para PDF
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
            driver.execute_script("document.getElementById('ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox').click();")
            time.sleep(3)
            driver.execute_script("document.getElementById('ctl00_MainContent_rbtImprimirAtendimentos_input').click();")
            
            # 6. Seleção do Formato no Iframe
            time.sleep(15)
            if len(driver.find_elements(By.TAG_NAME, "iframe")) > 0:
                driver.switch_to.frame(0)
            
            dropdown = wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
            Select(dropdown).select_by_value("PDF") # Seleciona "Acrobat (PDF) file"
            time.sleep(2)
            driver.execute_script("document.getElementById('ReportView_ReportToolbar_ExportGr_Export').click();")
            
            st.write("📥 Aguardando download do PDF...")
            time.sleep(25)

            # 7. Processamento Final
            arquivos = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.pdf')]
            if arquivos:
                recente = max(arquivos, key=os.path.getctime)
                if processar_pdf_amhp(recente, "300", "Direto"):
                    st.success("✅ Relatório PDF lido e consolidado com sucesso!")
                os.remove(recente)
            else:
                st.error("Erro: O PDF não foi localizado na pasta de download.")

            status.update(label="Processo Finalizado!", state="complete")

    except Exception as e:
        st.error(f"Erro Crítico: {e}")
    finally:
        driver.quit()

# --- ÁREA DE EXIBIÇÃO ---
if not st.session_state.db_consolidado.empty:
    st.divider()
    st.subheader("📊 Base de Dados Consolidada")
    st.dataframe(st.session_state.db_consolidado) [cite: 1]
    
    csv = st.session_state.db_consolidado.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')
    st.download_button("💾 Baixar CSV Consolidado", csv, "base_amhp.csv", "text/csv")
    
    if st.button("🗑️ Resetar Banco Temporário"):
        st.session_state.db_consolidado = pd.DataFrame()
        st.rerun()
