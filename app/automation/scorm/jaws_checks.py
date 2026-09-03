"""
JAWS / screen-reader static checks for the Accessible Path of a dual-path course.

These checks only run when the user has marked the course as dual-path; they
inspect every slide whose scene title matches /accessible path/i and report
issues that JAWS users would actually hear (or fail to hear).

Each check is a thin function that takes the parsed ScormData and returns a
Section.  The runner is `run_jaws_checks(data)` — call it from checks/runner.py.
"""

import re
from typing import Optional

from .parser import ScormData, SlideInfo, AccObject, LayerInfo
from .checks.models import Section

try:
    from spellchecker import SpellChecker as _SpellChecker
    _SPELLCHECKER_AVAILABLE = True
except ImportError:
    _SPELLCHECKER_AVAILABLE = False

# Lazy-built singleton — building the dictionary takes a beat, no point doing
# it twice when both jaws_checks and the text/language checks want one.
_spell_checker = None


def _get_spell_checker():
    """Return a cached pyspellchecker SpellChecker, or None if unavailable."""
    global _spell_checker
    if _spell_checker is None and _SPELLCHECKER_AVAILABLE:
        _spell_checker = _SpellChecker()
    return _spell_checker


# ---------------------------------------------------------------------------
# Acronym check — manual override lists
# ---------------------------------------------------------------------------
# The acronym check normally filters out anything pyspellchecker recognises
# as a real English word. These two sets override that default in either
# direction. All entries should be UPPERCASE — matching is exact uppercase.
#
# _ACRONYM_ALWAYS_IGNORE
#   Words to NEVER flag, even if pyspellchecker doesn't know them. Use this
#   for pronounceable acronyms that JAWS already says correctly as a word
#   (AARP, NASA, NATO, etc.) and for course-specific brand names.
#
# _ACRONYM_ALWAYS_FLAG
#   Words to ALWAYS flag, even if pyspellchecker thinks they're valid.
#   Use this for short two-letter acronyms that collide with English
#   pronouns/prepositions ("IT", "US", "AS", "AT") — pyspellchecker treats
#   these as known words but in the course context they're meant to be read
#   as initialisms and need letter-spacing.
#
# Order of precedence (highest first):
#   1. _ACRONYM_ALWAYS_FLAG    → always flag
#   2. _ACRONYM_ALWAYS_IGNORE  → never flag
#   3. pyspellchecker          → flag if unknown
# ---------------------------------------------------------------------------

_ACRONYM_ALWAYS_IGNORE: set[str] = {
    # Pronounceable acronyms — JAWS reads these as words, not letter-by-letter
    # Or it pronounces them correctly as individual letters
    "AARP", "GDPR", "HIPAA",
    "NASA", "NATO", "NORAD",
    "OSHA", "FEMA", "FAFSA",
    "UNESCO", "UNICEF", "PDF", "MFA",
    "SCUBA", "RADAR", "LASER", "SONAR",
    "UNIX", "VPN", "QR", "URL", "SQL",
    "PCI", "CISO", "CNN", "BBC", "ESPN",
    "CBS", "DHL", "HTTP", "CCPA", "ARTICO",
    "CP", "DSS", "FIPS", "FISMA", "HPM",
    "NIST", "RMF",
    # Course-specific brand or product names — add as needed
}

_ACRONYM_ALWAYS_FLAG: set[str] = {
    # Two-letter initialisms that collide with English words/pronouns and so
    # slip past pyspellchecker. JAWS would pronounce them as the word; we
    # want them spelled out.
    "IT",   # Information Technology vs. the pronoun "it"
    "US",   # United States vs. the pronoun "us"
    "AS",   # Application Server vs. the conjunction "as"
    "AT",   # vs. the preposition "at"
    "OR",   # Operating Room vs. the conjunction "or"
    "IN",   # Indiana vs. the preposition "in"
    "ON",   # vs. the preposition "on"
    "BE",   # vs. the verb "be"
    "DO",   # vs. the verb "do"
    "GO",   # vs. the verb "go"
    "NO",   # vs. the word "no"
    "SO",   # vs. the word "so"
    "AM",   # vs. the verb "am"
    "ME",   # vs. the pronoun "me"
    "MY",   # vs. the pronoun "my"
    "BY",   # vs. the preposition "by"
    "OF",   # vs. the preposition "of"
    "TO",   # vs. the preposition "to"
    # Add more here as you find spellcheck-blind acronyms in courses
}


