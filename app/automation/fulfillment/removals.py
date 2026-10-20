"""
Fulfillment: decide which portal-only courses are archived and removed.

A course on the portal but not on the PDF is normally archived and removed.
That only holds when the PDF lists every course the client is renewing. Some
proposals only list what's changing ("TravelBrands will upgrade from the 2021
OWASP bundle to the new OWASP Top 10 11570 course"), and everything else on
the portal simply carries on. So the portal-only courses are offered as a
checklist, and this module works out:

* the PDF text that says what's being removed or replaced (SKU descriptions
  that mention upgrading / replacing, and the Course Archive table's rows),
* whether the PDF looks like a full course list or a change order, which sets
  the default ticks (full list: all ticked; change order: only courses whose
  numbers the archive text names),
* which unticked courses match words in that text, so they can be ticked in
  one go after a look.

Nothing is removed without the user seeing the list: Course Archive can't be
reversed, so the matching only suggests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from . import course_compare as cc
from . import order_check as oc

UPGRADE = "upgrade"            # a SKU description that mentions upgrading/replacing
ARCHIVE_REMOVE = "archive_remove"
ARCHIVE_RESET = "archive_reset"
ARCHIVE_OTHER = "archive_other"  # "Remove Assessment", "Requested Date of Archive", ...

_CHANGE_WORDS_RE = re.compile(
    r"\b(?:upgrad\w*|updat\w*|replac\w*|switch\w*|migrat\w*|swap\w*|transition\w*|"
    r"in\s+place\s+of|instead\s+of)\b", re.I)
_ARCHIVE_REMOVE_RE = re.compile(r"^Archive\s*(?:and|&)\s*Remove\b", re.I)
_ARCHIVE_RESET_RE = re.compile(r"^Archive\s*(?:and|&)\s*Reset\b", re.I)
# "... upgrade from the 2021 OWASP bundle to the new ..." -> "the 2021 OWASP bundle"
_FROM_CLAUSE_RE = re.compile(r"\b(?:from|replac\w*|instead\s+of)\s+(.+?)(?:\s+(?:to|with|into|by)\b|[.;]|$)", re.I)
# Archive text that names nothing specific.
_GENERIC_ARCHIVE_RE = re.compile(
    r"^(?:n/?a|none|tbd|-+)?$|\ball\s+(?:courses?|courseware|content|items?)\b", re.I)

# Words too common in course names (or in the PDF's phrasing) to mean anything
# when matching archive text against the portal list.
_STOPWORDS = frozenset("""
    a an and the of for to from with in on at by or all any other our your their
    will be is are was this that these those new old current existing previous
    course courses courseware module modules training trainings bundle bundles
    package packages program programs content item items essentials essential
    basics basic principle principles series collection version versions
    renewal renew renewed renewing licensing license upgrade upgrading upgraded
    update updated updating replace replaced replacing remove removed archive
    archived reset not none listed above below term year years client
