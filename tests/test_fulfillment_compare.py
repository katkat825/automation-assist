"""Renewal course list comparison (Fulfillment tab).

PDF extraction is tested on synthetic word positions shaped like the real
proposal layouts, so no customer PDFs are needed in the repo.
"""

from app.automation.fulfillment import course_compare as cc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CHAR_W = 4.0
_H = 9.0


def _words(lines):
    """[(top, x0, "text"), ...] -> pdfplumber-style word dicts."""
    out = []
    for top, x0, text in lines:
        x = x0
        for token in text.split(" "):
            w = len(token) * _CHAR_W
            out.append({"text": token, "x0": x, "x1": x + w, "top": top, "bottom": top + _H})
            x += w + 2.5
    return out


def _numbers(courses):
    return [c.number for c in courses]


PORTAL_SAMPLE = (
    "Anti-Phishing Essentials 10922 \t1 \t375 \tThirdPartyScorm\n"
    "\t\n"
    "\tCyberEssentials: Principles of Cybersecurity (2025) – Narvar \t1 \t375 \tThirdPartyScorm\n"
    "\t\n"
    "\tOWASP \t5525 \t20218081 \tItemCollection\n"
    "\t\n"
    "\tSecureClip 10201: Phishing: What Happens when you Click \t1 \t350 \tThirdPartyScorm\n"
    "\t\n"
    "\tSlay The Scam 10781 \t1 \t350 \tThirdPartyScorm\n"
    "\t\n"
    "\tSpot the Phish: Spear Phishing 10291\n"
)


# ---------------------------------------------------------------------------
# Portal list
# ---------------------------------------------------------------------------

def test_portal_list_reads_numbers_from_names():
    r = cc.parse_portal_list(PORTAL_SAMPLE)
    assert _numbers(r.courses) == ["10922", "10201", "10781", "10291"]


def test_portal_last_row_without_trailing_columns_is_read():
    r = cc.parse_portal_list("Spot the Phish: Spear Phishing 10291")
    assert _numbers(r.courses) == ["10291"]


def test_portal_item_without_number_goes_to_review():
    r = cc.parse_portal_list(PORTAL_SAMPLE)
    assert len(r.review) == 1
    assert "No course number" in r.review[0].reason


def test_portal_item_collections_are_skipped_entirely():
    r = cc.parse_portal_list(PORTAL_SAMPLE)
    assert all("OWASP" not in c.name for c in r.courses)
    assert all("OWASP" not in x.text for x in r.review)


def test_portal_duplicate_number_goes_to_review():
    r = cc.parse_portal_list("Slay The Scam 10781\t1\nSlay The Scam 10781\t1\n")
    assert _numbers(r.courses) == ["10781"]
    assert len(r.review) == 1


def test_portal_space_separated_paste_is_accepted():
    r = cc.parse_portal_list("Slay The Scam 10781    1    350    ThirdPartyScorm")
    assert _numbers(r.courses) == ["10781"]


def test_portal_extra_columns_are_dropped_from_names():
    r = cc.parse_portal_list(PORTAL_SAMPLE)
    assert [c.name for c in r.courses][0] == "Anti-Phishing Essentials 10922"
    assert all("ThirdPartyScorm" not in c.name for c in r.courses)


def test_portal_paste_without_tabs():
    text = PORTAL_SAMPLE.replace("\t", " ")
    r = cc.parse_portal_list(text)
    assert _numbers(r.courses) == ["10922", "10201", "10781", "10291"]
    assert len(r.review) == 1


def test_portal_name_ending_in_numbers_keeps_them_without_tabs():
    r = cc.parse_portal_list("OWASP 2021 Top 10 Essentials 10736 1 375 ThirdPartyScorm")
    assert r.courses[0].name == "OWASP 2021 Top 10 Essentials 10736"
    assert r.courses[0].number == "10736"


def test_portal_paste_one_cell_per_line():
    text = ("Slay The Scam 10781\n1\n350\nThirdPartyScorm\n"
            "Share With Care 10823\n1\n350\nThirdPartyScorm\n")
    r = cc.parse_portal_list(text)
    assert _numbers(r.courses) == ["10781", "10823"]
    assert r.review == []


def test_portal_collection_one_cell_per_line_is_skipped():
    text = ("Slay The Scam 10781\n1\n350\nThirdPartyScorm\n"
            "OWASP\n5525\n20218081\nItemCollection\n"
            "Share With Care 10823\n1\n350\nThirdPartyScorm\n")
    r = cc.parse_portal_list(text)
    assert _numbers(r.courses) == ["10781", "10823"]
    assert r.review == []