# ---------------------------------------------------------------------------
# Slide-title and accessible-path detection helpers
# ---------------------------------------------------------------------------

# A scene whose title matches this is treated as the Accessible Path.
_ACC_PATH_SCENE_RE = re.compile(r"accessible\s*path", re.IGNORECASE)

# Back-end slide titles that contain these characters are flagged as
# "internal naming" — JAWS reads them literally and they sound bad.
_INTERNAL_TITLE_RE = re.compile(r"[_]")

# Common back-end-only title prefixes worth flagging too (case-insensitive).
_INTERNAL_TITLE_PREFIXES = ("acc_", "layer_", "slide_", "tmp_", "copy of ")

# Minimum number of slide titles a scene must share with a menu-bearing scene
# before we consider it an accessible-path mirror via parallel-structure detection.
_PARALLEL_OVERLAP_THRESHOLD = 2


def _normalise_title(t: str) -> str:
    """Lower-case, collapse whitespace, strip terminal punctuation/ellipsis."""
    return re.sub(r"[\s\?\.\!\u2026]+", " ", t or "").strip().casefold()


def get_accessible_path_scene_ids(data: ScormData) -> set:
    """
    Detect every scene_id that belongs to the Accessible Path of a dual-path
    course.  Two complementary signals:

      (a) Explicit naming — the scene title contains "Accessible Path".
          Seen on courses that label the alternate path directly.
      (b) Parallel structure — the scene has zero menu slides, contains at
          least 2 slides, and at least 2 of its slide titles also appear in
          a menu-bearing scene.  Seen on courses where the accessible-path
          scenes have empty titles but mirror the standard-path modules.

    Together these cover both shapes of dual-path course we've seen.  If a
    new shape appears, extend this function rather than touching the checks.
    """
    from collections import defaultdict

    by_scene: dict = defaultdict(list)
    for s in data.slides:
        by_scene[s.scene_id].append(s)

    acc_ids: set = set()

    # --- Rule (a): explicit scene title ---
    for sid, slides in by_scene.items():
        if any(_ACC_PATH_SCENE_RE.search(s.scene_title or "") for s in slides):
            acc_ids.add(sid)

    # --- Rule (b): parallel structure ---
    # Collect normalised slide titles from every menu-bearing scene.
    menu_scene_ids = {s.scene_id for s in data.slides if s.is_in_menu}
    menu_titles: set = set()
    for s in data.slides:
        if s.scene_id in menu_scene_ids and s.slide_title:
            menu_titles.add(_normalise_title(s.slide_title))

    for sid, slides in by_scene.items():
        if sid in acc_ids:
            continue
        if any(s.is_in_menu for s in slides):
            continue                    # this is a menu/standard-path scene
        if len(slides) < 2:
            continue                    # too small to be a module mirror
        overlap = sum(
            1 for s in slides
            if s.slide_title and _normalise_title(s.slide_title) in menu_titles
        )
        if overlap >= _PARALLEL_OVERLAP_THRESHOLD:
            acc_ids.add(sid)

    return acc_ids


def get_accessible_path_slides(data: ScormData) -> list:
    """Return every SlideInfo that belongs to the Accessible Path."""
    acc_ids = get_accessible_path_scene_ids(data)
    return [s for s in data.slides if s.scene_id in acc_ids]


