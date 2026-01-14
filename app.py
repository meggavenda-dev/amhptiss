# app.py
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
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException, WebDriverException

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
st.set_page_config(page_title="AMHP - Exportador (Texto/PDF) + Consolidação", layout="wide")
st.title("🏥 Exportador AMHP — Texto (ReportViewer) / PDF — Consolidador")

if "db_consolidado" not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame()

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
    new_cols, seen = [], {}
    for c in df.columns:
        c2 = sanitize_value(str(c))
        n  = seen.get(c2, 0) + 1
        seen[c2] = n
        new_cols.append(c2 if n == 1 else f"{c2}_{n}")
    df.columns = new_cols
    for col in df.select_dtypes(include=["object"]).columns:
        df[col] = df[col].apply(sanitize_value)
    return df

# ========= Esquema da Tabela — Atendimentos =========
TARGET_COLS = [
    "Atendimento","NrGuia","Realizacao","Hora","TipoGuia",
    "Operadora","Matricula","Beneficiario","Credenciado",
    "Prestador","ValorTotal"
]

def _norm_key(s: str) -> str:
    if not s: return ""
    t = s.lower().strip()
    t = (t.replace("á","a").replace("à","a").replace("â","a").replace("ã","a")
           .replace("é","e").replace("ê","e")
           .replace("í","i")
           .replace("ó","o").replace("ô","o").replace("õ","o")
           .replace("ú","u")
           .replace("ç","c"))
    t = re.sub(r"[^\w]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

SYNONYMS = {
    "atendimento": "Atendimento",
    "nr guia": "NrGuia",
    "nr guia operadora": "NrGuia",
    "nº guia": "NrGuia",
    "realizacao": "Realizacao",
    "hora": "Hora",
    "tipo guia": "TipoGuia",
    "operadora": "Operadora",
    "matricula": "Matricula",
    "beneficiario": "Beneficiario",
    "credenciado": "Credenciado",
    "prestador": "Prestador",
    "valor total": "ValorTotal",
}

def ensure_atendimentos_schema(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=TARGET_COLS)
    rename_map = {}
    for c in df.columns:
        key = _norm_key(str(c))
        if key in SYNONYMS:
            rename_map[c] = SYNONYMS[key]
    df2 = df.rename(columns=rename_map).copy()
    for col in TARGET_COLS:
        if col not in df2.columns:
            df2[col] = ""
    df2 = df2[TARGET_COLS]
    return df2

# ========= Regex e Parser de TEXTO (ReportViewer) =========
val_re        = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")
head_re       = re.compile(r"(\d+)\s+(\d+)\s+(\d{2}/\d{2}/\d{4})\s*(\d{2}:\d{2})(.*)")
code_start_re = re.compile(r"\d{3,6}-")
re_total_blk  = re.compile(r"total\s*r\$\s*\d{1,3}(?:\.\d{3})*,\d{2}", re.I)

def _normalize_ws2(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\u00A0", " ")).strip()

# ---------- LÓGICA DE CORREÇÃO: PRÉ-LIMPEZA DO TEXTO ----------
def _preclean_report_text(raw: str) -> str:
    if not raw:
        return ""
    txt = raw.replace("\u00A0", " ")
    txt = _ILLEGAL_CTRL_RE.sub("", txt)

    # 1. Separa blocos numéricos longos (Atendimento + NrGuia) que podem estar colados
    # Ex: 6347792963477929 -> 63477929 63477929
    txt = re.sub(r"(\d{8})(\d{8})", r"\1 \2", txt)

    # 2. Espaço entre Data e Hora coladas (dd/mm/aaaaHH:mm)
    txt = re.sub(r"(\d{2}/\d{2}/\d{4})(\d{2}:\d{2})", r"\1 \2", txt)

    # 3. Espaço entre Hora e Tipo de Guia (HH:mmConsulta)
    txt = re.sub(r"(\d{2}:\d{2})(?=[A-ZÁ-Ú])", r"\1 ", txt)

    # 4. Separação de campos de texto conhecidos colados (TipoGuiaOperadora)
    padroes_tipo = ["Consulta", "SP/SADT", "Honorário Individual", "Não TISS - Atendimento"]
    for p in padroes_tipo:
        txt = re.sub(rf"({p})(?=[A-ZÁ-Ú])", r"\1 ", txt)

    # 5. Espaço entre Matrícula/Beneficiário e Operadoras com parênteses (ex: ...BACEN(104)3607...)
    txt = re.sub(r"(\))(\d{5,})", r"\1 \2", txt)

    # Corta cabeçalho administrativo
    m = re.search(r"(Atendimento\s*Nr\.?\s*Guia.*?Valor\s*Total)", txt, flags=re.I|re.S)
    if m:
        txt = txt[m.end():]
    
    return _normalize_ws2(txt)

def is_mat_token(t: str) -> bool:
    # Matrículas podem ser apenas números ou alfanuméricas longas
    return bool(re.fullmatch(r"[0-9A-Z-]{5,}", t))

def split_tipo_operadora(tokens):
    if not tokens: return "", [], 0
    t0 = tokens[0]
    # Se o primeiro token for o tipo conhecido, separa
    tipos = ["consulta", "sp/sadt", "honorário", "não"]
    for tipo in tipos:
        if t0.lower().startswith(tipo):
            if t0.lower() == tipo:
                return t0, tokens[1:], 1
            # Caso esteja colado: ConsultaBACEN
            else:
                for real_tipo in ["Consulta", "SP/SADT", "Honorário Individual"]:
                    if t0.startswith(real_tipo):
                        rest0 = t0[len(real_tipo):]
                        return real_tipo, ([rest0] if rest0 else []) + tokens[1:], 1
    return t0, tokens[1:], 1

# ========= NOVO PARSER UNIFICADO (Lógica de Streaming) =========
_HDR_PAT_UNI = re.compile(r"(\d{8,})\s+(\d{8,})\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})")

def parse_streaming_any(texto: str) -> pd.DataFrame:
    # Aplica a pré-limpeza com as correções de colagem
    s = _preclean_report_text(texto)
    if not s:
        return pd.DataFrame(columns=TARGET_COLS)

    # Localiza todos os cabeçalhos de linha
    matches = list(_HDR_PAT_UNI.finditer(s))
    rows = []

    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i+1].start() if (i+1) < len(matches) else len(s)
        seg = s[start:end]

        # Busca o valor monetário ao final do segmento
        vals = list(val_re.finditer(seg))
        if not vals: continue
        
        last_val = vals[-1].group(0)
        core = seg[:vals[-1].start()].strip()

        atend, guia, data, hora = m.groups()
        
        # Remove os dados do cabeçalho do core para sobrar apenas o corpo (Operadora/Mat/Ben/etc)
        h_str = f"{atend} {guia} {data} {hora}"
        body = core.replace(h_str, "").strip()
        
        # Tokenização inteligente do corpo
        toks = body.split()
        tipo, tail, _ = split_tipo_operadora(toks)

        # Localiza matrícula (âncora entre Operadora e Beneficiário)
        idx_mat = None
        for j, t in enumerate(tail):
            if is_mat_token(t):
                idx_mat = j
                break
        
        if idx_mat is not None:
            operadora = " ".join(tail[:idx_mat]).strip()
            matricula = tail[idx_mat]
            beneficiario = " ".join(tail[idx_mat+1:]).strip()
        else:
            operadora = " ".join(tail).strip()
            matricula = ""
            beneficiario = ""

        # Credenciado e Prestador (Padrão 000000-Nome)
        # No texto corrido, eles costumam aparecer por último no beneficiário
        codes = list(code_start_re.finditer(beneficiario))
        cred, prest = "", ""
        if len(codes) >= 2:
            i1, i2 = codes[-2].start(), codes[-1].start()
            prest = beneficiario[i2:].strip()
            cred = beneficiario[i1:i2].strip()
            beneficiario = beneficiario[:i1].strip()
        elif len(codes) == 1:
            i1 = codes[0].start()
            prest = beneficiario[i1:].strip()
            beneficiario = beneficiario[:i1].strip()

        rows.append({
            "Atendimento": atend, "NrGuia": guia, "Realizacao": data, "Hora": hora,
            "TipoGuia": tipo, "Operadora": operadora, "Matricula": matricula,
            "Beneficiario": beneficiario, "Credenciado": cred, "Prestador": prest,
            "ValorTotal": last_val
        })

    df = pd.DataFrame(rows)
    return ensure_atendimentos_schema(sanitize_df(df))

# ========= Selenium Config =========
def configurar_driver():
    opts = Options()
    chrome_binary = os.environ.get("CHROME_BINARY", "/usr/bin/chromium")
    driver_binary = os.environ.get("CHROMEDRIVER_BINARY", "/usr/bin/chromedriver")
    if os.path.exists(chrome_binary): opts.binary_location = chrome_binary
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    
    prefs = {
        "download.default_directory": DOWNLOAD_TEMPORARIO,
        "download.prompt_for_download": False,
        "safebrowsing.enabled": True,
    }
    opts.add_experimental_option("prefs", prefs)
    
    service = Service(executable_path=driver_binary) if os.path.exists(driver_binary) else None
    return webdriver.Chrome(service=service, options=opts) if service else webdriver.Chrome(options=opts)

def safe_click(driver, locator, timeout=30):
    el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    driver.execute_script("arguments[0].click();", el)
    return el

def capture_report_text(driver):
    for selector in ["#VisibleReportContent", ".doc", ".FixedTable", "body"]:
        try:
            el = driver.find_element(By.CSS_SELECTOR, selector)
            txt = el.text.strip()
            if len(txt) > 100: return txt, selector
        except: continue
    return "", "falha"

def to_float_br(s):
    try: return float(str(s).replace('.', '').replace(',', '.'))
    except: return 0.0

# ========= UI Streamlit =========
with st.sidebar:
    st.header("Parâmetros")
    data_ini = st.text_input("Data Inicial", "01/12/2025")
    data_fim = st.text_input("Data Final", "31/12/2025")
    negociacao = st.text_input("Negociação", "Normal")
    status_list = st.multiselect("Status", ["300 - Pronto para Processamento"], default=["300 - Pronto para Processamento"])
    force_streaming = st.toggle("Correção de Texto (Streaming)", value=True)

# Processamento Manual
with st.expander("🧪 Processamento Manual (Colar Texto)"):
    texto_manual = st.text_area("Cole o texto aqui")
    if st.button("Processar Manual"):
        df_manual = parse_streaming_any(texto_manual) if force_streaming else parse_relatorio_text_to_atendimentos_df(texto_manual)
        st.dataframe(df_manual)
        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df_manual])

