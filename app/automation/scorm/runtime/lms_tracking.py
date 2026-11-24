"""LMS tracking timeline + suspend points for the QA Companion.

Pure helpers (no browser, no Qt) so they can be unit-tested:

* **Tracking timeline** — from the recording stub's SCORM API transcript
  (``window.__scormLog``), pick out the calls that matter for completion and
  scoring (status, score, interactions, Commit, Terminate) and say *if* and
  *when* each was sent, and on which screen.
* **Suspend points** — snapshot the stub's CMI data model at a chosen moment
  and turn a saved snapshot back into a launch seed, so a course can be
  restarted exactly where the snapshot was taken (``cmi.entry = resume``).

The stub stamps each SetValue / Commit / Terminate with the slide id that was
visible when the call was made (``slide``), so attributions are exact rather
than the nearest polling tick.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- tracking

# Status / score elements, SCORM 2004 then 1.2. Order = display order.
STATUS_KEYS = (
    "cmi.completion_status",
    "cmi.success_status",
    "cmi.score.raw",
    "cmi.score.scaled",
    "cmi.core.lesson_status",
    "cmi.core.score.raw",
)
_SET_FNS = ("SetValue", "LMSSetValue")
_COMMIT_FNS = ("Commit", "LMSCommit")
_TERM_FNS = ("Terminate", "LMSFinish")
_INTERACTION_RE = re.compile(r"^cmi\.interactions\.(\d+)\.(.+)$")
# interaction fields worth a timeline line (id / answer / result)
_INTERACTION_FIELDS = ("id", "result", "learner_response", "student_response")


def _is_12(log: list) -> bool:
    return any(e.get("fn", "").startswith("LMS") for e in log)


def status_keys_for(log: list) -> tuple:
    """The status keys to report for this transcript's SCORM version."""
    if _is_12(log):
        return ("cmi.core.lesson_status", "cmi.core.score.raw")
    return ("cmi.completion_status", "cmi.success_status",
            "cmi.score.raw", "cmi.score.scaled")


def classify(entry: dict) -> str | None:
    """'status' | 'interaction' | 'commit' | 'terminate' | 'exit' | None."""
    fn = entry.get("fn", "")
    if fn in _COMMIT_FNS:
        return "commit"
    if fn in _TERM_FNS:
        return "terminate"
    if fn not in _SET_FNS:
        return None
    key = (entry.get("args") or [""])[0]
    if key in STATUS_KEYS:
        return "status"
    if key in ("cmi.exit", "cmi.core.exit"):
        return "exit"
    m = _INTERACTION_RE.match(key)
    if m and m.group(2) in _INTERACTION_FIELDS:
        return "interaction"
    return None


def _clock(ms) -> str:
    try:
        d = datetime.fromtimestamp(float(ms) / 1000.0)
    except (TypeError, ValueError, OSError):
        return "??:??:??"
    return d.strftime("%H:%M:%S.") + f"{d.microsecond // 1000:03d}"


def screen_label(slide_id, screens: dict | None) -> str:
    """'screen 2.3' when the companion index knows the slide, else the id."""
    if not slide_id:
        return "launch (before the first screen)"
    info = (screens or {}).get(slide_id) or {}
    num = info.get("screen_number")
    if num:
        return f"screen {num}"
    title = info.get("slide_title")
    return f"slide '{title}'" if title else f"slide {slide_id}"


def format_event(entry: dict, screens: dict | None = None) -> str:
    """One human-readable timeline line for a tracking call."""
    kind = classify(entry)
    where = screen_label(entry.get("slide"), screens)
    when = _clock(entry.get("t"))
    fn = entry.get("fn", "")
    if kind in ("status", "interaction", "exit"):
        key, val = (list(entry.get("args") or []) + ["", ""])[:2]
        return f"{when} | {where} | {key} = {val}"
    if kind == "commit":
        return f"{when} | {where} | {fn}"
    if kind == "terminate":
        return f"{when} | {where} | {fn} (course closed the session)"
    return f"{when} | {where} | {fn} {entry.get('args')}"


def tracking_events(log: list) -> list:
    """The subset of the transcript that matters for tracking, in order."""
    return [e for e in log if classify(e)]


def summarize(log: list, screens: dict | None = None) -> dict:
    """If and when each tracking value was sent. JSON-ready."""
    keys = status_keys_for(log)
    status = {}
    for k in keys:
        sets = [e for e in log if e.get("fn") in _SET_FNS
                and (e.get("args") or [None])[0] == k]
        if not sets:
            status[k] = {"sent": False}
            continue
        first, last = sets[0], sets[-1]
        status[k] = {
            "sent": True,
            "times_sent": len(sets),
            "first_value": str(first["args"][1]) if len(first["args"]) > 1 else "",
            "first_at": _clock(first.get("t")),
            "first_on": screen_label(first.get("slide"), screens),
            "last_value": str(last["args"][1]) if len(last["args"]) > 1 else "",
            "last_at": _clock(last.get("t")),
            "last_on": screen_label(last.get("slide"), screens),
            "values": [str(e["args"][1]) if len(e["args"]) > 1 else ""
                       for e in sets],
        }

    # interactions: group by index; keep id/result/response + when id was sent
    inter = {}
    for e in log:
        if e.get("fn") not in _SET_FNS:
            continue
        m = _INTERACTION_RE.match((e.get("args") or [""])[0])
        if not m or m.group(2) not in _INTERACTION_FIELDS:
            continue
        rec = inter.setdefault(int(m.group(1)), {})
        field = "response" if m.group(2) in ("learner_response",
                                              "student_response") else m.group(2)
        rec[field] = str(e["args"][1]) if len(e["args"]) > 1 else ""
        if field == "id":
            rec["at"] = _clock(e.get("t"))
            rec["on"] = screen_label(e.get("slide"), screens)
    interactions = [dict(index=n, **inter[n]) for n in sorted(inter)]

    commits = [e for e in log if e.get("fn") in _COMMIT_FNS]
    terms = [e for e in log if e.get("fn") in _TERM_FNS]
    return {
        "scorm_version": "1.2" if _is_12(log) else "2004",
        "status": status,
        "interactions": interactions,
        "commits": len(commits),
        "last_commit_at": _clock(commits[-1].get("t")) if commits else "",
        "terminated": bool(terms),
        "terminated_at": _clock(terms[-1].get("t")) if terms else "",
    }


