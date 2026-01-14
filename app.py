
# -*- coding: utf-8 -*-
import os
import io
import re
import time
import shutil
import streamlit as st
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

from selenium.common.exceptions import (
    TimeoutException,
    ElementClickInterceptedException,
    WebDriverException,
)

# ========= CONFIG DOS SECRETS (paths para Cloud, se precisar) =========
try:
    chrome_bin_secret = st.secrets.get("env", {}).get("CHROME_BINARY", None)
    driver_bin_secret = st.secrets.get("env", {}).get("CHROMEDRIVER_BINARY", None)
    if chrome_bin_secret:
        os.environ["CHROME_BINARY"] = chrome_bin_secret
    if driver_bin_secret:
        os.environ["CHROMEDRIVER_BINARY"] = driver_bin_secret
except Exception:
    pass

# ========= CONFIG DA PÁGINA =========
st.set_page_config(page_title="AMHP - Exportador PDF + Consolidação", layout="wide")
st.title("🏥 Exportador AMHP (PDF) + Consolidador")

# ========= “BANCO” TEMPORÁRIO EM SESSÃO =========
if "db_consolidado" not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame()

# ========= PASTAS =========
def obter_caminho_final():
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.exists(desktop):
        path = os.path.join(desktop, "automacao_pdf")
    else:
        path = os.path.join(os.getcwd(), "automacao_pdf")
    if not os.path.exists(path):
        os.makedirs(path)
    return path

PASTA_FINAL = obter_caminho_final()
DOWNLOAD_TEMPORARIO = os.path.join(os.getcwd(), "temp_downloads")
os.makedirs(DOWNLOAD_TEMPORARIO, exist_ok=True)

# ========= SANITIZAÇÃO BÁSICA =========
_ILLEGAL_CTRL_RE = re.compile(r"[\x00-\x08\x0B-\x0C\x0E-\x1F]")

def _sanitize_text(s: str) -> str:
    if s is None:
        return s
    s = s.replace("\x00", "")
    s = _ILLEGAL_CTRL_RE.sub("", s)
    s = s.replace("\u00A0", " ").strip()
    return s

def sanitize_value(v):
    if pd.isna(v):
        return v
    if isinstance(v, (bytes, bytearray)):
        try:
            v = v.decode("utf-8", "ignore")
        except Exception:
            v = v.decode("latin-1", "ignore")
    if isinstance(v, str):
        return _sanitize_text(v)
    return v

def sanitize_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # Colunas
    new_cols, seen = [], {}
    for c in df.columns:
        c2 = sanitize_value(str(c))
        base = c2
        n = seen.get(base, 0) + 1
        seen[base] = n
        new_cols.append(base if n == 1 else f"{base}_{n}")
    df.columns = new_cols
    # Somente colunas texto
    for col in df.select_dtypes(include=["object"]).columns:
        df[col] = df[col].apply(sanitize_value)
    return df

# ========= SELENIUM HELPERS =========
def configurar_driver():
    opts = Options()
    chrome_binary = os.environ.get("CHROME_BINARY", "/usr/bin/chromium")
    driver_binary = os.environ.get("CHROMEDRIVER_BINARY", "/usr/bin/chromedriver")
    if os.path.exists(chrome_binary):
        opts.binary_location = chrome_binary

    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")

    # downloads
    prefs = {
        "download.default_directory": DOWNLOAD_TEMPORARIO,
        "download.prompt_for_download": False,
        "safebrowsing.enabled": True,
        "profile.default_content_setting_values.automatic_downloads": 1,
    }
    opts.add_experimental_option("prefs", prefs)

    if os.path.exists(driver_binary):
        service = Service(executable_path=driver_binary)
        driver = webdriver.Chrome(service=service, options=opts)
    else:
        driver = webdriver.Chrome(options=opts)

    driver.set_page_load_timeout(60)
    return driver

def wait_visible(driver, locator, timeout=30):
    return WebDriverWait(driver, timeout).until(EC.visibility_of_element_located(locator))

