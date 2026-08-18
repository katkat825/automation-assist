"""WordPress update result serialisation.

to_report_text() is the single renderer for both a live run and a run
replayed out of history. The history path goes through from_dict(), so if
these two drift the replayed view silently stops matching the live one.
These tests pin the round-trip.
"""

from app.automation.website.wp_updates import (
    PluginUpdate,
    ThemeUpdate,
    WpUpdateResult,
)


def _full_result():
    return WpUpdateResult(
        checked_at="2026-08-18 09:00",
        wp_core_current="6.5.2",
        wp_core_available="6.6",
        plugin_updates=[
            PluginUpdate("Yoast SEO", "22.0", "22.4"),
            PluginUpdate("WP Rocket", "3.15", "3.16"),
        ],
        theme_updates=[ThemeUpdate("Astra", "4.6", "4.7")],
    )


def test_has_updates_false_when_nothing_available():
    assert WpUpdateResult(checked_at="x").has_updates is False


def test_has_updates_true_for_core_only():
    assert WpUpdateResult(checked_at="x", wp_core_available="6.6").has_updates is True


def test_has_updates_true_for_plugins_only():
    r = WpUpdateResult(checked_at="x", plugin_updates=[PluginUpdate("P", "1", "2")])
    assert r.has_updates is True


def test_round_trip_preserves_every_field():
    original = _full_result()
    restored = WpUpdateResult.from_dict(original.to_dict())

    assert restored.checked_at == original.checked_at
    assert restored.wp_core_current == original.wp_core_current
    assert restored.wp_core_available == original.wp_core_available
    assert restored.error == original.error
    assert [(p.name, p.current_version, p.new_version) for p in restored.plugin_updates] == \
           [(p.name, p.current_version, p.new_version) for p in original.plugin_updates]
    assert [(t.name, t.current_version, t.new_version) for t in restored.theme_updates] == \
           [(t.name, t.current_version, t.new_version) for t in original.theme_updates]


def test_to_dict_is_stable_across_a_round_trip():
    d = _full_result().to_dict()
    assert WpUpdateResult.from_dict(d).to_dict() == d


def test_replayed_report_text_matches_live_report_text():
    """The regression this guards: history replay must render identically."""
    live = _full_result()
    replayed = WpUpdateResult.from_dict(live.to_dict())
    assert replayed.to_report_text() == live.to_report_text()


def test_report_text_reports_error_and_stops():
    text = WpUpdateResult(checked_at="x", error="login failed").to_report_text()
    assert "ERROR: login failed" in text
    # an error short-circuits: no "up to date" line follows it
    assert "No updates available" not in text


def test_error_takes_precedence_over_available_updates():
    """Both paths must agree even when error and updates are both present."""
    live = WpUpdateResult(
        checked_at="x",
        error="timeout",
        wp_core_available="6.6",
        plugin_updates=[PluginUpdate("P", "1", "2")],
    )
    replayed = WpUpdateResult.from_dict(live.to_dict())
    assert live.to_report_text() == replayed.to_report_text()
    assert "ERROR: timeout" in live.to_report_text()
    assert "PLUGIN UPDATES" not in live.to_report_text()


def test_no_updates_message():
    assert "No updates available" in WpUpdateResult(checked_at="x").to_report_text()


def test_core_update_shows_transition_when_current_known():
    text = WpUpdateResult(
        checked_at="x", wp_core_current="6.5.2", wp_core_available="6.6"
    ).to_report_text()
    assert "6.5.2 → 6.6" in text


def test_core_update_without_current_version_says_new_version():
    text = WpUpdateResult(checked_at="x", wp_core_available="6.6").to_report_text()
    assert "New version: 6.6" in text


def test_plugin_and_theme_counts_appear_in_headings():
    text = _full_result().to_report_text()
    assert "PLUGIN UPDATES (2)" in text
    assert "THEME UPDATES (1)" in text


def test_from_dict_tolerates_empty_and_none():
    assert WpUpdateResult.from_dict({}).checked_at == ""
    assert WpUpdateResult.from_dict(None).plugin_updates == []


def test_from_dict_tolerates_missing_nested_keys():
    r = WpUpdateResult.from_dict({"plugin_updates": [{}], "theme_updates": [{}]})
    assert r.plugin_updates[0].name == ""
    assert r.theme_updates[0].new_version == ""
