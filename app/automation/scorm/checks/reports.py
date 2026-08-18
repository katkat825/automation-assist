"""
Output artifacts built from parsed data: ad-hoc search, the screen table,
and the manual QA checklist. These produce UI payloads rather than Sections
(global_search is the exception).
"""

import re
from typing import Optional

from ..parser import ScormData, StoryData
from .. import jaws_checks
from .models import Section, _screen_num



# ---------------------------------------------------------------------------
# 10. Global text search (called on demand from UI)
# ---------------------------------------------------------------------------

def global_search(data: ScormData, query: str) -> Section:
    sec = Section(f'Search Results: "{query}"')

    if not query.strip():
        sec.add("info", "Enter a search term above and click Search")
        return sec

    pattern = re.compile(re.escape(query.strip()), re.IGNORECASE)
    hit_count = 0

    for slide in data.slides:
        slide_hits = []
        for text in slide.texts:
            if pattern.search(text):
                # Highlight context
                slide_hits.append(text.strip()[:200])

        if slide_hits:
            loc = _screen_num(slide)
            for h in slide_hits:
                sec.add("info", f"[{loc}]  …{h}…")
                hit_count += 1

    if hit_count == 0:
        sec.add("info", f'No results found for "{query}"')
    else:
        sec.add("info", f"— {hit_count} match(es) found —")

    return sec


# ---------------------------------------------------------------------------
# 11. Screen / slide table
# ---------------------------------------------------------------------------

def build_screen_table(data: ScormData) -> list:
    """
    Returns a list of dicts with keys:
      screen_id          e.g. "m1s3"
      screen_number      e.g. "2.3"
      scene_title        e.g. "Module 1: Data and Device Security"
      slide_title        e.g. "What is Data Classification?"
      lms_id             e.g. "Slide5"
      in_menu            bool
      has_external_link  bool
      path               "Standard" | "Accessible" | ""
      acc_screen_number  str — accessible-path mirror number(s), e.g.
                         "5.13, 5.14" for a standard slide that gets split
                         into two accessible slides.  Empty if no match or
                         the row is already an accessible-only extra.

    Accessible-only slides (no standard-path counterpart) are appended as
    their own rows at the bottom so the user can see every screen in both
    paths.
    """
    path_map = jaws_checks.build_path_mapping(data)
    acc_numbers_for = path_map["acc_numbers_for"]
    path_for_slide = path_map["path_for_slide"]
    accessible_only = path_map["accessible_only_slides"]

    rows = []

    # Walk the top-level nav outline in order to assign module numbers.
    # Items with child links are module folders (scenes).
    # Leaf items (no children) that appear *before* the first multi-child
    # folder are standalone intro / landing screens and belong to module 0
    # (e.g. m0s1).  A *single-child* top-level folder appearing before any
    # multi-child folder is also treated as an intro scene — Storyline
    # courses commonly wrap a lone "Start" / "Introduction" slide in its
    # own scene folder, and those slides should still be m0, not m1.
    #
    # Important: Storyline can place an intro slide inside the same scene as
    # the first module (both share the same scene ID).  Scene-level mapping
    # would therefore overwrite the module-0 assignment with module 1.
    # To avoid that, pre-module screens are tracked by their *slide ID* so
    # the override applies per-slide, not per-scene.
    nav_scene_ids: list[str] = []
    pre_module_slide_ids: set[str] = set()
    seen_real_module = False

    for link in data.nav_outline:
        raw_id = link.get("slideid", "").lstrip("_player.")
        child_links = link.get("links", [])
        if child_links:
            if not seen_real_module and len(child_links) == 1:
                # Leading single-slide folder → treat as intro (m0).
                child_raw = child_links[0].get("slideid", "").lstrip("_player.")
                child_parts = child_raw.split(".")
                if len(child_parts) >= 2:
                    pre_module_slide_ids.add(child_parts[1])
            else:
                seen_real_module = True
                if "." not in raw_id:               # scene-level id (no slide part)
                    nav_scene_ids.append(raw_id)
        elif not seen_real_module:                   # leaf before any module → intro
            parts = raw_id.split(".")
            if len(parts) >= 2:
                pre_module_slide_ids.add(parts[1])  # track the slide ID specifically

    scene_module_num: dict[str, int] = {}

    module_counter = 1
    for sid in nav_scene_ids:
        scene_module_num[sid] = module_counter
        module_counter += 1

    module_slide_counter: dict[int, int] = {}
    for slide in data.slides:
        slide_key = jaws_checks.path_mapping_key(slide)
        # Accessible-path slides are surfaced via the acc_screen_number
        # column of their matched standard slide — don't list them as
        # their own rows here.
        if path_for_slide.get(slide_key) == "Accessible":
            continue
        if not slide.is_in_menu and slide.slide_id not in pre_module_slide_ids:
            # Slide is not on the nav menu (quiz questions, pre-test slides,
            # Storyline routing scenes, etc.).  Show it in the table so every
            # screen is visible, but without a module/slide number.
            screen_id = "m?s?"
        else:
            if slide.slide_id in pre_module_slide_ids:
                mod_num = 0
            else:
                mod_num = scene_module_num.get(slide.scene_id, slide.scene_number)
            module_slide_counter[mod_num] = module_slide_counter.get(mod_num, 0) + 1
            screen_id = f"m{mod_num}s{module_slide_counter[mod_num]}"
        screen_number = f"{slide.scene_number}.{slide.slide_number}"
        rows.append(
            {
                "screen_id": screen_id,
                "screen_number": screen_number,
                "scene_title": slide.scene_title or f"Scene {slide.scene_number}",
                "slide_title": slide.slide_title,
                "lms_id": slide.lms_id,
                "in_menu": slide.is_in_menu,
                "has_external_link": bool(slide.external_links),
                "path": path_for_slide.get(slide_key, ""),
                "acc_screen_number": ", ".join(acc_numbers_for.get(slide_key, [])),
            }
        )

    # Accessible-only slides — acc-path screens with no standard counterpart
    # (extra answer keys, split overflow, etc.).  Listed at the bottom so
    # the user can see every screen that exists in either path.
    for slide in accessible_only:
        slide_key = jaws_checks.path_mapping_key(slide)
        screen_number = f"{slide.scene_number}.{slide.slide_number}"
        rows.append(
            {
                "screen_id": "",
                "screen_number": "",
                "scene_title": slide.scene_title or f"Scene {slide.scene_number}",
                "slide_title": slide.slide_title,
                "lms_id": slide.lms_id,
                "in_menu": slide.is_in_menu,
                "has_external_link": bool(slide.external_links),
                "path": path_for_slide.get(slide_key, "Accessible"),
                "acc_screen_number": screen_number,
            }
        )

    return rows


