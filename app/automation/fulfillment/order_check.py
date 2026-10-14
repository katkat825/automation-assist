"""
Fulfillment: check one order's inputs against each other.

Input
-----
* The Order Info email, pasted as-is ("Label: value" lines, any order).
* The PDFs already read by course_compare.extract_pdf_courses — the PandaDoc
  SOW and the QuickBooks estimate. Which is which is decided from the content,
  not the file name.
* The parsed portal Content Licensing list (for its license count), if pasted.

Output
------
The order's key facts, each with where it came from, plus a warning wherever
two sources disagree or something expected is missing. Nothing here decides
what to do about a warning; it only surfaces it.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional

from . import course_compare as cc

# ---------------------------------------------------------------------------
# Order Info email
# ---------------------------------------------------------------------------

# (key, display label, pattern matched against the email's own label).
# Matched by keyword so field order and small wording changes don't matter.
# First match wins, so order matters where labels share words.
EMAIL_FIELDS = (
    ("portal_url", "Portal URL", r"\burl\b"),
    ("opp_name", "Opp name", r"\bopp\b"),
    ("company", "Company", r"\bcompany\b"),
    ("billing_poc", "Billing POC", r"\bbilling\b"),
    ("training_poc", "Training POC", r"\b(?:admin|training)\s+poc\b"),
    ("license_dates", "License dates", r"\blicense\s+dates?\b"),
)
_EMAIL_LABELS = {key: label for key, label, _ in EMAIL_FIELDS}

# "Opp Name: Princeton Consultants: (CPH): ..." -> label is up to the FIRST colon.
_LABEL_LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 +/&()'-]{0,40}?)\s*:\s*(.*)$")


@dataclass
class OrderEmail:
    fields: dict[str, str] = field(default_factory=dict)  # key from EMAIL_FIELDS -> value
    other: dict[str, str] = field(default_factory=dict)   # unrecognised label -> value


def parse_order_email(text: str) -> OrderEmail:
    """Read "Label: value" lines. A line without a label continues the previous value."""
    email = OrderEmail()
    current: Optional[tuple[dict, str]] = None
    for raw in text.splitlines():
        line = cc.clean_text(raw)
        if not line:
            continue
        m = _LABEL_LINE_RE.match(line)
        if m and not m.group(2).startswith("//"):   # "https://..." is a value, not a label
            label, value = m.group(1).strip(), m.group(2).strip()
            key = next((k for k, _, pat in EMAIL_FIELDS if re.search(pat, label, re.I)), None)
            if key and key not in email.fields:
                email.fields[key] = value
                current = (email.fields, key)
            else:
                email.other[label] = value
                current = (email.other, label)
        elif current:
            store, key = current
            store[key] = f"{store[key]}\n{line}".strip()
    return email


# ---------------------------------------------------------------------------
# PDFs
# ---------------------------------------------------------------------------

SOW, ESTIMATE, UNKNOWN = "SOW", "Estimate", "Unknown"

_ESTIMATE_RE = re.compile(r"\bEstimate\s+(\d{3,})\b")           # QuickBooks header
_PANDADOC_RE = re.compile(r"\bDocument Ref:|\bPrepared for:")    # PandaDoc page stamp / cover
_PREPARED_FOR_RE = re.compile(r"Prepared for:\s*\n?\s*(.+)")
_PORTAL_URL_RE = re.compile(r"\b([\w-]+\.glsondemand\.com)\b", re.I)

# "CL-SST-ESS", "DL-LMS1", "CL-9282-CG"; may follow a colon with no space
# ("Related Services:DL-LMS1").
_SKU_RE = re.compile(r"(?<![\w-])([A-Z]{2,}(?:-[A-Z0-9]+)+)(?![\w-])")
_SKU_HEADER_RE = re.compile(r"^(?:SKU|ACTIVITY)$", re.I)          # SOW / estimate
_USERS_HEADER_RE = re.compile(r"\busers\b|\blicense\s+count\b", re.I)
_DESCRIPTION_HEADER_RE = re.compile(r"^description$", re.I)

# Content-license SKUs carry the user count; delivery (DL-) and service lines
# don't. Based on the 16842 example; widen if other license SKUs show up.
LICENSE_SKU_PREFIXES = ("CL-",)

# "Ess Bdl_76 users_2026": underscores are word characters, so no \b here.
_OPP_USERS_RE = re.compile(r"(?<!\d)(\d+)\s*users?(?![a-z])", re.I)


@dataclass(frozen=True)
class SkuLine:
    sku: str
    description: str
    users: Optional[int]
    source: str


def document_kind(text: str) -> str:
    if _PANDADOC_RE.search(text):
        return SOW
    if _ESTIMATE_RE.search(text):
        return ESTIMATE
    return UNKNOWN


def sku_lines(tables: Iterable[list[list[str]]], source: str) -> list[SkuLine]:
    """SKU rows from any table whose header has a SKU (or ACTIVITY) column."""
    out = []
    for table in tables:
        if not table:
            continue
        header = [cc.clean_text(h) for h in table[0]]
        sku_col = _column(header, _SKU_HEADER_RE)
        if sku_col is None:
            continue
        users_col = _column(header, _USERS_HEADER_RE)
        desc_col = _column(header, _DESCRIPTION_HEADER_RE)
        for row in table[1:]:
            cell = _cell(row, sku_col)
            m = _SKU_RE.search(cell)
            if not m:
                continue
            desc = " ".join(x for x in (cell.replace(m.group(1), " "), _cell(row, desc_col)) if x)
            users = _cell(row, users_col)
            out.append(SkuLine(
                sku=m.group(1),
                description=cc.clean_text(desc).strip(" :"),
                users=int(users) if users.isdigit() else None,
                source=source,
            ))
    return out


def archive_rows(tables: Iterable[list[list[str]]]) -> list[tuple[str, str]]:
    """The SOW's Course Archive table (Archive and Reset / ... / Requested Date)."""
    for table in tables:
        if table and table[0] and table[0][0].startswith("Archive and Reset"):
            return [(r[0], r[1]) for r in table if len(r) >= 2 and r[0]]
    return []


