"""Verifier: pure function of (scenario, runlog, runtime model).

Produces per-check verdicts: VERIFIED / BYPASSED / BLOCKED / UNCHECKED-BY-DESIGN,
each with pass/fail where applicable, plus the coverage denominators.
A VERIFIED+FAIL is a *useful* result — on unfinished courses it is the signal.
"""
from __future__ import annotations

import re


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip().lower()


def _alnum(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def _interactions_from_cmi(cmi: dict) -> dict:
    """Group cmi.interactions.N.* into {n: {field: value}}."""
    out = {}
    for k, v in cmi.items():
        m = re.match(r"cmi\.interactions\.(\d+)\.(.+)$", k)
        if m:
            out.setdefault(int(m.group(1)), {})[m.group(2)] = v
    return out


def verify(scenario: dict, runlog: dict, rm: dict) -> dict:
    checks = []
    cmi = runlog.get("final_cmi", {})
    expected = scenario.get("expected", {})

    def add(check, verdict, ok=None, expected_s="", observed_s="", detail=""):
        checks.append({"check": check, "verdict": verdict, "ok": ok,
                       "expected": expected_s, "observed": observed_s,
                       "detail": detail})

    blocked = runlog.get("blocked", False)

    # ---------------------------------------------------------- completion
    for key, label in (("completion_status", "cmi.completion_status"),
                       ("success_status", "cmi.success_status")):
        if key in expected:
            obs = cmi.get(f"cmi.{key}", "(never set)")
            if blocked:
                add(f"completion:{key}", "BLOCKED",
                    expected_s=expected[key], observed_s=obs,
                    detail=runlog.get("blocked_reason", ""))
            else:
                add(f"completion:{key}", "VERIFIED", ok=(obs == expected[key]),
                    expected_s=expected[key], observed_s=obs)
    raw = cmi.get("cmi.score.scaled", "")
    if "score_scaled_gte" in expected or "score_scaled_lt" in expected:
        try:
            val = float(raw)
            if "score_scaled_gte" in expected:
                ok = val >= expected["score_scaled_gte"]
                exp_s = f">= {expected['score_scaled_gte']}"
            else:
                ok = val < expected["score_scaled_lt"]
                exp_s = f"< {expected['score_scaled_lt']}"
            add("completion:score.scaled", "BLOCKED" if blocked else "VERIFIED",
                ok=None if blocked else ok, expected_s=exp_s, observed_s=raw)
        except (TypeError, ValueError):
            add("completion:score.scaled", "BLOCKED" if blocked else "VERIFIED",
                ok=None if blocked else False,
                expected_s="numeric scaled score", observed_s=repr(raw))
    if expected.get("commit_seen"):
        commits = [e for e in runlog.get("scorm_log", [])
                   if e["fn"] in ("Commit", "LMSCommit")]
        add("completion:commit", "BLOCKED" if blocked else "VERIFIED",
            ok=None if blocked else bool(commits),
            expected_s="Commit called", observed_s=f"{len(commits)} commits")

    # ---------------------------------------------------------- questions
    recorded = _interactions_from_cmi(cmi)
    inventory = {q["interaction_id"]: q for q in rm["questions"] if not q["is_survey"]}
    q_clicks = [f for f in runlog.get("findings", [])
                if f["kind"] == "question_click"]

    submitted = {s["detail"] for s in runlog.get("steps", [])
                 if s["action"] == "submit"}
    # match cmi records to answer plans in submission order: for each plan (in
    # route order) take the first unclaimed record whose id contains the lms_id
    claimed = set()
    plans_in_order = scenario.get("answer_plan", [])
    for plan_a in plans_in_order:
        iid = plan_a["interaction_id"]
        lms = plan_a.get("lms_id", iid)
        q = inventory.get(iid)
        if iid not in submitted:
            add(f"question:{iid}", "BLOCKED",
                expected_s="question submitted during run",
                observed_s="run never reached/submitted this question",
                detail=runlog.get("blocked_reason", ""))
            continue
        rec = None
        for n in sorted(recorded):
            if n in claimed:
                continue
            if lms and lms in (recorded[n].get("id") or ""):
                rec = recorded[n]
                claimed.add(n)
                break
        if rec is None:
            add(f"question:{iid}:recorded", "VERIFIED", ok=False,
                expected_s=f"a cmi.interactions.*.id containing {lms!r} "
                           f"(submission order)",
                observed_s=f"ids: {[r.get('id') for r in recorded.values()]}"[:200])
            continue
        # leg 3: id linkage (already matched); result
        res = rec.get("result", "")
        add(f"question:{iid}:result", "VERIFIED",
            ok=(res == plan_a["expect_result"]),
            expected_s=plan_a["expect_result"], observed_s=res)
        # leg 1: on-screen text at click time vs static key text
        clicks = [c for c in q_clicks if c["slide"] == plan_a["slide_id"]
                  and c["target"] in plan_a["click_objs"]]
        for c in clicks:
            exp_t, obs_t = _norm(c["expected"]), _norm(c["observed"])
            add(f"question:{iid}:onscreen:{c['target']}", "VERIFIED",
                ok=bool(exp_t) and (exp_t in obs_t or obs_t in exp_t),
                expected_s=c["expected"][:80], observed_s=c["observed"][:80])
        # leg 2: recorded learner_response contains every planned choice
        # (Storyline records snake-cased choice text joined by '[,]')
        resp_alnum = _alnum(rec.get("learner_response",
                                    rec.get("student_response", "")))
        missing = [text for text in plan_a["click_texts_expected"].values()
                   if _alnum(text)[:30] not in resp_alnum]
        add(f"question:{iid}:response", "VERIFIED", ok=not missing,
            expected_s=f"response containing all {len(plan_a['click_texts_expected'])} "
                       f"planned choice texts",
            observed_s=(rec.get("learner_response", "")[:90] if not missing
                        else f"missing: {[m[:40] for m in missing]}"[:120]))

    # unpredicted interaction writes: runtime audits static
    known = {q["lms_id"] for q in rm["questions"]} | {""}
    for n, r in recorded.items():
        rid = r.get("id", "")
        if rid and not any(k and k in rid for k in known):
            add(f"audit:unpredicted_interaction:{rid}", "VERIFIED", ok=False,
                expected_s="every cmi.interactions id predicted by static inventory",
                observed_s=rid,
                detail="static inventory gap — investigate freeform/variable question")

    # non-drivable questions -> human pass
    for q in rm["questions"]:
        if q["is_survey"]:
            continue
        if not q["drivable"]:
            add(f"question:{q['lms_id']}@{q['slide_id']}", "UNCHECKED-BY-DESIGN",
                detail=q["not_drivable_reason"])

    # ---------------------------------------------------------- gates & items
    for f in runlog.get("findings", []):
        if f["kind"] == "gate_hold":
            add(f"gate_hold:{f['slide']}", "VERIFIED", ok=f["ok"],
                expected_s=f["expected"], observed_s=f["observed"])
        elif f["kind"] == "item_reveal":
            add(f"item:{f['slide']}:{f['target']}", "VERIFIED", ok=f["ok"],
                expected_s=f["expected"], observed_s=f["observed"])
        elif f["kind"] == "item_unreachable":
            add(f"item:{f['slide']}:{f['target']}", "UNCHECKED-BY-DESIGN",
                detail=f["observed"])
        elif f["kind"] == "human_skip":
            add(f"slide:{f['slide']}", "UNCHECKED-BY-DESIGN",
                detail=f["expected"] + " — " + f["observed"])
        elif f["kind"] == "slide_arrival" and not f["ok"]:
            add(f"arrival:{f['slide']}", "VERIFIED", ok=False,
                expected_s=f["expected"], observed_s=f["observed"])

    # gate_open: a gated slide we successfully advanced from == gate opened
    gated = {s["slide_id"] for s in rm["automodel"]["slides"]
             if s.get("required_all") or s.get("required_any")}
    for e in runlog.get("edges", []):
        if e[0] in gated:
            add(f"gate_open:{e[0]}", "VERIFIED", ok=True,
                expected_s="continue works after all required items clicked",
                observed_s=f"advanced to {e[1]}")

    # ---------------------------------------------------------- coverage
    route = scenario["route"]
    visited = runlog.get("slides_visited", [])
    drivable_q = [q for q in rm["questions"] if not q["is_survey"] and q["drivable"]
                  and q["slide_id"] in route]
    graded_q = [q for q in rm["questions"] if not q["is_survey"]]
    answered_n = len([a for a in plans_in_order
                      if any(c["check"].startswith(f"question:{a['interaction_id']}:result")
                             and c["verdict"] == "VERIFIED"
                             for c in checks)])
    human_q_slides = {q["slide_id"] for q in rm["questions"]
                      if not q["drivable"] and not q["is_survey"]}
    sweep_items = 0
    for sid in route:
        if rm["slides"].get(sid, {}).get("text_entry") or sid in human_q_slides:
            continue   # handed to human pass; excluded from sweep denominator
        ams = next((s for s in rm["automodel"]["slides"] if s["slide_id"] == sid), {})
        fx = rm["slides"].get(sid, {}).get("effects", {})
        ids = {r["obj_id"] for r in ams.get("required_all", [])}
        ids |= {r["obj_id"] for r in ams.get("required_any", [])}
        closers = set()
        for l in rm["slides"].get(sid, {}).get("layers", []):
            closers.update(l["close_objects"])
        ids |= {oid for oid, f2 in fx.items()
                if f2.get("shows_layers") and not f2.get("advances_to")
                and not f2.get("exits") and oid not in closers}
        conts = {c["obj_id"] for c in ams.get("continue_buttons", [])}
        sweep_items += len(ids - conts)
    items_checked = len([c for c in checks if c["check"].startswith("item:")])
    gates_on_route = len([s for s in route if s in gated])
    gates_held = len([c for c in checks
                      if c["check"].startswith("gate_hold:") and c["ok"]])
    gates_opened = len([c for c in checks if c["check"].startswith("gate_open:")])

    coverage = {
        "slides_on_route": {"checked": len(set(visited) & set(route)), "of": len(route)},
        "slides_in_course": {"checked": len(set(visited)), "of": len(rm["slides"])},
        "graded_questions_course": {"checked": answered_n, "of": len(graded_q)},
        "graded_questions_route_drivable": {"checked": answered_n, "of": len(drivable_q)},
        "sweep_items": {"checked": items_checked, "of": sweep_items},
        "gates": {"hold_verified": gates_held, "open_verified": gates_opened,
                  "of": gates_on_route},
    }
    if not scenario.get("sweep", True):
        coverage["sweep_items"]["note"] = "sweep disabled for this scenario by plan"

    failed = [c for c in checks if c["verdict"] == "VERIFIED" and c["ok"] is False]
    blocked_checks = [c for c in checks if c["verdict"] == "BLOCKED"]
    return {
        "scenario_id": scenario["id"],
        "checks": checks,
        "coverage": coverage,
        "counts": {
            "VERIFIED_PASS": len([c for c in checks
                                  if c["verdict"] == "VERIFIED" and c["ok"]]),
            "VERIFIED_FAIL": len(failed),
            "BLOCKED": len(blocked_checks),
            "UNCHECKED_BY_DESIGN": len([c for c in checks
                                        if c["verdict"] == "UNCHECKED-BY-DESIGN"]),
            "BYPASSED": len([c for c in checks if c["verdict"] == "BYPASSED"]),
        },
        "blocked": runlog.get("blocked", False),
        "blocked_reason": runlog.get("blocked_reason", ""),
    }
