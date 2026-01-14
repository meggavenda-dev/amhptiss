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

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="AMHP Data Analytics", layout="wide")

# Inicializa o Banco de Dados Temporário na sessão
if 'db_consolidado' not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame()

# Diretório para downloads
DOWNLOAD_TEMPORARIO = os.path.join(os.getcwd(), "temp_downloads")
if not os.path.exists(DOWNLOAD_TEMPORARIO):
    os.makedirs(DOWNLOAD_TEMPORARIO)

# --- FUNÇÃO DE PROCESSAMENTO DE DADOS ---

def processar_e_acumular(caminho_arquivo, status_nome, neg_nome):
    """Lê o arquivo do AMHP e extrai apenas a tabela de dados real"""
    try:
        # Lê o arquivo como texto ignorando caracteres especiais
        with open(caminho_arquivo, 'r', encoding='latin1', errors='ignore') as f:
            linhas = f.readlines()
        
        # Localiza a linha correta do cabeçalho (onde estão os títulos das colunas)
        indice_cabecalho = -1
        for i, linha in enumerate(linhas):
            if "Atendimento" in linha and "Guia" in linha and "Valor Total" in linha:
                indice_cabecalho = i
                break
        
        if indice_cabecalho == -1:
            st.error("⚠️ Cabeçalho de dados não encontrado no arquivo.")
            return False

        # Carrega os dados a partir da linha identificada
        # sep=',' e on_bad_lines='skip' resolvem o erro de formatação
        df = pd.read_csv(
            io.StringIO("".join(linhas[indice_cabecalho:])), 
            sep=',', 
            engine='python', 
            on_bad_lines='skip',
            encoding='latin1'
        )
        
        # --- LIMPEZA DE COLUNAS ---
        # 1. Remove colunas "Unnamed" (geradas por vírgulas sobrando no XLS)
        df = df.loc[:, ~df.columns.str.contains('^Unnamed')]
        # 2. Limpa espaços vazios nos nomes das colunas
        df.columns = [c.strip() for c in df.columns]
        # 3. Remove linhas e colunas totalmente vazias
        df = df.dropna(how='all', axis=1).dropna(how='all', axis=0)
        
        # Adiciona metadados para controle
        df['Filtro_Status'] = status_nome
        df['Filtro_Negociacao'] = neg_nome
        
        # Concatena no banco global da sessão
        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df], ignore_index=True)
        return True
    except Exception as e:
        st.error(f"❌ Erro ao processar dados: {e}")
        return False

# --- FUNÇÃO DO NAVEGADOR ---

def iniciar_driver():
    options = Options()
    options.add_argument("--headless") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    prefs = {
        "download.default_directory": DOWNLOAD_TEMPORARIO,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
    }
    options.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(options=options)

# --- INTERFACE ---

st.title("🏥 Consolidador de Relatórios AMHP")

with st.sidebar:
    st.header("Parâmetros")
    data_ini = st.date_input("Data Inicial", value=pd.to_datetime("2026-01-01"))
    data_fim = st.date_input("Data Final", value=pd.to_datetime("2026-01-13"))
    negociacao = "Direto"
    status_p = "300 - Pronto para Processamento"

if st.button("🚀 Iniciar Captura"):
    driver = iniciar_driver()
    if driver:
        try:
            with st.status("Processando...", expanded=True) as s:
                wait = WebDriverWait(driver, 35)
                driver.get("https://portal.amhp.com.br/")
                
                # Login
                wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
                driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
                time.sleep(10)
                
                # TISS
                btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
                driver.execute_script("arguments[0].click();", btn_tiss)
                time.sleep(8)
                driver.switch_to.window(driver.window_handles[-1])

                # Limpeza e Navegação
                try: driver.execute_script("document.getElementById('fechar-informativo').click();")
                except: pass
                driver.execute_script("document.getElementById('IrPara').click();")
                time.sleep(2)
                wait.until(EC.presence_of_element_located((By.XPATH, "//span[text()='Consultório']"))).click()
                wait.until(EC.presence_of_element_located((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))).click()
                time.sleep(5)

                # Filtros
                def set_f(id, v):
                    el = driver.find_element(By.ID, id)
                    driver.execute_script("arguments[0].value = arguments[1];", el, v)
                    el.send_keys(Keys.ENTER)
                    time.sleep(2)

                set_f("ctl00_MainContent_rcbTipoNegociacao_Input", negociacao)
                set_f("ctl00_MainContent_rcbStatus_Input", status_p)
                
                d1, d2 = data_ini.strftime("%d/%m/%Y"), data_fim.strftime("%d/%m/%Y")
                driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput").send_keys(d1 + Keys.TAB)
                driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(d2 + Keys.TAB)

                # Buscar e Exportar
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
                
                st.write("📥 Aguardando download...")
                time.sleep(15)

                # Processamento
                arquivos = [f for f in os.listdir(DOWNLOAD_TEMPORARIO) if f.endswith(('.xls', '.csv'))]
                if arquivos:
                    recente = max([os.path.join(DOWNLOAD_TEMPORARIO, f) for f in arquivos], key=os.path.getctime)
                    if processar_e_acumular(recente, status_p, negociacao):
                        st.success("✅ Dados consolidados com sucesso!")
                    os.remove(recente)
                else:
                    st.error("Erro: Arquivo não baixado.")
                s.update(label="Concluído!", state="complete")

        except Exception as e:
            st.error(f"Erro: {e}")
        finally:
            driver.quit()

# --- ÁREA FINAL ---
st.divider()
if not st.session_state.db_consolidado.empty:
    st.subheader("📊 Base de Dados Consolidada")
    st.dataframe(st.session_state.db_consolidado)
    
    # Exportação Final Segura (sem depender de xlsxwriter específico)
    buffer = io.BytesIO()
    st.session_state.db_consolidado.to_excel(buffer, index=False)
    
    st.download_button(
        label="💾 Baixar Relatório Final Unificado (.xlsx)",
        data=buffer.getvalue(),
        file_name="relatorio_final_consolidado.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    
    if st.button("🗑️ Limpar Banco"):
        st.session_state.db_consolidado = pd.DataFrame()
        st.rerun()
