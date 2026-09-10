"""
Orchestration: run every check and assemble the Report.
"""

from typing import Optional

from ..parser import ScormData, StoryData
from .. import jaws_checks
from .models import Report, Section
from .course_checks import (
    check_course_overview,
    check_course_paths,
    check_scorm_api,
    check_scoring,
    check_menu,
    check_questions,
    check_video_speed,
)
from .text_checks import check_duplicates, check_whitespace_and_spelling
from .navigation_checks import check_prev_navigation
from .language_checks import check_terminology, check_untranslated_english
from .reports import build_screen_table, build_manual_checklist



# ---------------------------------------------------------------------------
# Master runner
# ---------------------------------------------------------------------------

def run_all_checks(
    data: ScormData,
    story: Optional[StoryData] = None,
    dual_path: bool = False,
    non_english: bool = False,
) -> Report:
    report = Report(parse_errors=data.parse_errors[:])
    if story:
        report.parse_errors += story.parse_errors

    sections = [
        check_course_overview(data),
        check_course_paths(data),
        check_scorm_api(data),
        check_scoring(data),
        check_menu(data),
        check_questions(data),
        check_video_speed(data, story),
        check_duplicates(data, dual_path=dual_path),
        check_whitespace_and_spelling(data, skip_spelling=non_english),
        check_prev_navigation(data),
    ]

    # English-specific checks: only meaningful when the course IS in English.
    # For non-English courses, swap in the untranslated-English check instead.
    if non_english:
        sections.append(check_untranslated_english(data))
    else:
        sections.insert(5, check_terminology(data))

    # JAWS / screen-reader checks — only meaningful for dual-path courses.
    if dual_path:
        if jaws_checks.has_accessible_path(data):
            sections.extend(jaws_checks.run_jaws_checks(data))
        else:
            # Dual-path is a deliberate selection, so if it's set but we can
            # find nothing that looks like an Accessible Path, the likeliest
            # explanation is a defect in the course itself (the accessible
            # path is missing, or its scene isn't named / structured so we
            # can recognize it) — not an accidental checkbox tick. Surface
            # that prominently as a failure worth investigating.
            jaws_missing = Section("JAWS / Screen Reader Checks")
            jaws_missing.add(
                "fail",
                "Dual-path is selected but no Accessible Path was detected. This is "
                "likely a course defect: the Accessible Path may be missing, or its "
                "scene isn't recognizable (expected a scene titled 'Accessible Path', "
                "or an unlabeled scene that mirrors the standard-path modules). "
                "As a safeguard, the JAWS / screen-reader checks below were still run "
                "against the whole course rather than skipped — their locations aren't "
                "narrowed to an accessible path, so read them with that in mind. "
                "Investigate the course; if it is genuinely single-path, deselect Dual-path.",
            )
            sections.append(jaws_missing)
            # Run the checks anyway against every slide, so a detection miss
            # never means the accessibility checks are silently skipped.
            sections.extend(
                jaws_checks.run_jaws_checks(
                    data,
                    slides=data.slides,
                    scope_label="the whole course (Accessible Path not detected)",
                )
            )

    report.sections = sections
    report.screen_table_rows = build_screen_table(data)
    report.manual_checklist = build_manual_checklist(data, story, sections, dual_path=dual_path)

    return report
