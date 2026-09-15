"""
Language-level checks: terminology consistency and untranslated English.

Both depend on vocabulary tables in wordlists.py.
"""

import re

from ..parser import ScormData
from .models import Section, _screen_num
from .wordlists import (
    _TERM_PAIRS,
    _ENGLISH_OK_TERMS,
    _ENGLISH_WORD_RE,
    _NON_ENGLISH_FALSE_POSITIVES,
    _UNTRANSLATED_MIN_WORDS,
    _TARGET_LANGUAGES,
    base_language_code,
    _URL_RE,
    _EMAIL_RE,
)

try:
    from spellchecker import SpellChecker as _SpellChecker
    _SPELLCHECKER_AVAILABLE = True
except ImportError:
    _SpellChecker = None  # always defined so it can be patched in tests
    _SPELLCHECKER_AVAILABLE = False



# A form counts as sentence-initial when it opens the text or follows
# terminal punctuation. "Email us. Send an email." is ordinary English, not a
# terminology inconsistency, so that leading capital is normalised away before
# forms are compared.
_SENTENCE_START_RE = re.compile(r"(?:^|[.!?:;]\s|\n)\s*$")

_MAX_LOCATIONS_SHOWN = 5


def _form_occurrences(data: ScormData, forms: list) -> dict:
    """Map each form of a term to the screen numbers where it appears.

    Matching is case-sensitive and word-bounded: capitalisation is the whole
    point for pairs like internet/Internet, and word boundaries stop "log in"
    matching inside unrelated words.
    """
    # Longest first so "cyber security" wins over a bare "cybersecurity"
    # substring when both could match at the same position.
    ordered = sorted(forms, key=len, reverse=True)
    patterns = [(f, re.compile(rf"\b{re.escape(f)}\b")) for f in ordered]

    found: dict[str, list] = {}
    lowered = {f.lower() for f in forms}

    for slide in data.slides:
        if not slide.texts:
            continue
        combined = " ".join(slide.texts)
        label = _screen_num(slide)

        for form, pattern in patterns:
            for m in pattern.finditer(combined):
                matched = m.group(0)
                # Fold a sentence-initial capital back to its lowercase form,
                # but only when that lowercase spelling is itself a known form.
                if (
                    matched[:1].isupper()
                    and matched[:1].lower() + matched[1:] in lowered
                    and _SENTENCE_START_RE.search(combined[:m.start()])
                ):
                    matched = matched[:1].lower() + matched[1:]
                if label not in found.setdefault(matched, []):
                    found[matched].append(label)

    return found


def check_terminology(data: ScormData) -> Section:
    """Flag terms that are spelled more than one way in the same package.

    Consistency is what matters, not which spelling is used: a course that
    says "e-mail" throughout is fine, and so is one that says "email"
    throughout. Only a package that uses both is reported.
    """
    sec = Section("Terminology Consistency")

    if not any(slide.texts for slide in data.slides):
        sec.add("warn", "No slide text extracted — cannot check terminology")
        return sec

    inconsistent = []

    for canonical, variants in _TERM_PAIRS:
        found = _form_occurrences(data, [canonical] + list(variants))
        if len(found) < 2:
            continue  # one spelling (or none) — consistent, nothing to report

        parts = []
        for form in sorted(found, key=lambda f: (-len(found[f]), f)):
            locs = found[form]
            shown = "; ".join(locs[:_MAX_LOCATIONS_SHOWN])
            more = f" (+{len(locs) - _MAX_LOCATIONS_SHOWN} more)" if len(locs) > _MAX_LOCATIONS_SHOWN else ""
            parts.append(f'"{form}" on {shown}{more}')
        inconsistent.append((canonical, " | ".join(parts)))

    if not inconsistent:
        sec.add("pass", "No terminology inconsistencies detected in checked term pairs")
    else:
        for canonical, detail in sorted(inconsistent):
            sec.add("warn", f"Mixed spelling of “{canonical}” — {detail}")

    sec.add(
        "info",
        "Note: only checks predefined pairs. Use Global Search to verify any additional terms.",
    )
    return sec


