"""
Text-hygiene checks: duplicates, doubled words, spacing, dashes, spelling.

The vocabulary and regexes these depend on live in wordlists.py.
"""

import re

from ..parser import ScormData
from .models import Section, _screen_num
from .wordlists import (
    _DOUBLED_WORD_IGNORE,
    _IGNORE_DUP_TITLE,
    _IGNORE_DUP_SENTENCE,
    _IGNORE_DUP_SENTENCE_CONTAINS,
    _SPELL_IGNORE,
    _WORD_RE,
    _URL_RE,
    _EMAIL_RE,
    _PUNCT_NO_SPACE_RE,
    _EMDASH_SPACING_RE,
    _WRONG_DASH_RE,
    _HYPHEN_PAIR_RE,
    _HYPHEN_PREFIXES,
    _HYPHEN_SUFFIXES,
    _HYPHEN_COMPOUND_OK,
)

try:
    from spellchecker import SpellChecker as _SpellChecker
    _SPELLCHECKER_AVAILABLE = True
except ImportError:
    _SPELLCHECKER_AVAILABLE = False

# Lazy-built singleton — building the dictionary takes a beat. The hyphen-pair
# rule and the spell check both want one, so build it at most once.
_spell_checker = None


def _get_spell_checker():
    """Return a cached pyspellchecker SpellChecker, or None if unavailable."""
    global _spell_checker
    if _spell_checker is None and _SPELLCHECKER_AVAILABLE:
        _spell_checker = _SpellChecker()
    return _spell_checker


def _is_known_compound(parts: list, checker) -> bool:
    """
    True when every word-like part of a hyphenated token is a real English
    word — i.e. the token is an ordinary compound ("risk-based",
    "decision-making"), not a hyphen standing in for an em-dash.

    We only judge alphabetic parts of length >= 2; single letters and any
    numeric/empty fragments are ignored (so "e-learning" or "top-10" are not
    disqualified by their short part). A token with no judgeable part at all
    returns False so it falls through to the existing heuristics.

    This is a deliberate trade: an em-dash mistakenly written as an *unspaced*
    single hyphen between two dictionary words (rare — those are usually typed
    as "--" or " - ", which _WRONG_DASH_RE still catches) will now be treated
    as a compound and not flagged. In exchange, the large hand-maintained
    _HYPHEN_COMPOUND_OK list no longer has to enumerate every legitimate
    compound a course might use.
    """
    if checker is None:
        return False
    judgeable = [p for p in parts if p.isalpha() and len(p) >= 2]
    if not judgeable:
        return False
    return len(checker.known(judgeable)) == len(judgeable)


