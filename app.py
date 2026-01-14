
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
    if chrome_bin_secret:  os.environ["CHROME_BINARY"]      = chrome_bin_secret
    if driver_bin_secret:  os.environ["CHROMEDRIVER_BINARY"] = driver_bin_secret
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
    if s is None: return s
    s = s.replace("\x00", "")
    s = _ILLEGAL_CTRL_RE.sub("", s)
    s = s.replace("\u00A0", " ").strip()
    return s

def sanitize_value(v):
    if pd.isna(v): return v
    if isinstance(v, (bytes, bytearray)):
        try: v = v.decode("utf-8", "ignore")
        except Exception: v = v.decode("latin-1", "ignore")
    if isinstance(v, str): return _sanitize_text(v)
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

# ========= Selenium =========
def configurar_driver():
    opts = Options()
    chrome_binary  = os.environ.get("CHROME_BINARY", "/usr/bin/chromium")
    driver_binary  = os.environ.get("CHROMEDRIVER_BINARY", "/usr/bin/chromedriver")
    if os.path.exists(chrome_binary): opts.binary_location = chrome_binary
    opts.add_argument("--headless"); opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage"); opts.add_argument("--window-size=1920,1080")
    prefs = {"download.default_directory": DOWNLOAD_TEMPORARIO, "download.prompt_for_download": False,
             "safebrowsing.enabled": True, "profile.default_content_setting_values.automatic_downloads": 1}
    opts.add_experimental_option("prefs", prefs)
    driver = (webdriver.Chrome(service=Service(executable_path=driver_binary), options=opts)
              if os.path.exists(driver_binary) else webdriver.Chrome(options=opts))
    driver.set_page_load_timeout(60)
    return driver

def wait_visible(driver, locator, timeout=30): return WebDriverWait(driver, timeout).until(EC.visibility_of_element_located(locator))
def safe_click(driver, locator, timeout=30):
    try:
        el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator)); el.click(); return el
    except (ElementClickInterceptedException, TimeoutException, WebDriverException):
        el = WebDriverWait(driver, timeout).until(EC.presence_of_element_located(locator))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        driver.execute_script("arguments[0].click();", el); return el

