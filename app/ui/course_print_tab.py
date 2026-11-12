"""
Course Print tab.

1. Build course print (Word): pick the SCORM package and the screenshots
   captured on the QA pass (SCORM Runtime tab → QA Companion with "Capture
   screenshots" ticked); writes the course print .docx.
2. Course print → PowerPoint: convert a finished course print into a deck.

Logic lives in app/automation/course_print/build_print.py and
docx_to_pptx.py.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from app.automation.course_print import build_print as bp
from app.automation.course_print import docx_to_pptx as d2p
from app.paths import DATA_DIR


def _shots_search_roots():
    """Where the QA Companion writes captures: <data dir>/runtime_qa (running
    from source) and %LOCALAPPDATA%/AutomationAssist/runtime_qa (the .exe)."""
    roots = [DATA_DIR / "runtime_qa"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "AutomationAssist" / "runtime_qa")
    return roots

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

class _Signals(QObject):
    progress = Signal(int, int)
    finished = Signal(dict)
    error = Signal(str)
    build_progress = Signal(int, int)
    build_finished = Signal(dict)
    build_error = Signal(str)


class CoursePrintTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._accent = d2p.DEFAULT_ACCENT
        self._last_out = ""
        self._signals = _Signals()
        self._signals.progress.connect(self._on_progress)
        self._signals.finished.connect(self._on_finished)
        self._signals.error.connect(self._on_error)
        self._signals.build_progress.connect(self._on_build_progress)
        self._signals.build_finished.connect(self._on_build_finished)
        self._signals.build_error.connect(self._on_build_error)
        self._last_print = ""
        self._setup_ui()

    # ------------------------------------------------------------------ UI
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        root.addWidget(self._build_group())

        grp = QGroupBox("Course print → PowerPoint")
        g = QVBoxLayout(grp)

        intro = QLabel(
            "Turns a course print (.docx) into a 16:9 deck: a title slide, a divider per "
            "module, and one slide per screenshot (each captured state / layer gets its own "
            "slide), with the narration, feedback and quiz answers in a panel beside the "
            "screenshot and the correct answer ticked. Nothing goes in the speaker notes."
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
        row.addWidget(QLabel("Accent colour:"))
        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(60, 24)
        self._color_btn.clicked.connect(self._pick_color)
        self._paint_color_btn()
        row.addWidget(self._color_btn)
        row.addSpacing(16)
        self._images_chk = QCheckBox("Include screenshots")
        self._images_chk.setChecked(True)
        self._images_chk.setToolTip(
            "Unticked: a text-only deck. Each screen becomes one full-width slide with its\n"
            "narration, feedback and answer choices; a much smaller file.")
        row.addWidget(self._images_chk)
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

    def _build_group(self):
        grp = QGroupBox("Build course print (Word)")
        g = QVBoxLayout(grp)

        intro = QLabel(
            "Builds the course print from the SCORM package and the screenshots you "
            "captured on your QA pass (SCORM Runtime tab → Open QA Companion with "
            "\u201cCapture screenshots\u201d ticked). Titles, modules, narration, on-screen "
            "text, layers and quiz answers come from the package; each screen gets its "
            "screenshot, and any screen you didn't capture gets a red placeholder. You can "
            "delete bad shots from the folder or drop extra images in; extras are placed "
            "by the screen in their file name (e.g. 012_s3-2_extra.png or Screen 12.png)."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #555;")
        g.addWidget(intro)

        def path_row(label, placeholder, browse):
            row = QHBoxLayout()
            lab = QLabel(label)
            lab.setFixedWidth(110)
            row.addWidget(lab)
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            row.addWidget(edit, 1)
            b = QPushButton("Browse…")
            b.setFixedWidth(80)
            b.clicked.connect(browse)
            row.addWidget(b)
            g.addLayout(row)
            return edit

        self._pkg_edit = path_row("SCORM package:", "Select the course .zip…", self._browse_pkg)
        self._pkg_edit.textChanged.connect(self._on_pkg_changed)
        self._shots_edit = path_row(
            "Screenshots:", "course_print_shots folder (found automatically when it exists)",
            self._browse_shots)
        self._shots_edit.textChanged.connect(self._update_cleanup_btn)
        self._print_out_edit = path_row(
            "Save as:", "(defaults to CoursePrint-<course>_<number>.docx next to the .zip)",
            self._browse_print_out)

        row = QHBoxLayout()
        self._footer_chk = QCheckBox("Confidentiality footer with page numbers")
        self._footer_chk.setChecked(True)
        row.addWidget(self._footer_chk)
        row.addSpacing(16)
        self._watermark_chk = QCheckBox("CONFIDENTIAL watermark")
        self._watermark_chk.setChecked(True)
        self._watermark_chk.setToolTip("Faint grey diagonal CONFIDENTIAL behind every page,\n"
                                       "like the hand-made course prints.")
        row.addWidget(self._watermark_chk)
        row.addStretch()
        g.addLayout(row)

        row = QHBoxLayout()
        self._build_btn = QPushButton("Build course print")
        self._build_btn.setFixedHeight(30)
        self._build_btn.setStyleSheet(_BTN_PRIMARY)
        self._build_btn.clicked.connect(self._build_print)
        row.addWidget(self._build_btn)
        self._open_print_btn = QPushButton("Open document")
        self._open_print_btn.setFixedHeight(30)
        self._open_print_btn.setStyleSheet(_BTN)
        self._open_print_btn.setEnabled(False)
        self._open_print_btn.clicked.connect(self._open_print)
        row.addWidget(self._open_print_btn)
        self._print_folder_btn = QPushButton("Open folder")
        self._print_folder_btn.setFixedHeight(30)
        self._print_folder_btn.setStyleSheet(_BTN)
        self._print_folder_btn.setEnabled(False)
        self._print_folder_btn.clicked.connect(self._open_print_folder)
        row.addWidget(self._print_folder_btn)
        row.addStretch()
        self._cleanup_btn = QPushButton("Clean up screenshots…")
        self._cleanup_btn.setFixedHeight(30)
        self._cleanup_btn.setStyleSheet(_BTN)
        self._cleanup_btn.setToolTip("Send this course's screenshots folder to the Recycle Bin\n"
                                     "once the course print is final (asks first).")
        self._cleanup_btn.setEnabled(False)
        self._cleanup_btn.clicked.connect(self._cleanup_shots)
        row.addWidget(self._cleanup_btn)
        g.addLayout(row)

        self._build_progress = QProgressBar()
        self._build_progress.setFixedHeight(8)
        self._build_progress.setTextVisible(False)
        self._build_progress.hide()
        g.addWidget(self._build_progress)

        self._build_status = QLabel("")
        self._build_status.setWordWrap(True)
        self._build_status.setStyleSheet("color: #555;")
        g.addWidget(self._build_status)
        return grp

    # ------------------------------------------------------- build actions
    def _browse_pkg(self):
        start = os.path.dirname(self._pkg_edit.text()) if self._pkg_edit.text() else ""
        path, _ = QFileDialog.getOpenFileName(self, "Select SCORM package", start,
                                              "SCORM package (*.zip)")
        if path:
            self._pkg_edit.setText(path)

    def _on_pkg_changed(self, text):
        text = text.strip().strip('"')
        if not text.lower().endswith(".zip"):
            return
        # pick up the QA Companion's captures for this package, unless the
        # user already chose a folder themselves
        if not self._shots_edit.text() or self._shots_edit.property("auto"):
            found = bp.find_shots_dir(text, _shots_search_roots()) or ""
            self._shots_edit.setText(found)
            self._shots_edit.setProperty("auto", True)
        if os.path.isfile(text) and (not self._print_out_edit.text()
                                     or self._print_out_edit.property("auto")):
            try:
                self._print_out_edit.setText(bp.default_output(text))
                self._print_out_edit.setProperty("auto", True)
            except Exception:
                pass  # not a readable package; Build will report it
        self._open_print_btn.setEnabled(False)
        self._print_folder_btn.setEnabled(False)

    def _browse_shots(self):
        start = self._shots_edit.text() or str(_shots_search_roots()[0])
        path = QFileDialog.getExistingDirectory(self, "Select the course_print_shots folder", start)
        if path:
            self._shots_edit.setText(path)
            self._shots_edit.setProperty("auto", False)

    def _browse_print_out(self):
        start = self._print_out_edit.text() or (
            str(Path(self._pkg_edit.text()).with_suffix(".docx")) if self._pkg_edit.text() else "")
        path, _ = QFileDialog.getSaveFileName(self, "Save course print as", start,
                                              "Word document (*.docx)")
        if path:
            if not path.lower().endswith(".docx"):
                path += ".docx"
            self._print_out_edit.setText(path)
            self._print_out_edit.setProperty("auto", False)

    def _build_print(self):
        pkg = self._pkg_edit.text().strip().strip('"')
        if not pkg or not os.path.isfile(pkg):
            QMessageBox.warning(self, "Course print", "Choose the course's SCORM .zip first.")
            return
        shots = self._shots_edit.text().strip().strip('"') or None
        if shots and not shots.lower().endswith(".json") and not os.path.isdir(shots):
            QMessageBox.warning(self, "Course print", "That screenshots folder doesn't exist.")
            return
        if not shots:
            r = QMessageBox.question(
                self, "No screenshots",
                "No screenshots folder is set, so every screen will get a placeholder "
                "instead of a screenshot. Build a text-only draft anyway?")
            if r != QMessageBox.StandardButton.Yes:
                return
        out = self._print_out_edit.text().strip().strip('"') or bp.default_output(pkg)
        if not out.lower().endswith(".docx"):
            out += ".docx"
        if os.path.exists(out):
            r = QMessageBox.question(self, "Overwrite?",
                                     f"{os.path.basename(out)} already exists. Replace it?")
            if r != QMessageBox.StandardButton.Yes:
                return
        self._build_btn.setEnabled(False)
        self._open_print_btn.setEnabled(False)
        self._print_folder_btn.setEnabled(False)
        self._build_progress.setRange(0, 0)
        self._build_progress.show()
        self._build_status.setText("Reading the SCORM package…")
        sig, footer = self._signals, self._footer_chk.isChecked()
        watermark = self._watermark_chk.isChecked()

        def work():
            try:
                summary = bp.build(pkg, out, shots=shots, footer=footer, watermark=watermark,
                                   progress=lambda i, n: sig.build_progress.emit(i, n))
                sig.build_finished.emit(summary)
            except PermissionError:
                sig.build_error.emit("Couldn't write the course print — is it open in Word? "
                                     "Close it and try again.")
            except Exception as e:
                sig.build_error.emit(f"{type(e).__name__}: {e}")

        threading.Thread(target=work, daemon=True).start()

    def _shots_folder(self):
        t = self._shots_edit.text().strip().strip('"')
        if t.lower().endswith(".json"):
            t = os.path.dirname(t)
        return t

    def _update_cleanup_btn(self, *_):
        if hasattr(self, "_cleanup_btn"):
            self._cleanup_btn.setEnabled(bp.is_shots_folder(self._shots_folder()))

    def _cleanup_shots(self):
        folder = self._shots_folder()
        if not bp.is_shots_folder(folder):
            QMessageBox.warning(self, "Clean up screenshots",
                                "That doesn't look like a course print screenshots folder, "
                                "so nothing was removed.")
            self._update_cleanup_btn()
            return
        n, size = bp.shots_folder_summary(folder)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Clean up screenshots")
        box.setText(f"Send this screenshots folder ({n} image(s), {size / 1_048_576:.1f} MB) "
                    "to the Recycle Bin?")
        box.setInformativeText(f"{folder}\n\nBuild the course print first if you still need "
                               "it. You can restore the folder from the Recycle Bin.")
        box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Ok:
            return
        try:
            bp.send_to_recycle_bin(folder)
        except Exception as e:
            QMessageBox.critical(self, "Clean up screenshots", str(e))
            self._update_cleanup_btn()
            return
        self._shots_edit.setText("")
        self._build_status.setText(f"✓ Screenshots folder moved to the Recycle Bin: {folder}")

    def _on_build_progress(self, i, n):
        if self._build_progress.maximum() != n:
            self._build_progress.setRange(0, n)
        self._build_progress.setValue(i)
        self._build_status.setText(f"Adding screen {i} of {n}…")

    def _on_build_finished(self, s):
        self._build_progress.hide()
        self._build_btn.setEnabled(True)
        self._last_print = s["out"]
        self._open_print_btn.setEnabled(True)
        self._print_folder_btn.setEnabled(True)
        notes = []
        if s["placeholders"]:
            notes.append(f"{s['placeholders']} screen(s) had no screenshot and got a placeholder.")
        if s["unmatched_shots"]:
            notes.append(f"{s['unmatched_shots']} screenshot(s) didn't match any screen in this "
                         "package — was the capture from a different version?")
        if s["missing_files"]:
            notes.append(f"{s['missing_files']} deleted screenshot(s) skipped; a screen whose "
                         "auto shot was deleted now leads with its first manual capture.")
        if s["extra_shots"]:
            notes.append(f"{s['extra_shots']} extra screenshot(s) added by file name.")
        if s["unplaced_shots"]:
            notes.append(f"{s['unplaced_shots']} extra screenshot(s) didn't name a screen; "
                         "they're under \u201cUnplaced screenshots\u201d at the end.")
        self._build_status.setText(
            f"✓ {s['title']}: {s['screens']} screens in {s['modules']} modules, "
            f"{s['images']} screenshots → {s['out']}." + ("  " + " ".join(notes) if notes else ""))
        # hand the new print to the PowerPoint converter below
        self._docx_edit.setText(s["out"])

    def _on_build_error(self, msg):
        self._build_progress.hide()
        self._build_btn.setEnabled(True)
        self._build_status.setText(f"✗ {msg}")

    def _open_print(self):
        if self._last_print and os.path.isfile(self._last_print):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_print))

    def _open_print_folder(self):
        if self._last_print:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self._last_print)))

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
        self._convert_btn.setEnabled(False)
        self._open_btn.setEnabled(False)
        self._folder_btn.setEnabled(False)
        self._progress.setRange(0, 0)
        self._progress.show()
        self._status.setText("Reading the course print…")
        sig, accent = self._signals, self._accent
        images = self._images_chk.isChecked()
        self._images_used = images

        def work():
            try:
                summary = d2p.convert(src, out, accent=accent, images=images,
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
        if s["text_only"] and self._images_used:
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
