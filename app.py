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

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="AMHP PDF Analytics", layout="wide")

if 'db_consolidado' not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame()

DOWNLOAD_DIR = os.path.join(os.getcwd(), "temp_downloads")

def preparar_pasta():
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR)

# --- FUNÇÃO DE LEITURA DE PDF ---
def processar_pdf_amhp(caminho_pdf, status_nome, neg_nome):
    """Lê o PDF do AMHPTISS e extrai a tabela de atendimentos"""
    try:
        dados_extraidos = []
        with pdfplumber.open(caminho_pdf) as pdf:
            for pagina in pdf.pages:
                tabela = pagina.extract_table()
                if tabela:
                    # Converte para DataFrame usando a primeira linha como cabeçalho
                    df_temp = pd.DataFrame(tabela[1:], columns=tabela[0])
                    dados_extraidos.append(df_temp)
        
        if not dados_extraidos:
            return False
            
        df_final = pd.concat(dados_extraidos, ignore_index=True)
        
        # Limpeza de nomes de colunas e caracteres estranhos
        df_final.columns = [str(c).replace('\n', ' ') for c in df_final.columns]
        df_final = df_final.applymap(lambda x: re.sub(r'[^\x20-\x7E\xA0-\xFF]', '', str(x)) if pd.notnull(x) else x)
        
        # Adiciona colunas de controle
        df_final['Filtro_Status'] = status_nome
        df_final['Filtro_Negociacao'] = neg_nome
        
        # Salva no banco de dados temporário (session_state)
        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df_final], ignore_index=True)
        return True
    except Exception as e:
        st.error(f"Erro ao ler o PDF: {e}")
        return False

# --- CONFIGURAÇÃO DO NAVEGADOR ---
def iniciar_driver():
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    
    prefs = {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True  # Força o download em vez de abrir no browser
    }
    opts.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(options=opts)

# --- INTERFACE ---
st.title("🏥 Consolidador AMHP - Leitor de PDF")

with st.sidebar:
    st.header("Parâmetros")
    data_ini = st.text_input("📅 Data Inicial", value="01/01/2026")
    data_fim = st.text_input("📅 Data Final", value="13/01/2026")

if st.button("🚀 Iniciar Captura em PDF"):
    preparar_pasta()
    driver = iniciar_driver()
    
    try:
        with st.status("Robô em operação (PDF Mode)...", expanded=True) as status:
            wait = WebDriverWait(driver, 40)
            
            # 1. LOGIN
            driver.get("https://portal.amhp.com.br/")
            wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            
            # 2. ENTRADA NO AMHPTISS
            time.sleep(12)
            btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
            driver.execute_script("arguments[0].click();", btn_tiss)
            
            time.sleep(10)
            if len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])

            # 3. LIMPEZA E NAVEGAÇÃO
            driver.execute_script("document.querySelectorAll('center, .loading, .overlay, #fechar-informativo').forEach(el => el.remove());")
            
            wait.until(EC.presence_of_element_located((By.ID, "IrPara")))
            driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "IrPara"))
            
            cons = wait.until(EC.presence_of_element_located((By.XPATH, "//span[contains(text(), 'Consultório')]")))
            driver.execute_script("arguments[0].click();", cons)
            
            atend = wait.until(EC.presence_of_element_located((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']")))
            driver.execute_script("arguments[0].click();", atend)
            
            # 4. FILTROS
            st.write("📅 Filtrando datas...")
            wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput"))).send_keys(data_ini + Keys.TAB)
            driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(data_fim + Keys.TAB)
            
            driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input"))
            
            # 5. SELEÇÃO E EXPORTAÇÃO
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
            driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox"))
            time.sleep(3)
            driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rbtImprimirAtendimentos_input"))
            
            # 6. EXPORTAR COMO PDF
            time.sleep(15)
            if len(driver.find_elements(By.TAG_NAME, "iframe")) > 0:
                driver.switch_to.frame(0)
            
            # MUDANÇA AQUI: Selecionando Acrobat (PDF) file
            dropdown = wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
            Select(dropdown).select_by_value("PDF") # No portal AMHP o valor costuma ser 'PDF'
            time.sleep(2)
            driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ReportView_ReportToolbar_ExportGr_Export"))
            
            st.write("📥 Baixando PDF...")
            time.sleep(20)

            # 7. PROCESSAMENTO DO PDF
            arquivos = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.pdf')]
            if arquivos:
                recente = max(arquivos, key=os.path.getctime)
                if processar_pdf_amhp(recente, "300", "Direto"):
                    st.success("✅ PDF processado e salvo no banco!")
                os.remove(recente)
            else:
                st.error("Erro: O arquivo PDF não foi gerado.")

            status.update(label="Concluído!", state="complete")
            
    except Exception as e:
        st.error(f"Erro no Robô: {e}")
    finally:
        driver.quit()

# --- EXIBIÇÃO ---
if not st.session_state.db_consolidado.empty:
    st.divider()
    st.dataframe(st.session_state.db_consolidado)
    csv = st.session_state.db_consolidado.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')
    st.download_button("💾 Baixar Consolidado", csv, "base_pdf_amhp.csv", "text/csv")
