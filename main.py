"""
Automation Assist — entry point.

Run this file directly to launch the app:
    python main.py

Or double-click the built .exe (see build.py).
"""

import os
import shutil
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from app.paths import DATA_DIR, SETTINGS_FILE, DB_FILE
from app.ui.main_window import MainWindow
from app.db import history


def _ensure_playwright_browsers():
    """Make sure the bundled exe can find Playwright's browser binaries.
    They live in %LOCALAPPDATA%/ms-playwright/ — Playwright auto-detects
    this when running from source, but a frozen exe may not."""
    if not getattr(sys, "frozen", False):
        return
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
        default = Path.home() / "AppData" / "Local" / "ms-playwright"
        if default.exists():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(default)


def _migrate_data_files():
    """Copy settings.json / history.db from the exe's directory into the
    permanent data directory on first launch of a bundled build.  This is a
    one-time convenience so the user's existing config carries over."""
    if not getattr(sys, "frozen", False):
        return  # running from source — paths already point to project root

    exe_dir = Path(sys.executable).parent
    for name in ("settings.json", "history.db"):
        src = exe_dir / name
        dst = DATA_DIR / name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)


def main():
    # High-DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Automation Assist")
    app.setOrganizationName("Local")

    # Point Playwright at its browser binaries (needed for bundled exe)
    _ensure_playwright_browsers()

    # Migrate data files from exe directory on first bundled launch
    _migrate_data_files()

    # Initialize local database (creates tables if first run)
    history.init_db()

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