def test_portal_collection_without_tabs_is_skipped():
    r = cc.parse_portal_list("OWASP 5525 20218081 ItemCollection")
    assert r.courses == [] and r.review == []


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def test_two_column_appendix_with_wrapped_names():
    page = _words([
        (130, 300, "Courseware"),
        (158, 43, "Leading a Secure Organization 11402"),
        (158, 340, "PCI DSS Basics 10360"),
        (169, 43, "CyberEssentials Principle: Authentication and Password Security"),
        (169, 340, "Respecting Privileged Access 10228"),
        (180, 43, "10972"),
        (180, 340, "CyberEssentials Principle: Avoiding Business Email Compromise (BEC)"),
        (191, 43, "GDPR Essentials 10983"),
        (191, 340, "Attacks 10976"),
    ])
    r = cc.extract_courses_from_words([page], "p.pdf")
    by = {c.number: c.name for c in r.courses}
    assert set(by) == {"11402", "10360", "10972", "10228", "10976", "10983"}
    assert by["10972"] == "CyberEssentials Principle: Authentication and Password Security 10972"
    assert by["10976"] == ("CyberEssentials Principle: Avoiding Business Email Compromise "
                           "(BEC) Attacks 10976")
    assert r.review == []


def test_tags_and_wrapped_tags_are_stripped_and_kept_as_notes():
    page = _words([
        (100, 43, "OWASP Top 10 11570 - NEW"),
        (100, 298, "Leading a Secure Organization 11402"),
        (111, 43, "Data and AI Governance Best Practices 11537 -"),
        (111, 298, "GDPR Essentials 10983"),
        (122, 43, "NEW"),
        (122, 298, "HIPAA Compliance Training 11072"),
        (133, 43, "Data Privacy Best Practices 10982"),
    ])
    r = cc.extract_courses_from_words([page], "p.pdf")
    by = {c.number: c for c in r.courses}
    assert by["11570"].name == "OWASP Top 10 11570" and by["11570"].note == "NEW"
    assert by["11537"].note == "NEW"
    assert by["10982"].name == "Data Privacy Best Practices 10982"


def test_parenthetical_after_number_wraps_without_polluting_next_name():
    page = _words([
        (100, 43, "Principles of Cybersecurity 11569P (includes"),
        (100, 298, "PCI DSS Basics 10360"),
        (111, 43, "modular test-outs) - UPDATED"),
        (111, 298, "GDPR Essentials 10983"),
        (122, 43, "Anti-Phishing Essentials 11594"),
    ])
    r = cc.extract_courses_from_words([page], "p.pdf")
    by = {c.number: c for c in r.courses}
    assert by["11569P"].name == "Principles of Cybersecurity 11569P"
    assert by["11569P"].note == "UPDATED"
    assert by["11594"].name == "Anti-Phishing Essentials 11594"


def test_secureclip_prefix_numbers():
    page = _words([
        (100, 43, "SecureClip 10201: Phishing: What Happens When You Click"),
        (100, 330, "SecureClip 10202: Phishing: How To Spot An Attack"),
        (111, 43, "SecureClip 10203: What Happens to Your Stolen Data"),
        (111, 330, "Share With Care 10823"),
        (122, 43, "Slay The Scam 10781"),
        (122, 330, "Spot the Phish: BEC 10224"),
    ])
    r = cc.extract_courses_from_words([page], "p.pdf")
    assert set(_numbers(r.courses)) == {"10201", "10202", "10203", "10823", "10781", "10224"}


def test_sku_style_rows_and_years_inside_names():
    page = _words([
        (138, 41, "CL-ENTL-CG"),
        (138, 114, "CL-9282-CG - Excel 2019 Level 1 Basic"),
        (138, 414, "$78.00"),
        (151, 114, "CL-9283-CG - Excel 2019 Level 2 Intermediate"),
        (164, 114, "​CL-9508-CG - Teams (Microsoft)"),
        (266, 41, "DL-LMS1"),
        (266, 114, "Delivery: GLS OnDemand LMS, Hosting & Deployment: In place."),
    ])
    r = cc.extract_courses_from_words([page], "p.pdf")
    by = {c.number: c.name for c in r.courses}
    assert by == {
        "9282": "Excel 2019 Level 1 Basic 9282",
        "9283": "Excel 2019 Level 2 Intermediate 9283",
        "9508": "Teams (Microsoft) 9508",
    }


