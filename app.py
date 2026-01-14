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
st.set_page_config(page_title="AMHP - Exportador PDF + Consolidação", layout="wide")
st.title("🏥 Exportador AMHP (PDF) + Consolidador")

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
    "n  guia": "NrGuia",
    "realizacao": "Realizacao",
    "realizacao data": "Realizacao",
    "realizacao atendimento": "Realizacao",
    "hora": "Hora",
    "tipo guia": "TipoGuia",
    "operadora": "Operadora",
    "matricula": "Matricula",
    "beneficiario": "Beneficiario",
    "nome do beneficiario": "Beneficiario",
    "credenciado": "Credenciado",
    "prestador": "Prestador",
    "valor total": "ValorTotal",
    "valor": "ValorTotal",
    "total": "ValorTotal",
}

def ensure_atendimentos_schema(df: pd.DataFrame) -> pd.DataFrame:
    """
    Garante as 11 colunas da Tabela — Atendimentos.
    Renomeia sinônimos, cria colunas faltantes e reordena.
    """
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

# ========= Selenium =========
def configurar_driver():
    opts = Options()
    chrome_binary  = os.environ.get("CHROME_BINARY", "/usr/bin/chromium")
    driver_binary  = os.environ.get("CHROMEDRIVER_BINARY", "/usr/bin/chromedriver")
    if os.path.exists(chrome_binary):
        opts.binary_location = chrome_binary

    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")

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

