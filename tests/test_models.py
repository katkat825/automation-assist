"""Result primitives.

Section/CheckItem/Report are the contract between the checks and the UI:
the UI renders sec.title, item.level and item.message directly, so their
shape and ordering matter more than they look.
"""

import dataclasses

from app.automation.scorm.checks.models import CheckItem, Report, Section, _screen_num
from tests.conftest import make_slide


def test_primitives_are_dataclasses():
    """They are constructed with keyword arguments throughout the codebase."""
    for cls in (CheckItem, Section, Report):
        assert dataclasses.is_dataclass(cls)


def test_report_accepts_keyword_construction():
    """Regression: losing @dataclass turns this into a TypeError."""
    r = Report(parse_errors=["boom"])
    assert r.parse_errors == ["boom"]
    assert r.sections == []
    assert r.manual_checklist == ""


def test_sections_do_not_share_mutable_state():
    a, b = Section("A"), Section("B")
    a.add("warn", "only in a")
    assert b.items == []


def test_reports_do_not_share_mutable_state():
    a, b = Report(), Report()
    a.sections.append(Section("x"))
    assert b.sections == []


def test_add_appends_in_order():
    sec = Section("T")
    sec.add("info", "first")
    sec.add("fail", "second")
    assert [(i.level, i.message) for i in sec.items] == [
        ("info", "first"),
        ("fail", "second"),
    ]


def test_to_text_underlines_the_title():
    sec = Section("Title")
    assert sec.to_text().splitlines()[:2] == ["Title", "====="]


def test_to_text_uses_the_expected_level_prefixes():
    sec = Section("T")
    for level in ("pass", "warn", "fail", "info"):
        sec.add(level, f"{level} message")
    body = sec.to_text()
    assert "[OK]  pass message" in body
    assert "[WARN]  warn message" in body
    assert "[FAIL]  fail message" in body
    assert "[INFO]  info message" in body


def test_to_text_falls_back_for_an_unknown_level():
    sec = Section("T")
    sec.add("weird", "msg")
    assert "[?]  msg" in sec.to_text()


def test_screen_num_formats_scene_dot_slide():
    slide = make_slide(scene_number=3, slide_number=4)
    assert _screen_num(slide) == "3.4"