def get_standard_path_slides(data: ScormData) -> list:
    """
    Every SlideInfo that belongs to the Standard Path — defined as menu-
    bearing scenes minus anything claimed by the Accessible Path.  (In
    practice the two sets are disjoint, but we subtract defensively.)
    """
    acc_ids = get_accessible_path_scene_ids(data)
    std_scene_ids = {s.scene_id for s in data.slides if s.is_in_menu} - acc_ids
    return [s for s in data.slides if s.scene_id in std_scene_ids]


# Minimum title similarity (difflib ratio, 0..1) to treat an accessible
# slide as the mirror of a standard slide.  Tuned against real dual-path
# courses, where accessible titles are often a shortened form of the
# standard title.  Representative ratios at this threshold:
#   "Cyber Hygiene" ↔ "Protection: Cyber Hygiene"          = 0.61
#   "Avoid Misuse"  ↔ "Protection: Avoid Credential Misuse" = 0.55
# Lowering it below 0.55 starts matching unrelated slides in the same module.
_PATH_MATCH_THRESHOLD = 0.55


def path_mapping_key(slide: SlideInfo) -> str:
    """Stable per-slide key for mapping lookups."""
    return f"{slide.scene_id}::{slide.slide_id}"


def build_path_mapping(data: ScormData) -> dict:
    """
    Align the Standard Path and the Accessible Path slide-by-slide so the
    screen table can show each standard slide's accessible counterpart(s)
    and surface accessible-only extras.

    Returns a dict:
      has_standard             bool — any menu-bearing non-accessible slides
      has_accessible           bool — any accessible-path slides
      acc_numbers_for          dict[str, list[str]] — standard-slide key ->
                               list of accessible "scene.slide" strings
      std_number_for           dict[str, str]       — accessible-slide key ->
                               standard "scene.slide" string (if matched)
      accessible_only_slides   list[SlideInfo] — accessible slides with no
                               standard counterpart (shown as extra rows)
      path_for_slide           dict[str, str] — slide key -> "Standard" |
                               "Accessible" | "Other"

    Alignment is a single left-to-right pass using difflib.SequenceMatcher
    ratios on normalised titles.  Splits (one standard -> many accessible)
    are supported by allowing the pointer to stay put; extras (accessible-
    only) are supported by leaving the pointer where it is.
    """
    from difflib import SequenceMatcher

    std_slides = get_standard_path_slides(data)
    acc_slides = get_accessible_path_slides(data)

    has_standard = bool(std_slides)
    has_accessible = bool(acc_slides)

    acc_ids = get_accessible_path_scene_ids(data)
    path_for_slide: dict = {}
    for s in data.slides:
        key = path_mapping_key(s)
        if s.scene_id in acc_ids:
            path_for_slide[key] = "Accessible"
        elif s.is_in_menu:
            path_for_slide[key] = "Standard"
        else:
            path_for_slide[key] = "Other"

    acc_numbers_for: dict = {}
    std_number_for: dict = {}
    accessible_only: list = []

    if not (has_standard and has_accessible):
        return {
            "has_standard": has_standard,
            "has_accessible": has_accessible,
            "acc_numbers_for": acc_numbers_for,
            "std_number_for": std_number_for,
            "accessible_only_slides": list(acc_slides) if not has_standard else [],
            "path_for_slide": path_for_slide,
        }

    std_norm = [_normalise_title(s.slide_title) for s in std_slides]

    def sim(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a, b).ratio()

    # Window of standard slides to consider for each accessible slide.
    # Needs to span across a module boundary (accessible path can insert an
    # extra intro / answer-key slide between two standard module groups)
    # without being so wide it produces spurious matches on repeated titles.
    _LOOKAHEAD = 5

    std_ptr = 0
    last_matched_std = -1   # so split-part extras attach to the right slide
    for acc in acc_slides:
        a_norm = _normalise_title(acc.slide_title)
        best_idx = -1
        best_score = 0.0
        for offset in range(_LOOKAHEAD + 1):
            idx = std_ptr + offset
            if idx >= len(std_slides):
                break
            score = sim(a_norm, std_norm[idx])
            if score > best_score:
                best_score = score
                best_idx = idx

        acc_num = f"{acc.scene_number}.{acc.slide_number}"
        if best_idx >= 0 and best_score >= _PATH_MATCH_THRESHOLD:
            std = std_slides[best_idx]
            std_key = path_mapping_key(std)
            acc_numbers_for.setdefault(std_key, []).append(acc_num)
            std_number_for[path_mapping_key(acc)] = (
                f"{std.scene_number}.{std.slide_number}"
            )
            std_ptr = best_idx      # allow repeats; do not advance past
            last_matched_std = best_idx
        else:
            # Unmatched accessible slide.  If a prior standard slide has
            # already been paired, attach this as an "(extra)" to that
            # slide — likely an answer-key or split-part overflow.  Only
            # slides that can't be attached to anything get listed as
            # accessible-only at the bottom of the screen table.
            if last_matched_std >= 0:
                std_key = path_mapping_key(std_slides[last_matched_std])
                acc_numbers_for.setdefault(std_key, []).append(
                    f"{acc_num} (extra)"
                )
            else:
                accessible_only.append(acc)

    return {
        "has_standard": has_standard,
        "has_accessible": has_accessible,
        "acc_numbers_for": acc_numbers_for,
        "std_number_for": std_number_for,
        "accessible_only_slides": accessible_only,
        "path_for_slide": path_for_slide,
    }