# ========= PDF → Tabela por colunas (pdfplumber) =========
def parse_pdf_to_atendimentos_df(pdf_path: str) -> pd.DataFrame:
    """
    Extrai a Tabela — Atendimentos por coordenadas:
    - Descobre o cabeçalho (linha com 'Atendimento ... Valor Total').
    - Define colunas por caixas (x0/x1) usando as posições das palavras do cabeçalho.
    - Agrupa palavras por faixas horizontais (linhas), cortando por colunas.
    - Remove 'Total R$ ...' final.
    """
    import pdfplumber
    val_re = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}$")  # ex.: 104,38
    # 1) Abre PDF e pega a primeira página (relatório é de 1 página nos seus exemplos) [1](https://amhpdfbr-my.sharepoint.com/personal/guilherme_cavalcante_amhp_com_br/_layouts/15/Doc.aspx?sourcedoc=%7B9680BAF3-0CBB-4670-886B-52010E59BD51%7D&file=2026-01-14T05-06_export.csv&action=default&mobileredirect=true)
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]

        # 2) Extrai todas as palavras com coordenadas
        words = page.extract_words(use_text_flow=True, extra_attrs=["x0","x1","top","bottom"])
        text = " ".join([w["text"] for w in words])

        # 3) Localiza linha do cabeçalho (contém 'Atendimento' e 'Valor Total') [1](https://amhpdfbr-my.sharepoint.com/personal/guilherme_cavalcante_amhp_com_br/_layouts/15/Doc.aspx?sourcedoc=%7B9680BAF3-0CBB-4670-886B-52010E59BD51%7D&file=2026-01-14T05-06_export.csv&action=default&mobileredirect=true)
        header_y = None
        header_words = []
        for w in words:
            if "Atendimento" in w["text"]:
                # Aproximação: pega banda horizontal do cabeçalho
                y_top = w["top"]
                band = [ww for ww in words if abs(ww["top"] - y_top) < 2.0]
                band_text = " ".join([b["text"] for b in band])
                if ("Valor" in band_text) and ("Total" in band_text):
                    header_y = y_top
                    header_words = sorted(band, key=lambda z: z["x0"])
                    break
        if header_y is None:
            # fallback: usa extract_tables se não achou (layout muito diferente)
            tbls = page.extract_tables()
            if tbls:
                df = pd.DataFrame(tbls[0])
                # tenta mapear nomes
                # (mantém fallback simples; o grid abaixo cobre 99% dos casos)
                df.columns = df.iloc[0]; df = df.iloc[1:].dropna(how="all", axis=1)
                return sanitize_df(df)

        # 4) Define colunas por ranges x0/x1 a partir das palavras do cabeçalho
        #    Vamos procurar labels chave: Atendimento | Nr. Guia | Realização | Hora | Tipo de Guia | Operadora | Matrícula | Beneficiário | Credenciado | Prestador | Valor Total
        labels_expected = ["Atendimento","Nr.","Nr","Guia","Realização","Hora","Tipo","de","Guia","Operadora",
                           "Matrícula","Beneficiário","Credenciado","Prestador","Valor","Total"]
        header_sorted = sorted(header_words, key=lambda z: z["x0"])
        # Junta palavras vizinhas em blocos (por proximidade x)
        blocks = []
        cur = [header_sorted[0]]
        for w in header_sorted[1:]:
            if abs(w["x0"] - cur[-1]["x1"]) <= 6:  # palavras muito próximas do cabeçalho pertencem ao mesmo bloco
                cur.append(w)
            else:
                blocks.append(cur); cur=[w]
        blocks.append(cur)

        # Texto dos blocos + range x
        header_blocks = [{"text":" ".join([b["text"] for b in bl]), "x0":min([b["x0"] for b in bl]), "x1":max([b["x1"] for b in bl])} for bl in blocks]

        # Mapeia blocos para nomes finais das colunas (heurística)
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
                columns.append({"name":name, "x0":hb["x0"], "x1":hb["x1"]})

        # Ordena colunas por x0
        columns = sorted(columns, key=lambda c: c["x0"])

        # 5) Agrupa palavras em “linhas” abaixo do cabeçalho (bandas horizontais)
        #    Ignora a banda do cabeçalho e tudo acima
        data_words = [w for w in words if w["top"] > header_y + 2]
        # Remove a linha de "Total R$ ..." (fica geralmente abaixo das linhas de dados) [1](https://amhpdfbr-my.sharepoint.com/personal/guilherme_cavalcante_amhp_com_br/_layouts/15/Doc.aspx?sourcedoc=%7B9680BAF3-0CBB-4670-886B-52010E59BD51%7D&file=2026-01-14T05-06_export.csv&action=default&mobileredirect=true)
        total_indices = [i for i,w in enumerate(data_words) if w["text"].lower()=="total"]
        if total_indices:
            # corta dados acima do 'Total'
            total_y = data_words[total_indices[0]]["top"]
            data_words = [w for w in data_words if w["top"] < total_y - 2]

        # cria bandas horizontais de linha (tolerância ~2.0 pts)
        rows = []
        band = []
        last_top = None
        for w in sorted(data_words, key=lambda z: (round(z["top"],1), z["x0"])):
            if last_top is None or abs(w["top"] - last_top) <= 2.0:
                band.append(w); last_top = w["top"]
            else:
                rows.append(band); band=[w]; last_top = w["top"]
        if band: rows.append(band)

        # 6) Para cada banda, coleta o texto de cada coluna por faixa x (x0/x1)
        records = []
        for row_words in rows:
            cols_text = {}
            for col in columns:
                col_words = [w for w in row_words if (w["x0"] >= col["x0"] - 1) and (w["x1"] <= col["x1"] + 1)]
                txt = " ".join([w["text"] for w in sorted(col_words, key=lambda z: z["x0"])])
                cols_text[col["name"]] = txt.strip()
            # Heurística: se não há ValorTotal, pode ser uma banda de quebra; pula
            if not cols_text.get("ValorTotal") or not val_re.search(cols_text["ValorTotal"]):
                continue
            records.append(cols_text)

        df = pd.DataFrame(records)
        # Ajustes finos: algumas colunas podem “vazar” texto longo entre Credenciado/Prestador
        # Se 'Credenciado' ou 'Prestador' vierem vazios, tenta recuperar por um padrão "CODIGO-Nome"
        code_start_re = re.compile(r"\d{3,6}-")
        cleaned = []
        for _, r in df.iterrows():
            # ValorTotal normaliza como string
            valor = r.get("ValorTotal","").strip()
            # Monta linha reconstituída para fallback do code-name caso necessário
            tail = " ".join([str(r.get("Beneficiario","")).strip(), str(r.get("Credenciado","")).strip(), str(r.get("Prestador","")).strip()]).strip()
            starts = [m.start() for m in code_start_re.finditer(tail)]
            cred, prest = r.get("Credenciado","").strip(), r.get("Prestador","").strip()
            if not cred or not prest:
                if len(starts) >= 2:
                    i1, i2 = starts[-2], starts[-1]
                    prest = tail[i2:].strip()
                    cred  = tail[i1:i2].strip()
            cleaned.append({
                "Atendimento":   r.get("Atendimento","").strip(),
                "NrGuia":        r.get("NrGuia","").strip(),
                "Realizacao":    r.get("Realizacao","").strip(),
                "Hora":          r.get("Hora","").strip(),
                "TipoGuia":      r.get("TipoGuia","").strip(),
                "Operadora":     r.get("Operadora","").strip(),
                "Matricula":     r.get("Matricula","").strip(),
                "Beneficiario":  r.get("Beneficiario","").strip(),
                "Credenciado":   cred,
                "Prestador":     prest,
                "ValorTotal":    valor,
            })
        out = pd.DataFrame(cleaned)
        # Ordena por data/hora
        try:
            out["Realizacao_dt"] = pd.to_datetime(out["Realizacao"], format="%d/%m/%Y")
            out = out.sort_values(["Realizacao_dt","Hora"]).drop(columns=["Realizacao_dt"])
        except Exception:
            pass
        return sanitize_df(out)

