"""Invariants for the check vocabulary.

wordlists.py is edited by hand, often in a hurry, whenever a course throws
up a new false positive. These tests catch the mistakes that editing makes
easy: an uppercase entry that will never match, a stray whitespace, a regex
that no longer compiles, a duplicated terminology pair.
"""

import re

import pytest

from app.automation.scorm.checks import wordlists as W

WORD_SETS = {
    "_SPELL_IGNORE": W._SPELL_IGNORE,
    "_ENGLISH_OK_TERMS": W._ENGLISH_OK_TERMS,
    "_NON_ENGLISH_FALSE_POSITIVES": W._NON_ENGLISH_FALSE_POSITIVES,
    "_DOUBLED_WORD_IGNORE": W._DOUBLED_WORD_IGNORE,
    "_HYPHEN_PREFIXES": W._HYPHEN_PREFIXES,
    "_HYPHEN_SUFFIXES": W._HYPHEN_SUFFIXES,
    "_HYPHEN_COMPOUND_OK": W._HYPHEN_COMPOUND_OK,
}

REGEXES = {
    "_IGNORE_DUP_TITLE": W._IGNORE_DUP_TITLE,
    "_IGNORE_DUP_SENTENCE": W._IGNORE_DUP_SENTENCE,
    "_IGNORE_DUP_SENTENCE_CONTAINS": W._IGNORE_DUP_SENTENCE_CONTAINS,
    "_ENGLISH_WORD_RE": W._ENGLISH_WORD_RE,
    "_WORD_RE": W._WORD_RE,
    "_URL_RE": W._URL_RE,
    "_EMAIL_RE": W._EMAIL_RE,
    "_PUNCT_NO_SPACE_RE": W._PUNCT_NO_SPACE_RE,
    "_EMDASH_SPACING_RE": W._EMDASH_SPACING_RE,
    "_WRONG_DASH_RE": W._WRONG_DASH_RE,
    "_HYPHEN_PAIR_RE": W._HYPHEN_PAIR_RE,
}


@pytest.mark.parametrize("name", sorted(WORD_SETS))
def test_word_sets_are_non_empty(name):
    assert len(WORD_SETS[name]) > 0


@pytest.mark.parametrize("name", sorted(WORD_SETS))
def test_word_set_entries_are_lowercase(name):
    """Lookups lowercase the candidate, so an uppercase entry can never match."""
    offenders = sorted(w for w in WORD_SETS[name] if w != w.lower())
    assert offenders == [], f"{name} has non-lowercase entries: {offenders}"


@pytest.mark.parametrize("name", sorted(WORD_SETS))
def test_word_set_entries_are_stripped_and_non_empty(name):
    offenders = sorted(repr(w) for w in WORD_SETS[name] if not w or w != w.strip())
    assert offenders == [], f"{name} has blank/padded entries: {offenders}"


@pytest.mark.parametrize("name", sorted(REGEXES))
def test_regexes_are_compiled_patterns(name):
    assert isinstance(REGEXES[name], re.Pattern)


def test_term_pairs_are_well_formed():
    for canonical, variants in W._TERM_PAIRS:
        assert isinstance(canonical, str) and canonical
        assert isinstance(variants, list) and variants
        assert all(isinstance(v, str) and v for v in variants)


def test_term_pair_canonicals_are_unique():
    canon = [c for c, _ in W._TERM_PAIRS]
    assert len(canon) == len(set(canon))


def test_no_variant_is_an_exact_duplicate_of_its_canonical():
    """A byte-identical variant could never be distinguished from the canonical.

    Case-only variants (internet/Internet) ARE meaningful — matching is
    case-sensitive precisely so that mixed capitalisation is detectable.
    An exact duplicate, however, is always a data-entry mistake.
    """
    for canonical, variants in W._TERM_PAIRS:
        assert canonical not in variants, f"{canonical!r} is listed as its own variant"


def test_variants_within_a_pair_are_unique():
    for canonical, variants in W._TERM_PAIRS:
        assert len(variants) == len(set(variants)), f"{canonical!r} has duplicate variants"


