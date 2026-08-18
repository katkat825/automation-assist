"""
OD Regression Tests runner.

Wraps the two commands normally run by hand in Command Prompt:

    1. Install Chrome Beta (used as the Playwright browser channel):
         npx playwright install chrome-beta

    2. Run the regression suite:
         npx playwright test tests --workers=1 --headed --reporter=html

Both run inside the Playwright project folder. That project is kept in a
separate private repository and is NOT part of this one — the specs encode
internal LMS behaviour. This module is only the runner.

The folder is resolved at call time by project_dir(): an explicit
``regression.project_dir`` setting wins, otherwise it is looked for at
``od_regress_tests/`` relative to the repo (from source) or next to the
executable (when frozen). It is also never bundled into the .exe — it needs
node_modules on disk and Playwright writes its report back into it.

If no project is present, the tab reports the missing folder and nothing
else in the app is affected.
"""

from __future__ import annotations

import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Callable, Optional

from app.config import settings as cfg

_PROJECT_FOLDER_NAME = "od_regress_tests"


def candidate_project_dirs() -> list[Path]:
    """Places to look for the Playwright project, in priority order.

    The project cannot be bundled into the executable: it needs node_modules
    on disk and Playwright writes playwright-report/ back into it. So when
    frozen we look next to the .exe, not inside PyInstaller's temporary
    extraction directory (sys._MEIPASS) — which is where __file__ points and
    which is wiped after every run.
    """
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        return [
            exe_dir / _PROJECT_FOLDER_NAME,
            exe_dir.parent / _PROJECT_FOLDER_NAME,   # e.g. exe sitting in dist/
        ]
    return [Path(__file__).resolve().parents[3] / _PROJECT_FOLDER_NAME]


def project_dir() -> Path:
    """Resolve the Playwright project folder.

    An explicit settings override always wins. Otherwise the first candidate
    that exists is used; if none exist, the first candidate is returned so the
    caller can show a concrete path in its error message.

    Read on every call rather than cached at import, so changing the setting
    takes effect without restarting the app.
    """
    configured = (cfg.load().get("regression") or {}).get("project_dir") or ""
    if configured.strip():
        return Path(configured.strip())

    candidates = candidate_project_dirs()
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


def report_index() -> Path:
    """Where Playwright's HTML reporter writes its index."""
    return project_dir() / "playwright-report" / "index.html"


def run_log_url() -> str:
    """Optional external run-log spreadsheet, opened after a run.

    Empty by default — set {"regression": {"run_log_url": "..."}} in
    settings.json to point at your own document.
    """
    return (cfg.load().get("regression") or {}).get("run_log_url") or ""


def _stream(
    cmd: str,
    cwd: Path,
    log: Optional[Callable[[str], None]] = None,
) -> int:
    """Run *cmd* in *cwd*, streaming each output line to *log*. Returns exit code."""
    def _emit(msg: str):
        if log:
            log(msg)

    if not cwd.exists():
        _emit(f"ERROR: test project folder not found: {cwd}")
        return 1

    _emit(f"$ {cmd}")
    _emit(f"  (cwd: {cwd})")

    creationflags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        creationflags=creationflags,
    )

    assert proc.stdout is not None
    for line in proc.stdout:
        _emit(line.rstrip())

    return proc.wait()


def _run_in_console(
    cmd: str,
    cwd: Path,
    log: Optional[Callable[[str], None]] = None,
) -> int:
    """Run *cmd* in a visible Command Prompt window and wait for it to finish.

    Used for commands whose output is poorly behaved under a piped stdout
    (carriage-return progress bars, buffered Node output) or that may trigger
    a UAC prompt. The user sees progress natively in the console; we only
    capture the final exit code.
    """
    def _emit(msg: str):
        if log:
            log(msg)

    if not cwd.exists():
        _emit(f"ERROR: test project folder not found: {cwd}")
        return 1

    _emit(f"$ {cmd}")
    _emit(f"  (cwd: {cwd})")
    _emit("Opening a Command Prompt window — watch that window for progress.")

    # Wrap so the window stays open only if the command errors out; on success
    # it closes automatically so the next step (tests) can start seamlessly.
    wrapped = (
        f'{cmd} || (echo. & echo [Install failed — press any key to close] & pause >nul)'
    )

    creationflags = 0
    if hasattr(subprocess, "CREATE_NEW_CONSOLE"):
        creationflags = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]

    proc = subprocess.Popen(
        f'cmd /c "{wrapped}"',
        cwd=str(cwd),
        shell=False,
        creationflags=creationflags,
    )
    return proc.wait()


def install_chrome_beta(log: Optional[Callable[[str], None]] = None) -> int:
    """Download and install the Chrome Beta channel via Playwright.

    Runs in a separate Command Prompt window because Playwright's installer
    uses a \\r-based progress bar (which doesn't stream well through a pipe)
    and may trigger a UAC elevation prompt.
    """
    return _run_in_console(
        "npx playwright install chrome-beta", project_dir(), log
    )


def run_regression_tests(log: Optional[Callable[[str], None]] = None) -> int:
    """Run the full Playwright regression suite (headless per config, 1 worker, HTML report)."""
    return _stream(
        "npx playwright test tests --workers=1 --reporter=html",
        project_dir(),
        log,
    )


def run_install_and_tests(log: Optional[Callable[[str], None]] = None) -> int:
    """Install Chrome Beta, then run the regression suite. Returns the test exit code.

    If the install step fails with a non-zero exit code, the tests are skipped
    and that exit code is returned.
    """
    def _emit(msg: str):
        if log:
            log(msg)

    _emit("=== Step 1 / 2: Install Chrome Beta ===")
    install_code = install_chrome_beta(log)
    if install_code != 0:
        _emit(f"\nInstall step failed (exit code {install_code}). Skipping tests.")
        return install_code

    _emit("\n=== Step 2 / 2: Run Regression Tests ===")
    return run_regression_tests(log)


def get_chrome_beta_version_banner() -> Optional[str]:
    """Return a Chrome-style version banner for the installed Chrome Beta, or None.

    Example: "Version 148.0.7778.40 (Official Build) beta (64-bit)"
    """
    candidates = [
        (r"C:\Program Files\Google\Chrome Beta\Application\chrome.exe", "64-bit"),
        (r"C:\Program Files (x86)\Google\Chrome Beta\Application\chrome.exe", "32-bit"),
    ]

    creationflags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

    for exe_path, arch in candidates:
        if not Path(exe_path).exists():
            continue
        try:
            ps_cmd = (
                f"(Get-Item -LiteralPath '{exe_path}').VersionInfo.ProductVersion"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=creationflags,
            )
            version = result.stdout.strip()
            if version:
                return f"Version {version} (Official Build) beta ({arch})"
        except Exception:
            continue

    return None


def open_report() -> bool:
    """Open the most recent Playwright HTML report in the default browser."""
    index = report_index()
    if not index.exists():
        return False
    webbrowser.open(index.as_uri())
    return True


def open_run_log() -> bool:
    """Open the configured run-log spreadsheet in the default browser.

    Returns False if no run_log_url is configured, so the caller can tell the
    user rather than silently opening nothing.
    """
    url = run_log_url()
    if not url:
        return False
    webbrowser.open(url)
    return True
