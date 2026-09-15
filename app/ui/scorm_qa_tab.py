"""
SCORM QA Tab — static analysis of a SCORM zip and/or Articulate .story file.

Layout
------
Top bar  : zip file input | story file input | Run Analysis | Clear Files buttons
Inner tabs:
  Static Checks   — all automated check results, each section copyable
  Questions       — questions + correct answers, copyable
  Manual Checklist — plain-text checklist, single copy button
  Screen Table    — sortable table of Scene / Screen ID / Number / Title
  Global Search   — type a term and search across all slide text
"""

import os
import re
import threading

from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QTabWidget, QTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QProgressBar, QSizePolicy, QApplication, QMessageBox,
    QCheckBox, QScrollArea, QFrame, QComboBox,
)

from ..automation.scorm.parser import parse_scorm_zip, parse_story_file
from ..automation.scorm.checks import run_all_checks, global_search
from ..automation.scorm.checks.wordlists import (
    _TARGET_LANGUAGES, detect_language_from_filename,
)
from ..automation.scorm.automation_model import build_automation_model, format_report


_SCREEN_ID_RE = re.compile(r"^m(\d+)s(\d+)$")
_NATSORT_SPLIT_RE = re.compile(r"(\d+)")


def _natural_key(text: str) -> tuple:
    """Sort key that treats embedded numbers numerically.

    "3.10" sorts after "3.2"; "m1s10" sorts after "m1s2".
    Each chunk is tagged (0, int) for digits or (1, lowered-str) for text
    so mixed-content cells still compare without raising TypeError.
    """
    parts = _NATSORT_SPLIT_RE.split(str(text))
    return tuple(
        (0, int(p)) if p.isdigit() else (1, p.lower())
        for p in parts
    )


class _NaturalSortItem(QTableWidgetItem):
    """QTableWidgetItem that sorts numerically-embedded strings naturally."""
    def __lt__(self, other):
        if isinstance(other, QTableWidgetItem):
            return _natural_key(self.text()) < _natural_key(other.text())
        return super().__lt__(other)


# ---------------------------------------------------------------------------
# Worker signal bridge (parser runs on a thread; signals cross to UI thread)
# ---------------------------------------------------------------------------

class _WorkerSignals(QObject):
    finished = Signal(object, object, object)   # (Report, ScormData, AutomationModel|None)
    error = Signal(str)


class _AnalysisWorker(threading.Thread):
    def __init__(self, zip_path, story_path, signals, dual_path=False, non_english=False, target_lang=None):
        super().__init__(daemon=True)
        self.zip_path = zip_path
        self.story_path = story_path
        self.signals = signals
        self.dual_path = dual_path
        self.non_english = non_english
        self.target_lang = target_lang

    def run(self):
        try:
            scorm_data = parse_scorm_zip(self.zip_path)
            story_data = parse_story_file(self.story_path) if self.story_path else None
            report = run_all_checks(
                scorm_data,
                story_data,
                dual_path=self.dual_path,
                non_english=self.non_english,
                target_lang=self.target_lang,
            )
            # Automation model is a separate feature; never let it fail the run.
            try:
                automodel = build_automation_model(self.zip_path, data=scorm_data)
            except Exception:
                automodel = None
            self.signals.finished.emit(report, scorm_data, automodel)
        except Exception as e:
            self.signals.error.emit(str(e))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MONO = QFont("Courier New")
_SECTION_SEP = "\n" + ("─" * 60) + "\n"


def _copy_button(label="Copy") -> QPushButton:
    btn = QPushButton(label)
    btn.setFixedWidth(80)
    btn.setToolTip("Copy this section to clipboard")
    return btn


def _copy_text(widget):
    """Copy the full plain text of a QTextEdit to the clipboard."""
    QApplication.clipboard().setText(widget.toPlainText())


def _make_text_area(read_only=True) -> QTextEdit:
    ta = QTextEdit()
    ta.setFont(_MONO)
    ta.setReadOnly(read_only)
    ta.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
    return ta


