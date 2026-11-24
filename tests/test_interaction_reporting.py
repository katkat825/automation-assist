"""Static check: will question/answer interactions be sent to the LMS?"""
from app.automation.scorm.checks import check_interaction_reporting, run_all_checks
from tests.conftest import make_data, make_interaction, make_slide

DRIVER_ON = 'var DO_NOT_REPORT_INTERACTIONS = false;     //set this'
DRIVER_OFF = 'var DO_NOT_REPORT_INTERACTIONS = true;'


def _course(**kw):
    slides = kw.pop("slides", None) or [
        make_slide(slide_id="q1", scene_number=2, slide_number=1,
                   interactions=[make_interaction("i1", lms_id="Q1")]),
        make_slide(slide_id="q2", scene_number=2, slide_number=2,
                   interactions=[make_interaction("i2", lms_id="Q2")]),
    ]
    quizzes = kw.pop("quiz_configs", [{"id": "QZ", "issurvey": False, "sliderefs": [
        {"id": "_player.scn.q1"}, {"id": "_player.scn.q2"}]}])
    scorings = kw.pop("scoring_configs", [{"type": "quiz", "scoreRefs": [
        {"type": "quiz", "ref": "QZ"}]}])
    return make_data(slides, quiz_configs=quizzes, scoring_configs=scorings,
                     scormdriver_js=kw.pop("js", DRIVER_ON), **kw)


def _levels(sec):
    return [(i.level, i.message) for i in sec.items]


def test_all_questions_in_reported_quiz_pass():
    lv = _levels(check_interaction_reporting(_course()))
    assert ("fail" not in [l for l, _ in lv]) and ("warn" not in [l for l, _ in lv])
    assert any(l == "pass" and "2 of 2 graded" in m for l, m in lv)


def test_driver_kill_switch_fails():
    lv = _levels(check_interaction_reporting(_course(js=DRIVER_OFF)))
    assert any(l == "fail" and "DO_NOT_REPORT_INTERACTIONS = true" in m for l, m in lv)


def test_no_scoring_fails():
    lv = _levels(check_interaction_reporting(_course(scoring_configs=[])))
    assert any(l == "fail" and "No scoring config" in m for l, m in lv)


def test_question_outside_reported_quiz_warns_with_screen():
    quizzes = [{"id": "QZ", "sliderefs": [{"id": "_player.scn.q1"}]}]
    lv = _levels(check_interaction_reporting(_course(quiz_configs=quizzes)))
    warns = [m for l, m in lv if l == "warn"]
    assert len(warns) == 1 and warns[0].startswith("[2.2]")


def test_action_scoring_with_no_quiz_warns_every_question():
    lv = _levels(check_interaction_reporting(_course(
        quiz_configs=[], scoring_configs=[{"type": "action", "scoreRefs": []}])))
    assert len([1 for l, _ in lv if l == "warn"]) == 2
    assert any("doesn't reference a quiz" in m for _, m in lv)


def test_combined_results_quiz_passes_scoring_to_child_quizzes():
    quizzes = [{"id": "QZ", "sliderefs": [{"id": "_player.scn.q1"}]},
               {"id": "QZ2", "sliderefs": [{"id": "_player.scn.q2"}]},
               {"id": "ALL", "sliderefs": [], "quizrefs": [
                   {"id": "_player.QZ"}, {"id": "_player.QZ2"}]}]
    scorings = [{"type": "quiz", "scoreRefs": [{"type": "quiz", "ref": "ALL"}]}]
    lv = _levels(check_interaction_reporting(_course(
        quiz_configs=quizzes, scoring_configs=scorings)))
    assert not [m for l, m in lv if l == "warn"]


def test_no_questions_is_info_only():
    sec = check_interaction_reporting(make_data([make_slide()], scormdriver_js=DRIVER_ON))
    assert {i.level for i in sec.items} <= {"pass", "info"}


def test_section_is_in_full_report():
    titles = [s.title for s in run_all_checks(_course()).sections]
    assert "Question Interaction Reporting" in titles
