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
import time
import zipfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from .runtime_model import build_runtime_model
from .planner import plan
from .harness import CourseServer, launch
from .driver import Driver
from .verifier import verify
from .reporter import report


def _prepare_course_dir(args):
    """Extract the package (or reuse --extract-dir) and transcode media.
    Returns (course_dir, cleanup_dir_or_None)."""
    if args.extract_dir:
        course_dir = args.extract_dir
        cleanup = None
    else:
        cleanup = tempfile.mkdtemp(prefix="scorm_course_")
        course_dir = cleanup
        print("extracting package ...", flush=True)
        with zipfile.ZipFile(args.zip_path) as zf:
            zf.extractall(course_dir)
    from .media_prep import prep_media
    mp = prep_media(course_dir)
    print(f"media: {len(mp['transcoded'])} transcoded, {len(mp['failed'])} failed", flush=True)
    return course_dir, cleanup


class _Settle:
    """Decides when a screen's base state is ready to capture, by watching the
    on-screen text stop changing (the timeline finishing its text-in animation).

    Bounds:
      MIN  — ignore the first moment while the slide mounts.
      HOLD — text must be unchanged this long to count as 'done' (covers fades).
      MAX  — if it never holds still (looping text/animation), give up on the
             auto grab and ask for a manual one rather than shoot mid-animation.
    """
    MIN = 0.6
    HOLD = 1.4
    MAX = 40.0

    def __init__(self):
        self.sid = None
        self.done = True

    def reset(self, sid):
        self.sid = sid
        self.done = False
        self.arrived = time.monotonic()
        self.sig = None
        self.last_change = self.arrived

    def mark_manual(self):
        # a manual shot satisfies this screen; stop trying to auto-grab a base
        self.done = True

    def update(self, cur, sig):
        if cur != self.sid or self.done:
            return "wait", (self.sid and "captured" or "capture ready")
        now = time.monotonic()
        if sig != self.sig:
            self.sig = sig
            self.last_change = now
        stable = now - self.last_change
        elapsed = now - self.arrived
        if elapsed < self.MIN:
            return "wait", "⏳ screen loading…"
        if stable >= self.HOLD:
            self.done = True
            return "capture", "capturing base…"
        if elapsed >= self.MAX:
            self.done = True  # stop auto-attempts; defer to the reviewer
            return "wait", "✎ won't settle — press Capture when it looks right"
        return "wait", "⏳ waiting for timeline (text still appearing)…"


class _CaptureSession:
    """Collects course-iframe screenshots during an observe pass and writes a
    manifest the course-print generator consumes. Best-effort throughout: a
    failed shot is logged and skipped, never fatal to the QA pass.

    Files:  <shots_dir>/<seq>_<screen>_<slide_id>_<kind>.png
    Manifest: <shots_dir>/shots_manifest.json  (order == capture order)
    """

    def __init__(self, args, companion):
        base = Path(args.shots_dir) if args.shots_dir else (
            Path(args.data_dir) / Path(args.zip_path).stem / "course_print_shots")
        base.mkdir(parents=True, exist_ok=True)
        self.shots_dir = base
        self.meta = (companion or {}).get("slides", {})
        self.seq = 0
        self.per_slide = {}          # slide_id -> count (for the reviewer's log)
        self.records = []            # manifest rows, in capture order

    def capture(self, L, slide_id, kind="base"):
        self.seq += 1
        info = self.meta.get(slide_id, {})
        screen = info.get("screen_number", "") or "unknown"
        safe_screen = str(screen).replace(".", "-")
        fname = f"{self.seq:03d}_s{safe_screen}_{slide_id}_{kind}.png"
        path = self.shots_dir / fname
        ok = L.capture_course(str(path))
        if not ok:
            print(f"  capture: FAILED for screen {screen} ({slide_id})", flush=True)
            self.seq -= 1
            return
        self.per_slide[slide_id] = self.per_slide.get(slide_id, 0) + 1
        self.records.append({
            "seq": self.seq,
            "file": fname,
            "slide_id": slide_id,
            "screen_number": screen,
            "slide_title": info.get("slide_title", ""),
            "kind": kind,
            "ts": datetime.now().isoformat(timespec="seconds"),
        })
        if kind == "base":
            tag = "base"
        else:
            n_manual = sum(1 for r in self.records
                           if r["slide_id"] == slide_id and r["kind"] == "manual")
            tag = f"layer #{n_manual}"
        print(f"  📸 screen {screen} [{tag}] -> {fname}", flush=True)

    def finish(self):
        manifest = {
            "shots_dir": str(self.shots_dir),
            "count": len(self.records),
            "slides_captured": len(self.per_slide),
            "shots": self.records,
        }
        (self.shots_dir / "shots_manifest.json").write_text(
            json.dumps(manifest, indent=1))
        print(f"\ncapture: wrote {len(self.records)} screenshot(s) across "
              f"{len(self.per_slide)} screen(s)\n"
              f"capture: manifest -> {self.shots_dir / 'shots_manifest.json'}",
              flush=True)


