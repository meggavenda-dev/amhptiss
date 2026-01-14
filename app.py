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

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Automação AMHP", layout="wide")

# Configuração de pastas (Simulando Área de Trabalho)
DOWNLOAD_PATH = os.path.join(os.getcwd(), "temp_downloads")
FINAL_PATH = os.path.join(os.getcwd(), "automacao_excel")

if not os.path.exists(DOWNLOAD_PATH): os.makedirs(DOWNLOAD_PATH)
if not os.path.exists(FINAL_PATH): os.makedirs(FINAL_PATH)

def iniciar_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    # Configurações para baixar arquivos automaticamente sem perguntar
    prefs = {
        "download.default_directory": DOWNLOAD_PATH,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    options.add_experimental_option("prefs", prefs)
    
    service = Service("/usr/bin/chromedriver")
    try:
        return webdriver.Chrome(service=service, options=options)
    except:
        return webdriver.Chrome(options=options)

st.title("🏥 Exportador de Relatórios AMHP")

# Interface
col1, col2 = st.columns(2)
with col1: data_ini = st.text_input("📅 Data Inicial", value="01/01/2024")
with col2: data_fim = st.text_input("📅 Data Final", value="31/01/2024")

NEGOCIACAO = "Direto"
STATUS_PESQUISA = "300 - Pronto para Processamento"

if st.button("🚀 Gerar e Baixar Excel"):
    driver = iniciar_driver()
    if driver:
        try:
            with st.status("Iniciando processo...", expanded=True) as status:
                wait = WebDriverWait(driver, 45)
                
                # --- LOGIN E NAVEGAÇÃO ---
                driver.get("https://portal.amhp.com.br/")
                wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
                driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
                time.sleep(10)
                
                driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]"))))
                time.sleep(8)
                if len(driver.window_handles) > 1: driver.switch_to.window(driver.window_handles[1])
                try: driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable((By.ID, "fechar-informativo"))))
                except: pass

                # Navegação Menu
                wait.until(EC.element_to_be_clickable((By.ID, "IrPara"))).click()
                wait.until(EC.element_to_be_clickable((By.XPATH, "//span[@class='rtIn' and contains(text(), 'Consultório')]"))).click()
                wait.until(EC.element_to_be_clickable((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))).click()
                time.sleep(5)

                # --- FILTROS ---
                st.write("📝 Aplicando filtros...")
                def preencher(id, valor):
                    el = wait.until(EC.element_to_be_clickable((By.ID, id)))
                    driver.execute_script("arguments[0].value = '';", el)
                    el.send_keys(valor + Keys.ENTER)
                    time.sleep(2)

                preencher("ctl00_MainContent_rcbTipoNegociacao_Input", NEGOCIACAO)
                preencher("ctl00_MainContent_rcbStatus_Input", STATUS_PESQUISA)
                
                # Datas
                d_ini = driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput")
                driver.execute_script("arguments[0].value = '';", d_ini)
                d_ini.send_keys(data_ini + Keys.TAB)
                
                d_fim = driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput")
                driver.execute_script("arguments[0].value = '';", d_fim)
                d_fim.send_keys(data_fim + Keys.TAB)

                # --- BUSCAR E SELECIONAR ---
                st.write("🔍 Buscando atendimentos...")
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input"))
                
                # Aguarda a tabela aparecer
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
                st.write("✅ Dados carregados. Selecionando todos...")
                
                checkbox_all = wait.until(EC.element_to_be_clickable((By.ID, "ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox")))
                driver.execute_script("arguments[0].click();", checkbox_all)
                time.sleep(4) # Espera os 3 segundos que você mencionou

                # --- IMPRIMIR E EXPORTAR ---
                st.write("🖨️ Gerando visualização do relatório...")
                btn_imprimir = driver.find_element(By.ID, "ctl00_MainContent_rbtImprimirAtendimentos_input")
                driver.execute_script("arguments[0].click();", btn_imprimir)
                
                # Espera a tela suspensa (pode ser um iframe ou nova aba)
                time.sleep(10)
                # Se abrir em nova aba, troca
                if len(driver.window_handles) > 2: driver.switch_to.window(driver.window_handles[-1])
                
                st.write("📊 Configurando exportação para Excel...")
                # Selecionar Formato XLS
                dropdown = wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
                Select(dropdown).select_by_value("XLS")
                time.sleep(2)

                # Clicar Exportar
                btn_exportar = driver.find_element(By.ID, "ReportView_ReportToolbar_ExportGr_Export")
                driver.execute_script("arguments[0].click();", btn_exportar)
                
                st.write("📥 Baixando arquivo...")
                time.sleep(10) # Tempo para o download concluir

                # --- ORGANIZAÇÃO DO ARQUIVO ---
                arquivos = os.listdir(DOWNLOAD_PATH)
                if arquivos:
                    # Pega o arquivo mais recente
                    arquivo_baixado = max([os.path.join(DOWNLOAD_PATH, f) for f in arquivos], key=os.path.getctime)
                    nome_limpo = f"{STATUS_PESQUISA}_{NEGOCIACAO}.xls".replace(" ", "_").replace("-", "")
                    destino_final = os.path.join(FINAL_PATH, nome_limpo)
                    
                    shutil.move(arquivo_baixado, destino_final)
                    st.success(f"📂 Relatório salvo em: automacao_excel/{nome_limpo}")
                    
                    # Disponibiliza para download no Streamlit
                    with open(destino_final, "rb") as f:
                        st.download_button("💾 Baixar Planilha Agora", f, file_name=nome_limpo)
                else:
                    st.error("❌ Arquivo não localizado na pasta de downloads.")

                status.update(label="Concluído!", state="complete", expanded=False)

        except Exception as e:
            st.error(f"🚨 Erro: {e}")
            driver.save_screenshot("erro_exportacao.png")
            st.image("erro_exportacao.png")
        finally:
            driver.quit()
