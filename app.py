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

# --- CONFIGURAÇÃO DE AMBIENTE ---
st.set_page_config(page_title="AMHP Data Intelligence", layout="wide")

# Inicializa o Banco de Dados na sessão se não existir
if 'db_consolidado' not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame()

# Pastas de trabalho
DOWNLOAD_TEMPORARIO = os.path.join(os.getcwd(), "temp_downloads")
if not os.path.exists(DOWNLOAD_TEMPORARIO): os.makedirs(DOWNLOAD_TEMPORARIO)

# --- FUNÇÕES DE APOIO ---

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

def processar_e_acumular(caminho_arquivo, status, neg):
    """Lê o arquivo XLS/CSV do AMHP, limpa o cabeçalho e guarda no banco da sessão"""
    try:
        # O AMHP gera arquivos com várias linhas de cabeçalho. 
        # Baseado no seu arquivo, os dados começam na linha 15 (skiprows=14)
        df = pd.read_csv(caminho_arquivo, encoding='latin1', sep=None, engine='python', skiprows=14)
        
        # Remove colunas totalmente vazias
        df = df.dropna(how='all', axis=1)
        
        # Adiciona metadados
        df['Filtro_Status'] = status
        df['Filtro_Negociacao'] = neg
        
        # Acumula no st.session_state
        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df], ignore_index=True)
        return True
    except Exception as e:
        st.error(f"Erro ao processar planilha: {e}")
        return False

# --- INTERFACE ---
st.title("🏥 Sistema de Consolidação AMHPTISS")

with st.sidebar:
    st.header("Configurações de Filtro")
    data_ini = st.date_input("Data Inicial", value=pd.to_datetime("2026-01-01"))
    data_fim = st.date_input("Data Final", value=pd.to_datetime("2026-01-13"))
    negociacao_alvo = "Direto"
    status_alvo = "300 - Pronto para Processamento"

if st.button("🚀 Iniciar Coleta de Dados"):
    driver = iniciar_driver()
    if driver:
        try:
            with st.status("Processando Automato...", expanded=True) as status_progresso:
                wait = WebDriverWait(driver, 35)
                
                # LOGIN
                driver.get("https://portal.amhp.com.br/")
                wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
                driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
                time.sleep(10)
                
                # ENTRAR NO TISS
                st.write("🔗 Mudando para AMHPTISS...")
                btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
                driver.execute_script("arguments[0].click();", btn_tiss)
                time.sleep(10)
                driver.switch_to.window(driver.window_handles[-1])

                # FECHAR POPUP E LIMPAR CENTER
                try:
                    driver.execute_script("document.getElementById('fechar-informativo').click();")
                except: pass
                driver.execute_script("var c = document.getElementsByTagName('center'); for(var i=0; i<c.length; i++) c[i].style.display='none';")

                # NAVEGAÇÃO
                st.write("📂 Navegando no menu...")
                driver.execute_script("document.getElementById('IrPara').click();")
                time.sleep(2)
                wait.until(EC.presence_of_element_located((By.XPATH, "//span[text()='Consultório']"))).click()
                time.sleep(2)
                wait.until(EC.presence_of_element_located((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))).click()
                time.sleep(5)

                # FILTROS
                st.write("📝 Preenchendo filtros...")
                def set_val(id_el, val):
                    el = driver.find_element(By.ID, id_el)
                    driver.execute_script("arguments[0].value = arguments[1];", el, val)
                    el.send_keys(Keys.ENTER)
                    time.sleep(2)

                set_val("ctl00_MainContent_rcbTipoNegociacao_Input", negociacao_alvo)
                set_val("ctl00_MainContent_rcbStatus_Input", status_alvo)
                
                # Datas formatadas
                d1 = data_ini.strftime("%d/%m/%Y")
                d2 = data_fim.strftime("%d/%m/%Y")
                driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput").send_keys(d1 + Keys.TAB)
                driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(d2 + Keys.TAB)

                # BUSCAR E SELECIONAR
                st.write("🔍 Buscando atendimentos...")
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input"))
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
                
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox"))
                time.sleep(3)
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rbtImprimirAtendimentos_input"))
                
                # EXPORTAÇÃO
                st.write("📊 Gerando Excel...")
                time.sleep(15)
                if len(driver.find_elements(By.TAG_NAME, "iframe")) > 0:
                    driver.switch_to.frame(0)

                dropdown = wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
                Select(dropdown).select_by_value("XLS")
                time.sleep(2)
                driver.execute_script("document.getElementById('ReportView_ReportToolbar_ExportGr_Export').click();")
                
                st.write("📥 Aguardando Download...")
                time.sleep(15)

                # --- PROCESSAMENTO DO BANCO DE DADOS ---
                arquivos = [f for f in os.listdir(DOWNLOAD_TEMPORARIO) if f.endswith('.xls') or f.endswith('.csv')]
                if arquivos:
                    mais_recente = max([os.path.join(DOWNLOAD_TEMPORARIO, f) for f in arquivos], key=os.path.getctime)
                    if processar_e_acumular(mais_recente, status_alvo, negociacao_alvo):
                        st.success(f"✅ Dados de '{negociacao_alvo}' adicionados ao banco temporário!")
                    # Limpa a pasta temporária para o próximo teste
                    os.remove(mais_recente)
                else:
                    st.error("Arquivo não encontrado após download.")

                status_progresso.update(label="Teste Finalizado!", state="complete")

        except Exception as e:
            st.error(f"Erro: {e}")
        finally:
            driver.quit()

# --- ÁREA DO RELATÓRIO FINAL ---
st.divider()
st.header("📊 Banco de Dados Temporário")

if not st.session_state.db_consolidado.empty:
    st.write(f"Total de linhas carregadas: **{len(st.session_state.db_consolidado)}**")
    st.dataframe(st.session_state.db_consolidado)
    
    # Botão para baixar tudo o que está no Banco de Dados Temporário
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        st.session_state.db_consolidado.to_excel(writer, index=False, sheet_name='Consolidado')
    
    st.download_button(
        label="💾 Baixar Relatório Final (Excel Único)",
        data=output.getvalue(),
        file_name="relatorio_final_amhptiss.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
else:
    st.info("O banco de dados está vazio. Execute a coleta para carregar informações.")
