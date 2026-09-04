"""
SCORM Runtime QA Tab — drives a SCORM package through the runtime harness.

Unlike the static SCORM QA tab (which only inspects the package), this tab
launches the course in a headless browser, plays each completion scenario
(quiz pass / fail), records the SCORM API transcript, and verifies the
recorded completion/score against the course's scoring rule.

The heavy lifting lives in ``app.automation.scorm.runtime.cli.main`` — the
same entry point used from the command line:

    python -m app.automation.scorm.runtime.cli <course.zip> [--data-dir DIR] ...

Here we call ``cli.main(argv)`` on a worker thread and stream its printed
progress into the log pane, so the whole pipeline (build model -> plan ->
drive -> verify -> report) runs without blocking the UI.
"""

import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont, QDesktopServices
from PySide6.QtCore import QUrl
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
    def __init__(self, argv, signals):
        super().__init__(daemon=True)
        self._argv = argv
        self._signals = signals

    def run(self):
        stream = _StreamToSignal(self._signals.line)
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = stream
        try:
            rc = runtime_cli.main(self._argv)
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

        self._run_btn.setEnabled(False)
        self._companion_btn.setEnabled(False)
        self._open_btn.setEnabled(False)
        self._progress.show()
        self._status.setText("Running runtime QA — this can take several minutes…")
        self._log.clear()

        signals = _RunSignals()
        signals.line.connect(self._on_line)
        signals.finished.connect(self._on_finished)
        signals.error.connect(self._on_error)

        _RunWorker(argv, signals).start()

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

        self._run_btn.setEnabled(False)
        self._companion_btn.setEnabled(False)
        self._open_btn.setEnabled(False)
        self._progress.show()
        self._status.setText(
            "Companion open — navigate the course yourself; "
            "close the course window to stop."
        )
        self._log.clear()

        signals = _RunSignals()
        signals.line.connect(self._on_line)
        signals.finished.connect(self._on_finished)
        signals.error.connect(self._on_error)

        _RunWorker(argv, signals).start()

    def _on_line(self, line: str):
        self._log.append(line)
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_finished(self, rc: int):
        self._progress.hide()
        self._run_btn.setEnabled(True)
        self._companion_btn.setEnabled(True)
        if self._out_dir and Path(self._out_dir).exists():
            self._open_btn.setEnabled(True)
        self._status.setText(
            f"Done (exit code {rc}). Outputs in: {self._out_dir}"
        )

    def _on_error(self, msg: str):
        self._progress.hide()
        self._run_btn.setEnabled(True)
        self._companion_btn.setEnabled(True)
        self._status.setText(f"Error: {msg}")
        QMessageBox.critical(self, "Runtime QA Error", msg)

    def _open_output(self):
        if self._out_dir and Path(self._out_dir).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._out_dir))
