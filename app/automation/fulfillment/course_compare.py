"""
Fulfillment: compare a renewal proposal's course list with the portal's.

Input
-----
* One or more proposal / order PDFs. Layouts vary by who wrote the proposal:
    - Appendix-style: two columns of "Course Name 10291" entries, where long
      names wrap onto the next line and "- NEW" / "- UPDATED" tags float
      around them.
    - SKU-style: "CL-9282-CG - Excel 2019 Level 1 Basic" rows in the pricing
      table.
    - SKU-only: a pricing table row whose SKU cell is just "CL-11570" and
      whose description is free text ("... upgrade from the 2021 OWASP
      bundle to the new OWASP Top 10 11570 course.").
  Both are read from word positions (pdfplumber), not from plain text, so
  the two columns don't get interleaved.
* The portal's Content Licensing list, copied and pasted as-is (tab-separated:
  name, then a few numeric columns, then the item type).

Output
------
Compared by course number only — names differ between the proposal and the
portal ("&" vs "and", capitalisation, etc.), numbers don't.

* to_add          on the PDF, not on the portal
* archive_remove  on the portal, not on the PDF
* archive_reset   on both, plus portal-only courses the user chose to keep
                  (a change order that only lists what's changing)
* review          anything that could not be placed with confidence (portal
                  items with no number, year-like
                  numbers, lines in the PDF
                  that mention a number but couldn't be read as a course).
                  Review items are kept OUT of the three lists.

Each list is sorted by the full course name but is output as numbers only.

Nothing here is guaranteed: the PDF side is shown for review in the UI before
comparing, precisely because proposal layouts aren't consistent.
"""

from __future__ import annotations

import difflib
import re
import statistics
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Optional

# A course number: 4-5 digits, optionally a single capital-letter suffix
# (e.g. 11569P = the test-out variant).
# 4-5 digits plus any letters attached with no space. Each variant is its own
# course: 11569, 11569P, 11569A, 11569AP and 11569TX are five different courses.
_NUM = r"\d{4,5}[A-Za-z]{0,4}(?![A-Za-z])"
# Characters that, directly before a number, mean it's part of something
# else (a date, a price, a SKU, a phone number...).
_NOT_BEFORE = r"(?<![\w/.,$#+\-–:])"

_TRAILING_NUM_RE = re.compile(rf"{_NOT_BEFORE}({_NUM})$")
# "SecureClip 10201: Phishing: What Happens When You Click"
_PREFIX_NUM_RE = re.compile(rf"{_NOT_BEFORE}({_NUM}):\s+\S")
# "CL-9282-CG - Excel 2019 Level 1 Basic"
_SKU_RE = re.compile(rf"\bCL-({_NUM})-[A-Z0-9]+\s*[-–—]\s*(.+)$")
# "CyberEssentials: Principles of Cybersecurity 11569P (includes" + wrapped ")"
_NUM_THEN_PAREN_RE = re.compile(rf"{_NOT_BEFORE}({_NUM})\s+\((.*)$")
# Any number-like token, for the "couldn't read this line" safety net.
_ANY_NUM_RE = re.compile(rf"{_NOT_BEFORE}\b({_NUM})\b(?![/.,]\d)")

# "- NEW", "- UPDATED", "- UPDATED VERSION", "- NEW MODULE"
_TAG_WORDS = r"(?:NEW|UPDATED)(?:\s+(?:VERSION|MODULE))?"
_TRAILING_TAG_RE = re.compile(rf"\s+[-–—](?:\s*({_TAG_WORDS}))?\s*$", re.IGNORECASE)
_LEADING_TAG_RE = re.compile(rf"^(?:[-–—]\s*)?({_TAG_WORDS})\b\s*|^[-–—]\s*$", re.IGNORECASE)
# ...including the second word of a tag that wrapped on its own ("- UPDATED" / "VERSION").
_ONLY_TAG_RE = re.compile(rf"^[-–—]?\s*(?:{_TAG_WORDS}|VERSION|MODULE)?\s*$", re.IGNORECASE)

