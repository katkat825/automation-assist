"""
Shared WordPress login for the website automations (cache clear, update check).

The site sits behind a Cloudflare "Verify you are human" check on
wp-login.php. That check is meant to be completed by a person, so these
automations run in a visible (headed) browser and, when the check appears,
wait for you to click it. Nothing here tries to solve it automatically.

Any login that doesn't end on wp-admin is reported as a failure that says
what page the browser ended up on. That's on purpose: a failed login is the
signal to go and look at what WordPress wants (admin-email confirmation,
security notices, etc.).
"""

import time
from typing import Callable
from urllib.parse import urlparse

# How long to wait for a person to complete the Cloudflare check.
CHALLENGE_WAIT_S = 300
# How long to wait for the login form / redirect when no check is showing.
NORMAL_WAIT_S = 30


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
    detail = f"URL: {page.url} | title: {title!r}"
    try:
        err = page.locator("#login_error")
        if err.count():
            detail += f" | WordPress says: {err.first.inner_text().strip()}"
    except Exception:
        pass
    return detail


def login(page, base: str, username: str, password: str,
          log: Callable[[str], None]) -> None:
    """Log into WordPress at `base` (no trailing /wp-admin). Raises RuntimeError on failure."""
    login_url = f"{base}/wp-login.php"
    log(f"Logging in: {login_url}")
    page.goto(login_url, wait_until="domcontentloaded")

    # --- Get to the username/password form ---
    # GoDaddy-hosted WP shows an SSO form that hides the standard fields behind
    # a toggle rendered by JS after load, so poll: click the toggle whenever it
    # appears, and give a person time to clear the Cloudflare check if it shows.
    challenge_seen = False
    deadline = time.time() + NORMAL_WAIT_S
    while True:
        try:
            if page.locator("#user_login").is_visible():
                break
        except Exception:
            pass

        if is_cloudflare_challenge(page):
            if not challenge_seen:
                challenge_seen = True
                deadline = time.time() + CHALLENGE_WAIT_S
                log(
                    "Cloudflare check is showing. Click 'Verify you are human' in "
                    f"the browser window (waiting up to {CHALLENGE_WAIT_S // 60} min)..."
                )
        else:
            try:
                toggle = page.locator(".wpaas-sso-login-toggle")
                if toggle.count() and toggle.first.is_visible():
                    toggle.first.click()
            except Exception:
                pass

        if time.time() > deadline:
            if challenge_seen and is_cloudflare_challenge(page):
                raise RuntimeError(
                    f"Login blocked: the Cloudflare check wasn't completed within "
                    f"{CHALLENGE_WAIT_S // 60} minutes."
                )
            raise RuntimeError(
                "Login form never appeared. " + _describe_page(page)
            )
        page.wait_for_timeout(500)

    if challenge_seen:
        log("Cloudflare check passed.")

    page.locator("#user_login").fill(username)
    page.locator("#user_pass").fill(password)
    page.locator("#user_pass").press("Enter")

    # --- Wait until we're actually in wp-admin ---
    # Anything else (admin-email confirmation, login error, another Cloudflare
    # check that nobody clicked) ends as a failure describing the page.
    challenge_seen = False
    deadline = time.time() + NORMAL_WAIT_S
    while True:
        # Check the path only — the login URL can carry redirect_to=.../wp-admin.
        if "/wp-admin" in urlparse(page.url).path:
            break
        if is_cloudflare_challenge(page) and not challenge_seen:
            challenge_seen = True
            deadline = time.time() + CHALLENGE_WAIT_S
            log(
                "Cloudflare check is showing again after login. Click "
                "'Verify you are human' in the browser window..."
            )
        if time.time() > deadline:
            raise RuntimeError(
                "Login didn't reach wp-admin — check what WordPress is asking for. "
                + _describe_page(page)
            )
        page.wait_for_timeout(500)

    log("Login successful.")
