
# -*- coding: utf-8 -*-
"""
AMHP Data Analytics - Consolidador de Relatórios AMHP

Requisitos (no ambiente):
- streamlit
- pandas
- selenium
- xlrd==2.0.1 (para .xls BIFF)
- xlsxwriter (para exportar .xlsx)
- openpyxl (somente para ler .xlsx caso AMHP exporte EXCELOPENXML)
- (Cloud) chromium + chromium-driver + libs

Secrets (Streamlit):
[credentials]
usuario = "SEU_LOGIN_NO_AMHP"
senha   = "SUA_SENHA_NO_AMHP"

Opcional (Cloud):
[env]
CHROME_BINARY = "/usr/bin/chromium"
CHROMEDRIVER_BINARY = "/usr/bin/chromedriver"
"""

import os
import io
import re
import time
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
    NoSuchElementException,
    TimeoutException,
    ElementClickInterceptedException,
    WebDriverException,
)

# =========================================================
# Secrets -> env (Cloud-friendly)
# =========================================================
try:
    chrome_bin_secret = st.secrets.get("env", {}).get("CHROME_BINARY", None)
    driver_bin_secret = st.secrets.get("env", {}).get("CHROMEDRIVER_BINARY", None)
    if chrome_bin_secret:
        os.environ["CHROME_BINARY"] = chrome_bin_secret
    if driver_bin_secret:
        os.environ["CHROMEDRIVER_BINARY"] = driver_bin_secret
except Exception:
    pass  # Execução local sem secrets de env

# =========================================================
# Configuração da Página
# =========================================================
st.set_page_config(page_title="AMHP Data Analytics", layout="wide")
st.title("🏥 Consolidador de Relatórios AMHP")

# Banco em sessão
if "db_consolidado" not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame()

# Diretório temporário
DOWNLOAD_DIR = os.path.join(os.getcwd(), "temp_downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# =========================================================
# Sanitização robusta (remove caracteres de controle ilegais)
# =========================================================
_ILLEGAL_CTRL_RE = re.compile(r"[\x00-\x08\x0B-\x0C\x0E-\x1F]")

def _sanitize_text_for_excel(s: str) -> str:
    s = s.replace("\x00", "")
    s = _ILLEGAL_CTRL_RE.sub("", s)
    # Normaliza NBSP (char comum em SSRS) para espaço comum
    s = s.replace("\u00A0", " ").strip()
    return s

def sanitize_value_for_excel(v):
    if pd.isna(v):
        return v
    if isinstance(v, (bytes, bytearray)):
        try:
            v = v.decode("utf-8", "ignore")
        except Exception:
            v = v.decode("latin-1", "ignore")
    if isinstance(v, str):
        return _sanitize_text_for_excel(v)
    return v  # números/datas ficam intactos

def sanitize_df_for_excel(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # 1) Nomes de colunas saneados + unicidade
    new_cols, seen = [], {}
    for c in df.columns:
        c2 = sanitize_value_for_excel(str(c))
        base = c2
        n = seen.get(base, 0) + 1
        seen[base] = n
        new_cols.append(base if n == 1 else f"{base}_{n}")
    df.columns = new_cols

    # 2) Sanitiza apenas colunas de texto
    obj_cols = df.select_dtypes(include=["object"]).columns
    for col in obj_cols:
        df[col] = df[col].apply(sanitize_value_for_excel)

    return df

def find_illegal_chars_rows(df: pd.DataFrame):
    """Diagnóstico (opcional) de linhas com caracteres de controle ilegais."""
    rows = []
    for col in df.select_dtypes(include=["object"]).columns:
        s = df[col].astype(str)
        bad = s.str.contains(_ILLEGAL_CTRL_RE, regex=True)
        if bad.any():
            idxs = s[bad].index.tolist()[:20]
            rows.append((col, idxs))
    return rows

# =========================================================
# Processamento de .xls (BIFF8) + fallbacks (HTML/CSV disfarçado)
# =========================================================
def processar_xls_amhp(caminho_arquivo, status_nome, neg_nome):
    """Lê arquivos XLS binários gerados pelo AMHP usando xlrd com fallbacks."""
    try:
        import xlrd

        # Tenta BIFF8
        try:
            workbook = xlrd.open_workbook(caminho_arquivo)
            sheet = workbook.sheet_by_index(0)
            dados_brutos = [sheet.row_values(row_idx) for row_idx in range(sheet.nrows)]
            df_temp = pd.DataFrame(dados_brutos)

        except Exception:
            # Header do arquivo para detectar HTML
            with open(caminho_arquivo, "rb") as f:
                head = f.read(4096)
            prefix = head[:64].decode("latin-1", "ignore").lower()

            if "<html" in prefix or "<table" in prefix:
                # HTML (SSRS às vezes exporta .xls com HTML)
                try:
                    tables = pd.read_html(caminho_arquivo, header=None)
                    df_temp = tables[0]
                except Exception:
                    # CSV disfarçado
                    try:
                        df_temp = pd.read_csv(caminho_arquivo, sep=";", header=None, encoding="latin-1")
                    except Exception:
                        df_temp = pd.read_csv(caminho_arquivo, sep=",", header=None, encoding="latin-1")
            else:
                # CSV disfarçado
                try:
                    df_temp = pd.read_csv(caminho_arquivo, sep=";", header=None, encoding="latin-1")
                except Exception:
                    df_temp = pd.read_csv(caminho_arquivo, sep=",", header=None, encoding="latin-1")

        # Localiza dinamicamente o cabeçalho (linha que contém "Atendimento" e "Guia")
        indice_cabecalho = -1
        for i, linha in df_temp.iterrows():
            linha_str = " ".join([str(v).replace("\u00A0", " ") for v in linha.values])
            if "Atendimento" in linha_str and "Guia" in linha_str:
                indice_cabecalho = i
                break
        if indice_cabecalho == -1:
            indice_cabecalho = 0  # fallback

        # Define cabeçalhos e remove lixo
        df = df_temp.iloc[indice_cabecalho + 1:].copy()
        df.columns = df_temp.iloc[indice_cabecalho].astype(str).tolist()
        df = df.loc[:, df.columns.notnull()]
        df = df.dropna(how="all", axis=1).dropna(how="all", axis=0)

        # Sanitização
        df = sanitize_df_for_excel(df)

        # Metadados
        df["Filtro_Status"] = sanitize_value_for_excel(status_nome)
        df["Filtro_Negociacao"] = sanitize_value_for_excel(neg_nome)

        # Concatena ao banco
        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df], ignore_index=True)

        # Opcional: diagnóstico
        offenders = find_illegal_chars_rows(st.session_state.db_consolidado)
        if offenders:
            with st.expander("🚨 Linhas com caracteres ilegais (amostra)", expanded=False):
                for col, idxs in offenders:
                    st.write(f"• Coluna **{col}** – linhas: {idxs}")

        return True

    except Exception as e:
        st.error(f"Erro no processamento do arquivo: {e}")
        return False