def observe(args):
    """Passive mode: host the course in a headed browser and get out of the way
    so a human can drive it with a screen reader. Runs NO driver. While idle,
    logs each screen change with a timestamp so the run can later be merged with
    a JAWS speech transcript (see docs/JAWS_TRANSCRIPT_DESIGN.md).

    Note: screen markers are wall-clock timestamped; the JAWS-side log must use
    the same clock for a precise merge (clock alignment is still an open item).
    """
    course_dir, cleanup = _prepare_course_dir(args)
    screens_log = Path(args.screens_log) if args.screens_log else (
        Path(args.data_dir) / Path(args.zip_path).stem / "screens.log")
    screens_log.parent.mkdir(parents=True, exist_ok=True)

    # Build the per-screen companion panel data from the parsed course.
    companion = None
    try:
        from ..parser import parse_scorm_zip
        from ..companion import build_companion_data
        cdata = parse_scorm_zip(args.zip_path)
        companion = build_companion_data(
            cdata, dual_path=args.dual_path, non_english=args.non_english)
        n = sum(1 for s in companion["slides"].values() if s["checks"])
        print(f"companion: {len(companion['slides'])} screens indexed, "
              f"{n} with check items", flush=True)
    except Exception as e:  # noqa: BLE001 — panel is a convenience, never block QA
        print(f"companion: disabled ({e!r})", flush=True)

    # --- optional screenshot capture for course-print generation -------------
    cap = _CaptureSession(args, companion) if args.capture_shots else None
    if cap:
        print(f"capture: ON — screenshots -> {cap.shots_dir}", flush=True)
        print("capture: base state grabbed on each new screen; press the panel's "
              "‘Capture state’ button (or Ctrl+Shift+S) for each layer.", flush=True)

    srv = CourseServer(course_dir, companion=companion, capture=bool(cap))
    try:
        with sync_playwright() as p:
            browser, L = launch(p, srv, headless=False, no_viewport=True)
            print(f"\nREADY — drive the course yourself in the open window.")
            print(f"Screen changes are being logged to: {screens_log}")
            print("Close the course window (or press Ctrl+C here) when you're done.\n",
                  flush=True)
            last = object()  # sentinel so the first real screen always logs
            settle = _Settle()  # per-screen text-stability tracker for base capture
            try:
                while True:
                    try:
                        cur = L.current_slide_id()
                    except Exception:
                        break  # window closed or page gone
                    if cur is not None and cur != last:
                        last = cur
                        stamp = datetime.now()
                        line = (f"{stamp.strftime('%H:%M:%S.')}"
                                f"{stamp.microsecond // 1000:03d} "
                                f"{int(time.time() * 1000)} | SCREEN {cur}")
                        with screens_log.open("a", encoding="utf-8") as fh:
                            fh.write(line + "\n")
                        print("  " + line, flush=True)
                        if cap:
                            settle.reset(cur)

                    if cap:
                        # base capture: fire once the on-screen TEXT has stopped
                        # changing (timeline done). Never grabs a mid-animation
                        # frame — a screen that won't settle is flagged for a
                        # manual grab instead. Manual captures are immediate.
                        decision, status = settle.update(cur, L.active_slide_text())
                        L.set_capture_status(status)
                        if decision == "capture":
                            cap.capture(L, cur, kind="base")
                            L.set_capture_status(f"✓ base captured — screen {cur[:10]}…")
                        for _req in L.drain_capture_queue():
                            cap.capture(L, cur, kind="manual")
                            settle.mark_manual()
                            L.set_capture_status("✓ captured (manual)")
                    time.sleep(0.25)
            except KeyboardInterrupt:
                print("\nstopping observe ...", flush=True)
            finally:
                if cap:
                    cap.finish()
                try:
                    browser.close()
                except Exception:
                    pass
    finally:
        srv.close()
        if cleanup:
            shutil.rmtree(cleanup, ignore_errors=True)
    return 0


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
    ap.add_argument("--observe", action="store_true",
                    help="host the course headed and DO NOT drive it — "
                         "for manual JAWS/screen-reader QA; logs screen changes "
                         "and shows the per-screen companion panel")
    ap.add_argument("--screens-log", default=None,
                    help="where --observe writes screen-change markers "
                         "(default: <data-dir>/<course>/screens.log)")
    ap.add_argument("--dual-path", action="store_true",
                    help="observe: treat as a dual-path course (adds accessibility "
                         "checks to the companion panel)")
    ap.add_argument("--non-english", action="store_true",
                    help="observe: course is not in English (skips spell/terminology)")
    ap.add_argument("--capture-shots", action="store_true",
                    help="observe: capture course screenshots for the course print — "
                         "base state auto-grabbed per screen, plus the panel's "
                         "‘Capture state’ button for layer states. Off by default.")
    ap.add_argument("--shots-dir", default=None,
                    help="where --capture-shots writes images + shots_manifest.json "
                         "(default: <data-dir>/<course>/course_print_shots)")
    args = ap.parse_args(argv)

    if args.observe:
        return observe(args)

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
