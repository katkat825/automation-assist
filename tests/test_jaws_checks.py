"""Accessible-path detection and screen-reader checks.

Dual-path courses ship a rich-media path and a screen-reader path in one
package. Detecting which scenes belong to which is the foundation every
other accessibility check builds on, so it gets the most coverage here.
"""

from app.automation.scorm import jaws_checks
from tests.conftest import (
    make_data,
    make_interaction,
    make_layer,
    make_obj,
    make_slide,
)


def std_slide(**kw):
    kw.setdefault("is_in_menu", True)
    kw.setdefault("scene_id", "std")
    kw.setdefault("scene_title", "Module 1")
    return make_slide(**kw)


def acc_slide(**kw):
    kw.setdefault("is_in_menu", False)
    kw.setdefault("scene_id", "acc")
    kw.setdefault("scene_title", "Accessible Path")
    return make_slide(**kw)


# --- explicit naming rule -------------------------------------------------

def test_scene_titled_accessible_path_is_detected():
    data = make_data([std_slide(slide_id="s1"), acc_slide(slide_id="a1")])
    assert "acc" in jaws_checks.get_accessible_path_scene_ids(data)


def test_detection_is_case_insensitive():
    data = make_data([acc_slide(slide_id="a1", scene_title="ACCESSIBLE PATH")])
    assert "acc" in jaws_checks.get_accessible_path_scene_ids(data)


def test_single_path_course_has_no_accessible_scenes():
    data = make_data([std_slide(slide_id="s1"), std_slide(slide_id="s2")])
    assert jaws_checks.get_accessible_path_scene_ids(data) == set()


def test_scene_titled_accessible_dash_module_is_detected():
    """Courses authored as standard-path-then-accessible-path often name the
    accessible scenes "Accessible - <module>" rather than "Accessible Path".
    These scenes are ALSO menu-bearing, so the parallel-structure rule can't
    catch them — the "begins with Accessible" naming rule must. (Mirrors the
    real 11570A OWASP course.)"""
    data = make_data([
        std_slide(slide_id="s1", scene_id="std", scene_title="A01"),
        make_slide(slide_id="a1", scene_id="acc",
                   scene_title="Accessible - A01", is_in_menu=True),
    ])
    assert "acc" in jaws_checks.get_accessible_path_scene_ids(data)


def test_accessibility_content_module_is_not_a_false_positive():
    """A standard content module *about* accessibility (title begins with
    "Accessibility", not the word "Accessible") must not be mistaken for the
    accessible path."""
    data = make_data([
        std_slide(slide_id="s1", scene_id="std", scene_title="Intro"),
        std_slide(slide_id="s2", scene_id="a11y", scene_title="Accessibility Basics"),
    ])
    assert "a11y" not in jaws_checks.get_accessible_path_scene_ids(data)


# --- parallel-structure rule ---------------------------------------------

def test_untitled_scene_mirroring_menu_titles_is_detected():
    """Some courses leave accessible scene titles blank but mirror the modules."""
    data = make_data([
        std_slide(slide_id="s1", slide_title="Passwords"),
        std_slide(slide_id="s2", slide_title="Phishing"),
        make_slide(slide_id="a1", slide_title="Passwords", scene_id="acc",
                   scene_title="", is_in_menu=False),
        make_slide(slide_id="a2", slide_title="Phishing", scene_id="acc",
                   scene_title="", is_in_menu=False),
    ])
    assert "acc" in jaws_checks.get_accessible_path_scene_ids(data)


def test_single_overlapping_title_is_below_the_threshold():
    """One shared title is coincidence, not a mirrored module."""
    data = make_data([
        std_slide(slide_id="s1", slide_title="Passwords"),
        make_slide(slide_id="x1", slide_title="Passwords", scene_id="other",
                   scene_title="", is_in_menu=False),
        make_slide(slide_id="x2", slide_title="Unrelated", scene_id="other",
                   scene_title="", is_in_menu=False),
    ])
    assert "other" not in jaws_checks.get_accessible_path_scene_ids(data)


def test_a_scene_with_one_slide_is_never_a_mirror():
    data = make_data([
        std_slide(slide_id="s1", slide_title="Passwords"),
        make_slide(slide_id="x1", slide_title="Passwords", scene_id="tiny",
                   scene_title="", is_in_menu=False),
    ])
    assert "tiny" not in jaws_checks.get_accessible_path_scene_ids(data)


def test_menu_bearing_scenes_are_never_accessible_path():
    data = make_data([
        std_slide(slide_id="s1", slide_title="Passwords"),
        std_slide(slide_id="s2", slide_title="Phishing"),
    ])
    assert jaws_checks.get_accessible_path_scene_ids(data) == set()


# --- slide partitioning ---------------------------------------------------

def test_accessible_and_standard_slide_sets_are_disjoint():
    data = make_data([std_slide(slide_id="s1"), acc_slide(slide_id="a1"), acc_slide(slide_id="a2")])
    acc = {s.slide_id for s in jaws_checks.get_accessible_path_slides(data)}
    std = {s.slide_id for s in jaws_checks.get_standard_path_slides(data)}
    assert acc & std == set()
    assert "a1" in acc and "s1" in std


def test_has_accessible_path_reflects_detection():
    dual = make_data([std_slide(slide_id="s1"), acc_slide(slide_id="a1")])
    single = make_data([std_slide(slide_id="s1")])
    assert jaws_checks.has_accessible_path(dual) is True
    assert jaws_checks.has_accessible_path(single) is False


# --- path mapping ---------------------------------------------------------