# ========= PDF → Tabela (coordenadas + textual reforçado) =========
def parse_pdf_to_atendimentos_df(pdf_path: str, mode: str = "coord", debug: bool = False) -> pd.DataFrame:
    """
    mode: "coord" (coordenadas) | "text" (fallback textual reforçado)
    Sempre aplica ensure_atendimentos_schema() antes de retornar.
    """
    import pdfplumber
    from PyPDF2 import PdfReader

    TOP_TOL      = 4.5
    MERGE_GAP_X  = 10.0
    COL_MARGIN   = 4.0

    val_re        = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")
    val_line_re   = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}$")
    code_start_re = re.compile(r"\d{3,6}-")
    re_total_blk  = re.compile(r"total\s*r\$\s*\d{1,3}(?:\.\d{3})*,\d{2}", re.I)
    head_re       = re.compile(r"(\d+)\s+(\d+)\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})\s+(.*)")

    def _normalize_ws(s: str) -> str:
        return re.sub(r"\s+", " ", s.replace("\u00A0", " ")).strip()

    # ---------- Coordenadas ----------
    def parse_by_coords() -> pd.DataFrame:
        all_records = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    words = page.extract_words(use_text_flow=True, extra_attrs=["x0","x1","top","bottom"])
                    if not words: continue

                    header_y = None
                    header_words = []
                    for w in words:
                        if "Atendimento" in w["text"]:
                            y_top = w["top"]
                            band = [ww for ww in words if abs(ww["top"] - y_top) < TOP_TOL]
                            band_text = " ".join([b["text"] for b in band])
                            if ("Valor" in band_text) and ("Total" in band_text):
                                header_y = y_top
                                header_words = sorted(band, key=lambda z: z["x0"])
                                break

                    if header_y is None or not header_words:
                        tbls = page.extract_tables()
                        if tbls:
                            df = pd.DataFrame(tbls[0])
                            if not df.empty:
                                df.columns = df.iloc[0]
                                df = df.iloc[1:].dropna(how="all", axis=1)
                                df = ensure_atendimentos_schema(df)
                                for _, r in df.iterrows():
                                    all_records.append({k: str(r.get(k, "")).strip() for k in TARGET_COLS})
                        continue

                    blocks, cur = [], [header_words[0]]
                    for w in header_words[1:]:
                        if (w["x0"] - cur[-1]["x1"]) <= MERGE_GAP_X:
                            cur.append(w)
                        else:
                            blocks.append(cur); cur = [w]
                    blocks.append(cur)

                    header_blocks = [{"text": " ".join([b["text"] for b in bl]),
                                      "x0": min([b["x0"] for b in bl]),
                                      "x1": max([b["x1"] for b in bl])} for bl in blocks]

                    def map_block(txt: str):
                        t = txt.lower()
                        if "atendimento" in t:                   return "Atendimento"
                        if "nr" in t and "guia" in t:            return "NrGuia"
                        if "realiza" in t:                       return "Realizacao"
                        if "hora" in t:                          return "Hora"
                        if "tipo" in t and "guia" in t:          return "TipoGuia"
                        if "operadora" in t:                     return "Operadora"
                        if "matr" in t:                          return "Matricula"
                        if "benef" in t:                         return "Beneficiario"
                        if "credenciado" in t:                   return "Credenciado"
                        if "prestador" in t:                     return "Prestador"
                        if "valor" in t and "total" in t:        return "ValorTotal"
                        return None

                    columns = []
                    for hb in header_blocks:
                        name = map_block(hb["text"])
                        if name:
                            columns.append({"name": name, "x0": hb["x0"], "x1": hb["x1"]})
                    columns = sorted(columns, key=lambda c: c["x0"])
                    if not columns: continue

                    data_words = [w for w in words if w["top"] > header_y + TOP_TOL]
                    total_candidates = [w for w in data_words if w["text"].lower() == "total"]
                    if total_candidates:
                        total_y = total_candidates[0]["top"]
                        data_words = [w for w in data_words if w["top"] < total_y - TOP_TOL]

                    rows, band, last_top = [], [], None
                    for w in sorted(data_words, key=lambda z: (round(z["top"], 1), z["x0"])):
                        if (last_top is None) or (abs(w["top"] - last_top) <= TOP_TOL):
                            band.append(w); last_top = w["top"]
                        else:
                            rows.append(band); band = [w]; last_top = w["top"]
                    if band: rows.append(band)

                    col_centers = [(c["name"], (c["x0"] + c["x1"]) / 2.0) for c in columns]
                    def assign_to_nearest_col(w):
                        wc = (w["x0"] + w["x1"]) / 2.0
                        name, dist = None, 1e9
                        for cname, cc in col_centers:
                            d = abs(wc - cc)
                            if d < dist: name, dist = cname, d
                        return name

                    for row_words in rows:
                        bucket = {c["name"]: [] for c in columns}
                        for w in row_words:
                            cname = assign_to_nearest_col(w)
                            if cname is None:
                                for c in columns:
                                    intersects = not (w["x1"] < (c["x0"] - COL_MARGIN) or w["x0"] > (c["x1"] + COL_MARGIN))
                                    if intersects: cname = c["name"]; break
                            if cname is None: continue
                            bucket[cname].append(w)

                        cols_text = {k: " ".join([ww["text"] for ww in sorted(v, key=lambda z: z["x0"])]) for k, v in bucket.items()}
                        if not cols_text.get("ValorTotal") or not val_line_re.search(cols_text["ValorTotal"]): continue

                        tail = _normalize_ws(" ".join([cols_text.get("Beneficiario",""), cols_text.get("Credenciado",""), cols_text.get("Prestador","")]))
                        starts = [m.start() for m in code_start_re.finditer(tail)]
                        cred = cols_text.get("Credenciado","").strip()
                        prest = cols_text.get("Prestador","").strip()
                        if (not cred or not prest) and len(starts) >= 2:
                            i1, i2 = starts[-2], starts[-1]
                            prest = tail[i2:].strip()
                            cred  = tail[i1:i2].strip()

                        all_records.append({
                            "Atendimento":   cols_text.get("Atendimento","").strip(),
                            "NrGuia":        cols_text.get("NrGuia","").strip(),
                            "Realizacao":    cols_text.get("Realizacao","").strip(),
                            "Hora":          cols_text.get("Hora","").strip(),
                            "TipoGuia":      cols_text.get("TipoGuia","").strip(),
                            "Operadora":     cols_text.get("Operadora","").strip(),
                            "Matricula":     cols_text.get("Matricula","").strip(),
                            "Beneficiario":  cols_text.get("Beneficiario","").strip(),
                            "Credenciado":   cred,
                            "Prestador":     prest,
                            "ValorTotal":    cols_text.get("ValorTotal","").strip(),
                        })
        except Exception as e:
            if debug: st.error(f"[coord] Falha: {e}")

        out = pd.DataFrame(all_records)
        if not out.empty:
            try:
                out["Realizacao_dt"] = pd.to_datetime(out["Realizacao"], format="%d/%m/%Y", errors="coerce")
                out = out.sort_values(["Realizacao_dt","Hora"]).drop(columns=["Realizacao_dt"])
            except Exception:
                pass
        return ensure_atendimentos_schema(out)