""".split())


@dataclass(frozen=True)
class ChangeNote:
    kind: str      # UPGRADE / ARCHIVE_REMOVE / ARCHIVE_RESET / ARCHIVE_OTHER
    label: str     # SKU, or the archive table's row label
    text: str
    source: str


@dataclass
class RemovalPlan:
    candidates: list[cc.Course] = field(default_factory=list)  # portal-only, by name
    ticked: set[str] = field(default_factory=set)       # default: archive and remove
    suggested: dict[str, str] = field(default_factory=dict)  # number -> why it matches
    partial: bool = False                                # looks like a change order
    reasons: list[str] = field(default_factory=list)     # why it looks that way
    warnings: list[str] = field(default_factory=list)    # archive text vs the lists


def change_notes(pdfs: Iterable[cc.ParseResult]) -> list[ChangeNote]:
    """SKU descriptions that mention a change, and the Course Archive rows, from every PDF."""
    out: list[ChangeNote] = []
    seen: set[tuple[str, str]] = set()

    def add(note: ChangeNote):
        key = (note.kind, cc.clean_text(note.text).casefold())
        if key not in seen:
            seen.add(key)
            out.append(note)

    for p in pdfs:
        for s in oc.sku_lines(p.tables, p.source):
            if _CHANGE_WORDS_RE.search(s.description):
                add(ChangeNote(UPGRADE, s.sku, s.description, p.source))
        for label, value in oc.archive_rows(p.tables):
            label, value = cc.clean_text(label), cc.clean_text(value)
            kind = (ARCHIVE_REMOVE if _ARCHIVE_REMOVE_RE.match(label)
                    else ARCHIVE_RESET if _ARCHIVE_RESET_RE.match(label) else ARCHIVE_OTHER)
            add(ChangeNote(kind, label, value, p.source))
    return out


def _keywords(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9+#]*|\d{4,}", text)
    return {w.casefold() for w in words
            if w.casefold() not in _STOPWORDS and (len(w) >= 3 or w.isupper())
            and not cc.is_year_like(w)}


def _match_text(notes: list[ChangeNote]) -> list[str]:
    """The parts of the notes that describe what's going away."""
    parts = []
    for n in notes:
        if n.kind == ARCHIVE_REMOVE and not _GENERIC_ARCHIVE_RE.search(n.text):
            parts.append(n.text)
        elif n.kind == UPGRADE:
            parts.extend(m.group(1) for m in _FROM_CLAUSE_RE.finditer(n.text))
    return parts


def plan_removals(pdf_courses: Iterable[cc.Course], portal_courses: Iterable[cc.Course],
                  notes: list[ChangeNote]) -> RemovalPlan:
    pdf_numbers = {c.number for c in pdf_courses}
    portal = list(portal_courses)
    plan = RemovalPlan(candidates=sorted(
        (c for c in portal if c.number not in pdf_numbers),
        key=lambda c: (c.name.casefold(), c.number)))
    candidate_numbers = {c.number for c in plan.candidates}

    # -- course numbers written in the archive-and-remove text -------------
    remove_text = " ".join(n.text for n in notes if n.kind == ARCHIVE_REMOVE)
    named = {m.upper() for m in re.findall(rf"(?<![\w/.$-])({cc._NUM})\b", remove_text)
             if not cc.is_year_like(m)}
    for num in sorted(named):
        if num in candidate_numbers:
            plan.suggested[num] = "number listed under Archive and Remove"
        elif num in pdf_numbers:
            plan.warnings.append(f"{num} is listed under Archive and Remove but is also on "
                                 "the PDF course list, so it's being kept.")
        else:
            plan.warnings.append(f"{num} is listed under Archive and Remove but isn't on the "
                                 "portal list.")

    # -- words from the change text ---------------------------------------
    words = set().union(*(_keywords(t) for t in _match_text(notes))) if notes else set()
    if words:
        for c in plan.candidates:
            if c.number in plan.suggested:
                continue
            hits = sorted(w for w in words
                          if re.search(rf"(?<![A-Za-z0-9]){re.escape(w)}(?![A-Za-z0-9])", c.name, re.I))
            if hits:
                plan.suggested[c.number] = "name matches " + ", ".join(f'"{h}"' for h in hits)

    # -- full list or change order? ----------------------------------------
    upgrades = [n for n in notes if n.kind == UPGRADE]
    if upgrades:
        plan.reasons.append(f"{upgrades[0].label} mentions an upgrade or replacement.")
    if portal and len(pdf_numbers & {c.number for c in portal}) < len(portal) / 2:
        plan.reasons.append(f"Only {len(pdf_numbers & {c.number for c in portal})} of the "
                            f"{len(portal)} portal courses are on the PDF.")
    plan.partial = bool(plan.reasons)

    plan.ticked = ({n for n in plan.suggested if n in named} if plan.partial
                   else set(candidate_numbers))
    if not plan.partial and named and candidate_numbers - named:
        plan.warnings.append(
            "Archive and Remove names specific courses, but "
            f"{len(candidate_numbers - named)} other portal course(s) aren't on the PDF either. "
            "Check whether they should be kept.")
    return plan
