"""Scenario planner: static -> static. Enumerates executable scenarios from the
runtime model. Emits BLOCKED scenarios (with reasons) rather than guessing, and
always reports what it did NOT enumerate."""
from __future__ import annotations


def _question_slides_on(route, rm):
    out = []
    by_slide = {}
    for q in rm["questions"]:
        by_slide.setdefault(q["slide_id"], []).append(q)
    for sid in route:
        if sid in by_slide:
            out.append((sid, by_slide[sid]))
    return out


def _answer_plan(route, rm, mode):
    """mode: 'correct' | 'incorrect'. Returns per-slide answer plans, skipping
    non-drivable questions (they stay on the human checklist)."""
    plans, skipped = [], []
    for sid, qs in _question_slides_on(route, rm):
        for q in qs:
            if q["is_survey"]:
                continue
            if not q["drivable"]:
                skipped.append({"interaction": q["lms_id"], "slide": sid,
                                "reason": q["not_drivable_reason"]})
                continue
            if q["qtype"] == "matching" and q.get("pairs"):
                pairs = [dict(pr) for pr in q["pairs"]]
                if mode == "incorrect":
                    # rotate drop targets by one -> every pair wrong (there are
                    # always >=2 distinct targets in a graded matching question)
                    drops = [pr["drop_obj"] for pr in pairs]
                    dtexts = [pr["drop_text"] for pr in pairs]
                    rot = 1
                    for i2, pr in enumerate(pairs):
                        pr["drop_obj"] = drops[(i2 + rot) % len(drops)]
                        pr["drop_text"] = dtexts[(i2 + rot) % len(dtexts)]
                        # guard: rotation may map same->same when targets repeat
                        if pr["drop_obj"] == q["pairs"][i2]["drop_obj"]:
                            alt = next((d for d in q["drop_objs"]
                                        if d != q["pairs"][i2]["drop_obj"]), None)
                            if alt:
                                pr["drop_obj"] = alt
                                pr["drop_text"] = ""
                plans.append({
                    "slide_id": sid,
                    "interaction_id": q["interaction_id"],
                    "lms_id": q["lms_id"],
                    "mode": mode,
                    "type": "dragdrop",
                    "pairs": pairs,
                    "click_objs": [pr["drag_obj"] for pr in pairs],
                    "click_texts_expected": {pr["drag_obj"]: pr["drag_text"]
                                             for pr in pairs},
                    "submit_obj": q["submit_obj"],
                    "expect_result": "correct" if mode == "correct" else "incorrect",
                })
                continue
            correct = set(q["correct_choice_ids"])
            chosen = [c for c in q["choices"] if
                      (c["choice_id"] in correct) == (mode == "correct")]
            if mode == "incorrect":
                wrong = [c for c in q["choices"] if c["choice_id"] not in correct]
                if q["qtype"] in ("multiplechoice", "truefalse"):
                    chosen = wrong[:1]          # single-select: one wrong answer
                elif not chosen:
                    chosen = wrong or q["choices"][:1]
            if mode == "correct" and not chosen:
                skipped.append({"interaction": q["lms_id"], "slide": sid,
                                "reason": "no correct choices in key"})
                continue
            plans.append({
                "slide_id": sid,
                "interaction_id": q["interaction_id"],   # unique internal id
                "lms_id": q["lms_id"],                   # for cmi id matching
                "mode": mode,
                "click_objs": [c["obj_id"] for c in chosen],
                "click_texts_expected": {c["obj_id"]: c["text"] for c in chosen},
                "submit_obj": q["submit_obj"],
                "expect_result": "correct" if mode == "correct" else "incorrect",
            })
    return plans, skipped


def plan(rm: dict) -> dict:
    route = rm["routes"]["forward"]
    scenarios, not_enumerated = [], []

    pass_pct = rm.get("pass_percent", 80)
    # Storyline passStatus 'pass' => SCORM2004 passed + completed
    pass_expect = {"success_status": "passed", "completion_status": "completed",
                   "score_scaled_gte": pass_pct / 100.0, "commit_seen": True}
    fail_status = rm.get("fail_status", "incomplete")
    # Storyline 'Passed/Incomplete' reporting (failStatus=incomplete) leaves
    # success_status untouched on failure; 'Passed/Failed' sets it to failed.
    fail_expect = {"success_status": ("unknown" if fail_status == "incomplete"
                                       else "failed"),
                   "completion_status": ("incomplete" if fail_status == "incomplete"
                                          else "completed"),
                   "score_scaled_lt": pass_pct / 100.0, "commit_seen": True}

    # The fail route ends at the slide after the last graded question (the
    # results slide): by design a failed quiz offers Retake, not Continue —
    # driving past results is not part of a failed run.
    q_slides = {q["slide_id"] for q in rm["questions"] if not q["is_survey"]}
    last_q_idx = max((i for i, sid in enumerate(route) if sid in q_slides),
                     default=len(route) - 1)
    fail_route = route[:min(last_q_idx + 2, len(route))]

    for sid, mode, expect, r in (("quiz_pass", "correct", pass_expect, route),
                                 ("quiz_fail", "incorrect", fail_expect, fail_route)):
        answers, skipped = _answer_plan(r, rm, mode)
        scenarios.append({
            "id": sid,
            "description": f"Traverse forward route answering every drivable graded "
                           f"question {mode}ly; verify completion per scoring config",
            "route": r,
            "answer_plan": answers,
            "sweep": sid == "quiz_pass",   # full interaction sweep once; fail run is quiz-focused
            "expected": expect,
            "skipped_questions": skipped,
        })

    # things we know we did not enumerate — reported, not silent
    if rm.get("slides_viewed_mode") and rm.get("view_threshold"):
        not_enumerated.append("view-threshold completion scenario (config present)")
    not_enumerated.append("suspend/resume scenario (planner v1)")
    for name, note in rm.get("route_notes", {}).items():
        if name.startswith("branch@"):
            not_enumerated.append(f"branch route {name}: {note} (planner v1 runs "
                                  f"forward route only; pretest branch is on the "
                                  f"human checklist until planner v2)")
    return {"scenarios": scenarios, "not_enumerated": not_enumerated}