def _column(header: list[str], pattern: re.Pattern) -> Optional[int]:
    return next((i for i, h in enumerate(header) if pattern.search(h)), None)


def _cell(row: list[str], col: Optional[int]) -> str:
    return cc.clean_text(row[col]) if col is not None and col < len(row) else ""


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Fact:
    label: str
    value: str
    source: str


@dataclass
class OrderCheck:
    facts: list[Fact] = field(default_factory=list)
    skus: list[SkuLine] = field(default_factory=list)
    archive: list[tuple[str, str]] = field(default_factory=list)
    exp: cc.ExpDateResult = field(default_factory=cc.ExpDateResult)
    warnings: list[str] = field(default_factory=list)


def check_order(email_text: str, pdfs: Iterable[cc.ParseResult],
                portal: Optional[cc.ParseResult] = None) -> OrderCheck:
    pdfs = list(pdfs)
    out = OrderCheck()
    email = parse_order_email(email_text)

    by_kind: dict[str, list[cc.ParseResult]] = {SOW: [], ESTIMATE: [], UNKNOWN: []}
    for p in pdfs:
        by_kind[document_kind(p.text)].append(p)

    # -- what's missing --------------------------------------------------
    if not email_text.strip():
        out.warnings.append("No Order Info email pasted.")
    else:
        missing = [label for key, label in _EMAIL_LABELS.items() if not email.fields.get(key)]
        if missing:
            out.warnings.append("Order email is missing: " + ", ".join(missing) + ".")
    if not by_kind[SOW]:
        out.warnings.append("No PandaDoc SOW among the PDFs.")
    if not by_kind[ESTIMATE]:
        out.warnings.append("No QuickBooks estimate among the PDFs.")
    for p in by_kind[UNKNOWN]:
        out.warnings.append(f"{p.source}: couldn't tell whether this is the SOW or the estimate. "
                            "It was still read for courses and dates.")

    # -- facts -----------------------------------------------------------
    for p in by_kind[ESTIMATE]:
        m = _ESTIMATE_RE.search(p.text)
        out.facts.append(Fact("Order number", m.group(1), p.source))
    for key, label in _EMAIL_LABELS.items():
        if email.fields.get(key):
            out.facts.append(Fact(label, email.fields[key], "Order email"))
    for label, value in email.other.items():
        out.facts.append(Fact(label, value, "Order email"))
    prepared_for = [(m.group(1).strip(), p.source) for p in by_kind[SOW]
                    for m in [_PREPARED_FOR_RE.search(p.text)] if m]
    out.facts.extend(Fact("Prepared for", v, src) for v, src in prepared_for)
    estimate_urls = [(m.group(1), p.source) for p in by_kind[ESTIMATE]
                     for m in [_PORTAL_URL_RE.search(p.text)] if m]
    out.facts.extend(Fact("Portal URL", v, src) for v, src in estimate_urls)

    for p in by_kind[SOW] + by_kind[ESTIMATE]:
        out.skus.extend(sku_lines(p.tables, p.source))
    for p in by_kind[SOW]:
        out.archive.extend(archive_rows(p.tables))

    # -- expiration date: the email's License Dates join the PDFs' terms --
    email_dates, _ = cc.find_expiration_dates(email_text, "Order email")
    out.exp = cc.pick_expiration_date(pdfs + [cc.ParseResult(exp_dates=email_dates)])
    out.warnings.extend(out.exp.notes)

    # -- cross-checks ----------------------------------------------------
    out.warnings.extend(_license_count_warnings(out.skus, email, portal))
    out.warnings.extend(_sku_set_warnings(out.skus, by_kind))
    url = email.fields.get("portal_url", "")
    for est_url, src in estimate_urls:
        if url and _url_key(url) != _url_key(est_url):
            out.warnings.append(f"Portal URL differs: {url} (Order email) vs {est_url} ({src}).")
    company = email.fields.get("company", "")
    for name, src in prepared_for:
        if company and not _names_overlap(company, name):
            out.warnings.append(f"Company name differs: {company} (Order email) vs {name} ({src}).")
    return out


