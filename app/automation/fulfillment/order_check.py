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
    ("portal_url", "Portal URL", r"\burl\b|^od$"),   # "OD: princeton" = the subdomain
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
# Estimate header "SALES REP  OD POC  OD URL" and the line under it:
# "Natalie Williams callen@princeton.com Princeton.glsondemand.com". OD URL may be
# missing, a bare subdomain ("Vibrenthealth") or "NA SCORM".
_ESTIMATE_CONTACT_RE = re.compile(
    r"SALES REP\s+OD POC(?:\s+OD URL)?[ \t]*\n[^\n@]*?(\S+@\S+)[ \t]*([^\n]*)")

PORTAL_DOMAIN = "glsondemand.com"

# "CL-SST-ESS", "DL-LMS1", "CL-9282-CG"; may follow a colon with no space
# ("Related Services:DL-LMS1").
_SKU_RE = re.compile(r"(?<![\w-])([A-Z]{2,}(?:-[A-Z0-9]+)+)(?![\w-])")
_SKU_HEADER_RE = re.compile(r"^(?:SKU|ACTIVITY)$", re.I)          # SOW / estimate
_USERS_HEADER_RE = re.compile(r"\busers\b|\blicense\s+count\b", re.I)
_DESCRIPTION_HEADER_RE = re.compile(r"^description$", re.I)

# Content-license SKUs carry the user count; delivery (DL-) and service lines
# don't. Based on the 16842 example; widen if other license SKUs show up.
LICENSE_SKU_PREFIXES = ("CL-",)

RENEWAL, NEW_ACCOUNT = "Renewal", "New Account"
# SOW title: "2026 RENEWAL PROPOSAL", "YEAR 2/3 RENEWAL PROPOSAL", "RENEWAL INVOICE".
_RENEWAL_TITLE_RE = re.compile(r"\bRENEWAL\s+(?:PROPOSAL|INVOICE)\b")
# Estimate note "New Client", or the new-client Organization Information form in the SOW.
_NEW_ACCOUNT_RE = re.compile(r"\bNew Client\b|\bORGANIZATION INFORMATION\b")

_COMMS_TOOLBOX_RE = re.compile(r"\bCommunications?\s+Tool(?:box|kit)\b", re.I)

GLS_HOSTED, SELF_HOSTED = "GLS-Hosted", "Self-Hosted"
HOSTING_SKU_PREFIXES = ("DL-LMS",)   # OnDemand hosting
SCORM_SKU_PREFIXES = ("DL-SCR",)     # SCORM delivery = the client hosts it

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
            cell = re.sub(r"-\s+(?=[A-Z0-9])", "-", _cell(row, sku_col))   # "PH-SAAS- PB"
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


# First row of the Course Archive table: "Archive and Reset", "Archive & Remove", ...
_ARCHIVE_TABLE_RE = re.compile(r"^Archive\s*(?:and|&)\s*(?:Reset|Remove)\b", re.I)


def archive_rows(tables: Iterable[list[list[str]]]) -> list[tuple[str, str]]:
    """The SOW's Course Archive table (Archive and Reset / Archive and Remove /
    ... / Requested Date). Either archive row can come first, or be the only one."""
    for table in tables:
        if table and table[0] and _ARCHIVE_TABLE_RE.match(cc.clean_text(table[0][0])):
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
    # Read once here so the task list doesn't re-derive them.
    email: OrderEmail = field(default_factory=OrderEmail)
    order_number: str = ""
    order_type: str = ""                 # RENEWAL / NEW_ACCOUNT / "" (not detected)
    sow_users: Optional[int] = None      # license count on the SOW's first CL- line
    portal_users: Optional[int] = None   # license count on the pasted portal list
    company: str = ""                    # email Full Company Name, else SOW Prepared for
    portal_url: str = ""                 # normalised: email URL/OD, else estimate OD URL
    hosting: str = ""                    # GLS_HOSTED / SELF_HOSTED / "" (not detected)
    poc: str = ""                        # email OD Admin POC, else estimate OD POC
    sources: list[str] = field(default_factory=list)   # PDF names, attached to tickets
    estimate_sources: list[str] = field(default_factory=list)  # SKUs here were actually bought
    has_comms_toolbox: bool = False      # the SOW lists the Communications Toolbox


