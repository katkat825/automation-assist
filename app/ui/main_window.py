"""
Main application window.
Hosts a tab bar with one tab per automation module.
"""

from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QWidget, QLabel,
    QVBoxLayout, QMenuBar, QMenu, QApplication,
)
from PySide6.QtGui import QAction, QFont, QKeySequence
from PySide6.QtCore import Qt

from app.config import settings as cfg
from .settings_dialog import SettingsDialog
from .website_tab import WebsiteTab
from .scorm_qa_tab import ScormQaTab
from .scorm_runtime_tab import ScormRuntimeTab
from .scheduler_tab import SchedulerTab
from .od_regression_tab import OdRegressionTab

_DEFAULT_FONT_SIZE = 0  # resolved at runtime from QApplication default
_MIN_FONT_SIZE = 7
_MAX_FONT_SIZE = 24
_STEP = 1


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Automation Assist")
        self.resize(860, 620)

        # Resolve the system default font size once
        self._system_font_size = QApplication.font().pointSize()
        if self._system_font_size <= 0:
            self._system_font_size = 9  # fallback

        self._setup_menu()
        self._setup_tabs()
        self._apply_saved_font_size()

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _setup_menu(self):
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("File")
        settings_action = QAction("Settings…", self)
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)
        file_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = menu_bar.addMenu("View")

        zoom_in = QAction("Zoom In", self)
        zoom_in.setShortcut(QKeySequence("Ctrl+="))
        zoom_in.triggered.connect(self._zoom_in)
        view_menu.addAction(zoom_in)

        zoom_out = QAction("Zoom Out", self)
        zoom_out.setShortcut(QKeySequence("Ctrl+-"))
        zoom_out.triggered.connect(self._zoom_out)
        view_menu.addAction(zoom_out)

        reset_zoom = QAction("Reset Zoom", self)
        reset_zoom.setShortcut(QKeySequence("Ctrl+0"))
        reset_zoom.triggered.connect(self._zoom_reset)
        view_menu.addAction(reset_zoom)

    # ------------------------------------------------------------------
    # Zoom
    # ------------------------------------------------------------------

    def _current_font_size(self) -> int:
        return QApplication.font().pointSize()

    def _set_font_size(self, size: int):
        size = max(_MIN_FONT_SIZE, min(_MAX_FONT_SIZE, size))
        font = QApplication.font()
        font.setPointSize(size)
        QApplication.setFont(font)

        # Persist to settings
        s = cfg.load()
        s.setdefault("ui", {})["font_size"] = size
        cfg.save(s)

        self.setWindowTitle(f"Automation Assist  ({size}pt)")

    def _zoom_in(self):
        self._set_font_size(self._current_font_size() + _STEP)

    def _zoom_out(self):
        self._set_font_size(self._current_font_size() - _STEP)

    def _zoom_reset(self):
        self._set_font_size(self._system_font_size)
        self.setWindowTitle("Automation Assist")

        # Clear the saved override
        s = cfg.load()
        s.setdefault("ui", {})["font_size"] = 0
        cfg.save(s)

    def _apply_saved_font_size(self):
        s = cfg.load()
        saved = s.get("ui", {}).get("font_size", 0)
        if saved > 0:
            self._set_font_size(saved)

    def _setup_tabs(self):
        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        tabs.addTab(WebsiteTab(), "Website")
        tabs.addTab(ScormQaTab(), "SCORM QA")
        tabs.addTab(ScormRuntimeTab(), "SCORM Runtime QA")
        tabs.addTab(OdRegressionTab(), "OD Regress Tests")

        # Placeholder tabs for future modules
        # (JAWS / screen-reader checks live inside the SCORM QA tab — gated
        # on the Dual-path checkbox — so there is no separate JAWS tab.)
        tabs.addTab(self._placeholder("SQL Queries", "SQL query runner — coming soon"), "SQL Queries")
        tabs.addTab(SchedulerTab(), "Scheduler")

        self.setCentralWidget(tabs)

    def _placeholder(self, title: str, message: str) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label = QLabel(message)
        label.setStyleSheet("color: #6c757d;")
        layout.addWidget(label)
        return w

    def _open_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()
