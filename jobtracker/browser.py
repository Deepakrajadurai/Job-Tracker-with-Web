"""Method B: render JavaScript pages in a headless browser (optional, needs Playwright).

Used only as a second attempt for pages that were already fetched successfully over HTTP
(so robots.txt allowed them and the site answered) but contained no job data in the static
HTML. It does not log in, solve challenges or disguise itself.

Install once:  pip install playwright  &&  playwright install chromium
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class BrowserUnavailable(RuntimeError):
    pass


def render_pages(urls: list[str], browser_cfg: dict, user_agent: str) -> dict[str, str]:
    """Return {url: rendered HTML}. Pages that fail to load are left out and logged."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserUnavailable("Playwright is not installed (pip install playwright, "
                                 "then playwright install chromium)") from exc
    timeout_ms = int(float(browser_cfg.get("timeout", 30)) * 1000)
    rendered: dict[str, str] = {}
    try:
        with sync_playwright() as pw:
            launch = {"headless": True}
            if browser_cfg.get("channel"):
                launch["channel"] = browser_cfg["channel"]       # e.g. "chrome" to use an installed Chrome
            browser = pw.chromium.launch(**launch)
            try:
                context = browser.new_context(user_agent=user_agent, locale="de-DE")
                for url in urls:
                    page = context.new_page()
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                        try:
                            page.wait_for_load_state("networkidle", timeout=timeout_ms)
                        except PlaywrightError:
                            pass                                  # busy pages never go idle, use what loaded
                        page.wait_for_timeout(int(float(browser_cfg.get("settle_seconds", 1.5)) * 1000))
                        rendered[url] = page.content()
                    except PlaywrightError as exc:
                        logger.warning("browser could not render page",
                                       extra={"source": "browser", "url": url, "error": str(exc)[:200]})
                    finally:
                        page.close()
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise BrowserUnavailable(f"browser could not be started: {str(exc)[:200]}") from exc
    return rendered