def _slide_label(slide: SlideInfo) -> str:
    scene = slide.scene_title or f"Scene {slide.scene_number}"
    return f"{scene} / {slide.slide_title}"


# Texts that look like loading / placeholder noise — never a title.
_PLACEHOLDER_TITLE_RE = re.compile(
    r"^\s*(loading|please wait|wait|one moment|continue)\s*[\.\u2026]*\s*$",
    re.IGNORECASE,
)

# Progress / counter labels that appear at the top of slides but are not
# titles — e.g. "Screen 3 of 66", "Question 1 of 4", "Page 2 of 10",
# "Slide 5 of 12", "Step 1 of 3".
_COUNTER_RE = re.compile(
    r"^\s*(screen|question|page|slide|step)\s+\d+\s+of\s+\d+\s*$",
    re.IGNORECASE,
)


def _detect_on_screen_title(slide: SlideInfo) -> Optional[str]:
    """
    Heuristic: the on-screen title is a *short* text object near the top of
    the base layer that has at least one longer body-text object below it.
    Returns None when nothing plausible is found — not every course has an
    on-screen title and we'd rather report nothing than a wrong guess.

    Storyline courses sometimes use uniform font sizes (so font alone is a
    poor signal); we lean on length + position + presence-of-body instead.
    """
    if not slide.layers:
        return None

    base = next((l for l in slide.layers if l.is_base), None)
    if base is None:
        base = slide.layers[0]

    # All visible text-type objects on the base layer
    base_texts = [
        o for o in base.objects
        if o.has_text and o.acc_type == "text"
    ]
    if len(base_texts) < 2:
        # Need at least one body element below the title for confidence.
        return None

    # Title candidates: short, near the top, not placeholder/counter noise.
    candidates = [
        o for o in base_texts
        if o.y_pos < 100
        and 3 <= len(o.text) <= 80
        and not _PLACEHOLDER_TITLE_RE.match(o.text)
        and not _COUNTER_RE.match(o.text)
    ]
    if not candidates:
        return None

    # Pick the topmost; tie-break on shortest text.
    candidates.sort(key=lambda o: (o.y_pos, len(o.text)))
    candidate = candidates[0]

    # Confidence check: there must be at least one longer body text object
    # below the candidate (excluding placeholder noise).
    body_below = [
        o for o in base_texts
        if o.y_pos > candidate.y_pos
        and len(o.text) > len(candidate.text)
        and not _PLACEHOLDER_TITLE_RE.match(o.text)
    ]
    if not body_below:
        return None

    return candidate.text.strip()


# ---------------------------------------------------------------------------
# 3. Acronym spacing check
# ---------------------------------------------------------------------------

