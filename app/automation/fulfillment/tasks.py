"""
Fulfillment: the list of work one order needs.

Built from the order check (order_check.check_order) and, once the portal list
is pasted, the course comparison (course_compare.compare). Tasks that become a
BoldDesk ticket carry its text (tickets.py). Who handles what:

    Fulfillment             portal exp date, course adds, demo course
                            removals, phishing (PhishingBox),
                            customer notification (a BoldDesk ticket)
    Support/T1 (BoldDesk)   license count, course archive, SSO (unless it's
                            already in place), SCORM, communication materials
    Customization           customization, incl. PA screens (a BoldDesk ticket)

SKU-driven tasks (SSO, SCORM, customization, phishing) use the estimate's
line when there is one: it's what was bought, and its wording is what goes in
the ticket. A SKU only on the SOW (an optional item) is flagged to confirm.

Change orders: no portal expiration date task, no archive and reset (the
archive ticket only appears when something is archived and removed), no
QuickBooks estimate or order number needed, and the customer notification is
written by hand.

Not generated: portal creation (no example yet).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from . import course_compare as cc
from . import order_check as oc
from . import tickets as tk

# How alike a portal item's name must be to the SOW's "Remove Assessment" entry
# to count as the same item (same scale as course_compare's renumber check).
_ASSESSMENT_MATCH = 0.8

FULFILLMENT = "Fulfillment"
FULFILLMENT_TICKET = "Fulfillment (BoldDesk ticket)"
SUPPORT_TICKET = "Support/T1 (BoldDesk ticket)"
CUSTOMIZATION = "Customization (BoldDesk ticket)"

SSO_SKUS = ("DL-SSO",)
CUSTOMIZATION_SKUS = oc.CUSTOMIZATION_SKU_PREFIXES
PHISHING_SKUS = ("PH-",)
# "PH-SAAS1-PB" / "PH-SAAS-PB": PhishingBox (sold as SecurePhish).
_PHISHINGBOX_RE = re.compile(r"-PB$|\bSecurePhish", re.I)
# "In place." on the SKU line = already set up; "not yet in place" isn't.
_IN_PLACE_RE = re.compile(r"(?<!not\s)(?<!yet\s)\bin\s+place\b", re.I)


@dataclass(frozen=True)
class Task:
    title: str
    handled_by: str
    details: tuple[str, ...] = ()
    confirm: str = ""                  # why this needs a second look before doing it
    ticket: Optional[tk.Ticket] = None


@dataclass
class TaskPlan:
    header: list[str] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    # For the spreadsheet rows (sheet_rows).
    order_ref: str = ""     # order number, "Chubb Policyholder" or "No-cost CO"
    company: str = ""
    sales_rep: str = ""     # first name


def build_plan(check: oc.OrderCheck, comparison: Optional[cc.Comparison]) -> TaskPlan:
    plan = TaskPlan(header=[_order_line(check)], order_ref=_order_ref(check),
                    company=check.company, sales_rep=tk.first_name(check.sales_rep))
    if check.order_type == oc.CHUBB_POLICYHOLDER:
        return _policyholder_plan(check, plan)
    if check.warnings:
        plan.header.append(f"! {len(check.warnings)} warning(s) on the Order tab. Check them before starting.")
    portal_work = check.hosting != oc.SELF_HOSTED and check.order_type != oc.NEW_ACCOUNT
    if comparison is None and portal_work:
        plan.header.append("Course tasks will appear once the SOW courses and portal list are both in.")
    add = plan.tasks.append

    pfw = check.hosting == oc.PHISHFIREWALL_HOSTED
    change = check.order_type == oc.CHANGE_ORDER
    if check.hosting != oc.SELF_HOSTED and not change:   # change orders keep their date
        title = "Update PhishFirewall expiration date" if pfw else "Update portal expiration date"
        where = "PhishFirewall" if pfw else (check.portal_url or "Portal")
        confirm = "" if check.exp.date else "No single date found. See the Order tab."
        if pfw:
            confirm = " ".join(x for x in (_PFW_NO_STANDARD, confirm) if x)
        add(Task(title, FULFILLMENT, (f"{where}: {check.exp.date}",) if check.exp.date else (),
                 confirm=confirm))

    if (check.sow_users is not None and check.portal_users is not None
            and check.sow_users != check.portal_users):
        add(Task("License count change", SUPPORT_TICKET, ticket=tk.license_count(check)))

    if comparison is not None:
        plan.tasks.extend(_course_tasks(check, comparison))

    sku, on_estimate = _sku(check, SSO_SKUS)
    if sku and _in_place(check, SSO_SKUS):
        plan.header.append(f"SSO is already in place ({sku.sku}), so there's no SSO ticket.")
    elif sku:
        add(Task("SSO", SUPPORT_TICKET, confirm=_optional(on_estimate, check),
                 ticket=tk.sso(check, sku)))
    sku, on_estimate = _sku(check, oc.SCORM_SKU_PREFIXES)
    if sku:
        add(Task("SCORM delivery", SUPPORT_TICKET, confirm=_optional(on_estimate, check),
                 ticket=tk.scorm(check, sku)))
    task = _customization_task(check)
    if task:
        add(task)
        # Customization also goes on Monday work management; that part is Fulfillment's.
        add(Task("Add customization to Monday", FULFILLMENT,
                 (_order_line(check),) + tuple(task.ticket.body.split("\n\n\n", 1)[-1].split("\n\n"))))
    plan.tasks.extend(_phishing_tasks(check))

    if check.has_comms_toolbox:
        add(Task("Communication materials", SUPPORT_TICKET, ticket=tk.comms_materials(check)))
    elif check.order_type == oc.NEW_ACCOUNT:
        add(Task("Communication materials", SUPPORT_TICKET, ticket=tk.comms_materials(check),
                 confirm="The SOW doesn't list the Communications Toolbox; new accounts get it by default."))

    if check.order_type == oc.NEW_ACCOUNT:
        add(Task("Implementation", FULFILLMENT, (_order_line(check),)))
    if check.order_type == oc.NEW_ACCOUNT and check.hosting:
        add(Task("Customer notification", FULFILLMENT_TICKET, ticket=tk.welcome(check)))
    elif check.order_type == oc.NEW_ACCOUNT:
        add(Task("Customer notification", FULFILLMENT_TICKET, (_order_line(check),),
                 confirm="Hosting unknown, so no welcome email picked. Choose one in Order details."))
    elif check.order_type == oc.RENEWAL and check.hosting in (oc.GLS_HOSTED, ""):
        add(Task("Customer notification", FULFILLMENT_TICKET,
                 ticket=tk.customer_notification(check)))
    elif change:
        add(Task("Customer notification", FULFILLMENT_TICKET, (_order_line(check),),
                 confirm="Change order: write this notification by hand."))
    elif check.order_type == oc.RENEWAL and pfw:
        add(Task("Customer notification", FULFILLMENT_TICKET, (_order_line(check),),
                 confirm="No renewal wording for PhishFirewall-hosted yet. Write this one by hand."))
    elif not check.order_type and check.hosting != oc.SELF_HOSTED:
        add(Task("Customer notification", FULFILLMENT_TICKET, (_order_line(check),),
                 confirm="Order type unknown. Choose Renewal, New Account or Change Order "
                         "in Order details."))
    return plan


def _policyholder_plan(check: oc.OrderCheck, plan: TaskPlan) -> TaskPlan:
    """Chubb Policyholder: a new portal, then the Chubb welcome."""
    if check.warnings:
        plan.header.append(f"! {len(check.warnings)} warning(s) on the Order tab. Check them before starting.")
    f = check.policyholder
    labels = {k: label for k, label, _ in oc.POLICYHOLDER_FIELDS}
    details = tuple(f"{labels[k]}: {f[k]}" for k, _, _ in oc.POLICYHOLDER_FIELDS if f.get(k))
    plan.tasks.append(Task("Portal creation", FULFILLMENT, details or (_order_line(check),)))
    plan.tasks.append(Task(
        "Customer notification", FULFILLMENT_TICKET, ticket=tk.chubb_notification(check),
        confirm="Link \"Arnie's Calendar\" in the reply"
                + ("" if check.portal_url else ", and fill in the new portal's URL") + "."))
    return plan


def _course_tasks(check: oc.OrderCheck, r: cc.Comparison) -> list[Task]:
    tasks = []
    if r.to_add:
        count, confirm = _add_license_count(check)
        tasks.append(Task("Add courses", FULFILLMENT,
                          (cc.format_numbers(r.to_add), f"License count: {count}")
                          + _course_lines(r.to_add), confirm=confirm))
    if r.demo_remove:
        tasks.append(Task("Remove demo courses", FULFILLMENT,
                          (cc.format_numbers(r.demo_remove),) + _course_lines(r.demo_remove)))

    requested, assessment, other = "", "", []
    for label, value in check.archive:
        if label.startswith(("Archive and", "Archive &")) or _not_applicable(value):
            continue
        if label.startswith("Requested Date"):
            requested = value
        elif label.startswith("Remove Assessment"):
            assessment = value
        else:                                       # "Reset Assessments (cannot be archived)"
            other.append(f"{label}: {value}")

    # A portal item with no course number that IS the assessment the SOW removes.
    portal_assessment = next((i for i in r.review if assessment and i.source == "Portal"
                              and _similar(i.text, assessment)), None)
    review = [i for i in r.review if i is not portal_assessment]

    # Change order: archive and remove only, and only when something is removed.
    change = check.order_type == oc.CHANGE_ORDER
    if (r.archive_reset and not change) or r.archive_remove or assessment:
        also_remove = [portal_assessment.text if portal_assessment else assessment] if assessment else []
        dated = any(ch.isdigit() for ch in requested)
        on_sow = bool(check.archive)     # the SOW has a Course Archive section
        tasks.append(Task(
            "Course archive", SUPPORT_TICKET,
            confirm="" if dated or on_sow else "Requested archive date: not on the SOW.",
            ticket=tk.archive(check, None if change else r.archive_reset,
                              r.archive_remove, also_remove,
                              requested if dated else "", other,
                              date_tbd=on_sow and not dated)))

    if review:
        tasks.append(Task("Place items the comparison couldn't", FULFILLMENT,
                          tuple(f"{i.text} [{i.source}]: {i.reason}" for i in review),
                          confirm="Not in any list above."))
    return tasks


def _sku(check: oc.OrderCheck, prefixes: tuple[str, ...]) -> tuple[Optional[oc.SkuLine], bool]:
    """The SKU line for a family, preferring the estimate's. (line, is it on the estimate)"""
    lines = [s for s in check.skus if s.sku.startswith(prefixes)]
    bought = [s for s in lines if s.source in check.estimate_sources]
    return (bought or lines or [None])[0], bool(bought)


