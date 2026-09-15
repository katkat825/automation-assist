"""
SCORM Runtime QA Tab — drives a SCORM package through the runtime harness.

Unlike the static SCORM QA tab (which only inspects the package), this tab
launches the course in a headless browser, plays each completion scenario
(quiz pass / fail), records the SCORM API transcript, and verifies the
recorded completion/score against the course's scoring rule.

The heavy lifting lives in ``app.automation.scorm.runtime.cli.main`` — the
same entry point used from the command line:

    python -m app.automation.scorm.runtime.cli <course.zip> [--data-dir DIR] ...

Here we call ``cli.main(argv, cancel=...)`` on a worker thread and stream its
printed progress into the log pane, so the whole pipeline (build model -> plan
-> drive -> verify -> report) runs without blocking the UI. A cancel event
lets the reviewer stop a run that is taking too long — the slow pre-browser
stages (unzip / media transcode / launch) poll it and bail out cleanly, so the
tab never sits "loading" with no way out.
"""

import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QObject, QTimer, QUrl
from PySide6.QtGui import QFont, QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QGroupBox, QComboBox, QCheckBox, QTextEdit,
    QProgressBar, QSizePolicy, QMessageBox,
)

from ..paths import DATA_DIR
from ..automation.scorm.runtime import cli as runtime_cli


_MONO = QFont("Courier New")

# Runtime outputs land under this directory: DATA_DIR/runtime_qa/<course>/
_RUNTIME_OUT_DIR = DATA_DIR / "runtime_qa"

# Return code the CLI uses when a run was cancelled by the reviewer.
_RC_CANCELLED = 130


class _RunSignals(QObject):
    line = Signal(str)      # a line of progress output
    finished = Signal(int)  # return code
    error = Signal(str)     # unexpected exception


class _StreamToSignal:
    """File-like object that forwards writes to a Qt signal, line-buffered."""

    def __init__(self, signal):
        self._signal = signal
        self._buf = ""

    def write(self, text):
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._signal.emit(line)

    def flush(self):
        if self._buf:
            self._signal.emit(self._buf)
            self._buf = ""


class _RunWorker(threading.Thread):
    def __init__(self, argv, signals, cancel):
        super().__init__(daemon=True)
        self._argv = argv
        self._signals = signals
        self._cancel = cancel

    def run(self):
        stream = _StreamToSignal(self._signals.line)
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = stream
        try:
            rc = runtime_cli.main(self._argv, cancel=self._cancel)
            stream.flush()
            self._signals.finished.emit(int(rc or 0))
        except SystemExit as e:  # argparse errors
            stream.flush()
            self._signals.finished.emit(int(e.code or 0))
        except Exception as e:  # noqa: BLE001
            stream.flush()
            self._signals.error.emit(repr(e))
        finally:
            sys.stdout, sys.stderr = old_out, old_err


class ScormRuntimeTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._zip_path = ""
        self._out_dir = ""
        self._cancel = None          # threading.Event for the active run
        self._mode = ""              # "run" | "companion" (for status text)
        self._run_start = 0.0        # monotonic start time of the active run
        self._ready = False          # companion window reported READY
        self._hb = QTimer(self)      # heartbeat: keeps status honest while busy
        self._hb.setInterval(1000)
        self._hb.timeout.connect(self._heartbeat)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # --- File input ---
        file_group = QGroupBox("Package")
        fg = QVBoxLayout(file_group)
        fg.setSpacing(4)

        zip_row = QHBoxLayout()
        zip_row.addWidget(QLabel("SCORM zip:"))
        self._zip_label = QLabel("(none)")
        self._zip_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._zip_label.setStyleSheet("color: #555;")
        zip_row.addWidget(self._zip_label)
        zip_btn = QPushButton("Browse…")
        zip_btn.setFixedWidth(80)
        zip_btn.clicked.connect(self._browse_zip)
        zip_row.addWidget(zip_btn)
        fg.addLayout(zip_row)
        root.addWidget(file_group)

        # --- Options ---
        opt_group = QGroupBox("Options")
        og = QHBoxLayout(opt_group)

        og.addWidget(QLabel("Scenario:"))
        self._scenario = QComboBox()
        self._scenario.addItem("All", None)
        self._scenario.addItem("quiz_pass", "quiz_pass")
        self._scenario.addItem("quiz_fail", "quiz_fail")
        self._scenario.setToolTip("Run every planned scenario, or just one.")
        og.addWidget(self._scenario)

        og.addSpacing(16)
        self._locked_chk = QCheckBox("Locked build")
        self._locked_chk.setToolTip(
            "Locked build: no timeline seeking. The driver waits out each slide's\n"
            "duration instead (bounded 90s/slide) — slower, but matches a build\n"
            "where seek is disabled. Leave unchecked for UNLOCKED QA builds."
        )
        og.addWidget(self._locked_chk)

        og.addSpacing(12)
        self._headed_chk = QCheckBox("Headed (show browser)")
        self._headed_chk.setToolTip("Run with a visible browser window instead of headless.")
        og.addWidget(self._headed_chk)

        og.addSpacing(12)
        self._dual_path_chk = QCheckBox("Dual-path course")
        self._dual_path_chk.setToolTip(
            "Treat as a dual-path (accessible) course.\n"
            "Adds the accessibility checks to the companion panel."
        )
        og.addWidget(self._dual_path_chk)

        og.addSpacing(12)
        self._capture_chk = QCheckBox("Capture screenshots (course print)")
        self._capture_chk.setToolTip(
            "Only applies to ‘Open QA Companion’.\n"
            "While you drive the course, capture each screen's base state once its\n"
            "on-screen text stops animating in (waits out the timeline), plus any\n"
            "layer state you snap with the panel's ‘Capture state’ button (or\n"
            "Ctrl+Shift+S). A screen whose text never settles (looping animation) is\n"
            "flagged for a manual grab rather than shot mid-animation. Images + a\n"
            "manifest land in the output folder for the course-print generator.\n"
            "Off by default — turn on for your final verification pass."
        )
        og.addWidget(self._capture_chk)

        og.addStretch()
        root.addWidget(opt_group)

        # --- Action buttons ---
        action_row = QHBoxLayout()
        self._run_btn = QPushButton("Run Runtime QA")
        self._run_btn.setFixedHeight(30)
        self._run_btn.clicked.connect(self._run)
        action_row.addWidget(self._run_btn)

        self._companion_btn = QPushButton("Open QA Companion")
        self._companion_btn.setFixedHeight(30)
        self._companion_btn.setToolTip(
            "Open the course in a window with the per-screen QA companion panel.\n"
            "The course is NOT auto-driven — navigate it yourself (e.g. with JAWS).\n"
            "Close the course window to stop."
        )
        self._companion_btn.clicked.connect(self._run_companion)
        action_row.addWidget(self._companion_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setFixedHeight(30)
        self._stop_btn.setFixedWidth(90)
        self._stop_btn.setEnabled(False)
        self._stop_btn.setToolTip(
            "Cancel the current run. Safe to press if it looks stuck loading —\n"
            "the current stage (unzip / media transcode / launch) will stop and\n"
            "any browser window will close."
        )
        self._stop_btn.clicked.connect(self._stop)
        action_row.addWidget(self._stop_btn)

        self._open_btn = QPushButton("Open Output Folder")
        self._open_btn.setFixedHeight(30)
        self._open_btn.setFixedWidth(150)
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._open_output)
        action_row.addWidget(self._open_btn)

        clear_btn = QPushButton("Clear Log")
        clear_btn.setFixedHeight(30)
        clear_btn.setFixedWidth(90)
        clear_btn.clicked.connect(lambda: self._log.clear())
        action_row.addWidget(clear_btn)

        action_row.addStretch()
        root.addLayout(action_row)

        # --- Progress bar (hidden until running) ---
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)   # indeterminate
        self._progress.setFixedHeight(6)
        self._progress.hide()
        root.addWidget(self._progress)

        # --- Status ---
        self._status = QLabel("")
        self._status.setStyleSheet("color: #555; font-style: italic;")
        root.addWidget(self._status)

        # --- Log pane ---
        root.addWidget(QLabel("Run log:"))
        self._log = QTextEdit()
        self._log.setFont(_MONO)
        self._log.setReadOnly(True)
        self._log.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        root.addWidget(self._log)

    # ------------------------------------------------------------------
    # File browsing
    # ------------------------------------------------------------------

    def _browse_zip(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select SCORM zip", "", "SCORM Package (*.zip)"
        )
        if path:
            self._zip_path = path
            self._zip_label.setText(os.path.basename(path))
            self._zip_label.setToolTip(path)
            self._zip_label.setStyleSheet("color: #000;")

    # ------------------------------------------------------------------
    # Run lifecycle helpers
    # ------------------------------------------------------------------

    def _start_worker(self, argv, mode, status):
        """Common setup for both entry points: reset state, flip the buttons,
        start the heartbeat, and launch the worker with a fresh cancel event."""
        self._mode = mode
        self._ready = False
        self._run_start = time.monotonic()
        self._cancel = threading.Event()

        self._run_btn.setEnabled(False)
        self._companion_btn.setEnabled(False)
        self._open_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._progress.show()
        self._status.setText(status)
        self._log.clear()
        self._hb.start()

        signals = _RunSignals()
        signals.line.connect(self._on_line)
        signals.finished.connect(self._on_finished)
        signals.error.connect(self._on_error)

        _RunWorker(argv, signals, self._cancel).start()

    def _reset_buttons(self):
        self._hb.stop()
        self._progress.hide()
        self._run_btn.setEnabled(True)
        self._companion_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def _run(self):
        if not self._zip_path:
            QMessageBox.warning(self, "No File", "Please select a SCORM zip file first.")
            return

        _RUNTIME_OUT_DIR.mkdir(parents=True, exist_ok=True)
        self._out_dir = str(_RUNTIME_OUT_DIR / Path(self._zip_path).stem)

        argv = [self._zip_path, "--data-dir", str(_RUNTIME_OUT_DIR)]
        scenario = self._scenario.currentData()
        if scenario:
            argv += ["--scenario", scenario]
        if self._locked_chk.isChecked():
            argv.append("--locked")
        if self._headed_chk.isChecked():
            argv.append("--headed")

        self._start_worker(
            argv, "run",
            "Starting runtime QA — preparing the package "
            "(courses with video can take a few minutes)…")

    def _run_companion(self):
        """Open the course + per-screen companion panel (no auto-driver)."""
        if not self._zip_path:
            QMessageBox.warning(self, "No File", "Please select a SCORM zip file first.")
            return

        _RUNTIME_OUT_DIR.mkdir(parents=True, exist_ok=True)
        self._out_dir = str(_RUNTIME_OUT_DIR / Path(self._zip_path).stem)

        argv = [self._zip_path, "--observe", "--data-dir", str(_RUNTIME_OUT_DIR)]
        if self._dual_path_chk.isChecked():
            argv.append("--dual-path")
        if self._capture_chk.isChecked():
            argv.append("--capture-shots")

        self._start_worker(
            argv, "companion",
            "Preparing the package before the window opens "
            "(courses with video can take a few minutes)…")

    def _stop(self):
        if self._cancel is not None:
            self._cancel.set()
        self._stop_btn.setEnabled(False)
        self._status.setText("Stopping — cancelling the current stage…")

    # ------------------------------------------------------------------
    # Worker callbacks
    # ------------------------------------------------------------------

    def _heartbeat(self):
        """Keep the status label honest while a run is in progress so the tab
        never looks frozen. The log pane shows the detailed per-stage output;
        this is the at-a-glance 'still working' line."""
        if self._cancel is not None and self._cancel.is_set():
            return  # leave the "Stopping…" message in place
        if self._ready:
            return  # companion window is up; status already says so
        secs = int(time.monotonic() - self._run_start)
        if secs < 30:
            hint = ""
        elif secs < 120:
            hint = " — large videos can take a few minutes. Press Stop to cancel."
        else:
            hint = " — if it looks stuck, press Stop."
        base = ("Preparing the package before the window opens"
                if self._mode == "companion" else "Running runtime QA")
        self._status.setText(f"{base}… ({secs}s){hint}")

    def _on_line(self, line: str):
        self._log.append(line)
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())
        # The CLI prints READY once the companion window is actually open — flip
        # the status then so it no longer claims to be "preparing".
        if self._mode == "companion" and not self._ready and "READY" in line:
            self._ready = True
            self._status.setText(
                "Companion open — navigate the course yourself; "
                "close the course window (or press Stop) to finish.")

    def _on_finished(self, rc: int):
        self._reset_buttons()
        if self._out_dir and Path(self._out_dir).exists():
            self._open_btn.setEnabled(True)
        if rc == _RC_CANCELLED:
            self._status.setText("Stopped. No further work was done.")
        else:
            self._status.setText(f"Done (exit code {rc}). Outputs in: {self._out_dir}")
        self._cancel = None

    def _on_error(self, msg: str):
        self._reset_buttons()
        self._status.setText(f"Error: {msg}")
        self._cancel = None
        QMessageBox.critical(self, "Runtime QA Error", msg)

    def _open_output(self):
        if self._out_dir and Path(self._out_dir).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._out_dir))
