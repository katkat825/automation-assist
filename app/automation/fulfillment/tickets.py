"""
Fulfillment: BoldDesk ticket text for one order, in the house format
(examples/fulfillment/bolddesk_tickets.md):

    Subject   "<order #>: <company>: <ticket type>"
    Body      order type / hosting / POC, then the ticket's own lines
    Attach    the order's PDFs

Anything the order check couldn't find shows as [brackets] so it can't be
pasted unnoticed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from . import course_compare as cc
from . import order_check as oc


@dataclass(frozen=True)
class Ticket:
    subject: str
    body: str
    attachments: tuple[str, ...] = ()
    reply: str = ""      # public reply to add once the ticket exists
    options: str = ""    # what to tick before saving


def subject(check: oc.OrderCheck, kind: str) -> str:
    return f"{check.order_number or '[order #]'}: {check.company or '[company]'}: {kind}"


def _header(check: oc.OrderCheck, with_url: bool = False) -> list[str]:
    lines = [check.order_type or "[order type?]"]
    if check.hosting == oc.GLS_HOSTED:
        lines.append(oc.GLS_HOSTED)
    elif not check.hosting:
        lines.append("[hosting?]")
    # self-hosted: no hosting line, as in the SCORM example
    lines.append(f"POC: {check.poc or '[POC?]'}")
    if with_url:
        lines.append(check.portal_url or "[portal URL?]")
    return lines


def _ticket(check: oc.OrderCheck, kind: str, lines: Iterable[str], with_url: bool = False) -> Ticket:
    body = "\n\n".join(_header(check, with_url)) + "\n\n\n" + "\n\n".join(lines)
    return Ticket(subject(check, kind), body, tuple(check.sources))


def comms_materials(check: oc.OrderCheck) -> Ticket:
    return _ticket(check, "Comms Materials", ["Please provide the communication materials package"])


def license_count(check: oc.OrderCheck) -> Ticket:
    verb = "reduce" if check.sow_users < check.portal_users else "increase"
    return _ticket(check, "License Count Change",
                   [f"License Change: {verb} license count to {check.sow_users}"])


def archive(check: oc.OrderCheck, reset: list[cc.Course], remove: list[cc.Course],
            also_remove: Iterable[str] = (), requested: str = "",
            other: Iterable[str] = ()) -> Ticket:
    """``also_remove``: items without course numbers (an assessment) added to the remove list."""
    lines = [f"DELAYED ARCHIVE: {requested}"] if requested else []
    remove_items = [x for x in [cc.format_numbers(remove), *also_remove] if x]
    lines.append("Archive and Reset:\n" + (cc.format_numbers(reset) or "none"))
    lines.append("Archive and Remove:\n" + (", ".join(remove_items) or "none"))
    lines.extend(other)
    return _ticket(check, "Archive", lines)


def scorm(check: oc.OrderCheck, sku: oc.SkuLine) -> Ticket:
    return _ticket(check, "SCORM", ["SCORM", sku.sku, sku.description])


def sso(check: oc.OrderCheck, sku: oc.SkuLine) -> Ticket:
    return _ticket(check, "SSO", [sku.sku, sku.description], with_url=True)


def customer_notification(check: oc.OrderCheck) -> Ticket:
    """Renewal wording only; there's no new-account version yet."""
    name = first_name(check.poc)
    url = f"https://{check.portal_url}" if check.portal_url else "[portal URL?]"
    reply = (
        f"Hello {name},\n\n" if name else "Hello,\n\n"
    ) + (
        "Thank you for renewing with us for another term. We look forward to continuing "
        "to work with you this year!\n\n"
        f"URL: {url}\n\n"
        "If you need your password, click 'Reset Password' on the Login page.\n\n"
        "Simply reply to Customer Support at support@globallearningsystems.com if you "
        "have any questions.\n\n"
        "Thanks again for choosing OnDemand!\n\n"
        "GLS Delivery Team"
    )
    return Ticket(
        subject=f"Thank you for renewing with GLS! Order #{check.order_number or '[order #]'}",
        body="\n\n".join(_header(check)),
        reply=reply,
        options='Tick "Exclude on behalf of" and "Don\'t send email", then add the public reply.',
    )


def first_name(poc: str) -> str:
    """'Christine Allen, callen@princeton.com' -> 'Christine'; '' if only an email."""
    first = re.split(r"[,\n]", poc, maxsplit=1)[0].strip() if poc else ""
    return "" if not first or "@" in first else first.split()[0]
