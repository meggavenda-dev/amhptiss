
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


def debug_list_inputs(soup: BeautifulSoup):
    """
    Lista todos os inputs/botões do <form> principal para diagnosticar names/ids.
    """
    lines = []
    form = soup.find("form")
    if not form:
        return "Nenhum <form> encontrado no HTML da página."
    lines.append("== Inputs do <form> ==")
    for inp in form.find_all("input"):
        lines.append(
            f"input name='{inp.get('name')}' id='{inp.get('id')}' "
            f"type='{inp.get('type')}' value='{inp.get('value')}' "
            f"placeholder='{inp.get('placeholder')}' aria-label='{inp.get('aria-label')}' title='{inp.get('title')}'"
        )
    for btn in form.find_all("button"):
        lines.append(
            f"button name='{btn.get('name')}' id='{btn.get('id')}' type='{btn.get('type')}' "
            f"text='{btn.get_text(strip=True)}' aria-label='{btn.get('aria-label')}' title='{btn.get('title')}'"
        )
    return "\n".join(lines)


def find_login_fields(soup: BeautifulSoup):
    """
    Heurística para identificar campos de login (usuário/senha) e botão.
    Retorna (user_field_name, pass_field_name, submit_name, submit_value).
    Ajuste se souber os names/IDs exatos.
    """
    form = soup.find("form") or soup
    text_like = form.find_all("input", {"type": ["text", "email"]})
    pass_inputs = form.find_all("input", {"type": "password"})
    submit_inputs = form.find_all("input", {"type": "submit"}) + form.find_all("button", {"type": "submit"})

    def score_user(inp):
        meta = " ".join([
            inp.get("id", ""), inp.get("name", ""), inp.get("placeholder", ""),
            inp.get("aria-label", ""), inp.get("title", "")
        ]).lower()
        s = 0
        if any(k in meta for k in ["usuario", "usuário", "login", "cpf", "email", "e-mail"]): s += 5
        if "user" in meta: s += 2
        if inp.get("type") == "email": s += 1
        return s

    user_field = None
    if text_like:
        text_like_sorted = sorted(text_like, key=score_user, reverse=True)
        user_field = text_like_sorted[0].get("name") or text_like_sorted[0].get("id")

    def score_pass(inp):
        meta = " ".join([
            inp.get("id", ""), inp.get("name", ""), inp.get("placeholder", ""),
            inp.get("aria-label", ""), inp.get("title", "")
        ]).lower()
        s = 0
        if any(k in meta for k in ["senha", "password"]): s += 5
        return s

    pass_field = None
    if pass_inputs:
        pass_inputs_sorted = sorted(pass_inputs, key=score_pass, reverse=True)
        pass_field = pass_inputs_sorted[0].get("name") or pass_inputs_sorted[0].get("id")

    submit_name, submit_value = None, None
    for inp in submit_inputs:
        nm = inp.get("name")
        val = inp.get("value", "Entrar")
        meta = " ".join([inp.get("id", ""), nm or "", val]).lower()
        if any(k in meta for k in ["entrar", "acessar", "login", "ok", "submit", "continuar"]):
            submit_name, submit_value = nm, val
            break

    return user_field, pass_field, submit_name, submit_value


def build_postback_payload(tokens: dict, extras: dict = None, event_target: str = None, event_argument: str = ""):
    """
    Monta payload de postback ASP.NET: tokens + __EVENTTARGET/__EVENTARGUMENT + extras.
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
