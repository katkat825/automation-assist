"""
Local settings - credentials and user preferences stored in a JSON file
next to the app. Nothing is sent anywhere; this file stays on your machine.
"""

import json
import os
from pathlib import Path

from app.paths import SETTINGS_FILE

_DEFAULTS = {
    "sql": {
        "server": "",
        "database": "",
        "username": "",
        "password": "",
        "trusted_connection": True,
    },
    "qa": {
        "screenshot_dir": str(Path.home() / "automation-assist-screenshots"),
        "headless": False,
    },
    "website": {
        "wp_admin_url": "",
        "wp_username": "",
        "wp_password": "",
        "live_url": "",
        "staging_url": "",
        "sample_pages": [],
    },
    "ui": {
        "font_size": 0,  # 0 = system default; positive = pt override
    },
    "regression": {
        # Full path to the Playwright project. That project lives in a separate
        # private repo and is not shipped here. Empty = look for od_regress_tests/
        # beside the repo root, or beside the .exe when frozen.
        "project_dir": "",
        # Optional external run-log spreadsheet opened after a run. Empty = skip.
        "run_log_url": "",
    },
}


def load() -> dict:
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        # Deep-merge saved values over defaults so new keys always exist
        result = _deep_merge(_DEFAULTS, saved)
        return result
    return _deep_merge({}, _DEFAULTS)


def save(settings: dict) -> None:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