def check_untranslated_english(data: ScormData, target_lang=None) -> Section:
    """
    For non-English courses, flag text elements that contain a meaningful
    amount of English.

    A word counts as untranslated English only when it is an English
    dictionary word AND is NOT a valid word in the course's target language
    (so cognates like Spanish "final"/"total"/"social", which are also English
    words, are not flagged). A text element is flagged when it contains
    _UNTRANSLATED_MIN_WORDS or more such words, after removing the
    _ENGLISH_OK_TERMS allowlist.

    ``target_lang`` is the course language (e.g. "de", "de-DE"). If it is
    missing, or names a language pyspellchecker has no dictionary for, the
    check is skipped — an English dictionary alone can't tell untranslated
    English from ordinary words in an unknown language, so running it would
    only produce noise.
    """
    sec = Section("Untranslated English Text")

    if not _SPELLCHECKER_AVAILABLE:
        sec.add(
            "info",
            "Cannot check — install pyspellchecker (pip install pyspellchecker)",
        )
        return sec

    base = base_language_code(target_lang)
    if base is None:
        sec.add(
            "info",
            "Untranslated-English check skipped — no course language selected. "
            "Pick the language (SCORM QA tab) or name it in the filename "
            "(e.g. _de-DE_) so the target-language dictionary can be used.",
        )
        return sec
    if base not in _TARGET_LANGUAGES:
        sec.add(
            "info",
            f"Untranslated-English check skipped — no dictionary available for "
            f"language '{target_lang}'. Supported: "
            f"{', '.join(f'{n} ({c})' for c, n in sorted(_TARGET_LANGUAGES.items(), key=lambda kv: kv[1]))}.",
        )
        return sec

    try:
        target_checker = _SpellChecker(language=base)
    except Exception as e:  # noqa: BLE001 — missing/broken dictionary => skip, don't crash
        sec.add(
            "info",
            f"Untranslated-English check skipped — could not load the "
            f"{_TARGET_LANGUAGES[base]} ({base}) dictionary ({e!r}).",
        )
        return sec

    checker = _SpellChecker()
    allowed = (
        {t.lower() for t in _ENGLISH_OK_TERMS}
        | _NON_ENGLISH_FALSE_POSITIVES
    )

    issues: list[tuple[str, str, list]] = []  # (loc, snippet, english_words)

    for slide in data.slides:
        loc = _screen_num(slide)
        for text in slide.texts:
            # Strip URLs and email addresses so their alphabetic fragments
            # don't count as English content.
            clean = _EMAIL_RE.sub(" ", text)
            clean = _URL_RE.sub(" ", clean)
            clean = re.sub(r"//\S+", " ", clean)
            clean = re.sub(r"%\w+%", " ", clean)
            clean = re.sub(r"\S*_player\.\S*", " ", clean)

            words = _ENGLISH_WORD_RE.findall(clean)
            if len(words) < _UNTRANSLATED_MIN_WORDS:
                continue

            lowered = [w.lower() for w in words]
            # Filter out allowed English-OK terms — they don't count
            # toward "untranslated content".
            candidates = [lw for lw in lowered if lw not in allowed]
            if len(candidates) < _UNTRANSLATED_MIN_WORDS:
                continue

            # English words = known to the English dictionary...
            english_unknown = checker.unknown(candidates)
            # ...but a word that is also valid in the target language is a
            # legitimate target-language word, not untranslated English.
            target_unknown = target_checker.unknown(candidates)
            english_words = [
                w for w in candidates
                if w not in english_unknown and w in target_unknown
            ]

            if len(english_words) >= _UNTRANSLATED_MIN_WORDS:
                snippet = text.strip()[:140]
                issues.append((loc, snippet, english_words))

    if not issues:
        sec.add(
            "pass",
            "No text elements with 3+ English dictionary words detected",
        )
    else:
        for loc, snippet, english_words in issues[:60]:
            # Deduplicate while preserving order for the preview
            seen = []
            for w in english_words:
                if w not in seen:
                    seen.append(w)
            preview = ", ".join(seen[:6])
            more = f" (+{len(seen) - 6})" if len(seen) > 6 else ""
            sec.add(
                "warn",
                f'[{loc}] English: {preview}{more} — "{snippet}…"',
            )
        if len(issues) > 60:
            sec.add("info", f"…and {len(issues) - 60} more text element(s) flagged")
        sec.add(
            "info",
            "Add words to _ENGLISH_OK_TERMS in checks/wordlists.py for terms "
            "that should stay in English (brand names, accepted loanwords, etc.)",
        )

    return sec
