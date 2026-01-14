
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

# ========= Regex e Parser de TEXTO (ReportViewer) =========
val_re        = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")  # valor pt-BR
# FLEXÍVEL: aceita data e hora coladas e não exige espaço após a hora
head_re       = re.compile(r"(\d+)\s+(\d+)\s+(\d{2}/\d{2}/\d{4})\s*(\d{2}:\d{2})(.*)")
code_start_re = re.compile(r"\d{3,6}-")
re_total_blk  = re.compile(r"total\s*r\$\s*\d{1,3}(?:\.\d{3})*,\d{2}", re.I)

def _normalize_ws2(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\u00A0", " ")).strip()

# ---------- PRÉ-LIMPEZA ROBUSTA DO TEXTO ----------
def _preclean_report_text(raw: str) -> str:
    """
    - Corta preâmbulo (filtros) e pula o cabeçalho (começa após 'Valor Total').
    - Insere espaços entre tokens colados:
      • dois blocos numéricos longos (Atendimento e NrGuia),
      • Data↔Hora,
      • Hora↔TipoGuia,
      • TipoGuia↔Operadora (p.ex. 'SP/SADTBACEN(104)'),
      • ')'↔Matrícula.
    - Normaliza whitespace.
    """
    if not raw:
        return ""
    txt = raw.replace("\u00A0", " ")
    txt = _ILLEGAL_CTRL_RE.sub("", txt)

    # Inserir espaço entre dois blocos numéricos longos colados
    txt = re.sub(r"(\d{6,})(\d{6,})", r"\1 \2", txt)

    # Espaço entre Data e Hora coladas
    txt = re.sub(r"(\d{2}/\d{2}/\d{4})(\d{2}:\d{2})", r"\1 \2", txt)

    # Espaço entre Hora e próximo token alfabético (Tipo de Guia)
    txt = re.sub(r"(\d{2}:\d{2})(?=[A-Za-zÁ-Úá-úNÇS/])", r"\1 ", txt)

    # Espaço entre Tipo de Guia e Operadora coladas
    txt = re.sub(r"([A-Za-zÁ-Úá-ú/])([A-Z]{2,}\()", r"\1 \2", txt)

    # Espaço entre ')' e matrícula coladas
    txt = re.sub(r"(\))(\d{5,})", r"\1 \2", txt)

    # Corta antes/até o cabeçalho e começa APÓS 'Valor Total'
    m = re.search(r"(Atendimento\s*Nr\.?\s*Guia.*?Valor\s*Total)", txt, flags=re.I|re.S)
    if m:
        txt = txt[m.end():]  # começa logo depois do cabeçalho
    else:
        # fallback: primeira ocorrência de dois números + data + hora
        m2 = re.search(r"\d{6,}\s+\d{6,}\s+\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}", txt)
        if m2:
            txt = txt[m2.start():]

    # Normaliza
    txt = _normalize_ws2(txt)
    return txt

def is_mat_token(t: str) -> bool:
    if re.fullmatch(r"\d{5,}", t):
        return True
    return bool(re.fullmatch(r"[0-9A-Z]{5,}", t))

def split_tipo_operadora(tokens):
    if not tokens:
        return "", [], 0
    t0 = tokens[0].lower()
    if "/" in tokens[0]:
        return tokens[0], tokens[1:], 1
    if t0 == "consulta":
        return tokens[0], tokens[1:], 1
    if t0 == "honorário" and len(tokens) >= 2:
        return " ".join(tokens[:2]), tokens[2:], 2
    if t0 == "não" and len(tokens) >= 3:
        upto = 3
        for i in range(1, min(5, len(tokens))):
            if tokens[i].lower().startswith("atendimento"):
                upto = i+1
                break
        return " ".join(tokens[:upto]), tokens[upto:], upto
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
    """
    Parser principal (antigo):
    0) Pré-limpeza
    1) Split por valor + 1º cabeçalho interno
    2) Fallback streaming por cabeçalhos e último valor
    """
    big = _preclean_report_text(texto or "")
    if not big:
        return pd.DataFrame(columns=TARGET_COLS)
    big = re_total_blk.sub("", big)

    if debug_heads:
        heads_test = list(head_re.finditer(big))
        st.caption(f"🧩 Cabeçalhos detectados (pré-limpeza): {len(heads_test)}")

    # Estratégia 1
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

    # Estratégia 2
    heads = list(head_re.finditer(big))
    if debug_heads:
        st.caption(f"🧩 Cabeçalhos detectados (streaming): {len(heads)}")
    if not heads:
        return pd.DataFrame(columns=TARGET_COLS)

    parsed2 = []
    for idx, m in enumerate(heads):
        start = m.start()
        end   = heads[idx+1].start() if (idx + 1) < len(heads) else len(big)
        segment = big[start:end].strip()

        vals = list(val_re.finditer(segment))
        if not vals:
            ext_end = min(len(big), end + max(200, int(0.1 * len(segment))))
            segment_ext = big[start:ext_end]
            vals = list(val_re.finditer(segment_ext))
            if not vals:
                continue
            val_end_idx = vals[-1].end()
            rec = segment_ext[:val_end_idx]
        else:
            val_end_idx = vals[-1].end()
            rec = segment[:val_end_idx]

        row = parse_record_text(rec)
        if row:
            parsed2.append(row)

    out2 = pd.DataFrame(parsed2)
    if not out2.empty:
        try:
            out2["Realizacao_dt"] = pd.to_datetime(out2["Realizacao"], format="%d/%m/%Y", errors="coerce")
            out2 = out2.sort_values(["Realizacao_dt","Hora"]).drop(columns=["Realizacao_dt"])
        except Exception:
            pass
        return ensure_atendimentos_schema(sanitize_df(out2))

    return pd.DataFrame(columns=TARGET_COLS)

def to_float_br(s):
    try:
        return float(str(s).replace('.', '').replace(',', '.'))
    except Exception:
        return 0.0

# ========= NOVO PARSER UNIFICADO (streaming) =========
# Detecta cabeçalho colado ou separado automaticamente.
_HDR_PAT_COLADO   = re.compile(r"(\d{10,})\s*(\d{2}/\d{2}/\d{4})\s*(\d{2}:\d{2})")
_HDR_PAT_SEPARADO = re.compile(r"(\d{6,})\s+(\d{6,})\s+(\d{2}/\d{2}/\d{4})\s*(\d{2}:\d{2})")
_VAL_RE_UNI       = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")
_CODE_START_UNI   = re.compile(r"\d{3,6}-")

def split_tipo_operadora_any(tokens):
    """Versão simples para separar TipoGuia e Operadora quando colados."""
    if not tokens:
        return "", [], 0
    t0 = tokens[0].lower()
    if "/" in tokens[0]:
        return tokens[0], tokens[1:], 1
    if t0 in ("consulta", "sp/sadt", "honorário", "não", "não tiss", "não tiss - atendimento"):
        return tokens[0], tokens[1:], 1
    return tokens[0], tokens[1:], 1

def parse_streaming_any(texto: str) -> pd.DataFrame:
    """
    Parser unificado: detecta cabeçalho colado (bloco numérico único + data + hora)
    ou separado (Atendimento NrGuia + data + hora).
    Retorna DataFrame com schema padronizado (TARGET_COLS).
    """
    if not texto or not str(texto).strip():
        return pd.DataFrame(columns=TARGET_COLS)

    s = texto.replace("\u00A0", " ")
    s = re.sub(r"\s+", " ", s).strip()

    m_colado = list(_HDR_PAT_COLADO.finditer(s))
    m_sep    = list(_HDR_PAT_SEPARADO.finditer(s))
    matches    = m_colado if len(m_colado) >= len(m_sep) else m_sep
    use_colado = (matches is m_colado)

    rows = []
    for i, m in enumerate(matches):
        start = m.start()
        end   = matches[i+1].start() if (i+1) < len(matches) else len(s)
        seg   = s[start:end]

        vals  = list(_VAL_RE_UNI.finditer(seg))
        if not vals:
            seg_ext = s[start:min(len(s), end + 40)]
            vals    = list(_VAL_RE_UNI.finditer(seg_ext))
            if not vals:
                continue
            last_val   = vals[-1].group(0)
            val_start  = vals[-1].start()
            working    = seg_ext[:val_start].strip()
        else:
            last_val   = vals[-1].group(0)
            val_start  = vals[-1].start()
            working    = seg[:val_start].strip()

        if use_colado:
            digits_block, data, hora = m.groups()
            if len(digits_block) <= 8:
                atendimento = digits_block
                nr_guia     = ""
            else:
                atendimento = digits_block[:-8]
                nr_guia     = digits_block[-8:]
        else:
            atendimento, nr_guia, data, hora = m.groups()

        hpos = working.find(hora)
        rest = working[hpos + len(hora):].strip()
        rest = re.sub(r"(Consulta|SP/SADT)(?=[A-Z]{2,}\()", r"\1 ", rest)

        body  = rest
        codes = list(_CODE_START_UNI.finditer(body))
        if len(codes) >= 2:
            i1, i2 = codes[-2].start(), codes[-1].start()
            prest  = body[i2:].strip()
            cred   = body[i1:i2].strip()
            core   = body[:i1].strip()
        elif len(codes) == 1:
            i2     = codes[-1].start()
            prest  = body[i2:].strip()
            cred   = ""
            core   = body[:i2].strip()
        else:
            prest  = ""
            cred   = ""
            core   = body

        toks = core.split()
        tipo, tail, _ = split_tipo_operadora_any(toks)

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
        df = df.sort_values(["Realizacao_dt", "Hora"]).drop(columns=["Realizacao_dt"])
    except Exception:
        pass
    return ensure_atendimentos_schema(sanitize_df(df))

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

# ========= Captura robusta de TEXTO no ReportViewer =========
def capture_report_text(driver):
    """
    Tenta extrair texto do ReportViewer em diferentes renderizações SSRS.
    Retorna (texto, origem) para debug.
    """
    # 1) Container padrão do SSRS moderno
    try:
        el = driver.find_element(By.ID, "VisibleReportContent")
        txt = el.text.strip()
        if len(txt) > 50:
            return txt, "#VisibleReportContent.text"
        # textContent via JS (pega textos invisíveis por CSS)
        txt2 = driver.execute_script("return arguments[0].textContent;", el) or ""
        txt2 = txt2.strip()
        if len(txt2) > 50:
            return txt2, "#VisibleReportContent.textContent"
    except Exception:
        pass

    # 2) Containers típicos
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

    # 3) Concatena todas as células em containers conhecidos
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

    # 4) Fallbacks do body
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

# ========= PDF → Tabela (coordenadas + textual reforçado) =========
def parse_pdf_to_atendimentos_df(pdf_path: str, mode: str = "coord", debug: bool = False) -> pd.DataFrame:
    """
    mode: "coord" (coordenadas) | "text" (fallback textual reforçado)
    Sempre aplica ensure_atendimentos_schema() antes de retornar.
    """
    import pdfplumber
    from PyPDF2 import PdfReader

    # Tolerâncias (apenas para 'coord')
    TOP_TOL      = 4.5
    MERGE_GAP_X  = 10.0
    COL_MARGIN   = 4.0

    # Regex comuns (locais ao parser PDF)
    val_re_local        = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")
    val_line_re         = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}$")
    code_start_re_local = re.compile(r"\d{3,6}-")
    re_total_blk_local  = re.compile(r"total\s*r\$\s*\d{1,3}(?:\.\d{3})*,\d{2}", re.I)
    head_re_local       = re.compile(r"(\d+)\s+(\d+)\s+(\d{2}/\d{2}/\d{4})\s*(\d{2}:\d{2})(.*)")

    def _normalize_ws_local(s: str) -> str:
        return re.sub(r"\s+", " ", s.replace("\u00A0", " ")).strip()

    # ---------- Coordenadas ----------
    def parse_by_coords() -> pd.DataFrame:
        all_records = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page_i, page in enumerate(pdf.pages, start=1):
                    words = page.extract_words(
                        use_text_flow=True,
                        extra_attrs=["x0","x1","top","bottom"]
                    )
                    if not words:
                        continue

                    # Cabeçalho: localizar banda com "Atendimento" e "Valor Total"
                    header_y = None
                    header_words = []
                    for w in words:
                        if "Atendimento" in w["text"]:
                            y_top = w["top"]
                            band = [ww for ww in words if abs(ww["top"] - y_top) <= TOP_TOL]
                            band_text = " ".join([b["text"] for b in band])
                            if ("Valor" in band_text) and ("Total" in band_text):
                                header_y = y_top
                                header_words = sorted(band, key=lambda z: z["x0"])
                                break

                    # Fallback extract_tables
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

                    # Blocos do cabeçalho
                    blocks, cur = [], [header_words[0]]
                    for w in header_words[1:]:
                        if (w["x0"] - cur[-1]["x1"]) <= MERGE_GAP_X:
                            cur.append(w)
                        else:
                            blocks.append(cur); cur = [w]
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
                        continue

                    # Palavras de dados; corta "Total"
                    data_words = [w for w in words if w["top"] > header_y + TOP_TOL]
                    total_candidates = [w for w in data_words if w["text"].lower() == "total"]
                    if total_candidates:
                        total_y = total_candidates[0]["top"]
                        data_words = [w for w in data_words if w["top"] < total_y - TOP_TOL]

                    # Bandas (linhas)
                    rows, band, last_top = [], [], None
                    for w in sorted(data_words, key=lambda z: (round(z["top"], 1), z["x0"])):
                        if (last_top is None) or (abs(w["top"] - last_top) <= TOP_TOL):
                            band.append(w); last_top = w["top"]
                        else:
                            rows.append(band); band = [w]; last_top = w["top"]
                    if band: rows.append(band)

                    # Atribuição por centro/interseção
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
                                    if intersects:
                                        cname = c["name"]; break
                            if cname is None:
                                continue
                            bucket[cname].append(w)

                        cols_text = {k: " ".join([ww["text"] for ww in sorted(v, key=lambda z: z["x0"])]) for k, v in bucket.items()}
                        if not cols_text.get("ValorTotal") or not val_line_re.search(cols_text["ValorTotal"]):
                            continue

                        # Ajuste Credenciado/Prestador
                        tail = _normalize_ws_local(" ".join([cols_text.get("Beneficiario",""), cols_text.get("Credenciado",""), cols_text.get("Prestador","")]))
                        starts = [m.start() for m in code_start_re_local.finditer(tail)]
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

    # ---------- Texto (fallback textual reforçado) ----------
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

                toks = rest.split()
                idx_mat = None
                for j, t in enumerate(toks):
                    if re.fullmatch(r"\d+", t):
                        idx_mat = j; break
                if idx_mat is None:
                    for j, t in enumerate(toks):
                        if re.fullmatch(r"\d{6,}", t):
                            idx_mat = j; break

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

    # Seleção de modo
    if mode == "text":
        return sanitize_df(parse_by_text())

    out = parse_by_coords()
    if out.empty:
        if debug: st.warning("Nenhuma linha por coordenadas — aplicando fallback textual.")
        out = parse_by_text()
    return sanitize_df(out)

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

    # 🔘 NOVO TOGGLE — Forçar parser streaming unificado
    force_streaming    = st.toggle("⚙️ Forçar parser streaming (texto colado/sem espaços)", value=True)
    st.caption("Ligado: usa o parser unificado `parse_streaming_any(texto)`.\nDesligado: usa o parser atual `parse_relatorio_text_to_atendimentos_df(texto)`.")

# ========= (Opcional) Processar TEXTO manualmente =========
with st.expander("🧪 Colar TEXTO do relatório (sem automação)", expanded=False):
    texto_manual = st.text_area("📋 Cole aqui o texto completo do ReportViewer:", height=250)
    if st.button("Processar TEXTO (manual)"):
        if not texto_manual.strip():
            st.warning("Cole o texto do relatório e tente novamente.")
        else:
            # 🔁 Usa o toggle para decidir o parser
            if force_streaming:
                df_txt = parse_streaming_any(texto_manual)
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
                    # 🔁 Usa o toggle para decidir o parser
                    if force_streaming:
                        df_txt = parse_streaming_any(texto_relatorio)
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
                        cols_show = TARGET_COLS
                        st.dataframe(df_txt[cols_show], use_container_width=True)

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
                    Select(dropdown).select_by_value("PDF")  # Se quiser CSV: "CSV"
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
