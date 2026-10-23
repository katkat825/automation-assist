"""Task list and BoldDesk ticket text (Fulfillment tab, Results > Tasks).

Uses the synthetic order from test_fulfillment_order_check.
"""

from app.automation.fulfillment import course_compare as cc
from app.automation.fulfillment import order_check as oc
from app.automation.fulfillment import tasks as ft
from app.automation.fulfillment import tickets as tk
from tests.test_fulfillment_order_check import EMAIL, ESTIMATE_TABLES, SOW_TABLES, _estimate, _sow

PORTAL = (
    "Anti-Phishing Essentials 10922\t1\t76\tThirdPartyScorm\n"
    "Slay The Scam 10781\t1\t76\tThirdPartyScorm\n"
    "SecureGenius®: CyberSecurity Awareness Assessment\t1\t76\tCertExam\n"
)


def _renewal_sow(tables=SOW_TABLES):
    sow = _sow(tables=tables)
    sow.text = "2026 RENEWAL PROPOSAL\nCommunications Toolbox\n" + sow.text
    return sow


def _plan(email=EMAIL, sow=None, estimate=None, with_courses=True):
    portal = cc.parse_portal_list(PORTAL)
    check = oc.check_order(email, [sow or _renewal_sow(), estimate or _estimate()], portal)
    comparison = None
    if with_courses:
        pdf = [cc.Course("11594", "Anti-Phishing Essentials 11594"),
               cc.Course("10781", "Slay The Scam 10781")]
        comparison = cc.compare(pdf, portal.courses, portal.review)
    return ft.build_plan(check, comparison)


def _task(plan, title):
    return next(t for t in plan.tasks if t.title == title)


def _titles(plan):
    return [t.title for t in plan.tasks]


# ---------------------------------------------------------------------------
# Which tasks
# ---------------------------------------------------------------------------

def test_renewal_tasks_in_order_with_handlers():
    plan = _plan()
    assert [(t.title, t.handled_by) for t in plan.tasks] == [
        ("Update portal expiration date", ft.FULFILLMENT),
        ("License count change", ft.SUPPORT_TICKET),
        ("Add courses", ft.FULFILLMENT),
        ("Course archive", ft.SUPPORT_TICKET),
        ("Communication materials", ft.SUPPORT_TICKET),
        ("Customer communication", ft.FULFILLMENT_TICKET),
    ]
    assert _task(plan, "Update portal expiration date").details == ("example.glsondemand.com: 10/17/2027",)


def test_license_count_task_only_when_sow_and_portal_differ():
    assert "License count change" in _titles(_plan())
    tables = [[[c.replace("60", "76") for c in row] for row in t] for t in SOW_TABLES]
    assert "License count change" not in _titles(_plan(sow=_renewal_sow(tables)))


def test_without_portal_list_no_course_tasks_and_header_says_why():
    plan = _plan(with_courses=False)
    assert "Add courses" not in _titles(plan) and "Course archive" not in _titles(plan)
    assert any("Course tasks will appear" in h for h in plan.header)


def test_sku_tasks_prefer_estimate_and_flag_sow_only_items():
    est = [ESTIMATE_TABLES[0] + [
        ["DL-SSO Single Sign On Annual Subscription: Azure AD", "1", "0", "0"],
        ["PH-SAAS1-PB SecurePhish Licensing Saas Solution: In place.", "300", "0", "0"]]]
    sow = [SOW_TABLES[0] + [["SRV-CUSB", "Customization: Policy Acknowledgement", "$0", "1", "$0"]],
           SOW_TABLES[1]]
    plan = _plan(sow=_renewal_sow(sow), estimate=_estimate(tables=est))
    assert "Single Sign On" in _task(plan, "SSO").ticket.body
    assert _task(plan, "SecurePhish").confirm == ""
    custom = _task(plan, "Customization")
    assert custom.handled_by == ft.CUSTOMIZATION and "not on the estimate" in custom.confirm


def test_self_hosted_new_account_gets_scorm_and_comms_but_no_portal_work():
    scorm = [[["SKU", "Description", "Total Users"], ["DL-SCR", "Delivery: SCORM File Integration", "1"]]]
    est = _estimate(tables=scorm, url="NA SCORM")
    est.text += "\nNew Client"
    plan = _plan(email="", sow=_sow(tables=[]), estimate=est, with_courses=False)
    assert _titles(plan) == ["SCORM delivery", "Communication materials"]
    assert _task(plan, "Communication materials").confirm   # no toolbox on the SOW
    assert not any("Course tasks" in h for h in plan.header)


def test_customer_communication_needs_renewal_wording():
    plan = _plan(sow=_sow())   # order type not detected
    task = _task(plan, "Customer communication")
    assert task.ticket is None and task.confirm


# ---------------------------------------------------------------------------
# Ticket text
# ---------------------------------------------------------------------------

def test_ticket_subject_header_and_attachments():
    t = _task(_plan(), "License count change").ticket
    assert t.subject == "16842: Example Co (EXC): License Count Change"
    assert t.body.startswith("Renewal\n\nGLS-Hosted\n\nPOC: Pat Lee, plee@example.com\n\n\n")
    assert t.body.endswith("License Change: reduce license count to 60")
    assert t.attachments == ("sow.pdf", "est.pdf")


def test_archive_ticket_lists_date_and_matched_assessment():
    plan = _plan()
    body = _task(plan, "Course archive").ticket.body
    assert "DELAYED ARCHIVE: 10-15-2026" in body
    assert "Archive and Reset:\n10781" in body
    assert "Archive and Remove:\n10922, SecureGenius®: CyberSecurity Awareness Assessment" in body
    assert "Place items the comparison couldn't" not in _titles(plan)


def test_archive_without_a_date_asks_to_confirm():
    tables = [SOW_TABLES[0], SOW_TABLES[1][:2] + [["Requested Date of Archive", "TBD"]]]
    task = _task(_plan(sow=_renewal_sow(tables)), "Course archive")
    assert "DELAYED ARCHIVE" not in task.ticket.body and "TBD" in task.confirm


def test_customer_notification_reply():
    t = _task(_plan(), "Customer communication").ticket
    assert t.subject == "Thank you for renewing with GLS! Order #16842"
    assert t.reply.startswith("Hello Pat,\n\n")
    assert "URL: https://example.glsondemand.com" in t.reply
    assert t.attachments == () and "Don't send email" in t.options


def test_first_name_from_poc():
    assert tk.first_name("Christine Allen, callen@princeton.com") == "Christine"
    assert tk.first_name("Russell Nicholson\nRussell.Nicholson@gt-ci.com") == "Russell"
    assert tk.first_name("guzzle@vibrenthealth.com") == ""


def test_missing_values_show_as_brackets():
    check = oc.check_order("", [])
    t = tk.comms_materials(check)
    assert t.subject == "[order #]: [company]: Comms Materials"
    assert "[order type?]" in t.body and "[POC?]" in t.body


def test_format_plan_includes_ticket_text():
    text = ft.format_plan(_plan())
    assert "\n1. Update portal expiration date  [Fulfillment]\n" in text
    assert "   Subject: 16842: Example Co (EXC): Archive" in text
    assert "   | Archive and Reset:" in text
    assert "   Attach: sow.pdf, est.pdf" in text