# Lines that are never courses, whatever numbers they contain.
_EXCLUDE_RE = re.compile(
    r"\$|@|www\.|https?://|\bPage \d+ of \d+|Document Ref|Copyright|\bPO Box\b|"
    r"\bEstimate\b|\bInvoice\b|tax ID|\bPhone\b|\bFax\b",
    re.IGNORECASE,
)

# Dates ("August 13, 2026", "08 / 31 / 2026") end in a 4-digit number too.
_DATE_TAIL_RE = re.compile(
    r"(?:\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?|[/,\-])\s*\d{4}$",
    re.IGNORECASE,
)
# Street addresses ("San Francisco, CA 94107 US") — ignored by the safety net.
_ADDRESS_RE = re.compile(r"\b[A-Z]{2},? \d{5}(?:-\d{4})?\b")
# The same, needing the punctuation after the city ("Fairfax, VA 22033", "Fairfax. VA
# 22033") so "Generative AI 11233" isn't one.
_POSTCODE_RE = re.compile(r"[,.]\s*[A-Z]{2},?\s+\d{5}(?:-\d{4})?\b")

_MAX_WRAP_LINES = 3  # longest a course name realistically wraps


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Course:
    number: str        # normalised, e.g. "10291", "11569P"
    name: str          # full name incl. the number, used for sorting
    note: str = ""     # "NEW" / "UPDATED" from the proposal, if any
    source: str = ""   # "Portal" or the PDF file name
    license_count: Optional[int] = None  # portal only: the license count column


@dataclass(frozen=True)
class ReviewItem:
    text: str
    source: str
    reason: str


@dataclass(frozen=True)
class ExpDate:
    date: str          # normalised MM/DD/YYYY
    source: str        # PDF file name
    found_as: str      # the text it was read from, for checking


