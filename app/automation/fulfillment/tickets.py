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
    if check.hosting in (oc.GLS_HOSTED, oc.PHISHFIREWALL_HOSTED):
        lines.append(check.hosting)
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
            other: Iterable[str] = (), date_tbd: bool = False) -> Ticket:
    """``also_remove``: items without course numbers (an assessment) added to the remove list.
    ``date_tbd``: the SOW has an archive section but no date yet."""
    lines = ([f"DELAYED ARCHIVE: {requested}"] if requested
             else ["Delayed Archive Date: TBD"] if date_tbd else [])
    remove_items = [x for x in [cc.format_numbers(remove), *also_remove] if x]
    lines.append("Archive and Reset:\n" + (cc.format_numbers(reset) or "none"))
    lines.append("Archive and Remove:\n" + (", ".join(remove_items) or "none"))
    lines.extend(other)
    return _ticket(check, "Archive", lines)


def scorm(check: oc.OrderCheck, sku: oc.SkuLine) -> Ticket:
    return _ticket(check, "SCORM", ["SCORM", sku.sku, sku.description])


def sso(check: oc.OrderCheck, sku: oc.SkuLine) -> Ticket:
    return _ticket(check, "SSO", [sku.sku, sku.description], with_url=True)


_NOTIFY_OPTIONS = 'Tick "Exclude on behalf of" and "Don\'t send email", then add the public reply.'
_SUPPORT_LINE = "support@globallearningsystems.com"


def _hello(check: oc.OrderCheck) -> str:
    name = first_name(check.poc)
    return f"Hello {name},\n\n" if name else "Hello,\n\n"


def _portal_link(check: oc.OrderCheck) -> str:
    return f"https://{check.portal_url}" if check.portal_url else "[portal URL?]"


def _notification(check: oc.OrderCheck, subject_start: str, reply: str) -> Ticket:
    return Ticket(
        subject=f"{subject_start} Order #{check.order_number or '[order #]'}",
        body="\n\n".join(_header(check)),
        reply=_hello(check) + reply,
        options=_NOTIFY_OPTIONS,
    )


# New-account welcomes (examples/fulfillment/bolddesk_tickets.md), by hosting.
_WELCOME_SELF_HOSTED = (
    "Thank you for choosing Global Learning Systems! We're excited to welcome you and look "
    "forward to working with you.\n\n"
    "Our team will be providing your SCORM course packages for use in your learning management "
    "system (LMS). They will also be available to assist with any questions you may have about "
    "the SCORM packages or their implementation.\n\n"
    f"If you have any other questions or need assistance, simply reply to Customer Support at {_SUPPORT_LINE}.\n\n"
    "Thanks again for choosing Global Learning Systems!\n\n"
    "GLS Delivery Team"
)
_WELCOME_PHISHFIREWALL = (
    "Thank you for choosing Global Learning Systems! We're excited to welcome you and look "
    "forward to working with you.\n\n"
    "Our implementation team will be reaching out to schedule an onboarding call. During this "
    "call, they'll walk you through the platform, review its features, and answer any questions "
    "you may have.\n\n"
    f"If you have any questions or need assistance in the meantime, simply reply to Customer Support at {_SUPPORT_LINE}.\n\n"
    "Thanks again for choosing Global Learning Systems!\n\n"
    "GLS Delivery Team"
)


def welcome(check: oc.OrderCheck) -> Ticket:
    """New account. None of the three is used for a hosting that isn't known."""
    if check.hosting == oc.SELF_HOSTED:
        return _notification(check, "Welcome to Global Learning Systems!", _WELCOME_SELF_HOSTED)
    if check.hosting == oc.PHISHFIREWALL_HOSTED:
        return _notification(check, "Welcome to Global Learning Systems!", _WELCOME_PHISHFIREWALL)
    return _notification(check, "Welcome to GLS OnDemand!", (
        "Thank you for choosing GLS OnDemand! We're excited to welcome you and look forward to "
        "working with you.\n\n"
        "Your OnDemand portal is ready to access at the following URL:\n\n"
        f"URL: {_portal_link(check)}\n\n"
        "If you haven't logged in yet, please visit the link above to get started. Your username "
        "is typically your email address. Click 'Forgot Password' on the Login page to set your "
        "password and access your account.\n\n"
        "As part of getting started, I'd like to schedule an implementation call to walk you "
        "through your OnDemand portal, review its features, and answer any questions you may have.\n\n"
        "Please let me know a few dates and times that work best for you, and we'll get something "
        "scheduled.\n\n"
        f"If you have any questions or need assistance, simply reply to Customer Support at {_SUPPORT_LINE}.\n\n"
        "Thanks again for choosing OnDemand!\n\n"
        "GLS Delivery Team"
    ))


def customer_notification(check: oc.OrderCheck) -> Ticket:
    """Renewal (GLS-hosted) wording."""
    url = _portal_link(check)
    return _notification(check, "Thank you for renewing with GLS!", (
        "Thank you for renewing with us for another term. We look forward to continuing "
        "to work with you this year!\n\n"
        f"URL: {url}\n\n"
        "If you need your password, click 'Reset Password' on the Login page.\n\n"
        "Simply reply to Customer Support at support@globallearningsystems.com if you "
        "have any questions.\n\n"
        "Thanks again for choosing OnDemand!\n\n"
        "GLS Delivery Team"
    ))


def first_name(poc: str) -> str:
    """'Christine Allen, callen@princeton.com' -> 'Christine'; '' if only an email."""
    first = re.split(r"[,\n]", poc, maxsplit=1)[0].strip() if poc else ""
    return "" if not first or "@" in first else first.split()[0]