def safe_click(driver, locator, timeout=30):
    try:
        el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator))
        el.click()
        return el
    except (ElementClickInterceptedException, TimeoutException, WebDriverException):
        el = WebDriverWait(driver, timeout).until(EC.presence_of_element_located(locator))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        driver.execute_script("arguments[0].click();", el)
        return el

def switch_to_iframe_safe(driver, timeout=20, iframe_locator=None, index_fallback=0):
    try:
        if iframe_locator:
            iframe_el = WebDriverWait(driver, timeout).until(EC.presence_of_element_located(iframe_locator))
            driver.switch_to.frame(iframe_el)
        else:
            WebDriverWait(driver, timeout).until(lambda d: len(d.find_elements(By.TAG_NAME, "iframe")) > index_fallback)
            driver.switch_to.frame(index_fallback)
    except TimeoutException:
        pass

# ========= PARSE DE PDF → DATAFRAME =========
def parse_pdf_to_df(pdf_path: str) -> pd.DataFrame:
    """
    Tenta extrair tabelas via pdfplumber; se não houver, faz fallback lendo texto com PyPDF2.
    Ajusta cabeçalho dinamicamente procurando termos 'Atendimento' e 'Guia'.
    """
    df_final = pd.DataFrame()

    # 1) Tenta com pdfplumber (tabelas)
    try:
        import pdfplumber  # requer 'pdfplumber' no requirements.txt
        tables_concat = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                # Se a tabela tiver linhas/desenho, pdfplumber detecta bastante bem
                tbls = page.extract_tables()
                for tbl in tbls:
                    # tbl é uma lista de linhas (listas de células)
                    if tbl and len(tbl) > 1:
                        tables_concat.append(pd.DataFrame(tbl))
        if tables_concat:
            df_temp = pd.concat(tables_concat, ignore_index=True)
            # Detecta linha de cabeçalho dinamicamente
            header_idx = -1
            for i, row in df_temp.iterrows():
                row_str = " ".join([str(v).replace("\u00A0", " ") for v in row.values])
                if "Atendimento" in row_str and "Guia" in row_str:
                    header_idx = i
                    break
            if header_idx == -1:
                header_idx = 0
            df = df_temp.iloc[header_idx+1:].copy()
            df.columns = df_temp.iloc[header_idx].astype(str).tolist()
            # Limpeza
            df = df.loc[:, df.columns.notnull()]
            df = df.dropna(how="all", axis=1).dropna(how="all", axis=0)
            df_final = df
    except Exception:
        pass

    # 2) Fallback com PyPDF2: extrai texto e faz parsing por linhas
    if df_final.empty:
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(open(pdf_path, "rb"))
            text_all = []
            for page in reader.pages:
                txt = page.extract_text() or ""
                text_all.append(txt)
            text = "\n".join(text_all)

            # Normaliza espaços, tenta encontrar bloco onde aparece cabeçalho
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            # Descobre índice de cabeçalho
            header_idx = -1
            for i, l in enumerate(lines):
                nl = l.replace("\u00A0", " ")
                if "Atendimento" in nl and "Guia" in nl:
                    header_idx = i
                    break
            if header_idx == -1:
                # Se não encontrou, tenta heurística: primeira linha longa
                header_idx = 0

            header_line = lines[header_idx].replace("\u00A0", " ")
            # Divide por múltiplos espaços
            cols = re.split(r"\s{2,}", header_line)
            rows = []
            for l in lines[header_idx+1:]:
                r = re.split(r"\s{2,}", l.replace("\u00A0", " "))
                # mantemos somente linhas com quantidade próxima de colunas
                if len(r) >= max(2, int(0.6 * len(cols))):
                    rows.append(r[:len(cols)])

            df_text = pd.DataFrame(rows, columns=cols[:len(rows[0])] if rows else cols)
            # Remoção de colunas vazias
            df_text = df_text.loc[:, df_text.columns.notnull()]
            df_text = df_text.dropna(how="all", axis=1).dropna(how="all", axis=0)

            df_final = df_text
        except Exception as e:
            st.warning(f"Fallback de texto no PDF falhou: {e}")

    # Sanitiza
    df_final = sanitize_df(df_final)
    return df_final

