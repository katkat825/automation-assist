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
    assert _titles(plan) == ["SCORM delivery", "Communication materials", "Implementation",
                             "Customer notification"]
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


# ---------------------------------------------------------------------------
# Change order
# ---------------------------------------------------------------------------

def _change_plan(sow=None, estimate=None, keep=()):
    portal = cc.parse_portal_list(PORTAL)
    check = oc.check_order(EMAIL, [sow or _renewal_sow(), estimate or _estimate()], portal,
                           order_type=oc.CHANGE_ORDER)
    pdf = [cc.Course("11594", "Anti-Phishing Essentials 11594")]
    comparison = cc.compare(pdf, portal.courses, portal.review, keep=keep, reset=False)
    return ft.build_plan(check, comparison)


def test_change_order_with_nothing_removed_has_no_archive_ticket():
    plan = _change_plan(sow=_renewal_sow([SOW_TABLES[0]]), keep={"10922", "10781"})
    assert "Course archive" not in _titles(plan)
    assert "Add courses" in _titles(plan)


def test_change_order_archive_ticket_is_archive_and_remove_only():
    plan = _change_plan(keep={"10781"})
    body = _task(plan, "Course archive").ticket.body
    assert "Archive and Reset" not in body
    assert "Archive and Remove:\n10922, SecureGenius®: CyberSecurity Awareness Assessment" in body
    assert body.startswith("Change Order\n\nGLS-Hosted")


def test_change_order_subject_needs_no_order_number():
    no_number = _estimate()
    no_number.text = no_number.text.replace("Estimate 16842\n", "")
    t = _task(_change_plan(estimate=no_number, keep={"10781"}), "Course archive").ticket
    assert t.subject == "Example Co (EXC): Archive"
    # A number that is there is still used.
    t = _task(_change_plan(keep={"10781"}), "Course archive").ticket
    assert t.subject == "16842: Example Co (EXC): Archive"


def test_change_order_customer_notification_is_written_by_hand():
    task = _task(_change_plan(keep={"10781"}), "Customer notification")
    assert task.ticket is None and "by hand" in task.confirm
    # Included for self-hosted change orders too.
    scorm = [[["SKU", "Description", "Total Users"], ["DL-SCR", "Delivery: SCORM", "1"]]]
    plan = _change_plan(sow=_sow(tables=scorm), estimate=_estimate(tables=scorm, url="NA SCORM"),
                        keep={"10781"})
    assert "Customer notification" in _titles(plan)


def test_change_order_skips_exp_date_and_needs_no_estimate():
    portal = cc.parse_portal_list(PORTAL)
    check = oc.check_order(EMAIL, [_renewal_sow()], portal, order_type=oc.CHANGE_ORDER)
    assert not any("QuickBooks estimate" in w for w in check.warnings)
    sow = [SOW_TABLES[0] + [["DL-SSO", "Single Sign On: Azure AD", "$0", "1", "$0"]], SOW_TABLES[1]]
    check = oc.check_order(EMAIL, [_renewal_sow(sow)], portal, order_type=oc.CHANGE_ORDER)
    plan = ft.build_plan(check, None)
    assert "Update portal expiration date" not in _titles(plan)
    assert _task(plan, "SSO").confirm == ""   # SOW-only isn't "optional" without an estimate
    # A renewal without an estimate still warns.
    assert any("QuickBooks estimate" in w
               for w in oc.check_order(EMAIL, [_renewal_sow()], portal).warnings)


# ---------------------------------------------------------------------------
# Customization
# ---------------------------------------------------------------------------

def _custom_plan(row=None, text=""):
    sow = _renewal_sow([SOW_TABLES[0] + ([row] if row else []), SOW_TABLES[1]])
    sow.text += text
    return _plan(sow=sow)


def test_customization_ticket_from_sku_with_course_item_and_hours():
    plan = _custom_plan(["SRV-CUSB", "Customization: PA Screen for course 10781, 4 hours", "$0", "1", "$0"])
    task = _task(plan, "Customization")
    assert task.handled_by == ft.CUSTOMIZATION
    t = task.ticket
    assert t.subject == "16842: Example Co (EXC): Customization"
    assert t.body.startswith("Renewal\n\nGLS-Hosted\n\nPOC: Pat Lee, plee@example.com\n\nexample.glsondemand.com")
    assert "SRV-CUSB: Customization: PA Screen for course 10781, 4 hours" in t.body
    assert "Course: 10781" in t.body
    assert "Customization: PA Screen" in t.body
    assert "Hours budgeted: 4" in t.body
    assert t.attachments == ("sow.pdf", "est.pdf")


def test_customization_without_details_says_so():
    t = _task(_custom_plan(["SRV-CUSB", "Customization", "$0", "1", "$0"]), "Customization").ticket
    assert "Course: [course #?]" in t.body
    assert "Customization: not specified on the SOW" in t.body
    assert "Hours budgeted: not stated" in t.body


def test_customization_found_from_description_without_the_sku():
    row = ["SRV-OTHER", "Customization: Policy Acknowledgement 10922", "$0", "1", "$0"]
    t = _task(_custom_plan(row), "Customization").ticket
    assert "Course: 10922" in t.body and "Customization: Policy Acknowledgement" in t.body


