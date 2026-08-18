"""Media prep for codec-less test browsers.

The bundled Playwright Chromium has no H.264/AAC decoders, so Storyline slides
with mp4 video never finish loading (infinite spinner). This pre-pass
transcodes every .mp4 in the extracted package to open codecs (VP9/Opus, same
filename, same container) so playback works. Deterministic, per-package,
cached via a marker file. Visual fidelity is irrelevant here — visuals belong
to the human pass — so video is downscaled for speed.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

MARKER = ".qa_media_prepped.json"


def prep_media(course_dir: str, max_width: int = 480) -> dict:
    root = Path(course_dir)
    marker = root / MARKER
    if marker.exists():
        return json.loads(marker.read_text())
    vids = sorted(root.rglob("*.mp4"))
    done, failed = [], []
    for v in vids:
        tmp = v.with_suffix(".qa_tmp.mp4")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(v),
               "-vf", f"scale='min({max_width},iw)':-2",
               "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8",
               "-b:v", "300k", "-c:a", "libopus", "-b:a", "48k", str(tmp)]
        try:
            subprocess.run(cmd, check=True, timeout=600)
            tmp.replace(v)
            done.append(str(v.relative_to(root)))
        except Exception as e:                                   # noqa: BLE001
            failed.append({"file": str(v.relative_to(root)), "error": str(e)[:200]})
            tmp.unlink(missing_ok=True)
    result = {"transcoded": done, "failed": failed}
    marker.write_text(json.dumps(result, indent=1))
    return result


if __name__ == "__main__":
    import sys
    r = prep_media(sys.argv[1])
    print(f"transcoded {len(r['transcoded'])}, failed {len(r['failed'])}")
    for f in r["failed"]:
        print("  FAILED:", f)
