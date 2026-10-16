"""MS Attack Simulation phish results report (Data Analysis tab).

Rows are built by hand in the Defender export's column layout, so no client
data is needed. Real exports write "--" for "never happened".
"""

import csv

from app.automation.data_analysis import attack_sim as a

DRIVEBY_HEADERS = [
    "UserName", "UserMail", "Compromised", "EmailLinkClicked_TimeStamp(Compromised)",
    "EmailLinkClicked_Browser", "SuccessfullyDeliveredEmail_TimeStamp", "MessageRead_TimeStamp",
    "MessageDeleted_TimeStamp", "MessageReplied_TimeStamp", "MessageForwarded_TimeStamp",
    "OutOfOfficeDays", "PositiveReinforcementMessageDelivered_TimeStamp",
    "FailedToDeliverEmail_TimeStamp", "Last Simulation Activity", "Phishing Reported On",
    "Department",
]
CREDS_HEADERS = DRIVEBY_HEADERS[:3] + ["EmailLinkClicked_TimeStamp",
                                       "CredSupplied_TimeStamp(Compromised)"] + DRIVEBY_HEADERS[5:]

TS = "10/8/2026 4:06:29 PM"


def row(headers, email, **events):
    """A delivered, read, otherwise-untouched user; events set columns by name."""
    vals = {h: "--" for h in headers}
    vals.update({"UserName": email.split("@")[0], "UserMail": email, "Compromised": "No",
                 "SuccessfullyDeliveredEmail_TimeStamp": TS, "MessageRead_TimeStamp": TS,
                 "OutOfOfficeDays": "0", "Department": ""})
    vals.update(events)
    return [vals[h] for h in headers]


def table(headers, rows):
    return a.Table(list(headers), [list(r) for r in rows])


def sheet_of(headers, **events):
    t = table(headers, [row(headers, "u@x.com", **events)])
    return a.classify_phase1(t, t.rows[0])


# ---------------------------------------------------------------------------
# Phase 1 — fail-first placement
# ---------------------------------------------------------------------------

def test_click_and_report_is_a_failure():
    assert sheet_of(DRIVEBY_HEADERS, **{
        "Compromised": "Yes", "EmailLinkClicked_TimeStamp(Compromised)": TS,
        "Phishing Reported On": TS}) == a.SHEET_FAIL


def test_delivery_failure_wins_over_everything():
    h = DRIVEBY_HEADERS
    assert sheet_of(h, **{"FailedToDeliverEmail_TimeStamp": TS,
                          "SuccessfullyDeliveredEmail_TimeStamp": "--",
                          "MessageRead_TimeStamp": "--"}) == a.SHEET_DELIVERY_FAILED


def test_reply_is_a_failure_even_when_reported():
    assert sheet_of(DRIVEBY_HEADERS, **{"MessageReplied_TimeStamp": TS,
                                        "Phishing Reported On": TS}) == a.SHEET_FAIL


def test_forward_goes_to_forwards_even_when_also_reported_or_deleted():
    assert sheet_of(DRIVEBY_HEADERS, **{"MessageForwarded_TimeStamp": TS,
                                        "Phishing Reported On": TS,
                                        "MessageDeleted_TimeStamp": TS}) == a.SHEET_FORWARDS


def test_report_beats_delete_and_ignore_is_the_rest():
    h = DRIVEBY_HEADERS
    assert sheet_of(h, **{"Phishing Reported On": TS, "MessageDeleted_TimeStamp": TS}) == a.SHEET_REPORT
    assert sheet_of(h, **{"MessageDeleted_TimeStamp": TS}) == a.SHEET_DELETE
    assert sheet_of(h) == a.SHEET_IGNORE
    # reading the email is not an interaction
    assert sheet_of(h, **{"MessageRead_TimeStamp": TS}) == a.SHEET_IGNORE


def test_creds_click_without_creds_is_still_a_failure():
    assert sheet_of(CREDS_HEADERS, **{"EmailLinkClicked_TimeStamp": TS}) == a.SHEET_FAIL


