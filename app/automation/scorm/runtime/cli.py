"""End-to-end runtime QA runner.

    python -m app.automation.scorm.runtime.cli <course.zip> [--data-dir DIR]
        [--scenario ID] [--locked] [--headed]

Pipeline: build runtime model -> plan scenarios -> drive each in a fresh
browser context -> verify -> report. All outputs land in DATA_DIR/<course>/.

Slow stages that run *before* a browser window appears (unzip, media
transcode, launch) report progress and honor an optional ``cancel`` signal so
the UI can show what's happening and let the reviewer stop a run that would
otherwise look frozen.
"""
from __future__ import annotations

import argparse
import contextlib
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
from .cancel import Cancelled, check, is_cancelled


def _default_log(msg):
    print(msg, flush=True)


@contextlib.contextmanager
def _stage(log, name, cancel=None):
    """Announce a pipeline stage and report how long it took. Checking cancel
    on entry means a stop request between stages aborts before the slow work."""
    check(cancel)
    t0 = time.monotonic()
    log(f"▶ {name} …")
    yield
    log(f"  ✓ {name} ({time.monotonic() - t0:.1f}s)")


def _prune_old_caches(cache_root: Path, stem: str, keep: str, log) -> None:
    """Drop stale extraction caches for the same course (different mtime/size),
    keeping only the current one so the cache doesn't grow without bound."""
    try:
        for d in cache_root.glob(f"{stem}__*"):
            if d.is_dir() and d.name != keep:
                shutil.rmtree(d, ignore_errors=True)
                log(f"cache: removed stale extraction {d.name}")
    except Exception:  # noqa: BLE001 — pruning is best-effort
        pass


def _prepare_course_dir(args, cancel=None, log=None):
    """Extract the package (or reuse --extract-dir) and transcode media.
    Returns (course_dir, cleanup_dir_or_None).

    For a plain zip we extract into a STABLE per-zip cache dir (keyed by name +
    mtime + size) rather than a throwaway temp dir. That makes the media-prep
    marker persist, so a second Companion open of the same package skips both
    the unzip and the (slow) transcode entirely. cleanup is None for the cache
    so it survives; a rebuilt package changes the key and re-extracts."""
    log = log or _default_log
    if args.extract_dir:
        return args.extract_dir, None

    zp = Path(args.zip_path)
    try:
        st = zp.stat()
        key = f"{zp.stem}__{int(st.st_mtime)}_{st.st_size}"
    except OSError:
        key = zp.stem
    cache_root = Path(args.data_dir) / "_course_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    course_dir = cache_root / key
    extracted_marker = course_dir / ".qa_extracted.json"

    if extracted_marker.exists():
        log(f"package: reusing cached extraction ({course_dir.name})")
    else:
        _prune_old_caches(cache_root, zp.stem, keep=key, log=log)
        shutil.rmtree(course_dir, ignore_errors=True)
        course_dir.mkdir(parents=True, exist_ok=True)
        with _stage(log, "extracting package", cancel):
            with zipfile.ZipFile(args.zip_path) as zf:
                zf.extractall(course_dir)
        extracted_marker.write_text(json.dumps(
            {"zip": str(zp), "extracted_at": datetime.now().isoformat()}))

    from .media_prep import prep_media
    with _stage(log, "preparing media", cancel):
        prep_media(str(course_dir), cancel=cancel, log=log)
    return str(course_dir), None


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
        print(f"  \U0001f4f8 screen {screen} [{tag}] -> {fname}", flush=True)

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


def observe(args, cancel=None):
    """Passive mode: host the course in a headed browser and get out of the way
    so a human can drive it with a screen reader. Runs NO driver. While idle,
    logs each screen change with a timestamp so the run can later be merged with
    a JAWS speech transcript (see docs/JAWS_TRANSCRIPT_DESIGN.md).

    Note: screen markers are wall-clock timestamped; the JAWS-side log must use
    the same clock for a precise merge (clock alignment is still an open item).
    """
    log = _default_log
    try:
        course_dir, cleanup = _prepare_course_dir(args, cancel=cancel, log=log)
    except Cancelled:
        log("✖ cancelled before the window opened (during package/media prep).")
        return 130

    screens_log = Path(args.screens_log) if args.screens_log else (
        Path(args.data_dir) / Path(args.zip_path).stem / "screens.log")
    screens_log.parent.mkdir(parents=True, exist_ok=True)

    # Build the per-screen companion panel data from the parsed course.
    companion = None
    try:
        with _stage(log, "indexing screens (companion panel)", cancel):
            from ..parser import parse_scorm_zip
            from ..companion import build_companion_data
            from ..checks.wordlists import (
                base_language_code, detect_language_from_filename,
            )
            # Determine the course language: explicit --lang wins, else the
            # code embedded in the SCORM filename (GLS convention …_de-DE_…).
            target_lang = args.lang or detect_language_from_filename(
                Path(args.zip_path).name)
            base = base_language_code(target_lang)
            # A detected non-English language implies non-English handling even
            # when --non-english wasn't passed; English / unknown falls back to
            # the flag so behavior is unchanged for those.
            non_english = args.non_english or (base is not None and base != "en")
            if target_lang:
                log(f"companion: course language = {target_lang} "
                    f"(non-English={non_english})")
            cdata = parse_scorm_zip(args.zip_path)
            companion = build_companion_data(
                cdata, dual_path=args.dual_path, non_english=non_english,
                target_lang=target_lang)
        n = sum(1 for s in companion["slides"].values() if s["checks"])
        log(f"companion: {len(companion['slides'])} screens indexed, "
            f"{n} with check items")
    except Cancelled:
        log("✖ cancelled before the window opened (during screen indexing).")
        if cleanup:
            shutil.rmtree(cleanup, ignore_errors=True)
        return 130
    except Exception as e:  # noqa: BLE001 — panel is a convenience, never block QA
        log(f"companion: disabled ({e!r})")

    # --- optional screenshot capture for course-print generation -------------
    cap = _CaptureSession(args, companion) if args.capture_shots else None
    if cap:
        log(f"capture: ON — screenshots -> {cap.shots_dir}")
        log("capture: base state grabbed on each new screen; press the panel's "
            "‘Capture state’ button (or Ctrl+Shift+S) for each layer.")

    srv = CourseServer(course_dir, companion=companion, capture=bool(cap))
    try:
        with sync_playwright() as p:
            try:
                browser, L = launch(p, srv, headless=False, no_viewport=True,
                                    cancel=cancel, log=log)
            except Cancelled:
                log("✖ cancelled while the course was launching — "
                    "no window opened.")
                return 130
            print(f"\nREADY — drive the course yourself in the open window.")
            print(f"Screen changes are being logged to: {screens_log}")
            print("Close the course window (or press Ctrl+C here) when you're done.\n",
                  flush=True)
            last = object()  # sentinel so the first real screen always logs
            settle = _Settle()  # per-screen text-stability tracker for base capture
            try:
                while True:
                    if is_cancelled(cancel):
                        print("\nstopping observe (cancelled) ...", flush=True)
                        break
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


