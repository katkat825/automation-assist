"""Playwright project folder resolution.

This is easy to get wrong under PyInstaller: __file__ points inside the
temporary extraction directory, which is wiped after every run, so a path
derived from it resolves somewhere that never exists. The frozen branch must
resolve relative to the executable instead.
"""

import sys
from pathlib import Path

from app.automation.regression import runner


def _freeze(monkey_exe):
    sys.frozen = True
    sys.executable = monkey_exe


def _unfreeze(original_exe):
    sys.executable = original_exe
    if hasattr(sys, "frozen"):
        del sys.frozen


def test_source_layout_resolves_to_repo_root():
    """From source, the project sits next to the app/ package."""
    candidates = runner.candidate_project_dirs()
    assert len(candidates) == 1
    assert candidates[0].name == "od_regress_tests"
    # parents[3] of app/automation/regression/runner.py is the repo root
    assert (candidates[0].parent / "app" / "automation" / "regression").is_dir()


def test_frozen_resolves_next_to_executable_not_temp_dir(tmp_path):
    original = sys.executable
    exe = tmp_path / "dist" / "AutomationAssist.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    try:
        _freeze(str(exe))
        candidates = runner.candidate_project_dirs()
        assert candidates[0] == exe.parent / "od_regress_tests"
        assert candidates[1] == exe.parent.parent / "od_regress_tests"
        # the failure mode being guarded against
        assert not any("_MEI" in str(c) for c in candidates)
    finally:
        _unfreeze(original)


def test_frozen_prefers_the_candidate_that_exists(tmp_path, monkeypatch_settings):
    """An exe in dist/ should find the project one level up."""
    original = sys.executable
    exe = tmp_path / "dist" / "AutomationAssist.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    (tmp_path / "od_regress_tests").mkdir()
    try:
        _freeze(str(exe))
        monkeypatch_settings({})
        assert runner.project_dir() == tmp_path / "od_regress_tests"
    finally:
        _unfreeze(original)


def test_missing_project_returns_first_candidate_for_the_error_message(tmp_path, monkeypatch_settings):
    original = sys.executable
    exe = tmp_path / "nowhere" / "AutomationAssist.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    try:
        _freeze(str(exe))
        monkeypatch_settings({})
        resolved = runner.project_dir()
        assert resolved == exe.parent / "od_regress_tests"
        assert not resolved.is_dir()
    finally:
        _unfreeze(original)


def test_settings_override_wins_over_discovery(monkeypatch_settings):
    # Compare Path objects, not strings: the same path renders with "/" on
    # POSIX and "\" on Windows, and this suite has to pass on both.
    monkeypatch_settings({"regression": {"project_dir": "/custom/playwright/project"}})
    assert runner.project_dir() == Path("/custom/playwright/project")


def test_blank_override_falls_back_to_discovery(monkeypatch_settings):
    monkeypatch_settings({"regression": {"project_dir": "   "}})
    assert runner.project_dir().name == "od_regress_tests"


def test_override_is_read_per_call_not_cached_at_import(monkeypatch_settings):
    """Changing the setting must take effect without restarting the app."""
    monkeypatch_settings({"regression": {"project_dir": "/first"}})
    assert runner.project_dir() == Path("/first")
    monkeypatch_settings({"regression": {"project_dir": "/second"}})
    assert runner.project_dir() == Path("/second")


def test_report_index_sits_under_the_project_dir(monkeypatch_settings):
    monkeypatch_settings({"regression": {"project_dir": "/proj"}})
    assert runner.report_index() == Path("/proj") / "playwright-report" / "index.html"


def test_run_log_url_defaults_to_empty(monkeypatch_settings):
    monkeypatch_settings({})
    assert runner.run_log_url() == ""


def test_run_log_url_reads_setting(monkeypatch_settings):
    monkeypatch_settings({"regression": {"run_log_url": "https://example.com/log"}})
    assert runner.run_log_url() == "https://example.com/log"


def test_open_run_log_reports_false_when_unconfigured(monkeypatch_settings):
    """False lets the caller tell the user instead of opening nothing."""
    monkeypatch_settings({})
    assert runner.open_run_log() is False


def test_open_report_returns_false_when_no_report_exists(monkeypatch_settings, tmp_path):
    monkeypatch_settings({"regression": {"project_dir": str(tmp_path)}})
    assert runner.open_report() is False
