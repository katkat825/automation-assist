"""Search, screen table and manual checklist.

These three feed the UI rather than the pass/fail report. The screen table
in particular is what gets pasted into conversations with course developers,
so its column set is a contract.
"""

from app.automation.scorm.checks.models import Section
from app.automation.scorm.checks.reports import (
    build_manual_checklist,
    build_screen_table,
    global_search,
)
from tests.conftest import make_data, make_slide

TABLE_COLUMNS = {
    "screen_id",
    "screen_number",
    "scene_title",
    "slide_title",
    "lms_id",
    "in_menu",
    "has_external_link",
    "path",
    "acc_screen_number",
}


def messages(section):
    return [i.message for i in section.items]


# --- global search --------------------------------------------------------

def test_search_finds_a_matching_slide():
    data = make_data([make_slide(texts=["Beware of phishing emails."])])
    assert any("phishing" in m for m in messages(global_search(data, "phishing")))


def test_search_is_case_insensitive():
    data = make_data([make_slide(texts=["Beware of Phishing emails."])])
    assert "1 match(es) found" in "\n".join(messages(global_search(data, "PHISHING")))


def test_search_reports_the_match_count():
    data = make_data([
        make_slide(slide_id="s1", texts=["phishing one"]),
        make_slide(slide_id="s2", texts=["phishing two"]),
    ])
    assert "2 match(es) found" in "\n".join(messages(global_search(data, "phishing")))


def test_search_reports_no_results_clearly():
    data = make_data([make_slide(texts=["nothing relevant here"])])
    assert any("No results found" in m for m in messages(global_search(data, "zebra")))


def test_empty_query_prompts_rather_than_searching():
    data = make_data([make_slide(texts=["anything"])])
    assert any("Enter a search term" in m for m in messages(global_search(data, "   ")))


def test_search_treats_the_query_literally():
    """Regex metacharacters in a query must not blow up or over-match."""
    data = make_data([make_slide(texts=["costs $5.00 (approx)"])])
    assert "1 match(es) found" in "\n".join(messages(global_search(data, "$5.00")))


def test_search_includes_the_screen_number():
    data = make_data([make_slide(scene_number=4, slide_number=2, texts=["findme"])])
    assert any("[4.2]" in m for m in messages(global_search(data, "findme")))


# --- screen table ---------------------------------------------------------

def test_screen_table_has_one_row_per_slide():
    data = make_data([make_slide(slide_id="s1"), make_slide(slide_id="s2")])
    assert len(build_screen_table(data)) == 2


def test_screen_table_rows_expose_the_expected_columns():
    rows = build_screen_table(make_data([make_slide()]))
    assert TABLE_COLUMNS.issubset(set(rows[0]))


def test_screen_table_carries_slide_titles_through():
    rows = build_screen_table(make_data([make_slide(slide_title="Spotting a Phish")]))
    assert rows[0]["slide_title"] == "Spotting a Phish"


def test_screen_table_falls_back_to_a_scene_number_label():
    rows = build_screen_table(make_data([make_slide(scene_title="", scene_number=3)]))
    assert rows[0]["scene_title"] == "Scene 3"


def test_screen_table_flags_external_links():
    data = make_data([make_slide(external_links=["https://example.com"])])
    assert build_screen_table(data)[0]["has_external_link"] is True


def test_screen_table_reports_no_external_links_when_absent():
    assert build_screen_table(make_data([make_slide()]))[0]["has_external_link"] is False


def test_screen_table_of_an_empty_course_is_empty():
    assert build_screen_table(make_data([])) == []


# --- manual checklist -----------------------------------------------------

def test_checklist_has_a_heading():
    text = build_manual_checklist(make_data([make_slide()]), None, [], dual_path=False)
    assert text.startswith("MANUAL QA CHECKLIST")


def test_checklist_is_returned_as_text():
    text = build_manual_checklist(make_data([make_slide()]), None, [], dual_path=False)
    assert isinstance(text, str) and len(text.splitlines()) > 3


def test_checklist_accepts_sections_from_the_run():
    sec = Section("Some Check")
    sec.add("fail", "something went wrong")
    text = build_manual_checklist(make_data([make_slide()]), None, [sec], dual_path=False)
    assert isinstance(text, str)


def test_checklist_handles_an_empty_course():
    assert build_manual_checklist(make_data([]), None, [], dual_path=False)
