"""
Static QA checks on parsed SCORM / Storyline data.

All checks accept a ScormData (required) and optional StoryData.
Each returns a Section.  run_all_checks() orchestrates everything and
returns a Report.

This package was split out of a single 1,686-line checks.py. The public
surface is re-exported here, so existing imports keep working unchanged:

    from app.automation.scorm.checks import run_all_checks, global_search

Layout:
    models.py          Section / CheckItem / Report primitives
    wordlists.py       all vocabulary, regexes and thresholds (data only)
    course_checks.py   course config and structure checks
    text_checks.py     duplicates, spacing, spelling
    language_checks.py terminology and untranslated English
    reports.py         search, screen table, manual checklist
    runner.py          run_all_checks orchestration
"""

from .models import CheckItem, Section, Report, _screen_num
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
from .reports import global_search, build_screen_table, build_manual_checklist
from .runner import run_all_checks

__all__ = [
    "CheckItem", "Section", "Report",
    "check_course_overview", "check_course_paths", "check_scorm_api",
    "check_scoring", "check_menu", "check_questions", "check_video_speed",
    "check_duplicates", "check_whitespace_and_spelling",
    "check_prev_navigation",
    "check_terminology", "check_untranslated_english",
    "global_search", "build_screen_table", "build_manual_checklist",
    "run_all_checks",
]