# =========================================================
# Helpers Selenium (espera/cliqueresiliente/janelas/iframes/debug)
# =========================================================
def wait_visible(driver, locator, timeout=30):
    return WebDriverWait(driver, timeout).until(EC.visibility_of_element_located(locator))

def wait_clickable(driver, locator, timeout=30):
    return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator))

def safe_click(driver, locator, timeout=30):
    """Tenta clique normal; se interceptado, força via JS."""
    try:
        el = wait_clickable(driver, locator, timeout)
        el.click()
        return el
    except (ElementClickInterceptedException, TimeoutException, WebDriverException):
        el = wait_visible(driver, locator, timeout)
        driver.execute_script("arguments[0].click();", el)
        return el

def wait_new_window_and_switch(driver, prev_handles, timeout=30):
    """Espera nova janela/aba e faz switch com segurança."""
    WebDriverWait(driver, timeout).until(lambda d: len(d.window_handles) > len(prev_handles))
    new_handle = (set(driver.window_handles) - set(prev_handles)).pop()
    driver.switch_to.window(new_handle)
    return new_handle

def switch_to_iframe_safe(driver, timeout=20, iframe_locator=None, index_fallback=0):
    """Tenta localizar iframe por locator (ID/CSS) e cai por índice se necessário."""
    try:
        if iframe_locator:
            iframe_el = WebDriverWait(driver, timeout).until(EC.presence_of_element_located(iframe_locator))
            driver.switch_to.frame(iframe_el)
        else:
            WebDriverWait(driver, timeout).until(lambda d: len(d.find_elements(By.TAG_NAME, "iframe")) > index_fallback)
            driver.switch_to.frame(index_fallback)
    except TimeoutException:
        pass  # se não tiver iframe, segue na página atual

def capture_debug(driver, label="falha"):
    """Salva screenshot e page source para auxiliar debug."""
    try:
        img_path = os.path.join(DOWNLOAD_DIR, f"debug_{label}.png")
        html_path = os.path.join(DOWNLOAD_DIR, f"debug_{label}.html")
        driver.save_screenshot(img_path)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        st.info(f"📸 Screenshot salvo: {img_path}")
        st.info(f"📄 Page source salvo: {html_path}")
        try:
            st.image(img_path, caption=f"Screenshot ({label})", use_column_width=True)
        except Exception:
            pass
    except Exception as e:
        st.warning(f"Não foi possível salvar debug: {e}")
    try:
        logs = driver.get_log("browser")
        if logs:
            with st.expander("📋 Logs do navegador"):
                st.write(logs[:50])
    except Exception:
        pass

