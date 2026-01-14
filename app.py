
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

# ========= PDF → Tabela (coordenadas + fallback textual) =========
def parse_pdf_to_atendimentos_df(pdf_path: str, mode: str = "coord", debug: bool = False) -> pd.DataFrame:
    """
    mode: "coord" (coordenadas, padrão) | "text" (fallback textual forçado)
    Sempre aplica ensure_atendimentos_schema() antes de retornar.
    """
    import pdfplumber
    from PyPDF2 import PdfReader

    # Tolerâncias
    TOP_TOL      = 4.5     # ↑ um pouco para PDFs do SSRS
    MERGE_GAP_X  = 10.0
    COL_MARGIN   = 4.0

    val_re        = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}$")
    code_start_re = re.compile(r"\d{3,6}-")

    def parse_by_coords() -> pd.DataFrame:
        all_records = []
        with pdfplumber.open(pdf_path) as pdf:
            for page_i, page in enumerate(pdf.pages, start=1):
                words = page.extract_words(
                    use_text_flow=True,
                    extra_attrs=["x0","x1","top","bottom"]
                )
                if not words:
                    continue

                # 1) Cabeçalho
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
                            if debug:
                                st.info(f"[coord] extract_tables usado na página {page_i}.")
                    continue

                # 2) Blocos do cabeçalho
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

                def map_block(txt):
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
                    if debug: st.warning("Nenhuma coluna mapeada a partir do cabeçalho.")
                    continue

                if debug:
                    st.info(f"[coord] Colunas detectadas (página {page_i}): {[c['name'] for c in columns]}")

                # 3) Palavras de dados; corta 'Total'
                data_words = [w for w in words if w["top"] > header_y + TOP_TOL]
                total_candidates = [w for w in data_words if w["text"].lower() == "total"]
                if total_candidates:
                    total_y = total_candidates[0]["top"]
                    data_words = [w for w in data_words if w["top"] < total_y - TOP_TOL]

                # 4) Bandas (linhas)
                rows = []
                band = []
                last_top = None
                for w in sorted(data_words, key=lambda z: (round(z["top"], 1), z["x0"])):
                    if (last_top is None) or (abs(w["top"] - last_top) <= TOP_TOL):
                        band.append(w); last_top = w["top"]
                    else:
                        rows.append(band); band = [w]; last_top = w["top"]
                if band: rows.append(band)

                # 5) Atribuição de palavras à coluna por centro mais próximo (mais robusto)
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
                        # extra: se estiver muito fora (nenhuma coluna “próxima”), usa interseção com margem
                        if cname is None:
                            for c in columns:
                                intersects = not (w["x1"] < (c["x0"] - COL_MARGIN) or w["x0"] > (c["x1"] + COL_MARGIN))
                                if intersects:
                                    cname = c["name"]; break
                        if cname is None:  # se ainda não caiu em nenhuma, ignora
                            continue
                        bucket[cname].append(w)

                    cols_text = {k: " ".join([ww["text"] for ww in sorted(v, key=lambda z: z["x0"])]) for k, v in bucket.items()}

                    # precisa ter ValorTotal válido
                    if not cols_text.get("ValorTotal") or not val_re.search(cols_text["ValorTotal"]):
                        continue

                    # Ajuste Credenciado/Prestador por padrão CODIGO-Nome
                    tail = " ".join([cols_text.get("Beneficiario",""), cols_text.get("Credenciado",""), cols_text.get("Prestador","")]).strip()
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

                if debug and all_records:
                    st.caption(f"[coord] Amostra da última linha (página {page_i}):")
                    st.write(all_records[-1])

        out = pd.DataFrame(all_records)
        if not out.empty:
            try:
                out["Realizacao_dt"] = pd.to_datetime(out["Realizacao"], format="%d/%m/%Y", errors="coerce")
                out = out.sort_values(["Realizacao_dt","Hora"]).drop(columns=["Realizacao_dt"])
            except Exception:
                pass
        return ensure_atendimentos_schema(out)

    def parse_by_text() -> pd.DataFrame:
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
        return ensure_atendimentos_schema(out)

    # Seleção de modo
    if mode == "text":
        return sanitize_df(parse_by_text())

    # Tenta coordenadas; se vazio, força fallback textual
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
    wait_time_download = st.number_input("⏱️ Tempo extra para concluir download (s)", min_value=10, value=18)
    extraction_mode    = st.selectbox("🧠 Modo de extração do PDF", ["Coordenadas (recomendado)", "Texto (fallback)"])
    debug_parser       = st.checkbox("🧪 Debug do parser PDF", value=False)

