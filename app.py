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
    if chrome_bin_secret: os.environ["CHROME_BINARY"] = chrome_bin_secret
    if driver_bin_secret: os.environ["CHROMEDRIVER_BINARY"] = driver_bin_secret
except Exception:
    pass

st.set_page_config(page_title="AMHP - Exportador", layout="wide")
st.title("🏥 Exportador AMHP — Consolidador de Relatórios")

if "db_consolidado" not in st.session_state:
    st.session_state.db_consolidado = pd.DataFrame()

# ========= Configurações de Caminho =========
DOWNLOAD_TEMPORARIO = os.path.join(os.getcwd(), "temp_downloads")
os.makedirs(DOWNLOAD_TEMPORARIO, exist_ok=True)

TARGET_COLS = [
    "Atendimento","NrGuia","Realizacao","Hora","TipoGuia",
    "Operadora","Matricula","Beneficiario","Credenciado",
    "Prestador","ValorTotal"
]

# ========= Motor de Extração de Texto (Parser) =========

def to_float_br(s):
    try:
        # Remove pontos de milhar e troca vírgula por ponto
        return float(str(s).replace('.', '').replace(',', '.'))
    except:
        return 0.0

def clean_text_flux(raw):
    """ Corrige colagens comuns antes do parsing """
    txt = raw.replace("\u00A0", " ")
    # Separa Atendimento de Guia se estiverem colados (8+8 dígitos)
    txt = re.sub(r"(\d{8})(\d{8})", r"\1 \2", txt)
    # Separa Data de Hora (dd/mm/aaaaHH:mm)
    txt = re.sub(r"(\d{2}/\d{2}/\d{4})(\d{2}:\d{2})", r"\1 \2", txt)
    # Separa Hora de Tipo de Guia (HH:mmConsulta)
    txt = re.sub(r"(\d{2}:\d{2})([A-ZÁ-Ú])", r"\1 \2", txt)
    return re.sub(r"\s+", " ", txt).strip()

def parse_streaming_any(texto_bruto):
    """ Lógica de extração por blocos de atendimento """
    s = clean_text_flux(texto_bruto)
    
    # Regex para detectar o início de cada linha (Atendimento, Guia, Data, Hora)
    # Atendimento e Guia podem ter de 6 a 12 dígitos cada
    pattern_head = re.compile(r"(\d{6,12})\s+(\d{6,12})\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})")
    
    # Regex para valor monetário (âncora de fim de linha)
    pattern_val = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")
    
    matches = list(pattern_head.finditer(s))
    rows = []
    
    for i, m in enumerate(matches):
        start_idx = m.start()
        # O fim do bloco é o começo do próximo ou o fim do texto
        end_idx = matches[i+1].start() if (i+1) < len(matches) else len(s)
        segmento = s[start_idx:end_idx]
        
        # Busca o valor monetário neste segmento
        valores = list(pattern_val.finditer(segmento))
        if not valores: continue
        
        valor_final = valores[-1].group(0)
        # O "miolo" é tudo entre a Hora e o Valor Final
        header_data = m.groups() # (atendimento, guia, data, hora)
        
        miolo = segmento[len(m.group(0)):segmento.rfind(valor_final)].strip()
        
        # Divisão do miolo (TipoGuia, Operadora, Matrícula, Beneficiário)
        tokens = miolo.split()
        if not tokens: continue
        
        # Lógica para Tipo de Guia (Consulta ou SP/SADT)
        tipo = tokens[0]
        if tipo.upper() in ["CONSULTA", "SP/SADT"]:
            restante = tokens[1:]
        else:
            tipo = "Outros"
            restante = tokens
            
        # Localiza Matrícula (token longo, geralmente numérico ou alfanumérico)
        idx_mat = None
        for idx, t in enumerate(restante):
            if len(t) >= 7 and (t.isdigit() or any(c.isdigit() for c in t)):
                idx_mat = idx
                break
        
        if idx_mat is not None:
            operadora = " ".join(restante[:idx_mat])
            matricula = restante[idx_mat]
            # O que sobra é o Beneficiário + Prestadores
            sobra = " ".join(restante[idx_mat+1:])
        else:
            operadora = " ".join(restante)
            matricula = ""
            sobra = ""

        # Separação de Credenciado e Prestador (Padrão: 0000-Nome)
        prestadores = re.findall(r"\d{3,}-\S+", sobra)
        if len(prestadores) >= 2:
            cred, prest = prestadores[-2], prestadores[-1]
            benef = sobra.split(cred)[0].strip()
        elif len(prestadores) == 1:
            cred, prest = "", prestadores[0]
            benef = sobra.split(prest)[0].strip()
        else:
            cred, prest, benef = "", "", sobra

        rows.append({
            "Atendimento": header_data[0],
            "NrGuia": header_data[1],
            "Realizacao": header_data[2],
            "Hora": header_data[3],
            "TipoGuia": tipo,
            "Operadora": operadora,
            "Matricula": matricula,
            "Beneficiario": benef,
            "Credenciado": cred,
            "Prestador": prest,
            "ValorTotal": valor_final
        })

    return pd.DataFrame(rows)