@dataclass
class ExpDateResult:
    date: str = ""                     # "" when nothing was found
    candidates: list[ExpDate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # conflicts, multi-year, ...


@dataclass
class ParseResult:
    courses: list[Course] = field(default_factory=list)
    review: list[ReviewItem] = field(default_factory=list)
    exp_dates: list[ExpDate] = field(default_factory=list)
    multi_year_notes: list[str] = field(default_factory=list)
    # Raw PDF content, kept so order_check can read the rest of the document
    # without opening it again. Empty for pasted text.
    source: str = ""
    text: str = ""
    tables: list[list[list[str]]] = field(default_factory=list)  # table -> row -> cell


@dataclass
class Comparison:
    to_add: list[Course]
    archive_remove: list[Course]
    archive_reset: list[Course]
    review: list[ReviewItem]
    # (pdf course being added, portal course being removed) whose names are
    # near-identical — usually a renumbered/updated version of the same course.
    possible_replacements: list[tuple[Course, Course]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_text(s: str) -> str:
    """Drop zero-width chars / nbsp and collapse whitespace."""
    s = s.replace("​", "").replace("﻿", "").replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def is_year_like(number: str) -> bool:
    return number.isdigit() and 1990 <= int(number) <= 2039


def base_number(number: str) -> str:
    """Digits only: 11569AP -> 11569."""
    return re.match(r"\d*", number).group(0)


def has_letter_suffix(number: str) -> bool:
    return bool(number) and number[-1].isalpha()


def course_number_from_name(name: str) -> Optional[str]:
    """Trailing number ("Slay The Scam 10781") or a "SecureClip 10201: ..." prefix."""
    name = clean_text(name)
    m = _TRAILING_NUM_RE.search(name)
    if m:
        return m.group(1).upper()
    m = _PREFIX_NUM_RE.search(name)
    if m:
        return m.group(1).upper()
    return None


def format_numbers(courses: Iterable[Course]) -> str:
    return ", ".join(c.number for c in courses)


def _sort_by_name(courses: Iterable[Course]) -> list[Course]:
    return sorted(courses, key=lambda c: (c.name.casefold(), c.number))


# ---------------------------------------------------------------------------
# Portal list
# ---------------------------------------------------------------------------

# The Content Licensing table is: course name | company id | license count |
# player type. Copy the whole table and paste it as-is; everything after the
# name is dropped. Matched on the collapsed line so it works whether the paste
# kept its tabs or not. Exactly two number columns: a greedy name keeps
# numbers that belong to it ("OWASP 2021 Top 10 Essentials 10736").
_PORTAL_ROW_RE = re.compile(r"^(?P<name>.*\S)\s+(?P<id>\d+)\s+(?P<count>\d+)\s+(?P<type>[A-Za-z]\w*)$")
# A player-type cell on its own line (some browsers copy one cell per line):
# CamelCase with 2+ humps, e.g. ThirdPartyScorm, ItemCollection.
_PLAYER_TYPE_RE = re.compile(r"^[A-Z][a-z]+(?:[A-Z][a-z]*)+$")


# A portal license count of 5 means a demo license: if the renewal includes
# that course it still has to be set up properly, so it counts as a content add.
DEMO_LICENSE_COUNT = 5


def _split_portal_row(raw: str) -> tuple[str, str, Optional[int]]:
    """Return (course name, player type, license count) for one pasted row."""
    line = clean_text(raw)
    m = _PORTAL_ROW_RE.match(line)
    if m:
        return clean_text(m.group("name")), m.group("type"), int(m.group("count"))
    # Fallback: tab-separated with a different column count — first cell is the name.
    cells = [clean_text(c) for c in raw.split("\t") if clean_text(c)]
    if len(cells) > 1:
        ptype = cells[-1] if _PLAYER_TYPE_RE.match(cells[-1]) else ""
        return cells[0], ptype, None
    return line, "", None


def parse_portal_list(text: str) -> ParseResult:
    """Parse the pasted Content Licensing table.

    Paste the whole table; ItemCollection rows are skipped entirely. The course
    number is read from the name; the license count is kept (a count of
    DEMO_LICENSE_COUNT marks a demo license); company id and player type are
    dropped.
    """
    result = ParseResult()
    seen: dict[str, Course] = {}
    # One-cell-per-line copies put the player type lines after the name, so
    # remember what the last name line produced in case it turns out to be a
    # collection.
    last_added: Optional[tuple[str, object]] = None
    stray_numbers: list[int] = []   # number cells seen since the last name line

    for raw in text.splitlines():
        line = clean_text(raw)
        if not line:
            continue
        # Stray cells from a one-cell-per-line copy: bare numbers / player type.
        if line.isdigit() or _PLAYER_TYPE_RE.match(line):
            if line.isdigit():
                stray_numbers.append(int(line))
            elif last_added is not None and last_added[0] == "course" and len(stray_numbers) == 2:
                # company id, license count, then the player type: fill in the count
                old = last_added[1]
                new = replace(old, license_count=stray_numbers[1])
                result.courses[result.courses.index(old)] = new
                seen[new.number] = new
                last_added = ("course", new)
            if line.casefold() == "itemcollection" and last_added is not None:
                kind, item = last_added
                if kind == "review":
                    result.review.remove(item)
                else:
                    result.courses.remove(item)
                    seen.pop(item.number, None)
            if not line.isdigit():
                last_added = None  # the player type closes the row
            continue
        last_added = None
        stray_numbers = []

        name, player_type, license_count = _split_portal_row(raw)

        if player_type.casefold() == "itemcollection":
            continue  # collections aren't courses; never listed anywhere

        number = course_number_from_name(name)
        if not number:
            item = ReviewItem(name, "Portal",
                "No course number in the name (custom or client-specific item?).")
            result.review.append(item)
            last_added = ("review", item)
            continue

        if number in seen:
            result.review.append(ReviewItem(name, "Portal",
                f"Course number {number} appears more than once on the portal "
                f"(also: {seen[number].name})."))
            continue

        course = Course(number=number, name=name, source="Portal",
                        license_count=license_count)
        seen[number] = course
        result.courses.append(course)
        last_added = ("course", course)

    return result


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

@dataclass
class _Segment:
    top: float
    x0: float
    text: str


def _group_lines(words: list[dict], tolerance: float = 3.0) -> list[list[dict]]:
    words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: list[list[dict]] = []
    for w in words:
        if lines and abs(w["top"] - lines[-1][0]["top"]) <= tolerance:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(l, key=lambda w: w["x0"]) for l in lines]


def _split_line(line: list[dict], anchors: list[float], big_gap: float) -> list[list[dict]]:
    """Split a line into segments at big gaps, or at a column anchor."""
    segs = [[line[0]]]
    for prev, w in zip(line, line[1:]):
        gap = w["x0"] - prev["x1"]
        at_anchor = gap > 4 and any(abs(w["x0"] - a) <= 3 for a in anchors)
        if gap > big_gap or at_anchor:
            segs.append([w])
        else:
            segs[-1].append(w)
    return segs


def _find_anchors(segments: list[list[dict]], min_count: int = 3, tol: float = 6.0) -> list[float]:
    """x positions where several segments start = column left edges."""
    xs = sorted(s[0]["x0"] for s in segments)
    groups: list[list[float]] = []
    for x in xs:
        if groups and x - groups[-1][0] <= tol:
            groups[-1].append(x)
        else:
            groups.append([x])
    return [statistics.median(g) for g in groups if len(g) >= min_count]


def _page_columns(words: list[dict]) -> tuple[dict[Optional[int], list[_Segment]], float]:
    """Return {column index or None for loose segments: [segments top-down]}, gap limit."""
    if not words:
        return {}, 0.0
    lines = _group_lines(words)
    big_gap = 12.0
    first_pass = [s for l in lines for s in _split_line(l, [], big_gap)]
    anchors = _find_anchors(first_pass)

    columns: dict[Optional[int], list[_Segment]] = {}
    for line in lines:
        for seg in _split_line(line, anchors, big_gap):
            x0 = seg[0]["x0"]
            text = clean_text(" ".join(w["text"] for w in seg))
            if not text:
                continue
            col = None
            for i, a in enumerate(anchors):
                if abs(x0 - a) <= 6:
                    col = i
                    break
            columns.setdefault(col, []).append(_Segment(seg[0]["top"], x0, text))

    heights = [w["bottom"] - w["top"] for w in words if w.get("bottom") is not None]
    h = statistics.median(heights) if heights else 9.0
    return columns, max(h * 2.2, 14.0)


class _ColumnReader:
    """Reads one column top-down, joining wrapped names until a number ends them."""

    def __init__(self, source: str, result: ParseResult, found: dict[str, Course]):
        self.source = source
        self.result = result
        self.found = found
        self.buffer: list[str] = []
        self.pending_paren = False
        self.pending_course: Optional[Course] = None
        self.prev_top: Optional[float] = None
        self.last_index: Optional[int] = None  # last course this column emitted

    # -- output ---------------------------------------------------------
    def _emit(self, number: str, name: str, note: str = "") -> None:
        number = number.upper()
        name = clean_text(name)
        if _POSTCODE_RE.search(name):
            return  # a postcode ("Fairfax, VA 22033"), not a course
        if number in self.found:
            return  # listed twice in the proposal (it happens)
        if is_year_like(number) and _DATE_TAIL_RE.search(name):
            return  # a date, not a course
        course = Course(number=number, name=name, note=note.upper(), source=self.source)
        self.found[number] = course
        self.result.courses.append(course)
        self.last_index = len(self.result.courses) - 1

    def _tag_last(self, tag: str) -> None:
        """A "NEW"/"UPDATED" that wrapped below the course it belongs to."""
        if self.last_index is None or not tag:
            return
        course = self.result.courses[self.last_index]
        if not course.note:
            updated = replace(course, note=tag.upper())
            self.result.courses[self.last_index] = updated
            self.found[course.number] = updated

    def _flush(self) -> None:
        """Discard the buffer, flagging it if it looked like it had a course number."""
        if self.buffer:
            text = " ".join(self.buffer)
            nums = [n for n in _ANY_NUM_RE.findall(text) if not is_year_like(n)]
            if nums and not _ADDRESS_RE.search(text):
                self.result.review.append(ReviewItem(text, self.source,
                    "Mentions a number but couldn't be read as a course. Check the PDF."))
        self.buffer = []
        self.pending_paren = False

    # -- input ----------------------------------------------------------
    def feed(self, seg: _Segment, gap_limit: float) -> None:
        if self.prev_top is not None and seg.top - self.prev_top > gap_limit:
            self._flush()
        self.prev_top = seg.top
        text = seg.text

        if self.pending_paren:
            idx = text.find(")")
            if idx == -1:
                return
            text = text[idx + 1:].strip()
            self.pending_paren = False

        # tags that wrapped onto this line ("NEW" under "... 11537 -")
        while True:
            m = _LEADING_TAG_RE.match(text)
            if not m or not m.group(0):
                break
            if m.group(1) and not self.buffer:
                self._tag_last(m.group(1))
            text = text[m.end():].strip()
        if not text or _ONLY_TAG_RE.match(text):
            return

        if _EXCLUDE_RE.search(text):
            self.buffer = []
            return

        m = _SKU_RE.search(text)
        if m:
            self.buffer = []
            number, desc = m.group(1), m.group(2)
            desc, note = self._strip_trailing_tag(desc)
            self._emit(number, f"{desc} {number}", note)
            return

        m = _PREFIX_NUM_RE.search(text)
        if m and not _TRAILING_NUM_RE.search(text):
            self._flush()
            body, note = self._strip_trailing_tag(text)
            self._emit(m.group(1), body, note)
            return

        body, note = self._strip_trailing_tag(text)
        m = _TRAILING_NUM_RE.search(body)
        if m:
            name = " ".join(self.buffer + [body])
            self.buffer = []
            self._emit(m.group(1), name, note)
            return

        m = _NUM_THEN_PAREN_RE.search(body)
        if m:
            name = " ".join(self.buffer + [body[:m.end(1)]])
            self.buffer = []
            self._emit(m.group(1), name, note)
            if ")" not in m.group(2):
                self.pending_paren = True
            return

        self.buffer.append(text)
        if len(self.buffer) > _MAX_WRAP_LINES:
            self.buffer.pop(0)

    @staticmethod
    def _strip_trailing_tag(text: str) -> tuple[str, str]:
        m = _TRAILING_TAG_RE.search(text)
        if m:
            return text[:m.start()].strip(), (m.group(1) or "")
        m = re.search(rf"\s+({_TAG_WORDS})$", text)
        if m and _TRAILING_NUM_RE.search(text[:m.start()]):
            return text[:m.start()].strip(), m.group(1)
        return text, ""


def extract_courses_from_words(pages: list[list[dict]], source: str) -> ParseResult:
    """Pure core of PDF extraction. ``pages`` = pdfplumber-style word dicts
    (x0, x1, top, bottom, text) per page."""
    result = ParseResult()
    found: dict[str, Course] = {}
    for words in pages:
        columns, gap_limit = _page_columns(words)
        for col, segments in columns.items():
            if col is None:
                # Loose segments (headings, floating tags): each read on its own.
                for seg in segments:
                    reader = _ColumnReader(source, result, found)
                    reader.feed(seg, gap_limit)
                    reader._flush()
                continue
            reader = _ColumnReader(source, result, found)
            for seg in sorted(segments, key=lambda s: s.top):
                reader.feed(seg, gap_limit)
            reader._flush()

    # Keep reading order stable-ish for the review table: by name.
    result.courses = _sort_by_name(result.courses)
    # Drop safety-net review lines whose numbers were all read elsewhere.
    kept = []
    for r in result.review:
        nums = {n.upper() for n in _ANY_NUM_RE.findall(r.text) if not is_year_like(n)}
        if nums and nums <= set(found):
            continue
        kept.append(r)
    result.review = kept
    return result


# ---------------------------------------------------------------------------
# Portal expiration date
# ---------------------------------------------------------------------------

_DATE = r"\d{1,2}\s*[/-]\s*\d{1,2}\s*[/-]\s*\d{2,4}"
# "License Term: 9/29/2026-9/28/2027", "License Term: 8/20/2026 to 8/19/2027",
# "Renewal PD: 9/29/2026-9/28/2027", "License Dates: 10/18/2026-10/17/2027"
# (Order Info email) -> the END of the term.
_TERM_RE = re.compile(
    rf"(?:License\s+Term|License\s+Dates?|Renewal\s+PD|License\s+Period|Term\s+Dates?)\s*:?\s*"
    rf"({_DATE})\s*(?:-|–|—|to|through|thru)\s*({_DATE})",
    re.IGNORECASE,
)
# Estimate/invoice header: "EXPIRATION DATE 09/28/2027" (used only if no term).
_EXPIRATION_RE = re.compile(rf"EXPIRATION\s+DATE\s*:?\s*({_DATE})", re.IGNORECASE)
# "Licensing Option Selected: 3 Year Licensing - Billed Upfront"
_MULTI_YEAR_RE = re.compile(r"Licensing\s+Option\s+Selected\s*:\s*(\d+)\s*Year[^\n]*", re.IGNORECASE)


def _normalise_date(d: str) -> Optional[str]:
    parts = re.split(r"\s*[/-]\s*", d.strip())
    if len(parts) != 3:
        return None
    try:
        m, day, y = (int(x) for x in parts)
    except ValueError:
        return None
    if y < 100:
        y += 2000
    if not (1 <= m <= 12 and 1 <= day <= 31):
        return None
    return f"{m:02d}/{day:02d}/{y}"


def find_expiration_dates(text: str, source: str) -> tuple[list[ExpDate], list[str]]:
    """Return (dates, multi-year notes) found in one PDF's text.

    License-term end dates are preferred; an estimate's EXPIRATION DATE is only
    used when the PDF has no term.
    """
    text = clean_text_keep_lines(text)
    dates: list[ExpDate] = []
    for m in _TERM_RE.finditer(text):
        d = _normalise_date(m.group(2))
        if d:
            dates.append(ExpDate(d, source, clean_text(m.group(0))))
    if not dates:
        for m in _EXPIRATION_RE.finditer(text):
            d = _normalise_date(m.group(1))
            if d:
                dates.append(ExpDate(d, source, clean_text(m.group(0))))

    notes = []
    for m in _MULTI_YEAR_RE.finditer(text):
        if int(m.group(1)) > 1:
            notes.append(f"{source}: \"{clean_text(m.group(0))}\". The term shown may be "
                         "year 1 only; check which date the portal should get.")
    return dates, notes


def clean_text_keep_lines(s: str) -> str:
    return "\n".join(clean_text(l) for l in s.splitlines())


def pick_expiration_date(results: Iterable[ParseResult]) -> ExpDateResult:
    out = ExpDateResult()
    for r in results:
        out.candidates.extend(r.exp_dates)
        out.notes.extend(r.multi_year_notes)
    distinct = sorted({c.date for c in out.candidates})
    if len(distinct) == 1:
        out.date = distinct[0]
    elif len(distinct) > 1:
        out.notes.insert(0, "The PDFs give different dates: " + "; ".join(
            f"{c.date} ({c.source}: {c.found_as})" for c in out.candidates))
    return out


def extract_pdf_courses(path: str | Path) -> ParseResult:
    """Read a proposal PDF and pull out the courses it licenses."""
    try:
        import pdfplumber  # imported lazily so the rest of the app runs without it
    except ImportError as e:  # pragma: no cover - environment dependent
        raise RuntimeError("pdfplumber is not installed (pip install pdfplumber).") from e

    path = Path(path)
    pages: list[list[dict]] = []
    texts: list[str] = []
    tables: list[list[list[str]]] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_words())
            texts.append(page.extract_text() or "")
            for table in page.extract_tables():
                tables.append([[clean_text(cell or "") for cell in row] for row in table])
    result = extract_courses_from_words(pages, path.name)
    add_sku_table_courses(result, tables, path.name)
    result.exp_dates, result.multi_year_notes = find_expiration_dates("\n".join(texts), path.name)
    result.source = path.name
    result.text = clean_text_keep_lines("\n".join(texts))
    result.tables = tables
    return result


