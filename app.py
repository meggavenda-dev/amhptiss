
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

# ========= Regex compartilhados =========
val_re        = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")  # valor pt-BR
code_start_re = re.compile(r"\d{3,6}-")
re_total_blk  = re.compile(r"total\s*r\$\s*\d{1,3}(?:\.\d{3})*,\d{2}", re.I)
HDR_ANY       = re.compile(r"(\d{6,})(?:\s+(\d{6,}))?\s*(\d{2}/\d{2}/\d{4})\s*(\d{2}:\d{2})")

# ========= Helpers comuns =========
def _normalize_ws2(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\u00A0", " ")).strip()

def is_mat_token(t: str) -> bool:
    if re.fullmatch(r"\d{5,}", t):
        return True
    return bool(re.fullmatch(r"[0-9A-Z]{5,}", t))

# ========= (LEGADOS) Parser tradicional e PDF (mantidos) =========
# -- (a) Parser legado por texto (mantido para fallback/inspeção) --
head_re = re.compile(r"(\d+)\s+(\d+)\s+(\d{2}/\d{2}/\d{4})\s*(\d{2}:\d{2})(.*)")

def split_tipo_operadora(tokens):
    if not tokens:
        return "", [], 0
    t0 = tokens[0]; t0low = t0.lower()
    if "/" in t0:
        if t0low.startswith("sp/sadt") and t0low != "sp/sadt":
            rest0 = t0[len("SP/SADT"):]
            tail  = ([rest0] if rest0 else []) + tokens[1:]
            return "SP/SADT", tail, 1
        return t0, tokens[1:], 1
    if t0low.startswith("consulta") and t0low != "consulta":
        rest0 = t0[len("Consulta"):]
        tail  = ([rest0] if rest0 else []) + tokens[1:]
        return "Consulta", tail, 1
    if t0low in ("consulta", "sp/sadt", "honorário", "honorário individual",
                 "não", "não tiss", "não tiss - atendimento"):
        return tokens[0], tokens[1:], 1
    return tokens[0], tokens[1:], 1

def parse_record_text(rec: str):
    rec = _normalize_ws2(rec)
    rec = re_total_blk.sub("", rec)

    m_vals = list(val_re.finditer(rec))
    if not m_vals:
        return None
    valor = m_vals[-1].group(0)
    body  = rec[:m_vals[-1].start()].strip()

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
        return None
    atendimento, nr_guia, realizacao, hora, rest = m_head.groups()

    # patches
    rest = re.sub(r'(?i)\b(Consulta)(?=[A-ZÁ-Ú])', r'\1 ', rest)
    rest = re.sub(r'(?i)\b(SP/SADT)(?=[A-ZÁ-Ú])', r'\1 ', rest)
    rest = re.sub(r'(?i)\b(Honorário(?:\s*Individual)?)\s*(?=[A-ZÁ-Ú])', r'\1 ', rest)
    rest = re.sub(r'(?i)\b(Não(?:\s*TISS)?(?:\s*-\s*Atendimento)?)\s*(?=[A-ZÁ-Ú])', r'\1 ', rest)
    rest = re.sub(r"(Consulta|SP/SADT)(?=[A-Z]{2,}\()", r"\1 ", rest)

    toks = rest.split()
    tipo, tail, _ = split_tipo_operadora(toks)

    idx_mat = None
    for j, t in enumerate(tail):
        if is_mat_token(t):
            idx_mat = j
            break

    if idx_mat is None:
        operadora    = " ".join(tail).strip()
        matricula    = ""
        beneficiario = ""
    else:
        operadora = " ".join(tail[:idx_mat]).strip()
        k = idx_mat
        mat_tokens = []
        while k < len(tail) and is_mat_token(tail[k]):
            mat_tokens.append(tail[k]); k += 1
        matricula    = " ".join(mat_tokens).strip()
        beneficiario = " ".join(tail[k:]).strip()

    return {
        "Atendimento":  atendimento,
        "NrGuia":       nr_guia,
        "Realizacao":   realizacao,
        "Hora":         hora,
        "TipoGuia":     tipo,
        "Operadora":    operadora,
        "Matricula":    matricula,
        "Beneficiario": beneficiario,
        "Credenciado":  cred,
        "Prestador":    prest,
        "ValorTotal":   valor,
    }

def parse_relatorio_text_to_atendimentos_df(texto: str, debug_heads: bool = False) -> pd.DataFrame:
    big = (texto or "").replace("\u00A0"," ")
    big = _ILLEGAL_CTRL_RE.sub("", big)
    m = re.search(r"(Atendimento\s*Nr\.?\s*Guia.*?Valor\s*Total)", big, flags=re.I|re.S)
    if m:
        big = big[m.end():]
    big = re_total_blk.sub("", big)
    if not big:
        return pd.DataFrame(columns=TARGET_COLS)

    parts = re.split(rf"({val_re.pattern})", big)
    records = []
    for i in range(1, len(parts), 2):
        valor = parts[i].strip()
        body  = _normalize_ws2(parts[i-1])
        if not body:
            continue
        m_start = head_re.search(body)
        if not m_start:
            continue
        body = body[m_start.start():].strip()
        if body.lower().startswith("total "):
            continue
        records.append(f"{body} {valor}".strip())

    parsed = []
    for rec in records:
        row = parse_record_text(rec)
        if row:
            parsed.append(row)

    if parsed:
        out = pd.DataFrame(parsed)
        try:
            out["Realizacao_dt"] = pd.to_datetime(out["Realizacao"], format="%d/%m/%Y", errors="coerce")
            out = out.sort_values(["Realizacao_dt","Hora"]).drop(columns=["Realizacao_dt"])
        except Exception:
            pass
        return ensure_atendimentos_schema(sanitize_df(out))

    return pd.DataFrame(columns=TARGET_COLS)

# -- (b) Parser PDF (mantido, sem alterações) --
def parse_pdf_to_atendimentos_df(pdf_path: str, mode: str = "coord", debug: bool = False) -> pd.DataFrame:
    import pdfplumber
    from PyPDF2 import PdfReader

    TOP_TOL      = 4.5
    MERGE_GAP_X  = 10.0
    COL_MARGIN   = 4.0

    val_re_local        = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")
    val_line_re         = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}$")
    code_start_re_local = re.compile(r"\d{3,6}-")
    re_total_blk_local  = re.compile(r"total\s*r\$\s*\d{1,3}(?:\.\d{3})*,\d{2}", re.I)
    head_re_local       = re.compile(r"(\d+)\s+(\d+)\s+(\d{2}/\d{2}/\d{4})\s*(\d{2}:\d{2})(.*)")

    def _normalize_ws_local(s: str) -> str:
        return re.sub(r"\s+", " ", s.replace("\u00A0", " ")).strip()

    def parse_by_text() -> pd.DataFrame:
        try:
            reader = PdfReader(open(pdf_path, "rb"))
            text_all = []
            for page in reader.pages:
                txt = page.extract_text() or ""
                text_all.append(txt)
            big = _normalize_ws_local(" ".join(text_all))
            if not big:
                return pd.DataFrame(columns=TARGET_COLS)

            big = re_total_blk_local.sub("", big)

            parts = re.split(rf"({val_re_local.pattern})", big)
            records = []
            for i in range(1, len(parts), 2):
                valor = parts[i].strip()
                body  = _normalize_ws_local(parts[i-1])
                if not body:
                    continue
                m_start = head_re_local.search(body)
                if m_start:
                    body = body[m_start.start():].strip()
                if body.lower().startswith("total "):
                    continue
                records.append(f"{body} {valor}".strip())

            parsed = []
            for l in records:
                m_vals = list(val_re_local.finditer(l))
                if not m_vals:
                    continue
                valor = m_vals[-1].group(0)
                body  = l[:m_vals[-1].start()].strip()

                codes = list(code_start_re_local.finditer(body))
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

                m_head = head_re_local.search(body)
                if not m_head:
                    continue
                atendimento, nr_guia, realizacao, hora, rest = m_head.groups()

                # patches
                rest = re.sub(r'(?i)\b(Consulta)(?=[A-ZÁ-Ú])', r'\1 ', rest)
                rest = re.sub(r'(?i)\b(SP/SADT)(?=[A-ZÁ-Ú])', r'\1 ', rest)
                rest = re.sub(r'(?i)\b(Honorário(?:\s*Individual)?)\s*(?=[A-ZÁ-Ú])', r'\1 ', rest)
                rest = re.sub(r'(?i)\b(Não(?:\s*TISS)?(?:\s*-\s*Atendimento)?)\s*(?=[A-ZÁ-Ú])', r'\1 ', rest)
                rest = re.sub(r"(Consulta|SP/SADT)(?=[A-Z]{2,}\()", r"\1 ", rest)

                toks = rest.split()
                tipo, tail, _ = split_tipo_operadora(toks)

                idx_mat = None
                for j, t in enumerate(toks):
                    if re.fullmatch(r"\d+", t):
                        idx_mat = j; break
                if idx_mat is None:
                    for j, t in enumerate(toks):
                        if re.fullmatch(r"\d{6,}", t):
                            idx_mat = j; break

                if idx_mat is None:
                    operadora   = " ".join(toks[1:]).strip() if len(toks) > 1 else ""
                    matricula   = ""
                    beneficiario = ""
                    tipo_guia   = toks[0] if toks else ""
                else:
                    tipo_guia   = toks[0]
                    operadora   = " ".join(toks[1:idx_mat]).strip()
                    j = idx_mat
                    mat_tokens = []
                    while j < len(toks) and re.fullmatch(r"\d+", toks[j]):
                        mat_tokens.append(toks[j]); j += 1
                    matricula    = " ".join(mat_tokens)
                    beneficiario = " ".join(toks[j:]).strip()

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
            if not out.empty:
                try:
                    out["Realizacao_dt"] = pd.to_datetime(out["Realizacao"], format="%d/%m/%Y", errors="coerce")
                    out = out.sort_values(["Realizacao_dt","Hora"]).drop(columns=["Realizacao_dt"])
                except Exception:
                    pass
            return ensure_atendimentos_schema(out)

        except Exception as e:
            if debug:
                st.error(f"[text] Falha: {e}")
            return pd.DataFrame(columns=TARGET_COLS)

    # para manter curto: usamos o modo textual
    return parse_by_text()

