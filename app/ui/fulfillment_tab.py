"""
Fulfillment tab — order check and renewal course list comparison.

Workflow:
    1. Add the SOW and QuickBooks estimate PDFs and read them.
    2. Check the courses pulled from the PDF (layouts vary, so this list is
       editable: untick, fix a number, or add a row).
    3. Paste the Order Info email and the portal's Content Licensing list.
    4. Check order → two result sub-tabs:
       Order    portal exp date, plus warnings wherever the email, SOW,
                estimate and portal disagree (dates, license counts, SKUs, ...)
       Courses  three comma-separated number lists (add / archive & remove /
                archive & reset), each sorted by full course name, plus a
                manual-review section for anything that couldn't be placed.

Logic lives in app/automation/fulfillment/ (course_compare.py, order_check.py).
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFileDialog, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget,
)

from app.automation.fulfillment import course_compare as cc
from app.automation.fulfillment import order_check as oc
from .fulfillment_order_view import OrderView

_NUMBER_OK = re.compile(r"^\d{4,5}[A-Z]{0,4}$")

_COL_USE, _COL_NUM, _COL_NAME, _COL_NOTE = range(4)

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


class FulfillmentTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pdf_review: list[cc.ReviewItem] = []
        self._pdf_results: list[cc.ParseResult] = []
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

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self._build_inputs())
        split.addWidget(self._build_results())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        root.addWidget(split, stretch=1)

    def _button(self, text, slot, style=_BTN, height=28) -> QPushButton:
        b = QPushButton(text)
        b.setFixedHeight(height)
        b.setStyleSheet(style)
        b.clicked.connect(slot)
        return b

    def _build_inputs(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)

        # 1. PDFs
        pdf_group = QGroupBox("1. SOW and QuickBooks estimate PDFs")
        pg = QVBoxLayout(pdf_group)
        self._pdf_list = QListWidget()
        self._pdf_list.setMaximumHeight(70)
        self._pdf_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._pdf_list.setToolTip("Drag PDFs onto this tab, or use Add PDF…")
        pg.addWidget(self._pdf_list)
        row = QHBoxLayout()
        row.addWidget(self._button("Add PDF…", self._on_add_pdfs))
        row.addWidget(self._button("Remove", self._on_remove_pdfs))
        row.addStretch()
        self._read_btn = self._button("Read PDFs", self._on_read_pdfs, _BTN_PRIMARY)
        row.addWidget(self._read_btn)
        pg.addLayout(row)
        layout.addWidget(pdf_group)

        vsplit = QSplitter(Qt.Orientation.Vertical)

        # 2. Courses read from the PDF
        table_group = QGroupBox("2. Courses on the PDF (check before comparing)")
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

        # 3. Pasted text: Order Info email + portal list
        paste_group = QGroupBox("3. Pasted text")
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
        row = QHBoxLayout()
        row.addWidget(self._button("Clear all", self._on_clear_all))
        row.addStretch()
        self._check_btn = self._button("▶  Check order", self._on_check, _BTN_PRIMARY, 32)
        row.addWidget(self._check_btn)
        ptg.addLayout(row)
        vsplit.addWidget(paste_group)

        layout.addWidget(vsplit, stretch=1)
        return w

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

    def _on_read_pdfs(self):
        paths = [self._pdf_list.item(i).data(Qt.ItemDataRole.UserRole)
                 for i in range(self._pdf_list.count())]
        if not paths:
            QMessageBox.information(self, "No PDFs", "Add at least one proposal PDF first.")
            return

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
        self._pdf_review = merged.review
        self._refresh_order()
        self._fill_table(merged.courses)
        self._clear_outputs()

        lines = []
        if errors:
            lines.append("<b>Could not read:</b><br>" + "<br>".join(html.escape(e) for e in errors))
        if not merged.courses:
            lines.append("<b>No courses were found in the PDF(s).</b> "
                         "The layout may be new. Add rows by hand, or send an example "
                         "so the reader can learn the layout.")
        if merged.review:
            lines.append(self._review_html(merged.review, "PDF lines to check"))
        self._details.setHtml("<br><br>".join(lines))

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

    def _refresh_order(self, portal: cc.ParseResult | None = None):
        self._order_view.show_check(
            oc.check_order(self._email.toPlainText(), self._pdf_results, portal))

    def _on_check(self):
        portal_text = self._portal.toPlainText()
        portal = cc.parse_portal_list(portal_text) if portal_text.strip() else None
        self._refresh_order(portal)

        # Course lists need both sides; the Order sub-tab says what's missing.
        pdf_courses, problems = self._table_courses()
        if problems:
            QMessageBox.warning(self, "Check the PDF course list",
                                "Fix or untick these rows first:\n\n" + "\n".join(problems))
            return
        if not pdf_courses or portal is None:
            self._clear_outputs()
            return

        result = cc.compare(pdf_courses, portal.courses, portal.review + self._pdf_review)

        for key, courses in (("add", result.to_add), ("remove", result.archive_remove),
                             ("reset", result.archive_reset)):
            label, field = self._outputs[key]
            label.setText(f"<b>{label.property('title')}</b> ({len(courses)})")
            field.setText(cc.format_numbers(courses))
            field.setCursorPosition(0)

        self._details.setHtml(self._details_html(result, len(portal.courses)))

    def _details_html(self, r: cc.Comparison, portal_count: int) -> str:
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
                + (f" <i>({html.escape(c.note)})</i>" if c.note else "") + "</li>"
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
        self._pdf_review = []
        self._fill_table([])
        self._email.clear()
        self._portal.clear()
        self._clear_outputs()
        self._order_view.clear()
        self._details.clear()
