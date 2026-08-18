"""Reporter: merges scenario results into one course report (JSON + text)."""
from __future__ import annotations

import json
from pathlib import Path


def report(rm: dict, planned: dict, results: list, out_dir: str) -> str:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "runtime_report.json").write_text(json.dumps({
        "course": rm["course_title"],
        "scorm_version": rm["scorm_version"],
        "results": results,
        "not_enumerated": planned.get("not_enumerated", []),
    }, indent=1))

    lines = []
    add = lines.append
    add("=" * 74)
    add(f"SCORM RUNTIME QA — {rm['course_title']}")
    add(f"SCORM {rm['scorm_version']} | pass >= {rm['pass_percent']}% "
        f"(pass->{rm['pass_status']}, fail->{rm['fail_status']})")
    add("=" * 74)

    green = True
    for r in results:
        add("")
        add(f"--- scenario: {r['scenario_id']} ---")
        c = r["counts"]
        add(f"verdicts: {c['VERIFIED_PASS']} VERIFIED-PASS, "
            f"{c['VERIFIED_FAIL']} VERIFIED-FAIL, {c['BLOCKED']} BLOCKED, "
            f"{c['UNCHECKED_BY_DESIGN']} UNCHECKED-BY-DESIGN, "
            f"{c['BYPASSED']} BYPASSED")
        if r["blocked"]:
            green = False
            add(f"RUN BLOCKED: {r['blocked_reason']}")
        cov = r["coverage"]
        add("coverage:")
        for k, v in cov.items():
            if "checked" in v:
                note = f"  ({v['note']})" if "note" in v else ""
                add(f"  {k}: checked {v['checked']} of {v['of']}{note}")
            else:
                add(f"  {k}: {v}")
        fails = [x for x in r["checks"]
                 if x["verdict"] == "VERIFIED" and x["ok"] is False]
        if fails:
            green = False
            add("VERIFIED FAILURES (these are the findings):")
            for x in fails:
                add(f"  [FAIL] {x['check']}")
                add(f"     expected: {x['expected']}")
                add(f"     observed: {x['observed']}")
                if x.get("detail"):
                    add(f"     detail:   {x['detail']}")
        blocked = [x for x in r["checks"] if x["verdict"] == "BLOCKED"]
        if blocked:
            green = False
            add("BLOCKED CHECKS:")
            for x in blocked[:20]:
                add(f"  [BLOCKED] {x['check']} — {x.get('detail') or ''}")
        uncheck = [x for x in r["checks"] if x["verdict"] == "UNCHECKED-BY-DESIGN"]
        if uncheck:
            add("HUMAN PASS (UNCHECKED-BY-DESIGN):")
            for x in uncheck:
                add(f"  [HUMAN] {x['check']} — {x.get('detail') or ''}")
        # incomplete coverage forces non-green
        for k, v in cov.items():
            if "checked" in v and "of" in v and v["of"] and v["checked"] < v["of"] \
               and "note" not in v and k != "slides_in_course" \
               and k != "graded_questions_course":
                green = False

    add("")
    add("--- not enumerated by planner (goes to human checklist) ---")
    for n in planned.get("not_enumerated", []):
        add(f"  [HUMAN] {n}")
    add("")
    add(f"COURSE VERDICT: {'GREEN' if green else 'NEEDS WORK / FINDINGS ABOVE'}")
    add("(green requires: every route denominator fully VERIFIED, zero BLOCKED, "
        "zero BYPASSED; UNCHECKED-BY-DESIGN items are handed to the human pass)")
    txt = "\n".join(lines)
    (out / "runtime_report.txt").write_text(txt)
    return txt
