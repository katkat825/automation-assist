"""Per-screen companion index.

build_companion_data assembles the data the live panel renders: each slide keyed
by its runtime slide_id, with the static-check findings and Q&A for that screen.
"""

from app.automation.scorm.companion import _screen_number_regex, build_companion_data
from app.automation.scorm.parser import Choice
from tests.conftest import (
    make_data,
    make_interaction,
    make_layer,
    make_obj,
    make_slide,
)


def test_index_is_keyed_by_slide_id_with_screen_fields():
    data = make_data([make_slide(slide_id="s1", scene_number=1, slide_number=1)])
    cd = build_companion_data(data)
    assert "s1" in cd["slides"]
    entry = cd["slides"]["s1"]
    assert entry["slide_id"] == "s1"
    assert entry["screen_number"] == "1.1"
    assert "screen_id" in entry  # m#s# from the screen table (may be "")


def test_numeric_tagged_finding_buckets_to_its_screen():
    # A double space triggers a whitespace warning tagged "[1.1]".
    data = make_data([
        make_slide(slide_id="s1", scene_number=1, slide_number=1,
                   texts=["This  has a double space."]),
    ])
    cd = build_companion_data(data)
    checks = cd["slides"]["s1"]["checks"]
    assert any("[1.1]" in c["message"] and c["level"] == "warn" for c in checks)


def test_questions_are_attached_and_ordered():
    ia = make_interaction(
        question_type="multiplechoice",
        choices=[Choice(id="c2", text="B. Second"), Choice(id="c1", text="A. First")],
        correct_choice_ids=["c1"],
    )
    data = make_data([make_slide(slide_id="s1", interactions=[ia])])
    q = cd_first_question(build_companion_data(data), "s1")
    assert q["order_verified"] is True
    assert [c["text"] for c in q["choices"]] == ["A. First", "B. Second"]
    assert q["choices"][0]["correct"] is True     # ✓ stays on "A. First"


def cd_first_question(cd, sid):
    return cd["slides"][sid]["questions"][0]


def test_questions_section_is_not_duplicated_into_checks():
    ia = make_interaction()
    data = make_data([make_slide(slide_id="s1", interactions=[ia])])
    cd = build_companion_data(data)
    assert all(c["section"] != "Questions & Correct Answers"
               for c in cd["slides"]["s1"]["checks"])


def test_jaws_finding_buckets_by_slide_label_on_accessible_path():
    """JAWS checks tag findings with the slide label, not [scene.slide] — the
    index must still route them to the right accessible slide."""
    unreachable_btn = make_obj(
        obj_id="b1", acc_type="button", text="Submit", has_text=True,
        tab_enabled=False, tab_index=-1,
    )
    acc = make_slide(
        slide_id="a1", slide_title="Sign In", scene_id="acc",
        scene_title="Accessible Path", is_in_menu=False,
        layers=[make_layer(objects=[unreachable_btn])],
    )
    std = make_slide(slide_id="s1", scene_id="std", scene_title="Module 1")
    data = make_data([std, acc])

    cd = build_companion_data(data, dual_path=True)
    acc_checks = cd["slides"]["a1"]["checks"]
    assert any("Keyboard Navigation" in c["section"] for c in acc_checks), acc_checks


def test_empty_course_does_not_crash():
    cd = build_companion_data(make_data([]))
    assert cd["slides"] == {}


# --- screen-number matching (bracketed OR bare) ---------------------------
# Checks aren't consistent: whitespace/dash use "[2.3]", but spelling and a few
# others write a bare "… — 2.3; 5.1". Both must route to the right screen, and
# a screen number must not match inside a longer number.

def test_screen_number_regex_matches_bracketed_and_bare():
    rx = _screen_number_regex(["2.3", "5.1", "10.2"])
    assert set(rx.findall("warn on [2.3]")) == {"2.3"}
    assert set(rx.findall('misspelling "x" — 2.3; 5.1')) == {"2.3", "5.1"}


def test_screen_number_regex_respects_boundaries():
    rx = _screen_number_regex(["2.3"])
    assert rx.findall("value 12.34 here") == []   # not inside a longer number
    assert rx.findall("path 2.3.4 here") == []    # not a version string


def test_screen_number_regex_none_when_no_numbers():
    assert _screen_number_regex([]) is None