# =========================================================
# Configuração do Navegador (Selenium robusto)
# =========================================================
def configurar_driver():
    opts = Options()

    # Paths (Cloud/local) via env
    chrome_binary = os.environ.get("CHROME_BINARY", "/usr/bin/chromium")
    driver_binary = os.environ.get("CHROMEDRIVER_BINARY", "/usr/bin/chromedriver")
    if os.path.exists(chrome_binary):
        opts.binary_location = chrome_binary

    # Headless estável
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")

    # Stealth básico
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0 Safari/537.36"
    )

    # Downloads
    prefs = {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "safebrowsing.enabled": True,
        "profile.default_content_setting_values.automatic_downloads": 1,
    }
    opts.add_experimental_option("prefs", prefs)

    # Service explícito, se existir
    if os.path.exists(driver_binary):
        service = Service(executable_path=driver_binary)
        driver = webdriver.Chrome(service=service, options=opts)
    else:
        driver = webdriver.Chrome(options=opts)

    driver.set_page_load_timeout(60)
    return driver

# =========================================================
# Sidebar (Configurações)
# =========================================================
with st.sidebar:
    st.header("Configurações")
    data_inicio = st.date_input("Data Inicial", value=pd.to_datetime("2026-01-01"))
    data_final  = st.date_input("Data Final",  value=pd.to_datetime("2026-01-13"))

    neg_label    = "Direto"
    status_label = "300 - Pronto para Processamento"

    st.caption("⚠️ Se o site demorar para renderizar, aumente os tempos abaixo.")
    wait_time_main     = st.number_input("Tempo extra pós login/troca de tela (s)", min_value=0, value=8)
    wait_time_download = st.number_input("Tempo extra para concluir download (s)", min_value=10, value=18)

# (Opcional) Botão de teste do navegador
if st.button("🧪 Testar navegador (Selenium)"):
    d = configurar_driver()
    try:
        d.get("https://www.google.com")
        st.success(f"Navegador OK! Título: {d.title}")
        shot_path = os.path.join(DOWNLOAD_DIR, "test_google.png")
        d.save_screenshot(shot_path)
        st.image(shot_path, caption="Screenshot headless", use_column_width=True)
    except Exception as e:
        st.error(f"Falha ao abrir navegador: {e}")
        capture_debug(d, "teste_selenium")
    finally:
        try:
            d.quit()
        except:
            pass

