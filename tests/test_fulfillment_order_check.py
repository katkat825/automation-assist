"""Order check (Fulfillment tab): order email + SOW + estimate + portal.

Built from synthetic text and tables shaped like the real documents, so no
customer files are needed in the repo.
"""

from app.automation.fulfillment import course_compare as cc
from app.automation.fulfillment import order_check as oc

EMAIL = (
    "URL: Example.glsondemand.com\n"
    "Opp Name: Example Co: (EXC): Ess Bdl_76 users_2026\n"
    "Full Company Name: Example Co (EXC)\n"
    "Billing POC Name + Email: Whoever you have on file; please CC\n"
    "Pat Lee, plee@example.com\n"
    "OD Admin POC Name + Email: Pat Lee, plee@example.com\n"
    "License Dates: 10/18/2026-10/17/2027\n"
)

SOW_TABLES = [
    [["SKU", "Description", "Price per User", "Total Users", "Total Discounted Price"],
     ["CL-SST-ESS", "Essentials Training Program", "$11.75", "60", "$705.00"],
     ["DL-LMS1", "Delivery: Hosting & Deployment: In place.", "$0.00", "1", "$0.00"],
     ["$705.00", "", "", "", ""]],
    [["Archive and Reset", "All courses that have been renewed for the coming term"],
     ["Remove Assessment (cannot be archived)", "SecureGenius CyberSecurity Assessment"],
     ["Requested Date of Archive", "10-15-2026"]],
]

ESTIMATE_TABLES = [
    [["ACTIVITY", "LICENSE COUNT", "PER USER PRICE", "AMOUNT"],
     ["CL-SST-ESS Essentials Training Program Renewal PD: 10/18/2026-10/17/2027", "60", "11.75", "705.00"],
     ["LMS Platform Licensing and Related Services:DL-LMS1 Delivery", "1", "0.00", "0.00"]],
]


def _sow(tables=SOW_TABLES, term="10/18/2026-10/17/2027"):
    text = f"Prepared for:\nExample Co\nPRICING License Term: {term}\nDocument Ref: ABC"
    dates, _ = cc.find_expiration_dates(text, "sow.pdf")
    return cc.ParseResult(exp_dates=dates, source="sow.pdf", text=text, tables=tables)


def _estimate(tables=ESTIMATE_TABLES, url="Example.glsondemand.com"):
    text = (f"Estimate 16842\nSALES REP OD POC OD URL\nSam Rep plee@example.com {url}\n"
            "Renewal PD: 10/18/2026-10/17/2027")
    dates, _ = cc.find_expiration_dates(text, "est.pdf")
    return cc.ParseResult(exp_dates=dates, source="est.pdf", text=text, tables=tables)


def _portal(count):
    return cc.parse_portal_list(f"Slay The Scam 10781\t1\t{count}\tThirdPartyScorm")


def _fact(check, label, source):
    return next(f.value for f in check.facts if f.label == label and f.source == source)


# ---------------------------------------------------------------------------
# Order email
# ---------------------------------------------------------------------------

def test_email_fields_read_by_label_in_any_order():
    lines = EMAIL.splitlines()
    shuffled = "\n".join(lines[5:] + lines[3:5] + lines[:3])  # Billing POC's 2 lines stay together
    for text in (EMAIL, shuffled):
        e = oc.parse_order_email(text)
        assert e.fields["portal_url"] == "Example.glsondemand.com"
        assert e.fields["company"] == "Example Co (EXC)"
        assert e.fields["training_poc"] == "Pat Lee, plee@example.com"
        assert e.fields["license_dates"] == "10/18/2026-10/17/2027"


def test_email_label_stops_at_first_colon_and_unlabelled_line_continues_value():
    e = oc.parse_order_email(EMAIL)
    assert e.fields["opp_name"] == "Example Co: (EXC): Ess Bdl_76 users_2026"
    assert e.fields["billing_poc"].endswith("CC\nPat Lee, plee@example.com")


def test_email_unknown_label_kept_and_url_value_not_a_label():
    e = oc.parse_order_email("Notes: rush\nhttps://example.com/x")
    assert e.other == {"Notes": "rush\nhttps://example.com/x"}


# ---------------------------------------------------------------------------
# PDFs
# ---------------------------------------------------------------------------

def test_document_kind_from_content():
    assert oc.document_kind(_sow().text) == oc.SOW
    assert oc.document_kind(_estimate().text) == oc.ESTIMATE
    assert oc.document_kind("something else") == oc.UNKNOWN


def test_sku_lines_from_sow_and_estimate_tables():
    sow = oc.sku_lines(SOW_TABLES, "sow.pdf")
    est = oc.sku_lines(ESTIMATE_TABLES, "est.pdf")
    assert [(s.sku, s.users) for s in sow] == [("CL-SST-ESS", 60), ("DL-LMS1", 1)]
    assert [(s.sku, s.users) for s in est] == [("CL-SST-ESS", 60), ("DL-LMS1", 1)]
    assert est[1].description.startswith("LMS Platform Licensing")


