"""Terminology consistency and untranslated-English detection.

Both run against translated courses, where the failure mode that matters is
noise: flagging correct text trains you to ignore the check.
"""

from app.automation.scorm.checks.language_checks import (
    check_terminology,
    check_untranslated_english,
)
from tests.conftest import make_data, make_slide


def warns(section):
    return [i.message for i in section.items if i.level == "warn"]


def levels(section):
    return {i.level for i in section.items}


# --- terminology ----------------------------------------------------------

def test_mixed_spelling_within_a_package_is_flagged():
    data = make_data([
        make_slide(slide_id="s1", texts=["Send an email to the team."]),
        make_slide(slide_id="s2", texts=["Send an e-mail to the team."]),
    ])
    assert any("email" in w and "e-mail" in w for w in warns(check_terminology(data)))


def test_mixed_spelling_on_a_single_slide_is_flagged():
    data = make_data([make_slide(texts=["Send an email or an e-mail."])])
    assert warns(check_terminology(data))


def test_one_spelling_used_consistently_passes():
    data = make_data([
        make_slide(slide_id="s1", texts=["Send an email to the team."]),
        make_slide(slide_id="s2", texts=["Another email arrived."]),
    ])
    assert "pass" in levels(check_terminology(data))


def test_the_non_preferred_spelling_alone_is_still_consistent():
    """Which form wins does not matter — only that the package picks one."""
    data = make_data([
        make_slide(slide_id="s1", texts=["Send an e-mail to the team."]),
        make_slide(slide_id="s2", texts=["Another e-mail arrived."]),
    ])
    assert "pass" in levels(check_terminology(data))


def test_website_used_alone_is_not_flagged():
    data = make_data([make_slide(texts=["Visit our website for details."])])
    assert "pass" in levels(check_terminology(data))


def test_website_mixed_with_web_site_is_flagged():
    data = make_data([
        make_slide(slide_id="s1", texts=["Visit our website for details."]),
        make_slide(slide_id="s2", texts=["Visit our web site for details."]),
    ])
    assert warns(check_terminology(data))


def test_capitalisation_counts_as_a_different_form():
    data = make_data([
        make_slide(slide_id="s1", scene_number=1, slide_number=1,
                   texts=["Browse the internet safely."]),
        make_slide(slide_id="s2", scene_number=2, slide_number=5,
                   texts=["The Internet is a big place."]),
    ])
    warning = warns(check_terminology(data))[0]
    assert "internet" in warning and "Internet" in warning
    assert "1.1" in warning and "2.5" in warning


def test_consistent_capitalisation_passes():
    data = make_data([
        make_slide(slide_id="s1", texts=["The Internet is a big place."]),
        make_slide(slide_id="s2", texts=["Use the Internet safely."]),
    ])
    assert "pass" in levels(check_terminology(data))


def test_sentence_initial_capital_is_not_an_inconsistency():
    """"Email us. Send an email." is ordinary English, not mixed terminology."""
    data = make_data([make_slide(texts=["Email us today. Then send an email."])])
    assert "pass" in levels(check_terminology(data))


def test_both_forms_and_their_locations_are_reported():
    data = make_data([
        make_slide(slide_id="s1", scene_number=1, slide_number=2, texts=["An email arrived."]),
        make_slide(slide_id="s2", scene_number=3, slide_number=4, texts=["An e-mail arrived."]),
    ])
    warning = warns(check_terminology(data))[0]
    assert "1.2" in warning and "3.4" in warning


def test_one_warning_per_term_not_per_occurrence():
    data = make_data([
        make_slide(slide_id=f"s{i}", texts=["An email and an e-mail."]) for i in range(5)
    ])
    assert len(warns(check_terminology(data))) == 1


def test_word_boundaries_prevent_matches_inside_longer_words():
    data = make_data([make_slide(texts=["The emailer tool and the emails folder."])])
    assert "pass" in levels(check_terminology(data))


def test_a_longer_unrelated_word_is_not_treated_as_a_form():
    """Without word boundaries "Internetworking" registers as "Internet" and is
    reported as mixed usage against a plain "internet".

    The longer word sits mid-sentence deliberately: at a sentence start its
    capital would be normalised away and the bug would stay hidden.
    """
    data = make_data([
        make_slide(slide_id="s1", scene_number=1, texts=["Browse the internet."]),
        make_slide(slide_id="s2", scene_number=2,
                   texts=["We also cover Internetworking, a different topic."]),
    ])
    assert "pass" in levels(check_terminology(data))


def test_all_caps_styling_is_not_a_separate_spelling():
    """Headings are often upper-cased. Case-sensitive matching means "EMAIL"
    simply isn't one of the tracked forms, so it cannot fake an inconsistency."""
    data = make_data([
        make_slide(slide_id="s1", texts=["EMAIL SECURITY"]),
        make_slide(slide_id="s2", texts=["Please send an email."]),
    ])
    assert "pass" in levels(check_terminology(data))


