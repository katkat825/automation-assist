from datetime import datetime
from typing import Callable, Optional

from playwright.sync_api import sync_playwright

from app.automation.website import wp_login


def clear_wp_cache(
    wp_admin_url: str,
    username: str,
    password: str,
    headless: bool = False,
    log: Optional[Callable[[str], None]] = None,
):
    def _log(msg: str):
        if log:
            log(msg)

    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    base = wp_admin_url.rstrip("/")
    if base.endswith("/wp-admin"):
        base = base[: -len("/wp-admin")]

    try:
        with sync_playwright() as pw:
            # Always headed: the Cloudflare check on wp-login.php needs a person
            # to click it. `headless` is kept for call compatibility but ignored.
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            # --- LOGIN (headed; you may need to click the Cloudflare check) ---
            wp_login.login(page, base, username, password, _log)

            # --- ELEMENTOR CACHE ---
            tools_url = f"{base}/wp-admin/admin.php?page=elementor-tools"
            _log("Opening Elementor Tools...")
            page.goto(tools_url, wait_until="domcontentloaded")

            page.wait_for_selector("#elementor-clear-cache-button", timeout=10000)

            _log("Clearing Elementor cache...")
            page.click("#elementor-clear-cache-button")

            # wait until button re-enabled
            page.wait_for_function(
                "document.querySelector('#elementor-clear-cache-button').disabled === false",
                timeout=20000
            )

            _log("Elementor cache cleared.")

            # --- WP ENGINE CACHE ---
            # GoDaddy's admin bar nests the Flush Cache link inside a "GoDaddy
            # Quick Links" submenu. Rather than navigating that menu UI (which
            # GoDaddy changes periodically), read the flush URL — including its
            # per-page nonce — straight from the DOM and navigate to it.
            _log("Flushing site cache...")

            flush_href = page.locator(
                "a[href*='wpaas_action=flush_cache']"
            ).first.get_attribute("href")

            if not flush_href:
                raise RuntimeError("Could not find GoDaddy flush-cache link in admin bar.")

            if flush_href.startswith("/"):
                flush_href = f"{base}{flush_href}"

            page.goto(flush_href, wait_until="domcontentloaded")

            _log("Site cache flushed.")

            browser.close()

            return {
                "checked_at": checked_at,
                "success": True
            }

    except Exception as e:
        _log(f"Error: {e}")
        return {
            "checked_at": checked_at,
            "success": False,
            "error": str(e)
        }
    