def test_archive_rows_from_sow():
    rows = dict(oc.archive_rows(SOW_TABLES))
    assert rows["Requested Date of Archive"] == "10-15-2026"


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------

def test_matching_order_has_facts_and_no_warnings():
    r = oc.check_order(EMAIL.replace("76 users", "60 users"), [_sow(), _estimate()], _portal(60))
    assert r.warnings == []
    assert r.exp.date == "10/17/2027"
    assert _fact(r, "Order number", "est.pdf") == "16842"
    assert _fact(r, "Prepared for", "sow.pdf") == "Example Co"


def test_license_count_mismatch_lists_every_source():
    r = oc.check_order(EMAIL, [_sow(), _estimate()], _portal(76))
    assert len(r.warnings) == 1
    w = r.warnings[0]
    assert "60 (CL-SST-ESS, sow.pdf)" in w and "76 (portal)" in w and "76 (opp name)" in w


def test_demo_license_count_on_portal_is_ignored():
    r = oc.check_order(EMAIL.replace("76 users", "60 users"), [_sow(), _estimate()],
                       _portal(cc.DEMO_LICENSE_COUNT))
    assert r.warnings == []


def test_date_url_company_and_sku_disagreements_are_warned():
    est_tables = [ESTIMATE_TABLES[0][:2]]  # no DL-LMS1 line
    email = EMAIL.replace("Full Company Name: Example Co (EXC)", "Full Company Name: Other Inc")
    r = oc.check_order(email, [_sow(term="10/18/2026-10/16/2027"),
                               _estimate(tables=est_tables, url="other.glsondemand.com")])
    text = " ".join(r.warnings)
    assert r.exp.date == ""
    assert "different dates" in text
    assert "Portal URL differs" in text
    assert "Company name differs" in text
    assert "only on SOW: DL-LMS1" in text


def test_portal_url_forms():
    assert oc.portal_url("Princeton.glsondemand.com") == "princeton.glsondemand.com"
    assert oc.portal_url("https://princeton.glsondemand.com/") == "princeton.glsondemand.com"
    assert oc.portal_url("Vibrenthealth") == "vibrenthealth.glsondemand.com"
    assert oc.portal_url("NA SCORM") == oc.portal_url("N/A") == oc.portal_url("") == ""


def test_email_od_field_is_the_portal_subdomain():
    email = EMAIL.replace("URL: Example.glsondemand.com", "OD: example")
    assert oc.check_order(email, [_sow(), _estimate()]).portal_url == "example.glsondemand.com"


def test_estimate_od_poc_and_url_fill_gaps_in_the_email():
    r = oc.check_order("", [_sow(), _estimate(url="Example")])
    assert r.portal_url == "example.glsondemand.com"
    assert r.poc == "plee@example.com"
    assert r.company == "Example Co"   # SOW Prepared for


def test_order_type_and_hosting():
    sow = _sow()
    sow.text = "RENEWAL INVOICE\n" + sow.text
    r = oc.check_order(EMAIL, [sow, _estimate()])
    assert (r.order_type, r.hosting) == (oc.RENEWAL, oc.GLS_HOSTED)

    scorm_tables = [[["SKU", "Description", "Total Users"], ["DL-SCR", "Delivery: SCORM", "1"]]]
    new = _estimate(tables=scorm_tables, url="NA SCORM")
    new.text += "\nNew Client"
    r = oc.check_order("", [new])
    assert (r.order_type, r.hosting) == (oc.NEW_ACCOUNT, oc.SELF_HOSTED)


def test_only_the_first_license_line_per_pdf_is_the_license_count():
    tables = [[SOW_TABLES[0][0], SOW_TABLES[0][1],
               ["CL-BD-RBT", "Roles-Based Training bundle", "$86.00", "30", "$2,580.00"]]]
    r = oc.check_order(EMAIL.replace("76 users", "60 users"), [_sow(tables=tables), _estimate()])
    assert r.sow_users == 60 and not any("License counts" in w for w in r.warnings)


def test_sku_spelling_differences_are_not_warned():
    est = [[ESTIMATE_TABLES[0][0], ["CL-SST-ESS x", "60", "1", "1"],
            ["LMS Platform Licensing and Related Services:DL-LMS-1 Delivery", "1", "0", "0"]]]
    r = oc.check_order(EMAIL, [_sow(), _estimate(tables=est)])
    assert not any("SKUs differ" in w for w in r.warnings)


def test_missing_inputs_are_warned():
    r = oc.check_order("URL: x.glsondemand.com", [])
    text = " ".join(r.warnings)
    assert "missing: Opp name, Company" in text
    assert "No PandaDoc SOW" in text and "No QuickBooks estimate" in text
    assert "No Order Info email" in " ".join(oc.check_order("", []).warnings)