# ========= NOVO: Parser Streaming — ÂNCORAS EXPLÍCITAS (versão de validação, NÃO v2) =========
def normalize_collages(s: str) -> str:
    """Normaliza 'colagens' do ReportViewer e escapes literais."""
    if not s: return ""
    s = s.replace("\u00A0", " ")
    s = _ILLEGAL_CTRL_RE.sub("", s)

    # Escapes do ReportViewer -> literais
    s = s.replace(r"\-", "-").replace(r"\(", "(").replace(r"\)", ")")

    # Espaços estratégicos
    s = re.sub(r"(\d{10,})(\d{2}/\d{2}/\d{4})", r"\1 \2", s)         # Atendimento+NrGuia ↔ Data
    s = re.sub(r"(\d{2}/\d{2}/\d{4})(\d{2}:\d{2})", r"\1 \2", s)     # Data ↔ Hora
    s = re.sub(r"(\d{2}:\d{2})(?=[A-Za-zÁ-Úá-úNÇS/])", r"\1 ", s)    # Hora ↔ Tipo
    s = re.sub(r"(\d{4})(\d{2}:\d{2})", r"\1 \2", s)                 # Ano ↔ Hora (fallback)

    # Tipo ↔ Operadora
    s = re.sub(r'(?i)\b(Consulta)(?=[A-ZÁ-Ú])', r'\1 ', s)
    s = re.sub(r'(?i)\b(SP/SADT)(?=[A-ZÁ-Ú])', r'\1 ', s)
    s = re.sub(r'(?i)\b(Honorário(?:\s*Individual)?)\s*(?=[A-ZÁ-Ú])', r'\1 ', s)
    s = re.sub(r'(?i)\b(Não(?:\s*TISS)?(?:\s*-\s*Atendimento)?)\s*(?=[A-ZÁ-Ú])', r'\1 ', s)
    s = re.sub(r"(Consulta|SP/SADT)(?=[A-Z]{2,}\()", r"\1 ", s)

    # ')' ↔ Matrícula + Matrícula ↔ Nome
    s = re.sub(r"(\))(\d{5,})", r"\1 \2", s)
    s = re.sub(r"(\d{5,})([A-ZÁ-Ú])", r"\1 \2", s)

    return re.sub(r"\s+", " ", s).strip()

