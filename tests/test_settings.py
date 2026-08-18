"""Settings loading and deep-merge.

The merge exists so that adding a new key to _DEFAULTS does not require
every existing user's settings.json to be edited. Nested sections must merge
rather than replace, or upgrading the app would silently drop saved values.
"""

import json

from app.config import settings as cfg


def test_defaults_expose_the_expected_sections():
    assert set(cfg._DEFAULTS) == {"sql", "qa", "website", "ui", "regression"}


def test_defaults_contain_no_baked_in_credentials():
    """Nothing shipped in code should carry a real secret."""
    for section in ("sql", "website"):
        for key, value in cfg._DEFAULTS[section].items():
            if any(t in key for t in ("password", "username", "token", "secret")):
                assert value == "", f"{section}.{key} ships a non-empty value"


def test_deep_merge_preserves_untouched_defaults():
    merged = cfg._deep_merge({"a": {"x": 1, "y": 2}}, {"a": {"x": 9}})
    assert merged == {"a": {"x": 9, "y": 2}}


def test_deep_merge_adds_new_keys():
    assert cfg._deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}


def test_deep_merge_replaces_scalars_with_the_override():
    assert cfg._deep_merge({"a": 1}, {"a": 2}) == {"a": 2}


def test_deep_merge_replaces_lists_wholesale():
    """Lists are values, not things to concatenate."""
    assert cfg._deep_merge({"a": [1, 2]}, {"a": [3]}) == {"a": [3]}


def test_deep_merge_recurses_into_nested_dicts():
    merged = cfg._deep_merge(
        {"outer": {"inner": {"keep": 1, "change": 2}}},
        {"outer": {"inner": {"change": 3}}},
    )
    assert merged["outer"]["inner"] == {"keep": 1, "change": 3}


def test_deep_merge_does_not_mutate_the_base():
    base = {"a": {"x": 1}}
    cfg._deep_merge(base, {"a": {"x": 2}})
    assert base == {"a": {"x": 1}}


def test_load_returns_defaults_when_no_file_exists(tmp_path, monkeypatch_paths):
    monkeypatch_paths(tmp_path / "missing.json")
    assert set(cfg.load()) == set(cfg._DEFAULTS)


def test_saved_values_win_over_defaults(tmp_path, monkeypatch_paths):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"website": {"live_url": "https://saved.example.com"}}))
    monkeypatch_paths(path)
    assert cfg.load()["website"]["live_url"] == "https://saved.example.com"


def test_keys_absent_from_a_saved_file_still_appear(tmp_path, monkeypatch_paths):
    """The reason the merge exists: old settings.json + new default key."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"website": {"live_url": "https://saved.example.com"}}))
    monkeypatch_paths(path)
    loaded = cfg.load()
    assert "regression" in loaded
    assert loaded["website"]["wp_username"] == ""


def test_save_then_load_round_trips(tmp_path, monkeypatch_paths):
    path = tmp_path / "settings.json"
    monkeypatch_paths(path)
    data = cfg.load()
    data["ui"]["font_size"] = 14
    cfg.save(data)
    assert cfg.load()["ui"]["font_size"] == 14
