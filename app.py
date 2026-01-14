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
    "Atendimento","NrGuia","Realizacao","Hora","TipoGuia",
    "Operadora","Matricula","Beneficiario","Credenciado",
    "Prestador","ValorTotal"
]

def ensure_atendimentos_schema(df: pd.DataFrame) -> pd.DataFrame:
    for c in TARGET_COLS:
        if c not in df.columns:
            df[c] = ""
    return df[TARGET_COLS]

# ========= Selenium =========
def configurar_driver():
    opts = Options()
    chrome_binary = os.environ.get("CHROME_BINARY", "/usr/bin/chromium")
    driver_binary = os.environ.get("CHROMEDRIVER_BINARY", "/usr/bin/chromedriver")

    if os.path.exists(chrome_binary):
        opts.binary_location = chrome_binary

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

    if os.path.exists(driver_binary):
        service = Service(executable_path=driver_binary)
        driver = webdriver.Chrome(service=service, options=opts)
    else:
        driver = webdriver.Chrome(options=opts)

    driver.set_page_load_timeout(180)
    driver.set_script_timeout(180)
    return driver

# ⚡ CORREÇÃO: clique seguro com retry + scroll
def js_safe_click(driver, by, value, timeout=30, retries=3):
    for attempt in range(retries):
        try:
            el = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((by, value))
            )
            driver.execute_script("arguments[0].scrollIntoView(true);", el)
            driver.execute_script("arguments[0].click();", el)
            return
        except (TimeoutException, ElementClickInterceptedException):
            time.sleep(1)
            if attempt == retries - 1:
                raise

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

    # Regex comuns
    val_re        = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")
    val_line_re   = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}$")
    code_start_re = re.compile(r"\d{3,6}-")
    re_total_blk  = re.compile(r"total\s*r\$\s*\d{1,3}(?:\.\d{3})*,\d{2}", re.I)
    # Cabeça da linha (agora com SEARCH em vez de MATCH)
    head_re       = re.compile(r"(\d+)\s+(\d+)\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})\s+(.*)")

    def _normalize_ws(s: str) -> str:
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

                    # Atribuição por centro mais próximo / interseção
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

# ========= Sidebar =========
with st.sidebar:
    st.header("Configurações")
    data_ini    = st.text_input("📅 Data Inicial (dd/mm/aaaa)", value="01/01/2026")
    data_fim    = st.text_input("📅 Data Final (dd/mm/aaaa)", value="13/01/2026")
    negociacao  = st.text_input("🤝 Tipo de Negociação", value="Direto")
    credenciado_filter = st.text_input("🏥 Filtrar por Credenciado (opcional)", value="")
    status_list = st.multiselect(
        "📌 Status",
        options=["300 - Pronto para Processamento","200 - Em Análise","100 - Recebido","400 - Processado"],
        default=["300 - Pronto para Processamento"]
    )
    wait_time_main     = st.number_input("⏱️ Tempo extra pós login/troca de tela (s)", min_value=0, value=10)
    wait_time_download = st.number_input("⏱️ Tempo extra para concluir download (s)", min_value=10, value=18)
    extraction_mode    = st.selectbox("🧠 Modo de extração do PDF (visual)", ["Coordenadas (recomendado)", "Texto (fallback)"])
    debug_parser       = st.checkbox("🧪 Debug do parser PDF", value=False)

# ========= PDF Manual =========
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
            js_safe_click(driver, By.XPATH, "//span[normalize-space()='Consultório']")
            js_safe_click(driver, By.XPATH, "//a[@href='AtendimentosRealizados.aspx']")
            time.sleep(3)

            # 5) Loop de Status
            for status_sel in status_list:
                st.write(f"📝 Filtros → Negociação: **{negociacao}**, Status: **{status_sel}**, Credenciado: **{credenciado_filter or 'Todos'}**, Período: **{data_ini}–{data_fim}**")

                neg_input  = wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rcbTipoNegociacao_Input")))
                stat_input = wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContent_rcbStatus_Input")))
                driver.execute_script("arguments[0].value = arguments[1];", neg_input, negociacao); neg_input.send_keys(Keys.ENTER)
                driver.execute_script("arguments[0].value = arguments[1];", stat_input, status_sel); stat_input.send_keys(Keys.ENTER)

                # ⚡ Novo filtro Credenciado
                if credenciado_filter.strip():
                    cred_input = wait.until(
                        EC.presence_of_element_located((By.ID, "ctl00_MainContent_rcbCredenciado_Input"))
                    )
                    driver.execute_script("arguments[0].value = arguments[1];", cred_input, credenciado_filter)
                    cred_input.send_keys(Keys.ENTER)

                d_ini_el = driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput"); d_ini_el.clear(); d_ini_el.send_keys(data_ini + Keys.TAB)
                d_fim_el = driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput"); d_fim_el.clear(); d_fim_el.send_keys(data_fim + Keys.TAB)

                # Buscar
                btn_buscar = driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input")
                driver.execute_script("arguments[0].click();", btn_buscar)
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable")))

                # Seleciona e imprime
                js_safe_click(driver, By.ID, "ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox")
                time.sleep(2)
                js_safe_click(driver, By.ID, "ctl00_MainContent_rbtImprimirAtendimentos_input")
                time.sleep(wait_time_main)

                # Iframe
                if len(driver.find_elements(By.TAG_NAME, "iframe")) > 0:
                    driver.switch_to.frame(0)

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

                    df_pdf = parse_pdf_to_atendimentos_df(destino_pdf, mode="text", debug=debug_parser)
                    if not df_pdf.empty:
                        df_pdf["Filtro_Negociacao"] = sanitize_value(negociacao)
                        df_pdf["Filtro_Status"]     = sanitize_value(status_sel)
                        df_pdf["Filtro_Credenciado"] = sanitize_value(credenciado_filter)
                        df_pdf["Periodo_Inicio"]    = sanitize_value(data_ini)
                        df_pdf["Periodo_Fim"]       = sanitize_value(data_fim)
                        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df_pdf], ignore_index=True)
                        st.dataframe(df_pdf, use_container_width=True)
                    else:
                        st.warning("⚠️ Modo textual não conseguiu extrair linhas.")

                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass
                else:
                    st.error("❌ PDF não encontrado após o download.")
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
    st.download_button("💾 Baixar Consolidação (CSV)", csv_bytes,    file_name="consolidado_amhp.csv",
        mime="text/csv"
    )