def parse_streaming_with_anchors(texto: str) -> pd.DataFrame:
    """
    Parser por ÂNCORAS (versão de validação — NÃO v2):
      1) Normaliza colagens (sem alterações de semântica).
      2) Segmenta por cabeçalho: número(s) + data + hora.
      3) Âncora de fim: último valor dentro do segmento; se não existir, descarta o segmento.
      4) Âncora central (parênteses com código) por TOKENS: procura o 1º token que contenha '(\\d+)'.
      5) À esquerda do token '(\\d+)': Tipo + nome da Operadora; à direita: Matrícula, Beneficiário, Códigos.
    """
    if not texto or not str(texto).strip():
        return pd.DataFrame(columns=TARGET_COLS)

    s = normalize_collages(texto)

    # Drop preâmbulo e 'Total R$ ...'
    m = re.search(r"(Atendimento\s*Nr\.?\s*Guia.*?Valor\s*Total)", s, flags=re.I|re.S)
    if m: s = s[m.end():]
    s = re.sub(r"total\s*r\$\s*\d{1,3}(?:\.\d{3})*,\d{2}", "", s, flags=re.I)

    heads = list(HDR_ANY.finditer(s))
    if not heads:
        return pd.DataFrame(columns=TARGET_COLS)

    rows = []
    for i, mh in enumerate(heads):
        start = mh.start()
        end   = heads[i+1].start() if (i + 1) < len(heads) else len(s)
        segment = s[start:end]

        # Âncora de fim: último valor (dentro do segmento); se não achar, descarta
        vals = list(val_re.finditer(segment))
        if not vals:
            # (NÃO usar janela estendida nesta versão)
            continue
        last_val  = vals[-1].group(0)
        working   = segment[:vals[-1].start()].strip()

        # Cabeçalho
        a_num, guia_num, data, hora = mh.groups()
        if guia_num and guia_num.strip():
            atendimento = a_num; nr_guia = guia_num
        else:
            atendimento = a_num[:-8] if len(a_num) > 8 else a_num
            nr_guia     = a_num[-8:] if len(a_num) > 8 else ""

        # Resto após Hora
        hpos = working.find(hora)
        if hpos == -1:
            continue
        rest = working[hpos + len(hora):].strip()

        # Normalizações leves (Tipo ↔ Operadora)
        rest = re.sub(r'(?i)\b(Consulta)(?=[A-ZÁ-Ú])', r'\1 ', rest)
        rest = re.sub(r'(?i)\b(SP/SADT)(?=[A-ZÁ-Ú])', r'\1 ', rest)
        rest = re.sub(r'(?i)\b(Honorário(?:\s*Individual)?)\s*(?=[A-ZÁ-Ú])', r'\1 ', rest)
        rest = re.sub(r'(?i)\b(Não(?:\s*TISS)?(?:\s*-\s*Atendimento)?)\s*(?=[A-ZÁ-Ú])', r'\1 ', rest)
        rest = re.sub(r"(Consulta|SP/SADT)(?=[A-Z]{2,}\()", r"\1 ", rest)

        # ---- ÂNCORA CENTRAL por TOKENS ----
        toks = rest.split()
        if not toks:
            continue
        tipo = toks[0]
        token_idx_code = None
        for j in range(1, len(toks)):
            if re.search(r"\(\d{2,}\)", toks[j]):  # ex.: "AFFEGO(056)", "SAÚDE(433)", "(216)"
                token_idx_code = j
                break

        if token_idx_code is None:
            # Fallback mínimo: tenta heurística antiga
            tail = toks[1:]
            idx_mat = None
            for j, t in enumerate(tail):
                if is_mat_token(t):
                    idx_mat = j; break
            if idx_mat is None:
                operadora    = " ".join(tail).strip()
                matricula    = ""
                beneficiario = ""
            else:
                operadora = " ".join(tail[:idx_mat]).strip()
                k = idx_mat
                mats = []
                while k < len(tail) and is_mat_token(tail[k]):
                    mats.append(tail[k]); k += 1
                matricula    = " ".join(mats).strip()
                beneficiario = " ".join(tail[k:]).strip()
        else:
            # Operadora = tudo de toks[1 : token_idx_code+1] (inclui token com '(ddd)')
            operadora_tokens = toks[1:token_idx_code+1]
            operadora = " ".join(operadora_tokens).strip()
            right_tail = toks[token_idx_code+1:]

            # Matrícula = 1º alfanum longo; Beneficiário = até códigos
            matricula = ""
            if right_tail:
                # 1º token longo
                pos_mat = None
                for k, t in enumerate(right_tail):
                    if re.fullmatch(r"[0-9A-Z]{5,}", t):
                        pos_mat = k; break
                if pos_mat is not None:
                    matricula = right_tail[pos_mat]
                    after_mat = right_tail[pos_mat+1:]
                else:
                    after_mat = right_tail[:]
            else:
                after_mat = []

            ben_str = " ".join(after_mat).strip()

            # Credenciado/Prestador via códigos no final
            codes = list(code_start_re.finditer(ben_str))
            if len(codes) >= 2:
                i1, i2 = codes[-2].start(), codes[-1].start()
                prest = ben_str[i2:].strip()
                cred  = ben_str[i1:i2].strip()
                beneficiario = ben_str[:i1].strip()
            elif len(codes) == 1:
                i2 = codes[-1].start()
                prest = ben_str[i2:].strip()
                cred  = ""
                beneficiario = ben_str[:i2].strip()
            else:
                cred = prest = ""
                beneficiario = ben_str

        rows.append({
            "Atendimento":  atendimento,
            "NrGuia":       nr_guia,
            "Realizacao":   data,
            "Hora":         hora,
            "TipoGuia":     tipo,
            "Operadora":    operadora,
            "Matricula":    matricula,
            "Beneficiario": beneficiario,
            "Credenciado":  cred,
            "Prestador":    prest,
            "ValorTotal":   last_val,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=TARGET_COLS)

    try:
        df["Realizacao_dt"] = pd.to_datetime(df["Realizacao"], format="%d/%m/%Y", errors="coerce")
        df = df.sort_values(["Realizacao_dt","Hora"]).drop(columns=["Realizacao_dt"])
    except Exception:
        pass

    return ensure_atendimentos_schema(sanitize_df(df))

# ========= Selenium / Automação =========
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

def capture_report_text(driver):
    try:
        el = driver.find_element(By.ID, "VisibleReportContent")
        txt = el.text.strip()
        if len(txt) > 50:
            return txt, "#VisibleReportContent.text"
        txt2 = driver.execute_script("return arguments[0].textContent;", el) or ""
        txt2 = txt2.strip()
        if len(txt2) > 50:
            return txt2, "#VisibleReportContent.textContent"
    except Exception:
        pass

    for sel in [".doc", ".FixedTable", ".FixedDocument", "div[aria-label='Relatório']"]:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            txt = el.text.strip()
            if len(txt) > 50:
                return txt, f"{sel}.text"
            txt2 = driver.execute_script("return arguments[0].textContent;", el) or ""
            txt2 = txt2.strip()
            if len(txt2) > 50:
                return txt2, f"{sel}.textContent"
        except Exception:
            continue

    try:
        cells = driver.find_elements(By.CSS_SELECTOR, ".FixedTable *")
        if not cells:
            cells = driver.find_elements(By.CSS_SELECTOR, ".doc *")
        parts = []
        for c in cells:
            t = (c.text or "").strip()
            if t:
                parts.append(t)
        if parts:
            txt = " ".join(parts)
            if len(txt) > 50:
                return txt, "FixedTable/doc cells join"
    except Exception:
        pass

    try:
        txt = driver.execute_script("return document.body.innerText;") or ""
        txt = txt.strip()
        if len(txt) > 50:
            return txt, "document.body.innerText"
    except Exception:
        pass

    try:
        txt2 = driver.execute_script("return document.body.textContent;") or ""
        txt2 = txt2.strip()
        if len(txt2) > 50:
            return txt2, "document.body.textContent"
    except Exception:
        pass

    return "", "no-text (image/canvas rendering)"

def to_float_br(s):
    try:
        return float(str(s).replace('.', '').replace(',', '.'))
    except Exception:
        return 0.0

# ========= UI =========
with st.sidebar:
    st.header("Configurações")
    data_ini    = st.text_input("📅 Data Inicial (dd/mm/aaaa)", value="01/01/2026")
    data_fim    = st.text_input("📅 Data Final (dd/mm/aaaa)", value="13/01/2026")
    negociacao  = st.text_input("🤝 Tipo de Negociação", value="Direto")
    status_list = st.multiselect(
        "📌 Status",
        options=["300 - Pronto para Processamento","200 - Em Análise","100 - Recebido","400 - Processado"],
        default=["300 - Pronto para Processamento"]
    )
    wait_time_main     = st.number_input("⏱️ Tempo extra pós login/troca de tela (s)", min_value=0, value=10)
    wait_time_download = st.number_input("⏱️ Tempo extra para concluir download (s) [apenas PDF]", min_value=10, value=18)

    extraction_mode    = st.selectbox(
        "🧠 Modo de extração do relatório",
        ["Texto (sem PDF) — recomendado", "PDF — exportar e tratar (legado)"]
    )
    debug_parser       = st.checkbox("🧪 Debug do parser PDF", value=False)
    debug_heads        = st.checkbox("🧩 Mostrar contagem de cabeçalhos detectados (modo TEXTO)", value=True)

    # 🔘 Toggle — Forçar parser streaming unificado (agora chama Âncoras Explícitas)
    force_streaming    = st.toggle("⚙️ Forçar parser streaming (texto colado/sem espaços)", value=True)
    st.caption("Ligado: usa o parser por âncoras explícitas. Desligado: usa o parser legado.")

# ========= (Opcional) Processar TEXTO manualmente =========
with st.expander("🧪 Colar TEXTO do relatório (sem automação)", expanded=False):
    texto_manual = st.text_area("📋 Cole aqui o texto completo do ReportViewer:", height=250)
    if st.button("Processar TEXTO (manual)"):
        if not texto_manual.strip():
            st.warning("Cole o texto do relatório e tente novamente.")
        else:
            # Usa o toggle para decidir o parser
            if force_streaming:
                df_txt = parse_streaming_with_anchors(texto_manual)  # <<<<<<<<
            else:
                df_txt = parse_relatorio_text_to_atendimentos_df(texto_manual, debug_heads=debug_heads)

            if df_txt.empty:
                st.error("Parser não conseguiu extrair linhas deste TEXTO.")
            else:
                df_txt["Filtro_Negociacao"] = sanitize_value(negociacao)
                df_txt["Filtro_Status"]     = "Manual (Texto)"
                df_txt["Periodo_Inicio"]    = sanitize_value(data_ini)
                df_txt["Periodo_Fim"]       = sanitize_value(data_fim)

                total_br = f"R$ {df_txt['ValorTotal'].apply(to_float_br).sum():,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                st.info(f"📑 Total do texto processado: **{total_br}** — {len(df_txt)} linha(s)")
                st.dataframe(df_txt[TARGET_COLS], use_container_width=True)

                st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df_txt], ignore_index=True)
                st.success(f"✅ Adicionado à consolidação. Registros acumulados: {len(st.session_state.db_consolidado)}")