def check_order(email_text: str, pdfs: Iterable[cc.ParseResult],
                portal: Optional[cc.ParseResult] = None) -> OrderCheck:
    pdfs = list(pdfs)
    email = parse_order_email(email_text)
    out = OrderCheck(email=email, sources=[p.source for p in pdfs if p.source])

    by_kind: dict[str, list[cc.ParseResult]] = {SOW: [], ESTIMATE: [], UNKNOWN: []}
    for p in pdfs:
        by_kind[document_kind(p.text)].append(p)
    out.estimate_sources = [p.source for p in by_kind[ESTIMATE]]
    out.has_comms_toolbox = any(_COMMS_TOOLBOX_RE.search(p.text) for p in by_kind[SOW])

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
        out.order_number = out.order_number or m.group(1)
        out.facts.append(Fact("Order number", m.group(1), p.source))
    for kind, pattern in ((RENEWAL, _RENEWAL_TITLE_RE), (NEW_ACCOUNT, _NEW_ACCOUNT_RE)):
        src = next((p.source for p in by_kind[SOW] + by_kind[ESTIMATE] if pattern.search(p.text)), None)
        if src and not out.order_type:
            out.order_type = kind
            out.facts.append(Fact("Order type", kind, src))
    for key, label in _EMAIL_LABELS.items():
        if email.fields.get(key):
            out.facts.append(Fact(label, email.fields[key], "Order email"))
    for label, value in email.other.items():
        out.facts.append(Fact(label, value, "Order email"))
    prepared_for = [(m.group(1).strip(), p.source) for p in by_kind[SOW]
                    for m in [_PREPARED_FOR_RE.search(p.text)] if m]
    out.facts.extend(Fact("Prepared for", v, src) for v, src in prepared_for)
    contacts = [(m.group(1), m.group(2).strip(), p.source) for p in by_kind[ESTIMATE]
                for m in [_ESTIMATE_CONTACT_RE.search(p.text)] if m]
    for poc, od_url, src in contacts:
        out.facts.append(Fact("OD POC", poc, src))
        if od_url:
            out.facts.append(Fact("OD URL", od_url, src))
    email_url = portal_url(email.fields.get("portal_url", ""))
    estimate_urls = [(portal_url(u), u, src) for _, u, src in contacts]
    out.portal_url = email_url or next((u for u, _, _ in estimate_urls if u), "")
    out.company = email.fields.get("company", "") or next((v for v, _ in prepared_for), "")
    out.poc = email.fields.get("training_poc", "") or next((poc for poc, _, _ in contacts), "")

    for p in by_kind[SOW] + by_kind[ESTIMATE]:
        out.skus.extend(sku_lines(p.tables, p.source))
    for p in by_kind[SOW]:
        out.archive.extend(archive_rows(p.tables))
    out.hosting = _hosting(out.skus, out.portal_url, [u for _, u, _ in contacts])
    if out.hosting:
        out.facts.append(Fact("Hosting", out.hosting, "SKUs / OD URL"))

    # -- expiration date: the email's License Dates join the PDFs' terms --
    email_dates, _ = cc.find_expiration_dates(email_text, "Order email")
    out.exp = cc.pick_expiration_date(pdfs + [cc.ParseResult(exp_dates=email_dates)])
    out.warnings.extend(out.exp.notes)

    # -- cross-checks ----------------------------------------------------
    counts = _license_counts(out.skus, email, portal)
    if len({n for n, _ in counts}) > 1:
        out.warnings.append("License counts don't match: "
                            + "; ".join(f"{n} ({src})" for n, src in counts) + ".")
    sow_sources = {p.source for p in by_kind[SOW]}
    out.sow_users = next((n for n, src in counts if src.split(", ", 1)[-1] in sow_sources), None)
    out.portal_users = next((n for n, src in counts if src == "portal"), None)
    out.warnings.extend(_sku_set_warnings(out.skus, by_kind))
    for est_url, raw, src in estimate_urls:
        if email_url and est_url and email_url != est_url:
            out.warnings.append(f"Portal URL differs: {email_url} (Order email) vs {raw} ({src}).")
    company = email.fields.get("company", "")
    for name, src in prepared_for:
        if company and not _names_overlap(company, name):
            out.warnings.append(f"Company name differs: {company} (Order email) vs {name} ({src}).")
    return out


