
# app.py
import os
import streamlit as st
import requests
from pathlib import Path
from datetime import datetime

from automation.amhp import login_portal, access_amhptiss
from automation.reports import open_reports_page, guess_export_button_name, guess_event_target_for_export, post_export, save_download_response

# Config
st.set_page_config(page_title="AMHP – HTTP Pura", page_icon="🔐", layout="centered")
st.title("🔐 AMHP – Login HTTP Pura, acesso ao AMHPTISS e download de relatórios")

PORTAL_URL = "https://portal.amhp.com.br/"
AMHPTISS_URL = "https://amhptiss.amhp.com.br/Default.aspx"

# Exemplos: ajuste para a página real de relatórios
DEFAULT_REPORTS_PAGE = "https://amhptiss.amhp.com.br/Relatorios/ProducaoMensal.aspx"  # AJUSTE AQUI

# Credenciais via secrets ou inputs
user_default = st.secrets.get("AMHP_USER", "")
pass_default = st.secrets.get("AMHP_PASS", "")

with st.form("login_form"):
    c1, c2 = st.columns(2)
    with c1:
        username = st.text_input("Usuário", value=user_default)
    with c2:
        password = st.text_input("Senha", value=pass_default, type="password")
    devmode = st.checkbox("Modo desenvolvedor (mostrar HTML bruto)", value=False)
    submit_login = st.form_submit_button("Fazer login")

if submit_login:
    if not username or not password:
        st.error("Informe usuário e senha (ou configure em `.streamlit/secrets.toml`).")
        st.stop()

    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (Streamlit/HTTP)"})

    ok, r_post, reason = login_portal(sess, username.strip(), password.strip())
    if not ok:
        st.error(reason or "Falha no login.")
        if devmode:
            with st.expander("HTML Login (trecho)"):
                st.code(r_post.text[:5000], language="html")
        st.stop()

    st.success("✅ Login realizado no Portal AMHP.")
    ok_tiss, r_tiss = access_amhptiss(sess)
    if ok_tiss:
        st.success(f"✅ AMHPTISS acessado. URL: {r_tiss.url}")
    else:
        st.warning("ℹ️ AMHPTISS não confirmou sessão; pode exigir clique/SSO.")
    if devmode:
        with st.expander("HTML AMHPTISS (trecho)"):
            st.code(r_tiss.text[:5000], language="html")

    # ---- Download de relatório (HTTP pura) ----
    st.markdown("---")
    st.subheader("📄 Baixar relatório")
    reports_page_url = st.text_input("URL da página de relatório", value=DEFAULT_REPORTS_PAGE)

    c3, c4 = st.columns(2)
    with c3:
        mes = st.selectbox("Mês", [f"{i:02d}" for i in range(1,13)], index=datetime.now().month-1)
    with c4:
        ano = st.text_input("Ano", value=str(datetime.now().year))

    # Names padrão (AJUSTE conforme HTML real)
    # Ex.: ctl00$MainContent$cmbMes / ctl00$MainContent$cmbAno
    periodo_names = {
        "ctl00$MainContent$cmbMes": mes,
        "ctl00$MainContent$cmbAno": ano,
    }

    if st.button("Gerar/Exportar"):
        try:
            tokens, soup, action_url = open_reports_page(sess, reports_page_url)
            submit_name = guess_export_button_name(soup)
            event_target = None if submit_name else guess_event_target_for_export(soup)

            r_export = post_export(
                session=sess,
                action_url=action_url,
                tokens=tokens,
                period_params=periodo_names,
                submit_name=submit_name,
                event_target=event_target,
            )

            out_dir = Path("reports") / datetime.now().strftime("%Y-%m")
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / f"relatorio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"  # ajuste extensão se necessário

            saved = save_download_response(sess, r_export, action_url, out_file)
            st.success(f"✅ Relatório salvo em: {saved}")
            with open(saved, "rb") as f:
                st.download_button("Baixar relatório", data=f.read(), file_name=saved.name)

            if devmode:
                st.info(f"submit_name: {submit_name} | event_target: {event_target}")

        except Exception as e:
            st.error(f"❌ Falha ao baixar relatório: {e}")
