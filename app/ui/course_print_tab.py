"""
Course Print tab — convert a finished course print (.docx) into a PowerPoint.

Pick the course print, choose a slide layout, Convert. The deck is written
next to the .docx (or wherever you choose) and can be opened from here.

Logic lives in app/automation/course_print/docx_to_pptx.py.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QColorDialog, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from app.automation.course_print import docx_to_pptx as d2p

_BTN = (
    "QPushButton { background: #6c757d; color: white; border-radius: 4px; padding: 0 12px; }"
    "QPushButton:hover { background: #5a6268; }"
    "QPushButton:disabled { background: #adb5bd; }"
)
_BTN_PRIMARY = (
    "QPushButton { background: #198754; color: white; border-radius: 4px; font-weight: bold; padding: 0 18px; }"
    "QPushButton:hover { background: #157347; }"
    "QPushButton:disabled { background: #6c757d; }"
)

_LAYOUT_CHOICES = [
    ("Large screenshot — transcript in speaker notes", "notes"),
    ("Screenshot + transcript panel beside it", "side"),
]


class _Signals(QObject):
    progress = Signal(int, int)
    finished = Signal(dict)
    error = Signal(str)


class CoursePrintTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._accent = d2p.DEFAULT_ACCENT
        self._last_out = ""
        self._signals = _Signals()
        self._signals.progress.connect(self._on_progress)
        self._signals.finished.connect(self._on_finished)
        self._signals.error.connect(self._on_error)
        self._setup_ui()

    # ------------------------------------------------------------------ UI
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        grp = QGroupBox("Course print → PowerPoint")
        g = QVBoxLayout(grp)

        intro = QLabel(
            "Turns a course print (.docx) into a 16:9 deck: a title slide, a divider per "
            "module, and one slide per screenshot (each captured state / layer gets its own "
            "slide). Narration, feedback and quiz answers go into the speaker notes; the "
            "correct answer is marked [Correct]."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #555;")
        g.addWidget(intro)

        row = QHBoxLayout()
        row.addWidget(QLabel("Course print:"))
        self._docx_edit = QLineEdit()
        self._docx_edit.setPlaceholderText("Select a course print .docx…")
        self._docx_edit.textChanged.connect(self._on_docx_changed)
        row.addWidget(self._docx_edit, 1)
        b = QPushButton("Browse…")
        b.setFixedWidth(80)
        b.clicked.connect(self._browse_docx)
        row.addWidget(b)
        g.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Save as:"))
        self._out_edit = QLineEdit()
        self._out_edit.setPlaceholderText("(defaults to the same name, .pptx, next to the .docx)")
        row.addWidget(self._out_edit, 1)
        b = QPushButton("Browse…")
        b.setFixedWidth(80)
        b.clicked.connect(self._browse_out)
        row.addWidget(b)
        g.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Slide layout:"))
        self._layout_combo = QComboBox()
        for label, key in _LAYOUT_CHOICES:
            self._layout_combo.addItem(label, key)
        self._layout_combo.setToolTip(
            "Large screenshot: best for presenting or sharing — the slide is the screen.\n"
            "Screenshot + panel: transcript / feedback / answer choices shown on the slide\n"
            "(font shrinks to fit; anything that doesn't fit stays in the speaker notes)."
        )
        row.addWidget(self._layout_combo)
        row.addSpacing(16)
        row.addWidget(QLabel("Accent colour:"))
        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(60, 24)
        self._color_btn.clicked.connect(self._pick_color)
        self._paint_color_btn()
        row.addWidget(self._color_btn)
        row.addStretch()
        g.addLayout(row)

        row = QHBoxLayout()
        self._convert_btn = QPushButton("Convert to PowerPoint")
        self._convert_btn.setFixedHeight(30)
        self._convert_btn.setStyleSheet(_BTN_PRIMARY)
        self._convert_btn.clicked.connect(self._convert)
        row.addWidget(self._convert_btn)
        self._open_btn = QPushButton("Open deck")
        self._open_btn.setFixedHeight(30)
        self._open_btn.setStyleSheet(_BTN)
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._open_deck)
        row.addWidget(self._open_btn)
        self._folder_btn = QPushButton("Open folder")
        self._folder_btn.setFixedHeight(30)
        self._folder_btn.setStyleSheet(_BTN)
        self._folder_btn.setEnabled(False)
        self._folder_btn.clicked.connect(self._open_folder)
        row.addWidget(self._folder_btn)
        row.addStretch()
        g.addLayout(row)

        self._progress = QProgressBar()
        self._progress.setFixedHeight(8)
        self._progress.setTextVisible(False)
        self._progress.hide()
        g.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: #555;")
        g.addWidget(self._status)

        root.addWidget(grp)
        root.addStretch()

    # ------------------------------------------------------------ actions
    def _browse_docx(self):
        start = os.path.dirname(self._docx_edit.text()) if self._docx_edit.text() else ""
        path, _ = QFileDialog.getOpenFileName(self, "Select course print", start,
                                              "Word document (*.docx)")
        if path:
            self._docx_edit.setText(path)

    def _on_docx_changed(self, text):
        # keep the default output in step with the chosen file unless the
        # user has typed their own path
        if not self._out_edit.text() or self._out_edit.property("auto"):
            if text.lower().endswith(".docx"):
                self._out_edit.setText(str(Path(text).with_suffix(".pptx")))
                self._out_edit.setProperty("auto", True)
        self._open_btn.setEnabled(False)
        self._folder_btn.setEnabled(False)

    def _browse_out(self):
        start = self._out_edit.text() or self._docx_edit.text()
        path, _ = QFileDialog.getSaveFileName(self, "Save PowerPoint as",
                                              str(Path(start).with_suffix(".pptx")) if start else "",
                                              "PowerPoint (*.pptx)")
        if path:
            if not path.lower().endswith(".pptx"):
                path += ".pptx"
            self._out_edit.setText(path)
            self._out_edit.setProperty("auto", False)

    def _pick_color(self):
        c = QColorDialog.getColor(QColor("#" + self._accent), self, "Accent colour")
        if c.isValid():
            self._accent = c.name()[1:].upper()
            self._paint_color_btn()

    def _paint_color_btn(self):
        self._color_btn.setStyleSheet(
            f"QPushButton {{ background: #{self._accent}; border: 1px solid #888; border-radius: 3px; }}")
        self._color_btn.setToolTip(f"#{self._accent} — title, divider and label colour")

    def _convert(self):
        src = self._docx_edit.text().strip().strip('"')
        if not src or not os.path.isfile(src):
            QMessageBox.warning(self, "Course print", "Choose an existing course print .docx first.")
            return
        out = self._out_edit.text().strip().strip('"') or str(Path(src).with_suffix(".pptx"))
        if os.path.abspath(out) == os.path.abspath(src):
            QMessageBox.warning(self, "Course print", "The output must be a .pptx, not the .docx.")
            return
        if os.path.exists(out):
            r = QMessageBox.question(self, "Overwrite?",
                                     f"{os.path.basename(out)} already exists. Replace it?")
            if r != QMessageBox.StandardButton.Yes:
                return
        layout = self._layout_combo.currentData()
        self._convert_btn.setEnabled(False)
        self._open_btn.setEnabled(False)
        self._folder_btn.setEnabled(False)
        self._progress.setRange(0, 0)
        self._progress.show()
        self._status.setText("Reading the course print…")
        sig, accent = self._signals, self._accent

        def work():
            try:
                summary = d2p.convert(src, out, layout=layout, accent=accent,
                                      progress=lambda i, n: sig.progress.emit(i, n))
                sig.finished.emit(summary)
            except PermissionError:
                sig.error.emit(f"Couldn't write {out} — is it open in PowerPoint? "
                               "Close it and try again.")
            except Exception as e:  # surface anything else in the status line
                sig.error.emit(f"{type(e).__name__}: {e}")

        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, i, n):
        if self._progress.maximum() != n:
            self._progress.setRange(0, n)
        self._progress.setValue(i)
        self._status.setText(f"Building slide {i} of {n}…")

    def _on_finished(self, s):
        self._progress.hide()
        self._convert_btn.setEnabled(True)
        self._last_out = s["out"]
        self._open_btn.setEnabled(True)
        self._folder_btn.setEnabled(True)
        extra = ""
        if s["text_only"]:
            extra = (f"  {s['text_only']} screen(s) had no screenshot and were laid out as "
                     "text — capture shots on your QA pass to fill them in.")
        self._status.setText(
            f"✓ {s['title']}: {s['slides']} slides ({s['sections']} module dividers, "
            f"{s['screen_slides']} screen slides) → {s['out']}.{extra}")

    def _on_error(self, msg):
        self._progress.hide()
        self._convert_btn.setEnabled(True)
        self._status.setText(f"✗ {msg}")

    def _open_deck(self):
        if self._last_out and os.path.isfile(self._last_out):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_out))

    def _open_folder(self):
        if self._last_out:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self._last_out)))
