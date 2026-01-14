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

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="AMHP Data Analytics", layout="wide")

if 'db_consolidado' not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame()

DOWNLOAD_DIR = os.path.join(os.getcwd(), "temp_downloads")
if not os.path.exists(DOWNLOAD_DIR): 
    os.makedirs(DOWNLOAD_DIR)

# --- FUNÇÃO DE LEITURA PARA FORMATO XLS (AMHP) ---
def processar_xls_amhp(caminho_arquivo, status_nome, neg_nome):
    """Lê arquivos XLS binários do AMHP usando xlrd"""
    try:
        # Importante: xlrd 2.0.1+ é necessário
        import xlrd
        
        workbook = xlrd.open_workbook(caminho_arquivo)
        sheet = workbook.sheet_by_index(0)
        
        # Extrai todas as linhas do arquivo binário
        dados_brutos = []
        for row_idx in range(sheet.nrows):
            dados_brutos.append(sheet.row_values(row_idx))
        
        df_temp = pd.DataFrame(dados_brutos)

        # Localiza dinamicamente a linha onde a tabela começa
        indice_cabecalho = -1
        for i, linha in df_temp.iterrows():
            linha_str = " ".join([str(v) for v in linha.values])
            if "Atendimento" in linha_str and "Guia" in linha_str:
                indice_cabecalho = i
                break
        
        if indice_cabecalho == -1:
            st.error("Não foi possível localizar a tabela de dados no arquivo baixado.")
            return False

        # Configura o cabeçalho correto
        df = df_temp.iloc[indice_cabecalho+1:].copy()
        df.columns = df_temp.iloc[indice_cabecalho]
        
        # Limpa colunas sem nome e linhas vazias
        df = df.loc[:, df.columns.notnull()]
        df = df.dropna(how='all', axis=1).dropna(how='all', axis=0)
        
        # Adiciona colunas de controle
        df['Filtro_Status'] = status_nome
        df['Filtro_Negociacao'] = neg_nome
        
        # Acumula no banco de dados da sessão
        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df], ignore_index=True)
        return True
    except Exception as e:
        st.error(f"Erro ao processar o XLS: {e}")
        return False

# --- CONFIGURAÇÃO DO ROBÔ ---
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

# --- INTERFACE DO USUÁRIO ---
st.title("🏥 Consolidador de Relatórios AMHP")

with st.sidebar:
    st.header("Configurações de Busca")
    # REINSERÇÃO DOS CAMPOS DE DATA
    data_inicio = st.date_input("Data Inicial", value=pd.to_datetime("2026-01-01"))
    data_final = st.date_input("Data Final", value=pd.to_datetime("2026-01-13"))
    
    negociacao_selecionada = "Direto"
    status_selecionado = "300 - Pronto para Processamento"

if st.button("🚀 Iniciar Captura de Dados"):
    driver = configurar_driver()
    try:
        with st.status("Robô em ação...", expanded=True) as s:
            wait = WebDriverWait(driver, 45)
            
            # 1. LOGIN
            driver.get("https://portal.amhp.com.br/")
            wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            
            time.sleep(10)
            
            # 2. ACESSAR SISTEMA
            btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
            driver.execute_script("arguments[0].click();", btn_tiss)
            
            time.sleep(10)
            driver.switch_to.window(driver.window_handles[-1])

            # 3. NAVEGAÇÃO
            driver.execute_script("document.getElementById('IrPara').click();")
            time.sleep(2)
            wait.until(EC.presence_of_element_located((By.XPATH, "//span[text()='Consultório']"))).click()
            wait.until(EC.presence_of_element_located((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))).click()
            
            # 4. APLICAÇÃO DOS FILTROS (COM AS DATAS DO SIDEBAR)
            st.write(f"📅 Filtrando de {data_inicio.strftime('%d/%m/%Y')} até {data_final.strftime('%d/%m/%Y')}")
            
            # Preenche Datas
            input_ini = wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput")))
            input_ini.send_keys(data_inicio.strftime("%d/%m/%Y") + Keys.TAB)
            
            input_fim = driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput")
            input_fim.send_keys(data_final.strftime("%d/%m/%Y") + Keys.TAB)
            
            # Busca
            driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input"))
            
            # 5. EXPORTAÇÃO
            st.write("⌛ Processando lista de atendimentos...")
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
            
            # Selecionar todos e imprimir
            driver.execute_script("document.getElementById('ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox').click();")
            time.sleep(3)
            driver.execute_script("document.getElementById('ctl00_MainContent_rbtImprimirAtendimentos_input').click();")
            
            # Seleção de formato no Iframe
            time.sleep(15)
            if len(driver.find_elements(By.TAG_NAME, "iframe")) > 0:
                driver.switch_to.frame(0)
            
            dropdown = Select(wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList"))))
            dropdown.select_by_value("XLS")
            time.sleep(2)
            driver.execute_script("document.getElementById('ReportView_ReportToolbar_ExportGr_Export').click();")
            
            st.write("📥 Baixando arquivo...")
            time.sleep(15)

            # 6. PROCESSAMENTO DO XLS BAIXADO
            arquivos = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.xls')]
            if arquivos:
                recente = max(arquivos, key=os.path.getctime)
                if processar_xls_amhp(recente, status_selecionado, negociacao_selecionada):
                    st.success("✅ Dados consolidados!")
                os.remove(recente)
            else:
                st.error("O arquivo não foi localizado na pasta de downloads.")

            s.update(label="Concluído!", state="complete")
            
    except Exception as e:
        st.error(f"Erro Crítico: {e}")
    finally:
        driver.quit()

# --- ÁREA DE RESULTADOS ---
st.divider()
if not st.session_state.db_consolidado.empty:
    st.subheader("📊 Base de Dados Consolidada")
    st.dataframe(st.session_state.db_consolidado)
    
    # Download do acumulado
    csv = st.session_state.db_consolidado.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')
    st.download_button(
        label="💾 Baixar Base Completa (Excel/CSV)",
        data=csv,
        file_name="consolidado_amhp.csv",
        mime="text/csv"
    )
    
    if st.button("🗑️ Limpar Banco de Dados"):
        st.session_state.db_consolidado = pd.DataFrame()
        st.rerun()
