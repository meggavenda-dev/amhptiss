
# --- Import safety patch: garante acesso ao pacote 'automation' ---
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT_CANDIDATES = [APP_DIR, APP_DIR.parent]
for candidate in REPO_ROOT_CANDIDATES:
    if (candidate / "automation").exists():
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
        break

# --- App ---
import os
from pathlib import Path as _Path
from datetime import datetime

import streamlit as st
import requests

from automation.amhp import login_portal, access_amhptiss
from automation.reports import (
    open_reports_page, guess_export_button_name, guess_event_target_for_export,
    post_export, save_download_response
)

st.set_page_config(page_title="AMHP – HTTP Pura", page_icon="🔐", layout="centered")
st.title("🔐 AMHP – Login HTTP Pura, acesso ao AMHPTISS e download de relatórios")

PORTAL_URL = "https://portal.amhp.com.br/"
AMHPTISS_URL = "https://amhptiss.amhp.com.br/Default.aspx"

# Ajuste para a página real de relatório (você pode sobrescrever na UI)
DEFAULT_REPORTS_PAGE = "https://amhptiss.amhp.com.br/Relatorios/ProducaoMensal.aspx"

# Credenciais (secrets ou inputs)
user_default = st.secrets.get("AMHP_USER", "")
pass_default = st.secrets.get("AMHP_PASS", "")

with st.form("login_form"):
    c1, c2 = st.columns(2)
    with c1:
        username = st.text_input("Usuário", value=user_default)
    with c2:
        password = st.text_input("Senha", value=pass_default, type="password")

    with st.expander("⚙️ Avançado (names do formulário ASP.NET)"):
        manual_user_name = st.text_input(
            "Name do campo de usuário (ex.: ctl00$MainContent$txtUsuario)",
            value=st.secrets.get("AMHP_USER_FIELD", "")
        )
        manual_pass_name = st.text_input(
            "Name do campo de senha (ex.: ctl00$MainContent$txtSenha)",
            value=st.secrets.get("AMHP_PASS_FIELD", "")
        )
        manual_submit_name = st.text_input(
            "Name do botão submit (opcional; ex.: ctl00$MainContent$btnEntrar)",
            value=st.secrets.get("AMHP_SUBMIT_FIELD", "")
        )

    devmode = st.checkbox("Modo desenvolvedor (mostrar HTML/diagnóstico)", value=False)
    submit_login = st.form_submit_button("Fazer login")

if submit_login:
    if not username or not password:
        st.error("Informe usuário e senha (ou configure em `.streamlit/secrets.toml`).")
        st.stop()

    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (Streamlit/HTTP)"})

    ok, r_post, reason = login_portal(
        sess,
        username.strip(),
        password.strip(),
        user_field_override=(manual_user_name or None),
        pass_field_override=(manual_pass_name or None),
        submit_name_override=(manual_submit_name or None),
    )

    if not ok:
        st.error("❌ Falha no login.")
        if reason:
            with st.expander("🪪 Diagnóstico do formulário / razão"):
                st.code(reason, language="text")
        if devmode and r_post is not None:
            with st.expander("HTML pós-login (trecho)"):
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

    # ------------------- Relatórios -------------------
    st.markdown("---")
    st.subheader("📄 Baixar relatório")
    reports_page_url = st.text_input("URL da página de relatório", value=DEFAULT_REPORTS_PAGE)

    c3, c4 = st.columns(2)
    with c3:
        mes = st.selectbox("Mês", [f"{i:02d}" for i in range(1, 13)], index=datetime.now().month - 1)
    with c4:
        ano = st.text_input("Ano", value=str(datetime.now().year))

    st.caption("⚠️ Ajuste os *names* abaixo conforme a página de relatório ASP.NET.")
    with st.expander("⚙️ Names dos campos de período (exemplos)"):
        st.write("Ex.: ctl00$MainContent$cmbMes  /  ctl00$MainContent$cmbAno")
    period_mes_name = st.text_input("Name do campo Mês", value="ctl00$MainContent$cmbMes")
    period_ano_name = st.text_input("Name do campo Ano", value="ctl00$MainContent$cmbAno")

    if st.button("Gerar/Exportar"):
        try:
            tokens, soup, action_url = open_reports_page(sess, reports_page_url)
            submit_name = guess_export_button_name(soup)
            event_target = None if submit_name else guess_event_target_for_export(soup)

            # Montar parâmetros de período com os names definidos
            period_params = {
                period_mes_name: mes,
                period_ano_name: ano,
            }

            r_export = post_export(
                session=sess,
                action_url=action_url,
                tokens=tokens,
                period_params=period_params,
                submit_name=submit_name,
                event_target=event_target,
            )

            out_dir = _Path("reports") / datetime.now().strftime("%Y-%m")
            out_dir.mkdir(parents=True, exist_ok=True)
            # Ajuste a extensão conforme o formato real retornado
            out_file = out_dir / f"relatorio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

            saved = save_download_response(sess, r_export, action_url, out_file)
            st.success(f"✅ Relatório salvo em: {saved}")
            with open(saved, "rb") as f:
                st.download_button("Baixar relatório", data=f.read(), file_name=saved.name)

            if devmode:
                st.info(f"submit_name: {submit_name} | event_target: {event_target}")
        except Exception as e:
            st.error(f"❌ Falha ao baixar relatório: {e}")

