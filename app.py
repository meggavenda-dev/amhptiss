# -*- coding: utf-8 -*-
import os, io, re, time, shutil
import streamlit as st
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException

# ========= Secrets/env =========
try:
    chrome_bin_secret = st.secrets.get("env", {}).get("CHROME_BINARY", None)
    driver_bin_secret = st.secrets.get("env", {}).get("CHROMEDRIVER_BINARY", None)
    if chrome_bin_secret:
        os.environ["CHROME_BINARY"] = chrome_bin_secret
    if driver_bin_secret:
        os.environ["CHROMEDRIVER_BINARY"] = driver_bin_secret
except Exception:
    pass

# ========= Página =========
st.set_page_config(page_title="AMHP - Exportador PDF + Consolidação", layout="wide")
st.title("🏥 Exportador AMHP (PDF) + Consolidador")

if "db_consolidado" not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame()

# ========= Paths =========
def obter_caminho_final():
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    path = os.path.join(desktop if os.path.exists(desktop) else os.getcwd(), "automacao_pdf")
    os.makedirs(path, exist_ok=True)
    return path

PASTA_FINAL = obter_caminho_final()
DOWNLOAD_TEMPORARIO = os.path.join(os.getcwd(), "temp_downloads")
os.makedirs(DOWNLOAD_TEMPORARIO, exist_ok=True)

# ========= Sanitização =========
_ILLEGAL_CTRL_RE = re.compile(r"[\x00-\x08\x0B-\x0C\x0E-\x1F]")

def sanitize_value(v):
    if pd.isna(v):
        return v
    if isinstance(v, str):
        v = v.replace("\x00", "").replace("\u00A0", " ").strip()
        v = _ILLEGAL_CTRL_RE.sub("", v)
    return v

def sanitize_df(df):
    df = df.copy()
    for col in df.select_dtypes(include=["object"]).columns:
        df[col] = df[col].apply(sanitize_value)
    return df

# ========= Schema =========
TARGET_COLS = [
    "Atendimento","NrGuia","Realizacao","Hora","TipoGuia",
    "Operadora","Matricula","Beneficiario","Credenciado",
    "Prestador","ValorTotal"
]

def ensure_atendimentos_schema(df):
    for c in TARGET_COLS:
        if c not in df.columns:
            df[c] = ""
    return df[TARGET_COLS]

# ========= Selenium =========
def configurar_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")

    prefs = {
        "download.default_directory": DOWNLOAD_TEMPORARIO,
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True,
    }
    opts.add_experimental_option("prefs", prefs)

    service = Service()
    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(180)
    return driver

def js_safe_click(driver, by, value, timeout=30):
    try:
        WebDriverWait(driver, 5).until(
            EC.invisibility_of_element_located((By.ID, "imgajuda"))
        )
    except Exception:
        pass

    el = WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((by, value))
    )
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    time.sleep(0.3)
    driver.execute_script("arguments[0].click();", el)

# ========= Parser PDF (INTOCADO) =========
def parse_pdf_to_atendimentos_df(pdf_path: str) -> pd.DataFrame:
    from PyPDF2 import PdfReader

    val_re = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")
    code_start_re = re.compile(r"\d{3,6}-")
    re_total_blk = re.compile(r"total\s*r\$\s*\d{1,3}(?:\.\d{3})*,\d{2}", re.I)
    head_re = re.compile(r"(\d+)\s+(\d+)\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})\s+(.*)")

    def norm(s): return re.sub(r"\s+", " ", s.replace("\u00A0"," ")).strip()

    reader = PdfReader(open(pdf_path, "rb"))
    big = norm(" ".join(page.extract_text() or "" for page in reader.pages))
    if not big:
        return pd.DataFrame(columns=TARGET_COLS)

    big = re_total_blk.sub("", big)
    parts = re.split(rf"({val_re.pattern})", big)

    parsed = []
    for i in range(1, len(parts), 2):
        valor = parts[i]
        body = norm(parts[i-1])
        m = head_re.search(body)
        if not m:
            continue

        atendimento, nr_guia, realizacao, hora, rest = m.groups()
        toks = rest.split()
        tipo_guia = toks[0]
        operadora = " ".join(toks[1:-2])
        matricula = toks[-2]
        beneficiario = toks[-1]

        parsed.append({
            "Atendimento": atendimento,
            "NrGuia": nr_guia,
            "Realizacao": realizacao,
            "Hora": hora,
            "TipoGuia": tipo_guia,
            "Operadora": operadora,
            "Matricula": matricula,
            "Beneficiario": beneficiario,
            "Credenciado": "",
            "Prestador": "",
            "ValorTotal": valor
        })

    return sanitize_df(ensure_atendimentos_schema(pd.DataFrame(parsed)))

# ========= Sidebar =========
with st.sidebar:
    data_ini = st.text_input("📅 Data Inicial", "01/01/2026")
    data_fim = st.text_input("📅 Data Final", "13/01/2026")
    negociacao = st.text_input("🤝 Negociação", "Direto")
    status_list = st.multiselect("📌 Status", ["300 - Pronto para Processamento"], default=["300 - Pronto para Processamento"])
    wait_time_main = st.number_input("⏱️ Espera navegação", 0, 60, 10)
    wait_time_download = st.number_input("⏱️ Espera download", 10, 60, 18)

# ========= Execução =========
if st.button("🚀 Iniciar Processo (PDF)"):
    driver = configurar_driver()
    try:
        with st.status("Executando automação...", expanded=True):
            wait = WebDriverWait(driver, 40)

            st.write("🔑 Login")
            driver.get("https://portal.amhp.com.br/")
            wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            time.sleep(wait_time_main)

            st.write("📂 Navegando até Atendimentos")
            js_safe_click(driver, By.XPATH, "//button[contains(., 'AMHPTISS')]")
            time.sleep(wait_time_main)
            if len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])

            driver.execute_script("document.getElementById('IrPara').click();")
            time.sleep(2)
            js_safe_click(driver, By.XPATH, "//span[normalize-space()='Consultório']")
            js_safe_click(driver, By.XPATH, "//a[@href='AtendimentosRealizados.aspx']")
            time.sleep(3)

            for status_sel in status_list:
                st.write(f"📝 Status {status_sel}")
                js_safe_click(driver, By.ID, "ctl00_MainContent_btnBuscar_input")
                time.sleep(2)

                js_safe_click(driver, By.ID, "ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox")
                time.sleep(1)
                js_safe_click(driver, By.ID, "ctl00_MainContent_rbtImprimirAtendimentos_input")

                time.sleep(wait_time_main)
                driver.switch_to.frame(0)
                Select(wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))).select_by_value("PDF")
                js_safe_click(driver, By.ID, "ReportView_ReportToolbar_ExportGr_Export")

                time.sleep(wait_time_download)
                driver.switch_to.default_content()

                pdfs = [os.path.join(DOWNLOAD_TEMPORARIO,f) for f in os.listdir(DOWNLOAD_TEMPORARIO) if f.lower().endswith(".pdf")]
                if not pdfs:
                    st.error("PDF não encontrado")
                    continue

                recente = max(pdfs, key=os.path.getctime)
                destino = os.path.join(PASTA_FINAL, os.path.basename(recente))
                shutil.move(recente, destino)

                df_pdf = parse_pdf_to_atendimentos_df(destino)
                st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df_pdf], ignore_index=True)

        st.success("✅ Processo finalizado")

    finally:
        driver.quit()

# ========= Resultado =========
if not st.session_state.db_consolidado.empty:
    st.subheader("📊 Banco temporário")
    st.dataframe(st.session_state.db_consolidado, use_container_width=True)
