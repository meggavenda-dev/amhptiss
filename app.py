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

# --- DEFINIÇÃO DE CAMINHOS LOCAIS (WINDOWS) ---
# Detecta a pasta Desktop do usuário atual
DESKTOP_PATH = os.path.join(os.path.expanduser("~"), "Desktop")
PASTA_FINAL = os.path.join(DESKTOP_PATH, "automacao_excel")
DOWNLOAD_TEMPORARIO = os.path.join(os.getcwd(), "temp_downloads")

# Cria as pastas se não existirem
for p in [PASTA_FINAL, DOWNLOAD_TEMPORARIO]:
    if not os.path.exists(p):
        os.makedirs(p)

def iniciar_driver():
    options = Options()
    # options.add_argument("--headless") # Desative o headless se quiser ver o processo
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    # Configura o Chrome para baixar na nossa pasta temporária
    prefs = {
        "download.default_directory": DOWNLOAD_TEMPORARIO,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    options.add_experimental_option("prefs", prefs)
    
    try:
        service = Service("/usr/bin/chromedriver")
        return webdriver.Chrome(service=service, options=options)
    except:
        return webdriver.Chrome(options=options)

st.title("🏥 Exportador AMHP para Área de Trabalho")

col1, col2 = st.columns(2)
with col1: data_ini = st.text_input("📅 Data Inicial", value="01/01/2026")
with col2: data_fim = st.text_input("📅 Data Final", value="13/01/2026")

if st.button("🚀 Gerar e Salvar no Desktop"):
    driver = iniciar_driver()
    if driver:
        try:
            with st.status("Processando...", expanded=True) as status:
                wait = WebDriverWait(driver, 30)
                
                # LOGIN
                driver.get("https://portal.amhp.com.br/")
                wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
                driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
                time.sleep(10)
                
                # ACESSO TISS
                driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]"))))
                time.sleep(8)
                if len(driver.window_handles) > 1: driver.switch_to.window(driver.window_handles[1])
                try: driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable((By.ID, "fechar-informativo"))))
                except: pass

                # NAVEGAÇÃO
                wait.until(EC.element_to_be_clickable((By.ID, "IrPara"))).click()
                wait.until(EC.element_to_be_clickable((By.XPATH, "//span[@class='rtIn' and contains(text(), 'Consultório')]"))).click()
                wait.until(EC.element_to_be_clickable((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))).click()
                time.sleep(5)

                # FILTROS
                st.write("📝 Aplicando filtros...")
                def preencher(id_campo, texto):
                    el = wait.until(EC.element_to_be_clickable((By.ID, id_campo)))
                    driver.execute_script("arguments[0].value = '';", el)
                    el.send_keys(texto + Keys.ENTER)
                    time.sleep(2)

                preencher("ctl00_MainContent_rcbTipoNegociacao_Input", "Direto")
                preencher("ctl00_MainContent_rcbStatus_Input", "300 - Pronto para Processamento")
                
                # DATAS
                driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput").send_keys(data_ini + Keys.TAB)
                driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(data_fim + Keys.TAB)

                # BUSCAR E SELECIONAR
                st.write("🔍 Buscando e selecionando todos...")
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input"))
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
                
                driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable((By.ID, "ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox"))))
                time.sleep(4)

                # IMPRIMIR E EXPORTAR
                st.write("🖨️ Abrindo relatório...")
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rbtImprimirAtendimentos_input"))
                time.sleep(12)

                # Lógica de Iframe para Exportação
                iframes = driver.find_elements(By.TAG_NAME, "iframe")
                if len(iframes) > 0: driver.switch_to.frame(0)

                try:
                    dropdown = wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
                    Select(dropdown).select_by_value("XLS")
                    time.sleep(2)
                    driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ReportView_ReportToolbar_ExportGr_Export"))
                    st.write("📥 Exportação iniciada...")
                except:
                    # Fallback via JS
                    driver.execute_script("document.getElementById('ReportView_ReportToolbar_ExportGr_FormatList_DropDownList').value = 'XLS';")
                    driver.execute_script("document.getElementById('ReportView_ReportToolbar_ExportGr_Export').click();")

                # Aguarda o download concluir na pasta temporária
                time.sleep(12)

                # --- MOVIMENTAÇÃO PARA O DESKTOP ---
                arquivos = os.listdir(DOWNLOAD_TEMPORARIO)
                if arquivos:
                    # Pega o arquivo baixado
                    recente = max([os.path.join(DOWNLOAD_TEMPORARIO, f) for f in arquivos], key=os.path.getctime)
                    
                    # Nome solicitado: Status + Negociação
                    nome_arquivo = "300_Pronto_Processamento_Direto.xls"
                    caminho_final = os.path.join(PASTA_FINAL, nome_arquivo)
                    
                    # Move da pasta temporária para a pasta na Área de Trabalho
                    shutil.move(recente, caminho_final)
                    st.success(f"✅ Relatório salvo na Área de Trabalho: automacao_excel/{nome_arquivo}")
                else:
                    st.error("❌ Arquivo não localizado. Verifique se o download iniciou.")

                status.update(label="Concluído!", state="complete")

        except Exception as e:
            st.error(f"🚨 Erro: {e}")
        finally:
            driver.quit()
