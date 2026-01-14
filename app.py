import streamlit as st
import pandas as pd
import numpy as np
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

# --- CONFIGURAÇÃO GLOBAL ---
st.set_page_config(page_title="AMHP PDF Analytics", layout="wide")

# Inicializa o banco de dados temporário se não existir
if 'db_consolidado' not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame()

# Definição do caminho de download (Caminho absoluto para evitar NameError)
DOWNLOAD_DIR = os.path.join(os.getcwd(), "temp_downloads")

def preparar_pasta():
    """Limpa e recria a pasta de downloads temporários"""
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- FUNÇÃO DE LEITURA DE PDF ---
def processar_pdf_amhp(caminho_pdf, status_nome, neg_nome):
    try:
        dados_lista = []
        with pdfplumber.open(caminho_pdf) as pdf:
            for pagina in pdf.pages:
                tabela = pagina.extract_table()
                if tabela:
                    # Cria DataFrame. A primeira linha da tabela do PDF costuma ser o cabeçalho
                    df_temp = pd.DataFrame(tabela[1:], columns=tabela[0])
                    dados_lista.append(df_temp)
        
        if not dados_lista:
            return False
            
        df_final = pd.concat(dados_lista, ignore_index=True)
        
        # Sanitização de colunas e caracteres (Evita erro de exportação do Streamlit)
        df_final.columns = [str(c).replace('\n', ' ').strip() for c in df_final.columns]
        df_final = df_final.applymap(lambda x: re.sub(r'[^\x20-\x7E\xA0-\xFF]', '', str(x)) if pd.notnull(x) else x)
        
        # Adiciona metadados
        df_final['Filtro_Status'] = status_nome
        df_final['Filtro_Negociacao'] = neg_nome
        
        # Salva no estado da sessão (Banco Temporário)
        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df_final], ignore_index=True)
        return True
    except Exception as e:
        st.error(f"Erro ao extrair dados do PDF: {e}")
        return False

# --- CONFIGURAÇÃO DO CHROME ---
def iniciar_driver():
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    
    prefs = {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True  # Força o download do PDF
    }
    opts.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(options=opts)

# --- INTERFACE ---
st.title("🏥 Consolidador AMHP - Fluxo PDF")

col1, col2 = st.columns(2)
with col1:
    data_ini = st.text_input("📅 Data Inicial", value="01/01/2026")
with col2:
    data_fim = st.text_input("📅 Data Final", value="13/01/2026")

if st.button("🚀 Iniciar Captura (Acrobat PDF)"):
    preparar_pasta() # Chamada da função que estava dando erro
    driver = iniciar_driver()
    wait = WebDriverWait(driver, 45)
    
    try:
        with st.status("Robô em execução...", expanded=True) as status:
            # 1. LOGIN
            driver.get("https://portal.amhp.com.br/")
            wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            
            # 2. TRANSIÇÃO AMHPTISS
            time.sleep(12)
            btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
            driver.execute_script("arguments[0].click();", btn_tiss)
            
            # Espera a nova aba e troca o foco
            wait.until(lambda d: len(d.window_handles) > 1)
            driver.switch_to.window(driver.window_handles[-1])
            
            # 3. LIMPEZA DE TELA
            st.write("🧹 Preparando ambiente...")
            driver.execute_script("""
                document.querySelectorAll('center, .loading, .overlay, #fechar-informativo').forEach(el => el.remove());
            """)
            
            # 4. NAVEGAÇÃO
            wait.until(EC.presence_of_element_located((By.ID, "IrPara"))).click()
            time.sleep(2)
            wait.until(EC.presence_of_element_located((By.XPATH, "//span[contains(text(), 'Consultório')]"))).click()
            wait.until(EC.presence_of_element_located((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))).click()
            
            # 5. FILTROS
            st.write("📅 Aplicando filtros...")
            wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput"))).send_keys(data_ini + Keys.TAB)
            driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(data_fim + Keys.TAB)
            driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input"))
            
            # 6. SELEÇÃO E IMPRESSÃO
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
            driver.execute_script("document.getElementById('ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox').click();")
            time.sleep(3)
            driver.execute_script("document.getElementById('ctl00_MainContent_rbtImprimirAtendimentos_input').click();")
            
            # 7. EXPORTAÇÃO PDF NO IFRAME
            time.sleep(15)
            if len(driver.find_elements(By.TAG_NAME, "iframe")) > 0:
                driver.switch_to.frame(0)
            
            dropdown = wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
            Select(dropdown).select_by_value("PDF")
            time.sleep(2)
            driver.execute_script("document.getElementById('ReportView_ReportToolbar_ExportGr_Export').click();")
            
            st.write("📥 Baixando arquivo...")
            time.sleep(25)

            # 8. PROCESSAMENTO
            arquivos = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.pdf')]
            if arquivos:
                recente = max(arquivos, key=os.path.getctime)
                if processar_pdf_amhp(recente, "300", "Direto"):
                    st.success("✅ Atendimentos extraídos do PDF e salvos!")
                os.remove(recente)
            else:
                st.error("O arquivo PDF não foi detectado.")

            status.update(label="Concluído!", state="complete")

    except Exception as e:
        st.error(f"Erro Crítico: {e}")
    finally:
        driver.quit()

# --- EXIBIÇÃO ---
if not st.session_state.db_consolidado.empty:
    st.divider()
    st.subheader("📊 Atendimentos Acumulados")
    st.dataframe(st.session_state.db_consolidado)
    
    csv = st.session_state.db_consolidado.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')
    st.download_button("💾 Baixar Base Completa (CSV)", csv, "consolidado_amhp.csv", "text/csv")
    
    if st.button("🗑️ Limpar Banco Temporário"):
        st.session_state.db_consolidado = pd.DataFrame()
        st.rerun()
