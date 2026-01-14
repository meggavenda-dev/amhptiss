
# app.py
import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

PORTAL_URL = "https://portal.amhp.com.br/"
AMHPTISS_URL = "https://amhptiss.amhp.com.br/Default.aspx"

st.set_page_config(page_title="AMHP – HTTP Login", page_icon="🔐", layout="centered")
st.title("🔐 AMHP – Login HTTP Puro e acesso ao AMHPTISS")

# --- Utilitários ASP.NET ---
def extract_aspnet_tokens(html_text):
    """Extrai __VIEWSTATE, __EVENTVALIDATION e __VIEWSTATEGENERATOR se existirem."""
    soup = BeautifulSoup(html_text, "html.parser")
    tokens = {}
    for name in ["__VIEWSTATE", "__EVENTVALIDATION", "__VIEWSTATEGENERATOR"]:
        tag = soup.find("input", {"name": name})
        tokens[name] = tag["value"] if tag and tag.has_attr("value") else None
    return tokens, soup

def find_login_fields(soup):
    """
    Descobre nomes dos campos de usuário, senha e botão no formulário de login,
    de forma heurística. Ajuste se souber os IDs exatos.
    """
    text_inputs = soup.find_all("input", {"type": "text"})
    pass_inputs = soup.find_all("input", {"type": "password"})
    submit_inputs = soup.find_all("input", {"type": "submit"}) + soup.find_all("button", {"type": "submit"})

    # Heurísticas por id/name/placeholder/label
    def candidate_username():
        for inp in text_inputs:
            txt = " ".join([inp.get("id",""), inp.get("name",""), inp.get("placeholder","")]).lower()
            if any(k in txt for k in ["usuario", "usuário", "login", "cpf", "email", "e-mail"]):
                return inp.get("name") or inp.get("id")
        # fallback: primeiro input text
        return text_inputs[0].get("name") if text_inputs else None

    def candidate_password():
        for inp in pass_inputs:
            txt = " ".join([inp.get("id",""), inp.get("name",""), inp.get("placeholder","")]).lower()
            if any(k in txt for k in ["senha", "password"]):
                return inp.get("name") or inp.get("id")
        return pass_inputs[0].get("name") if pass_inputs else None

    def candidate_submit_name_value():
        # retorna (name, value) para input/button de submit
        for inp in submit_inputs:
            nm = inp.get("name")
            val = inp.get("value","Entrar")
            txt = " ".join([inp.get("id",""), nm or "", val]).lower()
            if any(k in txt for k in ["entrar","acessar","login","ok","submit"]):
                return nm, val
        # fallback: se não há submit explícito, alguns ASP.NET disparam post via JS; use None
        return None, None

    return candidate_username(), candidate_password(), candidate_submit_name_value()

def login_portal(session: requests.Session, username: str, password: str, timeout=30):
    """Executa GET no login (para tokens) e POST com credenciais."""
    # 1) GET portal
    r_get = session.get(PORTAL_URL, timeout=timeout)
    if r_get.status_code != 200:
        return False, "Falha ao abrir portal (GET).", r_get

    tokens, soup = extract_aspnet_tokens(r_get.text)
    user_field, pass_field, (submit_name, submit_value) = find_login_fields(soup)

    if not user_field or not pass_field:
        return False, "Não identifiquei campos de usuário/senha. Ajuste find_login_fields().", r_get

    # 2) Monta payload do POST
    payload = {
        user_field: username,
        pass_field: password,
    }
    # ASP.NET tokens
    for k, v in tokens.items():
        if v is not None:
            payload[k] = v
    # Botão de submit se existir name
    if submit_name:
        payload[submit_name] = submit_value or "Entrar"

    # 3) POST ao mesmo endpoint (alguns portais têm action diferente; se houver form action, use urljoin)
    form = soup.find("form")
    action_url = PORTAL_URL
    if form and form.get("action"):
        action_url = urljoin(PORTAL_URL, form.get("action"))
    r_post = session.post(action_url, data=payload, timeout=timeout, allow_redirects=True)

    # 4) Heurística de login OK
    html_lower = r_post.text.lower()
    ok = (
        ("sair" in html_lower or "logoff" in html_lower or "minha conta" in html_lower) or
        ("senha" not in html_lower and "usuário" not in html_lower and "usuario" not in html_lower and "login" not in html_lower)
    )
    return ok, None if ok else "Falha no login (credenciais ou fluxo).", r_post

def access_amhptiss(session: requests.Session, timeout=30):
    """Tenta acessar AMHPTISS com a mesma sessão."""
    r = session.get(AMHPTISS_URL, timeout=timeout, allow_redirects=True)
    html_lower = r.text.lower()
    # Sucesso simples: não mostrou tela de login
    success = ("login" not in html_lower and "senha" not in html_lower)
    return success, r

# --- UI ---
with st.form("login_form"):
    col1, col2 = st.columns(2)
    with col1:
        user = st.text_input("Usuário")
    with col2:
        pwd = st.text_input("Senha", type="password")
    submit = st.form_submit_button("Fazer login e abrir AMHPTISS")

if submit:
    if not user or not pwd:
        st.error("Informe usuário e senha.")
        st.stop()

    # Sessão HTTP
    sess = requests.Session()
    # Header básico (User-Agent) ajuda em alguns portais
    sess.headers.update({"User-Agent": "Mozilla/5.0 (Streamlit/HTTP automation)"})

    ok, reason, r_login = login_portal(sess, user.strip(), pwd.strip())
    with st.expander("Resposta do login (HTML bruto)"):
        st.code(r_login.text[:5000], language="html")

    if not ok:
        st.error(f"❌ {reason or 'Falha no login.'}")
        st.stop()

    st.success("✅ Login realizado no Portal AMHP.")
    # Tenta abrir AMHPTISS
    tiss_ok, r_tiss = access_amhptiss(sess)
    if tiss_ok:
        st.success(f"✅ AMHPTISS acessado. URL final: {r_tiss.url}")
        with st.expander("HTML AMHPTISS (trecho)"):
            st.code(r_tiss.text[:5000], language="html")
    else:
        st.warning("ℹ️ AMHPTISS não confirmou sessão. Pode exigir clique/menu/SSO interno.")
        with st.expander("HTML AMHPTISS (trecho)"):
            st.code(r_tiss.text[:5000], language="html")