def check_duplicates(data: ScormData, dual_path: bool = False) -> Section:
    sec = Section("Duplicate & Doubled-Word Detection")

    # In a dual-path course every slide exists once in the interactive path and
    # once in the accessible path, so appearing exactly twice is expected.
    # Only flag when a title or paragraph appears MORE than twice in that mode.
    dup_threshold = 2 if dual_path else 1

    if dual_path:
        sec.add("info", "Dual-path mode: titles and paragraphs are only flagged if they appear more than 2 times")

    # Duplicate slide titles within the same scene (ignore common repeated titles).
    # Keyed by (scene_id, title) so the same title in different scenes is not flagged.
    title_map: dict[tuple, list] = {}
    for slide in data.slides:
        t = slide.slide_title.strip()
        if t and not _IGNORE_DUP_TITLE.match(t):
            title_map.setdefault((slide.scene_id, t.lower()), []).append(slide)

    dup_titles = {k: slides for k, slides in title_map.items() if len(slides) > dup_threshold}
    if dup_titles:
        for _, slides in sorted(dup_titles.items(), key=lambda x: x[1][0].slide_title):
            slide_nums = ", ".join(_screen_num(s) for s in slides)
            sec.add("warn", f'Duplicate title "{slides[0].slide_title}" appears {len(slides)}x: {slide_nums}')
    else:
        sec.add("pass", "No duplicate slide titles found within the same scene")

    # Duplicate text paragraphs (>= 40 chars, across different slides)
    # Skip sentences that use expected boilerplate phrasing
    para_map: dict[str, list] = {}
    for slide in data.slides:
        for text in slide.texts:
            t = text.strip()
            if (len(t) >= 40
                    and not _IGNORE_DUP_SENTENCE.match(t)
                    and not _IGNORE_DUP_SENTENCE_CONTAINS.search(t)):
                para_map.setdefault(t, []).append(slide.slide_id)

    dup_paras = {t: ids for t, ids in para_map.items() if len(set(ids)) > dup_threshold}
    if dup_paras:
        slide_by_id = {s.slide_id: s for s in data.slides}
        for text, slide_ids in list(dup_paras.items())[:20]:
            id_set = list(dict.fromkeys(slide_ids))  # unique, preserve order
            locs = ", ".join(
                _screen_num(slide_by_id[sid]) for sid in id_set if sid in slide_by_id
            )
            sec.add("warn", f'Duplicate paragraph at {locs}: "{text[:80]}…"')
        if len(dup_paras) > 20:
            sec.add("info", f"…and {len(dup_paras) - 20} more duplicate paragraphs")
    else:
        sec.add("pass", "No duplicate paragraphs found across slides")

    # Words that are only flagged when back-to-back (0 words between)
    _ADJACENT_ONLY_WORDS = {
        "learn", "that", "as", "tip", "the", 
        "score", "next", "to", "best", "no", 
        "and", "or", "but", "if", "then", 
        "when", "a", "is", "are", "in", "on", 
        "for", "with", "by", "of", "submit", 
        "try", "again", "select", "click", 
        "choose", "answer", "question", "retake", 
        "exit", "module", "password", "username"}

    # Doubled words within slides:
    #   - adjacent-only words: flag only "word word" (0 words between)
    #   - all other words:     flag "word word" or "word X word" (< 2 words between)
    adjacent_pattern = re.compile(
        r"\b(" + "|".join(re.escape(w) for w in _ADJACENT_ONLY_WORDS) + r")\s+\1\b",
        re.IGNORECASE,
    )
    nearby_pattern = re.compile(r"\b(\w{2,})\s+(?:\w+\s+)?\1\b", re.IGNORECASE)

    doubled_found = False
    for slide in data.slides:
        combined = " ".join(slide.texts)

        adj_matches = set(m.lower() for m in adjacent_pattern.findall(combined))

        # nearby_pattern also catches adjacent duplicates, so exclude adjacent-only words
        # from its results to avoid double-reporting
        nearby_raw = [m.lower() for m in nearby_pattern.findall(combined)]
        nearby_matches = set(
            m for m in nearby_raw if m not in _ADJACENT_ONLY_WORDS
        )

        all_matches = (adj_matches | nearby_matches) - _DOUBLED_WORD_IGNORE
        if all_matches:
            doubled_found = True
            unique_matches = sorted(all_matches)
            loc = _screen_num(slide)
            sec.add("warn", f'Doubled word(s) "{", ".join(unique_matches)}" at {loc}')

    if not doubled_found:
        sec.add("pass", "No doubled words detected")

    return sec