def _license_count_warnings(skus: list[SkuLine], email: OrderEmail,
                            portal: Optional[cc.ParseResult]) -> list[str]:
    counts: list[tuple[int, str]] = [
        (s.users, f"{s.sku}, {s.source}") for s in skus
        if s.users is not None and s.sku.startswith(LICENSE_SKU_PREFIXES)
    ]
    if portal is not None:
        portal_counts = Counter(c.license_count for c in portal.courses
                                if c.license_count not in (None, cc.DEMO_LICENSE_COUNT))
        if portal_counts:
            counts.append((portal_counts.most_common(1)[0][0], "portal"))
    m = _OPP_USERS_RE.search(email.fields.get("opp_name", ""))
    if m:
        counts.append((int(m.group(1)), "opp name"))
    if len({n for n, _ in counts}) > 1:
        return ["License counts don't match: " + "; ".join(f"{n} ({src})" for n, src in counts) + "."]
    return []


def _sku_set_warnings(skus: list[SkuLine], by_kind: dict[str, list[cc.ParseResult]]) -> list[str]:
    if not by_kind[SOW] or not by_kind[ESTIMATE]:
        return []
    sow_sources = {p.source for p in by_kind[SOW]}
    on_sow = {s.sku for s in skus if s.source in sow_sources}
    on_est = {s.sku for s in skus if s.source not in sow_sources}
    if on_sow == on_est:
        return []
    parts = []
    if on_sow - on_est:
        parts.append("only on SOW: " + ", ".join(sorted(on_sow - on_est)))
    if on_est - on_sow:
        parts.append("only on estimate: " + ", ".join(sorted(on_est - on_sow)))
    return ["SKUs differ between SOW and estimate (" + "; ".join(parts) + ")."]


def _url_key(url: str) -> str:
    return re.sub(r"^https?://", "", url.strip().casefold()).rstrip("/")


def _names_overlap(a: str, b: str) -> bool:
    """'Princeton Consultants (CPH)' and 'Princeton Consultants' are the same company."""
    ka = re.sub(r"[^a-z0-9]+", " ", a.casefold()).strip()
    kb = re.sub(r"[^a-z0-9]+", " ", b.casefold()).strip()
    return bool(ka and kb) and (ka in kb or kb in ka)
