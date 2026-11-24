"""LMS tracking timeline + suspend points (QA Companion)."""
import json

from app.automation.scorm.runtime import lms_tracking as lt

SCREENS = {"sA": {"screen_number": "1.2", "slide_title": "Intro"},
           "sB": {"screen_number": "3.4", "slide_title": "Quiz"}}


def _set(k, v, t, slide, fn="SetValue"):
    return {"t": t, "fn": fn, "args": [k, v], "result": "true", "slide": slide}


LOG_2004 = [
    {"t": 1000, "fn": "Initialize", "args": [""], "result": "true"},
    {"t": 1100, "fn": "GetValue", "args": ["cmi.suspend_data"], "result": ""},
    _set("cmi.completion_status", "incomplete", 1200, "sA"),
    _set("cmi.suspend_data", "abc", 1300, "sA"),
    _set("cmi.interactions.0.id", "0_Q1_Slide2", 2000, "sB"),
    _set("cmi.interactions.0.learner_response", "choice_a", 2001, "sB"),
    _set("cmi.interactions.0.result", "correct", 2002, "sB"),
    _set("cmi.score.raw", "100", 2003, "sB"),
    {"t": 2100, "fn": "Commit", "args": [""], "result": "true", "slide": "sB"},
    _set("cmi.success_status", "passed", 3000, "sB"),
    _set("cmi.completion_status", "completed", 3001, "sB"),
]


def test_classify_picks_tracking_calls_only():
    kinds = [lt.classify(e) for e in LOG_2004]
    assert kinds == [None, None, "status", None, "interaction", "interaction",
                     "interaction", "status", "commit", "status", "status"]


def test_summary_says_if_and_when_status_was_sent():
    s = lt.summarize(LOG_2004, SCREENS)
    cs = s["status"]["cmi.completion_status"]
    assert cs["sent"] and cs["times_sent"] == 2
    assert cs["first_value"] == "incomplete" and cs["first_on"] == "screen 1.2"
    assert cs["last_value"] == "completed" and cs["last_on"] == "screen 3.4"
    assert s["status"]["cmi.score.scaled"] == {"sent": False}
    assert s["commits"] == 1 and not s["terminated"]
    assert s["interactions"] == [{"index": 0, "id": "0_Q1_Slide2",
                                  "response": "choice_a", "result": "correct",
                                  "at": s["interactions"][0]["at"],
                                  "on": "screen 3.4"}]
    txt = lt.format_summary(s)
    assert "cmi.score.scaled: NEVER SENT" in txt
    assert "question interactions sent: 1" in txt


def test_scorm_12_uses_core_keys():
    log = [{"t": 1, "fn": "LMSInitialize", "args": [""], "result": "true"},
           _set("cmi.core.lesson_status", "passed", 5, "sA", fn="LMSSetValue")]
    s = lt.summarize(log, SCREENS)
    assert s["scorm_version"] == "1.2"
    assert set(s["status"]) == {"cmi.core.lesson_status", "cmi.core.score.raw"}
    assert s["status"]["cmi.core.lesson_status"]["last_value"] == "passed"


def test_format_event_names_screen_and_value():
    line = lt.format_event(LOG_2004[2], SCREENS)
    assert line.endswith("| screen 1.2 | cmi.completion_status = incomplete")
    assert "slide sZ" in lt.format_event(_set("cmi.score.raw", "5", 1, "sZ"), SCREENS)


def test_snapshot_roundtrip_seeds_a_resume(tmp_path):
    cmi = {"cmi.suspend_data": "xyz", "cmi.location": "s2", "cmi.entry": "ab-initio",
           "cmi.exit": "suspend", "cmi.interactions._count": "1",
           "cmi.interactions.0.id": "Q1", "cmi.completion_status": "incomplete"}
    snap = lt.make_snapshot(cmi, course="C1", slide_id="sB", screens=SCREENS)
    assert snap["screen_number"] == "3.4" and snap["suspend_data_length"] == 3
    path = lt.save_snapshot(snap, tmp_path)
    path2 = lt.save_snapshot(snap, tmp_path)       # same second -> no overwrite
    assert path != path2 and path2.exists()
    loaded = lt.load_snapshot(path)
    seed = lt.seed_from_snapshot(loaded)
    assert seed["cmi.entry"] == "resume"
    assert "cmi.exit" not in seed and "cmi.interactions._count" not in seed
    assert seed["cmi.suspend_data"] == "xyz" and seed["cmi.interactions.0.id"] == "Q1"
    listed = lt.list_snapshots(tmp_path)
    assert len(listed) == 2 and "Screen 3.4 — Quiz" in lt.describe_snapshot(listed[0][1])


def test_seed_sets_12_entry_too():
    seed = lt.seed_from_snapshot({"cmi": {"cmi.core.lesson_location": "x",
                                          "cmi.core.entry": "ab-initio"}})
    assert seed["cmi.core.entry"] == "resume"


def test_list_skips_non_snapshots(tmp_path):
    (tmp_path / "junk.json").write_text(json.dumps({"hello": 1}))
    (tmp_path / "bad.json").write_text("{not json")
    assert lt.list_snapshots(tmp_path) == []
