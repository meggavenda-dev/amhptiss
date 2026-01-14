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
import shutil
import io

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="AMHP Data Analytics", layout="wide")

if 'db_consolidado' not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame()

DOWNLOAD_TEMPORARIO = os.path.join(os.getcwd(), "temp_downloads")
if not os.path.exists(DOWNLOAD_TEMPORARIO): os.makedirs(DOWNLOAD_TEMPORARIO)

# --- PROCESSAMENTO ROBUSTO ---

def processar_e_acumular(caminho_arquivo, status_nome, neg_nome):
    try:
        # Tenta ler o arquivo com diferentes encodings caso o latin1 falhe
        try:
            with open(caminho_arquivo, 'r', encoding='latin1', errors='ignore') as f:
                linhas = f.readlines()
        except:
            with open(caminho_arquivo, 'r', encoding='utf-8', errors='ignore') as f:
                linhas = f.readlines()
        
        # BUSCA DINÂMICA PELO CABEÇALHO
        # Vamos procurar uma linha que tenha pelo menos 2 termos conhecidos
        indice_cabecalho = -1
        termos_chave = ["Atendimento", "Guia", "Valor", "Beneficiário", "Realização"]
        
        for i, linha in enumerate(linhas):
            encontrados = [termo for termo in termos_chave if termo.lower() in linha.lower()]
            if len(encontrados) >= 2: # Se achar pelo menos 2 termos, é o cabeçalho
                indice_cabecalho = i
                break
        
        if indice_cabecalho == -1:
            # Se não achar, tenta pular as 16 linhas padrão do AMHP como última tentativa
            indice_cabecalho = 16 

        # Carrega os dados
        df = pd.read_csv(
            io.StringIO("".join(linhas[indice_cabecalho:])), 
            sep=',', 
            engine='python', 
            on_bad_lines='skip'
        )
        
        # Limpeza de colunas vazias ou fantasmas
        df = df.loc[:, ~df.columns.str.contains('^Unnamed')]
        df.columns = [c.strip() for c in df.columns]
        df = df.dropna(how='all', axis=1).dropna(how='all', axis=0)
        
        # Adiciona os filtros para saber a origem do dado
        df['Filtro_Status'] = status_nome
        df['Filtro_Negociacao'] = neg_nome
        
        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df], ignore_index=True)
        return True
    except Exception as e:
        st.error(f"Erro no processamento: {e}")
        return False

# --- NAVEGADOR ---

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

st.title("🏥 Consolidador de Relatórios AMHP")

col1, col2 = st.columns(2)
with col1: data_ini = st.date_input("Início", value=pd.to_datetime("2026-01-01"))
with col2: data_fim = st.date_input("Fim", value=pd.to_datetime("2026-01-13"))

if st.button("🚀 Iniciar Captura"):
    driver = iniciar_driver()
    if driver:
        try:
            with st.status("Robô trabalhando...", expanded=True) as s:
                wait = WebDriverWait(driver, 35)
                driver.get("https://portal.amhp.com.br/")
                
                # Login (Usa secrets do Streamlit)
                wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
                driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
                time.sleep(10)
                
                # Navegação TISS
                driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]"))))
                time.sleep(8)
                driver.switch_to.window(driver.window_handles[-1])

                # Limpeza e Filtros
                try: driver.execute_script("document.getElementById('fechar-informativo').click();")
                except: pass
                driver.execute_script("document.getElementById('IrPara').click();")
                time.sleep(2)
                wait.until(EC.presence_of_element_located((By.XPATH, "//span[text()='Consultório']"))).click()
                wait.until(EC.presence_of_element_located((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))).click()
                time.sleep(5)

                # Preenchimento
                def fill(id, v):
                    el = driver.find_element(By.ID, id)
                    driver.execute_script("arguments[0].value = arguments[1];", el, v)
                    el.send_keys(Keys.ENTER)
                    time.sleep(2)

                fill("ctl00_MainContent_rcbTipoNegociacao_Input", "Direto")
                fill("ctl00_MainContent_rcbStatus_Input", "300 - Pronto para Processamento")
                
                d1, d2 = data_ini.strftime("%d/%m/%Y"), data_fim.strftime("%d/%m/%Y")
                driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput").send_keys(d1 + Keys.TAB)
                driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(d2 + Keys.TAB)

                # Exportação
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input"))
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox"))
                time.sleep(4)
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rbtImprimirAtendimentos_input"))
                
                time.sleep(15)
                if len(driver.find_elements(By.TAG_NAME, "iframe")) > 0: driver.switch_to.frame(0)
                
                dropdown = wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
                Select(dropdown).select_by_value("XLS")
                time.sleep(2)
                driver.execute_script("document.getElementById('ReportView_ReportToolbar_ExportGr_Export').click();")
                
                time.sleep(15) # Espera download

                # Banco de Dados
                arquivos = [f for f in os.listdir(DOWNLOAD_TEMPORARIO) if f.endswith(('.xls', '.csv'))]
                if arquivos:
                    recente = max([os.path.join(DOWNLOAD_TEMPORARIO, f) for f in arquivos], key=os.path.getctime)
                    processar_e_acumular(recente, "300", "Direto")
                    os.remove(recente)
                    st.success("✅ Relatório adicionado ao banco!")
                
                s.update(label="Concluído!", state="complete")
        except Exception as e:
            st.error(f"Erro: {e}")
        finally:
            driver.quit()

# --- EXIBIÇÃO ---
if not st.session_state.db_consolidado.empty:
    st.divider()
    st.subheader("📊 Base de Dados Consolidada")
    st.dataframe(st.session_state.db_consolidado)
    
    # Exportação para Excel
    buffer = io.BytesIO()
    st.session_state.db_consolidado.to_excel(buffer, index=False, engine='openpyxl')
    st.download_button("💾 Baixar Relatório Unificado (.xlsx)", buffer.getvalue(), "relatorio_final.xlsx")
    
    if st.button("🗑️ Limpar Tudo"):
        st.session_state.db_consolidado = pd.DataFrame()
        st.rerun()