# ========= (Opcional) Processar PDF manualmente =========
with st.expander("🧪 Testar parser com upload de PDF (sem automação)", expanded=False):
    up = st.file_uploader("Envie um PDF do AMHPTISS para teste", type=["pdf"])
    if up and st.button("Processar PDF (teste)"):
        tmp_pdf = os.path.join(DOWNLOAD_TEMPORARIO, "teste_upload.pdf")
        with open(tmp_pdf, "wb") as f:
            f.write(up.getvalue())
        df_test = parse_pdf_to_atendimentos_df(tmp_pdf, mode="text", debug=debug_parser)
        if df_test.empty:
            st.error("Parser não conseguiu extrair linhas deste PDF usando o modo textual.")
        else:
            st.success(f"{len(df_test)} linha(s) extraída(s) pelo modo textual.")
            st.dataframe(df_test, use_container_width=True)

# ========= Botão principal =========
if st.button("🚀 Iniciar Processo"):
    driver = configurar_driver()
    try:
        with st.status("Executando automação...", expanded=True) as status:
            wait = WebDriverWait(driver, 40)

            # 1) Login
            st.write("🔑 Fazendo login...")
            driver.get("https://portal.amhp.com.br/")
            wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            time.sleep(wait_time_main)

            # 2) AMHPTISS
            st.write("🔄 Acessando TISS...")
            try:
                btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
                driver.execute_script("arguments[0].click();", btn_tiss)
            except Exception:
                elems = driver.find_elements(By.XPATH, "//*[contains(translate(., 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'TISS')]")
                if elems:
                    driver.execute_script("arguments[0].click();", elems[0])
                else:
                    raise RuntimeError("Não foi possível localizar AMHPTISS/TISS.")
            time.sleep(wait_time_main)
            if len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])

            # 3) Limpeza
            st.write("🧹 Limpando tela...")
            try:
                driver.execute_script("""
                    const avisos = document.querySelectorAll('center, #fechar-informativo, .modal');
                    avisos.forEach(el => el.remove());
                """)
            except Exception:
                pass

            # 4) Navegação
            st.write("📂 Abrindo Atendimentos...")
            driver.execute_script("document.getElementById('IrPara').click();")
            time.sleep(2)
            safe_click(driver, (By.XPATH, "//span[normalize-space()='Consultório']"))
            safe_click(driver, (By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))
            time.sleep(3)

            # 5) Loop de Status
            for status_sel in status_list:
                st.write(f"📝 Filtros → Negociação: **{negociacao}**, Status: **{status_sel}**, Período: **{data_ini}–{data_fim}**")

                # Negociação/Status/Datas
                neg_input  = wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rcbTipoNegociacao_Input")))
                stat_input = wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rcbStatus_Input")))
                driver.execute_script("arguments[0].value = arguments[1];", neg_input, negociacao); neg_input.send_keys(Keys.ENTER)
                driver.execute_script("arguments[0].value = arguments[1];", stat_input, status_sel);  stat_input.send_keys(Keys.ENTER)
                d_ini_el = driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput"); d_ini_el.clear(); d_ini_el.send_keys(data_ini + Keys.TAB)
                d_fim_el = driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput");     d_fim_el.clear(); d_fim_el.send_keys(data_fim + Keys.TAB)

                # Buscar
                btn_buscar = driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input")
                driver.execute_script("arguments[0].click();", btn_buscar)
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))

                # Seleciona e imprime (ReportViewer)
                driver.execute_script("document.getElementById('ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox').click();")
                time.sleep(2)
                driver.execute_script("document.getElementById('ctl00_MainContent_rbtImprimirAtendimentos_input').click();")
                time.sleep(wait_time_main)

                # Iframe do ReportViewer
                if len(driver.find_elements(By.TAG_NAME, "iframe")) > 0:
                    driver.switch_to.frame(0)

                if extraction_mode.startswith("Texto"):
                    # ====== Captura TEXTO direto do ReportViewer ======
                    st.write("🧾 Capturando TEXTO do relatório no ReportViewer...")
                    time.sleep(2)  # pequeno tempo para render do SSRS
                    texto_relatorio, origem = capture_report_text(driver)
                    st.caption(f"Origem do texto capturado: **{origem}**")

                    if not texto_relatorio.strip():
                        st.warning("Não consegui capturar o texto do relatório automaticamente. Tente novamente ou use o expander 'Colar TEXTO'.")
                        try:
                            driver.switch_to.default_content()
                        except Exception:
                            pass
                        continue

                    st.write("📄 Processando TEXTO do relatório...")
                    # Usa o toggle para decidir o parser (ÂNCORAS EXPLÍCITAS)
                    if force_streaming:
                        df_txt = parse_streaming_with_anchors(texto_relatorio)   # <<<<<<<<
                    else:
                        df_txt = parse_relatorio_text_to_atendimentos_df(texto_relatorio, debug_heads=debug_heads)

                    if not df_txt.empty:
                        # Metadados
                        df_txt["Filtro_Negociacao"] = sanitize_value(negociacao)
                        df_txt["Filtro_Status"]     = sanitize_value(status_sel)
                        df_txt["Periodo_Inicio"]    = sanitize_value(data_ini)
                        df_txt["Periodo_Fim"]       = sanitize_value(data_fim)

                        # Total do lote
                        total_br = f"R$ {df_txt['ValorTotal'].apply(to_float_br).sum():,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                        st.info(f"📑 Total do lote (TEXTO): **{total_br}** — {len(df_txt)} linha(s)")

                        # Preview
                        st.dataframe(df_txt[TARGET_COLS], use_container_width=True)

                        # Consolida
                        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df_txt], ignore_index=True)
                        st.write(f"📊 Registros acumulados: {len(st.session_state.db_consolidado)}")
                    else:
                        st.warning("⚠️ Parser de TEXTO não conseguiu extrair linhas. Use o expander para colar manualmente ou troque para PDF.")

                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass

                else:
                    # ====== Fluxo legado: Exportar PDF ======
                    dropdown = wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
                    Select(dropdown).select_by_value("PDF")
                    time.sleep(2)
                    export_btn = driver.find_element(By.ID, "ReportView_ReportToolbar_ExportGr_Export")
                    driver.execute_script("arguments[0].click();", export_btn)

                    st.write("📥 Concluindo download do PDF...")
                    time.sleep(wait_time_download)

                    arquivos = [
                        os.path.join(DOWNLOAD_TEMPORARIO, f)
                        for f in os.listdir(DOWNLOAD_TEMPORARIO)
                        if f.lower().endswith(".pdf")
                    ]

                    if arquivos:
                        recente = max(arquivos, key=os.path.getctime)
                        nome_pdf = (
                            f"Relatorio_{status_sel.replace(' ', '_').replace('/','-')}_"
                            f"{data_ini.replace('/','-')}_a_{data_fim.replace('/','-')}.pdf"
                        )
                        destino_pdf = os.path.join(PASTA_FINAL, nome_pdf)
                        shutil.move(recente, destino_pdf)
                        st.success(f"✅ PDF salvo: {destino_pdf}")

                        st.write("📄 Extraindo Tabela — Atendimentos do PDF (modo textual)...")
                        df_pdf = parse_pdf_to_atendimentos_df(destino_pdf, mode="text", debug=debug_parser)

                        if not df_pdf.empty:
                            df_pdf["Filtro_Negociacao"] = sanitize_value(negociacao)
                            df_pdf["Filtro_Status"]     = sanitize_value(status_sel)
                            df_pdf["Periodo_Inicio"]    = sanitize_value(data_ini)
                            df_pdf["Periodo_Fim"]       = sanitize_value(data_fim)

                            cols_show = TARGET_COLS
                            missing = [c for c in cols_show if c not in df_pdf.columns]
                            if missing:
                                st.warning(f"As colunas {missing} não estavam presentes; exibindo todas as colunas retornadas para inspeção.")
                                st.write("Colunas retornadas:", list(df_pdf.columns))
                                st.dataframe(df_pdf, use_container_width=True)
                            else:
                                st.dataframe(df_pdf[cols_show], use_container_width=True)

                            st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df_pdf], ignore_index=True)
                            st.write(f"📊 Registros acumulados: {len(st.session_state.db_consolidado)}")
                        else:
                            st.warning("⚠️ Modo textual não conseguiu extrair linhas do PDF. Tente o modo TEXTO (sem PDF).")

                        try:
                            driver.switch_to.default_content()
                        except Exception:
                            pass
                    else:
                        st.error("❌ PDF não encontrado após o download. O SSRS pode ter demorado ou bloqueado.")
                        try:
                            driver.switch_to.default_content()
                        except Exception:
                            pass

            status.update(label="✅ Fim do processo!", state="complete")

    except Exception as e:
        st.error(f"Erro detectado: {e}")
        try:
            shot = os.path.join(PASTA_FINAL, "erro_interceptado.png")
            driver.save_screenshot(shot)
            st.image(shot, caption="Screenshot do erro")
        except Exception:
            pass
    finally:
        try:
            driver.quit()
        except Exception:
            pass

# ========= Resultados & Export =========
if not st.session_state.db_consolidado.empty:
    st.divider()
    df_preview = sanitize_df(st.session_state.db_consolidado)
    st.subheader("📊 Base consolidada (temporária)")
    st.dataframe(df_preview, use_container_width=True)

    # CSV
    csv_bytes = df_preview.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button("💾 Baixar Consolidação (CSV)", csv_bytes, file_name="consolidado_amhp.csv", mime="text/csv")

    # Excel
    xlsx_io = io.BytesIO()
    with pd.ExcelWriter(xlsx_io, engine="openpyxl") as writer:
        df_preview.to_excel(writer, index=False, sheet_name="Atendimentos")
    st.download_button(
        "📘 Baixar Consolidação (Excel)",
        xlsx_io.getvalue(),
        file_name="consolidado_amhp.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    if st.button("🗑️ Limpar Banco Temporário"):
        st.session_state.db_consolidado = pd.DataFrame()
        st.rerun()
