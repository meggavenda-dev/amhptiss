
# -*- coding: utf-8 -*-
"""
AMHP - Exportador PDF + Consolidador + Tratamento CSV legado

Fluxo:
1) Automatiza login e navegação no AMHPTISS.
2) Exporta relatório de Atendimentos (PDF preferencial; CSV se necessário).
3) Lê PDF/CSV aplicando a mesma lógica da “Tabela — Atendimentos”.
4) Sanitiza, acrescenta metadados (Status/Negociação/Período) e consolida em memória.
5) Permite baixar um CSV consolidado com múltiplos Status/Períodos.
6) Uploader opcional para tratar CSVs legados “tortos”.

Secrets (Streamlit):
[credentials]
usuario = "SEU_LOGIN_NO_AMHP"
senha   = "SUA_SENHA_NO_AMHP"
[env]
CHROME_BINARY = "/usr/bin/chromium"
CHROMEDRIVER_BINARY = "/usr/bin/chromedriver"
"""

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
    pass  # Execução local sem secrets de env

# ========= CONFIG DA PÁGINA =========
st.set_page_config(page_title="AMHP - Exportador PDF/CSV + Consolidação", layout="wide")
st.title("🏥 Exportador AMHP (PDF/CSV) + Consolidador")

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

# ========= PARSE PDF → Tabela — Atendimentos =========
def parse_pdf_to_atendimentos_df(pdf_path: str) -> pd.DataFrame:
    """
    Extrai a Tabela — Atendimentos de um PDF do AMHPTISS/SSRS.
    1) PyPDF2 (texto) com regex/heurísticas.
    2) Fallback pdfplumber (tabelas com linhas desenhadas).
    Retorna colunas:
       ['Atendimento','NrGuia','Realizacao','Hora','TipoGuia',
        'Operadora','Matricula','Beneficiario','Credenciado','Prestador','ValorTotal']
    """
    val_re = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}$")  # valor pt-BR
    code_name_re = re.compile(r"\d{3,6}-[^\d].+?")      # bloco CODIGO-Nome

    # ---------- 1) PyPDF2 ----------
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(open(pdf_path, "rb"))
        lines = []
        for page in reader.pages:
            txt = page.extract_text() or ""
            txt = txt.replace("\u00A0", " ")
            lines.extend([l.strip() for l in txt.splitlines() if l.strip()])

        # Cabeçalho
        hdr_idx = -1
        for i, l in enumerate(lines):
            if ("Atendimento" in l) and ("Valor" in l) and ("Total" in l):
                hdr_idx = i
                break
        if hdr_idx == -1:
            hdr_idx = 0

        # Linhas de dados até "Total R$ ..."
        data_lines = []
        for l in lines[hdr_idx+1:]:
            if l.startswith("Total "):
                break
            data_lines.append(l)

        parsed_rows = []
        for l in data_lines:
            m_val = val_re.search(l)
            if not m_val:
                continue
            valor = m_val.group(0)
            body  = l[:m_val.start()].strip()

            code_names = list(code_name_re.finditer(body))
            prestador  = code_names[-1].group(0) if code_names else ""
            if code_names:
                body = body[:code_names[-1].start()].strip()
            credenciado = code_names[-2].group(0) if len(code_names) >= 2 else ""
            if len(code_names) >= 2:
                body = body[:code_names[-2].start()].strip()

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
                "Credenciado": credenciado,
                "Prestador": prestador,
                "ValorTotal": valor,
            })

        df = pd.DataFrame(parsed_rows)
        if not df.empty:
            try:
                df["Realizacao_dt"] = pd.to_datetime(df["Realizacao"], format="%d/%m/%Y")
                df = df.sort_values(["Realizacao_dt","Hora"]).drop(columns=["Realizacao_dt"])
            except Exception:
                pass
            return sanitize_df(df)

    except Exception:
        pass  # fallback pdfplumber

    # ---------- 2) pdfplumber ----------
    try:
        import pdfplumber
        tables_concat = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tbls = page.extract_tables()
                for tbl in tbls:
                    if tbl and len(tbl) > 1:
                        tables_concat.append(pd.DataFrame(tbl))
        if tables_concat:
            df_temp = pd.concat(tables_concat, ignore_index=True)
            header_idx = -1
            for i, row in df_temp.iterrows():
                row_str = " ".join([str(v).replace("\u00A0", " ") for v in row.values])
                if ("Atendimento" in row_str) and ("Valor" in row_str) and ("Total" in row_str):
                    header_idx = i
                    break
            if header_idx == -1:
                header_idx = 0

            df = df_temp.iloc[header_idx+1:].copy()
            header = df_temp.iloc[header_idx].astype(str).tolist()
            df.columns = header[:len(df.columns)]

            def pick(df_cols, candidates):
                for c in candidates:
                    if c in df_cols: return c
                return None

            col_map = {
                "Atendimento": pick(df.columns, ["Atendimento","Nr Atendimento","Nº Atendimento"]),
                "NrGuia": pick(df.columns, ["Nr. Guia","Nr Guia","Nº Guia","Nº da Guia Operadora"]),
                "Realizacao": pick(df.columns, ["Realização","Realizacao","Data"]),
                "Hora": pick(df.columns, ["Hora"]),
                "TipoGuia": pick(df.columns, ["Tipo de Guia","Tipo de Guia:","Tipo"]),
                "Operadora": pick(df.columns, ["Operadora"]),
                "Matricula": pick(df.columns, ["Matrícula","Matricula"]),
                "Beneficiario": pick(df.columns, ["Beneficiário","Beneficiario","Nome do Beneficiário"]),
                "Credenciado": pick(df.columns, ["Credenciado","Prestador Credenciado"]),
                "Prestador": pick(df.columns, ["Prestador"]),
                "ValorTotal": pick(df.columns, ["Valor Total","Valor","Total"]),
            }

            rows_norm = []
            for _, r in df.iterrows():
                rows_norm.append({
                    "Atendimento": str(r.get(col_map["Atendimento"], "")).strip(),
                    "NrGuia": str(r.get(col_map["NrGuia"], "")).strip(),
                    "Realizacao": str(r.get(col_map["Realizacao"], "")).strip(),
                    "Hora": str(r.get(col_map["Hora"], "")).strip(),
                    "TipoGuia": str(r.get(col_map["TipoGuia"], "")).strip(),
                    "Operadora": str(r.get(col_map["Operadora"], "")).strip(),
                    "Matricula": str(r.get(col_map["Matricula"], "")).strip(),
                    "Beneficiario": str(r.get(col_map["Beneficiario"], "")).strip(),
                    "Credenciado": str(r.get(col_map["Credenciado"], "")).strip(),
                    "Prestador": str(r.get(col_map["Prestador"], "")).strip(),
                    "ValorTotal": str(r.get(col_map["ValorTotal"], "")).strip(),
                })

            df_out = pd.DataFrame(rows_norm)
            return sanitize_df(df_out)

    except Exception:
        pass

    return pd.DataFrame(columns=[
        "Atendimento","NrGuia","Realizacao","Hora","TipoGuia","Operadora",
        "Matricula","Beneficiario","Credenciado","Prestador","ValorTotal"
    ])

