"""
Build Automation Assist into a standalone Windows .exe.

Usage:
    python build.py

Output:
    dist/AutomationAssist.exe   (single-file, no console window)

Notes:
    - settings.json and history.db are NOT bundled — they live in
      %LOCALAPPDATA%/AutomationAssist/ at runtime.
    - Playwright browsers must be installed on the machine
      (run `playwright install chromium` once).
    - The Playwright regression project (od_regress_tests/) is kept in a
      separate private repo and is not part of this repository. It is also
      never bundled into the .exe: it needs node_modules on disk and
      Playwright writes playwright-report/ back into it, so it cannot live
      in the read-only temp dir PyInstaller extracts to. The app looks for it
      next to the .exe and one level up (so running from dist/ finds a copy
      in the repo root); otherwise set regression.project_dir in Settings.
      If you move the .exe elsewhere, move that folder with it.
"""

import os

import PyInstaller.__main__

# The runtime SCORM QA harness loads scorm_stub.js at runtime via
# Path(__file__).parent — it must be bundled next to its package so the
# frozen exe can still find it inside the temp extraction dir.
_STUB_SRC = os.path.join("app", "automation", "scorm", "runtime", "scorm_stub.js")
_STUB_DEST = os.path.join("app", "automation", "scorm", "runtime")
# PyInstaller's --add-data separator is ';' on Windows, ':' elsewhere.
_SEP = ";" if os.name == "nt" else ":"

PyInstaller.__main__.run([
    "main.py",
    "--name=AutomationAssist",
    "--onefile",
    "--windowed",
    "--noconfirm",
    # PySide6 plugins PyInstaller sometimes misses
    "--hidden-import=PySide6.QtSvg",
    "--hidden-import=PySide6.QtNetwork",
    # spellchecker data files
    "--collect-data=spellchecker",
    # SCORM runtime harness stub (loaded from disk at runtime)
    f"--add-data={_STUB_SRC}{_SEP}{_STUB_DEST}",
    # Exclude modules that bloat the build but aren't used at runtime
    "--exclude-module=tkinter",
    "--exclude-module=matplotlib",
    "--exclude-module=numpy",
])
