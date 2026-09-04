"""
Per-screen QA companion data.

Builds a single lookup — keyed by the runtime slide id (the same id the DOM
exposes as `.slide.cs-<id>` and that the driver matches on) — so a live panel
can show, for whatever screen the reviewer is on:

  * the screen number (scene.slide) and screen id (m#s#) and raw slide id,
  * the static-check findings that pertain to that screen,
  * the questions on that screen with their correct answers in on-screen order.

Nothing here drives a browser or renders UI; it just assembles the data. The
harness launcher embeds the JSON and renders the panel (see harness.py).
"""

from __future__ import annotations

import re
from typing import Optional

from .parser import ScormData, StoryData
from .checks.runner import run_all_checks
from .checks.course_checks import order_choices_by_label
from .checks.reports import build_screen_table


# Sections whose items are shown elsewhere (Q&A has its own panel block) or are
# course-level rather than per-screen — skip them when bucketing per screen.
_SKIP_SECTIONS = {"Questions & Correct Answers"}


def _screen_number_regex(valid_numbers):
    """Regex that finds any *valid* screen number in a message, whether it is
    bracketed ("[2.3]") or bare ("… — 2.3; 5.1"). Checks aren't consistent
    about which they use, so we match the actual screen numbers rather than a
    fixed format. Boundaries keep "2.3" from matching inside "12.34" or
    "2.3.4". Returns None when there are no screen numbers to match."""
    nums = [n for n in valid_numbers if n]
    if not nums:
        return None
    # longest first so "10.2" is tried before "0.2"
    alt = "|".join(re.escape(n) for n in sorted(nums, key=len, reverse=True))
    return re.compile(r"(?<![\d.])(" + alt + r")(?![\d.])")


def _slide_screen_number(slide) -> str:
    return f"{slide.scene_number}.{slide.slide_number}"


def _jaws_label(slide) -> str:
    """Mirror jaws_checks._slide_label: 'scene / slide_title'."""
    scene = slide.scene_title or f"Scene {slide.scene_number}"
    return f"{scene} / {slide.slide_title}"


def _questions_for_slide(slide) -> list:
    """Question + correct answers for one slide, choices in on-screen order."""
    out = []
    for ia in slide.interactions:
        ordered, verified = order_choices_by_label(ia)
        out.append({
            "question": ia.question_text,
            "type": ia.question_type,
            "is_survey": ia.is_survey,
            "order_verified": verified,
            "choices": [
                {"text": c.text, "correct": c.id in ia.correct_choice_ids}
                for c in ordered
            ],
        })
    return out


def build_companion_data(
    data: ScormData,
    story: Optional[StoryData] = None,
    dual_path: bool = False,
    non_english: bool = False,
) -> dict:
    """Assemble the per-screen companion index. Returns a JSON-ready dict:

        {
          "course_title": str,
          "dual_path": bool,
          "slides": {
             "<slide_id>": {
                "slide_id", "screen_number", "screen_id", "lms_id",
                "scene_title", "slide_title", "path", "acc_screen_number",
                "checks": [ {"section", "level", "message"}, ... ],
                "questions": [ {...}, ... ],
             }, ...
          },
        }
    """
    report = run_all_checks(data, story, dual_path=dual_path, non_english=non_english)

    # --- screen_id (m#s#) and path, joined from the screen table by lms_id ---
    table_by_lms: dict = {}
    for row in report.screen_table_rows:
        if row.get("lms_id"):
            table_by_lms[row["lms_id"]] = row

    # --- lookups from a finding message back to the slide(s) it references ---
    num_to_sid = {_slide_screen_number(s): s.slide_id for s in data.slides}
    label_to_sid = {_jaws_label(s): s.slide_id for s in data.slides}
    num_regex = _screen_number_regex(num_to_sid.keys())

    def slides_for_message(msg: str) -> set:
        sids = set()
        if num_regex is not None:                       # numeric-tagged checks
            for num in num_regex.findall(msg):
                if num in num_to_sid:
                    sids.add(num_to_sid[num])
        if not sids:                                    # jaws checks use labels
            for label, sid in label_to_sid.items():
                if label and label in msg:
                    sids.add(sid)
        return sids

    # --- seed one entry per slide ---
    slides: dict = {}
    for s in data.slides:
        row = table_by_lms.get(s.lms_id, {})
        slides[s.slide_id] = {
            "slide_id": s.slide_id,
            "screen_number": _slide_screen_number(s),
            "screen_id": row.get("screen_id", ""),
            "lms_id": s.lms_id,
            "scene_title": s.scene_title or f"Scene {s.scene_number}",
            "slide_title": s.slide_title,
            "path": row.get("path", ""),
            "acc_screen_number": row.get("acc_screen_number", ""),
            "checks": [],
            "questions": _questions_for_slide(s),
        }

    # --- bucket every static finding under the screen(s) it names ---
    for sec in report.sections:
        if sec.title in _SKIP_SECTIONS:
            continue
        for item in sec.items:
            if item.level not in ("warn", "fail", "info"):
                continue
            for sid in slides_for_message(item.message):
                if sid in slides:
                    slides[sid]["checks"].append({
                        "section": sec.title,
                        "level": item.level,
                        "message": item.message,
                    })

    return {
        "course_title": data.course_title,
        "dual_path": dual_path,
        "slides": slides,
    }
