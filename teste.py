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
import re

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="AMHP Data Analytics", layout="wide")

# Inicialização do Banco de Dados na Sessão
if 'db_consolidado' not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame()

# Diretório Temporário para Downloads
DOWNLOAD_DIR = os.path.join(os.getcwd(), "temp_downloads")
if not os.path.exists(DOWNLOAD_DIR): 
    os.makedirs(DOWNLOAD_DIR)

# --- FUNÇÃO DE PROCESSAMENTO XLS (LEGACY BINARY) ---
def processar_xls_amhp(caminho_arquivo, status_nome, neg_nome):
    """Lê arquivos XLS binários (BIFF8) gerados pelo AMHP usando xlrd"""
    try:
        import xlrd
        
        # Abre o arquivo em modo binário
        workbook = xlrd.open_workbook(caminho_arquivo)
        sheet = workbook.sheet_by_index(0)
        
        dados_brutos = []
        for row_idx in range(sheet.nrows):
            dados_brutos.append(sheet.row_values(row_idx))
        
        df_temp = pd.DataFrame(dados_brutos)

        # Localiza dinamicamente a linha do cabeçalho
        indice_cabecalho = -1
        for i, linha in df_temp.iterrows():
            linha_str = " ".join([str(v) for v in linha.values])
            if "Atendimento" in linha_str and "Guia" in linha_str:
                indice_cabecalho = i
                break
        
        if indice_cabecalho == -1:
            return False

        # Define cabeçalhos e remove lixo
        df = df_temp.iloc[indice_cabecalho+1:].copy()
        df.columns = df_temp.iloc[indice_cabecalho]
        
        # Limpeza de colunas inválidas e caracteres de controle
        df = df.loc[:, df.columns.notnull()]
        df = df.dropna(how='all', axis=1).dropna(how='all', axis=0)
        
        # Sanitização de caracteres para evitar erro de download no Excel
        def limpar(texto):
            return re.sub(r'[^\x20-\x7E\xA0-\xFF]', '', str(texto)) if pd.notnull(texto) else texto

        for col in df.columns:
            df[col] = df[col].apply(limpar)

        # Adiciona Metadados
        df['Filtro_Status'] = status_nome
        df['Filtro_Negociacao'] = neg_nome
        
        # Concatena ao banco global
        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df], ignore_index=True)
        return True
    except Exception as e:
        st.error(f"Erro no processamento do arquivo: {e}")
        return False

# --- CONFIGURAÇÃO DO NAVEGADOR ---
def configurar_driver():
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
st.title("🏥 Consolidador de Relatórios AMHP")

with st.sidebar:
    st.header("Configurações")
    data_inicio = st.date_input("Data Inicial", value=pd.to_datetime("2026-01-01"))
    data_final = st.date_input("Data Final", value=pd.to_datetime("2026-01-13"))
    
    neg_label = "Direto"
    status_label = "300 - Pronto para Processamento"

if st.button("🚀 Iniciar Robô"):
    driver = configurar_driver()
    try:
        with st.status("Executando automação...", expanded=True) as s:
            wait = WebDriverWait(driver, 45)
            
            # 1. Login
            driver.get("https://portal.amhp.com.br/")
            wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            
            time.sleep(8)
            
            # 2. AMHPTISS (Clique forçado para evitar interceptação)
            btn_tiss = wait.until(EC.presence_of_element_located((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
            driver.execute_script("arguments[0].click();", btn_tiss)
            
            time.sleep(8)
            driver.switch_to.window(driver.window_handles[-1])

            # 3. Limpeza de Avisos/Pop-ups
            driver.execute_script("""
                var avisos = document.querySelectorAll('center, #fechar-informativo, .modal');
                avisos.forEach(el => el.remove());
            """)

            # 4. Navegação via Script (Mais seguro contra erros de clique)
            driver.execute_script("document.getElementById('IrPara').click();")
            time.sleep(2)
            btn_cons = wait.until(EC.presence_of_element_located((By.XPATH, "//span[text()='Consultório']")))
            driver.execute_script("arguments[0].click();", btn_cons)
            
            link_atend = wait.until(EC.presence_of_element_located((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']")))
            driver.execute_script("arguments[0].click();", link_atend)
            
            # 5. Aplicação de Filtros
            st.write("📅 Aplicando filtros de data...")
            wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput"))).send_keys(data_inicio.strftime("%d/%m/%Y") + Keys.TAB)
            driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(data_final.strftime("%d/%m/%Y") + Keys.TAB)
            
            # Buscar
            btn_buscar = driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input")
            driver.execute_script("arguments[0].click();", btn_buscar)
            
            # 6. Seleção e Exportação
            st.write("⌛ Gerando lista de atendimentos...")
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
            
            driver.execute_script("document.getElementById('ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox').click();")
            time.sleep(3)
            driver.execute_script("document.getElementById('ctl00_MainContent_rbtImprimirAtendimentos_input').click();")
            
            # 7. Iframe de Download
            time.sleep(15)
            if len(driver.find_elements(By.TAG_NAME, "iframe")) > 0:
                driver.switch_to.frame(0)
            
            ddl = Select(wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList"))))
            ddl.select_by_value("XLS")
            time.sleep(2)
            driver.execute_script("document.getElementById('ReportView_ReportToolbar_ExportGr_Export').click();")
            
            st.write("📥 Baixando arquivo binário...")
            time.sleep(18)

            # 8. Processamento
            arquivos = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.xls')]
            if arquivos:
                recente = max(arquivos, key=os.path.getctime)
                if processar_xls_amhp(recente, status_label, neg_label):
                    st.success(f"✅ {len(st.session_state.db_consolidado)} registros processados!")
                os.remove(recente)
            else:
                st.error("Arquivo não encontrado. O sistema AMHP pode ter demorado demais.")

            s.update(label="Processo concluído!", state="complete")
            
    except Exception as e:
        st.error(f"Erro Crítico: {e}")
    finally:
        driver.quit()

# --- RESULTADOS ---
if not st.session_state.db_consolidado.empty:
    st.divider()
    st.dataframe(st.session_state.db_consolidado)
    
    # Exportação Final Segura
    csv_final = st.session_state.db_consolidado.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')
    st.download_button("💾 Baixar Relatório Consolidado", csv_final, "relatorio_amhp.csv", "text/csv")
    
    if st.button("🗑️ Limpar Banco"):
        st.session_state.db_consolidado = pd.DataFrame()
        st.rerun()
