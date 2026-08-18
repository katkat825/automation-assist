"""
Lightweight SQLite store for QA run history.
Keeps a record of every run so you can review past results inside the app.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.paths import DB_FILE


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't exist. Called once at app startup."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wp_update_runs (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT    NOT NULL,
                has_updates INTEGER NOT NULL DEFAULT 0,
                result    TEXT    NOT NULL   -- JSON blob of WpUpdateResult.to_dict()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS site_check_runs (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT    NOT NULL,
                target    TEXT    NOT NULL,  -- 'live' or 'staging'
                base_url  TEXT    NOT NULL,
                result    TEXT    NOT NULL   -- JSON blob of SiteCheckResult.to_dict()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache_clear_runs (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT    NOT NULL,
                result    TEXT    NOT NULL
            )
        """)


# ---------------------------------------------------------------------------
# WordPress update runs
# ---------------------------------------------------------------------------

def save_wp_update_run(result_dict: dict) -> int:
    """Persist a completed WordPress update check. Returns the new row id."""
    has_updates = int(
        bool(
            result_dict.get("wp_core_available")
            or result_dict.get("plugin_updates")
            or result_dict.get("theme_updates")
        )
    )
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO wp_update_runs (timestamp, has_updates, result) VALUES (?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), has_updates, json.dumps(result_dict)),
        )
        return cur.lastrowid


def get_last_wp_update_run() -> dict | None:
    """Return the most recent WordPress update check, or None if none exists."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM wp_update_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row:
        d = dict(row)
        d["result"] = json.loads(d["result"])
        return d
    return None


# ---------------------------------------------------------------------------
# Site health check runs
# ---------------------------------------------------------------------------

def save_site_check_run(target: str, base_url: str, result_dict: dict) -> int:
    """Persist a completed site health check. Returns the new row id."""
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO site_check_runs (timestamp, target, base_url, result) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), target, base_url, json.dumps(result_dict)),
        )
        return cur.lastrowid


def get_recent_site_checks(limit: int = 10) -> list[dict]:
    """Return the most recent site health checks, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, timestamp, target, base_url FROM site_check_runs ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Cache clear runs
# ---------------------------------------------------------------------------

def save_cache_clear_run(result_dict: dict) -> int:
    """Persist a completed cache clear run. Returns the new row id."""
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO cache_clear_runs (timestamp, result) VALUES (?, ?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                json.dumps(result_dict),
            ),
        )
        return cur.lastrowid


def get_last_cache_clear_run() -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM cache_clear_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()

    if row:
        d = dict(row)
        d["result"] = json.loads(d["result"])
        return d

    return None
