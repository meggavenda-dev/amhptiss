
# automation/reports.py
import re
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from .aspnet import extract_tokens, form_action_url, build_postback_payload

def open_reports_page(session: requests.Session, url: str, timeout: int = 60):
    r = session.get(url, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"Falha ao abrir página de relatórios: {url}")
    tokens, soup = extract_tokens(r.text)
    action_url = form_action_url(url, soup, default=url)
    return tokens, soup, action_url

def guess_export_button_name(soup: BeautifulSoup):
    """
    Tenta achar um input/button com 'Exportar'/'Gerar'/'Relatório'.
    Se não houver name, retornamos None (talvez seja necessário __EVENTTARGET).
    """
    for inp in soup.find_all("input", {"type":"submit"}):
        meta = (inp.get("value","") + " " + inp.get("id","") + " " + inp.get("name","")).lower()
        if any(k in meta for k in ["exportar","gerar","relatório","relatorio","csv","pdf","excel"]):
            return inp.get("name")
    for btn in soup.find_all("button"):
        meta = (btn.get_text(" ") + " " + btn.get("id","") + " " + btn.get("name","")).lower()
        if any(k in meta for k in ["exportar","gerar","csv","pdf","excel"]):
            return btn.get("name")
    return None

def guess_event_target_for_export(soup: BeautifulSoup):
    """
    Em ASP.NET, alguns botões não têm 'name', e o postback é feito via __EVENTTARGET com o 'id' do controle.
    Tentamos localizar um id sugestivo (ex.: btnExport, lnkExport).
    """
    candidates = []
    for tag in soup.find_all(["input","button","a"]):
        tid = tag.get("id","").lower()
        txt = (tag.get("value","") + " " + tag.get_text(" ") + " " + tid).lower()
        if any(k in txt for k in ["export","exportar","gerar","relatorio","relatório","csv","pdf","excel"]):
            if tid:
                candidates.append(tid)
    # Preferir nomes com 'export'
    for c in candidates:
        if "export" in c:
            return c
    return candidates[0] if candidates else None

def post_export(session: requests.Session, action_url: str, tokens: dict, period_params: dict, timeout: int = 60, submit_name: str = None, event_target: str = None):
    """
    Dispara export via POST: ou usando 'submit_name' (input/button com name), ou via postback '__EVENTTARGET'.
    """
    if submit_name:
        payload = {**{k:v for k,v in tokens.items() if v}, **(period_params or {}), submit_name: "Exportar"}
    else:
        payload = build_postback_payload(tokens, extras=(period_params or {}), event_target=event_target)

    r = session.post(action_url, data=payload, timeout=timeout, allow_redirects=True)
    return r

def save_download_response(session: requests.Session, response: requests.Response, action_url: str, out_path: Path):
    """
    Salva binário direto (PDF/CSV/XLS) ou segue link ... para baixar de fato.
    """
    ct = response.headers.get("Content-Type","").lower()
    content = response.content
    # Heurísticas para detectar PDF/binário
    if "application" in ct or content[:4] in [b"%PDF"]:
        out_path.write_bytes(content)
        return out_path

    # Tentar achar link para arquivo
    soup = BeautifulSoup(response.text, "html.parser")
    link = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"\.(pdf|csv|xls|xlsx)$", href, re.I):
            link = urljoin(action_url, href)
            break
    if link:
        r_file = session.get(link, timeout=60)
        out_path.write_bytes(r_file.content)
        return out_path

    # Às vezes o arquivo vem via window.location/href numa tag <script>; tentar regex simples
    for script in soup.find_all("script"):
        if script.string:
            m = re.search(r'"\')["\']', script.string, re.I)
            if m:
                dl = urljoin(action_url, m.group(1))
                r_file = session.get(dl, timeout=60)
                out_path.write_bytes(r_file.content)
                return out_path

    raise RuntimeError("Não encontrei binário nem link de download no response.")