def test_attachment_open_is_a_failure():
    h = ["UserName", "UserMail", "Compromised", "AttachmentOpened_TimeStamp(Compromised)",
         "MessageDeleted_TimeStamp", "MessageReplied_TimeStamp", "MessageForwarded_TimeStamp",
         "FailedToDeliverEmail_TimeStamp", "Phishing Reported On"]
    t = table(h, [["a", "a@x.com", "Yes", TS, "--", "--", "--", "--", TS]])
    assert a.detect_phish_type(h) == "Malware Attachment"
    assert a.classify_phase1(t, t.rows[0]) == a.SHEET_FAIL


def test_unknown_interaction_column_errs_towards_fail():
    h = DRIVEBY_HEADERS + ["OAuthConsentGranted_TimeStamp"]
    assert sheet_of(h, **{"OAuthConsentGranted_TimeStamp": TS}) == a.SHEET_FAIL


def test_unknown_action_column_answered_ignore_is_not_a_failure():
    h = DRIVEBY_HEADERS + ["OAuthConsentGranted_TimeStamp"]
    assert a.unknown_action_columns(h) == ["OAuthConsentGranted_TimeStamp"]
    t = table(h, [row(h, "u@x.com", **{"OAuthConsentGranted_TimeStamp": TS})])
    assert a.classify_phase1(t, t.rows[0], unknown_fail=set()) == a.SHEET_IGNORE
    assert a.classify_phase1(t, t.rows[0],
                             unknown_fail={"OAuthConsentGranted_TimeStamp"}) == a.SHEET_FAIL


def test_unknown_action_column_answered_fail_counts_as_a_click():
    h = DRIVEBY_HEADERS + ["OAuthConsentGranted_TimeStamp"]
    sheets = {a.SHEET_FAIL: table(h, [row(h, "u@x.com", **{"OAuthConsentGranted_TimeStamp": TS})])}
    assert a.summarize(sheets, {"OAuthConsentGranted_TimeStamp"}).click == 1
    assert a.summarize(sheets, set()).other == 1


def test_known_columns_are_never_asked_about():
    assert a.unknown_action_columns(DRIVEBY_HEADERS) == []
    assert a.unknown_action_columns(CREDS_HEADERS) == []


def test_message_timestamps_are_not_interactions():
    cols = a.interaction_columns(DRIVEBY_HEADERS)
    assert cols == ["EmailLinkClicked_TimeStamp(Compromised)"]


def test_phase_detection_by_columns():
    assert a.detect_phish_type(DRIVEBY_HEADERS) == "Drive-by URL"
    assert a.detect_phish_type(CREDS_HEADERS) == "Credential Harvest"


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

def test_report_name_parsing():
    assert a.parse_report_name("Oct 2026 Ev raw results") == ("Oct", 2026, "Ev")
    assert a.parse_report_name("Sept 2026 sp raw results") == ("Sept", 2026, "Sp")
    assert a.parse_report_name("AttackSimulationReport_123") is None


def test_phase1_paths():
    p = a.phase1_paths("/tmp", "Oct 2026 Ev")
    assert p["xlsx"].name == "Oct 2026 Ev Phish Results.xlsx"
    assert p["raw_csv"].name == "Oct 2026 Ev raw results.csv"


# ---------------------------------------------------------------------------
# Phase 1 end to end
# ---------------------------------------------------------------------------

def _write_raw(path, headers, rows, title_lines=()):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        for line in title_lines:
            w.writerow([line])
        w.writerow(headers)
        w.writerows(rows)


def _phase1_fixture(tmp_path):
    h = DRIVEBY_HEADERS
    rows = [
        row(h, "click@x.com", **{"Compromised": "Yes",
                                  "EmailLinkClicked_TimeStamp(Compromised)": TS}),
        row(h, "reply@x.com", **{"MessageReplied_TimeStamp": TS}),
        row(h, "fwd-ok@x.com", **{"MessageForwarded_TimeStamp": TS}),
        row(h, "fwd-bad@x.com", **{"MessageForwarded_TimeStamp": TS, "Phishing Reported On": TS}),
        row(h, "report@x.com", **{"Phishing Reported On": TS}),
        row(h, "delete@x.com", **{"MessageDeleted_TimeStamp": TS}),
        row(h, "ignore@x.com"),
        row(h, "bounce@x.com", **{"FailedToDeliverEmail_TimeStamp": TS,
                                   "SuccessfullyDeliveredEmail_TimeStamp": "--"}),
    ]
    src = tmp_path / "AttackSimUserReport.csv"
    _write_raw(src, h, rows, title_lines=["Simulation report"])
    return a.run_phase1(src, "Oct 2026 Ev")