def test_customization_found_from_pdf_text_when_no_sku_line_is_read():
    task = _task(_custom_plan(text="\nSRV-CUSB Customization: PA Screens for 10781"), "Customization")
    assert "PDF text" in task.confirm
    assert "Course: 10781" in task.ticket.body and "PA Screens" in task.ticket.body


def test_no_customization_no_task():
    assert "Customization" not in _titles(_plan())


def test_customization_also_gets_a_monday_reminder_for_fulfillment():
    plan = _custom_plan(["SRV-CUSB", "Customization: PA Screen for course 10781", "$0", "1", "$0"])
    titles = _titles(plan)
    assert titles.index("Add customization to Monday") == titles.index("Customization") + 1
    task = _task(plan, "Add customization to Monday")
    assert task.handled_by == ft.FULFILLMENT and task.ticket is None
    assert "Course: 10781" in task.details and "Customization: PA Screen" in task.details


# ---------------------------------------------------------------------------
# Chubb Policyholder
# ---------------------------------------------------------------------------

def test_policyholder_needs_portal_creation_and_chubb_notification():
    from tests.test_fulfillment_order_check import POLICYHOLDER_EMAIL
    plan = ft.build_plan(oc.check_order(POLICYHOLDER_EMAIL, []), None)
    assert [(t.title, t.handled_by) for t in plan.tasks] == [
        ("Portal creation", ft.FULFILLMENT), ("Customer notification", ft.FULFILLMENT_TICKET)]
    assert "Total number of users: 75" in _task(plan, "Portal creation").details
    t = _task(plan, "Customer notification").ticket
    assert t.subject == tk.CHUBB_SUBJECT
    assert t.body == "Chubb Policyholder\n\nGLS-Hosted\n\nPOC: Jordan Smith, jsmith@example.com"
    assert t.reply.startswith("Hello Jordan,\n\nThank you for registering")
    assert "Website:\n[portal URL?]" in t.reply and "Username:\njsmith@example.com" in t.reply
    assert "Arnie" in _task(plan, "Customer notification").confirm
    assert not any("Course tasks" in h for h in plan.header)


# ---------------------------------------------------------------------------
# Spreadsheet rows
# ---------------------------------------------------------------------------

def test_sheet_rows_one_per_task_mine_completed_others_assigned():
    from datetime import date
    sow = _renewal_sow()
    sow.text += "\nPrepared by:\nNatalie Williams"
    plan = _plan(sow=sow)
    rows = ft.sheet_rows(plan, date(2026, 11, 24))
    assert [r[3] for r in rows] == ["Portal Exp", "License Change", "Content Add", "Archive",
                                    "Comms Materials", "Customer Notification"]
    assert rows[0] == ["16842", "Example Co (EXC)", "Natalie", "Portal Exp", "Completed", "Kathleen",
                       "", "11/24/2026", "11/24/2026", ""]
    assert rows[1][4:9] == ["Assigned", "Support", "", "11/24/2026", ""]
    text = ft.format_sheet_rows(rows)
    assert text.count("\n") == len(rows) - 1 and text.splitlines()[0].count("\t") == 9


def test_sheet_order_ref_for_policyholder_and_no_cost_change_order():
    from datetime import date
    from tests.test_fulfillment_order_check import POLICYHOLDER_EMAIL
    plan = ft.build_plan(oc.check_order(POLICYHOLDER_EMAIL, []), None)
    rows = ft.sheet_rows(plan, date(2026, 11, 24))
    assert [(r[0], r[1], r[3]) for r in rows] == [
        ("Chubb Policyholder", "Example Holdings LLC", "Portal Creation"),
        ("Chubb Policyholder", "Example Holdings LLC", "Customer Notification")]
    no_number = _estimate()
    no_number.text = no_number.text.replace("Estimate 16842\n", "")
    plan = _change_plan(estimate=no_number, keep={"10781"})
    assert {r[0] for r in ft.sheet_rows(plan)} == {"No-cost CO"}


def test_sheet_owners_bob_for_sso_vineet_for_customization_and_open_implementation():
    from datetime import date
    est = [ESTIMATE_TABLES[0] + [["DL-SSO Single Sign On Annual Subscription: Azure AD", "1", "0", "0"]]]
    sow = [SOW_TABLES[0] + [["SRV-CUSB", "Customization: PA Screen 10781", "$0", "1", "$0"]], SOW_TABLES[1]]
    sow = _renewal_sow(sow)
    sow.text = sow.text.replace("2026 RENEWAL PROPOSAL", "New Client")
    plan = _plan(sow=sow, estimate=_estimate(tables=est))
    rows = {r[3] + ("/monday" if r[5] == "Kathleen" and r[3] == "Customization" else ""): r
            for r in ft.sheet_rows(plan, date(2026, 11, 24))}
    assert rows["SSO"][4:6] == ["Assigned", "Bob"]
    assert rows["Customization"][4:6] == ["Assigned", "Vineet"]
    assert rows["Customization/monday"][4:6] == ["Completed", "Kathleen"]
    assert rows["Implementation"][4:9] == ["Assigned", "Kathleen", "", "11/24/2026", ""]
    assert all(r[5] != "Sales Rep" for r in rows.values())