# ========= TRATAR CSV legado → Tabela — Atendimentos =========
def fix_messy_export_to_atendimentos_df(csv_path_or_str) -> pd.DataFrame:
    """
    Normaliza um CSV "bagunçado" (linhas coladas em 'Atendimento', quebras indevidas,
    'Total R$ ...' colado etc.) para o esquema:
      ['Atendimento','NrGuia','Realizacao','Hora','TipoGuia','Operadora',
       'Matricula','Beneficiario','Credenciado','Prestador','ValorTotal']
    """
    # Carrega texto bruto (path ou conteúdo)
    if "\n" in str(csv_path_or_str) and not str(csv_path_or_str).lower().endswith(".csv"):
        raw = csv_path_or_str
    else:
        with open(csv_path_or_str, "r", encoding="utf-8") as f:
            raw = f.read()

    df = pd.read_csv(io.StringIO(raw), dtype=str, keep_default_na=False)

    val_any    = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")         # valor "pt-BR"
    re_total   = re.compile(r"Total\s*R\$\s*\d{1,3}(?:\.\d{3})*,\d{2}", re.I)
    code_start = re.compile(r"\d{3,6}-")                          # início "CODIGO-"

    def norm(s):
        if s is None: return ""
        return (str(s)
                .replace("\u00A0", " ")
                .replace("\r", " ")
                .replace("\n", " ")
                .replace("\t", " ")
                .strip())

    records, buf = [], ""
    for _, row in df.iterrows():
        at  = norm(row.get("Atendimento"))
        ng  = norm(row.get("NrGuia"))
        dt  = norm(row.get("Realizacao"))
        hr  = norm(row.get("Hora"))
        tg  = norm(row.get("TipoGuia"))
        op  = norm(row.get("Operadora"))
        mat = norm(row.get("Matricula"))
        ben = norm(row.get("Beneficiario"))
        cre = norm(row.get("Credenciado"))
        pre = norm(row.get("Prestador"))
        vl  = norm(row.get("ValorTotal"))

        if at:
            at = re_total.sub("", at).strip()  # remove "Total R$ ..." colado

        if at.lower().startswith("total "):
            continue

        columnar    = all([at, ng, dt, hr, tg]) and bool(vl)
        concat_like = (at and not ng and not dt and not hr and not tg
                       and not op and not mat and not ben and not cre and not pre and not vl)

        if columnar:
            line = f"{at} {ng} {dt} {hr} {tg} {op} {mat} {ben} {cre} {pre} {vl}"
            line = re_total.sub("", line).strip()
            records.append(re.sub(r"\s+", " ", line).strip())
            continue

        if concat_like:
            buf = (buf + " " + at).strip() if buf else at
            if val_any.search(at):  # final da linha (tem valor)
                rec = re_total.sub("", buf).strip()
                records.append(re.sub(r"\s+", " ", rec).strip())
                buf = ""
            continue

    if buf and val_any.search(buf):
        rec = re_total.sub("", buf).strip()
        records.append(re.sub(r"\s+", " ", rec).strip())
        buf = ""

    # Converte "linha única" em colunas finais
    parsed = []
    for l in records:
        vals = list(val_any.finditer(l))
        if not vals:
            continue
        m_val = vals[-1]
        valor = m_val.group(0)
        body  = (l[:m_val.start()] + l[m_val.end():]).strip()

        # última e penúltima ocorrências "CODIGO-" → Prestador/Credenciado
        starts = [m.start() for m in code_start.finditer(body)]
        if starts:
            if len(starts) >= 2:
                i1, i2 = starts[-2], starts[-1]
                prest   = body[i2:].strip()
                tmp     = body[:i2].strip()
                cred    = tmp[i1:].strip()
                body    = tmp[:i1].strip()
            else:
                i2      = starts[-1]
                prest   = body[i2:].strip()
                cred    = ""
                body    = body[:i2].strip()
        else:
            cred = prest = ""

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

    df_out = pd.DataFrame(parsed)
    if not df_out.empty:
        try:
            df_out["Realizacao_dt"] = pd.to_datetime(df_out["Realizacao"], format="%d/%m/%Y")
            df_out = df_out.sort_values(["Realizacao_dt","Hora"]).drop(columns=["Realizacao_dt"])
        except Exception:
            pass
    return sanitize_df(df_out)

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

    st.divider()
    st.subheader("🧩 Tratar CSV legado (opcional)")
    csv_file = st.file_uploader("Arraste um CSV legado (.csv) do AMHP/SSRS", type=["csv"])
    if csv_file:
        if st.button("🔧 Corrigir CSV legado e consolidar"):
            try:
                raw_content = csv_file.getvalue().decode("utf-8", errors="ignore")
                df_corr = fix_messy_export_to_atendimentos_df(raw_content)
                if df_corr.empty:
                    st.warning("Não foi possível extrair linhas do CSV. Verifique o conteúdo.")
                else:
                    # metadados opcionais: usa os do painel
                    df_corr["Filtro_Negociacao"] = sanitize_value(negociacao)
                    df_corr["Filtro_Status"]     = "CSV Legado"
                    df_corr["Periodo_Inicio"]    = sanitize_value(data_ini)
                    df_corr["Periodo_Fim"]       = sanitize_value(data_fim)

                    st.session_state.db_consolidado = pd.concat(
                        [st.session_state.db_consolidado, df_corr],
                        ignore_index=True
                    )
                    st.success(f"✅ {len(df_corr)} linhas tratadas e adicionadas à consolidação!")
            except Exception as e:
                st.error(f"Falha ao tratar CSV: {e}")