def test_dates_prices_and_footers_are_not_courses():
    page = _words([
        (50, 300, "August 13, 2026"),
        (60, 300, "09 / 23 / 2026"),
        (70, 37, "License Term: 9/29/2026-9/28/2027"),
        (80, 37, "Renewal Courseware Licensing $23.00 350 $8,050.00"),
        (90, 37, "at least 1000 pixels wide and 300 dpi"),
        (783, 25, "Document Ref: SC7SC-TR6MB-4GLAI-VHQE5"),
        (783, 542, "Page 6 of 7"),
    ])
    r = cc.extract_courses_from_words([page], "p.pdf")
    assert r.courses == []


def test_unreadable_number_line_goes_to_review():
    page = _words([
        (100, 43, "Some Course Name 12345 with trailing words"),
        (140, 43, "Another heading"),
    ])
    r = cc.extract_courses_from_words([page], "p.pdf")
    assert r.courses == []
    assert len(r.review) == 1 and "12345" in r.review[0].text


def test_duplicate_listing_in_pdf_is_kept_once():
    page = _words([
        (100, 43, "Avoiding Phishing Attacks 10975"),
        (100, 298, "Avoiding Phishing Attacks 10975"),
        (111, 43, "GDPR Essentials 10983"),
        (111, 298, "PCI DSS Basics 10360"),
        (122, 43, "HIPAA Compliance Training 11072"),
        (122, 298, "Slay The Scam 10781"),
    ])
    r = cc.extract_courses_from_words([page], "p.pdf")
    assert _numbers(r.courses).count("10975") == 1


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def _c(name, source="PDF"):
    return cc.Course(number=cc.course_number_from_name(name), name=name, source=source)


def test_three_lists_sorted_by_name_output_as_numbers():
    pdf = [_c("Spot the Vish 11564"), _c("Slay The Scam 10781"), _c("Anti-Phishing Essentials 11594"),
           _c("GDPR Essentials 10983")]
    portal = [_c("Slay The Scam 10781", "Portal"), _c("GDPR Essentials 10983", "Portal"),
              _c("OWASP 2021 Module 03: Injection 10136", "Portal"),
              _c("Anti-Phishing Essentials 10922", "Portal")]
    r = cc.compare(pdf, portal)
    assert cc.format_numbers(r.to_add) == "11594, 11564"
    assert cc.format_numbers(r.archive_remove) == "10922, 10136"
    assert cc.format_numbers(r.archive_reset) == "10983, 10781"


def test_reset_list_uses_portal_names():
    r = cc.compare([_c("Role-Based Training: Security Managers 10194")],
                   [_c("Role Based Training - Security Managers 10194", "Portal")])
    assert r.archive_reset[0].source == "Portal"


def test_each_letter_variant_is_its_own_course():
    pdf = [_c("Principles 11569"), _c("Principles 11569P"), _c("Principles 11569AP"),
           _c("Principles 11569TX")]
    portal = [_c("Principles 11569", "Portal"), _c("Principles 11569A", "Portal"),
              _c("Principles 11569TX", "Portal")]
    r = cc.compare(pdf, portal)
    assert sorted(cc.format_numbers(r.to_add).split(", ")) == ["11569AP", "11569P"]
    assert cc.format_numbers(r.archive_remove) == "11569A"
    assert sorted(cc.format_numbers(r.archive_reset).split(", ")) == ["11569", "11569TX"]
    assert r.review == []
    # variants of the same course aren't reported as renumbered courses
    assert r.possible_replacements == []


def test_multi_letter_variants_are_read_from_names():
    assert cc.course_number_from_name("Principles of Cybersecurity 11569AP") == "11569AP"
    assert cc.course_number_from_name("Principles of Cybersecurity 11569tx") == "11569TX"
    assert cc.course_number_from_name("SecureClip 10201A: Phishing") == "10201A"


def test_multi_letter_variants_in_pdf():
    page = _words([
        (100, 43, "Principles of Cybersecurity 11569AP"),
        (100, 330, "Principles of Cybersecurity 11569TX - NEW"),
        (111, 43, "Principles of Cybersecurity 11569A"),
        (111, 330, "GDPR Essentials 10983"),
        (122, 43, "HIPAA Compliance Training 11072"),
        (122, 330, "PCI DSS Basics 10360"),
    ])
    r = cc.extract_courses_from_words([page], "p.pdf")
    assert {"11569AP", "11569TX", "11569A"} <= set(_numbers(r.courses))


def test_letter_suffix_variant_on_both_is_reset():
    r = cc.compare([_c("Principles 11569P")], [_c("Principles 11569P", "Portal")])
    assert cc.format_numbers(r.archive_reset) == "11569P"
    assert r.review == []


def test_possible_replacements_flag_renumbered_courses():
    r = cc.compare([_c("Role-Based Training: IT Administrators & Engineers 10408")],
                   [_c("Role-Based Training: IT Administrators and Engineers 10193", "Portal")])
    assert len(r.possible_replacements) == 1
    # still reported in the normal lists
    assert cc.format_numbers(r.to_add) == "10408"
    assert cc.format_numbers(r.archive_remove) == "10193"


