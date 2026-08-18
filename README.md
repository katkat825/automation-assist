# Automation Assist

A desktop tool that automates the QA and maintenance work I do most often. This primarily includes
static analysis of SCORM e-learning packages, plus WordPress upkeep and regression runs.

Built for my own daily use. Published so it can be read, not reused.

---

## Why it exists

I tracked where my time actually went and found course QA was the single largest cost.
Most of it was mechanical: reading every slide looking for
doubled words, inconsistent terminology, menu labels that drifted from slide titles.

So the tool checks the mechanical parts, and I spend my attention on the parts that
need judgment.

There are also aspects that exist to assist the manual QA and discussions with course developers.

---

## Three decisions worth explaining

**No check ever reports a guaranteed pass.**
Every result is either a hard failure or information that narrows down human review.
No checks currently specify that they are not definitive, since this tool is only used by me.
The SCORM API check is the clearest example: if `cmi.completion_status`
isn't set, that's a guaranteed failure and nothing else proceeds until it's fixed.
If it *is* set, I still verify it at runtime, because it can be configured correctly
in the package and still fail live.

**The WordPress flows fail hard, on purpose.**
Anything involving a login does not fail gracefully by design. Logins periodically surface
things that need human review, such as an email verification prompt, a policy change notice,
an upcoming-changes interstitial. Swallowing those and continuing would mean automating
right past the one thing that actually needed attention. A hard failure *is* the
notification.

**Vocabulary is separated from logic.**
The static checks change constantly as I find new things worth checking. All the word
lists, terminology pairs and regexes live in a single data module
(`app/automation/scorm/checks/wordlists.py`) with no logic in it, so adding a term
never means reading around a check implementation.

---

## What's in it

| Tab | Does |
|---|---|
| **SCORM QA** | Static analysis of a course package: structure, SCORM API wiring, terminology, duplicates, spelling/spacing, questions, screen inventory, generated manual checklist |
| **SCORM Runtime QA** | Drives a packaged course in a real browser and verifies tracking. Work in progress (see the Status: SCORM Runtime QA section below) |
| **Website** | WordPress update check (reports only, never applies), a cache-clear for a known Elementor rendering bug, and a site health smoke test |
| **OD Regress Tests** | Wraps a Playwright regression suite so it runs from one button (suite not included — see below) |
| **Scheduler** | Read-only view of what runs automatically and when |

Accessibility checks run inside SCORM QA rather than as a separate feature. Courses here
ship both a rich-media and a screen-reader-friendly path in one package, so the
accessibility checks only apply when a course is marked dual-path.

### Not included in this repository

The Playwright regression suite the **OD Regress Tests** tab drives is kept private. The
specs encode how an internal LMS behaves — certificate rules, scoring thresholds, reporting
filters — which isn't mine to publish.

What's here is the runner: the tab, the subprocess handling, and the folder resolution in
`app/automation/regression/runner.py`. It expects a Playwright project at `od_regress_tests/`,
or wherever `regression.project_dir` points. Without one, that tab reports the folder is
missing and the rest of the app is unaffected.

---

## Status: SCORM Runtime QA

This one is unfinished and may stay that way.

The reason I keep at it: a course can determine internally whether something
was clicked or answered correctly. It is therefore deterministic. 
Anything deterministic should be automatiable with a tool.
In practice SCORM is old, and authoring tools expose items differently and inconsistently at runtime.

Several approaches have failed. Each failure has narrowed the problem and suggested the next thing
to try, which is why it's still worth periodic effort. It is the most time-expensive thing
I own, so the payoff would be real.

---

## Stack

Python 3.10+, PySide6 (Qt), Playwright, BeautifulSoup. Packaged to a single Windows
executable with PyInstaller.

## Running it

```bash
pip install -r requirements.txt
playwright install

cp settings.example.json settings.json   # then fill in your own values
python main.py
```

`settings.json` holds credentials and stays local — it is gitignored and never committed.

## Tests

```bash
pytest
```

225 tests, no course packages or network required — the checks are fed
hand-built `ScormData` objects from `tests/conftest.py`, so the suite runs in a
clean checkout.

What they cover, and why those things:

- **Detection thresholds**, because a check that cries wolf gets ignored. The
  duplicate tests assert as much about what is *not* flagged (titles reused
  across modules, short UI labels, the expected twice-over of a dual-path
  course) as about what is.
- **Accessible-path detection**, since every accessibility check builds on it —
  including the parallel-structure rule for courses whose accessible scenes
  have no title.
- **The two failure modes that have actually bitten**: the WordPress history
  replay rendering differently from a live run, and the Playwright project path
  resolving into PyInstaller's temp directory in a frozen build.

The suite is mutation-tested. Sixteen deliberate regressions — dropping a
`@dataclass`, downgrading the `cmi.completion_status` failure to a warning,
reintroducing the frozen-path bug, removing the word boundaries from
terminology matching — are each confirmed to turn the suite red. Passing tests
are only worth what they'd catch.

That exercise paid for itself twice: two of the mutations initially survived,
which exposed assertions that were checking a section *count* where they should
have been checking which checks actually ran.

## License

Copyright © Kathleen Malone. All rights reserved.

This repository is public so it can be reviewed as a work sample. It is not licensed for
reuse, modification, or redistribution. See [LICENSE](LICENSE).