# ========= Funções utilitárias =========
TARGET_COLS = ["Atendimento","NrGuia","Realizacao","Hora","TipoGuia",
               "Operadora","Matricula","Beneficiario","Credenciado",
               "Prestador","ValorTotal"]

def ensure_atendimentos_schema(df: pd.DataFrame) -> pd.DataFrame:
    for c in TARGET_COLS:
        if c not in df.columns:
            df[c] = ""
    return df[TARGET_COLS]

def to_float(valor):
    try:
        if valor is None or valor == "":
            return 0.0
        if isinstance(valor, str):
            valor = valor.replace(".","").replace(",",".").strip()
        return float(valor)
    except Exception:
        return 0.0

# ========= Parser modo texto =========
def parse_pdf_text_fallback(pdf_path: str, debug: bool = False) -> pd.DataFrame:
    import pdfplumber
    all_rows = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text: continue
                lines = text.split("\n")
                for line in lines:
                    m = re.search(r"(\d{2}/\d{2}/\d{4}).*?R\$[\d\.,]+", line)
                    if m:
                        parts = re.split(r"\s{2,}", line)
                        if len(parts) >= 5:
                            all_rows.append({
                                "Atendimento": parts[0].strip(),
                                "NrGuia": parts[1].strip(),
                                "Realizacao": parts[2].strip(),
                                "Hora": parts[3].strip(),
                                "TipoGuia": parts[4].strip(),
                                "Operadora": parts[5].strip() if len(parts)>5 else "",
                                "Matricula": parts[6].strip() if len(parts)>6 else "",
                                "Beneficiario": parts[7].strip() if len(parts)>7 else "",
                                "Credenciado": parts[8].strip() if len(parts)>8 else "",
                                "Prestador": parts[9].strip() if len(parts)>9 else "",
                                "ValorTotal": parts[-1].strip()
                            })
    except Exception as e:
        if debug: st.error(f"[text fallback] Falha: {e}")

    df = pd.DataFrame(all_rows)
    return ensure_atendimentos_schema(df)

# ========= Streamlit UI =========
st.set_page_config(page_title="PDF Atendimentos", layout="wide")

st.title("📄 Gerenciador de Atendimentos PDF")
st.sidebar.header("Configurações")

pdf_file = st.sidebar.file_uploader("Selecione o PDF de atendimentos", type=["pdf"])
use_coords = st.sidebar.checkbox("Usar parser coordenadas", value=True)
search_cred = st.sidebar.text_input("Pesquisar por Credenciado")

if pdf_file:
    pdf_path = os.path.join(DOWNLOAD_TEMPORARIO, pdf_file.name)
    with open(pdf_path, "wb") as f:
        f.write(pdf_file.read())

    st.info("Processando PDF... ⏳")
    if use_coords:
        df = parse_pdf_to_atendimentos_df(pdf_path, mode="coord", debug=True)
        if df.empty:
            st.warning("Parser coordenadas não retornou dados, usando fallback textual.")
            df = parse_pdf_text_fallback(pdf_path, debug=True)
    else:
        df = parse_pdf_text_fallback(pdf_path, debug=True)

    # Filtro credenciado
    if search_cred:
        df = df[df["Credenciado"].str.contains(search_cred, case=False, na=False)]

    if df.empty:
        st.warning("Nenhum registro encontrado.")
    else:
        st.success(f"📊 Total de registros: {len(df)}")
        st.dataframe(df)

        # Download CSV
        csv_bytes = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="⬇️ Baixar CSV",
            data=csv_bytes,
            file_name="atendimentos.csv",
            mime="text/csv"
        )