# ========= Interface e Selenium =========

def configurar_driver():
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    # Configurar binários caso existam nos secrets
    if "env" in st.secrets:
        if "CHROME_BINARY" in st.secrets["env"]: opts.binary_location = st.secrets["env"]["CHROME_BINARY"]
    
    driver_path = st.secrets["env"]["CHROMEDRIVER_BINARY"] if "env" in st.secrets else None
    if driver_path:
        return webdriver.Chrome(service=Service(driver_path), options=opts)
    return webdriver.Chrome(options=opts)

def capture_report_text(driver):
    """ Tenta capturar o texto do relatório SSRS de múltiplas formas """
    try:
        # Tenta o container principal do ReportViewer
        container = driver.find_element(By.ID, "VisibleReportContent")
        return container.text
    except:
        # Fallback para o body inteiro caso o ID mude
        return driver.find_element(By.TAG_NAME, "body").text

# ========= Streamlit UI =========

with st.sidebar:
    st.header("Filtros")
    data_ini = st.text_input("Data Início", "01/12/2025")
    data_fim = st.text_input("Data Fim", "31/12/2025")
    btn_start = st.button("🚀 Iniciar Captura")

with st.expander("Colar texto manualmente"):
    txt_input = st.text_area("Se a automação falhar, cole o texto do relatório aqui:")
    if st.button("Processar Texto Colado"):
        df_manual = parse_streaming_any(txt_input)
        st.dataframe(df_manual)
        st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df_manual])

if btn_start:
    driver = configurar_driver()
    try:
        with st.status("Automação em curso...") as status_bar:
            driver.get("https://portal.amhp.com.br/")
            
            # Login (Secrets)
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
            driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
            
            # Navegação até o relatório
            time.sleep(5)
            # (Aqui deve seguir os cliques específicos que você já tinha no seu código)
            # ...
            
            # Captura de Texto
            txt_relatorio = capture_report_text(driver)
            df_lote = parse_streaming_any(txt_relatorio)
            
            if not df_lote.empty:
                st.session_state.db_consolidado = pd.concat([st.session_state.db_consolidado, df_lote])
                status_bar.update(label="Extração concluída!", state="complete")
            else:
                st.error("Nenhum dado encontrado no texto capturado.")
    finally:
        driver.quit()

# Exibição dos resultados
if not st.session_state.db_consolidado.empty:
    st.divider()
    st.subheader("Dados Consolidados")
    st.dataframe(st.session_state.db_consolidado, use_container_width=True)
    
    # Botão de Download
    xlsx_io = io.BytesIO()
    with pd.ExcelWriter(xlsx_io, engine='openpyxl') as writer:
        st.session_state.db_consolidado.to_excel(writer, index=False)
    st.download_button("Baixar Excel", xlsx_io.getvalue(), "relatorio_amhp.xlsx")
