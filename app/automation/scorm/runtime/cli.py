"""End-to-end runtime QA runner.

    python -m app.automation.scorm.runtime.cli <course.zip> [--data-dir DIR]
        [--scenario ID] [--locked] [--headed]

Pipeline: build runtime model -> plan scenarios -> drive each in a fresh
browser context -> verify -> report. All outputs land in DATA_DIR/<course>/.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path

from playwright.sync_api import sync_playwright

from .runtime_model import build_runtime_model
from .planner import plan
from .harness import CourseServer, launch
from .driver import Driver
from .verifier import verify
from .reporter import report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("zip_path")
    ap.add_argument("--data-dir", default="data_dir")
    ap.add_argument("--scenario", default=None, help="run only this scenario id")
    ap.add_argument("--locked", action="store_true",
                    help="locked build: no timeline seek; wait out durations")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--extract-dir", default=None,
                    help="reuse an already-extracted course dir")
    args = ap.parse_args(argv)

    course_out = Path(args.data_dir) / Path(args.zip_path).stem
    course_out.mkdir(parents=True, exist_ok=True)

    print("[1/5] building runtime model ...", flush=True)
    rm_obj = build_runtime_model(args.zip_path)
    rm = json.loads(rm_obj.to_json())
    (course_out / "runtime_model.json").write_text(json.dumps(rm, indent=1))
    drivable = sum(1 for q in rm["questions"] if q["drivable"] and not q["is_survey"])
    graded = sum(1 for q in rm["questions"] if not q["is_survey"])
    print(f"      {rm['course_title']}: {len(rm['slides'])} slides, "
          f"{graded} graded ({drivable} drivable), route {len(rm['routes']['forward'])}")

    print("[2/5] planning scenarios ...", flush=True)
    planned = plan(rm)
    (course_out / "scenarios.json").write_text(json.dumps(planned, indent=1))
    todo = [s for s in planned["scenarios"]
            if args.scenario in (None, s["id"])]
    print(f"      {[s['id'] for s in todo]}")

    if args.extract_dir:
        course_dir = args.extract_dir
        cleanup = None
    else:
        cleanup = tempfile.mkdtemp(prefix="scorm_course_")
        course_dir = cleanup
        print("[3/5] extracting package ...", flush=True)
        with zipfile.ZipFile(args.zip_path) as zf:
            zf.extractall(course_dir)

    print("[3b] media prep (open-codec transcode for test browser) ...", flush=True)
    from .media_prep import prep_media
    mp = prep_media(course_dir)
    print(f"      {len(mp['transcoded'])} transcoded, {len(mp['failed'])} failed")

    results = []
    print("[4/5] driving scenarios ...", flush=True)
    with sync_playwright() as p:
        for sc in todo:
            print(f"      >>> {sc['id']}", flush=True)
            srv = CourseServer(course_dir)
            browser = None
            try:
                browser, L = launch(p, srv, headless=not args.headed)
                drv = Driver(rm, L, str(course_out / "artifacts" / sc["id"]),
                             unlocked=not args.locked)
                runlog = drv.run(sc)
            except Exception as e:                               # noqa: BLE001
                from .driver import RunLog
                runlog = RunLog(scenario_id=sc["id"])
                runlog.blocked = True
                runlog.blocked_reason = f"launch failed: {e!r}"
            finally:
                if browser:
                    try:
                        browser.close()
                    except Exception:                            # noqa: BLE001
                        pass
                srv.close()
            rl = runlog.to_dict() if hasattr(runlog, "to_dict") else runlog
            (course_out / f"runlog_{sc['id']}.json").write_text(json.dumps(rl, indent=1))
            res = verify(sc, rl, rm)
            results.append(res)
            c = res["counts"]
            print(f"          {c['VERIFIED_PASS']} pass / {c['VERIFIED_FAIL']} fail "
                  f"/ {c['BLOCKED']} blocked"
                  + (f"  [RUN BLOCKED: {res['blocked_reason']}]" if res["blocked"] else ""),
                  flush=True)

    print("[5/5] reporting ...", flush=True)
    txt = report(rm, planned, results, str(course_out))
    print(txt)
    if cleanup:
        shutil.rmtree(cleanup, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
