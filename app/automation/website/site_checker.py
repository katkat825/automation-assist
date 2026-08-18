"""
Website health checker.
For each page in the sample list, checks:
  - HTTP status of the page itself
  - All images on the page (broken image detection)
  - All internal links on the page — checks their HTTP status (1 level deep)

Uses requests + BeautifulSoup for speed. No JS rendering.
"""

import concurrent.futures
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

_MAX_WORKERS = 10
_TIMEOUT = 10
_HEADERS = {
    "User-Agent": "AutomationAssist/1.0 (site health check)"
}


@dataclass
class ResourceResult:
    url: str
    status_code: int
    ok: bool
    note: str = ""


@dataclass
class PageResult:
    url: str
    status_code: int
    ok: bool
    broken_images: list = field(default_factory=list)   # list[ResourceResult]
    broken_links: list = field(default_factory=list)    # list[ResourceResult]
    error: Optional[str] = None

    @property
    def has_issues(self) -> bool:
        return not self.ok or bool(self.broken_images) or bool(self.broken_links)

    def issue_summary(self) -> str:
        parts = []
        if not self.ok:
            parts.append(f"page returned HTTP {self.status_code}")
        if self.broken_images:
            parts.append(f"{len(self.broken_images)} broken image(s)")
        if self.broken_links:
            parts.append(f"{len(self.broken_links)} dead link(s)")
        return ", ".join(parts) if parts else "OK"


@dataclass
class SiteCheckResult:
    target: str          # 'live' or 'staging'
    base_url: str
    checked_at: str
    pages: list = field(default_factory=list)   # list[PageResult]
    error: Optional[str] = None

    def to_report_text(self) -> str:
        target_label = self.target.capitalize()
        lines = [
            f"Site Health Check — {target_label} ({self.base_url})",
            f"Checked: {self.checked_at}",
            "=" * 60,
        ]

        if self.error:
            lines.append(f"\nERROR: {self.error}")
            return "\n".join(lines)

        issues_found = sum(1 for p in self.pages if p.has_issues)
        lines.append(
            f"\nChecked {len(self.pages)} page(s). Issues found on {issues_found} page(s)."
        )

        for pr in self.pages:
            marker = "✗" if pr.has_issues else "✓"
            lines.append(f"\n{marker} {pr.url}")

            if not pr.ok:
                lines.append(f"    Page returned HTTP {pr.status_code}")
            if pr.error:
                lines.append(f"    Error: {pr.error}")
            for img in pr.broken_images:
                note = f" ({img.note})" if img.note else ""
                lines.append(f"    [Broken image] {img.url}  → HTTP {img.status_code}{note}")
            for lnk in pr.broken_links:
                note = f" ({lnk.note})" if lnk.note else ""
                lines.append(f"    [Dead link]    {lnk.url}  → HTTP {lnk.status_code}{note}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "base_url": self.base_url,
            "checked_at": self.checked_at,
            "pages": [
                {
                    "url": p.url,
                    "status_code": p.status_code,
                    "ok": p.ok,
                    "broken_images": [
                        {"url": r.url, "status": r.status_code, "note": r.note}
                        for r in p.broken_images
                    ],
                    "broken_links": [
                        {"url": r.url, "status": r.status_code, "note": r.note}
                        for r in p.broken_links
                    ],
                    "error": p.error,
                }
                for p in self.pages
            ],
            "error": self.error,
        }