def _license_counts(skus: list[SkuLine], email: OrderEmail,
                    portal: Optional[cc.ParseResult]) -> list[tuple[int, str]]:
    """(count, where it came from) for every source that states a license count.
    Per PDF only the first CL- line counts: it's the main package, and later CL-
    lines are add-on bundles with their own counts (16845: 230, then 20, 30, 125)."""
    counts: list[tuple[int, str]] = []
    seen: set[str] = set()
    for s in skus:
        if s.users is not None and s.sku.startswith(LICENSE_SKU_PREFIXES) and s.source not in seen:
            seen.add(s.source)
            counts.append((s.users, f"{s.sku}, {s.source}"))
    if portal is not None:
        portal_counts = Counter(c.license_count for c in portal.courses
                                if c.license_count not in (None, cc.DEMO_LICENSE_COUNT))
        if portal_counts:
            counts.append((portal_counts.most_common(1)[0][0], "portal"))
    m = _OPP_USERS_RE.search(email.fields.get("opp_name", ""))
    if m:
        counts.append((int(m.group(1)), "opp name"))
    return counts


def _sku_set_warnings(skus: list[SkuLine], by_kind: dict[str, list[cc.ParseResult]]) -> list[str]:
    if not by_kind[SOW] or not by_kind[ESTIMATE]:
        return []
    sow_sources = {p.source for p in by_kind[SOW]}
    on_sow = {s.sku for s in skus if s.source in sow_sources}
    on_est = {s.sku for s in skus if s.source not in sow_sources}
    # Same SKU spelt differently ("PH-SAAS-PB" / "PH-SAAS1-PB", "CL-SST-ELT2" /
    # "CL-SST-ELT-2") isn't a difference worth a warning.
    sow_keys, est_keys = {_sku_key(x) for x in on_sow}, {_sku_key(x) for x in on_est}
    on_sow = {x for x in on_sow if _sku_key(x) not in est_keys}
    on_est = {x for x in on_est if _sku_key(x) not in sow_keys}
    if not on_sow and not on_est:
        return []
    parts = []
    if on_sow - on_est:
        parts.append("only on SOW: " + ", ".join(sorted(on_sow - on_est)))
    if on_est - on_sow:
        parts.append("only on estimate: " + ", ".join(sorted(on_est - on_sow)))
    return ["SKUs differ between SOW and estimate (" + "; ".join(parts) + ")."]


def _sku_key(sku: str) -> str:
    return re.sub(r"[^A-Z]", "", sku)


def portal_url(value: str) -> str:
    """'Princeton.glsondemand.com', 'https://princeton.glsondemand.com/', or a bare
    subdomain ('princeton', 'Vibrenthealth') -> 'princeton.glsondemand.com'.
    '' for anything that isn't a portal ('NA SCORM', 'N/A', blank)."""
    v = re.sub(r"^https?://", "", cc.clean_text(value).casefold()).strip("/ ")
    if not v or re.search(r"\bn/?a\b|\bscorm\b|\s", v):
        return ""
    return v if "." in v else f"{v}.{PORTAL_DOMAIN}"


def _hosting(skus: list[SkuLine], url: str, od_urls: list[str]) -> str:
    if url or any(s.sku.startswith(HOSTING_SKU_PREFIXES) for s in skus):
        return GLS_HOSTED
    if (any(s.sku.startswith(SCORM_SKU_PREFIXES) for s in skus)
            or any(re.search(r"\bscorm\b", u, re.I) for u in od_urls)):
        return SELF_HOSTED
    return ""


def _names_overlap(a: str, b: str) -> bool:
    """'Princeton Consultants (CPH)' and 'Princeton Consultants' are the same company."""
    ka = re.sub(r"[^a-z0-9]+", " ", a.casefold()).strip()
    kb = re.sub(r"[^a-z0-9]+", " ", b.casefold()).strip()
    return bool(ka and kb) and (ka in kb or kb in ka)
