"""Run-history persistence.

History is what lets the app skip work it already did recently (the 14-day
WordPress check, the daily cache clear) and replay a past result without
re-running it, so the JSON round-trip has to survive intact.
"""


def test_init_db_is_idempotent(temp_db):
    temp_db.init_db()
    temp_db.init_db()  # a second call on an existing db must not raise


def test_no_history_returns_none(temp_db):
    assert temp_db.get_last_wp_update_run() is None
    assert temp_db.get_last_cache_clear_run() is None


def test_wp_run_round_trips_the_result_payload(temp_db):
    payload = {
        "checked_at": "2026-08-18 09:00",
        "wp_core_available": "6.6",
        "plugin_updates": [{"name": "Yoast", "current": "22.0", "new": "22.4"}],
        "theme_updates": [],
        "error": None,
    }
    temp_db.save_wp_update_run(payload)
    assert temp_db.get_last_wp_update_run()["result"] == payload


def test_wp_run_records_a_timestamp(temp_db):
    temp_db.save_wp_update_run({"checked_at": "x"})
    assert temp_db.get_last_wp_update_run()["timestamp"]


def test_has_updates_flag_is_derived_from_the_payload(temp_db):
    temp_db.save_wp_update_run({"plugin_updates": [{"name": "P"}]})
    assert temp_db.get_last_wp_update_run()["has_updates"] == 1


def test_has_updates_flag_is_zero_when_nothing_available(temp_db):
    temp_db.save_wp_update_run({"plugin_updates": [], "theme_updates": []})
    assert temp_db.get_last_wp_update_run()["has_updates"] == 0


def test_latest_wp_run_wins(temp_db):
    temp_db.save_wp_update_run({"checked_at": "first"})
    temp_db.save_wp_update_run({"checked_at": "second"})
    assert temp_db.get_last_wp_update_run()["result"]["checked_at"] == "second"


def test_save_returns_increasing_row_ids(temp_db):
    first = temp_db.save_wp_update_run({"checked_at": "a"})
    second = temp_db.save_wp_update_run({"checked_at": "b"})
    assert second > first


def test_cache_clear_round_trips(temp_db):
    temp_db.save_cache_clear_run({"success": True, "cleared": ["elementor", "site"]})
    assert temp_db.get_last_cache_clear_run()["result"]["cleared"] == ["elementor", "site"]


def test_site_checks_return_newest_first(temp_db):
    temp_db.save_site_check_run("live", "https://a.example.com", {"issues": []})
    temp_db.save_site_check_run("staging", "https://b.example.com", {"issues": []})
    recent = temp_db.get_recent_site_checks()
    assert recent[0]["base_url"] == "https://b.example.com"


def test_site_check_limit_is_respected(temp_db):
    for i in range(5):
        temp_db.save_site_check_run("live", f"https://{i}.example.com", {})
    assert len(temp_db.get_recent_site_checks(limit=3)) == 3


def test_site_check_records_the_target(temp_db):
    temp_db.save_site_check_run("staging", "https://s.example.com", {})
    assert temp_db.get_recent_site_checks()[0]["target"] == "staging"


def test_unicode_survives_the_round_trip(temp_db):
    temp_db.save_cache_clear_run({"note": "clé — naïve — 日本語"})
    assert temp_db.get_last_cache_clear_run()["result"]["note"] == "clé — naïve — 日本語"


def test_monday_backup_helpers_are_gone(temp_db):
    """The Monday feature was removed; its history API should be too."""
    assert not hasattr(temp_db, "save_monday_backup_run")
    assert not hasattr(temp_db, "get_last_monday_backup_run")