def _in_place(check: oc.OrderCheck, prefixes: tuple[str, ...]) -> bool:
    return any(s.sku.startswith(prefixes) and _IN_PLACE_RE.search(s.description)
               for s in check.skus)


def _phishing_tasks(check: oc.OrderCheck) -> list[Task]:
    """PhishingBox (PH-SAAS-PB) is Fulfillment's, no ticket. PhishFirewall hasn't
    come up yet, so it's flagged rather than guessed at."""
    tasks = []
    sku, on_estimate = _sku(check, PHISHING_SKUS)
    lines = [s for s in check.skus if s.sku.startswith(PHISHING_SKUS)]
    firewall = check.hosting == oc.PHISHFIREWALL_HOSTED or check.has_phishfirewall or any(
        oc.PHISHFIREWALL_RE.search(f"{s.sku} {s.description}") for s in lines)
    if sku and any(_PHISHINGBOX_RE.search(s.sku) or _PHISHINGBOX_RE.search(s.description)
                   for s in lines):
        tasks.append(Task("Phishing: PhishingBox", FULFILLMENT, (f"{sku.sku}: {sku.description}",),
                          confirm=_optional(on_estimate, check)))
    elif sku and not firewall:
        tasks.append(Task("Phishing", FULFILLMENT, (f"{sku.sku}: {sku.description}",),
                          confirm="Phishing SKU that isn't PhishingBox (-PB). Check which product it is."))
    if firewall:
        details = (f"{sku.sku}: {sku.description}",) if sku and not tasks else ()
        tasks.append(Task("Phishing: PhishFirewall", FULFILLMENT, details,
                          confirm=_PFW_NO_STANDARD if check.hosting == oc.PHISHFIREWALL_HOSTED
                          else "First PhishFirewall order. Check who handles it and what it needs."))
    return tasks


