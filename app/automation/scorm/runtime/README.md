# SCORM Runtime QA — runtime package (v1)

Implements RUNTIME_QA_DESIGN.md (see §11 for v1 status). Lives at
`app/automation/scorm/runtime/` alongside the existing static modules.

## Run

    pip install playwright && playwright install chromium
    python -m app.automation.scorm.runtime.cli course.zip [--data-dir DIR]
        [--scenario quiz_pass|quiz_fail] [--locked] [--headed]
        [--extract-dir DIR]   # reuse an extracted package between runs

Outputs land in `DATA_DIR/<course>/`: `runtime_model.json`, `scenarios.json`,
`runlog_<scenario>.json`, `artifacts/<scenario>/` (screenshot + DOM per
BLOCKED step), `runtime_report.txt` / `.json`.

## Modules

- `runtime_model.py` — static extensions (§4): click effects, layer/choice
  bindings, question inventory (M), hub-aware route, menu index, durations.
- `harness.py` + `scorm_stub.js` — local server + recording SCORM API
  (1.2 + 2004) on the launcher window; cmi transcript is ground truth.
- `planner.py` — quiz_pass / quiz_fail scenarios + explicit not-enumerated list.
- `driver.py` — deterministic Playwright driver: [data-model-id] clicks,
  .slide.cs-<id> arrival, seek-to-end, modal clearing, reveal chains,
  gate negative/positive tests, interaction sweep, menu skip for text entry.
- `verifier.py` — pure verdicts: VERIFIED / BLOCKED / UNCHECKED-BY-DESIGN /
  BYPASSED + N-of-M coverage; three-way question match.
- `reporter.py` — merged course report; green only when denominators close.
- `media_prep.py` — VP9/Opus transcode of package mp4s (test Chromium has no
  H.264/AAC). Marker-cached; visuals stay the human pass's job.

## Notes

- Run against UNLOCKED QA builds. `--locked` waits out slide durations
  (bounded 90s/slide) instead of seeking — expensive by design, never guesses.
- Heuristics quarantine (design §6.6) is still empty — nothing has needed it;
  any future patch goes in a separate module and caps verdicts at BYPASSED.
