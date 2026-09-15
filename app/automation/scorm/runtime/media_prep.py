"""Media prep for codec-less test browsers.

The bundled Playwright Chromium has no H.264/AAC decoders, so Storyline slides
with mp4 video never finish loading (infinite spinner). This pre-pass
transcodes every .mp4 in the extracted package to open codecs (VP9/Opus, same
filename, same container) so playback works. Deterministic, per-package,
cached via a marker file. Visual fidelity is irrelevant here — visuals belong
to the human pass — so video is downscaled for speed.

Transcoding is the slowest thing that happens *before* the QA window opens, so
this module reports progress per video (via ``log``) and can be interrupted
(via ``cancel``) — otherwise a package with several large videos looks frozen.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from .cancel import Cancelled, check, is_cancelled

MARKER = ".qa_media_prepped.json"

# Per-video wall-clock cap. A single QA video that hasn't transcoded in this
# long is treated as stuck: we kill it, record the failure, and move on rather
# than blocking the whole pass. (Was 600s with no feedback and no way out.)
DEFAULT_TIMEOUT_S = 300


def _mb(path: Path) -> float:
    try:
        return path.stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0


def prep_media(
    course_dir: str,
    max_width: int = 480,
    *,
    cancel=None,
    log=None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> dict:
    """Transcode every mp4 under ``course_dir`` to open codecs.

    ``log``    — optional callable(str) for progress; defaults to flushed print.
    ``cancel`` — optional object with ``is_set()``; when set, the current
                 ffmpeg is killed and :class:`Cancelled` is raised.
    ``timeout_s`` — per-video cap; a video that exceeds it is killed and
                 recorded as failed instead of hanging the pass.

    Returns ``{"transcoded": [...], "failed": [...], "skipped": [...]}``.
    """
    log = log or (lambda m: print(m, flush=True))
    root = Path(course_dir)
    marker = root / MARKER
    if marker.exists():
        cached = json.loads(marker.read_text())
        log(f"media: reusing cached transcode "
            f"({len(cached.get('transcoded', []))} video(s) already prepped)")
        cached.setdefault("skipped", [])
        return cached

    vids = sorted(root.rglob("*.mp4"))
    done, failed, skipped = [], [], []

    if not vids:
        log("media: no videos to transcode")
        result = {"transcoded": done, "failed": failed, "skipped": skipped}
        marker.write_text(json.dumps(result, indent=1))
        return result

    if shutil.which("ffmpeg") is None:
        # Nothing we can do — but say so loudly, because the symptom otherwise
        # is a video slide that spins forever with no explanation.
        log(f"media: WARNING — ffmpeg not found on PATH; cannot transcode "
            f"{len(vids)} video(s). Video slides may not load in the QA browser.")
        skipped = [str(v.relative_to(root)) for v in vids]
        result = {"transcoded": done, "failed": failed, "skipped": skipped}
        marker.write_text(json.dumps(result, indent=1))
        return result

    total = len(vids)
    log(f"media: {total} video(s) to transcode (up to {timeout_s}s each)")
    for i, v in enumerate(vids, 1):
        check(cancel)  # bail cleanly between videos
        rel = v.relative_to(root)
        log(f"  [{i}/{total}] transcoding {rel} ({_mb(v):.1f} MB)…")
        tmp = v.with_suffix(".qa_tmp.mp4")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(v),
               "-vf", f"scale='min({max_width},iw)':-2",
               "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8",
               "-b:v", "300k", "-c:a", "libopus", "-b:a", "48k", str(tmp)]
        started = time.monotonic()
        proc = None
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            # Poll so we can react to cancel / timeout instead of blocking.
            while True:
                try:
                    proc.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if is_cancelled(cancel):
                    _kill(proc)
                    tmp.unlink(missing_ok=True)
                    log(f"  [{i}/{total}] cancelled — stopped transcoding {rel}")
                    raise Cancelled()
                if time.monotonic() - started > timeout_s:
                    _kill(proc)
                    tmp.unlink(missing_ok=True)
                    msg = f"timed out after {timeout_s}s"
                    failed.append({"file": str(rel), "error": msg})
                    log(f"  [{i}/{total}] FAILED ({msg}) — skipping {rel}")
                    break
            if proc.returncode == 0 and tmp.exists():
                tmp.replace(v)
                done.append(str(rel))
                log(f"  [{i}/{total}] done ({time.monotonic() - started:.0f}s)")
            elif proc.returncode not in (0, None):
                err = ""
                try:
                    err = (proc.stderr.read() or b"").decode("utf-8", "replace")
                except Exception:  # noqa: BLE001
                    pass
                if not any(f["file"] == str(rel) for f in failed):
                    failed.append({"file": str(rel), "error": err.strip()[:200]
                                   or f"ffmpeg exit {proc.returncode}"})
                    log(f"  [{i}/{total}] FAILED — {rel}")
                tmp.unlink(missing_ok=True)
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            failed.append({"file": str(rel), "error": str(e)[:200]})
            log(f"  [{i}/{total}] FAILED ({str(e)[:80]}) — {rel}")
            tmp.unlink(missing_ok=True)
        finally:
            if proc is not None and proc.stderr:
                try:
                    proc.stderr.close()
                except Exception:  # noqa: BLE001
                    pass

    log(f"media: {len(done)} transcoded, {len(failed)} failed, "
        f"{len(skipped)} skipped")
    result = {"transcoded": done, "failed": failed, "skipped": skipped}
    marker.write_text(json.dumps(result, indent=1))
    return result


def _kill(proc: subprocess.Popen) -> None:
    """Terminate an ffmpeg process, escalating to kill if it ignores us."""
    try:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    import sys
    r = prep_media(sys.argv[1])
    print(f"transcoded {len(r['transcoded'])}, failed {len(r['failed'])}, "
          f"skipped {len(r.get('skipped', []))}")
    for f in r["failed"]:
        print("  FAILED:", f)