# An "all-caps run" is 2+ consecutive uppercase letters with NO whitespace
# between them.  If the word is already letter-spaced (e.g. "U R L") it
# would not match this pattern, so it won't be flagged.
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,}\b")


def check_acronym_spacing(data: ScormData) -> "Section":
    sec = Section("Acronym Letter-Spacing (JAWS)")

    checker = _get_spell_checker()
    if checker is None:
        sec.add(
            "info",
            "Spellchecker unavailable — every all-caps word will be flagged. "
            "Run: pip install pyspellchecker"
        )

    # Aggregate hits per acronym so we report each one once with locations.
    hits: dict[str, list[str]] = {}

    for slide in get_accessible_path_slides(data):
        loc = _slide_label(slide)
        seen_for_slide: set[str] = set()
        for text in slide.texts:
            for m in _ACRONYM_RE.finditer(text):
                word = m.group(0)
                # Precedence: ALWAYS_FLAG > ALWAYS_IGNORE > spellcheck.
                if word in _ACRONYM_ALWAYS_FLAG:
                    pass  # fall through and flag
                elif word in _ACRONYM_ALWAYS_IGNORE:
                    continue
                elif checker is not None and not checker.unknown([word.lower()]):
                    # pyspellchecker recognises this as a real English word
                    # styled in caps for emphasis (NOTE, WHOOPS, BUSINESS,
                    # MERIDIAN, etc.) — JAWS pronounces it fine, so skip.
                    continue
                key = f"{word}::{loc}"
                if key in seen_for_slide:
                    continue
                seen_for_slide.add(key)
                hits.setdefault(word, []).append(loc)

    if not hits:
        sec.add("pass", "No unspaced acronyms detected in the Accessible Path")
        return sec

    for word in sorted(hits.keys()):
        locs = hits[word]
        unique = list(dict.fromkeys(locs))
        loc_str = "; ".join(unique[:3])
        more = f" (+{len(unique) - 3} more)" if len(unique) > 3 else ""
        sec.add(
            "warn",
            f'"{word}" should be letter-spaced for JAWS (e.g. "{" ".join(word)}") — {loc_str}{more}'
        )

    return sec


# ---------------------------------------------------------------------------
# 4. Back-end slide title checks
# ---------------------------------------------------------------------------

def check_slide_titles(data: ScormData) -> "Section":
    sec = Section("Slide Titles (back-end vs on-screen)")

    internal_count = 0
    mismatch_count = 0
    no_title_count = 0
    checked = 0

    for slide in get_accessible_path_slides(data):
        checked += 1
        backend = (slide.slide_title or "").strip()
        loc = _slide_label(slide)

        # 1) Back-end title contains internal naming (underscores / prefixes)
        if backend:
            lower = backend.lower()
            if _INTERNAL_TITLE_RE.search(backend) or any(lower.startswith(p) for p in _INTERNAL_TITLE_PREFIXES):
                internal_count += 1
                sec.add(
                    "warn",
                    f'Back-end title "{backend}" looks like an internal name — '
                    f"JAWS will read it literally. Rename for accessibility. ({loc})"
                )

        # 2) Compare back-end title to detected on-screen title
        on_screen = _detect_on_screen_title(slide)
        if on_screen is None:
            no_title_count += 1
            continue

        # Normalise for comparison: lower-case + strip ALL non-alphanumerics
        # so the comparison ignores capitalisation, whitespace, punctuation,
        # and acronym letter-spacing.  This allows two patterns to match:
        #   1) on-screen prepends a module label
        #      backend  : "Introduction"
        #      on-screen: "Module 1: Data and Device Security: Introduction"
        #   2) on-screen letter-spaces an acronym for JAWS
        #      backend  : "BEC Explained"
        #      on-screen: "B E C Explained"
        # Acronym letter-spacing, extra-space, and spelling have their own
        # dedicated checks elsewhere, so we don't need to enforce them here.
        norm_backend = _strip_for_match(backend)
        norm_screen = _strip_for_match(on_screen)
        if norm_backend and not norm_screen.endswith(norm_backend):
            mismatch_count += 1
            sec.add(
                "warn",
                f'Back-end title ≠ on-screen title: backend="{backend}" | on-screen="{on_screen}" ({loc})'
            )

    if checked == 0:
        sec.add("info", "No Accessible Path slides found — title check skipped")
        return sec

    if internal_count == 0:
        sec.add("pass", "No back-end titles contain underscores or internal prefixes")
    if mismatch_count == 0:
        sec.add("pass", "All detected on-screen titles match their back-end titles")
    if no_title_count > 0:
        sec.add(
            "info",
            f"{no_title_count} slide(s) had no detectable on-screen title — verify manually if expected"
        )

    return sec


