"""Duplicate, doubled-word and whitespace checks.

These are the checks that replaced the most manual reading, so their
false-positive behaviour matters as much as their detection: a check that
cries wolf gets ignored.
"""

from app.automation.scorm.checks import text_checks
from app.automation.scorm.checks.text_checks import (
    _is_known_compound,
    check_duplicates,
    check_whitespace_and_spelling,
)
from tests.conftest import make_data, make_slide


class _FakeChecker:
    """Stand-in for pyspellchecker so these tests run without the library
    (and its dictionary) installed. ``known`` mirrors the real method: it
    returns the subset of the given words that are 'known'."""

    def __init__(self, vocabulary):
        self._vocab = {w.lower() for w in vocabulary}

    def known(self, words):
        return {w for w in words if w.lower() in self._vocab}

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


# --- em-dash / hyphen: dictionary-based compound suppression --------------
#
# The unspaced-hyphen-pair rule flags "word-word" as a *possible* missed
# em-dash. Legitimate compounds whose parts are all real dictionary words are
# suppressed (English courses only) so the hand-maintained allowlist doesn't
# have to enumerate every compound. See text_checks._is_known_compound.

def test_is_known_compound_all_parts_known():
    checker = _FakeChecker({"customer", "focused"})
    assert _is_known_compound(["customer", "focused"], checker) is True


def test_is_known_compound_with_unknown_part():
    checker = _FakeChecker({"customer"})  # "focused" not in vocab
    assert _is_known_compound(["customer", "focused"], checker) is False


def test_is_known_compound_without_a_checker():
    assert _is_known_compound(["customer", "focused"], None) is False


def test_is_known_compound_ignores_short_and_numeric_parts():
    """Single letters and numbers shouldn't disqualify a real compound."""
    checker = _FakeChecker({"learning"})
    assert _is_known_compound(["e", "learning"], checker) is True
    assert _is_known_compound(["top", "10"], _FakeChecker({"top"})) is True


def test_known_compound_is_not_flagged_as_missed_em_dash(monkeypatch):
    monkeypatch.setattr(
        text_checks, "_get_spell_checker",
        lambda: _FakeChecker({"customer", "focused"}),
    )
    data = make_data([make_slide(texts=["We take a customer-focused approach."])])
    body = "\n".join(
        i.message for i in check_whitespace_and_spelling(data, skip_spelling=False).items
    )
    assert "customer-focused" not in body


def test_unknown_hyphenated_pair_is_still_flagged(monkeypatch):
    monkeypatch.setattr(
        text_checks, "_get_spell_checker",
        lambda: _FakeChecker({"customer"}),  # second part unknown
    )
    data = make_data([make_slide(texts=["A strange foo-zzz token here."])])
    warned = warns(check_whitespace_and_spelling(data, skip_spelling=False))
    assert any("foo-zzz" in w for w in warned)


def test_non_english_course_skips_dash_checks(monkeypatch):
    """Non-English courses skip the whole em-dash / hyphen dash family, because
    those heuristics are English typography and misfire on foreign text. No dash
    warning should surface, no matter what the hyphen looks like, and the spell
    checker is never built."""
    calls = []
    monkeypatch.setattr(
        text_checks, "_get_spell_checker",
        lambda: calls.append(1) or _FakeChecker({"customer", "focused"}),
    )
    data = make_data([make_slide(texts=[
        "We take a customer-focused approach.",   # unspaced hyphen pair
        "The plan failed -- we regrouped.",        # double hyphen
        "A break — then more text.",                # spaced em-dash
    ])])
    sec = check_whitespace_and_spelling(data, skip_spelling=True)
    warned = warns(sec)
    assert not any("customer-focused" in w for w in warned)
    assert not any("em-dash is intended" in w for w in warned)
    assert not any("Spaced em-dash" in w for w in warned)
    assert calls == []  # checker never built when spelling is skipped
    # A single skipped-notice explains why, instead of a misleading pass.
    assert any(
        "hyphen checks skipped" in i.message.lower() for i in sec.items
    )


def test_english_course_still_runs_dash_checks(monkeypatch):
    """Sanity guard: the English path is unchanged — dash checks still fire."""
    monkeypatch.setattr(
        text_checks, "_get_spell_checker",
        lambda: _FakeChecker({"customer"}),  # "focused" unknown -> flagged
    )
    data = make_data([make_slide(texts=["We take a customer-focused approach."])])
    warned = warns(check_whitespace_and_spelling(data, skip_spelling=False))
    assert any("customer-focused" in w for w in warned)


def test_spaced_hyphen_em_dash_flags_regardless_of_dictionary(monkeypatch):
    """The double/spaced-hyphen signal is independent of the compound check."""
    monkeypatch.setattr(
        text_checks, "_get_spell_checker",
        lambda: _FakeChecker({"plan", "we", "regrouped"}),
    )
    data = make_data([make_slide(texts=["The plan failed -- we regrouped."])])
    warned = warns(check_whitespace_and_spelling(data, skip_spelling=False))
    assert warned  # spaced/double hyphen still surfaces