def check_site(
    target: str,
    base_url: str,
    sample_pages: list,
    log: Optional[Callable[[str], None]] = None,
) -> SiteCheckResult:
    """
    Check each URL in sample_pages for broken content.

    Parameters
    ----------
    target       : 'live' or 'staging'
    base_url     : Root URL of the site — used to identify internal vs external links.
    sample_pages : List of URL strings to check.
    log          : Optional callback for progress messages.
    """
    def _log(msg: str):
        if log:
            log(msg)

    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not sample_pages:
        return SiteCheckResult(
            target=target,
            base_url=base_url,
            checked_at=checked_at,
            error="No sample pages configured. Add URLs in Settings → Website.",
        )

    # Normalize base URL netloc for internal-link detection
    # Strip "www." so https://www.example.com and https://example.com match
    base_netloc = urlparse(base_url).netloc.lstrip("www.")

    def is_internal(url: str) -> bool:
        try:
            netloc = urlparse(url).netloc.lstrip("www.")
            return netloc == base_netloc or not netloc
        except Exception:
            return False

    session = requests.Session()
    session.headers.update(_HEADERS)

    def head_check(url: str) -> ResourceResult:
        try:
            resp = session.head(url, timeout=_TIMEOUT, allow_redirects=True)
            # Some servers don't support HEAD — fall back to GET with stream
            if resp.status_code == 405:
                resp = session.get(url, timeout=_TIMEOUT, allow_redirects=True, stream=True)
                resp.close()
            ok = 200 <= resp.status_code < 400
            return ResourceResult(url=url, status_code=resp.status_code, ok=ok)
        except requests.exceptions.Timeout:
            return ResourceResult(url=url, status_code=0, ok=False, note="timeout")
        except Exception as e:
            return ResourceResult(url=url, status_code=0, ok=False, note=str(e)[:80])

    results = []
    for i, page_url in enumerate(sample_pages, 1):
        _log(f"[{i}/{len(sample_pages)}] Checking: {page_url}")
        pr = _check_page(session, page_url, is_internal, head_check, _log)
        results.append(pr)

    _log(
        f"Done. {sum(1 for p in results if p.has_issues)} page(s) with issues "
        f"out of {len(results)} checked."
    )

    return SiteCheckResult(
        target=target,
        base_url=base_url,
        checked_at=checked_at,
        pages=results,
    )


def _check_page(
    session: requests.Session,
    url: str,
    is_internal,
    head_check,
    log,
) -> PageResult:
    try:
        resp = session.get(url, timeout=_TIMEOUT, allow_redirects=True)
        ok = 200 <= resp.status_code < 400

        if not ok:
            log(f"  Page returned HTTP {resp.status_code}")
            return PageResult(url=url, status_code=resp.status_code, ok=False)

        content_type = resp.headers.get("Content-Type", "")
        if "html" not in content_type:
            # Not an HTML page (PDF, image, etc.) — just report it as OK
            return PageResult(url=url, status_code=resp.status_code, ok=True)

        soup = BeautifulSoup(resp.text, "html.parser")

        # Collect image source URLs (skip data URIs)
        img_urls = []
        for img in soup.find_all("img", src=True):
            src = img["src"].strip()
            if src and not src.startswith("data:"):
                img_urls.append(urljoin(url, src))

        # Collect internal link URLs, deduplicated
        link_urls = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            abs_url = urljoin(url, href).split("#")[0]   # strip fragment
            if abs_url not in seen and is_internal(abs_url):
                seen.add(abs_url)
                link_urls.append(abs_url)

        # Check images and links concurrently
        broken_images = []
        broken_links = []

        jobs = [("img", u) for u in img_urls] + [("link", u) for u in link_urls]

        if jobs:
            with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
                futures = {ex.submit(head_check, u): (kind, u) for kind, u in jobs}
                for fut in concurrent.futures.as_completed(futures):
                    kind, _ = futures[fut]
                    res = fut.result()
                    if not res.ok:
                        if kind == "img":
                            broken_images.append(res)
                        else:
                            broken_links.append(res)

        if broken_images or broken_links:
            log(
                f"  {len(broken_images)} broken image(s), "
                f"{len(broken_links)} dead link(s)"
            )

        return PageResult(
            url=url,
            status_code=resp.status_code,
            ok=True,
            broken_images=sorted(broken_images, key=lambda r: r.url),
            broken_links=sorted(broken_links, key=lambda r: r.url),
        )

    except Exception as e:
        log(f"  Error: {e}")
        return PageResult(url=url, status_code=0, ok=False, error=str(e))
