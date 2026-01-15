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
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException

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
        n = seen.get(c2, 0) + 1
        seen[c2] = n
        new_cols.append(c2 if n == 1 else f"{c2}_{n}")
    df.columns = new_cols
    for col in df.select_dtypes(include=["object"]).columns:
        df[col] = df[col].apply(sanitize_value)
    return df

# ========= Esquema =========
TARGET_COLS = [
    "Atendimento","NrGuia","Prontuario","Realizacao","Hora","TipoGuia",
    "Operadora","Matricula","Beneficiario","Credenciado",
    "Prestador","ValorTotal"
]

def ensure_atendimentos_schema(df: pd.DataFrame) -> pd.DataFrame:
    for c in TARGET_COLS:
        if c not in df.columns:
            df[c] = ""
    return df[TARGET_COLS]

# ========= Correções linha a linha =========
def fix_row(row):
    # Beneficiário contaminado
    if isinstance(row["Beneficiario"], str):
        if re.search(r"\d{2}/\d{2}/\d{4}", row["Beneficiario"]) or "Atendimento" in row["Beneficiario"]:
            partes = row["Beneficiario"].split()
            if partes:
                row["Beneficiario"] = partes[0].strip()

    # Inversão Credenciado/Prestador
    has_code_cred = isinstance(row["Credenciado"], str) and re.search(r"\d{5,6}-", row["Credenciado"])
    has_code_prest = isinstance(row["Prestador"], str) and re.search(r"\d{5,6}-", row["Prestador"])
    if has_code_prest and not has_code_cred:
        row["Credenciado"], row["Prestador"] = row["Prestador"], row["Credenciado"]

    # Operadora sem código
    OPERADORA_FIX = {
        "CASEC": "CASEC (043)",
        "CONAB": "CONAB(020)",
        "GEAP": "GEAP(225)",
        "BACEN": "BACEN(104)",
        "GDF SAÚDE": "GDF SAÚDE(433)",
        "CARE PLUS": "CARE PLUS(326)",
        "CBMDF": "CBMDF(375)",
    }
    if isinstance(row["Operadora"], str):
        op = row["Operadora"].strip()
        if not re.search(r"\(.+\)$", op):
            for k, v in OPERADORA_FIX.items():
                if op.startswith(k):
                    row["Operadora"] = v
                    break

    # ValorTotal inválido
    if isinstance(row["ValorTotal"], str):
        val = row["ValorTotal"].strip()
        if not re.fullmatch(r"\d{1,3}(\.\d{3})*,\d{2}", val):
            row["ValorTotal"] = "0,00"

    return row

# ========= Validação =========
def validate_df(df: pd.DataFrame):
    issues = []
    for i, r in df.iterrows():
        if not re.fullmatch(r"\d{8}", str(r["Atendimento"])):
            issues.append((i, "Atendimento inválido"))
        if not re.fullmatch(r"\d{8}", str(r["NrGuia"])):
            issues.append((i, "NrGuia inválido"))
        if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", str(r["Realizacao"])):
            issues.append((i, "Data inválida"))
        if isinstance(r["Operadora"], str) and not re.search(r"\(.+\)$", r["Operadora"]):
            issues.append((i, "Operadora sem código"))
        if isinstance(r["ValorTotal"], str) and not re.fullmatch(r"\d{1,3}(\.\d{3})*,\d{2}", r["ValorTotal"]):
            issues.append((i, "ValorTotal inválido"))
    return issues

