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

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="AMHP Data Analytics", layout="wide")

if 'db_consolidado' not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame()

# Diretório para downloads (Funciona no Streamlit Cloud)
DOWNLOAD_DIR = os.path.join(os.getcwd(), "temp_downloads")

def preparar_pasta():
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR)

# --- FUNÇÃO DE PROCESSAMENTO XLS (BIBLIOTECA XLRD) ---
def processar_xls_amhp(caminho_arquivo, status_nome, neg_nome):
    """Lê arquivos XLS binários e sanitiza os dados para o Pandas"""
    try:
        import xlrd
        workbook = xlrd.open_workbook(caminho_arquivo)
        sheet = workbook.sheet_by_index(0)
        dados_brutos = [sheet.row_values(i) for i in range(sheet.nrows)]
        df_temp = pd.DataFrame(dados_brutos)

        # Localiza o cabeçalho real
        idx_cabecalho = -1
        for i, linha in df_temp.iterrows():
            linha_str = " ".join([str(v) for v in linha.values])
            if "Atendimento" in linha_str and "Guia" in linha_str:
                idx_cabecalho = i
                break
        
        if idx_cabecalho == -1: return False

        # Define cabeçalhos e limpa colunas vazias
        df = df_temp.iloc[idx_cabecalho+1:].copy()
        df.columns = df_temp.iloc[idx_cabecalho]
        df = df.loc[:, df.columns.notnull()].dropna(how='all', axis=0)
        
        # REMOVE CARACTERES ILEGAIS (Regex para evitar erro de download no Streamlit)
        df = df.applymap(lambda x: re.sub(r'[^\x20-\x7E\xA0-\xFF]', '', str(x)) if pd.notnull(x) else x)
        
        df['Filtro_Status'] = status_nome
        df['Filtro_Negociacao'] = neg_nome
        
        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df], ignore_index=True)
        return True
    except Exception as e:
        st.error(f"Erro no processamento dos dados: {e}")
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
        "safebrowsing.enabled": True
    }
    opts.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(options=opts)

# --- INTERFACE ---
st.title("🏥 Consolidador AMHP - Versão Estável")

with st.sidebar:
    st.header("Parâmetros")
    data_ini = st.text_input("📅 Data Inicial", value="01/01/2026")
    data_fim = st.text_input("📅 Data Final", value="13/01/2026")
    status_filtro = "300 - Pronto para Processamento"
    neg_filtro = "Direto"

if st.button("🚀 Iniciar Robô"):
    preparar_pasta()
    driver = iniciar_driver()
    
    try:
        with st.status("Executando automação...", expanded=True) as status:
            wait = WebDriverWait(driver, 40)
            
            # 1. LOGIN
            driver.get("https://portal.amhp.com.br/")
            wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            
            # 2. ENTRAR NO AMHPTISS
            st.write("🔄 Acessando sistema TISS...")
            time.sleep(12)
            btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
            driver.execute_script("arguments[0].click();", btn_tiss)
            
            # Troca de Aba (Essencial para não dar erro)
            time.sleep(10)
            if len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])

            # 3. LIMPEZA DE OVERLAYS (Remove bloqueios centrais)
            st.write("🧹 Limpando pop-ups...")
            driver.execute_script("""
                var blockers = document.querySelectorAll('center, .loading, .overlay, #fechar-informativo');
                blockers.forEach(el => { el.style.display = 'none'; el.style.pointerEvents = 'none'; });
            """)
            try:
                driver.find_element(By.ID, "fechar-informativo").click()
            except: pass

            # 4. NAVEGAÇÃO VIA JAVASCRIPT
            st.write("📂 Abrindo menus...")
            ir_para = wait.until(EC.presence_of_element_located((By.ID, "IrPara")))
            driver.execute_script("arguments[0].click();", ir_para)
            
            cons = wait.until(EC.presence_of_element_located((By.XPATH, "//span[contains(text(), 'Consultório')]")))
            driver.execute_script("arguments[0].click();", cons)
            
            atend = wait.until(EC.presence_of_element_located((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']")))
            driver.execute_script("arguments[0].click();", atend)
            
            # 5. FILTROS
            st.write("📝 Preenchendo critérios...")
            # Negociação e Status
            input_neg = wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rcbTipoNegociacao_Input")))
            input_neg.send_keys(neg_filtro + Keys.ENTER)
            time.sleep(2)
            
            input_stat = wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rcbStatus_Input")))
            input_stat.send_keys(status_filtro + Keys.ENTER)
            time.sleep(2)
            
            # Datas
            driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput").send_keys(data_ini + Keys.TAB)
            driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(data_fim + Keys.TAB)

            # 6. EXPORTAÇÃO
            st.write("🔍 Gerando relatório...")
            btn_buscar = driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input")
            driver.execute_script("arguments[0].click();", btn_buscar)
            
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
            driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox"))
            time.sleep(3)
            driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rbtImprimirAtendimentos_input"))
            
            # Iframe de download
            time.sleep(15)
            if len(driver.find_elements(By.TAG_NAME, "iframe")) > 0:
                driver.switch_to.frame(0)
            
            dropdown = wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
            Select(dropdown).select_by_value("XLS")
            time.sleep(2)
            driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ReportView_ReportToolbar_ExportGr_Export"))
            
            st.write("📥 Baixando e processando arquivo binário...")
            time.sleep(25)

            # 7. PROCESSAMENTO FINAL
            arquivos = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.xls')]
            if arquivos:
                recente = max(arquivos, key=os.path.getctime)
                if processar_xls_amhp(recente, status_filtro, neg_filtro):
                    st.success("✅ Relatório consolidado na base de dados!")
                os.remove(recente)
            else:
                st.error("Arquivo não encontrado. O download pode ter falhado.")

            status.update(label="Processo Concluído!", state="complete")
            
    except Exception as e:
        st.error(f"Erro Crítico: {e}")
    finally:
        driver.quit()

# --- EXIBIÇÃO E DOWNLOAD ---
if not st.session_state.db_consolidado.empty:
    st.divider()
    st.subheader("📊 Base Consolidada")
    st.dataframe(st.session_state.db_consolidado)
    
    csv = st.session_state.db_consolidado.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')
    st.download_button("💾 Baixar Resultados (Excel/CSV)", csv, "relatorio_amhp_unificado.csv", "text/csv")
    
    if st.button("🗑️ Limpar Memória"):
        st.session_state.db_consolidado = pd.DataFrame()
        st.rerun()
