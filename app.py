
import streamlit as st
import requests
from bs4 import BeautifulSoup

LOGIN_URL = "https://portal.amhp.com.br/"
TISS_URL = "https://amhptiss.amhp.com.br/Default.aspx"

st.title("Login AMHP e acesso ao AMHPTISS")

user = st.text_input("Usuário")
pwd = st.text_input("Senha", type="password")

if st.button("Fazer login"):
    if not user or not pwd:
        st.error("Informe usuário e senha.")
        st.stop()

    try:
        session = requests.Session()
        # 1) Obter página de login para capturar VIEWSTATE
        resp = session.get(LOGIN_URL)
        soup = BeautifulSoup(resp.text, "html.parser")

        viewstate = soup.find("input", {"name": "__VIEWSTATE"})["value"]
        eventvalidation = soup.find("input", {"name": "__EVENTVALIDATION"})["value"]

        # 2) Montar payload do login (ajuste os nomes dos campos conforme HTML real)
        payload = {
            "__VIEWSTATE": viewstate,
            "__EVENTVALIDATION": eventvalidation,
            "txtUsuario": user,
            "txtSenha": pwd,
            "btnEntrar": "Entrar"
        }

        # 3) Enviar POST para login
        login_resp = session.post(LOGIN_URL, data=payload)

        if "sair" in login_resp.text.lower() or "logoff" in login_resp.text.lower():
            st.success("Login realizado com sucesso!")
            # 4) Acessar AMHPTISS
            tiss_resp = session.get(TISS_URL)
            if "login" not in tiss_resp.text.lower():
                st.success("Acesso ao AMHPTISS OK!")
                st.write("Página AMHPTISS carregada.")
            else:
                st.warning("Não conseguiu acessar AMHPTISS automaticamente. Pode exigir clique ou SSO.")
        else:
            st.error("Falha no login. Verifique credenciais ou fluxo.")
    except Exception as e:
        st.error(f"Erro: {e}")
