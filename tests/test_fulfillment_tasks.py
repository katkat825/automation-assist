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
        ("Customer notification", ft.FULFILLMENT_TICKET),
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
    assert _task(plan, "Phishing: PhishingBox").confirm == ""
    assert _task(plan, "Phishing: PhishingBox").ticket is None
    custom = _task(plan, "Customization")
    assert custom.handled_by == ft.CUSTOMIZATION and "not on the estimate" in custom.confirm


def test_self_hosted_new_account_gets_scorm_and_comms_but_no_portal_work():
    scorm = [[["SKU", "Description", "Total Users"], ["DL-SCR", "Delivery: SCORM File Integration", "1"]]]
    est = _estimate(tables=scorm, url="NA SCORM")
    est.text += "\nNew Client"
    plan = _plan(email="", sow=_sow(tables=[]), estimate=est, with_courses=False)
    assert _titles(plan) == ["SCORM delivery", "Communication materials", "Customer notification"]
    welcome = _task(plan, "Customer notification").ticket
    assert welcome.subject.startswith("Welcome to Global Learning Systems!")
    assert "SCORM course packages" in welcome.reply
    assert _task(plan, "Communication materials").confirm   # no toolbox on the SOW
    assert not any("Course tasks" in h for h in plan.header)


def test_customer_notification_needs_renewal_wording():
    plan = _plan(sow=_sow())   # order type not detected
    task = _task(plan, "Customer notification")
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
    assert "DELAYED ARCHIVE" not in task.ticket.body
    assert "Delayed Archive Date: TBD" in task.ticket.body and not task.confirm


def test_archive_without_an_archive_section_asks_to_confirm():
    task = _task(_plan(sow=_renewal_sow([SOW_TABLES[0]])), "Course archive")
    assert "Delayed Archive" not in task.ticket.body and "not on the SOW" in task.confirm


def test_customer_notification_reply():
    t = _task(_plan(), "Customer notification").ticket
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


# ---------------------------------------------------------------------------
# 2026-11-05 rules
# ---------------------------------------------------------------------------

def test_sso_already_in_place_is_not_a_ticket():
    est = [ESTIMATE_TABLES[0] + [["DL-SSO Single Sign On Annual Subscription: In place.", "1", "0", "0"]]]
    plan = _plan(estimate=_estimate(tables=est))
    assert "SSO" not in _titles(plan)
    assert any("SSO is already in place" in h for h in plan.header)


def test_sso_not_yet_in_place_is_a_ticket():
    est = [ESTIMATE_TABLES[0] + [["DL-SSO Single Sign On: not yet in place", "1", "0", "0"]]]
    assert _task(_plan(estimate=_estimate(tables=est)), "SSO").ticket


def test_demo_courses_are_a_fulfillment_task_not_in_the_archive_ticket():
    portal = cc.parse_portal_list(PORTAL + "Share With Care 10823\t1\t5\tThirdPartyScorm\n")
    check = oc.check_order(EMAIL, [_renewal_sow(), _estimate()], portal)
    pdf = [cc.Course("10781", "Slay The Scam 10781")]
    plan = ft.build_plan(check, cc.compare(pdf, portal.courses, portal.review))
    demo = _task(plan, "Remove demo courses")
    assert demo.handled_by == ft.FULFILLMENT and demo.details[0] == "10823"
    assert "10823" not in _task(plan, "Course archive").ticket.body


def test_add_courses_includes_license_count():
    details = _task(_plan(), "Add courses").details
    assert details[:2] == ("11594", "License count: 60")


def test_add_courses_flags_add_on_bundles_with_other_counts():
    sow = [SOW_TABLES[0] + [["CL-SST-ADD", "Add-on bundle", "$1", "20", "$20"]], SOW_TABLES[1]]
    task = _task(_plan(sow=_renewal_sow(sow)), "Add courses")
    assert "License count: 60" in task.details and "60, 20" in task.confirm


def test_phishfirewall_is_flagged():
    sow = _renewal_sow()
    sow.text += "\nPhishFirewall Licensing"
    task = _task(_plan(sow=sow), "Phishing: PhishFirewall")
    assert task.confirm and task.ticket is None


def test_term_in_order_email_reads_as_license_dates():
    email = EMAIL.replace("License Dates:", "Term:")
    check = oc.check_order(email, [_renewal_sow(), _estimate()])
    assert check.email.fields["license_dates"] == "10/18/2026-10/17/2027"


# ---------------------------------------------------------------------------
# Order details choices, PhishFirewall-hosted, welcome emails
# ---------------------------------------------------------------------------

def _check(order_type="", hosting="", sow=None, estimate=None, email=EMAIL):
    return oc.check_order(email, [sow or _renewal_sow(), estimate or _estimate()],
                          order_type=order_type, hosting=hosting)


def test_choices_replace_what_was_detected():
    check = _check(order_type=oc.NEW_ACCOUNT, hosting=oc.PHISHFIREWALL_HOSTED)
    assert (check.detected_order_type, check.detected_hosting) == (oc.RENEWAL, oc.GLS_HOSTED)
    assert (check.order_type, check.hosting) == (oc.NEW_ACCOUNT, oc.PHISHFIREWALL_HOSTED)
    assert any(f.source == oc.CHOSEN for f in check.facts)


def test_detection_says_where_it_came_from():
    check = _check()
    assert check.order_type_source == '"RENEWAL PROPOSAL" in sow.pdf' and "DL-LMS1" in check.hosting_source


def test_ai_managed_phishing_without_lms_sku_is_phishfirewall_hosted():
    sow = _sow(tables=[[["SKU", "Description", "Total Users"], ["CL-BD_CUST", "AI-Managed Phishing", "3768"]]])
    sow.text += "\nAI-Managed Phishing + Cybersecurity Awareness Education"
    check = oc.check_order("", [sow])
    assert check.hosting == oc.PHISHFIREWALL_HOSTED and "AI-Managed Phishing" in check.hosting_source


def test_phishfirewall_hosted_plan():
    plan = ft.build_plan(_check(order_type=oc.NEW_ACCOUNT, hosting=oc.PHISHFIREWALL_HOSTED), None)
    titles = _titles(plan)
    assert "Update PhishFirewall expiration date" in titles and "Update portal expiration date" not in titles
    assert _task(plan, "Phishing: PhishFirewall").confirm
    t = _task(plan, "Customer notification").ticket
    assert "onboarding call" in t.reply and "PhishFirewall-Hosted" in t.body


def test_gls_hosted_welcome_has_portal_url():
    t = _task(ft.build_plan(_check(order_type=oc.NEW_ACCOUNT), None), "Customer notification").ticket
    assert t.subject == "Welcome to GLS OnDemand! Order #16842"
    assert t.reply.startswith("Hello Pat,") and "URL: https://example.glsondemand.com" in t.reply