# Botão Principal
if st.button("🚀 Iniciar Automação"):
    driver = configurar_driver()
    try:
        with st.status("Processando...") as status:
            wait = WebDriverWait(driver, 30)
            driver.get("https://portal.amhp.com.br/")
            
            # Login
            wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            time.sleep(5)

            # Navegação TISS
            safe_click(driver, (By.XPATH, "//button[contains(., 'AMHPTISS')]"))
            time.sleep(5)
            driver.switch_to.window(driver.window_handles[-1])

            # Atendimentos Realizados
            driver.execute_script("document.getElementById('IrPara').click();")
            time.sleep(2)
            safe_click(driver, (By.XPATH, "//span[normalize-space()='Consultório']"))
            safe_click(driver, (By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))

            for s_sel in status_list:
                # Preenche Filtros
                wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rcbTipoNegociacao_Input"))).send_keys(negociacao + Keys.ENTER)
                wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rcbStatus_Input"))).send_keys(s_sel + Keys.ENTER)
                driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput").send_keys(data_ini + Keys.TAB)
                driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(data_fim + Keys.TAB)
                
                driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input").click()
                time.sleep(5)

                # Imprimir
                driver.execute_script("document.getElementById('ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox').click();")
                driver.find_element(By.ID, "ctl00_MainContent_rbtImprimirAtendimentos_input").click()
                time.sleep(10)

                driver.switch_to.frame(0)
                txt_cap, ori = capture_report_text(driver)
                df_lote = parse_streaming_any(txt_cap)
                st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df_lote])
                driver.switch_to.default_content()

            status.update(label="Concluído!", state="complete")
    finally:
        driver.quit()

# Resultados
if not st.session_state.db_consolidado.empty:
    st.subheader("Resultado Consolidado")
    st.dataframe(st.session_state.db_consolidado)
    
    xlsx_io = io.BytesIO()
    with pd.ExcelWriter(xlsx_io, engine="openpyxl") as writer:
        st.session_state.db_consolidado.to_excel(writer, index=False)
    st.download_button("Baixar Excel", xlsx_io.getvalue(), "relatorio.xlsx")