_PFW_NO_STANDARD = "PhishFirewall-hosted: no standard for these yet. Check what's needed."


def _add_license_count(check: oc.OrderCheck) -> tuple[str, str]:
    """(count for the added courses, why to double-check it). The main package's
    count; add-on bundles with their own counts are flagged."""
    if check.sow_users is None:
        return "[license count?]", ""
    counts = sorted({s.users for s in check.skus
                     if s.users is not None and s.sku.startswith(oc.LICENSE_SKU_PREFIXES)}, reverse=True)
    if len(counts) > 1:
        return str(check.sow_users), ("License SKUs have different counts ("
                                      + ", ".join(map(str, counts)) + "). Check which count each course gets.")
    return str(check.sow_users), ""


def _optional(on_estimate: bool, check: Optional[oc.OrderCheck] = None) -> str:
    """Change orders have no estimate, so a SOW-only line isn't a sign of anything."""
    if on_estimate or (check is not None and check.order_type == oc.CHANGE_ORDER):
        return ""
    return "On the SOW (optional item?) but not on the estimate."


# "8 hours", "10 hrs", "2.5 hours"
_HOURS_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\b", re.I)
# What Instructional Designers wouldn't know from a course number alone.
_CUSTOM_ITEM_RE = re.compile(
    r"\bPA\s+Screens?\b|\bPolicy\s+Acknowledg(?:e)?ments?\b|\bSplash\s+Screens?\b|"
    r"\bCustom\s+(?:Screens?|Slides?|Branding|Logo|Content)\b", re.I)
