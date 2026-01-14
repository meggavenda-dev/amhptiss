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
    # Apenas visual — a chamada do parser será forçada para "text"
    extraction_mode    = st.selectbox("🧠 Modo de extração do PDF (visual)", ["Coordenadas (recomendado)", "Texto (fallback)"])
    debug_parser       = st.checkbox("🧪 Debug do parser PDF", value=False)

# ========= (Opcional) Processar PDF manualmente =========
with st.expander("🧪 Testar parser com upload de PDF (sem automação)", expanded=False):
    up = st.file_uploader("Envie um PDF do AMHPTISS para teste", type=["pdf"])
    if up and st.button("Processar PDF (teste)"):
        tmp_pdf = os.path.join(DOWNLOAD_TEMPORARIO, "teste_upload.pdf")
        with open(tmp_pdf, "wb") as f:
            f.write(up.getvalue())
        # Força TEXTUAL mesmo no teste
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

                # ====== Processa PDF (correção de indentação + remoção de key_os) ======
                arquivos = [
                    os.path.join(DOWNLOAD_TEMPORARIO, f)
                    for f in os.listdir(DOWNLOAD_TEMPORARIO)
                    if f.lower().endswith(".pdf")
                ]

                if arquivos:
                    # pega o mais recente corretamente
                    # ❌ REMOVIDO: recente = max(arquivos, key_os=os.path.getctime)
                    # ✅ MANTIDO:
                    recente = max(arquivos, key=os.path.getctime)

                    nome_pdf = (
                        f"Relatorio_{status_sel.replace(' ', '_').replace('/','-')}_"
                        f"{data_ini.replace('/','-')}_a_{data_fim.replace('/','-')}.pdf"
                    )
                    destino_pdf = os.path.join(PASTA_FINAL, nome_pdf)
                    shutil.move(recente, destino_pdf)
                    st.success(f"✅ PDF salvo: {destino_pdf}")

                    st.write("📄 Extraindo Tabela — Atendimentos do PDF...")
                    # >>> FORÇANDO MODO TEXTUAL (seletor da UI é apenas visual)
                    df_pdf = parse_pdf_to_atendimentos_df(destino_pdf, mode="text", debug=debug_parser)

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
                        st.warning("⚠️ Modo textual não conseguiu extrair linhas. Envie o PDF pelo expander de teste para analisarmos.")

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