# ---------------------------------------------------------------------------
# 12. Manual QA checklist generator
# ---------------------------------------------------------------------------

def build_manual_checklist(
    data: ScormData,
    story: Optional[StoryData],
    sections: list,
    dual_path: bool = False,
) -> str:
    lines = ["MANUAL QA CHECKLIST", "=" * 50, ""]
    # --- Miscellaneous ---
    lines += [
        "GENERAL",
        "-------",
        "[ ] Course title is correct in LMS and in the player header",
        "[ ] Course icon added to LMS",
        "[ ] All images/media load without broken asset errors",
        "[ ] Captions (if applicable) display and sync correctly",
        "[ ] Audio Transcript (if applicable) correctly displayed",
        "[ ] All hyperlinks open and point to correct destinations",
        "[ ] Course renders correctly in the required browsers",
    ]
    
    # --- Video speed control ---
    lines += [
        "PLAYBACK & CONTROLS",
        "-------------------",
        "[ ] Playback speed control is visible in the player bar",
        "[ ] Adjust speed to 0.5x, 1x, 1.5x, 2x — audio/video stays in sync",
        "[ ] Volume control works",
        "[ ] Seekbar is present; test seek forward and back",
        "",
    ]

    # --- Determine completion scenarios from scoring config ---
    lines += [
        "COMPLETION & SCORING",
        "--------------------",
    ]

    scoring_type = "quiz"  # default
    pass_pct = 80
    pass_status = "passed"
    fail_status_label = "incomplete"

    for cfg in data.scoring_configs:
        scoring_type = cfg.get("type", "quiz")
        pass_pct = cfg.get("passPercent", 80)
        pass_status = cfg.get("passStatus", "passed")
        fail_status_label = cfg.get("failStatus", "incomplete")

    if scoring_type == "quiz":
        lines += [
            f"[ ] PASS scenario (quiz score >= {pass_pct}%):",
            f"     [ ] completion_status = 'completed'",
            f"     [ ] success_status = '{pass_status}'",
            f"     [ ] Score reports correctly to LMS",
            f"[ ] FAIL scenario (quiz score < {pass_pct}%):",
            f"     [ ] completion_status = '{fail_status_label}'",
            f"     [ ] success_status = 'failed'",
            f"     [ ] Score reports correctly to LMS",
        ]
    elif scoring_type == "views":
        lines += [
            f"[ ] View-based completion (threshold: {pass_pct}% of slides):",
            f"     [ ] completion_status = 'completed' after viewing enough slides",
            f"     [ ] Verify suspend data saves viewed slides",
        ]

    # Add pretest scenarios if pretests exist
    pretest_scenes = [
        s for s in data.slides
        if "question" in s.slide_title.lower() and s.scene_number > 8
    ]
    if pretest_scenes:
        lines += [
            "",
            "[ ] PRETEST scenarios (pretests detected in course):",
            "     [ ] Pass all pretests → verify correct module skipping",
            "     [ ] Pass some pretests → verify partial skipping",
            "     [ ] Fail all pretests → verify all modules are required",
            "     [ ] Confirm completion status with each pretest outcome",
            "     [ ] Confirm related quiz questions automatically answered",
        ]

    lines += [""]

    # --- Suspend/resume ---
    lines += [
        "SUSPEND & RESUME",
        "----------------",
        "[ ] Navigate partway through the course, then exit",
        "[ ] Re-launch — course resumes at the correct screen",
        "",
    ]

    # --- Navigation ---
    flow = (story.navigation_flow if story else None) or data.navigation_flow
    lines += [
        "NAVIGATION",
        "----------",
    ]
    if flow == "restricted":
        lines += [
            "[ ] Navigation is RESTRICTED — confirm forward-only until viewed",
            "[ ] Confirm learner cannot skip ahead past unviewed content",
        ]
    elif flow == "free":
        lines += [
            "[ ] Navigation is FREE — confirm learner can jump to any slide",
        ]
    else:
        lines += [
            "[ ] Verify navigation flow (free or restricted) matches design spec",
        ]
    lines += [
        "[ ] Previous/Next buttons work correctly on every slide",
        "[ ] Menu items navigate to correct screens",
        "",
    ]

    # --- JAWS / Screen reader (dual-path courses only) ---
    if dual_path and jaws_checks.has_accessible_path(data):
        lines += [
            "JAWS / SCREEN READER (Accessible Path)",
            "--------------------------------------",
            "[ ] Launch the course and choose 'Accessible Path' on the first screen",
            "[ ] Confirm focus moves logically through every interactive element",
            "[ ] Confirm JAWS announces the screen title at the start of each slide",
            "[ ] Confirm acronyms are pronounced as intended (letter-by-letter where needed)",
            "[ ] Confirm correct/incorrect feedback is read aloud",
            "[ ] Confirm modal/dialog layers announce themselves and trap focus",
            "[ ] Confirm Tab and Shift+Tab move focus in both directions",
            "[ ] Confirm Enter/Space activates buttons and radio choices",
            "[ ] Confirm focus is visible on every focusable element (visual indicator)",
            "[ ] Confirm audio (if any) does not collide with the screen reader",
            "[ ] Confirm timing-based content can be paused or extended",
            "[ ] Confirm course can be completed end-to-end using only the keyboard",
            "",
        ]

    # --- Flagged items from static checks ---
    flagged = []
    for sec in sections:
        for item in sec.items:
            if item.level in ("fail", "warn"):
                flagged.append(f"  [ ] {sec.title}: {item.message}")

    if flagged:
        lines += [
            "ITEMS FLAGGED BY STATIC CHECK",
            "------------------------------",
        ] + flagged + [""]

    

    return "\n".join(lines)