def test_case_only_variants_are_permitted_and_present():
    """Capitalisation-only pairs are intentional; matching is case-sensitive."""
    case_only = {
        canonical
        for canonical, variants in W._TERM_PAIRS
        if any(v.lower() == canonical.lower() and v != canonical for v in variants)
    }
    assert case_only, "expected at least one capitalisation-only pair to exist"


def test_untranslated_threshold_is_a_sensible_positive_int():
    assert isinstance(W._UNTRANSLATED_MIN_WORDS, int)
    assert W._UNTRANSLATED_MIN_WORDS >= 1


def test_url_regex_matches_http_and_bare_www():
    assert W._URL_RE.search("see https://example.com/x?y=1")
    assert W._URL_RE.search("see www.example.com")


def test_url_regex_ignores_ordinary_prose():
    assert not W._URL_RE.search("this sentence has no links at all")


def test_email_regex_matches_a_plain_address():
    assert W._EMAIL_RE.search("contact someone@example.com today")


def test_english_word_regex_requires_four_plus_ascii_letters():
    assert W._ENGLISH_WORD_RE.findall("the information") == ["information"]


def test_english_word_regex_skips_accented_words():
    """An ASCII run inside an accented word must not count as English."""
    assert W._ENGLISH_WORD_RE.findall("información") == []


def test_word_regex_ignores_digits_and_punctuation():
    assert W._WORD_RE.findall("abc 123 de fghi!") == ["abc", "fghi"]


def test_ignore_dup_title_matches_common_repeated_titles():
    assert W._IGNORE_DUP_TITLE.match("Introduction")


def test_ignore_dup_title_leaves_real_titles_alone():
    assert not W._IGNORE_DUP_TITLE.match("Spotting a Phishing Email")


def test_wrong_dash_regex_flags_double_hyphen():
    assert W._WRONG_DASH_RE.search("wait--stop")


# --- language code parsing (target-language selection) --------------------

def test_target_languages_keys_are_lowercase_two_letter():
    for code in W._TARGET_LANGUAGES:
        assert re.fullmatch(r"[a-z]{2}", code), code


def test_target_languages_excludes_english():
    # English is the normal (checkbox-off) path, never a target-language choice.
    assert "en" not in W._TARGET_LANGUAGES


def test_base_language_code_normalises_region_and_case():
    assert W.base_language_code("de-DE") == "de"
    assert W.base_language_code("es_LA") == "es"
    assert W.base_language_code("FR-ca") == "fr"
    assert W.base_language_code("pt") == "pt"
    assert W.base_language_code("ZH-CN") == "zh"


def test_base_language_code_rejects_non_codes():
    for bad in (None, "", "FOR-QA", "v4-03", "AntiPhish", "1234"):
        assert W.base_language_code(bad) is None


def test_detect_language_from_gls_filenames():
    cases = {
        "GLSsh_11594_de-DE_AntiPhishEss_v4-03_sc24_FOR-QA.zip": "de",
        "GLSsh_11594_es-LA_AntiPhishEss_v4-03_sc24_FOR-QA.zip": "es",
        "GLSsh_11594_fr-CA_AntiPhishEss_v4-03_sc24_FOR-QA.zip": "fr",
        "GLSsh_11594_pt-BR_AntiPhishEss_v4-03_sc24_FOR-QA.zip": "pt",
        "GLSsh_11594_zh-CN_AntiPhishEss_v4-03_sc24_FOR-QA.zip": "zh",
        "GLSsh_11594_en-US_AntiPhishEss_v4-03_sc24.zip": "en",
        "11570A_owasp2025_en-US_v1_27-final.story": "en",
    }
    for name, expected in cases.items():
        assert W.detect_language_from_filename(name) == expected, name


def test_detect_language_ignores_non_language_tokens():
    # No xx-YY language subtag present -> None (must not match FOR-QA, v4-03…).
    assert W.detect_language_from_filename("CPOC2026_11569_v4-04.docx") is None
    assert W.detect_language_from_filename("report_FOR-QA_v4-03.zip") is None
