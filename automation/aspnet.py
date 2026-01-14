# automation/aspnet.py
from bs4 import BeautifulSoup
from urllib.parse import urljoin

def extract_tokens(html_text: str):
    """
    Extrai tokens ASP.NET: __VIEWSTATE, __EVENTVALIDATION, __VIEWSTATEGENERATOR.
    Retorna (tokens_dict, soup).
    """
    soup = BeautifulSoup(html_text, "html.parser")
    def val(name):
        tag = soup.find("input", {"name": name})
        return tag["value"] if tag and tag.has_attr("value") else None
    tokens = {
        "__VIEWSTATE": val("__VIEWSTATE"),
        "__EVENTVALIDATION": val("__EVENTVALIDATION"),
        "__VIEWSTATEGENERATOR": val("__VIEWSTATEGENERATOR"),
    }
    return tokens, soup

def form_action_url(base_url: str, soup: BeautifulSoup, default: str = None):
    """
    Obtém a action do <form>, resolvendo contra a base.
    """
    form = soup.find("form")
    if form and form.get("action"):
        return urljoin(base_url, form.get("action"))
    return default or base_url

def find_login_fields(soup: BeautifulSoup):
    """
    Heurística para identificar campos de login (usuário/senha) e botão.
    Retorna (user_field_name, pass_field_name, submit_name, submit_value).
    Ajuste se souber os names/IDs exatos.
    """
    text_inputs = soup.find_all("input", {"type": "text"})
    pass_inputs = soup.find_all("input", {"type": "password"})
    submit_inputs = soup.find_all("input", {"type": "submit"}) + soup.find_all("button", {"type": "submit"})

    def pick_user():
        for inp in text_inputs:
            meta = " ".join([inp.get("id",""), inp.get("name",""), inp.get("placeholder","")]).lower()
            if any(k in meta for k in ["usuario","usuário","login","cpf","email","e-mail"]):
                return inp.get("name") or inp.get("id")
        return text_inputs[0].get("name") if text_inputs else None

    def pick_pass():
        for inp in pass_inputs:
            meta = " ".join([inp.get("id",""), inp.get("name",""), inp.get("placeholder","")]).lower()
            if any(k in meta for k in ["senha","password"]):
                return inp.get("name") or inp.get("id")
        return pass_inputs[0].get("name") if pass_inputs else None

    def pick_submit():
        for inp in submit_inputs:
            nm = inp.get("name")
            val = inp.get("value","Entrar")
            meta = " ".join([inp.get("id",""), nm or "", val]).lower()
            if any(k in meta for k in ["entrar","acessar","login","ok","submit"]):
                return nm, val
        return None, None

    u = pick_user()
    p = pick_pass()
    submit_name, submit_value = pick_submit()
    return u, p, submit_name, submit_value

def build_postback_payload(tokens: dict, extras: dict = None, event_target: str = None, event_argument: str = ""):
    """
    Monta payload típico de postback ASP.NET.
    Se o botão não tem name (apenas id/control), use __EVENTTARGET com o id do controle server-side.
    """
    payload = {}
    for k, v in (tokens or {}).items():
        if v is not None:
            payload[k] = v
    if event_target:
        payload["__EVENTTARGET"] = event_target
        payload["__EVENTARGUMENT"] = event_argument or ""
    if extras:
        payload.update(extras)
    return payload