def _level_prefix(level: str) -> str:
    return {"pass": "✔", "warn": "⚠", "fail": "✖", "info": "·"}.get(level, " ")


def _section_to_text(section) -> str:
    lines = [section.title, "=" * len(section.title)]
    for item in section.items:
        prefix = _level_prefix(item.level)
        lines.append(f"  {prefix}  {item.message}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Static Checks sub-tab
# ---------------------------------------------------------------------------

class _StaticChecksWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Single copyable text area covering all sections
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("All static check results:"))
        hdr.addStretch()
        copy_btn = _copy_button("Copy All")
        hdr.addWidget(copy_btn)
        layout.addLayout(hdr)

        self._text = _make_text_area()
        layout.addWidget(self._text)

        copy_btn.clicked.connect(lambda: _copy_text(self._text))

    def populate(self, sections):
        parts = []
        for sec in sections:
            # Skip Questions section here (has its own tab)
            if sec.title == "Questions & Correct Answers":
                continue
            parts.append(_section_to_text(sec))
        self._text.setPlainText(_SECTION_SEP.join(parts))


# ---------------------------------------------------------------------------
# Questions sub-tab
# ---------------------------------------------------------------------------

class _QuestionsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Questions and correct answers (✓ = correct):"))
        hdr.addStretch()
        copy_btn = _copy_button("Copy All")
        hdr.addWidget(copy_btn)
        layout.addLayout(hdr)

        self._text = _make_text_area()
        layout.addWidget(self._text)
        copy_btn.clicked.connect(lambda: _copy_text(self._text))

    def populate(self, sections):
        for sec in sections:
            if sec.title != "Questions & Correct Answers":
                continue
            lines = [
                sec.title,
                "=" * len(sec.title),
            ]
            first_question = True
            for item in sec.items:
                msg = item.message
                prefix = _level_prefix(item.level)
                if msg.startswith("["):
                    if not first_question:
                        lines.append("")
                    first_question = False
                    upper = msg.upper()
                    if "KNOWLEDGE CHECK" in upper:
                        tag = '<span style="color:#2563EB;">&#9679; KC</span>'
                    elif "PRE-TEST" in upper or "PRETEST" in upper:
                        tag = '<span style="color:#FF1493;">&#9679; PT</span>'
                    elif "QUIZ" in upper:
                        tag = '<span style="color:#16A34A;">&#9679; QZ</span>'
                    else:
                        tag = f"  {prefix} "
                    escaped = msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    lines.append(f"  {tag}  {escaped}")
                else:
                    escaped = msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    lines.append(f"  {prefix}  {escaped}")
            body = "\n".join(lines)
            self._text.setHtml(
                '<div style="font-family: Consolas, \'Courier New\', monospace;'
                ' white-space: pre;">'
                + body
                + "</div>"
            )
            return
        self._text.setPlainText("No questions found.")


# ---------------------------------------------------------------------------
# Manual Checklist sub-tab
# ---------------------------------------------------------------------------

