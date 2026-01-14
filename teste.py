import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
import time
import os
import shutil

# --- CONFIGURAÇÃO DE CAMINHOS ---
def obter_caminho_final():
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.exists(desktop):
        path = os.path.join(desktop, "automacao_excel")
    else:
        path = os.path.join(os.getcwd(), "automacao_excel")
    if not os.path.exists(path): os.makedirs(path)
    return path

PASTA_FINAL = obter_caminho_final()
DOWNLOAD_TEMPORARIO = os.path.join(os.getcwd(), "temp_downloads")
if not os.path.exists(DOWNLOAD_TEMPORARIO): os.makedirs(DOWNLOAD_TEMPORARIO)

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
    return webdriver.Chrome(options=options)

st.title("🏥 Exportador AMHP - Versão Estável")

col1, col2 = st.columns(2)
with col1: data_ini = st.text_input("📅 Data Inicial", value="01/01/2026")
with col2: data_fim = st.text_input("📅 Data Final", value="13/01/2026")

if st.button("🚀 Iniciar Processo"):
    driver = iniciar_driver()
    if driver:
        try:
            with st.status("Trabalhando...", expanded=True) as status:
                wait = WebDriverWait(driver, 35)
                
                # 1. LOGIN
                driver.get("https://portal.amhp.com.br/")
                wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
                driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
                time.sleep(12)
                
                # 2. ENTRAR NO AMHPTISS
                st.write("🔄 Acessando TISS...")
                btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
                driver.execute_script("arguments[0].click();", btn_tiss)
                time.sleep(10)
                if len(driver.window_handles) > 1: driver.switch_to.window(driver.window_handles[1])

                # 3. LIMPEZA DE BLOQUEIOS (Pop-ups e Overlays)
                st.write("🧹 Limpando tela...")
                # Tenta fechar o informativo
                try:
                    btn_fechar = wait.until(EC.element_to_be_clickable((By.ID, "fechar-informativo")))
                    driver.execute_script("arguments[0].click();", btn_fechar)
                    time.sleep(2)
                except: pass

                # Remove qualquer elemento <center> que possa estar bloqueando (causa do erro anterior)
                driver.execute_script("""
                    var overlays = document.querySelectorAll('center, .loading, .overlay');
                    for (var i = 0; i < overlays.length; i++) {
                        overlays[i].style.display = 'none';
                        overlays[i].style.pointerEvents = 'none';
                    }
                """)

                # 4. NAVEGAÇÃO (Usando clique forçado via JS)
                st.write("📂 Abrindo menu...")
                ir_para = wait.until(EC.presence_of_element_located((By.ID, "IrPara")))
                driver.execute_script("arguments[0].click();", ir_para)
                time.sleep(2)

                consultorio = wait.until(EC.presence_of_element_located((By.XPATH, "//span[@class='rtIn' and contains(text(), 'Consultório')]")))
                driver.execute_script("arguments[0].click();", consultorio)
                time.sleep(2)

                atendimentos = wait.until(EC.presence_of_element_located((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']")))
                driver.execute_script("arguments[0].click();", atendimentos)
                time.sleep(5)

                # 5. FILTROS
                st.write("📝 Preenchendo campos...")
                # Negociação
                neg = wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rcbTipoNegociacao_Input")))
                driver.execute_script("arguments[0].value = 'Direto';", neg)
                neg.send_keys(Keys.ENTER)
                time.sleep(2)

                # Status
                stat = wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rcbStatus_Input")))
                driver.execute_script("arguments[0].value = '300 - Pronto para Processamento';", stat)
                stat.send_keys(Keys.ENTER)
                time.sleep(2)
                
                # Datas
                d_ini = driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput")
                d_ini.send_keys(data_ini + Keys.TAB)
                d_fim = driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput")
                d_fim.send_keys(data_fim + Keys.TAB)

                # 6. BUSCAR E EXPORTAR
                st.write("🔍 Gerando relatório...")
                btn_buscar = driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input")
                driver.execute_script("arguments[0].click();", btn_buscar)
                
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox"))
                time.sleep(4)
                
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rbtImprimirAtendimentos_input"))
                time.sleep(15)

                # Troca para Iframe e Exporta
                iframes = driver.find_elements(By.TAG_NAME, "iframe")
                if len(iframes) > 0: driver.switch_to.frame(0)

                dropdown = wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
                Select(dropdown).select_by_value("XLS")
                time.sleep(2)
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ReportView_ReportToolbar_ExportGr_Export"))
                
                st.write("📥 Concluindo download...")
                time.sleep(15)

                # ORGANIZAÇÃO FINAL
                arquivos = os.listdir(DOWNLOAD_TEMPORARIO)
                if arquivos:
                    recente = max([os.path.join(DOWNLOAD_TEMPORARIO, f) for f in arquivos], key=os.path.getctime)
                    nome_f = f"Relatorio_300_Direto_{data_ini.replace('/','-')}.xls"
                    destino = os.path.join(PASTA_FINAL, nome_f)
                    shutil.move(recente, destino)
                    st.success(f"✅ Salvo em: {destino}")
                else:
                    st.error("Arquivo não encontrado.")

                status.update(label="Fim!", state="complete")

        except Exception as e:
            st.error(f"Erro detectado: {e}")
            driver.save_screenshot("erro_interceptado.png")
            st.image("erro_interceptado.png")
        finally:
            driver.quit()