_CUSTOM_COURSE_RE = re.compile(rf"(?<![\w/.$-])({cc._NUM})\b")


def _customization_task(check: oc.OrderCheck) -> Optional[Task]:
    """SRV-CUSB SKU line, else any SKU line or PDF line that says "Customization".
    The SOW may or may not say what's wanted or how many hours; it usually gives a
    course number and an item such as "PA Screen" / "Policy Acknowledgement"."""
    sku, on_estimate = _sku(check, CUSTOMIZATION_SKUS)
    if sku is None:
        sku = next((s for s in check.skus if re.search(r"\bCustomi[sz]ation\b", s.description, re.I)),
                   None)
        on_estimate = bool(sku) and sku.source in check.estimate_sources
    if sku is not None:
        lines = [f"{sku.sku}: {sku.description}" if sku.description else sku.sku]
        text = f"{sku.description} " + " ".join(
            t for t, _ in check.customization_text if sku.sku in t or sku.description in t)
        confirm = _optional(on_estimate, check)
    elif check.customization_text:
        lines = [t for t, _ in check.customization_text]
        text = " ".join(lines)
        confirm = "Found from the PDF text, not a SKU line. Check it's a customization order."
    else:
        return None
    courses = sorted({m.upper() for m in _CUSTOM_COURSE_RE.findall(text) if not cc.is_year_like(m)})
    items = list(dict.fromkeys(cc.clean_text(m.group(0)) for m in _CUSTOM_ITEM_RE.finditer(text)))
    hours = _HOURS_RE.search(text)
    return Task("Customization", CUSTOMIZATION, confirm=confirm,
                ticket=tk.customization(check, lines, courses, items,
                                        hours.group(1) if hours else ""))


def _not_applicable(value: str) -> bool:
    return cc.clean_text(value).casefold() in ("", "n/a", "na", "none")


def _course_lines(courses: list[cc.Course]) -> tuple[str, ...]:
    """Names already end in their number, e.g. "Slay The Scam 10781"."""
    return tuple(f"  {c.name}" + (f" ({c.note})" if c.note else "") for c in courses)


def _similar(a: str, b: str) -> bool:
    return difflib.SequenceMatcher(None, cc.name_key(a), cc.name_key(b)).ratio() >= _ASSESSMENT_MATCH


def _order_line(check: oc.OrderCheck) -> str:
    parts = [f"Order {check.order_number}" if check.order_number else "",
             check.company, check.portal_url, check.order_type, check.hosting]
    return " | ".join(p for p in parts if p) or "Order (no details yet)"


