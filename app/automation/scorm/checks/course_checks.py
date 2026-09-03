"""
Checks over course-level configuration and structure.

Each function reads a different part of ScormData and returns one Section.
They share no state beyond Section itself.
"""

import re
from typing import Optional

from ..parser import ScormData, StoryData
from .. import jaws_checks
from .models import Section, _screen_num



# ---------------------------------------------------------------------------
# 0. Course overview (title + estimated seat time)
# ---------------------------------------------------------------------------

def check_course_overview(data: ScormData) -> Section:
    sec = Section("Course Overview")

    # Title
    title = data.course_title or "Unknown"
    sec.add("info", f"Course title: {title}")

    # Slide count
    slide_count = len(data.slides)
    sec.add("info", f"Slides: {slide_count}")

    # Seat-time estimate from slide timeline durations
    total_ms = sum(s.duration_ms for s in data.slides)
    if total_ms > 0:
        total_min = total_ms / 60_000
        # Round to nearest 5 minutes for a cleaner estimate
        rounded = max(5, 5 * round(total_min / 5))
        sec.add("info", f"Estimated seat time: ~{rounded} min  (raw timeline total: {total_min:.1f} min)")
    else:
        # Fallback: rough 1.5 min/slide heuristic
        est = round(slide_count * 1.5)
        sec.add("info", f"Estimated seat time: ~{est} min  (heuristic — no timeline data)")

    return sec


def check_course_paths(data: ScormData) -> Section:
    """
    Report which navigation path(s) the course actually contains:
    Standard only, Accessible only, or Both.  Runs unconditionally so the
    user sees the path topology even when they haven't ticked "Dual-path".
    """
    sec = Section("Course Paths")
    path_map = jaws_checks.build_path_mapping(data)
    has_std = path_map["has_standard"]
    has_acc = path_map["has_accessible"]

    if has_std and has_acc:
        sec.add("info", "Course contains BOTH a Standard Path and an Accessible Path")
        acc_only = len(path_map["accessible_only_slides"])
        matched_std = len(path_map["acc_numbers_for"])
        sec.add(
            "info",
            f"Accessible-path slides mapped to {matched_std} standard-path slide(s); "
            f"{acc_only} accessible-only slide(s) with no standard counterpart",
        )
    elif has_std:
        sec.add("info", "Course contains a Standard Path only (no Accessible Path detected)")
    elif has_acc:
        sec.add("info", "Course contains an Accessible Path only (no Standard Path detected)")
    else:
        sec.add("warn", "No menu-bearing scenes found — unable to classify course path")

    return sec


# ---------------------------------------------------------------------------
# 1. SCORM API checks
# ---------------------------------------------------------------------------

def check_scorm_api(data: ScormData) -> Section:
    sec = Section("SCORM API Checks")
    js = data.scormdriver_js

    if not js:
        sec.add("fail", "scormdriver.js not found — cannot verify SCORM API calls")
        return sec

    # Completion
    if 'SetValue.*cmi.completion_status' in js or '"cmi.completion_status"' in js:
        sec.add("pass", "cmi.completion_status is set")
    else:
        sec.add("fail", "cmi.completion_status not found in scormdriver.js")

    # Score
    if '"cmi.score.scaled"' in js:
        sec.add("pass", "cmi.score.scaled is set")
    else:
        sec.add("warn", "cmi.score.scaled not found — score may not report correctly")

    if '"cmi.score.raw"' in js:
        sec.add("pass", "cmi.score.raw is set")

    # Suspend data
    if '"cmi.suspend_data"' in js:
        sec.add("pass", "cmi.suspend_data is set (bookmarking supported)")
    else:
        sec.add("fail", "cmi.suspend_data not found — resume/bookmarking may not work")

    # Commit
    if "CallCommit" in js or "LMSCommit" in js or "SCORM2004_CommitData" in js:
        sec.add("pass", "Commit is called")
    else:
        sec.add("fail", "No Commit call found in scormdriver.js")

    # Exit type
    m = re.search(r"var\s+DEFAULT_EXIT_TYPE\s*=\s*(\w+)", js)
    if m:
        exit_type = m.group(1)
        if "SUSPEND" in exit_type.upper():
            sec.add("pass", f"Default exit type: {exit_type} (suspend — correct for bookmarking)")
        else:
            sec.add("warn", f"Default exit type: {exit_type} — expected EXIT_TYPE_SUSPEND for bookmarking")

    # Forced commit interval
    m2 = re.search(r'var\s+FORCED_COMMIT_TIME\s*=\s*"?(\d+)"?', js)
    if m2:
        ms = int(m2.group(1))
        if ms == 0:
            sec.add("warn", "FORCED_COMMIT_TIME = 0 (auto-commit disabled — data only saved on exit)")
        else:
            sec.add("info", f"FORCED_COMMIT_TIME = {ms} ms ({ms // 1000}s auto-commit interval)")

    # success_status
    if '"cmi.success_status"' in js:
        sec.add("pass", "cmi.success_status is set")
    else:
        sec.add("warn", "cmi.success_status not found — pass/fail status may not report")

    return sec