def test_phase1_writes_workbook_forwards_csv_and_renames_download(tmp_path):
    res = _phase1_fixture(tmp_path)
    assert res.raw_csv.name == "Oct 2026 Ev raw results.csv" and res.raw_csv.exists()
    assert res.counts == {a.SHEET_REPORT: 1, a.SHEET_DELETE: 1, a.SHEET_IGNORE: 1,
                          a.SHEET_FORWARDS: 2, a.SHEET_FAIL: 2, a.SHEET_DELIVERY_FAILED: 1}
    sheets = a.read_workbook(res.xlsx)
    assert list(sheets) == a.PHASE1_SHEET_ORDER
    with open(res.forwards_csv, encoding="utf-8-sig") as f:
        got = list(csv.reader(f))
    assert got[0] == a.FORWARDS_CSV_COLUMNS
    assert [r[1] for r in got[1:]] == ["fwd-ok@x.com", "fwd-bad@x.com"]


def test_phase1_dates_become_real_excel_dates(tmp_path):
    res = _phase1_fixture(tmp_path)
    t = a.read_workbook(res.xlsx)[a.SHEET_IGNORE]
    from datetime import datetime
    assert isinstance(t.get(t.rows[0], "SuccessfullyDeliveredEmail_TimeStamp"), datetime)
    assert t.get(t.rows[0], "MessageDeleted_TimeStamp") == "--"
    assert t.get(t.rows[0], "OutOfOfficeDays") == 0


# ---------------------------------------------------------------------------
# Forward review results
# ---------------------------------------------------------------------------

FWD = {"jdoe1@domain.com", "jdoe2@domain.com", "jdoe3@domain.com"}


def test_forward_rows_with_added_columns_anywhere():
    rows = [
        ["Backup?", "UserName", "UserMail", "MessageForwarded_TimeStamp", "Phishing Reported On", "Notes"],
        ["Yes", "Doe, John", "jdoe1@domain.com", TS, "--", ""],
        ["no", "Doe, John", "JDOE2@domain.com", TS, TS, "sent to manager"],
        ["", "Doe, John", "jdoe3@domain.com", TS, "--", ""],
        ["Yes", "x", "stranger@domain.com", TS, "--", ""],
    ]
    p = a.parse_forward_rows(rows, FWD)
    assert p.decisions == {"jdoe1@domain.com": True, "jdoe2@domain.com": False}
    assert p.ambiguous == ["jdoe3@domain.com"]
    assert p.unknown_emails == ["stranger@domain.com"]


def test_forward_rows_conflicting_answers_left_for_manual():
    rows = [["UserMail", "Reported", "Wrong mailbox"], ["jdoe1@domain.com", "Yes", "Yes"],
            ["jdoe2@domain.com", "Yes", "No"]]
    p = a.parse_forward_rows(rows, FWD)
    assert p.decisions == {"jdoe1@domain.com": True}
    assert p.ambiguous == ["jdoe2@domain.com"]


def test_forward_text_from_email_body():
    text = ("Hi Kathleen,\n"
            "jdoe1@domain.com - yes\n"
            "Doe, John <jdoe2@domain.com>: did not report, forwarded to a coworker\n"
            "jdoe3@domain.com\n")
    p = a.parse_forward_text(text, FWD)
    assert p.decisions == {"jdoe1@domain.com": True, "jdoe2@domain.com": False}
    assert p.ambiguous == ["jdoe3@domain.com"]
    p = a.parse_forward_text(text, FWD, listed_means=True)
    assert p.decisions["jdoe3@domain.com"] is True


def test_yes_no_words():
    assert a.yes_no("Yes") is True and a.yes_no("N") is False
    assert a.yes_no("Not reported") is False
    assert a.yes_no("--") is None and a.yes_no(TS) is None


# ---------------------------------------------------------------------------
# Phase 2 — finish + history + summary
# ---------------------------------------------------------------------------

def _prev_workbook(path):
    h = DRIVEBY_HEADERS
    sheets = {
        a.SHEET_REPORT: table(h, [row(h, "click@x.com")]),            # passed last time
        "Fail x 1": table(h, [row(h, "reply@x.com")]),
        "Fail x 3": table(h, [row(h, "fwd-bad@x.com")]),
        a.SHEET_DELIVERY_FAILED: table(h, []),
    }
    a.write_workbook(path, sheets, list(sheets))


