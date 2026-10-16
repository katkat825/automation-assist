"""
MS Attack Sim sub-tab (Data Analysis) — Defender Attack Simulation Training
phish results report, in two phases.

Phase 1  pick the downloaded results .csv, set month / year / Ev|Sp
         -> "<prefix> Phish Results.xlsx" sorted fail-first onto
            Pass - Report / Pass - Delete / Pass - Ignore / forwards / Fail /
            Delivery Failed, plus "<prefix> forwards.csv" to send to the
            reviewer. Optionally renames the download to "<prefix> raw results.csv".

Phase 2  once the forward review comes back: load the forwarded users, fill in
         each result (import the reviewer's csv/xlsx, paste the email body, or
         pick by hand), pick the previous simulation's final workbook, Finish.
         -> forwards moved to Pass - Report / Fail, Fail split into Fail x N,
            and the summary block for the results email.

Logic lives in app/automation/data_analysis/attack_sim.py.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from app.automation.data_analysis import attack_sim as sim
from app.config import settings as cfg

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

_RESULT_CHOICES = [("—", None), ("Yes — approved mailbox", True), ("No", False)]
_LISTED_CHOICES = [("leave for me to pick", None),
                   ("forwarded to the approved mailbox", True),
                   ("did NOT forward to the approved mailbox", False)]
_COL_NAME, _COL_MAIL, _COL_FWD, _COL_REP, _COL_RESULT = range(5)

# settings.json: {"data_analysis": {"attack_sim_columns": {column: "fail" | "ignore"}}}
_SETTINGS_SECTION = "data_analysis"
_SETTINGS_COLUMNS = "attack_sim_columns"


def _btn(text, slot, primary=False, enabled=True):
    b = QPushButton(text)
    b.setFixedHeight(30)
    b.setStyleSheet(_BTN_PRIMARY if primary else _BTN)
    b.setEnabled(enabled)
    b.clicked.connect(slot)
    return b


def _file_row(label, placeholder, browse_slot):
    row = QHBoxLayout()
    lbl = QLabel(label)
    lbl.setMinimumWidth(150)
    row.addWidget(lbl)
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    row.addWidget(edit, 1)
    b = QPushButton("Browse…")
    b.setFixedWidth(80)
    b.clicked.connect(browse_slot)
    row.addWidget(b)
    return row, edit


def _open(path):
    if path and os.path.exists(path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def _clean(text: str) -> str:
    return text.strip().strip('"')


def _fmt(v) -> str:
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%m/%d/%Y %I:%M %p")
    return str(v)


class AttackSimView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._p1_xlsx = ""
        self._p2_out = ""
        self._forward_users: list[dict] = []
        self._loaded = False        # forwards sheet loaded into the table
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        tabs = QTabWidget()
        tabs.addTab(self._build_phase1(), "Phase 1 — sort raw results")
        tabs.addTab(self._build_phase2(), "Phase 2 — forwards && finish")
        self._tabs = tabs
        root.addWidget(tabs)

    # ================================================================ Phase 1
    def _build_phase1(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)

        intro = QLabel(
            "Sorts the Defender results export fail-first: Delivery Failed → Fail "
            "(compromised / clicked or opened / replied) → forwards → Pass - Report → "
            "Pass - Delete → Pass - Ignore. A user who clicked and also reported is a Fail. "
            "Saves the workbook and a forwards .csv (UserName, UserMail, "
            "MessageForwarded_TimeStamp, Phishing Reported On) to send to the reviewer."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #555;")
        lay.addWidget(intro)

        grp = QGroupBox("Raw results")
        g = QVBoxLayout(grp)
        row, self._csv_edit = _file_row("Results export (.csv):", "Select the downloaded .csv…",
                                        self._browse_csv)
        self._csv_edit.textChanged.connect(self._on_csv_changed)
        g.addLayout(row)

        row = QHBoxLayout()
        lbl = QLabel("Report name:")
        lbl.setMinimumWidth(150)
        row.addWidget(lbl)
        self._month = QComboBox()
        self._month.setEditable(True)
        self._month.addItems(sim.MONTHS)
        self._year = QSpinBox()
        self._year.setRange(2020, 2100)
        self._type = QComboBox()
        for t, tip in (("Ev", "everyone / org-wide"), ("Sp", "specialized / targeted")):
            self._type.addItem(f"{t} — {tip}", t)
        today = date.today()
        self._month.setCurrentIndex(today.month - 1)
        self._year.setValue(today.year)
        for wdg in (self._month, self._year, self._type):
            row.addWidget(wdg)
        row.addStretch()
        g.addLayout(row)
        self._month.currentTextChanged.connect(self._update_names)
        self._year.valueChanged.connect(self._update_names)
        self._type.currentIndexChanged.connect(self._update_names)

        self._rename_chk = QCheckBox()
        self._rename_chk.setChecked(True)
        g.addWidget(self._rename_chk)
        self._names_lbl = QLabel()
        self._names_lbl.setStyleSheet("color: #555;")
        self._names_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        g.addWidget(self._names_lbl)
        lay.addWidget(grp)

        row = QHBoxLayout()
        self._p1_btn = _btn("Sort into workbook", self._run_phase1, primary=True)
        row.addWidget(self._p1_btn)
        self._p1_open = _btn("Open workbook", lambda: _open(self._p1_xlsx), enabled=False)
        row.addWidget(self._p1_open)
        self._p1_folder = _btn("Open folder", lambda: _open(os.path.dirname(self._p1_xlsx)),
                               enabled=False)
        row.addWidget(self._p1_folder)
        row.addStretch()
        forget = _btn("Forget column answers", self._forget_columns)
        forget.setToolTip("Clear the saved answers about unrecognised report columns, "
                          "so you're asked again next time.")
        row.addWidget(forget)
        lay.addLayout(row)

        self._p1_log = QPlainTextEdit()
        self._p1_log.setReadOnly(True)
        self._p1_log.setPlaceholderText("Results appear here.")
        lay.addWidget(self._p1_log, 1)
        self._update_names()
        return w

    def _prefix(self) -> str:
        return sim.report_prefix(self._month.currentText(), self._year.value(),
                                 self._type.currentData())

    def _update_names(self, *_):
        p = self._prefix()
        self._rename_chk.setText(f'Rename the download to "{p} raw results.csv"')
        self._names_lbl.setText(f'Creates:  "{p} Phish Results.xlsx"   and   "{p} forwards.csv"'
                                "   (next to the .csv)")

    def _browse_csv(self):
        start = os.path.dirname(_clean(self._csv_edit.text())) or str(Path.home() / "Downloads")
        path, _ = QFileDialog.getOpenFileName(self, "Select results export", start,
                                              "CSV (*.csv);;All files (*)")
        if path:
            self._csv_edit.setText(path)

    def _on_csv_changed(self, text):
        parsed = sim.parse_report_name(Path(_clean(text)).stem) if text else None
        if parsed:
            month, year, typ = parsed
            self._month.setCurrentText(month)
            self._year.setValue(year)
            self._type.setCurrentIndex(max(0, self._type.findData(typ)))
        self._p1_open.setEnabled(False)
        self._p1_folder.setEnabled(False)

    def _run_phase1(self):
        src = _clean(self._csv_edit.text())
        if not src or not os.path.isfile(src):
            QMessageBox.warning(self, "MS Attack Sim", "Choose the results .csv first.")
            return
        if not self._month.currentText().strip():
            QMessageBox.warning(self, "MS Attack Sim", "Fill in the month.")
            return
        paths = sim.phase1_paths(Path(src).parent, self._prefix())
        existing = [p.name for k, p in paths.items() if k != "raw_csv" and p.exists()]
        if existing:
            r = QMessageBox.question(self, "Overwrite?",
                                     "Already exists:\n  " + "\n  ".join(existing) + "\n\nReplace?")
            if r != QMessageBox.StandardButton.Yes:
                return
        try:
            raw = sim.read_raw_csv(src)
        except Exception as e:
            self._p1_log.setPlainText(f"✗ {e}")
            return
        answers = self._ask_unknown_columns(raw.headers, [raw])
        if answers is None:
            self._p1_log.setPlainText("Cancelled.")
            return
        unknown_fail, column_notes = answers
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            res = sim.run_phase1(src, self._prefix(), rename_raw=self._rename_chk.isChecked(),
                                 unknown_fail=unknown_fail)
        except PermissionError:
            self._p1_log.setPlainText("✗ Couldn't write the files — is the workbook or .csv "
                                      "open in Excel? Close it and try again.")
            return
        except Exception as e:
            self._p1_log.setPlainText(f"✗ {type(e).__name__}: {e}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        self._p1_xlsx = str(res.xlsx)
        self._p1_open.setEnabled(True)
        self._p1_folder.setEnabled(True)
        if str(res.raw_csv) != src:
            self._csv_edit.blockSignals(True)
            self._csv_edit.setText(str(res.raw_csv))
            self._csv_edit.blockSignals(False)
        # hand the workbook to phase 2
        self._p2_xlsx_edit.setText(self._p1_xlsx)

        c = res.counts
        delivered = sum(c.values()) - c[sim.SHEET_DELIVERY_FAILED]
        lines = [f"✓ {res.phish_type} — {delivered} delivered "
                 f"({c[sim.SHEET_DELIVERY_FAILED]} delivery failed, not counted)", ""]
        lines += [f"  {name:<16} {c[name]}" for name in sim.PHASE1_SHEET_ORDER]
        lines += ["", f"Workbook:  {res.xlsx}"]
        if c[sim.SHEET_FORWARDS]:
            lines += [f"Forwards:  {res.forwards_csv}",
                      f"→ Send the forwards .csv ({c[sim.SHEET_FORWARDS]} users) to the reviewer, "
                      "then do Phase 2 when it comes back."]
        else:
            lines += ["No forwards — Phase 2 only needs the previous simulation's workbook."]
        if column_notes:
            lines += ["", "Unrecognised columns:"] + [f"  • {n}" for n in column_notes]
        if res.warnings:
            lines += ["", "Check:"] + [f"  • {w}" for w in res.warnings]
        self._p1_log.setPlainText("\n".join(lines))

    # ------------------------------------------------- unrecognised columns
    def _saved_columns(self) -> dict:
        return dict(cfg.load().get(_SETTINGS_SECTION, {}).get(_SETTINGS_COLUMNS, {}))

    def _save_column(self, col: str, choice: str):
        s = cfg.load()
        s.setdefault(_SETTINGS_SECTION, {}).setdefault(_SETTINGS_COLUMNS, {})[col] = choice
        cfg.save(s)

    def _forget_columns(self):
        saved = self._saved_columns()
        if not saved:
            QMessageBox.information(self, "MS Attack Sim", "No saved column answers.")
            return
        listing = "\n".join(f"  {c}: {v}" for c, v in sorted(saved.items()))
        r = QMessageBox.question(self, "Forget column answers?",
                                 f"Saved answers:\n\n{listing}\n\nForget them and ask again next time?")
        if r == QMessageBox.StandardButton.Yes:
            s = cfg.load()
            s.get(_SETTINGS_SECTION, {}).pop(_SETTINGS_COLUMNS, None)
            cfg.save(s)

    def _ask_unknown_columns(self, headers: list[str], tables: list[sim.Table]):
        """Ask about each *_TimeStamp column the tool doesn't recognise.

        Returns (columns that count as fail, notes for the log), or None if
        cancelled. Saved answers are reused without asking."""
        saved = self._saved_columns()
        fail: set[str] = set()
        notes: list[str] = []
        for col in sim.unknown_action_columns(headers):
            choice = saved.get(col)
            if choice in ("fail", "ignore"):
                notes.append(f"{col} — {'counts as fail' if choice == 'fail' else 'ignored'} "
                             "(saved answer)")
            else:
                n = sum(1 for t in tables for r in t.rows if sim.has_value(t.get(r, col)))
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Icon.Question)
                box.setWindowTitle("Unrecognised column")
                box.setText(f"This report has a column the tool doesn't recognise:\n\n"
                            f"    {col}\n\n"
                            f"{n} user(s) have a value in it.\n\n"
                            "Does a value in this column mean the user failed?")
                box.addButton("Counts as fail", QMessageBox.ButtonRole.YesRole)
                box.addButton("Ignore it", QMessageBox.ButtonRole.NoRole)
                box.setEscapeButton(box.addButton(QMessageBox.StandardButton.Cancel))
                remember = QCheckBox("Remember this answer for future reports")
                remember.setChecked(True)
                box.setCheckBox(remember)
                box.exec()
                clicked = box.clickedButton()
                role = box.buttonRole(clicked) if clicked is not None else None
                if role == QMessageBox.ButtonRole.YesRole:
                    choice = "fail"
                elif role == QMessageBox.ButtonRole.NoRole:
                    choice = "ignore"
                else:
                    return None         # Cancel / closed
                if remember.isChecked():
                    self._save_column(col, choice)
                notes.append(f"{col} — {'counts as fail' if choice == 'fail' else 'ignored'}"
                             f" ({n} user(s) with a value)")
            if choice == "fail":
                fail.add(col)
        return fail, notes

    def _ask_for_workbook(self, sheets: dict):
        tables = list(sheets.values())
        return self._ask_unknown_columns(sim.workbook_headers(sheets), tables)

    # ================================================================ Phase 2
    def _build_phase2(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)

        grp = QGroupBox("Workbooks")
        g = QVBoxLayout(grp)
        row, self._p2_xlsx_edit = _file_row("This sim (phase 1):", "… Phish Results.xlsx from Phase 1",
                                            self._browse_p2_xlsx)
        self._p2_xlsx_edit.textChanged.connect(lambda *_: self._clear_forwards())
        g.addLayout(row)
        row, self._prev_edit = _file_row("Previous sim (final):",
                                         "The last simulation's finished Phish Results.xlsx",
                                         self._browse_prev)
        g.addLayout(row)
        self._no_prev_chk = QCheckBox("No previous simulation — everyone who failed is Fail x 1")
        self._no_prev_chk.toggled.connect(lambda on: self._prev_edit.setEnabled(not on))
        g.addWidget(self._no_prev_chk)
        lay.addWidget(grp)

        split = QSplitter(Qt.Orientation.Vertical)

        top = QWidget()
        t = QVBoxLayout(top)
        t.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(_btn("Load forwarded users", self._load_forwards))
        self._import_btn = _btn("Import reviewer's file…", self._import_results, enabled=False)
        row.addWidget(self._import_btn)
        row.addSpacing(12)
        row.addWidget(QLabel("A listed email with no Yes/No means:"))
        self._listed_combo = QComboBox()
        for label, val in _LISTED_CHOICES:
            self._listed_combo.addItem(label, val)
        row.addWidget(self._listed_combo)
        row.addStretch()
        t.addLayout(row)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["UserName", "UserMail", "Forwarded", "Reported On", "Forwarded to approved mailbox?"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_MAIL, QHeaderView.ResizeMode.Stretch)
        t.addWidget(self._table, 1)

        row = QHBoxLayout()
        self._paste = QPlainTextEdit()
        self._paste.setPlaceholderText(
            "…or paste the reviewer's reply here (one user per line — the email address plus "
            "Yes / No on the same line), then Apply.")
        self._paste.setMaximumHeight(80)
        row.addWidget(self._paste, 1)
        self._apply_btn = _btn("Apply pasted text", self._apply_paste, enabled=False)
        row.addWidget(self._apply_btn, 0, Qt.AlignmentFlag.AlignTop)
        t.addLayout(row)

        self._decided_lbl = QLabel("")
        self._decided_lbl.setStyleSheet("color: #555;")
        t.addWidget(self._decided_lbl)
        split.addWidget(top)

        bottom = QWidget()
        b = QVBoxLayout(bottom)
        b.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self._finish_btn = _btn("Finish report", self._finish, primary=True, enabled=False)
        self._finish_btn.setToolTip("Updates the phase-1 workbook in place: forwards moved, "
                                    "Fail split into Fail x N.")
        row.addWidget(self._finish_btn)
        self._p2_open = _btn("Open workbook", lambda: _open(self._p2_out), enabled=False)
        row.addWidget(self._p2_open)
        row.addWidget(_btn("Recalculate summary…", self._recalc))
        row.addStretch()
        self._copy_btn = _btn("Copy summary", self._copy_summary, enabled=False)
        row.addWidget(self._copy_btn)
        b.addLayout(row)

        out = QHBoxLayout()
        self._summary = QPlainTextEdit()
        self._summary.setReadOnly(True)
        self._summary.setPlaceholderText("Email summary appears here.")
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._summary.setFont(mono)
        self._summary.setTabStopDistance(28)
        out.addWidget(self._summary, 1)
        self._p2_log = QPlainTextEdit()
        self._p2_log.setReadOnly(True)
        self._p2_log.setPlaceholderText("Notes and anything to check appear here.")
        out.addWidget(self._p2_log, 1)
        b.addLayout(out, 1)
        split.addWidget(bottom)
        split.setSizes([320, 200])
        lay.addWidget(split, 1)
        return w

    # ------------------------------------------------------------ browsing
    def _browse_p2_xlsx(self):
        start = _clean(self._p2_xlsx_edit.text()) or self._p1_xlsx
        path, _ = QFileDialog.getOpenFileName(self, "Select this simulation's workbook",
                                              os.path.dirname(start) if start else "",
                                              "Excel workbook (*.xlsx)")
        if path:
            self._p2_xlsx_edit.setText(path)

    def _browse_prev(self):
        start = _clean(self._prev_edit.text()) or _clean(self._p2_xlsx_edit.text())
        path, _ = QFileDialog.getOpenFileName(self, "Select the previous simulation's final workbook",
                                              os.path.dirname(start) if start else "",
                                              "Excel workbook (*.xlsx)")
        if path:
            self._prev_edit.setText(path)

    # ------------------------------------------------------------ forwards
    def _clear_forwards(self):
        self._loaded = False
        self._forward_users = []
        self._table.setRowCount(0)
        self._import_btn.setEnabled(False)
        self._apply_btn.setEnabled(False)
        self._finish_btn.setEnabled(False)
        self._decided_lbl.setText("")

    def _load_forwards(self):
        path = _clean(self._p2_xlsx_edit.text())
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, "MS Attack Sim", "Choose this simulation's phase-1 workbook first.")
            return
        try:
            _, users = sim.forwarded_users(path)
        except PermissionError:
            QMessageBox.warning(self, "MS Attack Sim", "Couldn't read the workbook — close it in Excel.")
            return
        except Exception as e:
            QMessageBox.warning(self, "MS Attack Sim", str(e))
            return
        self._forward_users = users
        self._loaded = True
        self._table.setRowCount(len(users))
        for i, u in enumerate(users):
            for col, val in ((_COL_NAME, u["name"]), (_COL_MAIL, u["email"]),
                             (_COL_FWD, _fmt(u["forwarded"])), (_COL_REP, _fmt(u["reported"]))):
                self._table.setItem(i, col, QTableWidgetItem(str(val)))
            combo = QComboBox()
            for label, val in _RESULT_CHOICES:
                combo.addItem(label, val)
            combo.currentIndexChanged.connect(self._update_decided)
            self._table.setCellWidget(i, _COL_RESULT, combo)
        self._import_btn.setEnabled(bool(users))
        self._apply_btn.setEnabled(bool(users))
        self._update_decided()
        self._p2_log.setPlainText(
            f"{len(users)} forwarded user(s) loaded." if users
            else "No forwarded users — just pick the previous workbook and Finish.")

    def _combo(self, i) -> QComboBox:
        return self._table.cellWidget(i, _COL_RESULT)

    def _decisions(self) -> list:
        return [self._combo(i).currentData() for i in range(self._table.rowCount())]

    def _update_decided(self, *_):
        d = self._decisions()
        done = sum(v is not None for v in d)
        for i, v in enumerate(d):
            self._combo(i).setStyleSheet("" if v is not None else "QComboBox { background: #fff3cd; }")
        self._finish_btn.setEnabled(self._loaded and done == len(d))
        if d:
            self._decided_lbl.setText(f"{done} of {len(d)} decided"
                                      + ("" if done == len(d) else " — pick the highlighted ones."))

    def _apply_parse(self, parsed: sim.ForwardParse, source: str):
        by_email = {u["email"]: i for i, u in enumerate(self._forward_users) if u["email"]}
        for email, ok in parsed.decisions.items():
            i = by_email.get(email)
            if i is not None:
                c = self._combo(i)
                c.setCurrentIndex(c.findData(ok))
        missing = [u["email"] or u["name"] for i, u in enumerate(self._forward_users)
                   if self._combo(i).currentData() is None]
        lines = [f"{source}: filled {len(parsed.decisions)} result(s)."]
        if parsed.ambiguous:
            lines.append("Couldn't read a clear Yes/No for: " + ", ".join(parsed.ambiguous))
        if parsed.unknown_emails:
            lines.append("Not on the forwards sheet (ignored): " + ", ".join(parsed.unknown_emails))
        if missing:
            lines.append(f"Still to pick ({len(missing)}): " + ", ".join(missing))
        self._p2_log.setPlainText("\n".join(lines))
        self._update_decided()

    def _forward_emails(self) -> set[str]:
        return {u["email"] for u in self._forward_users if u["email"]}

    def _import_results(self):
        start = os.path.dirname(_clean(self._p2_xlsx_edit.text()))
        path, _ = QFileDialog.getOpenFileName(self, "Select the reviewer's results", start,
                                              "Results (*.csv *.xlsx);;All files (*)")
        if not path:
            return
        try:
            rows = sim.read_rows_any(path)
        except Exception as e:
            QMessageBox.warning(self, "MS Attack Sim", f"Couldn't read {os.path.basename(path)}: {e}")
            return
        parsed = sim.parse_forward_rows(rows, self._forward_emails(),
                                        listed_means=self._listed_combo.currentData())
        self._apply_parse(parsed, os.path.basename(path))

    def _apply_paste(self):
        text = self._paste.toPlainText()
        if not text.strip():
            return
        parsed = sim.parse_forward_text(text, self._forward_emails(),
                                        listed_means=self._listed_combo.currentData())
        self._apply_parse(parsed, "Pasted text")

    # ------------------------------------------------------------ finish
    def _finish(self):
        xlsx = _clean(self._p2_xlsx_edit.text())
        prev = None if self._no_prev_chk.isChecked() else _clean(self._prev_edit.text())
        if not self._no_prev_chk.isChecked() and (not prev or not os.path.isfile(prev)):
            QMessageBox.warning(self, "MS Attack Sim",
                                "Choose the previous simulation's final workbook, or tick "
                                "'No previous simulation'.")
            return
        try:
            answers = self._ask_for_workbook(sim.read_workbook(xlsx))
        except Exception as e:
            self._p2_log.setPlainText(f"✗ {e}")
            return
        if answers is None:
            self._p2_log.setPlainText("Cancelled.")
            return
        unknown_fail, column_notes = answers
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            res = sim.finish_workbook(xlsx, self._decisions(), prev, unknown_fail=unknown_fail)
        except PermissionError:
            self._p2_log.setPlainText("✗ Couldn't save — is the workbook open in Excel? "
                                      "Close it and try again.")
            return
        except Exception as e:
            self._p2_log.setPlainText(f"✗ {e}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        self._p2_out = str(res.path)
        self._p2_open.setEnabled(True)
        self._show_summary(res.summary)
        lines = [f"✓ Saved {res.path.name}",
                 f"  forwards → Pass - Report: {res.moved_to_report},  → Fail: {res.moved_to_fail}"]
        lines += self._streak_lines(res.summary)
        if column_notes:
            lines += ["", "Unrecognised columns:"] + [f"  • {n}" for n in column_notes]
        if res.warnings:
            lines += ["", "Check:"] + [f"  • {w}" for w in res.warnings]
        self._p2_log.setPlainText("\n".join(lines))
        # the workbook no longer has a forwards sheet
        self._loaded = False
        self._table.setRowCount(0)
        self._forward_users = []
        self._finish_btn.setEnabled(False)
        self._import_btn.setEnabled(False)
        self._apply_btn.setEnabled(False)
        self._decided_lbl.setText("")

    def _streak_lines(self, s: sim.Summary) -> list[str]:
        if not s.streaks:
            return []
        return ["  " + ",  ".join(f"Fail x {n}: {c}" for n, c in s.streaks.items()),
                f"  Delivery Failed (not counted): {s.delivery_failed}"]

    def _show_summary(self, s: sim.Summary):
        self._summary.setPlainText(s.email_text())
        self._copy_btn.setEnabled(True)

    def _recalc(self):
        start = _clean(self._p2_xlsx_edit.text()) or self._p2_out
        path, _ = QFileDialog.getOpenFileName(self, "Workbook to summarize",
                                              os.path.dirname(start) if start else "",
                                              "Excel workbook (*.xlsx)")
        if not path:
            return
        try:
            sheets = sim.read_workbook(path)
        except Exception as e:
            self._p2_log.setPlainText(f"✗ {e}")
            return
        answers = self._ask_for_workbook(sheets)
        if answers is None:
            return
        s = sim.summarize(sheets, answers[0])
        self._show_summary(s)
        lines = [f"Summary of {os.path.basename(path)}"] + self._streak_lines(s)
        if s.other:
            lines.append(f"  • {s.other} user(s) on a Fail sheet with no click, reply or forward "
                         "— counted as 'Other'.")
        self._p2_log.setPlainText("\n".join(lines))

    def _copy_summary(self):
        QGuiApplication.clipboard().setText(self._summary.toPlainText())
        self._p2_log.appendPlainText("Summary copied.")
