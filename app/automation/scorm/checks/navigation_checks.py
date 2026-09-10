"""
Previous-button navigation checks.

Two independent problems, both authored at creation time and therefore
statically checkable:

1. The player Previous button. Its per-slide target is stored in the slide's
   ``ActGrpOnPrevButtonClick`` action group. Per GLS standards the Previous
   button must move to the immediately-preceding screen in presentation order.
   A fixed target pointing anywhere else is a hard failure; a branching or
   otherwise unreadable target is flagged for manual review rather than guessed.

2. Custom Back buttons wired to ``history_prev`` (jump to the last-viewed slide)
   instead of a fixed target. Per standards no Back/Previous button should use
   last-viewed navigation. Only base-layer buttons are flagged here; buttons on
   dialog / lightbox layers are almost always legitimate "return to where you
   were" controls, so they are left to the runtime / manual pass to avoid noise.

Presentation order is (scene_number, slide_number). The first screen has no
predecessor and is skipped for the target comparison.

Like every other check here, this one never reports a guaranteed pass: a clean
result means nothing contradicted the standard statically, not that navigation
was exercised.
"""

from .models import Section, _screen_num


def check_prev_navigation(data) -> Section:
    sec = Section("Previous Button Navigation")

    slides = sorted(data.slides, key=lambda s: (s.scene_number, s.slide_number))
    if not slides:
        sec.add("warn", "No slides found — Previous-button navigation not checked")
        return sec

    by_id = {s.slide_id: s for s in slides}
    fails = warns = 0

    # --- 1. player Previous button target --------------------------------
    for i, slide in enumerate(slides):
        loc = _screen_num(slide)
        expected = slides[i - 1] if i > 0 else None
        kind = slide.prev_nav_kind

        # last-viewed navigation on the player Prev button is wrong wherever
        # it appears, including on the first screen.
        if kind == "history_prev":
            sec.add(
                "fail",
                f"{loc} '{slide.slide_title}': Previous button uses last-viewed "
                f"navigation (history) instead of a fixed target",
            )
            fails += 1
            continue

        if i == 0:
            continue  # first screen has no predecessor to compare against

        if kind == "gotoplay":
            target = by_id.get(slide.prev_nav_target)
            if target is expected:
                continue  # correct — reported quietly
            tgt_loc = _screen_num(target) if target else (slide.prev_nav_target or "?")
            tgt_title = f" '{target.slide_title}'" if target else ""
            sec.add(
                "fail",
                f"{loc} '{slide.slide_title}': Previous button points to "
                f"{tgt_loc}{tgt_title} but the preceding screen is "
                f"{_screen_num(expected)} '{expected.slide_title}'",
            )
            fails += 1
        elif kind in ("conditional", "unresolved"):
            sec.add(
                "warn",
                f"{loc} '{slide.slide_title}': Previous button target could not be "
                f"resolved statically (branching or custom logic) — review manually",
            )
            warns += 1
        elif kind == "none":
            # No explicit override: the player uses its default
            # previous-in-order behavior, which is correct by construction.
            pass
        else:  # "" — slide navigation data could not be parsed
            sec.add(
                "warn",
                f"{loc} '{slide.slide_title}': slide navigation data unavailable — "
                f"Previous button not checked",
            )
            warns += 1

    # --- 2. custom base-layer Back buttons using history_prev ------------
    hist_slides = [s for s in slides if s.history_prev_buttons > 0]
    hist_total = sum(s.history_prev_buttons for s in hist_slides)
    for s in hist_slides:
        n = s.history_prev_buttons
        sec.add(
            "fail",
            f"{_screen_num(s)} '{s.slide_title}': {n} base-layer Back button(s) use "
            f"last-viewed navigation (history) instead of a fixed target",
        )
        fails += n

    # --- summary ---------------------------------------------------------
    if fails == 0 and warns == 0:
        sec.add("pass", "All resolvable Previous buttons point to the preceding screen")
    else:
        parts = []
        if fails:
            parts.append(f"{fails} failure(s)")
        if warns:
            parts.append(f"{warns} needing manual review")
        sec.add("info", "Previous-button navigation: " + "; ".join(parts))
        if hist_total:
            sec.add(
                "info",
                f"(dialog/lightbox 'return' buttons using history are excluded by "
                f"design; only base-layer Back buttons are flagged)",
            )

    return sec
