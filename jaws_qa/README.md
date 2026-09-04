# JAWS transcript logger — apply & test

Spike artifact for `docs/JAWS_TRANSCRIPT_DESIGN.md`. `transcript_logger.jss` logs JAWS
speech to `C:\jaws_qa\transcript.log`. Not verified complete — see the `<< VERIFY` notes
in the script.

## Apply (JAWS 2021 — the reversible way)

`.jcf` is a config file, not a script. The default scripts live in the *program* folder;
don't edit those. `Ctrl+Shift+D` opens the default script and, on save, writes a **copy to
your user folder** and leaves the shipped one untouched — that's the reversible bit.

1. Create the folder `C:\jaws_qa\`.
2. Back up your user settings folder first:
   `%APPDATA%\Freedom Scientific\JAWS\2021\Settings\enu\` (copy it somewhere safe).
3. Open the JAWS Script Manager (`Insert+0`), then press `Ctrl+Shift+D` to open the
   default script.
4. Paste the two functions from `transcript_logger.jss` (`TLog` and the `SayString` hook)
   at the end.
5. Compile with `Ctrl+S`, then restart JAWS.

(Alternative: if `MyExtensions.jss` doesn't exist, you can create it — `File > New`, save as
`MyExtensions.jss` in the user `enu` folder — paste the functions there and compile. The
default script already loads MyExtensions, so it takes effect the same way.)

## Revert (clean)

Delete `default.jss` and `default.jsb` (or `MyExtensions.*` if you used that) from your
user settings folder `%APPDATA%\Freedom Scientific\JAWS\2021\Settings\enu\`, then restart
JAWS. If anything looks off, restore the backup from step 2 wholesale.

## Run the course for manual JAWS QA (no auto-driver)

    python -m app.automation.scorm.runtime.cli <course.zip> --observe

Hosts the course in a headed window and does **not** drive it, so JAWS/you navigate.
Screen changes are logged (with timestamps) to `<data-dir>/<course>/screens.log` — override
with `--screens-log C:\jaws_qa\screens.log`. Press `Ctrl+C` in the console when done.
(This is what makes Spike A testable without the runtime tool moving the course on its own.)

## Test checklist (very brief)

- [ ] JAWS reads the course when it runs in the tool's harness browser (Spike A).
- [ ] `transcript.log` fills as JAWS speaks (file writes work — VERIFY 2).
- [ ] Read a few slides by ear, compare to the log — anything JAWS said missing? (coverage — VERIFY 1).
- [ ] Companion writes a timestamped marker to `screens.log` on each slide change.
- [ ] Merge by timestamp files utterances under the right screen — spot-check a boundary slide.
- [ ] After Revert, JAWS behaves normally again.

> Later refinement: scope the hook to the harness browser app (an app-named `.jss`) instead
> of `default.jss`, so it's active only during a QA session. Global is fine for the spike.