def test_course_without_text_warns_rather_than_passing_silently():
    data = make_data([make_slide(texts=[])])
    sec = check_terminology(data)
    assert "warn" in levels(sec)
    assert "No slide text" in "\n".join(i.message for i in sec.items)


def test_a_note_about_scope_is_always_appended():
    data = make_data([make_slide(texts=["Send an email."])])
    assert "only checks predefined pairs" in "\n".join(i.message for i in check_terminology(data).items)


# --- untranslated English -------------------------------------------------

def test_english_sentence_in_a_translated_course_is_flagged():
    data = make_data([make_slide(texts=["Please review the security policy before continuing."])])
    assert warns(check_untranslated_english(data, target_lang="es"))


def test_short_english_fragments_are_below_the_threshold():
    """A stray acronym or brand name should not trip the check."""
    data = make_data([make_slide(texts=["VPN"])])
    assert not warns(check_untranslated_english(data, target_lang="es"))


def test_urls_do_not_count_as_english_text():
    data = make_data([make_slide(texts=["https://example.com/security/policy/review"])])
    assert not warns(check_untranslated_english(data, target_lang="es"))


def test_email_addresses_do_not_count_as_english_text():
    data = make_data([make_slide(texts=["soporte@example.com"])])
    assert not warns(check_untranslated_english(data, target_lang="es"))


def test_accented_text_is_not_treated_as_english():
    data = make_data([make_slide(texts=["Revisión de la política de seguridad непонятно"])])
    assert not warns(check_untranslated_english(data, target_lang="es"))


def test_empty_course_does_not_crash():
    assert check_untranslated_english(make_data([]), target_lang="es").title


# --- target-language dictionary behavior (deterministic; fake checker) -----
#
# These exercise the target-language subtraction without needing pyspellchecker
# installed, by monkeypatching a fake checker whose vocabulary we control.

from app.automation.scorm.checks import language_checks as _lc


class _FakeSpell:
    """Minimal stand-in for pyspellchecker. Vocabulary depends on the
    requested language, mirroring how a real per-language dictionary differs.
    ``unknown(words)`` returns the words NOT in that vocabulary (real API)."""

    _VOCAB = {
        None: {"zzalpha", "zzbeta", "zzgamma", "zzone", "zztwo", "zzthree"},  # "English"
        "en": {"zzalpha", "zzbeta", "zzgamma", "zzone", "zztwo", "zzthree"},
        "es": {"zzalpha", "zzbeta", "zzgamma"},  # shared cognates only
    }

    def __init__(self, language=None):
        self._vocab = self._VOCAB.get(language, set())

    def unknown(self, words):
        return {w for w in words if w.lower() not in self._vocab}


def _patch_fake(monkeypatch):
    monkeypatch.setattr(_lc, "_SPELLCHECKER_AVAILABLE", True)
    monkeypatch.setattr(_lc, "_SpellChecker", _FakeSpell)


def test_untranslated_skipped_when_no_language_selected(monkeypatch):
    _patch_fake(monkeypatch)
    data = make_data([make_slide(texts=["zzone zztwo zzthree extra"])])
    sec = check_untranslated_english(data, target_lang=None)
    assert not warns(sec)
    assert any("no course language" in i.message.lower() for i in sec.items)


def test_untranslated_skipped_for_unsupported_language(monkeypatch):
    _patch_fake(monkeypatch)
    data = make_data([make_slide(texts=["zzone zztwo zzthree extra"])])
    sec = check_untranslated_english(data, target_lang="zh-CN")
    assert not warns(sec)
    assert any("no dictionary available" in i.message.lower() for i in sec.items)


def test_english_only_run_is_flagged_in_supported_language(monkeypatch):
    _patch_fake(monkeypatch)
    data = make_data([make_slide(texts=["zzone zztwo zzthree together here"])])
    assert warns(check_untranslated_english(data, target_lang="es-LA"))


def test_target_language_words_are_not_flagged(monkeypatch):
    """Words valid in the target language are not untranslated English, even
    though the English dictionary also knows them (cognate suppression)."""
    _patch_fake(monkeypatch)
    data = make_data([make_slide(texts=["zzalpha zzbeta zzgamma word"])])
    assert not warns(check_untranslated_english(data, target_lang="es"))


def test_missing_target_dictionary_skips_gracefully(monkeypatch):
    """If the named language's dictionary can't be loaded, skip with a notice
    rather than crashing."""
    monkeypatch.setattr(_lc, "_SPELLCHECKER_AVAILABLE", True)

    class _Raises:
        def __init__(self, language=None):
            if language is not None:
                raise RuntimeError("no dictionary")
        def unknown(self, words):
            return set(words)

    monkeypatch.setattr(_lc, "_SpellChecker", _Raises)
    data = make_data([make_slide(texts=["zzone zztwo zzthree here"])])
    sec = check_untranslated_english(data, target_lang="de")
    assert not warns(sec)
    assert any("could not load" in i.message.lower() for i in sec.items)
