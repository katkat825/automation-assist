"""
Resolve data-file paths that work in both development and PyInstaller-bundled mode.

Development  : files live in the project root (next to main.py).
Bundled .exe  : files live in %LOCALAPPDATA%/AutomationAssist/.

The data directory is created on first import if it doesn't exist.
"""

import sys
from pathlib import Path

def _data_dir() -> Path:
    """Return the directory where settings.json and history.db are stored."""
    if getattr(sys, "frozen", False):
        # Running as a PyInstaller bundle — use a stable per-user location
        local = Path.home() / "AppData" / "Local" / "AutomationAssist"
    else:
        # Running from source — project root (parent of the app/ package)
        local = Path(__file__).parent.parent

    local.mkdir(parents=True, exist_ok=True)
    return local


DATA_DIR = _data_dir()
SETTINGS_FILE = DATA_DIR / "settings.json"
DB_FILE = DATA_DIR / "history.db"
