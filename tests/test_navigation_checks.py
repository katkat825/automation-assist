"""Previous-button navigation checks.

The player Previous button must move to the immediately-preceding screen in
presentation order (scene_number, slide_number). A fixed target elsewhere is a
hard failure; an unreadable/branching target is a manual-review warning; a
slide with no explicit override uses the player's default and is left alone.

Custom Back buttons wired to last-viewed navigation (history_prev) are a
failure, but only on the base layer — dialog/lightbox 'return' buttons are
excluded by design.
"""

from app.automation.scorm.checks.navigation_checks import check_prev_navigation
from tests.conftest import make_data, make_slide


def levels(section):
    return [i.level for i in section.items]


def text_of(section):
    return "\n".join(i.message for i in section.items)


def linear_course():
    """Three screens 1.1 -> 1.2 -> 1.3, each Prev pointing to its predecessor."""
    s1 = make_slide(slide_id="s1", slide_title="One", slide_number=1)
    s2 = make_slide(slide_id="s2", slide_title="Two", slide_number=2,
                    prev_nav_kind="gotoplay", prev_nav_target="s1")
    s3 = make_slide(slide_id="s3", slide_title="Three", slide_number=3,
                    prev_nav_kind="gotoplay", prev_nav_target="s2")
    return make_data([s1, s2, s3]), (s1, s2, s3)


# --- correct navigation ---------------------------------------------------

def test_correct_linear_prev_produces_no_failures():
    data, _ = linear_course()
    sec = check_prev_navigation(data)
    assert "fail" not in levels(sec)
    assert "pass" in levels(sec)


def test_first_screen_is_not_compared():
    """The first screen has no predecessor; a missing target must not fail."""
    s1 = make_slide(slide_id="s1", slide_number=1)  # prev_nav_kind defaults to ""
    s2 = make_slide(slide_id="s2", slide_number=2,
                    prev_nav_kind="gotoplay", prev_nav_target="s1")
    sec = check_prev_navigation(make_data([s1, s2]))
    assert "fail" not in levels(sec)


def test_default_runtime_prev_is_not_flagged():
    """kind='none' means no override -> the player's default linear prev -> OK."""
    s1 = make_slide(slide_id="s1", slide_number=1, prev_nav_kind="none")
    s2 = make_slide(slide_id="s2", slide_number=2, prev_nav_kind="none")
    sec = check_prev_navigation(make_data([s1, s2]))
    assert "fail" not in levels(sec)
    assert "warn" not in levels(sec)


# --- the known-bad case ---------------------------------------------------

def test_prev_pointing_to_wrong_screen_fails():
    s1 = make_slide(slide_id="s1", slide_title="Course Title", slide_number=1)
    s2 = make_slide(slide_id="s2", slide_title="What is Phishing?", slide_number=2)
    # 3rd screen's Prev points back to screen 1 instead of screen 2
    s3 = make_slide(slide_id="s3", slide_title="Two Truths and a Lie", slide_number=3,
                    prev_nav_kind="gotoplay", prev_nav_target="s1")
    sec = check_prev_navigation(make_data([s1, s2, s3]))
    assert "fail" in levels(sec)
    body = text_of(sec)
    assert "Two Truths and a Lie" in body
    assert "preceding screen is 1.2 'What is Phishing?'" in body


# --- history navigation on the player Prev button -------------------------

def test_player_prev_using_history_fails():
    s1 = make_slide(slide_id="s1", slide_number=1)
    s2 = make_slide(slide_id="s2", slide_number=2, prev_nav_kind="history_prev")
    sec = check_prev_navigation(make_data([s1, s2]))
    assert "fail" in levels(sec)
    assert "last-viewed" in text_of(sec)


# --- uncheckable cases warn, never fail -----------------------------------

def test_conditional_target_warns():
    s1 = make_slide(slide_id="s1", slide_number=1)
    s2 = make_slide(slide_id="s2", slide_number=2, prev_nav_kind="conditional")
    sec = check_prev_navigation(make_data([s1, s2]))
    assert "warn" in levels(sec)
    assert "fail" not in levels(sec)


def test_unparsed_slide_warns():
    s1 = make_slide(slide_id="s1", slide_number=1)
    s2 = make_slide(slide_id="s2", slide_number=2, prev_nav_kind="")  # never analyzed
    sec = check_prev_navigation(make_data([s1, s2]))
    assert "warn" in levels(sec)


# --- custom base-layer Back buttons ---------------------------------------

def test_base_layer_history_back_button_fails():
    s1 = make_slide(slide_id="s1", slide_number=1, prev_nav_kind="none")
    s2 = make_slide(slide_id="s2", slide_number=2, prev_nav_kind="none",
                    history_prev_buttons=2)
    sec = check_prev_navigation(make_data([s1, s2]))
    assert "fail" in levels(sec)
    assert "2 base-layer Back button" in text_of(sec)


def test_no_history_back_buttons_no_failure():
    s1 = make_slide(slide_id="s1", slide_number=1, prev_nav_kind="none")
    s2 = make_slide(slide_id="s2", slide_number=2, prev_nav_kind="none",
                    history_prev_buttons=0)
    sec = check_prev_navigation(make_data([s1, s2]))
    assert "fail" not in levels(sec)


# --- ordering is by presentation order, not list order --------------------

def test_expected_predecessor_uses_scene_then_slide_order():
    """A scene-2 first slide's predecessor is the last slide of scene 1."""
    a = make_slide(slide_id="a", slide_title="1.1", scene_number=1, slide_number=1)
    b = make_slide(slide_id="b", slide_title="1.2", scene_number=1, slide_number=2)
    # scene 2 slide 1, Prev correctly points to 1.2 (b)
    c = make_slide(slide_id="c", slide_title="2.1", scene_number=2, slide_number=1,
                   prev_nav_kind="gotoplay", prev_nav_target="b")
    # feed them out of order to prove sorting
    sec = check_prev_navigation(make_data([c, a, b]))
    assert "fail" not in levels(sec)


def test_empty_course_warns_and_does_not_crash():
    sec = check_prev_navigation(make_data([]))
    assert "warn" in levels(sec)