def check_whitespace_and_spelling(data: ScormData, skip_spelling: bool = False) -> Section:
    sec = Section("Whitespace & Spelling Checks")

    # Collect into typed buckets so the output is grouped by issue type.
    extra_space_msgs: list[str] = []
    punct_msgs: list[str] = []
    emdash_msgs: list[str] = []
    wrongdash_msgs: list[str] = []

    # Spans that look like answer choices ("A.  text" through "H.  text") may
    # have intentional spacing after the identifier — skip them for space checks.
    _answer_id = re.compile(r'^[A-Ha-h][\.\)]\s')

    # Dictionary used to recognise ordinary hyphenated compounds so they are not
    # flagged as possible missed em-dashes. English courses only — the whole
    # em-dash / hyphen dash family is skipped for non-English courses (see the
    # `if not skip_spelling:` guard below), so the checker is never built there.
    hyphen_checker = None if skip_spelling else _get_spell_checker()

    for slide in data.slides:
        loc = _screen_num(slide)
        for text in slide.texts:
            is_answer_choice = bool(_answer_id.match(text))

            # Strip emails, URLs, and internal tokens before whitespace checks.
            # Each pattern absorbs one optional surrounding space (\s?) on each
            # side so that removing the token doesn't leave a double space where
            # only a single space existed (e.g. "Send to email@x.com for info"
            # → "Send to for info", not "Send to  for info").
            text_no_urls = re.sub(
                r'\s?' + _EMAIL_RE.pattern + r'\s?', ' ', text, flags=re.IGNORECASE)
            text_no_urls = re.sub(
                r'\s?(?:https?://\S+|www\.\S+)\s?', ' ', text_no_urls, flags=re.IGNORECASE)
            text_no_urls = re.sub(r'\s?//\S+\s?', ' ', text_no_urls)
            text_no_urls = re.sub(r'\s?\S*_player\.\S*\s?', ' ', text_no_urls)

            # Use (?<!:) so that "Label:  <url-replaced-by-space>" patterns
            # (colon followed by two spaces after URL removal) are not flagged.
            if not is_answer_choice and re.search(r"(?<!:) {2,}", text_no_urls):
                preview = text.strip()[:120]
                extra_space_msgs.append(f'[{loc}]: "…{preview}…"')

            for m in _PUNCT_NO_SPACE_RE.finditer(text_no_urls):
                start = max(0, m.start() - 15)
                end = min(len(text), m.end() + 15)
                snippet = text[start:end].strip()
                punct_msgs.append(f'Missing space after "{m.group()[0]}" on [{loc}]: "…{snippet}…"')

            # Em-dash / hyphen dash checks are English-typography heuristics and
            # produce noise on non-English text (foreign hyphenated compounds,
            # different dash conventions), so skip them entirely for a
            # non-English course. A single skipped-notice is emitted below.
            if not skip_spelling:
                # Em-dash with a space on one or both sides (GLS style is unspaced).
                for m in _EMDASH_SPACING_RE.finditer(text_no_urls):
                    start = max(0, m.start() - 20)
                    end = min(len(text_no_urls), m.end() + 20)
                    snippet = text_no_urls[start:end].strip()
                    emdash_msgs.append(f'[{loc}]: "…{snippet}…"')

                # Hyphen likely meant to be an em-dash (double / spaced).
                for m in _WRONG_DASH_RE.finditer(text_no_urls):
                    start = max(0, m.start() - 20)
                    end = min(len(text_no_urls), m.end() + 20)
                    snippet = text_no_urls[start:end].strip()
                    found = m.group().strip()
                    wrongdash_msgs.append(f'[{loc}] found "{found}": "…{snippet}…"')

                # Unspaced hyphen between words — flag unless a known compound.
                for m in _HYPHEN_PAIR_RE.finditer(text_no_urls):
                    token = m.group()
                    low = token.lower()
                    parts = low.split("-")
                    if (low in _HYPHEN_COMPOUND_OK
                            or parts[0] in _HYPHEN_PREFIXES
                            or parts[-1] in _HYPHEN_SUFFIXES
                            or _is_known_compound(parts, hyphen_checker)):
                        continue
                    start = max(0, m.start() - 20)
                    end = min(len(text_no_urls), m.end() + 20)
                    snippet = text_no_urls[start:end].strip()
                    wrongdash_msgs.append(f'[{loc}] found "{token}": "…{snippet}…"')

    # --- Emit extra spaces ---
    if extra_space_msgs:
        for msg in extra_space_msgs:
            sec.add("warn", f"Extra space(s) on {msg}")
    else:
        sec.add("pass", "No extra spaces detected")

    # --- Emit punctuation issues ---
    if punct_msgs:
        for msg in punct_msgs:
            sec.add("warn", msg)
    else:
        sec.add("pass", "No missing spaces after punctuation detected")

    # --- Emit em-dash / hyphen dash issues (English courses only) ---
    if skip_spelling:
        sec.add("info", "Em-dash / hyphen checks skipped — non-English course")
    else:
        # --- Emit em-dash spacing issues ---
        if emdash_msgs:
            for msg in emdash_msgs:
                sec.add("warn", f"Spaced em-dash (should be unspaced) on {msg}")
        else:
            sec.add("pass", "No spaced em-dashes detected (all em-dashes are unspaced)")

        # --- Emit wrong-dash (hyphen-for-em-dash) issues ---
        if wrongdash_msgs:
            for msg in wrongdash_msgs:
                sec.add("warn", f"Possible hyphen where an em-dash is intended on {msg}")
            sec.add(
                "info",
                "Note: heuristic — double hyphens, spaced hyphens, and unspaced "
                "hyphenated word pairs are flagged as possible missed em-dashes. "
                "Compounds whose parts are all real dictionary words are now "
                "auto-suppressed (English courses only), so most legitimate "
                "compounds no longer appear here. If one still slips through — a "
                "proper noun or coined term the dictionary doesn't know — add it to "
                "_HYPHEN_COMPOUND_OK (or a prefix/suffix set) in checks/wordlists.py.",
            )
        else:
            sec.add("pass", "No misused hyphens detected where an em-dash may be intended")

    # --- Spell check (grouped last) ---
    if skip_spelling:
        sec.add("info", "Spell check skipped — non-English course")
        return sec
    if not _SPELLCHECKER_AVAILABLE:
        sec.add("info", "Spell checking skipped — run: pip install pyspellchecker")
        return sec

    checker = _SpellChecker()
    checker.word_frequency.load_words(_SPELL_IGNORE)

    spell_issues: dict[str, list] = {}  # word -> list of slide locations

    for slide in data.slides:
        loc = _screen_num(slide)
        combined = " ".join(slide.texts)

        # Strip email addresses, URLs, and Storyline variable references before
        # extracting words. Email must precede URL strip to avoid @domain remnants.
        combined = _EMAIL_RE.sub(" ", combined)
        combined = _URL_RE.sub(" ", combined)
        # Secondary strip: catch protocol-relative URLs or cases where Storyline
        # splits "https:" into one text element and "//rest-of-url" into another,
        # leaving "//domain.com/path" as apparent plain text after the join.
        combined = re.sub(r'//\S+', ' ', combined)
        combined = re.sub(r"%\w+%", " ", combined)
        # Strip any token containing "_player." — Storyline internal identifiers
        # like "_player.1.5" or compound tokens with _player. anywhere in them.
        combined = re.sub(r'\S*_player\.\S*', ' ', combined)

        # Also join texts without separators and strip URLs there.  Storyline
        # sometimes stores a URL hyperlink as multiple adjacent text runs, so
        # " ".join produces "https://domain.com/sites/ fjewiojel" — the regex
        # strips the URL portion but leaves the path slug as a bare word.
        # Stripping on the compact (no-separator) join catches such slugs.
        compact = "".join(slide.texts)
        compact = _EMAIL_RE.sub(" ", compact)
        compact = _URL_RE.sub(" ", compact)
        compact = re.sub(r'//\S+', ' ', compact)
        compact = re.sub(r"%\w+%", " ", compact)
        compact = re.sub(r'\S*_player\.\S*', ' ', compact)
        compact_words = {w.lower() for w in _WORD_RE.findall(compact)}

        candidates = [
            w for w in _WORD_RE.findall(combined)
            if not w.isupper()          # skip all-caps acronyms
            and len(w) < 15             # skip long compound identifiers
            and w.lower() not in _SPELL_IGNORE
            and w.lower() in compact_words  # skip words stripped from compact join (URL slugs)
        ]
        for word in checker.unknown(candidates):
            spell_issues.setdefault(word.lower(), []).append(loc)

    if not spell_issues:
        sec.add("pass", "No potential spelling issues detected")
    else:
        for word, locs in sorted(spell_issues.items()):
            unique_locs = list(dict.fromkeys(locs))
            loc_str = "; ".join(unique_locs[:3])
            more = f" (+{len(unique_locs) - 3} more)" if len(unique_locs) > 3 else ""
            suggestions = checker.candidates(word) or set()
            suggestion_str = ", ".join(list(suggestions)[:3]) if suggestions else "?"
            sec.add(
                "warn",
                f'Possible misspelling "{word}" (suggestions: {suggestion_str}) — {loc_str}{more}',
            )
        sec.add(
            "info",
            "Note: spell checker flags technical terms, proper nouns, and acronyms — review manually",
        )

    return sec
