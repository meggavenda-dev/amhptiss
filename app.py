# app.py
import asyncio
import os
import streamlit as st
from datetime import datetime
from automation.login import amhp_login

st.set_page_config(page_title="AMHP Automação - Login/TISS", page_icon="🔐", layout="centered")

st.title("🔐 AMHP – Login e acesso ao AMHPTISS")
st.caption("Protótipo em Streamlit com Playwright")

with st.expander("⚙️ Configurações"):
    headless = st.checkbox("Executar headless (sem abrir janela)", value=True)
    persist_state = st.checkbox("Persistir estado da sessão (cookies) em 'state.json'", value=False)
    timeout_sec = st.number_input("Timeout de navegação (segundos)", min_value=10, max_value=120, value=45, step=5)

st.subheader("Credenciais")
col1, col2 = st.columns(2)
with col1:
    user = st.text_input("Usuário", value=st.secrets.get("AMHP_USER", ""), placeholder="seu usuário")
with col2:
    pwd = st.text_input("Senha", value=st.secrets.get("AMHP_PASS", ""), placeholder="sua senha", type="password")

run = st.button("Fazer login e abrir AMHPTISS")

status_placeholder = st.empty()
img_col1, img_col2 = st.columns(2)
log_placeholder = st.empty()

def run_login():
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    first_png = f"login-{ts}.png"
    tiss_png  = f"tiss-{ts}.png"
    state_path = "state.json" if persist_state else None

    status_placeholder.info("Iniciando automação…")
    result = asyncio.run(amhp_login(
        username=user.strip(),
        password=pwd,
        headless=headless,
        persist_state_path=state_path,
        first_screenshot=first_png,
        tiss_screenshot=tiss_png,
        nav_timeout_ms=int(timeout_sec * 1000),
    ))

    if os.path.exists(first_png):
        with img_col1:
            st.image(first_png, caption="Tela após submit no Portal AMHP", use_column_width=True)
    if os.path.exists(tiss_png):
        with img_col2:
            st.image(tiss_png, caption="Tela ao acessar AMHPTISS", use_column_width=True)

    return result, first_png, tiss_png, state_path

if run:
    if not user or not pwd:
        st.error("Informe usuário e senha (ou configure em `st.secrets`).")
        st.stop()

    result, first_png, tiss_png, state_path = run_login()

    if result["ok"]:
        msg = f"✅ Login realizado. URL atual: {result['logged_url']}"
        if result.get("tiss_access_ok"):
            msg += "\n\n✅ AMHPTISS acessado com a mesma sessão."
        else:
            msg += "\n\nℹ️ AMHPTISS não confirmou acesso automático. Talvez exija clique/menu/SSO. Ajuste o fluxo."
        status_placeholder.success(msg)
    else:
        status_placeholder.error(f"❌ Falha: {result.get('reason','(sem detalhe)')}")

    with log_placeholder:
        st.code(result, language="json")

st.markdown("---")
st.caption("Dica: use **Headless = False** localmente para inspecionar o fluxo e ajustar os seletores.")