# ========= Parser PDF (textual fallback) =========
def parse_pdf_to_atendimentos_df(pdf_path: str, debug: bool = False) -> pd.DataFrame:
    from PyPDF2 import PdfReader

    def _normalize_ws(s: str) -> str:
        return re.sub(r"\s+", " ", s.replace("\u00A0", " ")).strip()

    reader = PdfReader(open(pdf_path, "rb"))
    all_lines = []
    for page in reader.pages:
        txt = page.extract_text()
        if txt:
            all_lines.extend(txt.splitlines())

    clean_lines = []
    for line in all_lines:
        if "Atendimentos Realizados Sintético" in line: continue
        if "Emitido por" in line and "Página" in line: continue
        clean_lines.append(line.strip())

    big = _normalize_ws(" ".join(clean_lines))

    record_start_re = re.compile(r"(?P<atend>\d{8})\s+(?P<guia>\d{8})\s+(?P<data>\d{2}/\d{2}/\d{4})")
    matches = list(record_start_re.finditer(big))
    parsed = []

    hora_re = re.compile(r"\b(\d{2}:\d{2})\b")
    valor_re = re.compile(r"\b(\d{1,3}(?:\.\d{3})*,\d{2})\b")
    tipo_tokens = ["Consulta", "SP/SADT", "Não TISS", "SADT"]
    cod_re = re.compile(r"\b(\d{5,6}-[A-Z0-9].+?)\b")
    operadora_re = re.compile(r"\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ\s\-/]+?)\s*\(\s*\d{2,4}\s*\)")

    for i in range(len(matches)):
        start_idx = matches[i].start()
        end_idx = matches[i+1].start() if i+1 < len(matches) else len(big)
        chunk = big[start_idx:end_idx].strip()

        atend = matches[i].group("atend")
        guia  = matches[i].group("guia")
        data  = matches[i].group("data")

        # hora
        hora_m = hora_re.search(chunk)
        hora = hora_m.group(1) if hora_m else ""

        # valor total: último valor do bloco
        valores = valor_re.findall(chunk)
        valor_total = valores[-1] if valores else ""

        # tipo de guia
        tipo_guia = ""
        for t in tipo_tokens:
            if t in chunk:
                tipo_guia = t
                break

        # operadora
        operadora_m = operadora_re.search(chunk)
        operadora = operadora_m.group(0) if operadora_m else ""

        # remover campos já identificados para isolar o "miolo"
        miolo = chunk
        for token in [atend, guia, data, hora, valor_total, tipo_guia, operadora]:
            if token:
                miolo = miolo.replace(token, " ")
        miolo = _normalize_ws(miolo)

        # códigos (credenciado/prestador)
        codes = list(cod_re.finditer(miolo))
        credenciado, prestador = "", ""
        if len(codes) >= 2:
            credenciado = codes[-2].group(1).strip()
            prestador   = codes[-1].group(1).strip()
            miolo = miolo.replace(credenciado, " ").replace(prestador, " ")
            miolo = _normalize_ws(miolo)
        elif len(codes) == 1:
            credenciado = codes[0].group(1).strip()
            miolo = _normalize_ws(miolo.replace(credenciado, " "))

        # matrícula
        mat_m = re.search(r"\b([A-Z0-9]{5,}X?[A-Z0-9/]*)\b", miolo)
        matricula = mat_m.group(1) if mat_m else ""

        # beneficiário
        beneficiario = miolo
        if matricula:
            beneficiario = _normalize_ws(beneficiario.replace(matricula, ""))
        beneficiario = re.sub(r"\b(Consulta|SP/SADT|Não TISS|SADT)\b", "", beneficiario).strip()

        parsed.append({
            "Atendimento": atend,
            "NrGuia": guia,
            "Prontuario": "",  # campo extra, pode vir vazio
            "Realizacao": data,
            "Hora": hora,
            "TipoGuia": tipo_guia,
            "Operadora": operadora,
            "Matricula": matricula,
            "Beneficiario": beneficiario,
            "Credenciado": credenciado,
            "Prestador": prestador,
            "ValorTotal": valor_total
        })

    df = pd.DataFrame(parsed)
    df = ensure_atendimentos_schema(sanitize_df(df))
    df = df.apply(fix_row, axis=1)

    if debug:
        import io
        buf = io.StringIO()
        df.head(20).to_string(buf)
        print(buf.getvalue())

    return df
if not st.session_state.db_consolidado.empty:
    st.divider()
    df_preview = sanitize_df(st.session_state.db_consolidado)
    st.subheader("📊 Base consolidada (temporária)")
    st.dataframe(df_preview, use_container_width=True)

    # 🔎 Validação final
    final_issues = validate_df(df_preview)
    if final_issues:
        st.warning(f"Inconsistências remanescentes: {len(final_issues)}. Ex.: {final_issues[:10]}")
    else:
        st.success("Base consolidada sem inconsistências de formato.")

    # Exportação CSV
    csv_bytes = df_preview.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        "💾 Baixar Consolidação (CSV)",
        csv_bytes,
        file_name="consolidado_amhp.csv",
        mime="text/csv"
    )