# ---------------------------------------------------------------------------
# 2. Scoring / completion config
# ---------------------------------------------------------------------------

def check_scoring(data: ScormData) -> Section:
    sec = Section("Scoring & Completion Config")

    if not data.scoring_configs:
        sec.add("warn", "No scoring config found in data.js")
        return sec

    for cfg in data.scoring_configs:
        scoring_type = cfg.get("type", "?")
        pass_pct = cfg.get("passPercent", "?")
        pass_status = cfg.get("passStatus", "?")
        fail_status = cfg.get("failStatus", "?")
        slides_viewed_mode = cfg.get("slidesViewedMode", "?")
        view_threshold = cfg.get("viewThreshold", "?")

        sec.add("info", f"Scoring type: {scoring_type}")
        sec.add("info", f"Pass threshold: {pass_pct}%")
        sec.add("info", f"On pass → completion_status / success_status: {fail_status} / {pass_status}")
        sec.add("info", f"On fail → completion_status: {fail_status}")
        sec.add("info", f"Slides-viewed mode: {slides_viewed_mode} (view threshold: {view_threshold}%)")

    for qcfg in data.quiz_configs:
        q_pass = qcfg.get("passPercent", "?")
        q_type = "survey" if qcfg.get("issurvey") else "graded quiz"
        q_name = qcfg.get("lmstext", "")
        sec.add("info", f"{q_type} — pass score: {q_pass}%  ({q_name})")

    return sec


# ---------------------------------------------------------------------------
# 3. Menu validation
# ---------------------------------------------------------------------------

def check_menu(data: ScormData) -> Section:
    sec = Section("Menu Validation")

    if not data.nav_outline:
        sec.add("warn", "No menu/navigation outline found in frame.js")
        return sec

    mismatch_count = 0
    orphan_count = 0

    def walk(links, parent_display=""):
        nonlocal mismatch_count, orphan_count
        for link in links:
            display = link.get("displaytext", "").strip()
            slide_title = link.get("slidetitle", "").strip()
            child_links = link.get("links", [])

            if slide_title and display:
                if display != slide_title:
                    sec.add(
                        "warn",
                        f'Menu label ≠ slide title: menu="{display}" | slide="{slide_title}"',
                    )
                    mismatch_count += 1

            # Orphan: a top-level menu entry with no children
            if not child_links and not slide_title:
                orphan_count += 1

            walk(child_links, display)

    walk(data.nav_outline)

    if mismatch_count == 0:
        sec.add("pass", "All menu labels match their slide titles")
    if orphan_count > 0:
        sec.add("warn", f"{orphan_count} menu item(s) have no visible slide title")

    # Navigation flow
    if data.navigation_flow:
        sec.add("info", f"Navigation flow: {data.navigation_flow}")
    elif data.frame_data:
        # Try to find flow in controlOptions
        nav_opts = (
            data.frame_data.get("controlOptions", {})
            .get("menuoptions", {})
        )
        flow = nav_opts.get("flow")
        if flow:
            sec.add("info", f"Navigation flow (from controlOptions): {flow}")

    return sec


# ---------------------------------------------------------------------------
# 5. Questions & answers
# ---------------------------------------------------------------------------

# Leading answer-choice label: a single letter or a 1-2 digit number followed
# by "." or ")" and a space — "A. ", "b) ", "1. ", "10) ".
_CHOICE_LABEL_RE = re.compile(r'^\s*([A-Za-z]|\d{1,2})[\.\)]\s')


