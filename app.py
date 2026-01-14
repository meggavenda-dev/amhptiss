
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

# ========= Enforce de esquema da Tabela — Atendimentos =========
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

# ========= PDF → Tabela por colunas (pdfplumber, multipage, robusto) =========
def parse_pdf_to_atendimentos_df(pdf_path: str, debug: bool = False) -> pd.DataFrame:
    """
    Extrai a Tabela — Atendimentos do PDF (SSRS) de forma robusta:
    1) pdfplumber por coordenadas (todas as páginas), com tolerâncias:
       - bandas horizontais (linhas) por aproximação de 'top'
       - colunas por interseção de caixas (x0/x1) com margem
    2) Fallback textual via PyPDF2 + heurísticas (regex) caso o grid falhe.
    Em todos os casos, aplica ensure_atendimentos_schema() antes de retornar.
    """
    import pdfplumber
    from PyPDF2 import PdfReader

    TOP_TOL      = 3.5     # tolerância vertical (pts) para agrupar palavras na mesma linha
    MERGE_GAP_X  = 8.0     # para fundir palavras do cabeçalho em um bloco
    COL_MARGIN   = 2.5     # margem lateral para considerar interseção com coluna

    val_re        = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}$")
    code_start_re = re.compile(r"\d{3,6}-")
    all_records   = []

    # ---------- 1) Coordenadas ----------
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_i, page in enumerate(pdf.pages, start=1):
                words = page.extract_words(
                    use_text_flow=True,
                    extra_attrs=["x0", "x1", "top", "bottom"]
                )
                if not words:
                    continue

                # Cabeçalho
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

                # Fallback extract_tables se não achou cabeçalho
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
                            if debug:
                                st.info(f"[pdfplumber] extract_tables usado na página {page_i}.")
                    continue

                # Blocos do cabeçalho
                blocks = []
                cur = [header_words[0]]
                for w in header_words[1:]:
                    if (w["x0"] - cur[-1]["x1"]) <= MERGE_GAP_X:
                        cur.append(w)
                    else:
                        blocks.append(cur)
                        cur = [w]
                blocks.append(cur)

                header_blocks = [{
                    "text": " ".join([b["text"] for b in bl]),
                    "x0": min([b["x0"] for b in bl]),
                    "x1": max([b["x1"] for b in bl]),
                } for bl in blocks]

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
                if not columns:
                    if debug:
                        st.warning("Nenhuma coluna mapeada a partir do cabeçalho.")
                    continue

                if debug:
                    st.info(f"Colunas detectadas (página {page_i}): {[c['name'] for c in columns]}")

                # Palavras de dados (abaixo do cabeçalho); corta antes de "Total"
                data_words = [w for w in words if w["top"] > header_y + TOP_TOL]
                total_candidates = [w for w in data_words if w["text"].lower() == "total"]
                if total_candidates:
                    total_y = total_candidates[0]["top"]
                    data_words = [w for w in data_words if w["top"] < total_y - TOP_TOL]

                # Bandas horizontais (linhas)
                rows = []
                band = []
                last_top = None
                for w in sorted(data_words, key=lambda z: (round(z["top"], 1), z["x0"])):
                    if (last_top is None) or (abs(w["top"] - last_top) <= TOP_TOL):
                        band.append(w); last_top = w["top"]
                    else:
                        rows.append(band); band = [w]; last_top = w["top"]
                if band: rows.append(band)

                # Função interseção coluna
                def intersects(w, col):
                    return not (w["x1"] < (col["x0"] - COL_MARGIN) or w["x0"] > (col["x1"] + COL_MARGIN))

                # Coleta por coluna
                for row_words in rows:
                    cols_text = {}
                    for col in columns:
                        col_words = [w for w in row_words if intersects(w, col)]
                        txt = " ".join([w["text"] for w in sorted(col_words, key=lambda z: z["x0"])])
                        cols_text[col["name"]] = txt.strip()

                    # precisa ter ValorTotal válido
                    if not cols_text.get("ValorTotal") or not val_re.search(cols_text["ValorTotal"]):
                        continue

                    # Ajuste Credenciado/Prestador por padrão CODIGO-Nome
                    tail = " ".join([
                        cols_text.get("Beneficiario",""),
                        cols_text.get("Credenciado",""),
                        cols_text.get("Prestador",""),
                    ]).strip()
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
        if debug:
            st.error(f"[pdfplumber] Falha coordenadas: {e}")

    # Se já conseguimos algo por coordenadas, retorna
    if all_records:
        out = pd.DataFrame(all_records)
        if not out.empty:
            try:
                out["Realizacao_dt"] = pd.to_datetime(out["Realizacao"], format="%d/%m/%Y", errors="coerce")
                out = out.sort_values(["Realizacao_dt","Hora"]).drop(columns=["Realizacao_dt"])
            except Exception:
                pass
        out = ensure_atendimentos_schema(out)
        return sanitize_df(out)

    # ---------- 2) FALLBACK TEXTUAL ----------
    try:
        reader = PdfReader(open(pdf_path, "rb"))
        lines = []
        for page in reader.pages:
            txt = page.extract_text() or ""
            txt = txt.replace("\u00A0", " ")
            lines.extend([l.strip() for l in txt.splitlines() if l.strip()])

        # cabeçalho
        hdr_idx = -1
        for i, l in enumerate(lines):
            if ("Atendimento" in l) and ("Valor" in l) and ("Total" in l):
                hdr_idx = i; break
        if hdr_idx == -1: hdr_idx = 0

        # linhas até 'Total'
        data_lines = []
        for l in lines[hdr_idx+1:]:
            if l.lower().startswith("total "):
                break
            data_lines.append(l)

        parsed_rows = []
        val_re = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}$")
        code_start_re = re.compile(r"\d{3,6}-")

        for l in data_lines:
            m_val = val_re.search(l)
            if not m_val:
                continue
            valor = m_val.group(0)
            body  = l[:m_val.start()].strip()

            codes = list(code_start_re.finditer(body))
            if len(codes) >= 2:
                i1, i2 = codes[-2].start(), codes[-1].start()
                prest = body[i2:].strip()
                cred  = body[i1:i2].strip()
                body  = body[:i1].strip()
            elif len(codes) == 1:
                i2    = codes[-1].start()
                prest = body[i2:].strip()
                cred  = ""
                body  = body[:i2].strip()
            else:
                prest = cred = ""

            m_head = re.match(r"^(\d+)\s+(\d+)\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})\s+(.*)$", body)
            if not m_head:
                continue
            atendimento, nr_guia, realizacao, hora, rest = m_head.groups()

            toks = rest.split()
            def is_num(t): return re.fullmatch(r"\d+", t) is not None

            idx_mat = None
            for i, t in enumerate(toks):
                if is_num(t): idx_mat = i; break
            if idx_mat is None:
                for i, t in enumerate(toks):
                    if re.fullmatch(r"\d{6,}", t): idx_mat = i; break

            if idx_mat is None:
                tipo_guia   = toks[0]
                operadora   = " ".join(toks[1:]).strip()
                matricula   = ""
                beneficiario = ""
            else:
                if "/" in toks[0] and idx_mat >= 2 and re.fullmatch(r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ\-]{2,15}", toks[1]):
                    tipo_tokens = toks[0:2]; start_oper = 2
                else:
                    tipo_tokens = toks[0:1]; start_oper = 1
                tipo_guia   = " ".join(tipo_tokens)
                operadora   = " ".join(toks[start_oper:idx_mat]).strip()
                j = idx_mat
                mat_tokens = []
                while j < len(toks) and is_num(toks[j]):
                    mat_tokens.append(toks[j]); j += 1
                matricula    = " ".join(mat_tokens)
                beneficiario = " ".join(toks[j:]).strip()

            parsed_rows.append({
                "Atendimento": atendimento,
                "NrGuia": nr_guia,
                "Realizacao": realizacao,
                "Hora": hora,
                "TipoGuia": tipo_guia,
                "Operadora": operadora,
                "Matricula": matricula,
                "Beneficiario": beneficiario,
                "Credenciado": cred,
                "Prestador": prest,
                "ValorTotal": valor,
            })

        out = pd.DataFrame(parsed_rows)
        if not out.empty:
            try:
                out["Realizacao_dt"] = pd.to_datetime(out["Realizacao"], format="%d/%m/%Y", errors="coerce")
                out = out.sort_values(["Realizacao_dt","Hora"]).drop(columns=["Realizacao_dt"])
            except Exception:
                pass
        out = ensure_atendimentos_schema(out)
        return sanitize_df(out)

    except Exception as e:
        if debug:
            st.error(f"[PyPDF2] Falha textual: {e}")

    # nada deu — retorna vazio com esquema
    return ensure_atendimentos_schema(pd.DataFrame())
