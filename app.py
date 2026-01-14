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
import shutil
import pdfplumber

# --- CONFIGURAÇÃO DE CAMINHOS ---
DOWNLOAD_TEMPORARIO = os.path.join(os.getcwd(), "temp_downloads")

def preparar_ambiente():
    if os.path.exists(DOWNLOAD_TEMPORARIO):
        shutil.rmtree(DOWNLOAD_TEMPORARIO)
    os.makedirs(DOWNLOAD_TEMPORARIO)
    if 'db_consolidado' not in st.session_state:
        st.session_state.db_consolidado = pd.DataFrame()

# --- FUNÇÃO DE LEITURA DE PDF ---
def processar_pdf_amhp(caminho_pdf, status_nome, neg_nome):
    try:
        dados_lista = []
        with pdfplumber.open(caminho_pdf) as pdf:
            for pagina in pdf.pages:
                tabela = pagina.extract_table()
                if tabela:
                    df_temp = pd.DataFrame(tabela[1:], columns=tabela[0])
                    dados_lista.append(df_temp)
        
        if not dados_lista: return False
        
        df_final = pd.concat(dados_lista, ignore_index=True)
        # Limpeza de caracteres invisíveis e quebras de linha
        df_final.columns = [str(c).replace('\n', ' ').strip() for c in df_final.columns]
        df_final = df_final.applymap(lambda x: re.sub(r'[^\x20-\x7E\xA0-\xFF]', '', str(x)) if pd.notnull(x) else x)
        
        df_final['Filtro_Status'] = status_nome
        df_final['Filtro_Negociacao'] = neg_nome
        
        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df_final], ignore_index=True)
        return True
    except Exception as e:
        st.error(f"Erro ao processar PDF: {e}")
        return False

# --- CONFIGURAÇÃO DO DRIVER ---
def iniciar_driver():
    options = Options()
    options.add_argument("--headless") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    prefs = {
        "download.default_directory": DOWNLOAD_TEMPORARIO,
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True # Essencial para PDF
    }
    options.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(options=options)

# --- INTERFACE ---
st.title("🏥 Consolidador AMHP - PDF PRO")

col1, col2 = st.columns(2)
with col1: data_ini = st.text_input("📅 Data Inicial", value="01/01/2026")
with col2: data_fim = st.text_input("📅 Data Final", value="13/01/2026")

if st.button("🚀 Iniciar Processo PDF"):
    preparar_ambiente()
    driver = iniciar_driver()
    wait = WebDriverWait(driver, 35)
    
    try:
        with st.status("Realizando login e captura...", expanded=True) as status:
            # 1. LOGIN
            driver.get("https://portal.amhp.com.br/")
            wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            
            time.sleep(12)
            
            # 2. ENTRAR NO AMHPTISS
            st.write("🔄 Acessando sistema TISS...")
            btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
            driver.execute_script("arguments[0].click();", btn_tiss)
            
            time.sleep(10)
            if len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])

            # 3. LIMPEZA DE BLOQUEIOS
            driver.execute_script("""
                document.querySelectorAll('center, .loading, .overlay, #fechar-informativo').forEach(el => el.remove());
            """)

            # 4. NAVEGAÇÃO
            ir_para = wait.until(EC.element_to_be_clickable((By.ID, "IrPara"))) # CORREÇÃO 1: element_to_be_clickable
            driver.execute_script("arguments[0].click();", ir_para)
            time.sleep(2)

            cons = wait.until(EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Consultório')]"))) # CORREÇÃO 1
            driver.execute_script("arguments[0].click();", cons)
            
            atend = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))) # CORREÇÃO 1
            driver.execute_script("arguments[0].click();", atend)
            time.sleep(5)

            # 5. FILTROS
            st.write("📝 Aplicando filtros...")
            driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput").send_keys(data_ini + Keys.TAB)
            driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(data_fim + Keys.TAB)
            
            btn_buscar = driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input")
            driver.execute_script("arguments[0].click();", btn_buscar)
            
            # 6. EXPORTAR PDF
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
            time.sleep(2) # Pausa para renderização estável da tabela
            driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox"))
            time.sleep(4)
            
            driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rbtImprimirAtendimentos_input"))
            time.sleep(15)

            # CORREÇÃO 2: Espera segura pelo Iframe antes de trocar
            wait.until(EC.frame_to_be_available_and_switch_to_it((By.TAG_NAME, "iframe")))

            # Seleciona PDF
            dropdown = wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
            Select(dropdown).select_by_value("PDF")
            time.sleep(2)
            
            # CORREÇÃO 3: Garantir que o botão de exportação é clicável no Iframe
            btn_export = wait.until(EC.element_to_be_clickable((By.ID, "ReportView_ReportToolbar_ExportGr_Export")))
            driver.execute_script("arguments[0].click();", btn_export)
            
            st.write("📥 Baixando e processando...")
            time.sleep(25)

            # 7. PROCESSAMENTO E BANCO TEMPORÁRIO
            arquivos = [os.path.join(DOWNLOAD_TEMPORARIO, f) for f in os.listdir(DOWNLOAD_TEMPORARIO) if f.endswith('.pdf')]
            if arquivos:
                recente = max(arquivos, key=os.path.getctime)
                if processar_pdf_amhp(recente, "300", "Direto"):
                    st.success("✅ Dados extraídos do PDF e salvos no banco temporário!")
                os.remove(recente)
            else:
                st.error("Arquivo PDF não encontrado.")

            status.update(label="Concluído!", state="complete")

    except Exception as e:
        st.error(f"Erro: {e}")
    finally:
        driver.quit()

# --- EXIBIÇÃO ---
if not st.session_state.db_consolidado.empty:
    st.divider()
    st.dataframe(st.session_state.db_consolidado)
    csv = st.session_state.db_consolidado.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')
    st.download_button("💾 Baixar Consolidado (CSV)", csv, "relatorio_amhp.csv", "text/csv")
    if st.button("🗑️ Limpar Banco"):
        st.session_state.db_consolidado = pd.DataFrame()
        st.rerun()
