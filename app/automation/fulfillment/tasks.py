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
    Customization (Monday)  customization, incl. PA screens

SKU-driven tasks (SSO, SCORM, customization, phishing) use the estimate's
line when there is one: it's what was bought, and its wording is what goes in
the ticket. A SKU only on the SOW (an optional item) is flagged to confirm.

Not generated: portal creation (no example yet).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
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
CUSTOMIZATION = "Customization (Monday)"

SSO_SKUS = ("DL-SSO",)
CUSTOMIZATION_SKUS = ("SRV-CUS",)
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


def build_plan(check: oc.OrderCheck, comparison: Optional[cc.Comparison]) -> TaskPlan:
    plan = TaskPlan(header=[_order_line(check)])
    if check.warnings:
        plan.header.append(f"! {len(check.warnings)} warning(s) on the Order tab. Check them before starting.")
    portal_work = check.hosting != oc.SELF_HOSTED and check.order_type != oc.NEW_ACCOUNT
    if comparison is None and portal_work:
        plan.header.append("Course tasks will appear once the SOW courses and portal list are both in.")
    add = plan.tasks.append

    pfw = check.hosting == oc.PHISHFIREWALL_HOSTED
    if check.hosting != oc.SELF_HOSTED:
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
        add(Task("SSO", SUPPORT_TICKET, confirm=_optional(on_estimate), ticket=tk.sso(check, sku)))
    sku, on_estimate = _sku(check, oc.SCORM_SKU_PREFIXES)
    if sku:
        add(Task("SCORM delivery", SUPPORT_TICKET, confirm=_optional(on_estimate),
                 ticket=tk.scorm(check, sku)))
    sku, on_estimate = _sku(check, CUSTOMIZATION_SKUS)
    if sku:
        add(Task("Customization", CUSTOMIZATION, (_order_line(check), f"{sku.sku}: {sku.description}"),
                 confirm=_optional(on_estimate)))
    plan.tasks.extend(_phishing_tasks(check))

    if check.has_comms_toolbox:
        add(Task("Communication materials", SUPPORT_TICKET, ticket=tk.comms_materials(check)))
    elif check.order_type == oc.NEW_ACCOUNT:
        add(Task("Communication materials", SUPPORT_TICKET, ticket=tk.comms_materials(check),
                 confirm="The SOW doesn't list the Communications Toolbox; new accounts get it by default."))

    if check.order_type == oc.NEW_ACCOUNT and check.hosting:
        add(Task("Customer notification", FULFILLMENT_TICKET, ticket=tk.welcome(check)))
    elif check.order_type == oc.NEW_ACCOUNT:
        add(Task("Customer notification", FULFILLMENT_TICKET, (_order_line(check),),
                 confirm="Hosting unknown, so no welcome email picked. Choose one in Order details."))
    elif check.order_type == oc.RENEWAL and check.hosting in (oc.GLS_HOSTED, ""):
        add(Task("Customer notification", FULFILLMENT_TICKET,
                 ticket=tk.customer_notification(check)))
    elif check.order_type == oc.RENEWAL and pfw:
        add(Task("Customer notification", FULFILLMENT_TICKET, (_order_line(check),),
                 confirm="No renewal wording for PhishFirewall-hosted yet. Write this one by hand."))
    elif not check.order_type and check.hosting != oc.SELF_HOSTED:
        add(Task("Customer notification", FULFILLMENT_TICKET, (_order_line(check),),
                 confirm="Order type unknown. Choose Renewal or New Account in Order details."))
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

    if r.archive_reset or r.archive_remove or assessment:
        also_remove = [portal_assessment.text if portal_assessment else assessment] if assessment else []
        dated = any(ch.isdigit() for ch in requested)
        on_sow = bool(check.archive)     # the SOW has a Course Archive section
        tasks.append(Task(
            "Course archive", SUPPORT_TICKET,
            confirm="" if dated or on_sow else "Requested archive date: not on the SOW.",
            ticket=tk.archive(check, r.archive_reset, r.archive_remove, also_remove,
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
                          confirm=_optional(on_estimate)))
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


def _optional(on_estimate: bool) -> str:
    return "" if on_estimate else "On the SOW (optional item?) but not on the estimate."


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
