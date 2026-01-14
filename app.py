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

# --- CONFIGURAÇÃO DE AMBIENTE ---
st.set_page_config(page_title="AMHP Data Intelligence", layout="wide")

# Garantir que o banco de dados temporário exista na sessão
if 'db_consolidado' not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame()

DOWNLOAD_DIR = os.path.join(os.getcwd(), "temp_downloads")
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# --- FUNÇÃO DE LIMPEZA DE DADOS (CRUCIAL) ---
def limpar_dados_amhp(caminho_arquivo):
    """
    Lê o arquivo binário do AMHP, ignora lixo binário e 
    extrai apenas texto aproveitável.
    """
    try:
        # Lê o arquivo como binário puro para não travar com caracteres especiais
        with open(caminho_arquivo, 'rb') as f:
            raw_data = f.read()
        
        # Tenta decodificar ignorando caracteres que o Excel/Python não entendem
        texto = raw_data.decode('latin1', errors='ignore')
        
        # O AMHP coloca a tabela após uma série de metadados. 
        # Vamos buscar a linha que contém os títulos das colunas.
        linhas = texto.splitlines()
        indice_inicio = -1
        for i, linha in enumerate(linhas):
            if "Atendimento" in linha and "Guia" in linha and "Valor" in linha:
                indice_inicio = i
                break
        
        if indice_inicio == -1:
            # Se não achar o cabeçalho, o arquivo pode estar vazio ou muito corrompido
            return None

        # Cria o DataFrame apenas com a parte útil
        df = pd.read_csv(io.StringIO("\n".join(linhas[indice_inicio:])), sep=',', engine='python', on_bad_lines='skip')
        
        # Remove colunas fantasmas e caracteres de controle
        df = df.loc[:, ~df.columns.str.contains('^Unnamed')]
        df = df.applymap(lambda x: re.sub(r'[^\x20-\x7E\xA0-\xFF]', '', str(x)) if pd.notnull(x) else x)
        
        return df
    except Exception as e:
        st.error(f"Erro na limpeza do arquivo: {e}")
        return None

# --- NAVEGADOR ---
def configurar_driver():
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    # Impede que pop-ups de download quebrem o Selenium
    prefs = {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "safebrowsing.enabled": True
    }
    opts.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(options=opts)

# --- INTERFACE ---
st.title("🏥 Extrator AMHP - Estabilidade Máxima")

with st.sidebar:
    st.header("Filtros")
    d_ini = st.date_input("Data Inicial", value=pd.to_datetime("2026-01-01"))
    d_fim = st.date_input("Data Final", value=pd.to_datetime("2026-01-13"))

if st.button("🚀 Iniciar Processo"):
    driver = configurar_driver()
    try:
        with st.status("Executando robô...", expanded=True) as s:
            wait = WebDriverWait(driver, 40)
            
            # Login
            driver.get("https://portal.amhp.com.br/")
            wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            
            # Entrar no TISS
            st.write("⏱️ Aguardando carregamento do Portal...")
            time.sleep(12)
            btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
            driver.execute_script("arguments[0].click();", btn_tiss)
            
            time.sleep(10)
            driver.switch_to.window(driver.window_handles[-1])

            # Limpeza de tela (Pop-ups)
            driver.execute_script("""
                var pop = document.getElementById('fechar-informativo');
                if(pop) pop.click();
                var centers = document.getElementsByTagName('center');
                for(var i=0; i<centers.length; i++) centers[i].style.display='none';
            """)

            # Navegação via JS (Mais estável que o clique do Selenium)
            st.write("📂 Acessando menu de atendimentos...")
            driver.execute_script("document.getElementById('IrPara').click();")
            time.sleep(2)
            wait.until(EC.presence_of_element_located((By.XPATH, "//span[text()='Consultório']"))).click()
            wait.until(EC.presence_of_element_located((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))).click()
            
            # Filtros
            st.write("📝 Aplicando filtros de data...")
            wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput"))).send_keys(d_ini.strftime("%d/%m/%Y") + Keys.TAB)
            driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(d_fim.strftime("%d/%m/%Y") + Keys.TAB)
            
            driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input"))
            
            # Seleção e Impressão
            st.write("⏳ Processando lista...")
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
            driver.execute_script("document.getElementById('ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox').click();")
            time.sleep(3)
            driver.execute_script("document.getElementById('ctl00_MainContent_rbtImprimirAtendimentos_input').click();")
            
            # Exportação no Iframe
            st.write("📊 Gerando arquivo para download...")
            time.sleep(15)
            if len(driver.find_elements(By.TAG_NAME, "iframe")) > 0:
                driver.switch_to.frame(0)
            
            select_format = Select(wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList"))))
            select_format.select_by_value("XLS")
            time.sleep(2)
            driver.execute_script("document.getElementById('ReportView_ReportToolbar_ExportGr_Export').click();")
            
            st.write("📥 Baixando e limpando dados...")
            time.sleep(15)

            # --- PROCESSAMENTO DO ARQUIVO ---
            arquivos = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR)]
            if arquivos:
                recente = max(arquivos, key=os.path.getctime)
                df_novo = limpar_dados_amhp(recente)
                
                if df_novo is not None:
                    st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df_novo], ignore_index=True)
                    st.success("✅ Dados processados e adicionados ao banco!")
                
                os.remove(recente) # Limpa a pasta
            else:
                st.error("❌ O arquivo não foi detectado na pasta de download.")

            s.update(label="Processo Finalizado!", state="complete")

    except Exception as e:
        st.error(f"Ocorreu um erro crítico: {e}")
    finally:
        driver.quit()

# --- ÁREA DE DOWNLOAD DO RELATÓRIO FINAL ---
st.divider()
if not st.session_state.db_consolidado.empty:
    st.subheader("📋 Visualização dos Dados Acumulados")
    st.dataframe(st.session_state.db_consolidado)
    
    # Exporta como CSV UTF-8 com BOM (perfeito para o Excel abrir sem erro)
    csv_final = st.session_state.db_consolidado.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')
    
    st.download_button(
        label="💾 Baixar Relatório Consolidado (Excel)",
        data=csv_final,
        file_name="relatorio_final_amhp.csv",
        mime="text/csv"
    )
    
    if st.button("🗑️ Resetar Banco de Dados"):
        st.session_state.db_consolidado = pd.DataFrame()
        st.rerun()
