# -*- coding: utf-8 -*-
import os
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
st.set_page_config(page_title="AMHP - Exportador PDF", layout="wide")
st.title("🏥 Exportador AMHP (PDF)")

# ========= Estado =========
if "db_consolidado" not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame()

# ========= Utils =========
def sanitize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Placeholder seguro para evitar NameError"""
    return df.copy()

def calcular_total_passos(negociacoes, status_list):
    return max(1, len(negociacoes) * len(status_list))

# ========= Paths =========
def obter_caminho_final():
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    path = os.path.join(
        desktop if os.path.exists(desktop) else os.getcwd(),
        "automacao_pdf"
    )
    os.makedirs(path, exist_ok=True)
    return path

PASTA_FINAL = obter_caminho_final()
DOWNLOAD_TEMPORARIO = os.path.join(os.getcwd(), "temp_downloads")
os.makedirs(DOWNLOAD_TEMPORARIO, exist_ok=True)

# ========= Sidebar =========
with st.sidebar:
    st.header("Configurações")

    data_ini = st.text_input("📅 Data Inicial", "01/01/2026")
    data_fim = st.text_input("📅 Data Final", "13/01/2026")

    modo_execucao = st.selectbox(
        "⚙️ Modo de Execução",
        ["Manual (teste)", "Automático (Direto + Normal)"],
        index=0
    )

    if modo_execucao == "Manual (teste)":
        negociacoes = [st.selectbox(
            "🤝 Tipo de Negociação",
            ["Direto", "Normal"],
            index=0
        )]
    else:
        negociacoes = ["Direto", "Normal"]

    status_list = st.multiselect(
        "📌 Status",
        ["300 - Pronto para Processamento", "200 - Em Análise"],
        default=["300 - Pronto para Processamento"]
    )

    wait_time_main = st.number_input("⏱️ Espera navegação (s)", 0, 60, 10)
    wait_time_download = st.number_input("⏱️ Espera download (s)", 10, 60, 18)

# ========= Selenium =========
def configurar_driver():
    opts = Options()
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

    service = Service()
    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(180)
    return driver

def safe_click(driver, locator, timeout=30):
    el = WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable(locator)
    )
    driver.execute_script("arguments[0].click();", el)

# ========= Botão Principal =========
if st.button("🚀 Iniciar Processo (PDF)"):

    progress_bar = st.progress(0)
    status_box = st.empty()

    driver = configurar_driver()

    try:
        wait = WebDriverWait(driver, 40)

        total_passos = calcular_total_passos(negociacoes, status_list)
        passo_atual = 0

        status_box.info("🔐 Realizando login no portal AMHP...")
        driver.get("https://portal.amhp.com.br/")

        wait.until(
            EC.presence_of_element_located((By.ID, "input-9"))
        ).send_keys(st.secrets["credentials"]["usuario"])

        driver.find_element(By.ID, "input-12").send_keys(
            st.secrets["credentials"]["senha"] + Keys.ENTER
        )

        time.sleep(wait_time_main)

        status_box.info("🧭 Acessando módulo AMHPTISS...")
        btn_tiss = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(., 'AMHPTISS')]")
            )
        )
        driver.execute_script("arguments[0].click();", btn_tiss)
        time.sleep(wait_time_main)

        if len(driver.window_handles) > 1:
            driver.switch_to.window(driver.window_handles[-1])

        status_box.info("📂 Navegando para Atendimentos Realizados...")
        driver.execute_script("document.getElementById('IrPara').click();")
        time.sleep(2)
        safe_click(driver, (By.XPATH, "//span[normalize-space()='Consultório']"))
        safe_click(driver, (By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))
        time.sleep(3)

        # ========= LOOP PRINCIPAL =========
        for negociacao in negociacoes:
            for status_sel in status_list:

                passo_atual += 1
                percentual = int((passo_atual / total_passos) * 100)

                status_box.info(
                    f"🔄 {passo_atual}/{total_passos} | "
                    f"Negociação: {negociacao} | Status: {status_sel}"
                )
                progress_bar.progress(percentual)

                # Negociação
                neg_input = wait.until(
                    EC.presence_of_element_located(
                        (By.ID, "ctl00_MainContent_rcbTipoNegociacao_Input")
                    )
                )
                neg_input.click()
                neg_input.send_keys(Keys.CONTROL, "a", Keys.BACKSPACE, negociacao)
                time.sleep(0.5)
                neg_input.send_keys(Keys.ENTER)

                # Status
                stat_input = wait.until(
                    EC.presence_of_element_located(
                        (By.ID, "ctl00_MainContent_rcbStatus_Input")
                    )
                )
                stat_input.click()
                stat_input.send_keys(Keys.CONTROL, "a", Keys.BACKSPACE, status_sel)
                stat_input.send_keys(Keys.ENTER)

                # Datas
                driver.find_element(
                    By.ID,
                    "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput"
                ).send_keys(Keys.CONTROL, "a", Keys.BACKSPACE, data_ini)

                driver.find_element(
                    By.ID,
                    "ctl00_MainContent_rdpDigitacaoDataFim_dateInput"
                ).send_keys(Keys.CONTROL, "a", Keys.BACKSPACE, data_fim)

                # Buscar
                driver.find_element(
                    By.ID,
                    "ctl00_MainContent_btnBuscar_input"
                ).click()

                wait.until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, ".rgMasterTable")
                    )
                )

                # Seleciona tudo
                driver.execute_script(
                    "document.getElementById("
                    "'ctl00_MainContent_rdgAtendimentosRealizados_ctl00_ctl02_ctl00_"
                    "SelectColumnSelectCheckBox').click();"
                )

                time.sleep(2)

                # Imprimir
                driver.find_element(
                    By.ID,
                    "ctl00_MainContent_rbtImprimirAtendimentos_input"
                ).click()

                time.sleep(wait_time_main)

                # Exportar PDF
                driver.switch_to.frame(0)
                dropdown = wait.until(
                    EC.presence_of_element_located(
                        (By.ID, "ReportView_ReportToolbar_ExportGr_FormatList_DropDownList")
                    )
                )
                Select(dropdown).select_by_value("PDF")

                driver.find_element(
                    By.ID,
                    "ReportView_ReportToolbar_ExportGr_Export"
                ).click()

                time.sleep(wait_time_download)
                driver.switch_to.default_content()

        progress_bar.progress(100)
        status_box.success("🎯 Automação finalizada com sucesso!")
        st.success("✅ Processo concluído!")

    except Exception as e:
        st.error(f"❌ Erro durante a automação: {e}")

    finally:
        try:
            driver.quit()
        except Exception:
            pass