# ---------------------------------------------------------------------------
# 5. Keyboard / tab-order checks
# ---------------------------------------------------------------------------

def check_keyboard_navigation(data: ScormData) -> "Section":
    sec = Section("Keyboard Navigation & Tab Order")

    issues_found = False

    for slide in get_accessible_path_slides(data):
        loc = _slide_label(slide)

        for layer in slide.layers:
            # Tabbable objects (anything with tabEnabled and a real tabIndex)
            tabbable = [
                o for o in layer.objects
                if o.tab_enabled and o.tab_index >= 0
            ]

            # 5a) Buttons that aren't keyboard reachable
            for o in layer.objects:
                if o.acc_type == "button":
                    if not o.tab_enabled or o.tab_index < 0:
                        issues_found = True
                        name = o.text or o.alt_text or o.reference_name or "(unnamed)"
                        sec.add(
                            "fail",
                            f'Button "{name[:40]}" is not keyboard reachable '
                            f'(tabEnabled={o.tab_enabled}, tabIndex={o.tab_index}) — {loc}'
                        )

            # 5b) Click-handling text (object has onclick but accType=='text')
            for o in layer.objects:
                if o.has_click_event and o.acc_type == "text":
                    issues_found = True
                    name = o.text[:40] or o.reference_name or "(unnamed)"
                    sec.add(
                        "warn",
                        f'Object "{name}" has a click action but accType="text" — '
                        f'JAWS will not announce it as a button ({loc})'
                    )

            # 5c) (removed) "tabIndex>0 + tabEnabled=False" was too noisy:
            # Storyline assigns a default tabIndex to most objects and uses
            # tabEnabled=False to legitimately remove them from focus order.
            # The button-not-keyboard-reachable check (5a) above already
            # catches the real bug — a button that can't actually be tabbed.

            # 5d) Duplicate tabIndex on the same layer
            seen: dict[int, list[AccObject]] = {}
            for o in tabbable:
                seen.setdefault(o.tab_index, []).append(o)
            for ti, objs in seen.items():
                if len(objs) > 1:
                    issues_found = True
                    names = ", ".join(
                        (o.text or o.alt_text or o.reference_name or "?")[:25] for o in objs
                    )
                    sec.add(
                        "warn",
                        f"Duplicate tabIndex {ti} on layer "
                        f"({'base' if layer.is_base else 'overlay'}): {names} — {loc}"
                    )

            # 5e) Focusable ghosts: tabEnabled with no name source at all
            for o in layer.objects:
                if not o.tab_enabled:
                    continue
                if o.tab_index < 0:
                    continue
                has_name = bool(o.text or o.alt_text or o.reference_name)
                if not has_name:
                    issues_found = True
                    sec.add(
                        "warn",
                        f"Tab-stop with no name (objId={o.obj_id}, kind={o.kind}) — "
                        f"JAWS will announce it as 'blank' ({loc})"
                    )

    if not issues_found:
        sec.add("pass", "No keyboard / tab-order issues detected in the Accessible Path")
    return sec


# ---------------------------------------------------------------------------
# 6. Modal / dialog labelling
# ---------------------------------------------------------------------------