class _ChecklistWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._structure = []   # list of entry dicts, mutated in-place

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Manual QA checklist — check items off as you go:"))
        hdr.addStretch()
        reset_btn = QPushButton("Reset All")
        reset_btn.setFixedWidth(80)
        reset_btn.setToolTip("Uncheck all completed items")
        reset_btn.clicked.connect(self._reset)
        hdr.addWidget(reset_btn)
        layout.addLayout(hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._content_layout.setSpacing(3)
        self._content_layout.setContentsMargins(6, 6, 6, 6)
        scroll.setWidget(self._content)
        layout.addWidget(scroll)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def populate(self, checklist_text: str):
        self._parse(checklist_text)
        self._rebuild()

    def clear(self):
        self._structure = []
        self._rebuild()

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse(self, text: str):
        self._structure = []
        current_section = ""
        item_index = 0

        for line in text.split("\n"):
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))

            if stripped.startswith("[ ]"):
                item_text = stripped[4:]   # drop "[ ] "
                self._structure.append({
                    "type": "item",
                    "text": item_text,
                    "indent": indent,
                    "section": current_section,
                    "index": item_index,
                    "done": False,
                })
                item_index += 1
            elif stripped.startswith("---") or stripped.startswith("==="):
                pass  # drop separator lines; section styling handles this visually
            elif stripped == "":
                self._structure.append({"type": "blank"})
            else:
                current_section = stripped
                self._structure.append({"type": "header", "text": stripped})

    # ------------------------------------------------------------------
    # UI rebuild
    # ------------------------------------------------------------------

    def _rebuild(self):
        # Remove all existing widgets
        while self._content_layout.count():
            child = self._content_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        pending = [e for e in self._structure if e.get("type") == "item" and not e["done"]]
        done    = [e for e in self._structure if e.get("type") == "item" and e["done"]]

        pending_sections = {e["section"] for e in pending}

        # --- Render pending items, preserving section structure ---
        for entry in self._structure:
            t = entry.get("type")
            if t == "item":
                if entry["done"]:
                    continue
                self._content_layout.addWidget(self._make_checkbox(entry))

            elif t == "header":
                if entry["text"] not in pending_sections:
                    continue
                lbl = QLabel(f"<b>{entry['text']}</b>")
                lbl.setStyleSheet("margin-top: 6px;")
                self._content_layout.addWidget(lbl)

            elif t == "blank":
                self._content_layout.addSpacing(4)

        # --- Completed section ---
        if done:
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setFrameShadow(QFrame.Shadow.Sunken)
            self._content_layout.addSpacing(8)
            self._content_layout.addWidget(sep)

            done_lbl = QLabel("<b>Completed</b>")
            done_lbl.setStyleSheet("color: #888; margin-top: 4px;")
            self._content_layout.addWidget(done_lbl)

            for entry in done:
                cb = self._make_checkbox(entry)
                cb.setStyleSheet("color: #999; text-decoration: line-through;")
                self._content_layout.addWidget(cb)

        self._content_layout.addStretch()

    def _make_checkbox(self, entry: dict) -> QWidget:
        indent = entry.get("indent", 0)
        cb = QCheckBox(entry["text"])
        cb.blockSignals(True)
        cb.setChecked(entry["done"])
        cb.blockSignals(False)
        cb.stateChanged.connect(lambda state, e=entry: self._on_check(state, e))

        if indent > 0:
            container = QWidget()
            row = QHBoxLayout(container)
            row.setContentsMargins(max(indent * 2, 20), 0, 0, 0)
            row.setSpacing(0)
            row.addWidget(cb)
            row.addStretch()
            return container
        return cb

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_check(self, state: int, entry: dict):
        entry["done"] = bool(state)
        self._rebuild()

    def _reset(self):
        for e in self._structure:
            if e.get("type") == "item":
                e["done"] = False
        self._rebuild()


# ---------------------------------------------------------------------------
# Screen Table sub-tab
# ---------------------------------------------------------------------------

