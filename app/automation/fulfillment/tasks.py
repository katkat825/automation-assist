"""
Fulfillment: the list of work one order needs.

Built from the order check (order_check.check_order) and, once the portal list
is pasted, the course comparison (course_compare.compare). Tasks that become a
BoldDesk ticket carry its text (tickets.py). Who handles what:

    Fulfillment             portal exp date, course adds, SecurePhish,
                            customer communication (a BoldDesk ticket)
    Support/T1 (BoldDesk)   license count, course archive, SSO, SCORM,
                            communication materials
    Customization (Monday)  customization, incl. PA screens

SKU-driven tasks (SSO, SCORM, customization, SecurePhish) use the estimate's
line when there is one: it's what was bought, and its wording is what goes in
the ticket. A SKU only on the SOW (an optional item) is flagged to confirm.

Not generated: portal creation (no example yet).
"""

from __future__ import annotations

import difflib
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
SECUREPHISH_SKUS = ("PH-SAAS",)


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

    if check.hosting != oc.SELF_HOSTED:
        if check.exp.date:
            add(Task("Update portal expiration date", FULFILLMENT,
                     (f"{check.portal_url or 'Portal'}: {check.exp.date}",)))
        else:
            add(Task("Update portal expiration date", FULFILLMENT,
                     confirm="No single date found. See the Order tab."))

    if (check.sow_users is not None and check.portal_users is not None
            and check.sow_users != check.portal_users):
        add(Task("License count change", SUPPORT_TICKET, ticket=tk.license_count(check)))

    if comparison is not None:
        plan.tasks.extend(_course_tasks(check, comparison))

    sku, on_estimate = _sku(check, SSO_SKUS)
    if sku:
        add(Task("SSO", SUPPORT_TICKET, confirm=_optional(on_estimate), ticket=tk.sso(check, sku)))
    sku, on_estimate = _sku(check, oc.SCORM_SKU_PREFIXES)
    if sku:
        add(Task("SCORM delivery", SUPPORT_TICKET, confirm=_optional(on_estimate),
                 ticket=tk.scorm(check, sku)))
    sku, on_estimate = _sku(check, CUSTOMIZATION_SKUS)
    if sku:
        add(Task("Customization", CUSTOMIZATION, (_order_line(check), f"{sku.sku}: {sku.description}"),
                 confirm=_optional(on_estimate)))
    sku, on_estimate = _sku(check, SECUREPHISH_SKUS)
    if sku:
        add(Task("SecurePhish", FULFILLMENT, (f"{sku.sku}: {sku.description}",),
                 confirm=_optional(on_estimate)))

    if check.has_comms_toolbox:
        add(Task("Communication materials", SUPPORT_TICKET, ticket=tk.comms_materials(check)))
    elif check.order_type == oc.NEW_ACCOUNT:
        add(Task("Communication materials", SUPPORT_TICKET, ticket=tk.comms_materials(check),
                 confirm="The SOW doesn't list the Communications Toolbox; new accounts get it by default."))

    if check.hosting != oc.SELF_HOSTED:
        if check.order_type == oc.RENEWAL:
            add(Task("Customer communication", FULFILLMENT_TICKET,
                     ticket=tk.customer_notification(check)))
        else:
            add(Task("Customer communication", FULFILLMENT_TICKET, (_order_line(check),),
                     confirm="Only the renewal wording exists so far. Write this one by hand."))
    return plan


def _course_tasks(check: oc.OrderCheck, r: cc.Comparison) -> list[Task]:
    tasks = []
    if r.to_add:
        tasks.append(Task("Add courses", FULFILLMENT,
                          (cc.format_numbers(r.to_add),) + _course_lines(r.to_add)))

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
        tasks.append(Task(
            "Course archive", SUPPORT_TICKET,
            confirm="" if dated else f"Requested archive date: {requested or 'not on the SOW'}.",
            ticket=tk.archive(check, r.archive_reset, r.archive_remove, also_remove,
                              requested if dated else "", other)))

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
