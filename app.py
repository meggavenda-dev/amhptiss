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

# ========= PDF → Tabela =========
def parse_pdf_to_atendimentos_df(pdf_path: str, mode: str = "coord", debug: bool = False) -> pd.DataFrame:
    import pdfplumber
    from PyPDF2 import PdfReader

    val_re        = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")
    code_start_re = re.compile(r"\d{3,6}-")
    re_total_blk  = re.compile(r"total\s*r\$\s*\d{1,3}(?:\.\d{3})*,\d{2}", re.I)
    head_re       = re.compile(r"(\d+)\s+(\d+)\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})\s+(.*)")

    def _normalize_ws(s: str) -> str:
        return re.sub(r"\s+", " ", s.replace("\u00A0", " ")).strip()

    # >>> AJUSTE PDF TEXTUAL <<<
    def parse_by_text() -> pd.DataFrame:
        try:
            reader = PdfReader(open(pdf_path, "rb"))
            text_all = []
            for page in reader.pages:
                text_all.append(page.extract_text() or "")
            big = _normalize_ws(" ".join(text_all))
            if not big:
                return pd.DataFrame(columns=TARGET_COLS)

            big = re_total_blk.sub("", big)

            parts = re.split(rf"({val_re.pattern})", big)
            records = []
            for i in range(1, len(parts), 2):
                valor = parts[i].strip()
                body  = _normalize_ws(parts[i-1])
                if not body:
                    continue
                m_start = head_re.search(body)
                if m_start:
                    body = body[m_start.start():].strip()
                if body.lower().startswith("total "):
                    continue
                records.append(f"{body} {valor}".strip())

            parsed = []
            for l in records:
                m_vals = list(val_re.finditer(l))
                if not m_vals:
                    continue
                valor = m_vals[-1].group(0)
                body  = l[:m_vals[-1].start()].strip()

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

                m_head = head_re.search(body)
                if not m_head:
                    continue
                atendimento, nr_guia, realizacao, hora, rest = m_head.groups()

                toks = rest.split()
                def is_num(t): return re.fullmatch(r"\d+", t) is not None

                idx_mat = None
                for j, t in enumerate(toks):
                    if is_num(t):
                        idx_mat = j; break

                if idx_mat is None:
                    tipo_guia = toks[0]
                    operadora = " ".join(toks[1:])
                    matricula = ""
                    beneficiario = ""
                else:
                    tipo_guia = toks[0]
                    operadora = " ".join(toks[1:idx_mat])
                    j = idx_mat
                    mat_tokens = []
                    while j < len(toks) and is_num(toks[j]):
                        mat_tokens.append(toks[j]); j += 1
                    matricula = " ".join(mat_tokens)
                    beneficiario = " ".join(toks[j:])

                parsed.append({
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

            out = pd.DataFrame(parsed)
            return ensure_atendimentos_schema(out)

        except Exception as e:
            if debug:
                st.error(f"[text] Falha: {e}")
            return pd.DataFrame(columns=TARGET_COLS)

    return sanitize_df(parse_by_text())
