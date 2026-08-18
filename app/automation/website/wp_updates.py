"""
WordPress update checker.
Logs into WP admin with Playwright, scrapes the Updates page, and returns
a structured result. Read-only — does not apply any updates.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from playwright.sync_api import sync_playwright


@dataclass
class PluginUpdate:
    name: str
    current_version: str
    new_version: str


@dataclass
class ThemeUpdate:
    name: str
    current_version: str
    new_version: str


@dataclass
class WpUpdateResult:
    checked_at: str
    wp_core_current: Optional[str] = None
    wp_core_available: Optional[str] = None
    plugin_updates: list = field(default_factory=list)   # list[PluginUpdate]
    theme_updates: list = field(default_factory=list)    # list[ThemeUpdate]
    error: Optional[str] = None

    @property
    def has_updates(self) -> bool:
        return bool(self.wp_core_available or self.plugin_updates or self.theme_updates)

    def to_report_text(self) -> str:
        lines = [
            f"WordPress Update Check — {self.checked_at}",
            "=" * 50,
        ]
        if self.error:
            lines.append(f"\nERROR: {self.error}")
            return "\n".join(lines)

        if not self.has_updates:
            lines.append("\nNo updates available. Everything is up to date.")
            return "\n".join(lines)

        if self.wp_core_available:
            lines.append("\nWORDPRESS CORE UPDATE AVAILABLE:")
            if self.wp_core_current:
                lines.append(f"  {self.wp_core_current} → {self.wp_core_available}")
            else:
                lines.append(f"  New version: {self.wp_core_available}")

        if self.plugin_updates:
            lines.append(f"\nPLUGIN UPDATES ({len(self.plugin_updates)}):")
            for p in self.plugin_updates:
                lines.append(f"  {p.name}: {p.current_version} → {p.new_version}")

        if self.theme_updates:
            lines.append(f"\nTHEME UPDATES ({len(self.theme_updates)}):")
            for t in self.theme_updates:
                lines.append(f"  {t.name}: {t.current_version} → {t.new_version}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "checked_at": self.checked_at,
            "wp_core_current": self.wp_core_current,
            "wp_core_available": self.wp_core_available,
            "plugin_updates": [
                {"name": p.name, "current": p.current_version, "new": p.new_version}
                for p in self.plugin_updates
            ],
            "theme_updates": [
                {"name": t.name, "current": t.current_version, "new": t.new_version}
                for t in self.theme_updates
            ],
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WpUpdateResult":
        """Inverse of to_dict(). Used to replay a run stored in history.

        Keeping this next to to_dict() means to_report_text() is the single
        renderer for both live and replayed results — change the format once
        and both paths follow.
        """
        d = d or {}
        return cls(
            checked_at=d.get("checked_at", ""),
            wp_core_current=d.get("wp_core_current"),
            wp_core_available=d.get("wp_core_available"),
            plugin_updates=[
                PluginUpdate(
                    name=p.get("name", ""),
                    current_version=p.get("current", ""),
                    new_version=p.get("new", ""),
                )
                for p in (d.get("plugin_updates") or [])
            ],
            theme_updates=[
                ThemeUpdate(
                    name=t.get("name", ""),
                    current_version=t.get("current", ""),
                    new_version=t.get("new", ""),
                )
                for t in (d.get("theme_updates") or [])
            ],
            error=d.get("error"),
        )


def check_wp_updates(
    wp_admin_url: str,
    username: str,
    password: str,
    headless: bool = True,
    log: Optional[Callable[[str], None]] = None,
) -> WpUpdateResult:
    """
    Log into WordPress admin and check for available updates.
    Returns a WpUpdateResult. Never applies updates.

    Parameters
    ----------
    wp_admin_url : Base URL of the site or the /wp-admin URL — both work.
    username     : WordPress admin username or email.
    password     : WordPress admin password.
    headless     : Run Playwright browser headless (default True for scheduled runs).
    log          : Optional callback for progress messages.
    """
    def _log(msg: str):
        if log:
            log(msg)

    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Accept either "https://example.com" or "https://example.com/wp-admin"
    base = wp_admin_url.rstrip("/")
    if base.endswith("/wp-admin"):
        base = base[: -len("/wp-admin")]

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            context = browser.new_context()
            page = context.new_page()

            # --- Login ---
            login_url = f"{base}/wp-login.php"
            _log(f"Logging in: {login_url}")

            page.goto(login_url, wait_until="domcontentloaded")

            # GoDaddy-hosted WP shows an SSO form that hides the standard fields.
            # Try clicking the "log in with username and password" toggle; ignore
            # the error if the button doesn't exist on this host.
            try:
                page.locator(".wpaas-sso-login-toggle").click(timeout=4000)
            except Exception:
                pass

            # Wait for the standard login fields to become visible before filling.
            page.locator("#user_login").wait_for(state="visible", timeout=10000)
            page.locator("#user_login").fill(username)
            page.locator("#user_pass").fill(password)

            page.locator("#user_pass").press("Enter")

            page.wait_for_url("**/wp-admin/**", timeout=30000)
            _log("Login successful.")

            # --- Navigate to updates page ---
            # force-check=1 tells WordPress to bypass its cached transient and
            # re-query the update API right now, so we see all current updates.
            updates_url = f"{base}/wp-admin/update-core.php?force-check=1"
            _log(f"Navigating to {updates_url}")
            page.goto(updates_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_selector("#wpbody-content", timeout=15000)

            result = WpUpdateResult(checked_at=checked_at)

            _parse_core(page, result, _log)
            _parse_plugins(page, result, _log)
            _parse_themes(page, result, _log)

            browser.close()
            _log(
                f"Done. Core update: {'yes' if result.wp_core_available else 'no'}, "
                f"Plugins: {len(result.plugin_updates)}, "
                f"Themes: {len(result.theme_updates)}"
            )
            return result

    except Exception as e:
        _log(f"Error during WP update check: {e}")
        return WpUpdateResult(checked_at=checked_at, error=str(e))


# ---------------------------------------------------------------------------
# Internal parsers
# ---------------------------------------------------------------------------

def _parse_core(page, result: WpUpdateResult, log) -> None:
    """Extract WordPress core version info from the updates page."""
    try:
        body_text = page.text_content("#wpbody-content") or ""

        if re.search(
            r"You have the latest version|WordPress is up to date|up to date",
            body_text, re.IGNORECASE
        ):
            log("WordPress core: up to date")
            return

        # "An updated version of WordPress X.X.X is available"
        available_match = re.search(
            r"(?:updated version|new version)[^<]*?(\d+\.\d+(?:\.\d+)?)",
            body_text, re.IGNORECASE
        )
        if not available_match:
            available_match = re.search(
                r"WordPress\s+(\d+\.\d+(?:\.\d+)?)\s+is available",
                body_text, re.IGNORECASE
            )

        if available_match:
            result.wp_core_available = available_match.group(1)
            log(f"WordPress core update available: {result.wp_core_available}")

        current_match = re.search(
            r"You are using WordPress\s+(\d+\.\d+(?:\.\d+)?)"
            r"|current version[:\s]+(\d+\.\d+(?:\.\d+)?)",
            body_text, re.IGNORECASE
        )
        if current_match:
            result.wp_core_current = current_match.group(1) or current_match.group(2)

    except Exception as e:
        log(f"Warning — could not parse core version info: {e}")


def _parse_plugins(page, result: WpUpdateResult, log) -> None:
    """Extract plugin update info from the updates page."""
    try:
        # WP renders the form with name="upgrade-plugins" but no id attribute.
        form = page.query_selector('form[name="upgrade-plugins"]')
        if not form:
            log("No plugin update form found — no plugin updates.")
            return

        rows = form.query_selector_all("tbody tr")
        for row in rows:
            try:
                name_el = row.query_selector(
                    "td.plugin-title strong, td.column-plugin-title strong"
                )
                if not name_el:
                    continue

                name = name_el.inner_text().strip()
                version_els = row.query_selector_all("td.column-version")

                current_ver, new_ver = _extract_two_versions(version_els, row)

                if name:
                    result.plugin_updates.append(
                        PluginUpdate(name=name, current_version=current_ver, new_version=new_ver)
                    )
                    log(f"Plugin update: {name}  {current_ver} → {new_ver}")

            except Exception as e:
                log(f"Warning — could not parse plugin row: {e}")

    except Exception as e:
        log(f"Warning — could not parse plugin updates: {e}")


def _parse_themes(page, result: WpUpdateResult, log) -> None:
    """Extract theme update info from the updates page."""
    try:
        form = page.query_selector('form[name="upgrade-themes"]')
        if not form:
            log("No theme update form found — no theme updates.")
            return

        rows = form.query_selector_all("tbody tr")
        for row in rows:
            try:
                name_el = row.query_selector("td.plugin-title strong, td strong")
                if not name_el:
                    continue

                name = name_el.inner_text().strip()
                version_els = row.query_selector_all("td.column-version")

                current_ver, new_ver = _extract_two_versions(version_els, row)

                if name:
                    result.theme_updates.append(
                        ThemeUpdate(name=name, current_version=current_ver, new_version=new_ver)
                    )
                    log(f"Theme update: {name}  {current_ver} → {new_ver}")

            except Exception as e:
                log(f"Warning — could not parse theme row: {e}")

    except Exception as e:
        log(f"Warning — could not parse theme updates: {e}")


def _extract_two_versions(version_els, row=None) -> tuple:
    """Given a list of td.column-version elements, return (current, new) version strings.

    Falls back to scanning the full row text for version patterns if the
    dedicated version columns are absent (some WP/host setups omit them).
    WP row text is typically: "You have version X installed. Update to Y."
    """
    _ver_re = re.compile(r"(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)")

    def _extract(el) -> str:
        text = el.inner_text().strip()
        m = _ver_re.search(text)
        return m.group(1) if m else text

    if len(version_els) >= 2:
        return _extract(version_els[0]), _extract(version_els[1])
    elif len(version_els) == 1:
        return "", _extract(version_els[0])

    # Fallback: parse the row's prose text.
    # Matches patterns like "You have version 1.2.3 installed. Update to 4.5.6."
    if row:
        row_text = row.inner_text()
        installed = re.search(
            r"(?:have version|installed[:\s]+)\s*(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)",
            row_text, re.IGNORECASE
        )
        update_to = re.search(
            r"(?:update to|new version[:\s]+)\s*(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)",
            row_text, re.IGNORECASE
        )
        current = installed.group(1) if installed else ""
        new = update_to.group(1) if update_to else ""
        if not current and not new:
            # Last resort: grab first two version-like strings in order
            all_vers = _ver_re.findall(row_text)
            if len(all_vers) >= 2:
                return all_vers[0], all_vers[1]
            elif len(all_vers) == 1:
                return "", all_vers[0]
        return current, new

    return "", ""
