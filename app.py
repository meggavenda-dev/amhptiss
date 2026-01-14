import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
import time
import os
import shutil

# --- CONFIGURAÇÃO DE CAMINHOS INTELIGENTE ---
def obter_caminho_final():
    # Tenta localizar o Desktop (Windows/Mac/Linux Local)
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.exists(desktop):
        path = os.path.join(desktop, "automacao_excel")
    else:
        # Se estiver no Streamlit Cloud (Linux Server), usa pasta do projeto
        path = os.path.join(os.getcwd(), "automacao_excel")
    
    if not os.path.exists(path):
        os.makedirs(path)
    return path

PASTA_FINAL = obter_caminho_final()
DOWNLOAD_TEMPORARIO = os.path.join(os.getcwd(), "temp_downloads")
if not os.path.exists(DOWNLOAD_TEMPORARIO):
    os.makedirs(DOWNLOAD_TEMPORARIO)

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
        "safebrowsing.enabled": True
    }
    options.add_experimental_option("prefs", prefs)
    
    # No Streamlit Cloud, não passamos o caminho do Service manualmente
    # O sistema gerencia o chromedriver instalado via packages.txt
    try:
        return webdriver.Chrome(options=options)
    except Exception as e:
        st.error(f"Erro ao iniciar Chrome: {e}")
        return None

st.title("🏥 Exportador AMHP")
st.info(f"📍 Os arquivos serão salvos em: {PASTA_FINAL}")

col1, col2 = st.columns(2)
with col1: data_ini = st.text_input("📅 Data Inicial", value="01/01/2026")
with col2: data_fim = st.text_input("📅 Data Final", value="13/01/2026")

if st.button("🚀 Gerar e Salvar Relatório"):
    driver = iniciar_driver()
    if driver:
        try:
            with st.status("Processando...", expanded=True) as status:
                wait = WebDriverWait(driver, 35)
                
                # LOGIN
                driver.get("https://portal.amhp.com.br/")
                wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
                driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
                time.sleep(12)
                
                # AMHPTISS
                btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
                driver.execute_script("arguments[0].click();", btn_tiss)
                time.sleep(10)
                if len(driver.window_handles) > 1: driver.switch_to.window(driver.window_handles[1])

                # MENU E FILTROS
                wait.until(EC.element_to_be_clickable((By.ID, "IrPara"))).click()
                wait.until(EC.element_to_be_clickable((By.XPATH, "//span[@class='rtIn' and contains(text(), 'Consultório')]"))).click()
                wait.until(EC.element_to_be_clickable((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))).click()
                time.sleep(5)

                # Preenchimento
                wait.until(EC.element_to_be_clickable((By.ID, "ctl00_MainContent_rcbTipoNegociacao_Input"))).send_keys("Direto" + Keys.ENTER)
                time.sleep(2)
                wait.until(EC.element_to_be_clickable((By.ID, "ctl00_MainContent_rcbStatus_Input"))).send_keys("300 - Pronto para Processamento" + Keys.ENTER)
                time.sleep(2)
                
                # Datas
                driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput").send_keys(data_ini + Keys.TAB)
                driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(data_fim + Keys.TAB)

                # BUSCAR
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input"))
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
                
                # SELECIONAR E IMPRIMIR
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox"))
                time.sleep(4)
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rbtImprimirAtendimentos_input"))
                time.sleep(15)

                # EXPORTAÇÃO (IFRAME)
                iframes = driver.find_elements(By.TAG_NAME, "iframe")
                if len(iframes) > 0: driver.switch_to.frame(0)

                dropdown = wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
                Select(dropdown).select_by_value("XLS")
                time.sleep(2)
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ReportView_ReportToolbar_ExportGr_Export"))
                
                time.sleep(15) # Aguarda download

                # ORGANIZAÇÃO
                arquivos = os.listdir(DOWNLOAD_TEMPORARIO)
                if arquivos:
                    recente = max([os.path.join(DOWNLOAD_TEMPORARIO, f) for f in arquivos], key=os.path.getctime)
                    nome_final = f"Relatorio_300_Direto_{data_ini.replace('/','-')}.xls"
                    caminho_final = os.path.join(PASTA_FINAL, nome_final)
                    shutil.move(recente, caminho_final)
                    
                    st.success(f"✅ Arquivo salvo em: {caminho_final}")
                    with open(caminho_final, "rb") as f:
                        st.download_button("💾 Baixar para seu computador", f, file_name=nome_final)
                else:
                    st.error("Arquivo não encontrado. O download pode ter falhado.")

        except Exception as e:
            st.error(f"Erro: {e}")
        finally:
            driver.quit()