# A pricing-table SKU cell that is only a course licence: "CL-11570", "CL-11569P".
_SKU_CELL_RE = re.compile(rf"^CL-({_NUM})$", re.IGNORECASE)


def add_sku_table_courses(result: ParseResult, tables: Iterable[list[list[str]]],
                          source: str) -> None:
    """Courses named only by a "CL-<number>" SKU cell in a pricing table.

    The description next to it is free text, so the word reader can't pick the
    number out of it; the SKU cell itself is unambiguous.
    """
    found = {c.number for c in result.courses}
    for table in tables:
        header = [clean_text(h) for h in (table[0] if table else [])]
        sku_col = next((i for i, h in enumerate(header) if re.fullmatch(r"SKU", h, re.I)), None)
        if sku_col is None:
            continue
        desc_col = next((i for i, h in enumerate(header) if re.search(r"Description", h, re.I)), None)
        for row in table[1:]:
            m = _SKU_CELL_RE.match(clean_text(row[sku_col]) if sku_col < len(row) else "")
            if not m:
                continue
            number = m.group(1).upper()
            if number in found:
                continue
            desc = clean_text(row[desc_col]) if desc_col is not None and desc_col < len(row) else ""
            found.add(number)
            result.courses.append(Course(number=number, name=f"{desc or 'SKU CL-' + number} {number}",
                                         note="from SKU", source=source))
    result.courses = _sort_by_name(result.courses)
    # The free-text description usually lands in review; drop it once its
    # numbers are all accounted for.
    kept = []
    for r in result.review:
        nums = {n.upper() for n in _ANY_NUM_RE.findall(r.text) if not is_year_like(n)}
        if nums and nums <= found:
            continue
        kept.append(r)
    result.review = kept


