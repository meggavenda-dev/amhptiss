
# automation/login.py
import asyncio
import re
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

LOGIN_URL = "https://portal.amhp.com.br/"
TISS_URL  = "https://amhptiss.amhp.com.br/Default.aspx"

async def ensure_playwright_installed() -> None:
    """
    Tenta instalar o Chromium do Playwright em tempo de execução.
    Útil para Streamlit Cloud ou ambientes novos.
    Executa apenas se necessário; custo ~ alguns segundos na primeira vez.
    """
    try:
        # Verifica se já consegue importar o driver baixado
        from playwright._impl._driver import compute_driver_executable
        _ = compute_driver_executable()
    except Exception:
        import subprocess, sys
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True
        )

async def amhp_login(
    username: str,
    password: str,
    headless: bool = True,
    persist_state_path: Optional[str] = None,
    first_screenshot: Optional[str] = None,
    tiss_screenshot: Optional[str] = None,
    nav_timeout_ms: int = 45000,
) -> Dict[str, Any]:
    """
    Faz login no portal AMHP e tenta acessar o AMHPTISS com a mesma sessão.

    Retorna:
      {
        "ok": bool,
        "reason": Optional[str],
        "logged_url": str,
        "tiss_access_ok": bool,
        "tiss_url": str
      }
    """
    await ensure_playwright_installed()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        context = await browser.new_context()
        page = await context.new_page()

        try:
            # 1) Abre o portal e aguarda DOM
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=nav_timeout_ms)

            # 2) Preenche usuário
            filled_user = False
            # Tente por label
            locators_user = [
                page.get_by_label(re.compile("usu[aá]rio|login|cpf", re.I)),
                page.get_by_placeholder(re.compile("usu[aá]rio|login|cpf", re.I)),
                page.locator('input[type="text"]'),
                page.locator('input[name*="User" i]'),
            ]
            for loc in locators_user:
                if await loc.count() > 0:
                    await loc.first.fill(username)
                    filled_user = True
                    break

            # 3) Preenche senha
            filled_pass = False
            locators_pass = [
                page.get_by_label(re.compile("senha|password", re.I)),
                page.get_by_placeholder(re.compile("senha|password", re.I)),
                page.locator('input[type="password"]'),
                page.locator('input[name*="Pass" i]'),
            ]
            for loc in locators_pass:
                if await loc.count() > 0:
                    await loc.first.fill(password)
                    filled_pass = True
                    break

            if first_screenshot:
                await page.screenshot(path=first_screenshot, full_page=True)

            if not (filled_user and filled_pass):
                await browser.close()
                return {
                    "ok": False,
                    "reason": "Não localizei os campos de usuário/senha. Ajuste os seletores.",
                    "logged_url": page.url,
                    "tiss_access_ok": False,
                    "tiss_url": "",
                }

            # 4) Clica no botão "Entrar"/"Acessar"/"Login" ou envia ENTER
            clicked = False
            btn_candidates = [
                page.get_by_role("button", name=re.compile("entrar|acessar|login|ok", re.I)),
                page.get_by_role("link", name=re.compile("entrar|acessar|login|ok", re.I)),
                page.locator('button[type="submit"]'),
                page.locator('input[type="submit"]'),
            ]
            for loc in btn_candidates:
                if await loc.count() > 0:
                    await loc.first.click()
                    clicked = True
                    break
            if not clicked:
                await page.keyboard.press("Enter")

            # 5) Aguarda mudança plausível de página/estado
            try:
                await page.wait_for_load_state("networkidle", timeout=nav_timeout_ms)
            except PlaywrightTimeoutError:
                pass  # alguns portais mantêm conexões em aberto

            # 6) Heurísticas de sucesso de login
            html = (await page.content()).lower()
            login_ok = (
                ("sair" in html or "logoff" in html or "minha conta" in html) or
                ("erro" not in html and "senha" not in html and "usuário" not in html and "usuario" not in html and "login" not in html and LOGIN_URL not in page.url.lower())
            )

            if not login_ok:
                await browser.close()
                return {
                    "ok": False,
                    "reason": "Falha no login (credenciais inválidas ou mudança no fluxo).",
                    "logged_url": page.url,
                    "tiss_access_ok": False,
                    "tiss_url": "",
                }

            # 7) Persiste estado (cookies/localStorage) se solicitado
            if persist_state_path:
                await context.storage_state(path=persist_state_path)

            # 8) Acessa o AMHPTISS com a mesma sessão
            await page.goto(TISS_URL, wait_until="domcontentloaded", timeout=nav_timeout_ms)

            # Alguns SSO redirecionam; aceita estar numa URL diferente se autenticado
            current_url = page.url
            tiss_html = (await page.content()).lower()
            tiss_access_ok = (
                ("tiss" in current_url.lower() or "default.aspx" in current_url.lower()) and
                ("login" not in tiss_html and "senha" not in tiss_html)
            )

            if tiss_screenshot:
                await page.screenshot(path=tiss_screenshot, full_page=True)

            await browser.close()
            return {
                "ok": True,
                "reason": None,
                "logged_url": current_url,
                "tiss_access_ok": tiss_access_ok,
                "tiss_url": current_url,
            }

        except Exception as e:
            await browser.close()
            return {
                "ok": False,
                "reason": f"Exceção: {e}",
                "logged_url": "",
                "tiss_access_ok": False,
                "tiss_url": "",
            }
