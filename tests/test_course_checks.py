"""Course-level configuration checks.

check_scorm_api is the highest-stakes one: a missing completion_status is a
hard failure that stops QA, so the pass/fail levels it emits are load-bearing.
"""

from app.automation.scorm.checks.course_checks import (
    check_course_overview,
    check_course_paths,
    check_questions,
    check_scorm_api,
    check_video_speed,
)
from app.automation.scorm.parser import StoryData
from tests.conftest import GOOD_SCORMDRIVER_JS, make_data, make_interaction, make_slide


def levels(section):
    return {i.level for i in section.items}


def text_of(section):
    return "\n".join(i.message for i in section.items)


# --- overview -------------------------------------------------------------

def test_overview_reports_title_and_slide_count():
    data = make_data([make_slide(), make_slide(slide_id="s2")], course_title="Phishing 101")
    body = text_of(check_course_overview(data))
    assert "Phishing 101" in body
    assert "Slides: 2" in body


def test_overview_falls_back_to_unknown_title():
    assert "Unknown" in text_of(check_course_overview(make_data([], course_title="")))


def test_overview_uses_timeline_duration_when_present():
    data = make_data([make_slide(duration_ms=600_000)])  # 10 minutes
    assert "~10 min" in text_of(check_course_overview(data))


def test_overview_falls_back_to_heuristic_without_timeline():
    data = make_data([make_slide(duration_ms=0)])
    assert "heuristic" in text_of(check_course_overview(data))


def test_overview_seat_time_has_a_five_minute_floor():
    """A very short timeline should not report ~0 min."""
    data = make_data([make_slide(duration_ms=1000)])
    assert "~5 min" in text_of(check_course_overview(data))


# --- SCORM API ------------------------------------------------------------

def test_scorm_api_fails_hard_without_scormdriver():
    sec = check_scorm_api(make_data([], scormdriver_js=""))
    assert "fail" in levels(sec)
    assert "scormdriver.js not found" in text_of(sec)


def test_scorm_api_all_good_produces_no_failures():
    sec = check_scorm_api(make_data([], scormdriver_js=GOOD_SCORMDRIVER_JS))
    assert "fail" not in levels(sec)


def test_missing_completion_status_is_a_failure():
    """The check the whole QA process gates on."""
    js = GOOD_SCORMDRIVER_JS.replace('"cmi.completion_status"', '"cmi.other"')
    sec = check_scorm_api(make_data([], scormdriver_js=js))
    assert "fail" in levels(sec)
    assert "cmi.completion_status not found" in text_of(sec)


def test_missing_suspend_data_is_a_failure():
    js = GOOD_SCORMDRIVER_JS.replace('"cmi.suspend_data"', '"cmi.other"')
    assert "cmi.suspend_data not found" in text_of(check_scorm_api(make_data([], scormdriver_js=js)))


def test_missing_commit_is_a_failure():
    js = GOOD_SCORMDRIVER_JS.replace("CallCommit", "NoOp")
    assert "No Commit call found" in text_of(check_scorm_api(make_data([], scormdriver_js=js)))


def test_missing_scaled_score_warns_rather_than_fails():
    js = GOOD_SCORMDRIVER_JS.replace('"cmi.score.scaled"', '"cmi.nothing"')
    sec = check_scorm_api(make_data([], scormdriver_js=js))
    assert "cmi.score.scaled not found" in text_of(sec)


def test_suspend_exit_type_passes():
    assert "suspend — correct" in text_of(
        check_scorm_api(make_data([], scormdriver_js=GOOD_SCORMDRIVER_JS))
    )


def test_non_suspend_exit_type_warns():
    js = GOOD_SCORMDRIVER_JS.replace("EXIT_TYPE_SUSPEND", "EXIT_TYPE_NORMAL")
    sec = check_scorm_api(make_data([], scormdriver_js=js))
    assert "warn" in levels(sec)
    assert "EXIT_TYPE_NORMAL" in text_of(sec)


def test_zero_forced_commit_time_warns_about_disabled_autocommit():
    js = GOOD_SCORMDRIVER_JS.replace('"60000"', '"0"')
    assert "auto-commit disabled" in text_of(check_scorm_api(make_data([], scormdriver_js=js)))


def test_forced_commit_time_is_reported_in_seconds():
    assert "60s auto-commit" in text_of(
        check_scorm_api(make_data([], scormdriver_js=GOOD_SCORMDRIVER_JS))
    )


# --- paths ----------------------------------------------------------------

def test_standard_path_only_is_reported():
    data = make_data([make_slide(is_in_menu=True)])
    assert "Standard Path only" in text_of(check_course_paths(data))


def test_no_menu_scenes_warns():
    data = make_data([make_slide(is_in_menu=False)])
    sec = check_course_paths(data)
    assert "warn" in levels(sec)
    assert "unable to classify" in text_of(sec)


def test_dual_path_course_is_detected_from_scene_title():
    data = make_data([
        make_slide(slide_id="a1", scene_id="sc1", scene_title="Module 1", is_in_menu=True),
        make_slide(slide_id="b1", scene_id="sc2", scene_title="Accessible Path", is_in_menu=False),
        make_slide(slide_id="b2", scene_id="sc2", scene_title="Accessible Path", is_in_menu=False),
    ])
    assert "BOTH" in text_of(check_course_paths(data))


# --- questions ------------------------------------------------------------

def test_questions_lists_each_interaction():
    data = make_data([make_slide(interactions=[make_interaction(question_text="Pick one")])])
    assert "Pick one" in text_of(check_questions(data))


def test_questions_handles_a_course_with_none():
    sec = check_questions(make_data([make_slide()]))
    assert sec.items  # says something rather than returning empty


# --- video speed ----------------------------------------------------------

def test_video_speed_reports_enabled_from_story():
    story = StoryData(speed_control_enabled=True)
    assert text_of(check_video_speed(make_data([]), story))


def test_video_speed_without_story_still_returns_a_section():
    sec = check_video_speed(make_data([]), None)
    assert sec.title