def _order_ref(check: oc.OrderCheck) -> str:
    if check.order_type == oc.CHUBB_POLICYHOLDER:
        return "Chubb Policyholder"
    if check.order_number:
        return check.order_number
    return "No-cost CO" if check.order_type == oc.CHANGE_ORDER else ""


# ---------------------------------------------------------------------------
# Spreadsheet rows
# ---------------------------------------------------------------------------

SHEET_COLUMNS = ("Order #", "Company", "Sales Rep", "Item", "Status", "Owner", "Ticket #",
                 "Date Assigned", "Date Completed", "Notes")
ME = "Kathleen"
# Task title -> the spreadsheet's Item. Titles not listed (e.g. "Place items the
# comparison couldn't") aren't rows.
SHEET_ITEMS = {
    "Update portal expiration date": "Portal Exp",
    "Update PhishFirewall expiration date": "Portal Exp",
    "License count change": "License Change",
    "Add courses": "Content Add",
    "Remove demo courses": "Archive",
    "Course archive": "Archive",
    "SSO": "SSO",
    "SCORM delivery": "SCORM",
    "Customization": "Customization",
    "Add customization to Monday": "Customization",
    "Phishing: PhishingBox": "SecurePhish",
    "Phishing": "SecurePhish",
    "Phishing: PhishFirewall": "PhishFirewall",
    "Communication materials": "Comms Materials",
    "Customer notification": "Customer Notification",
    "Portal creation": "Portal Creation",
    "Implementation": "Implementation",
}
# Who does it, by task handler. Fulfillment's work is Kathleen's.
SHEET_OWNERS = {
    FULFILLMENT: ME,
    FULFILLMENT_TICKET: ME,
    SUPPORT_TICKET: "Support",
    CUSTOMIZATION: "Vineet",
}
# Overrides by task title. Sales Rep is never set here: Kathleen assigns those by hand.
SHEET_OWNER_BY_TITLE = {"SSO": "Bob"}
# Kathleen's, but not finished the day the order is entered.
SHEET_NOT_DONE_TODAY = frozenset({"Implementation"})


def sheet_rows(plan: TaskPlan, today: Optional[date] = None) -> list[list[str]]:
    """One row per task, in SHEET_COLUMNS order. Kathleen's own tasks are entered
    as Completed today; the rest as Assigned with no completed date. Ticket # and
    Notes are left for her to fill in."""
    today = today or date.today()
    day = f"{today.month}/{today.day}/{today.year}"
    rows = []
    for t in plan.tasks:
        item = SHEET_ITEMS.get(t.title)
        if not item:
            continue
        owner = SHEET_OWNER_BY_TITLE.get(t.title) or SHEET_OWNERS.get(t.handled_by, "")
        mine = owner == ME and t.title not in SHEET_NOT_DONE_TODAY
        rows.append([plan.order_ref, plan.company, plan.sales_rep, item,
                     "Completed" if mine else "Assigned", owner, "", day,
                     day if mine else "", ""])
    return rows


def format_sheet_rows(rows: list[list[str]]) -> str:
    """Tab-separated, so a paste into Excel fills one cell per column."""
    return "\n".join("\t".join(c.replace("\t", " ").replace("\n", " ") for c in r) for r in rows)


def task_text(task: Task) -> str:
    """What the copy button copies for a task without a ticket."""
    return "\n".join(task.details)


def format_plan(plan: TaskPlan) -> str:
    """Everything as plain text (Copy all)."""
    lines = list(plan.header) + [""]
    for n, t in enumerate(plan.tasks, 1):
        lines.append(f"{n}. {t.title}  [{t.handled_by}]")
        if t.confirm:
            lines.append(f"   ! {t.confirm}")
        lines.extend(f"   {d}" for d in t.details)
        if t.ticket:
            lines.append(f"   Subject: {t.ticket.subject}")
            lines.extend(f"   | {x}" for x in t.ticket.body.splitlines())
            if t.ticket.options:
                lines.append(f"   ({t.ticket.options})")
            if t.ticket.reply:
                lines.append("   Public reply:")
                lines.extend(f"   | {x}" for x in t.ticket.reply.splitlines())
            if t.ticket.attachments:
                lines.append("   Attach: " + ", ".join(t.ticket.attachments))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