# ========= UI =========
with st.sidebar:
    st.header("Configurações")
    data_ini = st.text_input("📅 Data Inicial (dd/mm/aaaa)", value="01/01/2026")
    data_fim = st.text_input("📅 Data Final (dd/mm/aaaa)", value="13/01/2026")
    negociacao = st.text_input("🤝 Tipo de Negociação", value="Direto")

    st.caption("Escolha 1 ou mais Status para consolidar. O robô repetirá para cada Status.")
    status_list = st.multiselect(
        "📌 Status",
        options=[
            "300 - Pronto para Processamento",
            "200 - Em Análise",
            "100 - Recebido",
            "400 - Processado"
        ],
        default=["300 - Pronto para Processamento"]
    )

    wait_time_main = st.number_input("⏱️ Tempo extra pós login/troca de tela (s)", min_value=0, value=10)
    wait_time_download = st.number_input("⏱️ Tempo extra para concluir download (s)", min_value=10, value=18)

# ========= BOTÃO PRINCIPAL =========
if st.button("🚀 Iniciar Processo (PDF)"):
    driver = configurar_driver()
    try:
        with st.status("Trabalhando...", expanded=True) as status:
            wait = WebDriverWait(driver, 40)

            # 1. LOGIN
            st.write("🔑 Fazendo login...")
            driver.get("https://portal.amhp.com.br/")
            wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            time.sleep(wait_time_main)

            # 2. ENTRAR NO AMHPTISS
            st.write("🔄 Acessando TISS...")
            try:
                btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
                driver.execute_script("arguments[0].click();", btn_tiss)
            except Exception:
                # Fallback: tentar qualquer coisa com 'TISS'
                elems = driver.find_elements(By.XPATH, "//*[contains(translate(., 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'TISS')]")
                if elems:
                    driver.execute_script("arguments[0].click();", elems[0])
                else:
                    raise RuntimeError("Não foi possível localizar botão/link AMHPTISS/TISS.")
            time.sleep(wait_time_main)
            if len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])

            # 3. LIMPEZA DE BLOQUEIOS (Pop-ups e Overlays)
            st.write("🧹 Limpando tela...")
            try:
                driver.execute_script("""
                    const avisos = document.querySelectorAll('center, #fechar-informativo, .modal');
                    avisos.forEach(el => el.remove());
                """)
            except Exception:
                pass

            # 4. NAVEGAÇÃO
            st.write("📂 Abrindo menu e página de Atendimentos...")
            driver.execute_script("document.getElementById('IrPara').click();")
            time.sleep(2)
            safe_click(driver, (By.XPATH, "//span[normalize-space()='Consultório']"))
            safe_click(driver, (By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))
            time.sleep(3)

            # 5. LOOP DE STATUS (para consolidar)
            for status_sel in status_list:
                st.write(f"📝 Aplicando filtros → Negociação: **{negociacao}**, Status: **{status_sel}**, Período: **{data_ini} a {data_fim}**")

                # Negociação
                neg_input = wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rcbTipoNegociacao_Input")))
                driver.execute_script("arguments[0].value = arguments[1];", neg_input, negociacao)
                neg_input.send_keys(Keys.ENTER)
                time.sleep(1)

                # Status
                stat_input = wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rcbStatus_Input")))
                driver.execute_script("arguments[0].value = arguments[1];", stat_input, status_sel)
                stat_input.send_keys(Keys.ENTER)
                time.sleep(1)

                # Datas
                d_ini_el = driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput")
                d_ini_el.clear(); d_ini_el.send_keys(data_ini + Keys.TAB)
                d_fim_el = driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput")
                d_fim_el.clear(); d_fim_el.send_keys(data_fim + Keys.TAB)

                # Buscar
                btn_buscar = driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input")
                driver.execute_script("arguments[0].click();", btn_buscar)

                # Tabela
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))
                # Seleciona checkbox geral
                driver.execute_script("document.getElementById('ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox').click();")
                time.sleep(2)

                # Imprimir (abre ReportViewer)
                driver.execute_script("document.getElementById('ctl00_MainContent_rbtImprimirAtendimentos_input').click();")
                time.sleep(wait_time_main)

                # Iframe do ReportViewer
                if len(driver.find_elements(By.TAG_NAME, "iframe")) > 0:
                    driver.switch_to.frame(0)

                # Exportar como PDF
                dropdown = wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
                Select(dropdown).select_by_value("PDF")
                time.sleep(2)
                export_btn = driver.find_element(By.ID, "ReportView_ReportToolbar_ExportGr_Export")
                driver.execute_script("arguments[0].click();", export_btn)

                st.write("📥 Concluindo download do PDF...")
                time.sleep(wait_time_download)

                # PROCESSA ARQUIVO
                arquivos = [os.path.join(DOWNLOAD_TEMPORARIO, f) for f in os.listdir(DOWNLOAD_TEMPORARIO) if f.lower().endswith(".pdf")]
                if arquivos:
                    recente = max(arquivos, key=os.path.getctime)
                    # Move p/ pasta final com nome organizado
                    nome_pdf = f"Relatorio_{status_sel.replace(' ', '_').replace('/','-')}_{data_ini.replace('/','-')}_a_{data_fim.replace('/','-')}.pdf"
                    destino_pdf = os.path.join(PASTA_FINAL, nome_pdf)
                    shutil.move(recente, destino_pdf)
                    st.success(f"✅ PDF salvo: {destino_pdf}")

                    # Lê PDF → DataFrame
                    st.write("📄 Lendo dados do PDF...")
                    df_pdf = parse_pdf_to_df(destino_pdf)

                    if not df_pdf.empty:
                        # Metadados de filtro
                        df_pdf["Filtro_Negociacao"] = sanitize_value(negociacao)
                        df_pdf["Filtro_Status"] = sanitize_value(status_sel)
                        df_pdf["Periodo_Inicio"] = sanitize_value(data_ini)
                        df_pdf["Periodo_Fim"] = sanitize_value(data_fim)

                        # Consolida no “banco” temporário
                        st.session_state.db_consolidado = pd.concat(
                            [st.session_state.db_consolidado, df_pdf],
                            ignore_index=True
                        )
                        st.write(f"📊 Registros acumulados: {len(st.session_state.db_consolidado)}")
                    else:
                        st.warning("⚠️ Não foi possível extrair linhas do PDF (tabelas não detectadas). Verifique o arquivo salvo.")

                    # Volta do iframe para a página de filtros
                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass

                else:
                    st.error("❌ PDF não encontrado após o download. O SSRS pode ter demorado ou bloqueado.")
                    # Volta do iframe para tentar continuar
                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass

            status.update(label="✅ Fim do processo!", state="complete")

    except Exception as e:
        st.error(f"Erro detectado: {e}")
        try:
            driver.save_screenshot(os.path.join(PASTA_FINAL, "erro_interceptado.png"))
            st.image(os.path.join(PASTA_FINAL, "erro_interceptado.png"))
        except Exception:
            pass
    finally:
        try:
            driver.quit()
        except Exception:
            pass

# ========= RESULTADOS & EXPORTAÇÃO =========
if not st.session_state.db_consolidado.empty:
    st.divider()
    df_preview = sanitize_df(st.session_state.db_consolidado)
    st.subheader("📊 Base consolidada (temporária)")
    st.dataframe(df_preview, use_container_width=True)

    # Exporta Consolidado em CSV (sem Excel)
    csv_bytes = df_preview.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        "💾 Baixar Consolidação (CSV)",
        csv_bytes,
        file_name="consolidado_amhp.csv",
        mime="text/csv"
    )

    if st.button("🗑️ Limpar Banco Temporário"):
        st.session_state.db_consolidado = pd.DataFrame()
        st.rerun()