# ========= UI =========
with st.sidebar:
    st.header("Configurações")
    data_ini    = st.text_input("📅 Data Inicial (dd/mm/aaaa)", value="01/01/2026")
    data_fim    = st.text_input("📅 Data Final (dd/mm/aaaa)", value="13/01/2026")
    negociacao  = st.text_input("🤝 Tipo de Negociação", value="Direto")
    status_list = st.multiselect("📌 Status", options=[
        "300 - Pronto para Processamento","200 - Em Análise","100 - Recebido","400 - Processado"
    ], default=["300 - Pronto para Processamento"])

    wait_time_main     = st.number_input("⏱️ Tempo extra pós login/troca de tela (s)", min_value=0, value=10)
    wait_time_download = st.number_input("⏱️ Tempo extra para concluir download (s)", min_value=10, value=18)

# ========= Botão principal =========
if st.button("🚀 Iniciar Processo (PDF)"):
    driver = configurar_driver()
    try:
        with st.status("Executando automação...", expanded=True) as status:
            wait = WebDriverWait(driver, 40)

            # 1. Login
            st.write("🔑 Fazendo login...")
            driver.get("https://portal.amhp.com.br/")
            wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            time.sleep(wait_time_main)

            # 2. AMHPTISS (força clique)
            st.write("🔄 Acessando TISS...")
            try:
                btn_tiss = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]")))
                driver.execute_script("arguments[0].click();", btn_tiss)
            except Exception:
                elems = driver.find_elements(By.XPATH, "//*[contains(translate(., 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'TISS')]")
                if elems: driver.execute_script("arguments[0].click();", elems[0])
                else: raise RuntimeError("Não foi possível localizar AMHPTISS/TISS.")
            time.sleep(wait_time_main)
            if len(driver.window_handles) > 1: driver.switch_to.window(driver.window_handles[-1])

            # 3. Limpeza
            st.write("🧹 Limpando tela...")
            try:
                driver.execute_script("""
                    const avisos = document.querySelectorAll('center, #fechar-informativo, .modal');
                    avisos.forEach(el => el.remove());
                """)
            except Exception: pass

            # 4. Navegação
            st.write("📂 Abrindo Atendimentos...")
            driver.execute_script("document.getElementById('IrPara').click();")
            time.sleep(2)
            safe_click(driver, (By.XPATH, "//span[normalize-space()='Consultório']"))
            safe_click(driver, (By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))
            time.sleep(3)

            # 5. Loop de Status
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
                if len(driver.find_elements(By.TAG_NAME, "iframe")) > 0: driver.switch_to.frame(0)

                # Exportar sempre em PDF (evita CSV instável) [1](https://amhpdfbr-my.sharepoint.com/personal/guilherme_cavalcante_amhp_com_br/_layouts/15/Doc.aspx?sourcedoc=%7B9680BAF3-0CBB-4670-886B-52010E59BD51%7D&file=2026-01-14T05-06_export.csv&action=default&mobileredirect=true)
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
                    df_pdf = parse_pdf_to_atendimentos_df(destino_pdf)

                    if not df_pdf.empty:
                        # Metadados
                        df_pdf["Filtro_Negociacao"] = sanitize_value(negociacao)
                        df_pdf["Filtro_Status"]     = sanitize_value(status_sel)
                        df_pdf["Periodo_Inicio"]    = sanitize_value(data_ini)
                        df_pdf["Periodo_Fim"]       = sanitize_value(data_fim)

                        # Preview
                        cols_show = ["Atendimento","NrGuia","Realizacao","Hora","TipoGuia","Operadora","Matricula","Beneficiario","Credenciado","Prestador","ValorTotal"]
                        st.dataframe(df_pdf[cols_show], use_container_width=True)

                        # Consolida
                        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df_pdf], ignore_index=True)
                        st.write(f"📊 Registros acumulados: {len(st.session_state.db_consolidado)}")
                    else:
                        st.warning("⚠️ Não foi possível extrair linhas do PDF. Verifique o arquivo salvo.")

                    try: driver.switch_to.default_content()
                    except Exception: pass
                else:
                    st.error("❌ PDF não encontrado após o download. O SSRS pode ter demorado ou bloqueado.")
                    try: driver.switch_to.default_content()
                    except Exception: pass

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
        try: driver.quit()
        except Exception: pass

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