def merge_results(results: Iterable[ParseResult]) -> ParseResult:
    """Combine several PDFs (first occurrence of a course number wins)."""
    merged = ParseResult()
    seen: set[str] = set()
    for r in results:
        for c in r.courses:
            if c.number not in seen:
                seen.add(c.number)
                merged.courses.append(c)
        merged.review.extend(r.review)
        merged.exp_dates.extend(r.exp_dates)
        merged.multi_year_notes.extend(r.multi_year_notes)
    merged.courses = _sort_by_name(merged.courses)
    return merged


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def name_key(name: str) -> str:
    """Course name without its number or punctuation, for comparing names."""
    name = re.sub(rf"\b{_NUM}\b:?", " ", name)
    name = re.sub(r"\((?:BEC)\)", " ", name)
    name = name.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()


def compare(pdf_courses: Iterable[Course], portal_courses: Iterable[Course],
            review: Iterable[ReviewItem] = (), keep: Iterable[str] = ()) -> Comparison:
    """``keep``: numbers of portal-only courses to archive and reset instead of
    archive and remove (chosen by hand when the PDF only lists what's changing)."""
    keep = set(keep)
    pdf_by = {c.number: c for c in pdf_courses}
    portal_by = {c.number: c for c in portal_courses}
    review = list(review)

    to_add, remove, reset = [], [], []

    for num, c in pdf_by.items():
        if num in portal_by:
            portal_c = portal_by[num]
            if portal_c.license_count == DEMO_LICENSE_COUNT:
                # Demo license on the portal -> being purchased now = a content add.
                to_add.append(replace(c, note=", ".join(
                    x for x in (c.note, f"demo license ({DEMO_LICENSE_COUNT}) on portal") if x)))
            else:
                reset.append(portal_c)
        elif is_year_like(num):
            review.append(ReviewItem(c.name, c.source,
                f"{num} looks like a year. Confirm it's really a course number."))
        else:
            to_add.append(c)

    for num, c in portal_by.items():
        if num in pdf_by:
            continue
        (reset if num in keep else remove).append(c)

    replacements = []
    for a in to_add:
        ak = name_key(a.name)
        best, best_ratio = None, 0.0
        for r in remove:
            if base_number(r.number) == base_number(a.number):
                continue  # a different variant of the same course, not a renumber
            ratio = difflib.SequenceMatcher(None, ak, name_key(r.name)).ratio()
            if ratio > best_ratio:
                best, best_ratio = r, ratio
        if best is not None and best_ratio >= 0.85:
            replacements.append((a, best))

    return Comparison(
        to_add=_sort_by_name(to_add),
        archive_remove=_sort_by_name(remove),
        archive_reset=_sort_by_name(reset),
        review=review,
        possible_replacements=replacements,
    )