def test_sort_is_case_insensitive():
    r = cc.compare([_c("apple 10001"), _c("Banana 10002"), _c("cherry 10003")], [])
    assert cc.format_numbers(r.to_add) == "10001, 10002, 10003"


# ---------------------------------------------------------------------------
# Portal expiration date
# ---------------------------------------------------------------------------

def test_exp_date_is_end_of_license_term():
    dates, notes = cc.find_expiration_dates(
        "pricing below. License Term: 9/29/2026-9/28/2027\u200b", "p.pdf")
    assert [d.date for d in dates] == ["09/28/2027"] and notes == []


def test_exp_date_term_with_to():
    dates, _ = cc.find_expiration_dates("License Term: 8/20/2026 to 8/19/2027", "p.pdf")
    assert dates[0].date == "08/19/2027"


def test_exp_date_from_order_email_license_dates():
    dates, _ = cc.find_expiration_dates("URL: x\nLicense Dates: 10/18/2026-10/17/2027", "Order email")
    assert [d.date for d in dates] == ["10/17/2027"]


def test_estimate_prefers_renewal_period_over_expiration_date():
    text = "DATE 07/09/2026\nEXPIRATION DATE 09/30/2027\nRenewal PD: 9/29/2026-9/28/2027"
    dates, _ = cc.find_expiration_dates(text, "e.pdf")
    assert [d.date for d in dates] == ["09/28/2027"]


def test_expiration_date_used_when_no_term():
    dates, _ = cc.find_expiration_dates("EXPIRATION DATE 09/28/2027", "e.pdf")
    assert [d.date for d in dates] == ["09/28/2027"]


def test_no_date_found_is_empty():
    r = cc.pick_expiration_date([cc.ParseResult()])
    assert r.date == "" and r.notes == []


def test_multi_year_selection_adds_note():
    _, notes = cc.find_expiration_dates(
        "License Term: 8/20/2026 to 8/19/2027\nLicensing Option Selected: 3 Year Licensing - Billed Upfront",
        "p.pdf")
    assert len(notes) == 1 and "3 Year" in notes[0]


def test_conflicting_dates_across_pdfs_are_not_picked():
    a = cc.ParseResult(exp_dates=[cc.ExpDate("09/28/2027", "a.pdf", "x")])
    b = cc.ParseResult(exp_dates=[cc.ExpDate("09/28/2028", "b.pdf", "y")])
    r = cc.pick_expiration_date([a, b])
    assert r.date == "" and "different dates" in r.notes[0]


def test_matching_dates_across_pdfs_agree():
    a = cc.ParseResult(exp_dates=[cc.ExpDate("09/28/2027", "a.pdf", "x")])
    b = cc.ParseResult(exp_dates=[cc.ExpDate("09/28/2027", "b.pdf", "y")])
    assert cc.pick_expiration_date([a, b]).date == "09/28/2027"


# ---------------------------------------------------------------------------
# Demo licenses (portal license count of 5)
# ---------------------------------------------------------------------------

def test_portal_license_count_is_read():
    r = cc.parse_portal_list("Slay The Scam 10781 \t1 \t5 \tThirdPartyScorm\n"
                             "Share With Care 10823 1 350 ThirdPartyScorm")
    assert [c.license_count for c in r.courses] == [5, 350]


def test_portal_license_count_one_cell_per_line():
    r = cc.parse_portal_list("Slay The Scam 10781\n1\n5\nThirdPartyScorm\n"
                             "Share With Care 10823\n1\n350\nThirdPartyScorm\n")
    assert [c.license_count for c in r.courses] == [5, 350]


def test_demo_license_on_pdf_counts_as_add_not_reset():
    portal = cc.parse_portal_list("Slay The Scam 10781\t1\t5\tThirdPartyScorm\n"
                                  "Share With Care 10823\t1\t350\tThirdPartyScorm").courses
    pdf = [_c("Slay The Scam 10781"), _c("Share With Care 10823")]
    r = cc.compare(pdf, portal)
    assert cc.format_numbers(r.to_add) == "10781"
    assert "demo" in r.to_add[0].note
    assert cc.format_numbers(r.archive_reset) == "10823"


def test_demo_license_not_on_pdf_is_removed():
    portal = cc.parse_portal_list("Slay The Scam 10781\t1\t5\tThirdPartyScorm").courses
    r = cc.compare([], portal)
    assert cc.format_numbers(r.archive_remove) == "10781"