class _ScreenTableWidget(QWidget):
    COLUMNS = [
        ("Screen #", "screen_number"),
        ("Acc. Screen #", "acc_screen_number"),
        ("Path", "path"),
        ("Screen ID", "screen_id"),
        ("In Menu", "in_menu"),
        ("Module / Scene", "scene_title"),
        ("Slide Title", "slide_title"),
        ("LMS ID", "lms_id"),
        ("Ext. Link", "has_external_link"),
    ]
    _CHECKMARK_KEYS = {"has_external_link"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list = []
        self._current_offset: int = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Screen / slide map:"))
        hdr.addStretch()

        self._offset_label = QLabel("")
        self._offset_label.setStyleSheet("color: #555; font-style: italic;")
        hdr.addWidget(self._offset_label)

        set_m1_btn = QPushButton("Set Selected as M1")
        set_m1_btn.setToolTip(
            "Renumber modules so the selected row's module becomes M1.\n"
            "Modules before it collapse into M0; later modules shift to fit."
        )
        set_m1_btn.clicked.connect(self._set_selected_as_m1)
        hdr.addWidget(set_m1_btn)

        reset_num_btn = QPushButton("Reset Numbering")
        reset_num_btn.setToolTip("Restore the original M#/S# numbering.")
        reset_num_btn.clicked.connect(self._reset_numbering)
        hdr.addWidget(reset_num_btn)

        copy_btn = _copy_button("Copy TSV")
        hdr.addWidget(copy_btn)
        layout.addLayout(hdr)

        self._table = QTableWidget()
        self._table.setColumnCount(len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels([c[0] for c in self.COLUMNS])
        self._table.horizontalHeader().setSectionResizeMode(
            6, QHeaderView.ResizeMode.Stretch
        )
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        layout.addWidget(self._table)

        copy_btn.clicked.connect(self._copy_tsv)

    def populate(self, rows: list):
        self._rows = [dict(r) for r in rows]
        self._current_offset = 0
        self._render(self._rows)

    def clear(self):
        self._rows = []
        self._current_offset = 0
        self._offset_label.setText("")
        self._table.setRowCount(0)

    def _render(self, rows: list):
        self._table.setRowCount(0)
        self._table.setSortingEnabled(False)
        for row in rows:
            r = self._table.rowCount()
            self._table.insertRow(r)
            for col_idx, (_, key) in enumerate(self.COLUMNS):
                val = row.get(key, "")
                if isinstance(val, bool):
                    if key in self._CHECKMARK_KEYS:
                        val = "\u2713" if val else ""
                    else:
                        val = "Yes" if val else "No"
                item = _NaturalSortItem(str(val))
                if key in self._CHECKMARK_KEYS:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(r, col_idx, item)
        self._table.setSortingEnabled(True)
        self._table.resizeColumnsToContents()
        # keep Slide Title column stretched
        self._table.horizontalHeader().setSectionResizeMode(
            6, QHeaderView.ResizeMode.Stretch
        )
        # Cap Acc. Screen # so a long path label doesn't blow it out
        acc_col = next(
            i for i, (_, k) in enumerate(self.COLUMNS) if k == "acc_screen_number"
        )
        self._table.setColumnWidth(acc_col, 100)

    def _set_selected_as_m1(self):
        if not self._rows:
            return
        selected = self._table.currentRow()
        if selected < 0:
            QMessageBox.information(
                self, "No Selection",
                "Click a row in the table first, then press this button."
            )
            return
        screen_id_col = next(
            i for i, (_, k) in enumerate(self.COLUMNS) if k == "screen_id"
        )
        item = self._table.item(selected, screen_id_col)
        if not item:
            return
        m = _SCREEN_ID_RE.match(item.text())
        if not m:
            QMessageBox.warning(
                self, "Cannot Renumber",
                f"Selected row has no module number ({item.text() or 'empty'})."
            )
            return
        view_module = int(m.group(1))
        # Convert the viewed module back to its original module number,
        # then compute the offset needed to make it M1.
        original_module = view_module + self._current_offset
        new_offset = original_module - 1
        if new_offset == self._current_offset:
            return
        self._apply_offset(new_offset)

    def _reset_numbering(self):
        if self._current_offset == 0:
            return
        self._apply_offset(0)

    def _apply_offset(self, offset: int):
        new_rows = []
        slide_counter: dict = {}
        for row in self._rows:
            new_row = dict(row)
            sid = row.get("screen_id", "")
            m = _SCREEN_ID_RE.match(sid)
            if m:
                old_mod = int(m.group(1))
                new_mod = max(0, old_mod - offset)
                slide_counter[new_mod] = slide_counter.get(new_mod, 0) + 1
                new_row["screen_id"] = f"m{new_mod}s{slide_counter[new_mod]}"
            new_rows.append(new_row)
        self._current_offset = offset
        if offset == 0:
            self._offset_label.setText("")
        else:
            sign = "−" if offset > 0 else "+"
            self._offset_label.setText(f"Renumbered ({sign}{abs(offset)})")
        self._render(new_rows)

    def _copy_tsv(self):
        rows = []
        headers = [c[0] for c in self.COLUMNS]
        rows.append("\t".join(headers))
        for r in range(self._table.rowCount()):
            row_data = []
            for c in range(self._table.columnCount()):
                item = self._table.item(r, c)
                row_data.append(item.text() if item else "")
            rows.append("\t".join(row_data))
        QApplication.clipboard().setText("\n".join(rows))


# ---------------------------------------------------------------------------
# Global Search sub-tab
# ---------------------------------------------------------------------------

class _SearchWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scorm_data = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a word or phrase and press Search…")
        self._input.returnPressed.connect(self._do_search)
        search_row.addWidget(self._input)
        search_btn = QPushButton("Search")
        search_btn.setFixedWidth(80)
        search_btn.clicked.connect(self._do_search)
        search_row.addWidget(search_btn)
        copy_btn = _copy_button("Copy")
        copy_btn.clicked.connect(lambda: _copy_text(self._results))
        search_row.addWidget(copy_btn)
        layout.addLayout(search_row)

        self._results = _make_text_area()
        self._results.setPlaceholderText("Search results will appear here.")
        layout.addWidget(self._results)

    def set_data(self, scorm_data):
        self._scorm_data = scorm_data

    def _do_search(self):
        if not self._scorm_data:
            self._results.setPlainText("Run analysis first.")
            return
        query = self._input.text().strip()
        if not query:
            self._results.setPlainText("Enter a search term.")
            return
        sec = global_search(self._scorm_data, query)
        self._results.setPlainText(_section_to_text(sec))


# ---------------------------------------------------------------------------
# Automation Map sub-tab
# ---------------------------------------------------------------------------

class _AutomationMapWidget(QWidget):
    """Shows the runtime-automation model: per-slide continue buttons (resolved
    by action), branch forks, Next-gate requirements, and question answer keys.

    This is the map a Playwright driver would follow to click through the
    course deterministically — surfaced here so it can be eyeballed per course.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Runtime automation map (continue buttons, branches, Next-gates, answer keys):"))
        hdr.addStretch()
        copy_btn = _copy_button("Copy All")
        hdr.addWidget(copy_btn)
        layout.addLayout(hdr)

        self._text = _make_text_area()
        self._text.setPlaceholderText("Run analysis to build the automation map.")
        layout.addWidget(self._text)
        copy_btn.clicked.connect(lambda: _copy_text(self._text))

    def populate(self, model):
        if model is None:
            self._text.setPlainText(
                "Automation map could not be built for this package.\n"
                "(The course parsed for static checks, but the automation model "
                "failed — likely an unrecognised slide structure.)"
            )
            return
        self._text.setPlainText(format_report(model, max_slides=0))

    def clear(self):
        self._text.clear()


# ---------------------------------------------------------------------------
# Main SCORM QA Tab
# ---------------------------------------------------------------------------

class ScormQaTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._zip_path = ""
        self._story_path = ""
        self._scorm_data = None

        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # --- File inputs ---
        file_group = QGroupBox("Files")
        fg_layout = QVBoxLayout(file_group)
        fg_layout.setSpacing(4)

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
        fg_layout.addLayout(zip_row)

        story_row = QHBoxLayout()
        story_row.addWidget(QLabel(".story file:"))
        self._story_label = QLabel("(none — optional)")
        self._story_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._story_label.setStyleSheet("color: #555;")
        story_row.addWidget(self._story_label)
        story_btn = QPushButton("Browse…")
        story_btn.setFixedWidth(80)
        story_btn.clicked.connect(self._browse_story)
        story_row.addWidget(story_btn)
        fg_layout.addLayout(story_row)

        root.addWidget(file_group)

        # --- Action buttons ---
        action_row = QHBoxLayout()
        self._run_btn = QPushButton("Run Analysis")
        self._run_btn.setFixedHeight(30)
        self._run_btn.clicked.connect(self._run_analysis)
        action_row.addWidget(self._run_btn)

        clear_btn = QPushButton("Clear Results")
        clear_btn.setFixedHeight(30)
        clear_btn.setFixedWidth(100)
        clear_btn.clicked.connect(self._clear_files)
        action_row.addWidget(clear_btn)

        action_row.addSpacing(16)
        self._dual_path_chk = QCheckBox("Dual-path course")
        self._dual_path_chk.setToolTip(
            "Check if this course has a parallel accessible path.\n"
            "• Paragraphs and titles appearing exactly twice will not be flagged as duplicates.\n"
            "• JAWS / screen-reader static checks will run on the Accessible Path scene."
        )
        action_row.addWidget(self._dual_path_chk)

        action_row.addSpacing(12)
        self._non_english_chk = QCheckBox("Non-English course")
        self._non_english_chk.setToolTip(
            "Check if this course's content language is NOT English.\n"
            "• Spell check, English terminology pairs, and em-dash / hyphen checks will be skipped (they misfire on non-English text).\n"
            "• A new \"Untranslated English Text\" check flags slides that still contain English (pick the course Language at right).\n"
            "• Edit _ENGLISH_OK_TERMS in app/automation/scorm/checks/wordlists.py to allow\n"
            "  brand names, acronyms, and loanwords that should stay in English."
        )
        action_row.addWidget(self._non_english_chk)

        # Target-language picker for the Untranslated English Text check.
        # Only languages with a dictionary are offered; anything else skips
        # that check. Auto-selected from the filename when a package is chosen.
        self._lang_label = QLabel("Language:")
        self._lang_combo = QComboBox()
        self._lang_combo.addItem("(select…)", None)
        for _code, _name in sorted(_TARGET_LANGUAGES.items(), key=lambda kv: kv[1]):
            self._lang_combo.addItem(f"{_name} ({_code})", _code)
        self._lang_combo.setToolTip(
            "Target language for the Untranslated English Text check.\n"
            "• Only languages with a spell dictionary are listed — a course in "
            "any other language skips that check.\n"
            "• Auto-selected from the SCORM filename (…_de-DE_…) when you pick "
            "a package; you can override it here."
        )
        self._lang_label.setEnabled(False)
        self._lang_combo.setEnabled(False)
        self._non_english_chk.toggled.connect(self._lang_label.setEnabled)
        self._non_english_chk.toggled.connect(self._lang_combo.setEnabled)
        action_row.addSpacing(6)
        action_row.addWidget(self._lang_label)
        action_row.addWidget(self._lang_combo)

        action_row.addStretch()
        root.addLayout(action_row)

        # --- Progress bar (hidden until running) ---
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)   # indeterminate
        self._progress.setFixedHeight(6)
        self._progress.hide()
        root.addWidget(self._progress)

        # --- Status label ---
        self._status = QLabel("")
        self._status.setStyleSheet("color: #555; font-style: italic;")
        root.addWidget(self._status)

        # --- Inner tab widget ---
        self._inner_tabs = QTabWidget()
        self._inner_tabs.setDocumentMode(True)

        self._static_widget = _StaticChecksWidget()
        self._questions_widget = _QuestionsWidget()
        self._checklist_widget = _ChecklistWidget()
        self._table_widget = _ScreenTableWidget()
        self._automation_widget = _AutomationMapWidget()
        self._search_widget = _SearchWidget()

        self._inner_tabs.addTab(self._static_widget, "Static Checks")
        self._inner_tabs.addTab(self._questions_widget, "Questions")
        self._inner_tabs.addTab(self._checklist_widget, "Manual Checklist")
        self._inner_tabs.addTab(self._table_widget, "Screen Table")
        self._inner_tabs.addTab(self._automation_widget, "Automation Map")
        self._inner_tabs.addTab(self._search_widget, "Global Search")

        root.addWidget(self._inner_tabs)

    # --- File browsing ---

    def _browse_zip(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select SCORM zip", "", "SCORM Package (*.zip)"
        )
        if path:
            self._zip_path = path
            self._zip_label.setText(os.path.basename(path))
            self._zip_label.setToolTip(path)
            self._zip_label.setStyleSheet("color: #000;")
            # Auto-configure language from the GLS filename convention.
            base = detect_language_from_filename(os.path.basename(path))
            if base == "en":
                self._non_english_chk.setChecked(False)
            elif base:
                self._non_english_chk.setChecked(True)  # enables the combo
                idx = self._lang_combo.findData(base)
                # Supported -> select it; non-English but unsupported -> leave
                # on the placeholder so the check skips with a clear notice.
                self._lang_combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _browse_story(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Storyline file", "", "Articulate Storyline (*.story)"
        )
        if path:
            self._story_path = path
            self._story_label.setText(os.path.basename(path))
            self._story_label.setToolTip(path)
            self._story_label.setStyleSheet("color: #000;")

    def _clear_files(self):
        self._zip_path = ""
        self._story_path = ""
        self._scorm_data = None
        self._zip_label.setText("(none)")
        self._zip_label.setStyleSheet("color: #555;")
        self._story_label.setText("(none — optional)")
        self._story_label.setStyleSheet("color: #555;")
        self._status.setText("Results cleared.")
        self._static_widget._text.clear()
        self._questions_widget._text.clear()
        self._checklist_widget.clear()
        self._table_widget.clear()
        self._automation_widget.clear()
        self._search_widget._results.clear()
        self._search_widget.set_data(None)

    # --- Analysis ---

    def _run_analysis(self):
        if not self._zip_path:
            QMessageBox.warning(self, "No File", "Please select a SCORM zip file first.")
            return

        self._run_btn.setEnabled(False)
        self._progress.show()
        self._status.setText("Parsing files…")

        signals = _WorkerSignals()
        signals.finished.connect(self._on_finished)
        signals.error.connect(self._on_error)

        target_lang = (
            self._lang_combo.currentData()
            if self._non_english_chk.isChecked() else None
        )
        worker = _AnalysisWorker(
            self._zip_path, self._story_path, signals,
            dual_path=self._dual_path_chk.isChecked(),
            non_english=self._non_english_chk.isChecked(),
            target_lang=target_lang,
        )
        worker.start()

    def _on_finished(self, report, scorm_data, automodel):
        self._scorm_data = scorm_data
        self._search_widget.set_data(scorm_data)

        self._static_widget.populate(report.sections)
        self._questions_widget.populate(report.sections)
        self._checklist_widget.populate(report.manual_checklist)
        self._table_widget.populate(report.screen_table_rows)
        self._automation_widget.populate(automodel)

        fail_count = sum(
            1
            for sec in report.sections
            for item in sec.items
            if item.level == "fail"
        )
        warn_count = sum(
            1
            for sec in report.sections
            for item in sec.items
            if item.level == "warn"
        )

        parts = []
        if report.parse_errors:
            parts.append(f"{len(report.parse_errors)} parse error(s)")
        parts.append(f"{fail_count} fail(s)")
        parts.append(f"{warn_count} warning(s)")
        title = scorm_data.course_title or "Unknown course"
        self._status.setText(f"{title}  —  {', '.join(parts)}")

        self._progress.hide()
        self._run_btn.setEnabled(True)
        self._inner_tabs.setCurrentIndex(0)

    def _on_error(self, msg: str):
        self._progress.hide()
        self._run_btn.setEnabled(True)
        self._status.setText(f"Error: {msg}")
        QMessageBox.critical(self, "Analysis Error", msg)
