"""run_all_checks orchestration and the package's public surface.

The UI imports exactly two names from this package and reads the Report by
attribute, so both are contracts worth pinning. The flag behaviour matters
too: dual_path gates the accessibility checks and non_english swaps
terminology for untranslated-English detection.
"""

from app.automation.scorm import checks
from app.automation.scorm.checks import run_all_checks
from tests.conftest import GOOD_SCORMDRIVER_JS, make_data, make_slide


def section_titles(report):
    return [s.title for s in report.sections]


def dual_path_data():
    return make_data([
        make_slide(slide_id="s1", slide_title="Passwords", scene_id="std",
                   scene_title="Module 1", is_in_menu=True),
        make_slide(slide_id="a1", slide_title="Passwords", scene_id="acc",
                   scene_title="Accessible Path", is_in_menu=False),
        make_slide(slide_id="a2", slide_title="Phishing", scene_id="acc",
                   scene_title="Accessible Path", is_in_menu=False),
    ])


# --- public surface -------------------------------------------------------

def test_ui_imports_still_resolve():
    """app/ui/scorm_qa_tab.py imports exactly these two names."""
    from app.automation.scorm.checks import global_search, run_all_checks  # noqa: F401


def test_package_exports_the_primitives_jaws_checks_needs():
    for name in ("Section", "CheckItem", "Report"):
        assert hasattr(checks, name)


def test_all_lists_only_names_that_exist():
    for name in checks.__all__:
        assert hasattr(checks, name), f"__all__ advertises missing {name}"


# --- report shape ---------------------------------------------------------

def test_report_exposes_the_attributes_the_ui_reads():
    report = run_all_checks(make_data([make_slide()]), None)
    for attr in ("sections", "manual_checklist", "screen_table_rows", "parse_errors"):
        assert hasattr(report, attr)


def test_parse_errors_are_carried_through():
    data = make_data([make_slide()], parse_errors=["bad slide 3"])
    assert "bad slide 3" in run_all_checks(data, None).parse_errors


def test_parse_errors_are_copied_not_aliased():
    """Mutating the report must not corrupt the parsed data."""
    data = make_data([make_slide()], parse_errors=["one"])
    report = run_all_checks(data, None)
    report.parse_errors.append("two")
    assert data.parse_errors == ["one"]


def test_screen_table_is_populated():
    report = run_all_checks(make_data([make_slide(), make_slide(slide_id="s2")]), None)
    assert len(report.screen_table_rows) == 2


def test_manual_checklist_is_generated():
    report = run_all_checks(make_data([make_slide()]), None)
    assert report.manual_checklist.startswith("MANUAL QA CHECKLIST")


# --- section composition --------------------------------------------------

def test_core_sections_are_always_present():
    titles = section_titles(run_all_checks(make_data([make_slide()]), None))
    assert "Course Overview" in titles
    assert "Course Paths" in titles
    assert "SCORM API Checks" in titles


def test_every_section_is_populated():
    report = run_all_checks(make_data([make_slide()], scormdriver_js=GOOD_SCORMDRIVER_JS), None)
    empty = [s.title for s in report.sections if not s.items]
    assert empty == [], f"sections with no items: {empty}"


def test_every_item_uses_a_known_level():
    report = run_all_checks(make_data([make_slide()], scormdriver_js=GOOD_SCORMDRIVER_JS), None)
    levels = {i.level for s in report.sections for i in s.items}
    assert levels <= {"pass", "warn", "fail", "info"}, f"unexpected levels: {levels}"


# --- flags ----------------------------------------------------------------

def test_non_english_swaps_terminology_for_untranslated_english():
    data = make_data([make_slide(texts=["Send an email."])])
    english = section_titles(run_all_checks(data, None, non_english=False))
    translated = section_titles(run_all_checks(data, None, non_english=True))

    assert "Terminology Consistency" in english
    assert "Terminology Consistency" not in translated
    assert "Untranslated English Text" in translated


# The sections jaws_checks.run_jaws_checks actually produces. Asserting on
# these (rather than on a section count) is what distinguishes "the checks
# ran" from "a placeholder saying they were skipped was appended".
JAWS_SECTIONS = {
    "Acronym Letter-Spacing (JAWS)",
    "Slide Titles (back-end vs on-screen)",
    "Keyboard Navigation & Tab Order",
    "Reading Order vs Visual Order",
}


def test_dual_path_runs_the_real_accessibility_checks():
    data = dual_path_data()
    titles = set(section_titles(run_all_checks(data, None, dual_path=True)))
    assert JAWS_SECTIONS.issubset(titles)
    # the skip placeholder must NOT be what we got instead
    assert "JAWS / Screen Reader Checks" not in titles


def test_accessibility_checks_do_not_run_unless_asked():
    data = dual_path_data()
    titles = set(section_titles(run_all_checks(data, None, dual_path=False)))
    assert titles.isdisjoint(JAWS_SECTIONS)


def test_dual_path_without_an_accessible_path_fails_as_a_probable_defect():
    """Dual-path is a deliberate selection, not an accidental tick.

    So if it's set but no Accessible Path can be found, the likeliest cause
    is a defect in the course (missing or unrecognizable accessible path).
    The runner surfaces that as a failure worth investigating rather than a
    soft, self-blaming skip.
    """
    data = make_data([make_slide(is_in_menu=True)])
    report = run_all_checks(data, None, dual_path=True)

    jaws = [s for s in report.sections if s.title == "JAWS / Screen Reader Checks"]
    assert len(jaws) == 1
    assert [i.level for i in jaws[0].items] == ["fail"]
    msg = jaws[0].items[0].message
    assert "no Accessible Path was detected" in msg
    assert "likely a course defect" in msg


def test_dual_path_without_an_accessible_path_still_runs_the_jaws_checks():
    """A detection miss must NOT silently skip the accessibility checks.

    Even when no Accessible Path is detected, the JAWS / screen-reader checks
    still run (against the whole course as a fallback) alongside the defect
    flag — because failing to isolate the accessible path is exactly when
    silently skipping the checks would be most dangerous.
    """
    data = make_data([make_slide(is_in_menu=True)])
    titles = set(section_titles(run_all_checks(data, None, dual_path=True)))

    # the probable-defect flag is present ...
    assert "JAWS / Screen Reader Checks" in titles
    # ... AND the real accessibility checks ran anyway
    assert JAWS_SECTIONS.issubset(titles)


def test_single_path_course_gets_no_jaws_section_by_default():
    data = make_data([make_slide(is_in_menu=True)])
    titles = section_titles(run_all_checks(data, None, dual_path=False))
    assert "JAWS / Screen Reader Checks" not in titles


def test_flags_are_independent():
    data = dual_path_data()
    titles = section_titles(run_all_checks(data, None, dual_path=True, non_english=True))
    assert "Untranslated English Text" in titles
    assert "Terminology Consistency" not in titles


# --- robustness -----------------------------------------------------------

def test_empty_course_does_not_crash():
    report = run_all_checks(make_data([]), None)
    assert report.sections


def test_slide_with_no_text_or_title_does_not_crash():
    data = make_data([make_slide(slide_title="", texts=[], lms_id="")])
    assert run_all_checks(data, None).sections
