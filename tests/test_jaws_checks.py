"""Accessible-path detection and screen-reader checks.

Dual-path courses ship a rich-media path and a screen-reader path in one
package. Detecting which scenes belong to which is the foundation every
other accessibility check builds on, so it gets the most coverage here.
"""

from app.automation.scorm import jaws_checks
from tests.conftest import make_data, make_layer, make_obj, make_slide


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