def test_path_mapping_key_is_scene_and_slide():
    slide = make_slide(scene_id="sc9", slide_id="sl9")
    assert jaws_checks.path_mapping_key(slide) == "sc9::sl9"


def test_path_mapping_key_is_public():
    """checks/reports.py calls this across the module boundary."""
    assert not jaws_checks.path_mapping_key.__name__.startswith("_")


def test_build_path_mapping_reports_both_paths():
    data = make_data([std_slide(slide_id="s1"), acc_slide(slide_id="a1"), acc_slide(slide_id="a2")])
    mapping = jaws_checks.build_path_mapping(data)
    assert mapping["has_standard"] is True
    assert mapping["has_accessible"] is True


def test_build_path_mapping_on_single_path_course():
    mapping = jaws_checks.build_path_mapping(make_data([std_slide(slide_id="s1")]))
    assert mapping["has_standard"] is True
    assert mapping["has_accessible"] is False


def test_build_path_mapping_exposes_the_documented_keys():
    """checks/course_checks.py and reports.py read these by name."""
    mapping = jaws_checks.build_path_mapping(make_data([std_slide(slide_id="s1")]))
    for key in ("has_standard", "has_accessible", "accessible_only_slides", "acc_numbers_for"):
        assert key in mapping


# --- title normalisation --------------------------------------------------

def test_normalise_title_collapses_whitespace_and_case():
    assert jaws_checks._normalise_title("  Cyber   HYGIENE  ") == "cyber hygiene"


def test_normalise_title_strips_trailing_punctuation():
    assert jaws_checks._normalise_title("Are you ready?") == "are you ready"


def test_normalise_title_handles_none_and_empty():
    assert jaws_checks._normalise_title("") == ""
    assert jaws_checks._normalise_title(None) == ""


# --- section wiring -------------------------------------------------------

def test_run_jaws_checks_returns_sections():
    data = make_data([std_slide(slide_id="s1"), acc_slide(slide_id="a1")])
    sections = jaws_checks.run_jaws_checks(data)
    assert sections
    assert all(hasattr(s, "title") and hasattr(s, "items") for s in sections)


def test_jaws_checks_share_the_one_section_class():
    """Regression: jaws_checks used a deferred import to dodge a cycle."""
    from app.automation.scorm.checks.models import Section
    assert jaws_checks.Section is Section


def test_checks_tolerate_slides_with_layers_and_objects():
    layer = make_layer(objects=[make_obj(obj_id="o1", text="Continue", has_text=True)])
    data = make_data([std_slide(slide_id="s1", layers=[layer])])
    assert jaws_checks.run_jaws_checks(data) is not None


def test_checks_tolerate_an_empty_course():
    assert jaws_checks.run_jaws_checks(make_data([])) is not None


# --- radio-button Tab-key instructions -----------------------------------
#
# In the Accessible Path, a single-select question is a radio group; pressing
# the Down arrow SELECTS the next option rather than just moving to it, so the
# slide must tell the learner to use the Tab key. See
# jaws_checks.check_radio_tab_instructions.

TAB_TEXT = "Use the Tab key to move between the answer choices."


def _radio_levels(data):
    """Return the set of item levels in the radio-Tab section."""
    sec = jaws_checks.check_radio_tab_instructions(data)
    return sec, {item.level for item in sec.items}


def test_radio_question_without_tab_text_is_flagged():
    q = make_interaction(question_type="multiplechoice")
    data = make_data([acc_slide(slide_id="a1", interactions=[q], texts=["Pick one."])])
    sec, levels = _radio_levels(data)
    assert "warn" in levels


def test_radio_question_with_tab_text_passes():
    q = make_interaction(question_type="multiplechoice")
    data = make_data([
        acc_slide(slide_id="a1", interactions=[q], texts=["Pick one.", TAB_TEXT])
    ])
    sec, levels = _radio_levels(data)
    assert "warn" not in levels
    assert "pass" in levels


def test_truefalse_is_treated_as_radio():
    q = make_interaction(question_type="truefalse")
    data = make_data([acc_slide(slide_id="a1", interactions=[q], texts=["True or false?"])])
    _, levels = _radio_levels(data)
    assert "warn" in levels


def test_multipleresponse_checkbox_is_exempt():
    """Checkboxes don't auto-select on arrow, so no Tab instruction is needed."""
    q = make_interaction(question_type="multipleresponse")
    data = make_data([acc_slide(slide_id="a1", interactions=[q], texts=["Select all."])])
    sec, levels = _radio_levels(data)
    assert "warn" not in levels
    # No radio questions at all -> the informational "none found" message.
    assert "info" in levels


def test_survey_radio_question_is_exempt():
    q = make_interaction(question_type="multiplechoice", is_survey=True)
    data = make_data([acc_slide(slide_id="a1", interactions=[q], texts=["Your opinion?"])])
    _, levels = _radio_levels(data)
    assert "warn" not in levels


def test_standard_path_radio_question_is_not_checked():
    """Only the Accessible Path is subject to JAWS checks."""
    q = make_interaction(question_type="multiplechoice")
    data = make_data([
        std_slide(slide_id="s1", interactions=[q], texts=["Pick one."]),
        acc_slide(slide_id="a1", texts=["Accessible intro."]),
    ])
    sec, levels = _radio_levels(data)
    assert "warn" not in levels


def test_radio_check_is_in_the_runner():
    q = make_interaction(question_type="multiplechoice")
    data = make_data([
        std_slide(slide_id="s1"),
        acc_slide(slide_id="a1", interactions=[q], texts=["Pick one."]),
    ])
    titles = [s.title for s in jaws_checks.run_jaws_checks(data)]
    assert "Radio-Button Tab Instructions (JAWS)" in titles
