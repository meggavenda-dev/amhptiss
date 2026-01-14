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
import io

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="AMHP Data Analytics", layout="wide")

if 'db_consolidado' not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame()

DOWNLOAD_TEMPORARIO = os.path.join(os.getcwd(), "temp_downloads")
if not os.path.exists(DOWNLOAD_TEMPORARIO): os.makedirs(DOWNLOAD_TEMPORARIO)

# --- FUNÇÃO DE LIMPEZA DE "LIXO" BINÁRIO ---
def limpar_caracteres_invalidos(texto):
    """Remove caracteres de controle e lixo binário que travam o Excel"""
    if not isinstance(texto, str):
        return texto
    # Mantém apenas caracteres imprimíveis e acentuação básica
    return re.sub(r'[^\x20-\x7E\xA0-\xFF\n\r\t]', '', texto)

def processar_e_acumular(caminho_arquivo, status_nome, neg_nome):
    try:
        # Lê o arquivo de forma binária e tenta decodificar limpando erros
        with open(caminho_arquivo, 'rb') as f:
            conteudo_binario = f.read()
        
        # Converte para texto ignorando o que for lixo binário
        texto_limpo = conteudo_binario.decode('latin1', errors='ignore')
        linhas = texto_limpo.splitlines()
        
        # Localiza o cabeçalho real
        indice_cabecalho = -1
        for i, linha in enumerate(linhas):
            if "Atendimento" in linha and "Guia" in linha:
                indice_cabecalho = i
                break
        
        if indice_cabecalho == -1:
            indice_cabecalho = 16 # Fallback para o padrão AMHP

        # Carrega no Pandas
        df = pd.read_csv(
            io.StringIO("\n".join(linhas[indice_cabecalho:])), 
            sep=',', 
            engine='python', 
            on_bad_lines='skip'
        )
        
        # LIMPEZA PROFUNDA
        df = df.loc[:, ~df.columns.str.contains('^Unnamed')]
        df.columns = [c.strip() for c in df.columns]
        
        # Aplica a limpeza de caracteres ilegais em todas as células
        for col in df.columns:
            df[col] = df[col].apply(limpar_caracteres_invalidos)
        
        df['Filtro_Status'] = status_nome
        df['Filtro_Negociacao'] = neg_nome
        
        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df], ignore_index=True)
        return True
    except Exception as e:
        st.error(f"Erro no processamento: {e}")
        return False

# --- NAVEGADOR (HEADLESS) ---
def iniciar_driver():
    options = Options()
    options.add_argument("--headless") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    prefs = {"download.default_directory": DOWNLOAD_TEMPORARIO, "download.prompt_for_download": False}
    options.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(options=options)

# --- INTERFACE ---
st.title("🏥 Consolidador AMHP (Versão Estável)")

col1, col2 = st.columns(2)
with col1: data_ini = st.date_input("Início", value=pd.to_datetime("2026-01-01"))
with col2: data_fim = st.date_input("Fim", value=pd.to_datetime("2026-01-13"))

if st.button("🚀 Iniciar Captura"):
    driver = iniciar_driver()
    if driver:
        try:
            with st.status("Extraindo dados...", expanded=True) as s:
                wait = WebDriverWait(driver, 35)
                driver.get("https://portal.amhp.com.br/")
                
                # Login
                wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
                driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
                time.sleep(10)
                
                # Acesso TISS
                driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]"))))
                time.sleep(8)
                driver.switch_to.window(driver.window_handles[-1])

                # Navegação
                try: driver.execute_script("document.getElementById('fechar-informativo').click();")
                except: pass
                driver.execute_script("document.getElementById('IrPara').click();")
                time.sleep(2)
                wait.until(EC.presence_of_element_located((By.XPATH, "//span[text()='Consultório']"))).click()
                wait.until(EC.presence_of_element_located((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))).click()
                time.sleep(5)

                # Preenchimento
                driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput").send_keys(data_ini.strftime("%d/%m/%Y") + Keys.TAB)
                driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(data_fim.strftime("%d/%m/%Y") + Keys.TAB)

                # Buscar e Baixar
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input"))
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox"))
                time.sleep(4)
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rbtImprimirAtendimentos_input"))
                
                time.sleep(15)
                if len(driver.find_elements(By.TAG_NAME, "iframe")) > 0: driver.switch_to.frame(0)
                
                Select(wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))).select_by_value("XLS")
                time.sleep(2)
                driver.execute_script("document.getElementById('ReportView_ReportToolbar_ExportGr_Export').click();")
                
                time.sleep(15) 

                # Processar
                arquivos = [f for f in os.listdir(DOWNLOAD_TEMPORARIO) if f.endswith(('.xls', '.csv'))]
                if arquivos:
                    recente = max([os.path.join(DOWNLOAD_TEMPORARIO, f) for f in arquivos], key=os.path.getctime)
                    processar_e_acumular(recente, "300", "Direto")
                    os.remove(recente)
                    st.success("✅ Relatório carregado!")
                s.update(label="Fim!", state="complete")
        except Exception as e:
            st.error(f"Erro: {e}")
        finally:
            driver.quit()

# --- ÁREA DE DOWNLOAD (BLINDADA) ---
if not st.session_state.db_consolidado.empty:
    st.divider()
    st.subheader("📊 Base Consolidada")
    st.dataframe(st.session_state.db_consolidado)
    
    # Exportamos para CSV com codificação Excel (utf-8-sig) para evitar o erro de caracteres ilegais do XLSX
    csv_data = st.session_state.db_consolidado.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')
    
    st.download_button(
        label="💾 Baixar Relatório (Abrir no Excel)",
        data=csv_data,
        file_name="relatorio_consolidado.csv",
        mime="text/csv"
    )
    
    if st.button("🗑️ Limpar Banco"):
        st.session_state.db_consolidado = pd.DataFrame()
        st.rerun()