def check_dialog_labels(data: ScormData) -> "Section":
    sec = Section("Modal / Dialog Labels")

    flagged = 0
    total_dialogs = 0
    for slide in get_accessible_path_slides(data):
        loc = _slide_label(slide)

        for layer in slide.layers:
            if layer.present_as != "dialog":
                continue
            total_dialogs += 1
            if not layer.labeled_by_id:
                flagged += 1
                sec.add(
                    "warn",
                    f'Dialog layer "{layer.layer_id}" has no labeledById — '
                    f"JAWS will announce it without a name ({loc})"
                )

    if total_dialogs == 0:
        sec.add("info", "No dialog/modal layers found in the Accessible Path")
    elif flagged == 0:
        sec.add("pass", f"All {total_dialogs} dialog layer(s) have an accessible label")
    return sec


# ---------------------------------------------------------------------------
# 7. Reading order vs visual order
# ---------------------------------------------------------------------------
# JAWS reads objects in tab-index order. If the visual layout is left-to-right,
# top-to-bottom but the tab order is something else, sighted screen-reader and
# screen-magnifier users will hear content in an order that doesn't match what
# they see. We flag *backward jumps* — pairs of consecutive tab stops where
# the second is significantly above (or to the left of, on the same row) the
# first. Tolerances absorb small position differences from baseline alignment.

_READING_ORDER_Y_TOLERANCE = 20   # px — same "row" within this y-band
_READING_ORDER_X_TOLERANCE = 20   # px — ignore tiny x jitter
_READING_ORDER_PER_LAYER_LIMIT = 3  # max issues to print per layer (rest summarised)


def check_reading_order(data: ScormData) -> "Section":
    sec = Section("Reading Order vs Visual Order")

    issues_found = False

    for slide in get_accessible_path_slides(data):
        loc = _slide_label(slide)

        for layer in slide.layers:
            tabbable = [
                o for o in layer.objects
                if o.tab_enabled and o.tab_index > 0
            ]
            if len(tabbable) < 2:
                continue
            tabbable.sort(key=lambda o: o.tab_index)

            backward_jumps = []
            for i in range(len(tabbable) - 1):
                a = tabbable[i]
                b = tabbable[i + 1]
                if b.y_pos < a.y_pos - _READING_ORDER_Y_TOLERANCE:
                    backward_jumps.append((a, b, "above"))
                elif (abs(b.y_pos - a.y_pos) <= _READING_ORDER_Y_TOLERANCE
                      and b.x_pos < a.x_pos - _READING_ORDER_X_TOLERANCE):
                    backward_jumps.append((a, b, "left of"))

            if backward_jumps:
                issues_found = True
                layer_label = "base" if layer.is_base else f"overlay {layer.layer_id[:8]}"
                for a, b, direction in backward_jumps[:_READING_ORDER_PER_LAYER_LIMIT]:
                    a_name = (a.text or a.alt_text or a.reference_name or "?")[:30]
                    b_name = (b.text or b.alt_text or b.reference_name or "?")[:30]
                    sec.add(
                        "warn",
                        f'Tab order goes backward on {layer_label}: '
                        f'tabIdx {b.tab_index} "{b_name}" is {direction} '
                        f'tabIdx {a.tab_index} "{a_name}" — {loc}'
                    )
                if len(backward_jumps) > _READING_ORDER_PER_LAYER_LIMIT:
                    extra = len(backward_jumps) - _READING_ORDER_PER_LAYER_LIMIT
                    sec.add(
                        "info",
                        f"  ... +{extra} more backward jump(s) on {layer_label} ({loc})"
                    )

    if not issues_found:
        sec.add("pass", "Tab order matches visual order on all Accessible Path layers")
    return sec


# ---------------------------------------------------------------------------
# 8. Radio-button Tab-key instructions
# ---------------------------------------------------------------------------
# In the Accessible Path, a single-select question renders as a group of radio
# buttons. When a JAWS user reaches the group and presses the Down arrow to
# hear the next option, the radio group SELECTS that option instead of just
# moving to it — so an arrow press silently changes their answer. The
# accessible fix courses use is on-screen text telling the learner to press
# Tab (not the arrow keys) to move between choices. This check flags any
# radio-button question whose slide is missing that guidance.
#
# Storyline question types: "multiplechoice" (one answer) and "truefalse" both
# render as radio buttons; "multipleresponse" renders as checkboxes, where the
# arrow-selects-next problem does not occur, so it is exempt.
_RADIO_QUESTION_TYPES = {"multiplechoice", "truefalse"}

