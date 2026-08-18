"""
OD Regression Tests tab.

Single button: runs `npx playwright install chrome-beta` (in a popup console
window so progress + any UAC prompt are visible), then runs the regression
suite headlessly in the in-app log pane. When the run finishes, the HTML
report opens in the default browser — along with the external run-log
spreadsheet, if one is configured via regression.run_log_url in settings —
and the Chrome Beta version is appended as the final line.
"""

import threading

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QTextEdit, QMessageBox,
)

from app.automation.regression import runner


class _Signals(QObject):
    log = Signal(str)
    finished = Signal(int)   # final exit code (install failure OR test exit code)


class OdRegressionTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._signals = _Signals()
        self._signals.log.connect(self._on_log)
        self._signals.finished.connect(self._on_finished)

        self._running = False
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        intro = QLabel(
            f"Installs the latest Chrome Beta, then runs the Playwright regression "
            f"suite headlessly.\nProject folder: {runner.project_dir()}"
        )
        intro.setStyleSheet("color: #6c757d;")
        root.addWidget(intro)

        root.addWidget(self._build_actions_group())
        root.addWidget(self._build_log_group(), stretch=1)

    def _build_actions_group(self) -> QGroupBox:
        group = QGroupBox("Actions")
        layout = QHBoxLayout(group)

        self._run_btn = QPushButton("▶  Run Full Regression")
        self._run_btn.setFixedHeight(32)
        self._run_btn.clicked.connect(self._on_run_clicked)
        self._run_btn.setStyleSheet(
            "QPushButton { background: #198754; color: white; border-radius: 4px; font-weight: bold; padding: 0 18px; }"
            "QPushButton:hover { background: #157347; }"
            "QPushButton:disabled { background: #6c757d; }"
        )

        self._open_report_btn = QPushButton("Open Last Report")
        self._open_report_btn.setFixedHeight(32)
        self._open_report_btn.clicked.connect(self._on_open_report_clicked)
        self._open_report_btn.setStyleSheet(
            "QPushButton { background: #6c757d; color: white; border-radius: 4px; padding: 0 14px; }"
            "QPushButton:hover { background: #5a6268; }"
        )

        layout.addWidget(self._run_btn)
        layout.addStretch()
        layout.addWidget(self._open_report_btn)
        return group

    def _build_log_group(self) -> QGroupBox:
        group = QGroupBox("Output")
        layout = QVBoxLayout(group)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText(
            "Output from the Playwright commands will appear here."
        )
        self._log.setStyleSheet(
            "font-family: Consolas, monospace; background: #f8f9fa; color: #222;"
        )
        layout.addWidget(self._log)
        return group

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_run_clicked(self):
        if self._running:
            return
        if not runner.project_dir().is_dir():
            looked_in = "\n".join(f"  • {p}" for p in runner.candidate_project_dirs())
            QMessageBox.critical(
                self, "Folder Not Found",
                f"Playwright project folder not found.\n\nLooked in:\n{looked_in}\n\n"
                "The regression suite is kept in a separate private repository, so it "
                "is not part of a public checkout of this project. It also can't be "
                "bundled inside the .exe — it needs node_modules on disk and writes "
                "its report back into the folder.\n\n"
                "Fix by either:\n"
                "  • placing the Playwright project at one of the paths above, or\n"
                "  • setting regression.project_dir in Settings to its full path."
            )
            return

        self._running = True
        self._run_btn.setEnabled(False)
        self._run_btn.setText("Running…")
        self._log.clear()

        threading.Thread(target=self._worker, daemon=True).start()

    def _on_open_report_clicked(self):
        if not runner.open_report():
            QMessageBox.information(
                self, "No Report Yet",
                f"No HTML report was found at:\n{runner.report_index()}\n\n"
                "Run the regression tests first."
            )

    # ------------------------------------------------------------------
    # Worker plumbing
    # ------------------------------------------------------------------

    def _worker(self):
        code = runner.run_install_and_tests(
            log=lambda m: self._signals.log.emit(m)
        )
        self._signals.finished.emit(code)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    @Slot(str)
    def _on_log(self, msg: str):
        self._log.append(msg)

    @Slot(int)
    def _on_finished(self, code: int):
        self._running = False
        self._run_btn.setEnabled(True)
        self._run_btn.setText("▶  Run Full Regression")

        if code == 0:
            self._log.append("\nRegression tests completed (all passed).")
        else:
            # Playwright exits non-zero when any test fails — this is expected
            # and does not mean the run itself errored. We still open the report.
            self._log.append(f"\nRegression tests finished with exit code {code} "
                             "(one or more tests may have failed — check the report).")

        # Always open the HTML report and the run-log spreadsheet.
        if not runner.open_report():
            self._log.append(f"(No HTML report found at {runner.report_index()})")
        if not runner.open_run_log():
            self._log.append(
                "(No run-log URL configured — set regression.run_log_url in settings.json)"
            )

        # Final line: the Chrome Beta version that was tested against.
        banner = runner.get_chrome_beta_version_banner()
        if banner:
            self._log.append(f"\n{banner}")
        else:
            self._log.append("\n(Chrome Beta version could not be determined.)")

        # Manual verification reminder — surface as a pop-up so it isn't missed.
        QMessageBox.information(
            self,
            "Verify Email Delivery",
            "Please check your inbox and confirm you received all 3 emails:\n\n"
            "   1.  New-user welcome email\n"
            "   2.  Workflow-triggered email\n"
            "   3.  Report-results email",
        )
