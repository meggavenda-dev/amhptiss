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

# --- CONFIGURAÇÃO DE DIRETÓRIOS ---
# No Streamlit Cloud, usamos o /tmp para downloads por ser uma pasta com permissão de escrita
DOWNLOAD_PATH = "/tmp/downloads"
FINAL_PATH = "/tmp/automacao_excel"

for p in [DOWNLOAD_PATH, FINAL_PATH]:
    if not os.path.exists(p):
        os.makedirs(p)

def iniciar_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    prefs = {
        "download.default_directory": DOWNLOAD_PATH,
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

st.title("🏥 Exportador de Relatórios AMHP")

col1, col2 = st.columns(2)
with col1: data_ini = st.text_input("📅 Data Inicial", value="01/01/2026")
with col2: data_fim = st.text_input("📅 Data Final", value="13/01/2026")

if st.button("🚀 Gerar e Baixar Excel"):
    driver = iniciar_driver()
    if driver:
        try:
            with st.status("Executando automação...", expanded=True) as status:
                wait = WebDriverWait(driver, 30)
                
                # LOGIN (Simplificado)
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
                
                # Datas (Usando TAB para validar)
                d_ini = driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput")
                d_ini.send_keys(data_ini + Keys.TAB)
                d_fim = driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput")
                d_fim.send_keys(data_fim + Keys.TAB)

                # BUSCAR E SELECIONAR
                st.write("🔍 Buscando dados...")
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input"))
                
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
                st.write("✅ Selecionando todos os registros...")
                driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable((By.ID, "ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox"))))
                time.sleep(4)

                # IMPRIMIR
                st.write("🖨️ Abrindo visualização de relatório...")
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rbtImprimirAtendimentos_input"))
                time.sleep(12) # Aguarda o carregamento do relatório

                # --- TRATAMENTO DO IFRAME E EXPORTAÇÃO ---
                st.write("📊 Tentando localizar controles de exportação...")
                
                # Verifica se o relatório abriu em um Iframe
                iframes = driver.find_elements(By.TAG_NAME, "iframe")
                if len(iframes) > 0:
                    driver.switch_to.frame(0) # Entra no primeiro iframe (comum em relatórios ASP)

                try:
                    # Tenta selecionar o formato via JavaScript (mais seguro para componentes Telerik)
                    dropdown = wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
                    Select(dropdown).select_by_value("XLS")
                    time.sleep(2)
                    
                    btn_exportar = driver.find_element(By.ID, "ReportView_ReportToolbar_ExportGr_Export")
                    driver.execute_script("arguments[0].click();", btn_exportar)
                    st.write("📥 Download solicitado!")
                except Exception as e:
                    st.warning("Falha ao interagir com o menu de exportação. Tentando modo alternativo...")
                    # Tenta clicar direto no link se o Select falhar
                    driver.execute_script("document.getElementById('ReportView_ReportToolbar_ExportGr_FormatList_DropDownList').value = 'XLS';")
                    driver.execute_script("document.getElementById('ReportView_ReportToolbar_ExportGr_Export').click();")

                time.sleep(10) # Tempo para baixar

                # --- RENOMEAR E MOVER ---
                arquivos = os.listdir(DOWNLOAD_PATH)
                if arquivos:
                    recente = max([os.path.join(DOWNLOAD_PATH, f) for f in arquivos], key=os.path.getctime)
                    nome_final = "300_Pronto_Processamento_Direto.xls"
                    caminho_final = os.path.join(FINAL_PATH, nome_final)
                    
                    shutil.move(recente, caminho_final)
                    st.success(f"✅ Arquivo salvo com sucesso!")
                    
                    with open(caminho_final, "rb") as f:
                        st.download_button("💾 Clique aqui para baixar o Excel", f, file_name=nome_final)
                else:
                    st.error("❌ O arquivo não foi encontrado na pasta de downloads.")
                
                status.update(label="Processo Finalizado!", state="complete")

        except Exception as e:
            st.error(f"🚨 Erro crítico: {e}")
            driver.save_screenshot("erro_captura.png")
            st.image("erro_captura.png")
        finally:
            driver.quit()
