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

# --- FUNÇÕES DE PROCESSAMENTO DE DADOS ---

def processar_e_acumular(caminho_arquivo, status_nome, neg_nome):
    """Lê o arquivo XLS/CSV irregular do AMHP e limpa os dados para o banco"""
    try:
        # Lê o arquivo como texto para encontrar o início real da tabela
        with open(caminho_arquivo, 'r', encoding='latin1') as f:
            linhas = f.readlines()
        
        # Localiza a linha do cabeçalho (onde começam os dados reais)
        indice_cabecalho = 0
        for i, linha in enumerate(linhas):
            if "Atendimento" in linha and "Guia" in linha:
                indice_cabecalho = i
                break
        
        # Lê os dados a partir do índice encontrado
        # on_bad_lines='skip' ignora linhas com erro de formato (ex: erro linha 61)
        df = pd.read_csv(
            io.StringIO("".join(linhas[indice_cabecalho:])), 
            sep=None, 
            engine='python', 
            on_bad_lines='skip',
            encoding='latin1'
        )
        
        # Limpeza: remove colunas e linhas totalmente vazias
        df = df.dropna(how='all', axis=1).dropna(how='all', axis=0)
        
        # Adiciona colunas de identificação para o banco de dados
        df['Filtro_Status'] = status_nome
        df['Filtro_Negociacao'] = neg_nome
        
        # Concatena no banco global da sessão
        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df], ignore_index=True)
        return True
    except Exception as e:
        st.error(f"Erro ao processar planilha: {e}")
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

# --- INTERFACE DO USUÁRIO ---

st.title("🏥 Consolidador de Relatórios AMHP")
st.markdown("Gere múltiplos relatórios e unifique-os em uma única base de dados.")

with st.sidebar:
    st.header("Parâmetros")
    data_ini = st.date_input("Data Inicial", value=pd.to_datetime("2026-01-01"))
    data_fim = st.date_input("Data Final", value=pd.to_datetime("2026-01-13"))
    negociacao = "Direto"
    status_pesquisa = "300 - Pronto para Processamento"

if st.button("🚀 Iniciar Captura de Dados"):
    driver = iniciar_driver()
    if driver:
        try:
            with st.status("Robô em execução...", expanded=True) as status_box:
                wait = WebDriverWait(driver, 35)
                
                # LOGIN
                st.write("🔐 Fazendo login...")
                driver.get("https://portal.amhp.com.br/")
                wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
                driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
                time.sleep(10)
                
                # ACESSAR SISTEMA TISS
                st.write("🌐 Entrando no AMHPTISS...")
                btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
                driver.execute_script("arguments[0].click();", btn_tiss)
                time.sleep(8)
                driver.switch_to.window(driver.window_handles[-1])

                # LIMPEZA DE TELA
                try: driver.execute_script("document.getElementById('fechar-informativo').click();")
                except: pass
                driver.execute_script("var c = document.getElementsByTagName('center'); for(var i=0; i<c.length; i++) c[i].style.display='none';")

                # NAVEGAÇÃO
                st.write("📂 Acessando Atendimentos Realizados...")
                driver.execute_script("document.getElementById('IrPara').click();")
                time.sleep(2)
                wait.until(EC.presence_of_element_located((By.XPATH, "//span[text()='Consultório']"))).click()
                time.sleep(2)
                wait.until(EC.presence_of_element_located((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))).click()
                time.sleep(5)

                # FILTROS
                st.write(f"📝 Filtrando: {negociacao} | {status_pesquisa}")
                def preencher_filtro(id_input, valor):
                    el = driver.find_element(By.ID, id_input)
                    driver.execute_script("arguments[0].value = arguments[1];", el, valor)
                    el.send_keys(Keys.ENTER)
                    time.sleep(2)

                preencher_filtro("ctl00_MainContent_rcbTipoNegociacao_Input", negociacao)
                preencher_filtro("ctl00_MainContent_rcbStatus_Input", status_pesquisa)
                
                # DATAS
                d1, d2 = data_ini.strftime("%d/%m/%Y"), data_fim.strftime("%d/%m/%Y")
                driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput").send_keys(d1 + Keys.TAB)
                driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(d2 + Keys.TAB)

                # BUSCAR E SELECIONAR TUDO
                st.write("🔍 Gerando lista e selecionando registros...")
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input"))
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
                
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox"))
                time.sleep(4)
                
                st.write("🖨️ Abrindo janela de exportação...")
                driver.execute_script("arguments[0].click();", driver.find_element(By.ID, "ctl00_MainContent_rbtImprimirAtendimentos_input"))
                
                # EXPORTAR EXCEL (IFRAME)
                time.sleep(15)
                if len(driver.find_elements(By.TAG_NAME, "iframe")) > 0:
                    driver.switch_to.frame(0)

                st.write("📊 Selecionando formato Excel...")
                dropdown = wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
                Select(dropdown).select_by_value("XLS")
                time.sleep(2)
                driver.execute_script("document.getElementById('ReportView_ReportToolbar_ExportGr_Export').click();")
                
                st.write("📥 Baixando e processando dados...")
                time.sleep(15)

                # --- INTEGRAÇÃO COM BANCO DE DADOS ---
                arquivos = [f for f in os.listdir(DOWNLOAD_TEMPORARIO) if f.endswith(('.xls', '.csv'))]
                if arquivos:
                    recente = max([os.path.join(DOWNLOAD_TEMPORARIO, f) for f in arquivos], key=os.path.getctime)
                    if processar_e_acumular(recente, status_pesquisa, negociacao):
                        st.success("✅ Dados adicionados ao Banco Temporário!")
                    os.remove(recente) # Limpa para o próximo
                else:
                    st.error("Falha no download.")

                status_box.update(label="Processo Concluído!", state="complete")

        except Exception as e:
            st.error(f"Erro durante a execução: {e}")
        finally:
            driver.quit()

# --- ÁREA DO BANCO DE DADOS CONSOLIDADO ---
st.divider()
st.header("📊 Banco de Dados de Atendimentos")

if not st.session_state.db_consolidado.empty:
    st.write(f"Atualmente existem **{len(st.session_state.db_consolidado)}** linhas carregadas.")
    st.dataframe(st.session_state.db_consolidado)
    
    # Exportação Final para Excel real
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        st.session_state.db_consolidado.to_excel(writer, index=False, sheet_name='Relatorio_Consolidado')
    
    st.download_button(
        label="💾 Baixar Base de Dados Completa (.xlsx)",
        data=buffer.getvalue(),
        file_name="relatorio_final_consolidado.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    
    if st.button("🗑️ Limpar Banco de Dados"):
        st.session_state.db_consolidado = pd.DataFrame()
        st.rerun()
else:
    st.info("O banco de dados está vazio. Inicie a coleta para importar dados.")
