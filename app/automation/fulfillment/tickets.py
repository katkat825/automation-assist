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
from typing import Iterable, Optional

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
    """A change order doesn't need an order number: it's left out when there isn't one."""
    if not check.order_number and check.order_type == oc.CHANGE_ORDER:
        return f"{check.company or '[company]'}: {kind}"
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


def archive(check: oc.OrderCheck, reset: Optional[list[cc.Course]], remove: list[cc.Course],
            also_remove: Iterable[str] = (), requested: str = "",
            other: Iterable[str] = (), date_tbd: bool = False) -> Ticket:
    """``also_remove``: items without course numbers (an assessment) added to the remove list.
    ``date_tbd``: the SOW has an archive section but no date yet.
    ``reset``: None leaves the Archive and Reset line out (change orders never reset)."""
    lines = ([f"DELAYED ARCHIVE: {requested}"] if requested
             else ["Delayed Archive Date: TBD"] if date_tbd else [])
    remove_items = [x for x in [cc.format_numbers(remove), *also_remove] if x]
    if reset is not None:
        lines.append("Archive and Reset:\n" + (cc.format_numbers(reset) or "none"))
    lines.append("Archive and Remove:\n" + (", ".join(remove_items) or "none"))
    lines.extend(other)
    return _ticket(check, "Archive", lines)


def customization(check: oc.OrderCheck, lines: Iterable[str], courses: Iterable[str] = (),
                  items: Iterable[str] = (), hours: str = "") -> Ticket:
    """``lines``: the SKU line (or the PDF lines that mention customization).
    What's wanted and the hours may not be given; that's said plainly rather than
    bracketed, since there's often nothing more to find."""
    courses, items = list(courses), list(items)
    body = list(lines)
    body.append("Course: " + (", ".join(courses) if courses else "[course #?]"))
    body.append("Customization: " + (", ".join(items) if items else "not specified on the SOW"))
    body.append(f"Hours budgeted: {hours}" if hours else "Hours budgeted: not stated")
    return _ticket(check, "Customization", body, with_url=True)


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


# Chubb Policyholder welcome (docs/working_notes/policyholder.md). Arnie Walker is
# the program's point of contact in the template itself, not a field from the email.
CHUBB_SUBJECT = "Action Required: Schedule Your Program Onboarding Session"
_CHUBB_REPLY = (
    "Thank you for registering for the complimentary Chubb Cyber Awareness Training Program. "
    "Your training portal is now ready, and we're excited to help you launch a successful "
    "cybersecurity awareness program.\n\n"
    "Your Next Step: Program Onboarding Session\n\n"
    "If you haven't already scheduled your Program Onboarding Session, please do so using the "
    "link below. We recommend reserving your onboarding appointment within 5 days of registration "
    "to keep your implementation on track and help ensure a smooth launch.\n\n"
    "During your Program Onboarding Session, Arnie Walker, your primary point of contact, will "
    "help you prepare your program for launch by:\n\n"
    "- Importing your users (via CSV or reviewing SSO integration)\n"
    "- Reviewing IP configuration and email safelisting requirements\n"
    "- Reviewing your employee welcome messaging and launch communications\n"
    "- Sharing best practices for a successful rollout\n"
    "- Answering any questions before you launch\n\n"
    "📅 Schedule Your Program Onboarding Session: Arnie's Calendar\n\n"
    "How to Access Your Training Portal\n\n"
    "Website:\n{website}\n\n"
    "Username:\n{username}\n\n"
    "If you need your password, simply click Reset Password on the login page.\n\n"
    "After You Log In\n"
    "- Open the Admin Dashboard.\n"
    "- Click Show Page Help in the title bar to access helpful videos and support articles.\n"
    "- Your assigned course(s) are already loaded and ready to launch.\n"
    "- Your course(s) have been assigned to the Everyone group. Any new users you add will "
    "automatically be included. You may create additional groups at any time to better organize "
    "your learners.\n\n"
    "Need Assistance?\n\n"
    "Additional help is always available through the Frequently Asked Questions (FAQ) within your "
    "training portal.\n\n"
    "If you have questions before your Program Onboarding Session or need assistance at any point, "
    "we're here to help.\n\n"
    "Thank you for participating in the complimentary Chubb Cyber Awareness Training Program. We "
    "look forward to helping you build a stronger security culture.\n\n"
    "Best regards,\n"
    "GLS Delivery Team\n\n\n"
    "Looking to Expand Your Program?\n"
    "As your cybersecurity program evolves, Global Learning Systems offers additional solutions to "
    "help organizations strengthen security awareness and reduce human risk, including:\n"
    "- AI-powered phishing simulations\n"
    "- Role-based security awareness training\n"
    "- Secure Coding training for developers\n"
    "- Compliance training\n"
    "- Managed security awareness services\n"
    "- Custom learning solutions\n\n"
    "If you'd like to learn more about these solutions, simply ask Arnie during your Program "
    "Onboarding Session or visit www.globallearningsystems.com to learn more."
)


def chubb_notification(check: oc.OrderCheck) -> Ticket:
    """Chubb Policyholder welcome. The portal is new, so its URL is usually not
    known yet and shows as [portal URL?]."""
    email = check.policyholder.get("admin_email", "")
    reply = _CHUBB_REPLY.format(website=_portal_link(check), username=email or "[admin email?]")
    return Ticket(subject=CHUBB_SUBJECT, body="\n\n".join(_header(check)),
                  reply=_hello(check) + reply, options=_NOTIFY_OPTIONS)


def first_name(poc: str) -> str:
    """'Christine Allen, callen@princeton.com' -> 'Christine'; '' if only an email."""
    first = re.split(r"[,\n]", poc, maxsplit=1)[0].strip() if poc else ""
    return "" if not first or "@" in first else first.split()[0]