def test_phase2_end_to_end(tmp_path):
    res1 = _phase1_fixture(tmp_path)
    t, users = a.forwarded_users(res1.xlsx)
    assert [u["email"] for u in users] == ["fwd-ok@x.com", "fwd-bad@x.com"]
    prev = tmp_path / "Sept 2026 Ev Phish Results.xlsx"
    _prev_workbook(prev)

    res2 = a.finish_workbook(res1.xlsx, [True, False], prev)
    sheets = a.read_workbook(res2.path)
    assert a.SHEET_FORWARDS not in sheets and a.SHEET_FAIL not in sheets
    assert list(sheets) == [a.SHEET_REPORT, a.SHEET_DELETE, a.SHEET_IGNORE,
                            "Fail x 1", "Fail x 2", "Fail x 3", "Fail x 4", a.SHEET_DELIVERY_FAILED]

    def mails(name):
        tt = sheets[name]
        return sorted(tt.get(r, "UserMail") for r in tt.rows)

    assert mails(a.SHEET_REPORT) == ["fwd-ok@x.com", "report@x.com"]
    assert mails("Fail x 1") == ["click@x.com"]       # passed last time -> streak resets
    assert mails("Fail x 2") == ["reply@x.com"]
    assert mails("Fail x 3") == []
    assert mails("Fail x 4") == ["fwd-bad@x.com"]

    s = res2.summary
    assert (s.sent, s.passed, s.report, s.failed) == (7, 4, 2, 3)
    assert (s.reply, s.forward, s.click) == (1, 1, 1)
    assert s.streaks == {1: 1, 2: 1, 4: 1}


def test_phase2_refuses_undecided_forwards(tmp_path):
    res1 = _phase1_fixture(tmp_path)
    try:
        a.finish_workbook(res1.xlsx, [True, None], None)
    except ValueError as e:
        assert "forwarded user" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_phase2_without_history_is_all_fail_x_1(tmp_path):
    res1 = _phase1_fixture(tmp_path)
    res2 = a.finish_workbook(res1.xlsx, [True, True], None)
    assert res2.summary.streaks == {1: 2}
    assert any("No previous" in w for w in res2.warnings)


def test_prev_delivery_failed_is_flagged_for_a_look_back():
    h = DRIVEBY_HEADERS
    hist = a.read_history({a.SHEET_DELIVERY_FAILED: table(h, [row(h, "b@x.com")]),
                           "Pass - Ignore": table(h, [row(h, "c@x.com")])})
    assert hist.streak == {"b@x.com": a.PREV_DELIVERY_FAILED, "c@x.com": 0}


def test_summary_email_text_matches_the_template():
    s = a.Summary(phish_type="Credential Harvest", sent=3628, report=1337, delete=469,
                  ignore=1655, reply=1, forward=7, click=159, creds=87, other=0,
                  delivery_failed=0, streaks={})
    assert s.email_text() == (
        "Total Emails Sent: 3628\n"
        "\tPassed: 3461 (95%)\n"
        "\t\tReport: 1337 (37%)\n"
        "\t\tDelete: 469\n"
        "\t\tIgnore: 1655\n"
        "\tFail: 167 (5%)\n"
        "\t\tReply: 1\n"
        "\t\tForward: 7\n"
        "\t\tClick: 159\n"
        "\t\t\tClick-Only: 72\n"
        "\t\t\tCreds Supplied: 87"
    )


def test_drive_by_summary_has_no_click_breakdown():
    s = a.Summary(phish_type="Drive-by URL", sent=10, report=5, delete=0, ignore=4,
                  reply=0, forward=0, click=1, creds=0, other=0, delivery_failed=0, streaks={})
    assert "Click-Only" not in s.email_text()


def test_attachment_summary_says_attachment_opened():
    s = a.Summary(phish_type="Malware Attachment", sent=10, report=5, delete=0, ignore=4,
                  reply=0, forward=0, click=1, creds=0, other=0, delivery_failed=0, streaks={})
    text = s.email_text()
    assert "\t\tAttachment Opened: 1" in text and "Click" not in text