def main(argv=None, cancel=None):
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
                    help="observe: course is not in English (skips spell/terminology and em-dash/hyphen checks)")
    ap.add_argument("--lang", default=None,
                    help="observe: course language code (e.g. de-DE, es-LA, fr-CA). "
                         "Used to pick the target-language dictionary for the "
                         "untranslated-English check. Default: auto-detected from "
                         "the SCORM filename (…_de-DE_…). A non-English language "
                         "here also implies --non-english.")
    ap.add_argument("--capture-shots", action="store_true",
                    help="observe: capture course screenshots for the course print — "
                         "base state auto-grabbed per screen, plus the panel's "
                         "‘Capture state’ button for layer states. Off by default.")
    ap.add_argument("--shots-dir", default=None,
                    help="where --capture-shots writes images + shots_manifest.json "
                         "(default: <data-dir>/<course>/course_print_shots)")
    args = ap.parse_args(argv)

    log = _default_log

    if args.observe:
        return observe(args, cancel=cancel)

    course_out = Path(args.data_dir) / Path(args.zip_path).stem
    course_out.mkdir(parents=True, exist_ok=True)

    try:
        with _stage(log, "[1/5] building runtime model", cancel):
            rm_obj = build_runtime_model(args.zip_path)
            rm = json.loads(rm_obj.to_json())
            (course_out / "runtime_model.json").write_text(json.dumps(rm, indent=1))
        drivable = sum(1 for q in rm["questions"] if q["drivable"] and not q["is_survey"])
        graded = sum(1 for q in rm["questions"] if not q["is_survey"])
        log(f"      {rm['course_title']}: {len(rm['slides'])} slides, "
            f"{graded} graded ({drivable} drivable), route {len(rm['routes']['forward'])}")

        with _stage(log, "[2/5] planning scenarios", cancel):
            planned = plan(rm)
            (course_out / "scenarios.json").write_text(json.dumps(planned, indent=1))
        todo = [s for s in planned["scenarios"]
                if args.scenario in (None, s["id"])]
        log(f"      {[s['id'] for s in todo]}")

        if args.extract_dir:
            course_dir = args.extract_dir
            cleanup = None
        else:
            cleanup = tempfile.mkdtemp(prefix="scorm_course_")
            course_dir = cleanup
            with _stage(log, "[3/5] extracting package", cancel):
                with zipfile.ZipFile(args.zip_path) as zf:
                    zf.extractall(course_dir)

        from .media_prep import prep_media
        with _stage(log, "[3b] media prep (open-codec transcode for test browser)", cancel):
            prep_media(course_dir, cancel=cancel, log=log)
    except Cancelled:
        log("✖ cancelled during setup — nothing was driven.")
        return 130

    results = []
    log("[4/5] driving scenarios ...")
    with sync_playwright() as p:
        for sc in todo:
            if is_cancelled(cancel):
                log("✖ cancelled — stopping before the next scenario.")
                break
            log(f"      >>> {sc['id']}")
            srv = CourseServer(course_dir)
            browser = None
            try:
                browser, L = launch(p, srv, headless=not args.headed,
                                    cancel=cancel, log=log)
                drv = Driver(rm, L, str(course_out / "artifacts" / sc["id"]),
                             unlocked=not args.locked)
                runlog = drv.run(sc)
            except Cancelled:
                if browser:
                    try:
                        browser.close()
                    except Exception:  # noqa: BLE001
                        pass
                srv.close()
                log("✖ cancelled during launch — stopping.")
                break
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
            log(f"          {c['VERIFIED_PASS']} pass / {c['VERIFIED_FAIL']} fail "
                f"/ {c['BLOCKED']} blocked"
                + (f"  [RUN BLOCKED: {res['blocked_reason']}]" if res["blocked"] else ""))

    log("[5/5] reporting ...")
    txt = report(rm, planned, results, str(course_out))
    print(txt)
    if cleanup:
        shutil.rmtree(cleanup, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
