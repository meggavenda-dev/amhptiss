
# automation/amhp.py
import requests
from bs4 import BeautifulSoup
from .aspnet import extract_tokens, form_action_url, find_login_fields

PORTAL_URL = "https://portal.amhp.com.br/"
AMHPTISS_URL = "https://amhptiss.amhp.com.br/Default.aspx"

def login_portal(session: requests.Session, username: str, password: str, timeout: int = 30):
    """
    Fluxo: GET portal -> extrai tokens -> descobre campos -> POST credenciais.
    Retorna (ok, response_post, reason).
    """
    r_get = session.get(PORTAL_URL, timeout=timeout)
    if r_get.status_code != 200:
        return False, r_get, "Falha ao abrir portal (GET)."

    tokens, soup = extract_tokens(r_get.text)
    user_field, pass_field, submit_name, submit_value = find_login_fields(soup)
    if not user_field or not pass_field:
        return False, r_get, "Não identifiquei campos de usuário/senha. Ajuste find_login_fields()."

    payload = {**{k:v for k,v in tokens.items() if v}, user_field: username, pass_field: password}
    if submit_name:
        payload[submit_name] = submit_value or "Entrar"

    action_url = form_action_url(PORTAL_URL, soup, default=PORTAL_URL)
    r_post = session.post(action_url, data=payload, timeout=timeout, allow_redirects=True)

    html = r_post.text.lower()
    ok = ("sair" in html or "logoff" in html or "minha conta" in html) or ("senha" not in html and "usuario" not in html and "usuário" not in html and "login" not in html)
    return ok, r_post, None if ok else "Falha no login."

def access_amhptiss(session: requests.Session, timeout: int = 30):
    """
    GET na URL do AMHPTISS com cookies da sessão autenticada.
    Retorna (ok, response).
    """
    r = session.get(AMHPTISS_URL, timeout=timeout, allow_redirects=True)
    html = r.text.lower()
    ok = ("login" not in html and "senha" not in html)
    return ok, r
