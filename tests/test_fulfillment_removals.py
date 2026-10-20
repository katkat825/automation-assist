"""Removal choices (Fulfillment tab, step 4): full course lists vs change orders.

Built from synthetic tables shaped like the real proposals, so no customer
files are needed in the repo.
"""

from app.automation.fulfillment import course_compare as cc
from app.automation.fulfillment import order_check as oc
from app.automation.fulfillment import removals as rm

UPGRADE_TABLES = [
    [["SKU", "Description", "Price per User", "Total Users", "Total Discounted Price"],
     ["CL-11570", "Renewal and Upgrade Courseware Licensing: TravelBrands will upgrade from "
                  "the 2021 OWASP bundle to the new OWASP Top 10 11570 course.",
      "$200.00", "15", "$3,000.00"],
     ["DL-LMS1", "Delivery: GLS OnDemand™ LMS, Hosting & Deployment: In place.", "$0.00", "15", "$0.00"]],
    [["Archive and Remove", "11 OWASP 2021 modules and OWASP Essentials course"],
     ["Requested Date of Archive", "TBD"]],
]

FULL_TABLES = [
    [["SKU", "Description", "Price per User", "Total Users", "Total Discounted Price"],
     ["CL-SST-ELT2", "Renewal Courseware Licensing: SecureSuite Elite Training Program.",
      "$23.00", "350", "$8,050.00"]],
    [["Archive and Reset", "All courses that are renewed for the coming term"],
     ["Archive & Remove", "All courses that are not renewed for the coming term"],
     ["Requested Date of Archive", "09-23-2026"]],
]

PORTAL = [cc.Course(n, name, source="Portal", license_count=15) for n, name in (
    ("10922", "Anti-Phishing Essentials 10922"),
    ("10867", "Data Privacy Essentials 10867"),
    ("10133", "OWASP 2021 Introduction to Secure Coding with OWASP 10133"),
    ("10134", "OWASP 2021 Module 01: Broken Access Control 10134"),
    ("10736", "OWASP 2021 Top 10 Essentials 10736"),
    ("10291", "Spot the Phish: Spear Phishing 10291"),
)]


def _pdf(tables, source="proposal.pdf", courses=()):
    r = cc.ParseResult(courses=list(courses), source=source, tables=tables)
    cc.add_sku_table_courses(r, tables, source)
    return r


def test_sku_only_course_is_read_from_the_sku_cell():
    r = cc.ParseResult(review=[cc.ReviewItem(
        "Renewal and Upgrade Courseware Licensing: ... OWASP Top 10 11570 course.",
        "proposal.pdf", "Mentions a number but couldn't be read as a course.")])
    cc.add_sku_table_courses(r, UPGRADE_TABLES, "proposal.pdf")
    assert [c.number for c in r.courses] == ["11570"]
    assert r.courses[0].note == "from SKU"
    assert r.review == []   # its only number is now accounted for


def test_sku_with_suffix_or_word_is_not_a_course():
    tables = [[["SKU", "Description"], ["CL-9282-CG", "Excel"], ["CL-SST-ESS", "Essentials"],
               ["CL-11569P", "CyberEssentials test-out"]]]
    r = cc.ParseResult()
    cc.add_sku_table_courses(r, tables, "p.pdf")
    assert [c.number for c in r.courses] == ["11569P"]


def test_archive_rows_found_when_archive_and_remove_comes_first():
    assert oc.archive_rows(UPGRADE_TABLES) == [
        ("Archive and Remove", "11 OWASP 2021 modules and OWASP Essentials course"),
        ("Requested Date of Archive", "TBD")]
    assert oc.archive_rows(FULL_TABLES)[1][0] == "Archive & Remove"


def test_change_notes_pick_up_upgrade_sku_and_archive_rows_only():
    notes = rm.change_notes([_pdf(UPGRADE_TABLES)])
    assert [(n.kind, n.label) for n in notes] == [
        (rm.UPGRADE, "CL-11570"),
        (rm.ARCHIVE_REMOVE, "Archive and Remove"),
        (rm.ARCHIVE_OTHER, "Requested Date of Archive")]
    # "Renewal Courseware Licensing" alone is not a change.
    assert all(n.kind != rm.UPGRADE for n in rm.change_notes([_pdf(FULL_TABLES)]))


def test_change_notes_are_not_repeated_across_sow_and_estimate():
    notes = rm.change_notes([_pdf(UPGRADE_TABLES, "sow.pdf"), _pdf(UPGRADE_TABLES, "est.pdf")])
    assert len([n for n in notes if n.kind == rm.UPGRADE]) == 1


def test_upgrade_order_is_partial_with_nothing_ticked_and_matches_suggested():
    pdf = _pdf(UPGRADE_TABLES)
    plan = rm.plan_removals(pdf.courses, PORTAL, rm.change_notes([pdf]))
    assert plan.partial
    assert plan.ticked == set()
    assert set(plan.suggested) == {"10133", "10134", "10736"}   # every OWASP course, nothing else
    assert "owasp" in plan.suggested["10736"]
    assert len(plan.candidates) == len(PORTAL)


def test_generic_words_in_archive_text_do_not_suggest_courses():
    # "Essentials" is in the archive text but too common to mean a course.
    pdf = _pdf(UPGRADE_TABLES)
    plan = rm.plan_removals(pdf.courses, PORTAL, rm.change_notes([pdf]))
    assert "10922" not in plan.suggested and "10867" not in plan.suggested


def test_full_list_ticks_every_portal_only_course_and_suggests_nothing():
    pdf_courses = [c for c in PORTAL if c.number not in ("10133", "10134")]
    pdf = _pdf(FULL_TABLES, courses=pdf_courses)
    plan = rm.plan_removals(pdf.courses, PORTAL, rm.change_notes([pdf]))
    assert not plan.partial
    assert plan.ticked == {"10133", "10134"}
    assert plan.suggested == {}


def test_numbers_in_archive_text_are_ticked_and_checked_against_the_lists():
    tables = [UPGRADE_TABLES[0], [["Archive and Remove", "10133, 10134, 10291 and 99999"]]]
    pdf = _pdf(tables, courses=[PORTAL[-1]])   # 10291 is also on the PDF
    plan = rm.plan_removals(pdf.courses, PORTAL, rm.change_notes([pdf]))
    assert plan.partial
    assert plan.ticked == {"10133", "10134"}
    assert any("10291" in w and "also on the PDF" in w for w in plan.warnings)
    assert any("99999" in w and "isn't on the portal" in w for w in plan.warnings)


def test_few_pdf_courses_against_a_big_portal_is_partial_without_upgrade_wording():
    pdf = _pdf([], courses=[PORTAL[0]])
    plan = rm.plan_removals(pdf.courses, PORTAL, [])
    assert plan.partial and "Only 1 of the 6" in plan.reasons[0]


def test_compare_keeps_unticked_portal_only_courses_as_archive_and_reset():
    pdf = [cc.Course("11570", "OWASP Top 10 11570")]
    keep = {c.number for c in PORTAL} - {"10133", "10134", "10736"}
    result = cc.compare(pdf, PORTAL, keep=keep)
    assert [c.number for c in result.to_add] == ["11570"]
    assert sorted(c.number for c in result.archive_remove) == ["10133", "10134", "10736"]
    assert {c.number for c in result.archive_reset} == keep