def _choice_label_key(text: str):
    """Return a sort key for a choice's leading label, or None if it has none.

    Letters sort as ('a', 'A'); numbers as ('n', <int>). The family tag keeps
    a course that mixes styles from being treated as an ordered set.
    """
    m = _CHOICE_LABEL_RE.match(text or "")
    if not m:
        return None
    tok = m.group(1)
    if tok.isdigit():
        return ("n", int(tok))
    return ("a", tok.upper())


def order_choices_by_label(interaction):
    """
    Approximate the on-screen order of a question's answer choices from their
    leading labels.

    The data file stores choices in authoring order, which is not always the
    order shown on screen. Most courses letter their options (A, B, C ...), so
    when every choice carries a clean, complete, non-repeating label sequence
    we can sort by it and match the screen. When they don't, we can't tell —
    so we return the original order and report it as unverified.

    Returns (ordered_choices, is_verified).
    """
    choices = interaction.choices
    keys = [_choice_label_key(c.text) for c in choices]

    if len(choices) < 2 or any(k is None for k in keys):
        return choices, False
    if len({k[0] for k in keys}) != 1:          # mixed letter/number styles
        return choices, False

    values = [k[1] for k in keys]
    if len(set(values)) != len(values):          # duplicate labels
        return choices, False

    if keys[0][0] == "a":
        expected = [chr(ord("A") + i) for i in range(len(values))]  # A, B, C ...
    else:
        start = min(values)
        expected = list(range(start, start + len(values)))          # n, n+1 ...
    if sorted(values) != expected:               # gap, or doesn't start clean
        return choices, False

    ordered = [c for _, c in sorted(zip(keys, choices), key=lambda p: p[0][1])]
    return ordered, True


def check_questions(data: ScormData) -> Section:
    sec = Section("Questions & Correct Answers")

    q_count = 0
    unverified = 0
    for slide in data.slides:
        for ia in slide.interactions:
            if ia.is_survey:
                continue
            q_count += 1

            loc = _screen_num(slide)
            ordered, verified = order_choices_by_label(ia)

            # True/False order is trivial and rarely mislabelled, so we don't
            # nag about it; every other unlabelled question gets a marker so
            # the reviewer knows to confirm the on-screen order themselves.
            if verified or ia.question_type == "truefalse":
                sec.add("info", f"[{loc}]  ({ia.question_type})")
            else:
                unverified += 1
                sec.add(
                    "warn",
                    f"[{loc}]  ({ia.question_type})  * ORDER UNVERIFIED — options "
                    "have no sequential A/B/C labels, so the order below may not "
                    "match the screen; confirm manually",
                )
            sec.add("info", f"  Q: {ia.question_text[:300]}")

            for choice in ordered:
                marker = "✓" if choice.id in ia.correct_choice_ids else " "
                sec.add("info", f"  [{marker}] {choice.text[:200]}")

    if q_count == 0:
        sec.add("info", "No graded questions found in the course data")
    else:
        sec.add("info", f"Total graded questions: {q_count}")
        if unverified:
            sec.add(
                "info",
                f"{unverified} question(s) marked ORDER UNVERIFIED — answers "
                "shown in stored order, which may differ from the screen",
            )

    return sec


# ---------------------------------------------------------------------------
# 6. Video speed control
# ---------------------------------------------------------------------------

def check_video_speed(data: ScormData, story: Optional[StoryData] = None) -> Section:
    sec = Section("Video Playback Speed Control")

    # Prefer .story file setting (authoritative), fall back to frame.js
    enabled = None
    source = ""

    if story and story.speed_control_enabled is not None:
        enabled = story.speed_control_enabled
        source = "playerProps.xml (.story file)"
    elif data.speed_control_enabled is not None:
        enabled = data.speed_control_enabled
        source = "frame.js (SCORM zip)"

    if enabled is True:
        sec.add("pass", f"Playback speed control is ENABLED ({source})")
        sec.add("info", "Verify manually: speed control should appear in the player controls bar")
    elif enabled is False:
        sec.add("fail", f"Playback speed control is DISABLED ({source})")
        sec.add("info", "Flag for manual verification — learners cannot adjust playback speed")
    else:
        sec.add("warn", "Could not determine speed control setting — check manually")

    return sec