# On-screen guidance counts when the text mentions the Tab key alongside an
# arrow/select/move/key context — loose enough to match real phrasings
# ("Use the Tab key to move between answers", "Press Tab to review each option
# before selecting"), tight enough that a stray "tab" elsewhere won't pass.
_TAB_WORD_RE = re.compile(r"\btab\b", re.IGNORECASE)
_TAB_CONTEXT_RE = re.compile(r"\b(arrow|select|selects|move|moving|key|keys|choice|choices|option|options|answer|answers)\b", re.IGNORECASE)


def _is_radio_question(ia) -> bool:
    """True for single-select questions that render as radio buttons."""
    return (not ia.is_survey) and (ia.question_type in _RADIO_QUESTION_TYPES)


def check_radio_tab_instructions(data: ScormData) -> "Section":
    sec = Section("Radio-Button Tab Instructions (JAWS)")

    checked = 0
    flagged = 0

    for slide in get_accessible_path_slides(data):
        radio_qs = [ia for ia in slide.interactions if _is_radio_question(ia)]
        if not radio_qs:
            continue
        checked += 1

        slide_text = " ".join(slide.texts)
        has_tab_guidance = bool(
            _TAB_WORD_RE.search(slide_text) and _TAB_CONTEXT_RE.search(slide_text)
        )
        if not has_tab_guidance:
            flagged += 1
            loc = _slide_label(slide)
            q_label = radio_qs[0].question_text[:50] or radio_qs[0].lms_id or "(unnamed)"
            sec.add(
                "warn",
                f'Radio-button question "{q_label}" has no on-screen Tab-key '
                f"instruction — a JAWS user pressing the Down arrow will select "
                f"the next answer instead of just moving to it. Add text telling "
                f"the learner to use the Tab key to move between choices. ({loc})"
            )

    if checked == 0:
        sec.add("info", "No radio-button questions found in the Accessible Path")
    elif flagged == 0:
        sec.add(
            "pass",
            f"All {checked} radio-button question slide(s) include on-screen "
            "Tab-key guidance"
        )

    return sec


# ---------------------------------------------------------------------------
# Helpers shared by other JAWS checks above
# ---------------------------------------------------------------------------

def _strip_for_match(s: str) -> str:
    """
    Aggressive normalisation for substring matching: lower-case and strip
    every non-alphanumeric character.  Used by check_slide_titles so that
    spacing, punctuation, capitalisation, and acronym letter-spacing don't
    cause spurious mismatches.
    """
    return re.sub(r"[^a-z0-9]", "", (s or "").casefold())


# ---------------------------------------------------------------------------
# Master JAWS runner
# ---------------------------------------------------------------------------

def run_jaws_checks(data: ScormData) -> list:
    """
    Run every JAWS / screen-reader static check on the Accessible Path slides
    of the given course.  Returns a list of Sections in display order.

    The caller (checks.run_all_checks) is responsible for deciding whether to
    invoke this — it should only do so when the user has marked the course
    as dual-path AND an Accessible Path scene actually exists.
    """
    return [
        check_acronym_spacing(data),
        check_slide_titles(data),
        check_keyboard_navigation(data),
        check_reading_order(data),
        check_radio_tab_instructions(data),
        # check_dialog_labels(data),
        # ^ Disabled by team practice: JAWS announcing "modal dialog" without a
        # name is currently acceptable. Re-enable if that policy changes.
    ]


def has_accessible_path(data: ScormData) -> bool:
    """
    Return True if the course appears to have an Accessible Path scene.
    Uses both detection rules in get_accessible_path_scene_ids().
    """
    return bool(get_accessible_path_scene_ids(data))
