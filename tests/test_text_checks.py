"""Duplicate, doubled-word and whitespace checks.

These are the checks that replaced the most manual reading, so their
false-positive behaviour matters as much as their detection: a check that
cries wolf gets ignored.
"""

from app.automation.scorm.checks.text_checks import (
    check_duplicates,
    check_whitespace_and_spelling,
)
from tests.conftest import make_data, make_slide

LONG_A = "This is a sufficiently long paragraph of body copy used for duplicate detection."
LONG_B = "An entirely different paragraph that also comfortably exceeds the length floor."


def warns(section):
    return [i.message for i in section.items if i.level == "warn"]


def levels(section):
    return {i.level for i in section.items}


# --- duplicate titles -----------------------------------------------------

def test_duplicate_titles_in_the_same_scene_are_flagged():
    data = make_data([
        make_slide(slide_id="s1", slide_title="Overview of Threats", scene_id="sc1", slide_number=1),
        make_slide(slide_id="s2", slide_title="Overview of Threats", scene_id="sc1", slide_number=2),
    ])
    assert any("Overview of Threats" in w for w in warns(check_duplicates(data)))


def test_same_title_in_different_scenes_is_not_flagged():
    """Modules legitimately reuse titles; only within-scene repeats matter."""
    data = make_data([
        make_slide(slide_id="s1", slide_title="Summary Points", scene_id="sc1"),
        make_slide(slide_id="s2", slide_title="Summary Points", scene_id="sc2"),
    ])
    assert not any("Summary Points" in w for w in warns(check_duplicates(data)))


def test_boilerplate_titles_are_exempt():
    data = make_data([
        make_slide(slide_id="s1", slide_title="Introduction", scene_id="sc1"),
        make_slide(slide_id="s2", slide_title="Introduction", scene_id="sc1"),
    ])
    assert not any("Introduction" in w for w in warns(check_duplicates(data)))


def test_dual_path_allows_exactly_two_occurrences():
    """Every slide exists twice in a dual-path course by design."""
    data = make_data([
        make_slide(slide_id="s1", slide_title="Reporting an Incident", scene_id="sc1"),
        make_slide(slide_id="s2", slide_title="Reporting an Incident", scene_id="sc1"),
    ])
    assert not any("Reporting an Incident" in w for w in warns(check_duplicates(data, dual_path=True)))


def test_dual_path_still_flags_a_third_occurrence():
    data = make_data([
        make_slide(slide_id=f"s{i}", slide_title="Reporting an Incident", scene_id="sc1")
        for i in range(3)
    ])
    assert any("Reporting an Incident" in w for w in warns(check_duplicates(data, dual_path=True)))


def test_dual_path_mode_announces_itself():
    body = "\n".join(i.message for i in check_duplicates(make_data([]), dual_path=True).items)
    assert "Dual-path mode" in body


# --- duplicate paragraphs -------------------------------------------------

def test_duplicate_paragraphs_across_slides_are_flagged():
    data = make_data([
        make_slide(slide_id="s1", texts=[LONG_A]),
        make_slide(slide_id="s2", texts=[LONG_A]),
    ])
    assert any("Duplicate paragraph" in w for w in warns(check_duplicates(data)))


def test_distinct_paragraphs_are_not_flagged():
    data = make_data([
        make_slide(slide_id="s1", texts=[LONG_A]),
        make_slide(slide_id="s2", texts=[LONG_B]),
    ])
    assert not any("Duplicate paragraph" in w for w in warns(check_duplicates(data)))


def test_short_repeated_strings_are_below_the_length_floor():
    """UI labels repeat constantly; only substantial paragraphs are compared."""
    data = make_data([
        make_slide(slide_id="s1", texts=["Next"]),
        make_slide(slide_id="s2", texts=["Next"]),
    ])
    assert not any("Duplicate paragraph" in w for w in warns(check_duplicates(data)))


def test_same_paragraph_twice_on_one_slide_is_not_cross_slide_duplication():
    data = make_data([make_slide(slide_id="s1", texts=[LONG_A, LONG_A])])
    assert not any("Duplicate paragraph" in w for w in warns(check_duplicates(data)))


def test_clean_course_reports_passes():
    data = make_data([make_slide(slide_id="s1", slide_title="Unique Title", texts=[LONG_A])])
    assert "pass" in levels(check_duplicates(data))


# --- whitespace / punctuation --------------------------------------------

def test_double_space_is_flagged():
    data = make_data([make_slide(texts=["This sentence  has a double space."])])
    body = "\n".join(i.message for i in check_whitespace_and_spelling(data, skip_spelling=True).items)
    assert "space" in body.lower()


def test_clean_text_is_not_flagged_for_spacing():
    data = make_data([make_slide(texts=["This sentence is perfectly clean."])])
    sec = check_whitespace_and_spelling(data, skip_spelling=True)
    assert "fail" not in levels(sec)


def test_skip_spelling_flag_suppresses_the_spelling_pass():
    """Non-English courses skip spelling because it hangs on foreign text."""
    data = make_data([make_slide(texts=["Bonjour tout le monde, comment allez-vous."])])
    body = "\n".join(i.message for i in check_whitespace_and_spelling(data, skip_spelling=True).items)
    assert "Possible misspelling" not in body


def test_empty_course_does_not_crash():
    sec = check_whitespace_and_spelling(make_data([]), skip_spelling=True)
    assert sec.title
