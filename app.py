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

def validate_df(df: pd.DataFrame):
    issues = []
    for i, r in df.iterrows():
        # Atendimento/NrGuia devem ser 8 dígitos
        if not re.fullmatch(r"\d{8}", str(r["Atendimento"])):
            issues.append((i, "Atendimento inválido"))
        if not re.fullmatch(r"\d{8}", str(r["NrGuia"])):
            issues.append((i, "NrGuia inválido"))

        # Data dd/mm/aaaa
        if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", str(r["Realizacao"])):
            issues.append((i, "Data inválida"))

        # Operadora com sufixo numérico
        if isinstance(r["Operadora"], str) and not re.search(r"\(.+\)$", r["Operadora"]):
            issues.append((i, "Operadora sem código"))

        # ValorTotal no formato brasileiro
        if isinstance(r["ValorTotal"], str) and not re.fullmatch(r"\d{1,3}(\.\d{3})*,\d{2}", r["ValorTotal"]):
            issues.append((i, "ValorTotal inválido"))

    return issues

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

# ========= Parser PDF (textual fallback) =========
def parse_pdf_to_atendimentos_df(pdf_path: str, debug: bool = False) -> pd.DataFrame:
    from PyPDF2 import PdfReader
    import re

    def _normalize_ws(s: str) -> str:
        return re.sub(r"\s+", " ", s.replace("\u00A0", " ")).strip()

    reader = PdfReader(open(pdf_path, "rb"))
    all_lines = []
    for page in reader.pages:
        txt = page.extract_text()
        if txt:
            all_lines.extend(txt.splitlines())

    # remove cabeçalhos/rodapés comuns
    clean_lines = []
    for line in all_lines:
        if "Atendimentos Realizados Sintético" in line: continue
        if "Emitido por" in line and "Página" in line: continue
        clean_lines.append(line.strip())

    big = _normalize_ws(" ".join(clean_lines))

    # início de registro: atendimento(8) guia(8) data
    record_start_re = re.compile(r"(?P<atend>\d{8})\s+(?P<guia>\d{8})\s+(?P<data>\d{2}/\d{2}/\d{4})")
    matches = list(record_start_re.finditer(big))
    parsed = []

    # padrões auxiliares
    hora_re = re.compile(r"\b(\d{2}:\d{2})\b")
    valor_re = re.compile(r"\b(\d{1,3}(?:\.\d{3})*,\d{2})\b")
    tipo_tokens = ["Consulta", "SP/SADT", "Não TISS", "SADT"]
    cod_re = re.compile(r"\b(\d{5,6}-[A-Z0-9].+?)\b")  # código + nome
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
            # heurística: último é prestador, penúltimo é credenciado
            credenciado = codes[-2].group(1).strip()
            prestador   = codes[-1].group(1).strip()
            # remove ambos do miolo
            miolo = miolo.replace(credenciado, " ").replace(prestador, " ")
            miolo = _normalize_ws(miolo)
        elif len(codes) == 1:
            # se só um, assume como credenciado
            credenciado = codes[0].group(1).strip()
            miolo = _normalize_ws(miolo.replace(credenciado, " "))

        # matrícula: bloco alfanumérico médio (evitar capturar códigos)
        mat_m = re.search(r"\b([A-Z0-9]{5,}X?[A-Z0-9/]*)\b", miolo)
        matricula = mat_m.group(1) if mat_m else ""

        # beneficiário: o restante após matrícula
        beneficiario = miolo
        if matricula:
            beneficiario = _normalize_ws(beneficiario.replace(matricula, ""))
        # limpa resíduos óbvios
        beneficiario = re.sub(r"\b(Consulta|SP/SADT|Não TISS|SADT)\b", "", beneficiario).strip()

        parsed.append({
            "Atendimento": atend,
            "NrGuia": guia,
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

    # pós-processamento: correções rápidas
    df = df.apply(fix_row, axis=1)

    if debug:
        import io
        buf = io.StringIO()
        df.head(20).to_string(buf)
        print(buf.getvalue())

    return df


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

        df_test = parse_pdf_to_atendimentos_df(tmp_pdf, debug=debug_parser)

        if df_test.empty:
            st.error("Parser não conseguiu extrair linhas deste PDF usando o modo textual.")
        else:
            issues = validate_df(df_test)
            if issues:
                st.warning(f"Inconsistências detectadas em {len(issues)} linha(s). Ex.: {issues[:5]}")
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

                    # 👇 chamada corrigida, sem 'mode'
                    df_pdf = parse_pdf_to_atendimentos_df(destino_pdf, debug=debug_parser)
                    issues = validate_df(df_pdf)
                    if issues:
                        st.warning(f"Inconsistências detectadas em {len(issues)} linha(s). Ex.: {issues[:5]}")
                        
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
