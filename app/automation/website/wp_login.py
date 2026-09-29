"""
Shared WordPress access for the website automations (cache clear, update check).

The site sits behind a Cloudflare "Verify you are human" check that won't let
a script-launched browser log in. So instead of logging in, the automations
attach to a Chrome (or Edge) window that YOU keep open and logged in:

  1. Website tab -> "Open WordPress Browser" starts Chrome with its own
     profile (separate from your everyday Chrome) and remote debugging on
     localhost only.
  2. You log in there as the automation user (tick "Remember Me") and leave
     that window open.
  3. Each run opens a new tab in that window, does its work, and closes
     the tab. Your logged-in tab is never touched.

If the run can't reach wp-admin (logged out, Cloudflare check, WordPress
asking for something), it fails and leaves its tab open on that page so you
can see what WordPress wants.
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from app.paths import DATA_DIR

# Remote-debugging port for the WordPress browser. Chrome binds it to
# 127.0.0.1 only, so it isn't reachable from other machines.
CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"

# Dedicated profile: keeps the automation login separate from your everyday
# Chrome, and Chrome (136+) refuses remote debugging on the default profile.
PROFILE_DIR = DATA_DIR / "wp-browser-profile"


class WordPressBrowserError(RuntimeError):
    """The WordPress browser isn't open or isn't logged in."""


# ---------------------------------------------------------------------------
# Browser discovery / launch
# ---------------------------------------------------------------------------

def find_browser() -> Optional[str]:
    """Path to Chrome, falling back to Edge. None if neither is found."""
    candidates = []
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(env)
        if root:
            candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
        root = os.environ.get(env)
        if root:
            candidates.append(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    for c in candidates:
        if c.exists():
            return str(c)
    for name in ("chrome", "google-chrome", "chromium", "msedge", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def is_browser_open() -> bool:
    """True if the WordPress browser is running with remote debugging on CDP_PORT."""
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=1.5) as r:
            return "webSocketDebuggerUrl" in json.loads(r.read().decode("utf-8"))
    except Exception:
        return False


def open_browser(url: str) -> str:
    """Start the WordPress browser at `url`. Returns the browser path used."""
    exe = find_browser()
    if not exe:
        raise WordPressBrowserError(
            "Couldn't find Chrome or Edge on this computer to open the WordPress browser."
        )
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    args = [
        exe,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]
    kwargs = {}
    if sys.platform == "win32":
        # Detach so the browser stays open when Automation Assist closes.
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
    return exe


# ---------------------------------------------------------------------------
# Attach + check we're in wp-admin
# ---------------------------------------------------------------------------

def is_cloudflare_challenge(page) -> bool:
    """True when the page is Cloudflare's 'Performing security verification' page."""
    try:
        if "just a moment" in (page.title() or "").lower():
            return True
    except Exception:
        pass
    try:
        body = page.locator("body").inner_text(timeout=1000).lower()
        return (
            "performing security verification" in body
            or "verify you are human" in body
        )
    except Exception:
        return False


def _describe_page(page) -> str:
    try:
        title = page.title()
    except Exception:
        title = "?"
    return f"URL: {page.url} | title: {title!r}"


def open_admin_tab(pw, base: str, log: Callable[[str], None]):
    """
    Attach to the WordPress browser and open a new tab on wp-admin.
    Returns (browser, page). Raises WordPressBrowserError if the browser
    isn't open or the tab doesn't end up in wp-admin.
    """
    if not is_browser_open():
        open_browser(f"{base}/wp-login.php")  # raises if Chrome/Edge not found
        raise WordPressBrowserError(
            "The WordPress browser wasn't open. It's opening now — "
            "log in there as the automation user (tick 'Remember Me'), "
            "leave the window open, then run this again."
        )

    log("Attaching to the WordPress browser...")
    browser = pw.chromium.connect_over_cdp(CDP_URL)
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()

    admin_url = f"{base}/wp-admin/"
    log(f"Opening {admin_url}")
    page.goto(admin_url, wait_until="domcontentloaded", timeout=45000)

    # Path only — the login URL can carry redirect_to=.../wp-admin.
    if "/wp-admin" not in urlparse(page.url).path or is_cloudflare_challenge(page):
        # Leave this tab open on whatever WordPress/Cloudflare is showing.
        try:
            page.bring_to_front()
        except Exception:
            pass
        reason = (
            "a Cloudflare check is showing" if is_cloudflare_challenge(page)
            else "WordPress isn't logged in or is asking for something"
        )
        raise WordPressBrowserError(
            f"Couldn't reach wp-admin: {reason}. Check the WordPress browser "
            f"window (the tab is left open), log in if needed, then run again. "
            + _describe_page(page)
        )

    log("Logged in (using the WordPress browser).")
    return browser, page


def close_tab(page) -> None:
    """Close only the tab this run opened; leave the browser and your tab alone."""
    try:
        page.close()
    except Exception:
        pass