# =========================================================
# Botão principal: executar automação
# =========================================================
if st.button("🚀 Iniciar Robô"):
    driver = configurar_driver()
    try:
        with st.status("Executando automação...", expanded=True) as s:
            wait = WebDriverWait(driver, 45)

            # 1. Login
            driver.get("https://portal.amhp.com.br/")
            wait_visible(driver, (By.ID, "input-9")).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            time.sleep(wait_time_main)

            # 2. AMHPTISS (clique + troca de janela segura)
            prev_handles = driver.window_handles
            safe_click(driver, (By.XPATH, "//button[contains(., 'AMHPTISS')]"))
            try:
                wait_new_window_and_switch(driver, prev_handles, timeout=30)
            except TimeoutException:
                # Fallback: mesma janela
                try:
                    link = driver.find_element(By.XPATH, "//a[contains(., 'AMHPTISS')]")
                    href = link.get_attribute("href")
                    if href:
                        driver.get(href)
                    else:
                        driver.execute_script("arguments[0].click();", link)
                except Exception as e:
                    capture_debug(driver, "amhptiss_click")
                    raise e

            time.sleep(wait_time_main)

            # 3. Limpeza de avisos/pop-ups
            driver.execute_script("""
                const avisos = document.querySelectorAll('center, #fechar-informativo, .modal');
                avisos.forEach(el => el.remove());
            """)

            # 4. Navegação
            driver.execute_script("document.getElementById('IrPara').click();")
            time.sleep(2)
            safe_click(driver, (By.XPATH, "//span[normalize-space()='Consultório']"))
            safe_click(driver, (By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))

            # 5. Filtros
            st.write("📅 Aplicando filtros de data...")
            wait_visible(driver, (By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput"))\
                .send_keys(data_inicio.strftime("%d/%m/%Y") + Keys.TAB)
            driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput")\
                .send_keys(data_final.strftime("%d/%m/%Y") + Keys.TAB)

            # Buscar
            safe_click(driver, (By.ID, "ctl00_MainContent_btnBuscar_input"))

            # 6. Seleção e impressão
            st.write("⌛ Gerando lista de atendimentos...")
            wait_visible(driver, (By.CSS_SELECTOR, ".rgMasterTable"))
            driver.execute_script("document.getElementById('ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_SelectColumnSelectCheckBox').click();")
            time.sleep(2)
            driver.execute_script("document.getElementById('ctl00_MainContent_rbtImprimirAtendimentos_input').click();")

            # 7. Iframe de Download
            time.sleep(wait_time_main)
            switch_to_iframe_safe(
                driver,
                timeout=20,
                iframe_locator=(By.CSS_SELECTOR, "iframe[id*='ReportView']"),
                index_fallback=0
            )

            # 8. Exportar (CSV/EXCELOPENXML preferidos; senão XLS)
            ddl = Select(wait_visible(driver, (By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")))
            selected = False
            for val in ["CSV", "EXCELOPENXML", "XLS"]:
                try:
                    ddl.select_by_value(val)
                    selected = True
                    break
                except Exception:
                    continue
            if not selected:
                try:
                    ddl.select_by_index(0)
                except Exception:
                    pass

            time.sleep(2)
            safe_click(driver, (By.ID, "ReportView_ReportToolbar_ExportGr_Export"))

            st.write("📥 Solicitando arquivo de relatório...")
            time.sleep(wait_time_download)

            # 9. Processamento do download
            arquivos = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR)
                        if f.lower().endswith((".xls", ".csv", ".xlsx"))]
            if arquivos:
                recente = max(arquivos, key=os.path.getctime)
                ext = os.path.splitext(recente)[1].lower()

                if ext == ".xls":
                    ok = processar_xls_amhp(recente, status_label, neg_label)
                    if ok:
                        st.success(f"✅ {len(st.session_state.db_consolidado)} registros processados!")
                    os.remove(recente)

                elif ext == ".csv":
                    # CSV direto (tenta ; e fallback para ,)
                    try:
                        df_csv = pd.read_csv(recente, sep=";", encoding="utf-8-sig")
                    except Exception:
                        df_csv = pd.read_csv(recente, sep=",", encoding="latin-1")
                    df_csv["Filtro_Status"] = sanitize_value_for_excel(status_label)
                    df_csv["Filtro_Negociacao"] = sanitize_value_for_excel(neg_label)
                    st.session_state.db_consolidado = pd.concat(
                        [st.session_state.db_consolidado, sanitize_df_for_excel(df_csv)],
                        ignore_index=True
                    )
                    st.success(f"✅ {len(st.session_state.db_consolidado)} registros processados (CSV)!")
                    os.remove(recente)

                elif ext == ".xlsx":
                    df_xlsx = pd.read_excel(recente, engine="openpyxl")
                    df_xlsx["Filtro_Status"] = sanitize_value_for_excel(status_label)
                    df_xlsx["Filtro_Negociacao"] = sanitize_value_for_excel(neg_label)
                    st.session_state.db_consolidado = pd.concat(
                        [st.session_state.db_consolidado, sanitize_df_for_excel(df_xlsx)],
                        ignore_index=True
                    )
                    st.success(f"✅ {len(st.session_state.db_consolidado)} registros processados (XLSX)!")
                    os.remove(recente)
            else:
                st.error("Arquivo não encontrado. O sistema AMHP pode ter demorado demais ou bloqueou o download.")
                capture_debug(driver, "sem_arquivo")

            s.update(label="Processo concluído!", state="complete")

    except Exception as e:
        st.error(f"Erro Crítico: {e}")
        capture_debug(driver, "erro_critico")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

# =========================================================
# Resultados & Exportações (CSV + XLSX/xlsxwriter)
# =========================================================
if not st.session_state.db_consolidado.empty:
    st.divider()
    df_safe_preview = sanitize_df_for_excel(st.session_state.db_consolidado)
    st.dataframe(df_safe_preview)

    # CSV
    csv_bytes = df_safe_preview.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        "💾 Baixar Relatório Consolidado (CSV)",
        csv_bytes,
        "relatorio_amhp.csv",
        "text/csv",
    )

    # XLSX (xlsxwriter)
    xlsx_buffer = io.BytesIO()
    with pd.ExcelWriter(xlsx_buffer, engine="xlsxwriter") as writer:
        df_safe_preview.to_excel(writer, index=False, sheet_name="Relatório")
        worksheet = writer.sheets["Relatório"]
        for i, col in enumerate(df_safe_preview.columns):
            try:
                max_len = int(max(12, df_safe_preview[col].astype(str).str.len().max()))
            except Exception:
                max_len = 12
            worksheet.set_column(i, i, min(max_len + 2, 60))

    st.download_button(
        "📊 Baixar Relatório Consolidado (Excel XLSX)",
        data=xlsx_buffer.getvalue(),
        file_name="relatorio_amhp.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    if st.button("🗑️ Limpar Banco"):
        st.session_state.db_consolidado = pd.DataFrame()
        st.rerun()