def format_summary(summary: dict) -> str:
    """Plain-text block for the run log / summary file."""
    out = [f"LMS TRACKING SUMMARY (SCORM {summary['scorm_version']})"]
    for k, v in summary["status"].items():
        if not v["sent"]:
            out.append(f"  {k}: NEVER SENT")
            continue
        line = (f"  {k}: last '{v['last_value']}' at {v['last_at']} on "
                f"{v['last_on'].replace('screen ', '')}")
        if v["times_sent"] > 1:
            line += (f"  (sent {v['times_sent']}x; first '{v['first_value']}' "
                     f"at {v['first_at']} on {v['first_on'].replace('screen ', '')})")
        out.append(line)
    n = len(summary["interactions"])
    out.append(f"  question interactions sent: {n}")
    for r in summary["interactions"]:
        out.append(f"    #{r['index']}: {r.get('id', '(no id)')} -> "
                   f"{r.get('result', '(no result)')}"
                   + (f"  [{r['at']} on {r['on'].replace('screen ', '')}]"
                      if r.get("at") else ""))
    out.append(f"  commits: {summary['commits']}"
               + (f" (last {summary['last_commit_at']})" if summary["commits"] else ""))
    out.append("  terminate: " + (f"yes at {summary['terminated_at']}"
                                  if summary["terminated"] else
                                  "not called (normal if the window was "
                                  "closed before the course exited)"))
    return "\n".join(out)


# ---------------------------------------------------------------- suspend points

SUSPEND_DIRNAME = "suspend_points"
_SESSION_KEYS = ("cmi.entry", "cmi.core.entry", "cmi.exit", "cmi.core.exit",
                 "cmi.session_time", "cmi.core.session_time")


def make_snapshot(cmi: dict, *, course: str, slide_id: str | None,
                  screens: dict | None = None, taken_at: datetime | None = None
                  ) -> dict:
    """Freeze the course's CMI state as a suspend point.

    Everything the course wrote is kept (suspend_data, location, status,
    score, interactions), so a resume sees what a real LMS would return.
    """
    taken_at = taken_at or datetime.now()
    info = (screens or {}).get(slide_id or "") or {}
    return {
        "kind": "scorm_suspend_point",
        "course": course,
        "taken_at": taken_at.isoformat(timespec="seconds"),
        "slide_id": slide_id or "",
        "screen_number": info.get("screen_number", ""),
        "slide_title": info.get("slide_title", ""),
        "suspend_data_length": len(cmi.get("cmi.suspend_data", "") or ""),
        "cmi": dict(cmi),
    }


def seed_from_snapshot(snap: dict) -> dict:
    """CMI seed that relaunches the course from a snapshot.

    Sets entry=resume (both versions), clears the previous session's exit
    and session time, and drops derived ``_count`` values (the stub
    recomputes them)."""
    cmi = dict(snap.get("cmi") or {})
    for k in _SESSION_KEYS:
        cmi.pop(k, None)
    for k in [k for k in cmi if k.endswith("._count")]:
        cmi.pop(k)
    if any(k.startswith("cmi.core.") for k in cmi):
        cmi["cmi.core.entry"] = "resume"
    cmi["cmi.entry"] = "resume"
    return cmi


def suspend_dir(data_dir, zip_path) -> Path:
    return Path(data_dir) / Path(zip_path).stem / SUSPEND_DIRNAME


def snapshot_filename(snap: dict) -> str:
    stamp = snap["taken_at"].replace(":", "").replace("-", "")
    scr = (snap.get("screen_number") or "unknown").replace(".", "-")
    return f"{stamp}_s{scr}.json"


def save_snapshot(snap: dict, folder) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / snapshot_filename(snap)
    n = 2
    while path.exists():
        path = folder / f"{path.stem.split('__')[0]}__{n}.json"
        n += 1
    path.write_text(json.dumps(snap, indent=1), encoding="utf-8")
    return path


def load_snapshot(path) -> dict:
    snap = json.loads(Path(path).read_text(encoding="utf-8"))
    if snap.get("kind") != "scorm_suspend_point" or "cmi" not in snap:
        raise ValueError(f"{path} is not a saved suspend point")
    return snap


def list_snapshots(folder) -> list:
    """[(path, snapshot)] newest first; unreadable files are skipped."""
    out = []
    for p in sorted(Path(folder).glob("*.json"), reverse=True):
        try:
            out.append((p, load_snapshot(p)))
        except (ValueError, OSError, json.JSONDecodeError):
            continue
    return out


def describe_snapshot(snap: dict) -> str:
    """Short label for a picker: 'Screen 2.3 — Title (2026-11-24 09:15)'."""
    when = snap.get("taken_at", "").replace("T", " ")[:16]
    scr = snap.get("screen_number") or "?"
    title = snap.get("slide_title") or snap.get("slide_id") or ""
    return f"Screen {scr} — {title} ({when})"