# ========= BOTÃO PRINCIPAL (Automação) =========
if st.button("🚀 Iniciar Processo (PDF/CSV)"):
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
                elems = driver.find_elements(By.XPATH, "//*[contains(translate(., 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'TISS')]")
                if elems:
                    driver.execute_script("arguments[0].click();", elems[0])
                else:
                    raise RuntimeError("Não foi possível localizar botão/link AMHPTISS/TISS.")
            time.sleep(wait_time_main)
            if len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])

            # 3. LIMPEZA DE BLOQUEIOS
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
                driver.execute_script("document.getElementById('ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox').click();")
                time.sleep(2)

                # Imprimir (abre ReportViewer)
                driver.execute_script("document.getElementById('ctl00_MainContent_rbtImprimirAtendimentos_input').click();")
                time.sleep(wait_time_main)

                # Iframe do ReportViewer
                if len(driver.find_elements(By.TAG_NAME, "iframe")) > 0:
                    driver.switch_to.frame(0)

                # Exportar (PDF preferido; CSV opcional)
                dropdown = wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
                # Se preferir CSV em algum cenário, troque para "CSV"
                Select(dropdown).select_by_value("PDF")
                time.sleep(2)
                export_btn = driver.find_element(By.ID, "ReportView_ReportToolbar_ExportGr_Export")
                driver.execute_script("arguments[0].click();", export_btn)

                st.write("📥 Concluindo download...")
                time.sleep(wait_time_download)

                # PROCESSA ARQUIVO (PDF/CSV)
                arquivos = [os.path.join(DOWNLOAD_TEMPORARIO, f) for f in os.listdir(DOWNLOAD_TEMPORARIO)
                            if f.lower().endswith((".pdf", ".csv"))]
                if arquivos:
                    recente = max(arquivos, key=os.path.getctime)
                    ext = os.path.splitext(recente)[1].lower()

                    if ext == ".pdf":
                        # Move e processa PDF
                        nome_pdf = f"Relatorio_{status_sel.replace(' ', '_').replace('/','-')}_{data_ini.replace('/','-')}_a_{data_fim.replace('/','-')}.pdf"
                        destino_pdf = os.path.join(PASTA_FINAL, nome_pdf)
                        shutil.move(recente, destino_pdf)
                        st.success(f"✅ PDF salvo: {destino_pdf}")

                        st.write("📄 Lendo dados do PDF...")
                        df_pdf = parse_pdf_to_atendimentos_df(destino_pdf)

                        if not df_pdf.empty:
                            # Metadados de filtro
                            df_pdf["Filtro_Negociacao"] = sanitize_value(negociacao)
                            df_pdf["Filtro_Status"]     = sanitize_value(status_sel)
                            df_pdf["Periodo_Inicio"]    = sanitize_value(data_ini)
                            df_pdf["Periodo_Fim"]       = sanitize_value(data_fim)

                            # Total do PDF atual
                            def to_float_br(s):
                                try: return float(str(s).replace('.', '').replace(',', '.'))
                                except: return 0.0
                            total_pdf = df_pdf["ValorTotal"].apply(to_float_br).sum()
                            total_pdf_br = f"R$ {total_pdf:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                            st.info(f"📑 Total do PDF atual: **{total_pdf_br}**")

                            # Preview
                            cols_show = ["Atendimento","NrGuia","Realizacao","Hora","TipoGuia",
                                         "Operadora","Matricula","Beneficiario","Credenciado",
                                         "Prestador","ValorTotal"]
                            st.dataframe(df_pdf[cols_show], use_container_width=True)

                            # Consolida
                            st.session_state.db_consolidado = pd.concat(
                                [st.session_state.db_consolidado, df_pdf],
                                ignore_index=True
                            )
                            st.write(f"📊 Registros acumulados: {len(st.session_state.db_consolidado)}")
                        else:
                            st.warning("⚠️ Não foi possível extrair linhas do PDF. Verifique o arquivo salvo.")

                        try:
                            driver.switch_to.default_content()
                        except Exception:
                            pass

                    elif ext == ".csv":
                        # Move e trata CSV legado
                        nome_csv = f"Relatorio_{status_sel.replace(' ', '_').replace('/','-')}_{data_ini.replace('/','-')}_a_{data_fim.replace('/','-')}.csv"
                        destino_csv = os.path.join(PASTA_FINAL, nome_csv)
                        shutil.move(recente, destino_csv)
                        st.success(f"✅ CSV salvo: {destino_csv}")

                        st.write("📄 Tratando CSV legado e normalizando...")
                        df_csv = fix_messy_export_to_atendimentos_df(destino_csv)

                        if not df_csv.empty:
                            df_csv["Filtro_Negociacao"] = sanitize_value(negociacao)
                            df_csv["Filtro_Status"]     = sanitize_value(status_sel)
                            df_csv["Periodo_Inicio"]    = sanitize_value(data_ini)
                            df_csv["Periodo_Fim"]       = sanitize_value(data_fim)

                            # Preview
                            cols_show = ["Atendimento","NrGuia","Realizacao","Hora","TipoGuia",
                                         "Operadora","Matricula","Beneficiario","Credenciado",
                                         "Prestador","ValorTotal"]
                            st.dataframe(df_csv[cols_show], use_container_width=True)

                            st.session_state.db_consolidado = pd.concat(
                                [st.session_state.db_consolidado, df_csv],
                                ignore_index=True
                            )
                            st.write(f"📊 Registros acumulados: {len(st.session_state.db_consolidado)}")
                        else:
                            st.warning("⚠️ CSV não pôde ser normalizado. Verifique o conteúdo.")

                        try:
                            driver.switch_to.default_content()
                        except Exception:
                            pass
                else:
                    st.error("❌ Arquivo não encontrado após o download. O SSRS pode ter demorado ou bloqueado.")
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
