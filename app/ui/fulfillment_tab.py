"""
Fulfillment tab — order check and renewal course list comparison, as a
step-by-step wizard (Back / Next, or click a step you've already reached):

    1. PDFs      Add the SOW and QuickBooks estimate PDFs. Next reads them.
    2. Courses   Check the courses pulled from the PDF (layouts vary, so this
                 list is editable: untick, fix a number, or add a row). The
                 PDF's upgrade / Course Archive text is shown above it.
    3. Paste     The Order Info email and the portal's Content Licensing list.
    4. Removals  Portal courses that aren't on the PDF, with the PDF's text
                 about what's being removed above them. Ticked = archive and
                 remove, unticked = archive and reset. A PDF that only lists
                 what's changing (an upgrade) starts with nothing ticked.
    5. Results   Order   portal exp date, plus warnings wherever the email,
                         SOW, estimate and portal disagree
                 Courses three comma-separated number lists (add / archive &
                         remove / archive & reset), each sorted by full course
                         name, plus a manual-review section.

Logic lives in app/automation/fulfillment/ (course_compare.py, order_check.py,
removals.py).
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFileDialog, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QStackedWidget, QTabWidget, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from app.automation.fulfillment import course_compare as cc
from app.automation.fulfillment import order_check as oc
from app.automation.fulfillment import removals as rm
from .fulfillment_order_view import OrderView

_NUMBER_OK = re.compile(r"^\d{4,5}[A-Z]{0,4}$")

_COL_USE, _COL_NUM, _COL_NAME, _COL_NOTE = range(4)
# Removals table: tick = archive and remove; the last column says why a row is suggested.
_RCOL_REMOVE, _RCOL_NUM, _RCOL_NAME, _RCOL_WHY = range(4)

_STEPS = ("PDFs", "Courses on PDF", "Paste", "Removals", "Results")
_PDFS, _COURSES, _PASTE, _REMOVALS, _RESULTS = range(5)

# Highlighted (suggested) rows: dark text set explicitly so they stay readable
# when Windows is in dark mode (its default text colour there is white).
_SUGGESTED_BG = QColor("#ffe08a")
_SUGGESTED_FG = QColor("#212529")
# Selected rows: the same in light and dark mode.
_RTABLE_STYLE = (
    "QTableWidget::item:selected { background: #0b5ed7; color: white; }"
)

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
_STEP_BTN = (
    "QPushButton { border: none; padding: 4px 8px; color: #adb5bd; background: transparent; }"
    "QPushButton:enabled { color: #495057; }"
    "QPushButton:checked { color: #198754; font-weight: bold; border-bottom: 2px solid #198754; }"
)


class FulfillmentTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pdf_review: list[cc.ReviewItem] = []
        self._pdf_results: list[cc.ParseResult] = []
        self._pdf_errors: list[str] = []
        self._notes: list[rm.ChangeNote] = []
        self._portal_result: cc.ParseResult | None = None
        self._plan: rm.RemovalPlan | None = None
        self._plan_key = None   # inputs the removals table was built from
        self._reached = _PDFS   # furthest step reached; earlier steps are clickable
        self._last_dir = ""
        self.setAcceptDrops(True)
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)

        intro = QLabel(
            "Order check and renewal course list comparison. Courses are compared "
            "by course number; each list is sorted by course name."
        )
        intro.setStyleSheet("color: #6c757d;")
        root.addWidget(intro)

        steps = QHBoxLayout()
        steps.setSpacing(2)
        self._step_btns: list[QPushButton] = []
        for i, name in enumerate(_STEPS):
            if i:
                sep = QLabel("›")
                sep.setStyleSheet("color: #adb5bd;")
                steps.addWidget(sep)
            b = QPushButton(f"{i + 1}. {name}")
            b.setCheckable(True)
            b.setStyleSheet(_STEP_BTN)
            b.clicked.connect(lambda _=False, i=i: self._go_to(i))
            steps.addWidget(b)
            self._step_btns.append(b)
        steps.addStretch()
        root.addLayout(steps)

        self._stack = QStackedWidget()
        for page in (self._build_pdf_page(), self._build_courses_page(), self._build_paste_page(),
                     self._build_removals_page(), self._build_results()):
            self._stack.addWidget(page)
        root.addWidget(self._stack, stretch=1)

        nav = QHBoxLayout()
        self._back_btn = self._button("‹  Back", self._on_back)
        nav.addWidget(self._back_btn)
        nav.addWidget(self._button("Clear all", self._on_clear_all))
        nav.addStretch()
        self._next_btn = self._button("Next  ›", self._on_next, _BTN_PRIMARY, 32)
        self._next_btn.setMinimumWidth(150)
        nav.addWidget(self._next_btn)
        root.addLayout(nav)
        self._show_step(_PDFS)

    def _button(self, text, slot, style=_BTN, height=28) -> QPushButton:
        b = QPushButton(text)
        b.setFixedHeight(height)
        b.setStyleSheet(style)
        b.clicked.connect(slot)
        return b

    def _build_pdf_page(self) -> QWidget:
        pdf_group = QGroupBox("SOW and QuickBooks estimate PDFs")
        pg = QVBoxLayout(pdf_group)
        hint = QLabel("Drag PDFs onto this tab, or use Add PDF…. Next reads them.")
        hint.setStyleSheet("color: #6c757d;")
        pg.addWidget(hint)
        self._pdf_list = QListWidget()
        self._pdf_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._pdf_list.setToolTip("Drag PDFs onto this tab, or use Add PDF…")
        pg.addWidget(self._pdf_list, stretch=1)
        row = QHBoxLayout()
        row.addWidget(self._button("Add PDF…", self._on_add_pdfs))
        row.addWidget(self._button("Remove", self._on_remove_pdfs))
        row.addStretch()
        pg.addLayout(row)
        return pdf_group

    def _build_courses_page(self) -> QWidget:
        vsplit = QSplitter(Qt.Orientation.Vertical)

        notes_group = QGroupBox("From the PDF: upgrades, course archive, lines to check")
        ng = QVBoxLayout(notes_group)
        self._pdf_notes = QTextEdit()
        self._pdf_notes.setReadOnly(True)
        ng.addWidget(self._pdf_notes)
        vsplit.addWidget(notes_group)

        table_group = QGroupBox("Courses on the PDF (check before comparing)")
        tg = QVBoxLayout(table_group)
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Use", "Number", "Course name", "Note"])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(_COL_USE, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_NUM, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(_COL_NOTE, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.itemChanged.connect(lambda _item: self._update_table_count())
        tg.addWidget(self._table)
        row = QHBoxLayout()
        self._table_count = QLabel("")
        self._table_count.setStyleSheet("color: #6c757d;")
        row.addWidget(self._table_count)
        row.addStretch()
        row.addWidget(self._button("Add row", self._on_add_row))
        row.addWidget(self._button("Remove selected", self._on_remove_rows))
        tg.addLayout(row)
        vsplit.addWidget(table_group)
        vsplit.setStretchFactor(0, 1)
        vsplit.setStretchFactor(1, 3)
        return vsplit

    def _build_paste_page(self) -> QWidget:
        paste_group = QGroupBox("Pasted text")
        ptg = QVBoxLayout(paste_group)
        self._email = QPlainTextEdit()
        self._email.setPlaceholderText(
            "Paste the Order Info email (URL:, Opp Name:, License Dates:, ... in any order)."
        )
        self._portal = QPlainTextEdit()
        self._portal.setPlaceholderText(
            "Paste the whole Content Licensing table here (Edit Company → Content Licensing).\n"
            "Course names are compared; the license count is checked against the SOW."
        )
        paste_tabs = QTabWidget()
        for box, title in ((self._email, "Order info email"), (self._portal, "Portal content list")):
            box.setStyleSheet("font-family: Consolas, monospace;")
            paste_tabs.addTab(box, title)
        ptg.addWidget(paste_tabs)
        return paste_group

    def _build_removals_page(self) -> QWidget:
        vsplit = QSplitter(Qt.Orientation.Vertical)

        top = QGroupBox("What the PDF says is being removed or replaced")
        topl = QVBoxLayout(top)
        self._removal_mode = QLabel("")
        self._removal_mode.setWordWrap(True)
        topl.addWidget(self._removal_mode)
        self._removal_text = QTextEdit()
        self._removal_text.setReadOnly(True)
        topl.addWidget(self._removal_text)
        vsplit.addWidget(top)

        table_group = QGroupBox("On the portal but not on the PDF. Ticked = archive and remove; "
                                "unticked = archive and reset")
        tg = QVBoxLayout(table_group)
        self._rtable = QTableWidget(0, 4)
        self._rtable.setStyleSheet(_RTABLE_STYLE)
        self._rtable.setHorizontalHeaderLabels(["Remove", "Number", "Course name", "Matches PDF text"])
        hdr = self._rtable.horizontalHeader()
        hdr.setSectionResizeMode(_RCOL_REMOVE, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_RCOL_NUM, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_RCOL_NAME, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(_RCOL_WHY, QHeaderView.ResizeMode.ResizeToContents)
        self._rtable.verticalHeader().setVisible(False)
        self._rtable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._rtable.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._rtable.itemChanged.connect(lambda _item: self._update_removal_count())
        tg.addWidget(self._rtable)
        row = QHBoxLayout()
        self._removal_count = QLabel("")
        self._removal_count.setStyleSheet("color: #6c757d;")
        row.addWidget(self._removal_count)
        row.addStretch()
        row.addWidget(self._button("Select all", lambda: self._tick_removals(lambda n: True)))
        row.addWidget(self._button("Select none", lambda: self._tick_removals(lambda n: False)))
        self._tick_suggested_btn = self._button(
            "Tick highlighted",
            lambda: self._tick_removals(lambda n: n in self._plan.suggested, keep_others=True))
        self._tick_suggested_btn.setToolTip("Tick the rows whose names match the PDF's text")
        row.addWidget(self._tick_suggested_btn)
        tg.addLayout(row)
        vsplit.addWidget(table_group)
        vsplit.setStretchFactor(0, 1)
        vsplit.setStretchFactor(1, 3)
        return vsplit

    def _build_results(self) -> QWidget:
        self._order_view = OrderView(self._button)
        courses = QWidget()
        layout = QVBoxLayout(courses)
        layout.setContentsMargins(0, 0, 0, 0)

        self._outputs: dict[str, tuple[QLabel, QLineEdit]] = {}
        for key, title in (
            ("add", "Add to portal"),
            ("remove", "Archive and remove"),
            ("reset", "Archive and reset"),
        ):
            group = QGroupBox()
            g = QVBoxLayout(group)
            label = QLabel(f"<b>{title}</b>")
            g.addWidget(label)
            row = QHBoxLayout()
            field = QLineEdit()
            field.setReadOnly(True)
            field.setStyleSheet("font-family: Consolas, monospace;")
            row.addWidget(field, stretch=1)
            row.addWidget(self._button("Copy", lambda _=False, f=field: self._copy(f)))
            g.addLayout(row)
            layout.addWidget(group)
            self._outputs[key] = (label, field)
            label.setProperty("title", title)

        details_group = QGroupBox("Details")
        dg = QVBoxLayout(details_group)
        self._details = QTextEdit()
        self._details.setReadOnly(True)
        self._details.setPlaceholderText(
            "Names behind each list, anything that needs manual review, and "
            "possible renumbered courses will show here."
        )
        dg.addWidget(self._details)
        layout.addWidget(details_group, stretch=1)

        self._result_tabs = QTabWidget()
        self._result_tabs.addTab(self._order_view, "Order")
        self._result_tabs.addTab(courses, "Courses")
        return self._result_tabs

    # ------------------------------------------------------------------
    # PDFs
    # ------------------------------------------------------------------

    def _add_pdf_paths(self, paths):
        existing = {self._pdf_list.item(i).data(Qt.ItemDataRole.UserRole)
                    for i in range(self._pdf_list.count())}
        for p in paths:
            p = str(p)
            if p.lower().endswith(".pdf") and p not in existing:
                self._pdf_list.addItem(Path(p).name)
                item = self._pdf_list.item(self._pdf_list.count() - 1)
                item.setData(Qt.ItemDataRole.UserRole, p)
                item.setToolTip(p)
                existing.add(p)
                self._last_dir = str(Path(p).parent)

    def _on_add_pdfs(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select proposal / order PDF(s)", self._last_dir, "PDF files (*.pdf)"
        )
        self._add_pdf_paths(paths)

    def _on_remove_pdfs(self):
        for item in self._pdf_list.selectedItems():
            self._pdf_list.takeItem(self._pdf_list.row(item))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self._add_pdf_paths(u.toLocalFile() for u in event.mimeData().urls())

    def _on_read_pdfs(self) -> bool:
        paths = [self._pdf_list.item(i).data(Qt.ItemDataRole.UserRole)
                 for i in range(self._pdf_list.count())]
        if not paths:
            QMessageBox.information(self, "No PDFs", "Add at least one proposal PDF first.")
            return False

        results, errors = [], []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for p in paths:
                try:
                    results.append(cc.extract_pdf_courses(p))
                except Exception as e:  # show every failure, keep reading the rest
                    errors.append(f"{Path(p).name}: {e}")
        finally:
            QApplication.restoreOverrideCursor()

        merged = cc.merge_results(results)
        self._pdf_results = results
        self._pdf_errors = errors
        self._pdf_review = merged.review
        self._notes = rm.change_notes(results)
        self._fill_table(merged.courses)
        self._clear_outputs()
        self._details.clear()
        self._pdf_notes.setHtml(self._pdf_notes_html(not merged.courses))
        return True

    def _pdf_notes_html(self, no_courses: bool) -> str:
        lines = []
        if self._pdf_errors:
            lines.append("<b style='color:#b02a37'>Could not read:</b><br>"
                         + "<br>".join(html.escape(e) for e in self._pdf_errors))
        if no_courses:
            lines.append("<b>No courses were found in the PDF(s).</b> "
                         "The layout may be new. Add rows by hand, or send an example "
                         "so the reader can learn the layout.")
        if self._notes:
            lines.append(self._notes_html(self._notes))
        if self._pdf_review:
            lines.append(self._review_html(self._pdf_review, "PDF lines to check"))
        return "<br>".join(lines) or "<p style='color:#6c757d'>Nothing else to note in the PDF(s).</p>"

    @staticmethod
    def _notes_html(notes: list[rm.ChangeNote]) -> str:
        rows = "".join(
            f"<tr><td><b>{html.escape(n.label)}</b></td><td>{html.escape(n.text)}</td>"
            f"<td style='color:#6c757d'>{html.escape(n.source)}</td></tr>"
            for n in notes
        )
        return f"<table cellspacing='0' cellpadding='3'>{rows}</table>"

    # ------------------------------------------------------------------
    # Table
    # ------------------------------------------------------------------

    def _fill_table(self, courses):
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        for c in courses:
            self._append_row(c.number, c.name, c.note, c.source)
        self._table.blockSignals(False)
        self._update_table_count()

    def _append_row(self, number="", name="", note="", source="Added by hand"):
        r = self._table.rowCount()
        self._table.insertRow(r)
        use = QTableWidgetItem()
        use.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                     | Qt.ItemFlag.ItemIsSelectable)
        use.setCheckState(Qt.CheckState.Checked)
        self._table.setItem(r, _COL_USE, use)
        self._table.setItem(r, _COL_NUM, QTableWidgetItem(number))
        self._table.setItem(r, _COL_NAME, QTableWidgetItem(name))
        note_item = QTableWidgetItem(note)
        note_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        note_item.setToolTip(source)
        self._table.setItem(r, _COL_NOTE, note_item)
        return r

    def _on_add_row(self):
        r = self._append_row()
        self._table.scrollToBottom()
        self._table.editItem(self._table.item(r, _COL_NUM))
        self._update_table_count()

    def _on_remove_rows(self):
        rows = sorted({i.row() for i in self._table.selectedIndexes()}, reverse=True)
        for r in rows:
            self._table.removeRow(r)
        self._update_table_count()

    def _update_table_count(self):
        total = self._table.rowCount()
        used = sum(
            1 for r in range(total)
            if self._table.item(r, _COL_USE)
            and self._table.item(r, _COL_USE).checkState() == Qt.CheckState.Checked
        )
        self._table_count.setText(f"{used} of {total} courses will be compared" if total else "")

    def _table_courses(self) -> tuple[list[cc.Course], list[str]]:
        courses, problems = [], []
        seen = set()
        for r in range(self._table.rowCount()):
            use = self._table.item(r, _COL_USE)
            if not use or use.checkState() != Qt.CheckState.Checked:
                continue
            num = (self._table.item(r, _COL_NUM).text() if self._table.item(r, _COL_NUM) else "")
            num = num.strip().upper()
            name = (self._table.item(r, _COL_NAME).text() if self._table.item(r, _COL_NAME) else "")
            name = cc.clean_text(name) or num
            note_item = self._table.item(r, _COL_NOTE)
            if not num and not name:
                continue
            if not _NUMBER_OK.match(num):
                problems.append(f"Row {r + 1}: '{num or '(blank)'}' isn't a course number ({name})")
                continue
            if num in seen:
                continue
            seen.add(num)
            courses.append(cc.Course(
                number=num, name=name,
                note=note_item.text() if note_item else "",
                source=note_item.toolTip() if note_item else "PDF",
            ))
        return courses, problems

    # ------------------------------------------------------------------
    # Compare
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Wizard navigation
    # ------------------------------------------------------------------

    def _show_step(self, step: int):
        self._reached = max(self._reached, step)
        self._stack.setCurrentIndex(step)
        for i, b in enumerate(self._step_btns):
            b.setChecked(i == step)
            b.setEnabled(i <= self._reached)
        self._back_btn.setEnabled(step > _PDFS)
        self._next_btn.setVisible(step < _RESULTS)
        self._next_btn.setText({_PDFS: "Read PDFs  ›", _PASTE: "Next  ›",
                                _REMOVALS: "▶  Check order"}.get(step, "Next  ›"))

    def _go_to(self, step: int):
        """Step bar click: going back is free; going forward runs each step's Next."""
        while self._stack.currentIndex() < step:
            if not self._advance():
                return
        if step < self._stack.currentIndex():
            self._show_step(step)

    def _on_back(self):
        step = self._stack.currentIndex() - 1
        if step == _REMOVALS and self._portal_result is None:
            step = _PASTE   # removals were skipped (no portal list)
        self._show_step(max(step, _PDFS))

    def _on_next(self):
        self._advance()

    def _advance(self) -> bool:
        """Run the current step's Next action and move on. False = stay put."""
        step = self._stack.currentIndex()
        if step == _PDFS:
            if not self._pdf_results or self._pdf_paths_changed():
                if not self._on_read_pdfs():
                    return False
            self._show_step(_COURSES)
        elif step == _COURSES:
            _courses, problems = self._table_courses()
            if problems:
                QMessageBox.warning(self, "Check the PDF course list",
                                    "Fix or untick these rows first:\n\n" + "\n".join(problems))
                return False
            self._show_step(_PASTE)
        elif step == _PASTE:
            portal_text = self._portal.toPlainText()
            self._portal_result = cc.parse_portal_list(portal_text) if portal_text.strip() else None
            if self._portal_result is None:
                # Nothing to choose removals from; the Order tab can still be checked.
                self._on_check()
                self._show_step(_RESULTS)
                return True
            self._fill_removals()
            self._show_step(_REMOVALS)
        elif step == _REMOVALS:
            self._on_check()
            self._show_step(_RESULTS)
        else:
            return False
        return True

    def _pdf_paths_changed(self) -> bool:
        listed = {self._pdf_list.item(i).text() for i in range(self._pdf_list.count())}
        return listed != {r.source for r in self._pdf_results} | {
            e.split(":", 1)[0] for e in self._pdf_errors}

    # ------------------------------------------------------------------
    # Removals
    # ------------------------------------------------------------------

    def _fill_removals(self):
        pdf_courses, _problems = self._table_courses()
        portal = self._portal_result
        key = (tuple(sorted(c.number for c in pdf_courses)),
               tuple(c.number for c in portal.courses),
               tuple(self._notes))
        if key == self._plan_key:
            return   # same inputs: keep the ticks the user already set
        self._plan_key = key
        plan = self._plan = rm.plan_removals(pdf_courses, portal.courses, self._notes)

        if plan.partial:
            self._removal_mode.setText(
                "<b style='color:#b02a37'>This PDF looks like a change order, not a full course "
                "list</b>, so nothing is ticked yet: unticked courses are archived and reset. "
                "Tick the ones being removed (highlighted rows match the PDF's text).<br>"
                "<span style='color:#6c757d'>" + html.escape(" ".join(plan.reasons)) + "</span>")
        else:
            self._removal_mode.setText(
                "The PDF looks like a full course list, so every course that isn't on it is "
                "ticked for archive and remove. Untick any the client is keeping.")

        parts = [self._notes_html(self._notes)] if self._notes else [
            "<p style='color:#6c757d'>The PDF has no upgrade or Course Archive text.</p>"]
        if plan.warnings:
            parts.append("<b style='color:#b02a37'>Check:</b><ul>" + "".join(
                f"<li>{html.escape(w)}</li>" for w in plan.warnings) + "</ul>")
        self._removal_text.setHtml("".join(parts))

        self._rtable.blockSignals(True)
        self._rtable.setRowCount(0)
        # Highlighted (suggested) rows first so they're seen without scrolling.
        for c in sorted(plan.candidates, key=lambda c: c.number not in plan.suggested):
            r = self._rtable.rowCount()
            self._rtable.insertRow(r)
            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                          | Qt.ItemFlag.ItemIsSelectable)
            tick.setCheckState(Qt.CheckState.Checked if c.number in plan.ticked
                               else Qt.CheckState.Unchecked)
            tick.setData(Qt.ItemDataRole.UserRole, c.number)
            why = plan.suggested.get(c.number, "")
            cells = [tick, QTableWidgetItem(c.number), QTableWidgetItem(c.name), QTableWidgetItem(why)]
            for col, item in enumerate(cells):
                if why:
                    item.setBackground(QBrush(_SUGGESTED_BG))
                    if col != _RCOL_REMOVE:   # the tick mark is drawn in this colour too
                        item.setForeground(QBrush(_SUGGESTED_FG))
                self._rtable.setItem(r, col, item)
        self._rtable.blockSignals(False)
        self._tick_suggested_btn.setEnabled(bool(plan.suggested))
        self._update_removal_count()

    def _tick_removals(self, want, keep_others: bool = False):
        self._rtable.blockSignals(True)
        for r in range(self._rtable.rowCount()):
            item = self._rtable.item(r, _RCOL_REMOVE)
            if want(item.data(Qt.ItemDataRole.UserRole)):
                item.setCheckState(Qt.CheckState.Checked)
            elif not keep_others:
                item.setCheckState(Qt.CheckState.Unchecked)
        self._rtable.blockSignals(False)
        self._update_removal_count()

    def _removal_numbers(self) -> tuple[set[str], set[str]]:
        """(ticked = archive and remove, unticked = keep as archive and reset)."""
        ticked, unticked = set(), set()
        for r in range(self._rtable.rowCount()):
            item = self._rtable.item(r, _RCOL_REMOVE)
            (ticked if item.checkState() == Qt.CheckState.Checked else unticked).add(
                item.data(Qt.ItemDataRole.UserRole))
        return ticked, unticked

    def _update_removal_count(self):
        ticked, unticked = self._removal_numbers()
        total = len(ticked) + len(unticked)
        self._removal_count.setText(
            f"{len(ticked)} of {total} will be archived and removed; "
            f"{len(unticked)} archived and reset" if total else
            "Every portal course is on the PDF: nothing to remove.")

    # ------------------------------------------------------------------
    # Compare
    # ------------------------------------------------------------------

    def _refresh_order(self, portal: cc.ParseResult | None = None):
        self._order_view.show_check(
            oc.check_order(self._email.toPlainText(), self._pdf_results, portal))

    def _on_check(self):
        portal = self._portal_result
        self._refresh_order(portal)

        # Course lists need both sides; the Order sub-tab says what's missing.
        pdf_courses, problems = self._table_courses()
        if problems:
            QMessageBox.warning(self, "Check the PDF course list",
                                "Fix or untick these rows first:\n\n" + "\n".join(problems))
            return
        if not pdf_courses or portal is None:
            self._clear_outputs()
            self._details.setHtml(
                "<p style='color:#6c757d'>The course lists need both the PDF courses and the "
                "portal Content Licensing list.</p>")
            return

        _ticked, keep = self._removal_numbers()
        result = cc.compare(pdf_courses, portal.courses, portal.review + self._pdf_review,
                            keep=keep)

        for key, courses in (("add", result.to_add), ("remove", result.archive_remove),
                             ("reset", result.archive_reset)):
            label, field = self._outputs[key]
            label.setText(f"<b>{label.property('title')}</b> ({len(courses)})")
            field.setText(cc.format_numbers(courses))
            field.setCursorPosition(0)

        self._details.setHtml(self._details_html(result, len(portal.courses), keep))

    def _details_html(self, r: cc.Comparison, portal_count: int, kept: set[str] = frozenset()) -> str:
        parts = []
        if r.review:
            parts.append(self._review_html(r.review, "Needs manual review (not in any list)"))
        if r.possible_replacements:
            items = "".join(
                f"<li>Adding <b>{html.escape(a.number)}</b> {html.escape(a.name)}<br>"
                f"removing <b>{html.escape(b.number)}</b> {html.escape(b.name)}</li>"
                for a, b in r.possible_replacements
            )
            parts.append(
                "<h4>Possible renumbered / updated courses</h4>"
                "<p style='color:#6c757d'>Same name, different number. These are still in "
                f"the add and remove lists. Shown for information only.</p><ul>{items}</ul>"
            )
        for title, courses in (("Add to portal", r.to_add),
                               ("Archive and remove", r.archive_remove),
                               ("Archive and reset", r.archive_reset)):
            items = "".join(
                f"<li><b>{html.escape(c.number)}</b> {html.escape(c.name)}"
                + (f" <i>({html.escape(c.note)})</i>" if c.note else "")
                + (" <i>(not on the PDF; kept in step 4)</i>"
                   if title == "Archive and reset" and c.number in kept else "") + "</li>"
                for c in courses
            ) or "<li><i>none</i></li>"
            parts.append(f"<h4>{title} ({len(courses)})</h4><ul>{items}</ul>")
        parts.append(f"<p style='color:#6c757d'>Portal courses read: {portal_count}</p>")
        return "".join(parts)

    @staticmethod
    def _review_html(items: list[cc.ReviewItem], title: str) -> str:
        lis = "".join(
            f"<li>{html.escape(i.text)}<br><span style='color:#6c757d'>"
            f"{html.escape(i.reason)} [{html.escape(i.source)}]</span></li>"
            for i in items
        )
        return f"<h4 style='color:#b02a37'>{title} ({len(items)})</h4><ul>{lis}</ul>"

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _copy(self, field: QLineEdit):
        QApplication.clipboard().setText(field.text())

    def _clear_outputs(self):
        for label, field in self._outputs.values():
            label.setText(f"<b>{label.property('title')}</b>")
            field.clear()

    def _on_clear_all(self):
        self._pdf_list.clear()
        self._pdf_results = []
        self._pdf_errors = []
        self._pdf_review = []
        self._notes = []
        self._portal_result = None
        self._plan = None
        self._plan_key = None
        self._pdf_notes.clear()
        self._rtable.setRowCount(0)
        self._removal_text.clear()
        self._removal_mode.clear()
        self._removal_count.clear()
        self._fill_table([])
        self._email.clear()
        self._portal.clear()
        self._clear_outputs()
        self._order_view.clear()
        self._details.clear()
        self._reached = _PDFS
        self._show_step(_PDFS)
