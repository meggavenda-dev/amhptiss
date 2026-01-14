import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
import time

st.set_page_config(page_title="Automação AMHP", layout="wide")

def iniciar_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    service = Service("/usr/bin/chromedriver")
    try:
        return webdriver.Chrome(service=service, options=options)
    except:
        return webdriver.Chrome(options=options)

st.title("🏥 Gerador de Relatórios AMHP")

col1, col2 = st.columns(2)
with col1:
    data_inicio = st.text_input("📅 Data Inicial", value="01/01/2024")
with col2:
    data_fim = st.text_input("📅 Data Final", value="31/01/2024")

if st.button("🚀 Gerar Relatório"):
    driver = iniciar_driver()
    if driver:
        try:
            with st.status("Executando...", expanded=True) as status:
                wait = WebDriverWait(driver, 40) # Aumentamos o tempo de espera geral
                
                # --- LOGIN E NAVEGAÇÃO ---
                st.write("🔐 Acessando e Logando...")
                driver.get("https://portal.amhp.com.br/")
                wait.until(EC.presence_of_element_located((By.ID, "input-9"))).send_keys(st.secrets["credentials"]["usuario"])
                driver.find_element(By.ID, "input-12").send_keys(st.secrets["credentials"]["senha"] + Keys.ENTER)
                time.sleep(10) 
                
                driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'AMHPTISS')]"))))
                time.sleep(8)
                if len(driver.window_handles) > 1: driver.switch_to.window(driver.window_handles[1])

                try: # Fechar informativo
                    driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable((By.ID, "fechar-informativo"))))
                except: pass

                # --- NAVEGAÇÃO ATÉ A TELA ---
                st.write("📂 Abrindo Atendimentos Realizados...")
                wait.until(EC.element_to_be_clickable((By.ID, "IrPara"))).click()
                wait.until(EC.element_to_be_clickable((By.XPATH, "//span[@class='rtIn' and contains(text(), 'Consultório')]"))).click()
                wait.until(EC.element_to_be_clickable((By.XPATH, "//a[@href='AtendimentosRealizados.aspx']"))).click()
                
                # --- PREENCHIMENTO DOS FILTROS ---
                st.write("📝 Configurando filtros...")
                
                # Negociação
                neg = wait.until(EC.element_to_be_clickable((By.ID, "ctl00_MainContent_rcbTipoNegociacao_Input")))
                neg.clear()
                neg.send_keys("Direto" + Keys.ENTER)
                time.sleep(1)

                # Status
                stat = wait.until(EC.element_to_be_clickable((By.ID, "ctl00_MainContent_rcbStatus_Input")))
                stat.clear()
                stat.send_keys("300 - Pronto para Processamento" + Keys.ENTER)
                time.sleep(1)

                # Datas
                driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataInicio_dateInput").send_keys(data_inicio)
                driver.find_element(By.ID, "ctl00_MainContent_rdpDigitacaoDataFim_dateInput").send_keys(data_fim)

                # --- BOTÃO BUSCAR E ESPERA INTELIGENTE ---
                st.write("🔍 Gerando relatório... Por favor, aguarde.")
                btn_buscar = driver.find_element(By.ID, "ctl00_MainContent_btnBuscar_input")
                driver.execute_script("arguments[0].click();", btn_buscar)

                # ESPERA DINÂMICA:
                # 1. Esperamos um breve momento para o 'loading' aparecer
                time.sleep(3)
                
                # 2. Esperamos até que o indicador de carregamento (se houver) suma 
                # OU até que a tabela de resultados (Grid) seja atualizada/visível.
                # Geralmente o Telerik usa IDs que contêm 'Grid' ou 'RadGrid'
                try:
                    st.write("⏳ O sistema está processando os dados...")
                    # Espera até 60 segundos por algum elemento que indique que a tabela carregou
                    # Aqui usamos um seletor genérico para tabelas de resultados
                    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".rgMasterTable, #ctl00_MainContent_gvAtendimentos")))
                    st.write("✅ Dados carregados com sucesso!")
                except:
                    st.write("⚠️ O tempo de espera excedeu, tentando capturar o que estiver na tela...")

                # Finalização
                driver.save_screenshot("relatorio_gerado.png")
                st.image("relatorio_gerado.png", caption=f"Relatório Gerado: {data_inicio} a {data_fim}")
                st.success("Processo concluído!")
                status.update(label="Relatório Pronto!", state="complete", expanded=False)

        except Exception as e:
            st.error(f"🚨 Erro: {e}")
            driver.save_screenshot("erro_relatorio.png")
            st.image("erro_relatorio.png")
        finally:
            driver.quit()