# ========= (Opcional) Processar PDF manualmente =========
with st.expander("🧪 Testar parser com upload de PDF (sem automação)", expanded=False):
    up = st.file_uploader("Envie um PDF do AMHPTISS para teste", type=["pdf"])
    if up and st.button("Processar PDF (teste)"):
        tmp_pdf = os.path.join(DOWNLOAD_TEMPORARIO, "teste_upload.pdf")
        with open(tmp_pdf, "wb") as f:
            f.write(up.getvalue())
        mode = "text" if extraction_mode.startswith("Texto") else "coord"
        df_test = parse_pdf_to_atendimentos_df(tmp_pdf, mode=mode, debug=debug_parser)
        if df_test.empty:
            st.error("Parser não conseguiu extrair linhas deste PDF. Ajuste tolerâncias ou use o outro modo.")
        else:
            st.success(f"{len(df_test)} linha(s) extraída(s).")
            st.dataframe(df_test, use_container_width=True)

# ========= Botão principal =========
if st.button("🚀 Iniciar Processo (PDF)"):
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

                # Exportar sempre em PDF
                dropdown = wait.until(EC.presence_of_element_located((By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
                Select(dropdown).select_by_value("PDF")
                time.sleep(2)
                export_btn = driver.find_element(By.ID, "ReportView_ReportToolbar_ExportGr_Export")
                driver.execute_script("arguments[0].click();", export_btn)

                st.write("📥 Concluindo download do PDF...")
                time.sleep(wait_time_download)

                # Processa PDF
                arquivos = [os.path.join(DOWNLOAD_TEMPORARIO, f) for f in os.listdir(DOWNLOAD_TEMPORARIO) if f.lower().endswith(".pdf")]
                if arquivos:
                    recente = max(arquivos, key=os.path.getctime)
                    nome_pdf = f"Relatorio_{status_sel.replace(' ', '_').replace('/','-')}_{data_ini.replace('/','-')}_a_{data_fim.replace('/','-')}.pdf"
                    destino_pdf = os.path.join(PASTA_FINAL, nome_pdf)
                    shutil.move(recente, destino_pdf)
                    st.success(f"✅ PDF salvo: {destino_pdf}")

                    st.write("📄 Extraindo Tabela — Atendimentos do PDF...")
                    mode = "text" if extraction_mode.startswith("Texto") else "coord"
                    df_pdf = parse_pdf_to_atendimentos_df(destino_pdf, mode=mode, debug=debug_parser)

                    if not df_pdf.empty:
                        # Metadados
                        df_pdf["Filtro_Negociacao"] = sanitize_value(negociacao)
                        df_pdf["Filtro_Status"]     = sanitize_value(status_sel)
                        df_pdf["Periodo_Inicio"]    = sanitize_value(data_ini)
                        df_pdf["Periodo_Fim"]       = sanitize_value(data_fim)

                        # Guard das colunas
                        cols_show = TARGET_COLS
                        missing = [c for c in cols_show if c not in df_pdf.columns]
                        if missing:
                            st.warning(f"As colunas {missing} não estavam presentes; exibindo todas as colunas retornadas para inspeção.")
                            st.write("Colunas retornadas:", list(df_pdf.columns))
                            st.dataframe(df_pdf, use_container_width=True)
                        else:
                            st.dataframe(df_pdf[cols_show], use_container_width=True)

                        # Consolida
                        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df_pdf], ignore_index=True)
                        st.write(f"📊 Registros acumulados: {len(st.session_state.db_consolidado)}")
                    else:
                        st.warning("⚠️ Não foi possível extrair linhas do PDF. Tente o outro modo de extração e/ou ajuste tolerâncias.")

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

    csv_bytes = df_preview.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button("💾 Baixar Consolidação (CSV)", csv_bytes, file_name="consolidado_amhp.csv", mime="text/csv")

    if st.button("🗑️ Limpar Banco Temporário"):
        st.session_state.db_consolidado = pd.DataFrame()
        st.rerun()
