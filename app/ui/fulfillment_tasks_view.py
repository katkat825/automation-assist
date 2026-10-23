"""
Tasks sub-tab of the Fulfillment tab: the order's tasks on the left; the
selected task on the right as separate Subject / Body / Public reply boxes, each
with its own Copy button, ready to paste into BoldDesk.

Display only. The rules live in app/automation/fulfillment/tasks.py and the
ticket wording in tickets.py.
"""

from __future__ import annotations

import html
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPlainTextEdit,
    QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from app.automation.fulfillment import tasks as ft

_GREY = "#6c757d"
_RED = "#b02a37"
_MONO = "font-family: Consolas, monospace;"


class TasksView(QWidget):
    def __init__(self, make_button: Callable[..., QPushButton], parent=None):
        super().__init__(parent)
        self._plan = ft.TaskPlan()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._header = QLabel("")
        self._header.setWordWrap(True)
        layout.addWidget(self._header)

        split = QSplitter(Qt.Orientation.Horizontal)
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._show_task)
        split.addWidget(self._list)

        detail = QWidget()
        d = QVBoxLayout(detail)
        d.setContentsMargins(0, 0, 0, 0)
        self._info = QLabel("")
        self._info.setWordWrap(True)
        self._info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        d.addWidget(self._info)

        self._subject_label = QLabel("<b>Subject</b>")
        d.addWidget(self._subject_label)
        row = QHBoxLayout()
        self._subject = QLineEdit()
        self._subject.setReadOnly(True)
        self._subject.setStyleSheet(_MONO)
        row.addWidget(self._subject, stretch=1)
        self._subject_copy = make_button("Copy", lambda _=False: _copy(self._subject.text()))
        row.addWidget(self._subject_copy)
        d.addLayout(row)

        self._body_label = QLabel("<b>Body</b>")
        d.addWidget(self._body_label)
        self._body, body_row = self._text_box(make_button)
        d.addWidget(self._body, stretch=2)
        d.addLayout(body_row)

        self._reply_label = QLabel("<b>Public reply</b>")
        d.addWidget(self._reply_label)
        self._reply, self._reply_row = self._text_box(make_button)
        d.addWidget(self._reply, stretch=2)
        d.addLayout(self._reply_row)
        split.addWidget(detail)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        layout.addWidget(split, stretch=1)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(make_button("Copy all", lambda _=False: _copy(ft.format_plan(self._plan))))
        layout.addLayout(row)
        self.clear()

    @staticmethod
    def _text_box(make_button) -> tuple[QPlainTextEdit, QHBoxLayout]:
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setStyleSheet(_MONO)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(make_button("Copy", lambda _=False: _copy(box.toPlainText())))
        return box, row

    def show_plan(self, plan: ft.TaskPlan):
        self._plan = plan
        self._header.setText("<br>".join(
            f"<span style='color:{_RED}'>{html.escape(h)}</span>" if h.startswith("!")
            else html.escape(h) for h in plan.header))
        self._list.clear()
        for n, t in enumerate(plan.tasks, 1):
            self._list.addItem(f"{n}. {'! ' if t.confirm else ''}{t.title}")
        if plan.tasks:
            self._list.setCurrentRow(0)

    def clear(self):
        self._plan = ft.TaskPlan()
        self._header.setText(f"<span style='color:{_GREY}'>Check order lists the work this order needs here.</span>")
        self._list.clear()
        self._show_task(-1)

    def _show_task(self, row: int):
        task = self._plan.tasks[row] if 0 <= row < len(self._plan.tasks) else None
        ticket = task.ticket if task else None

        info = []
        if task:
            info.append(f"<b>{html.escape(task.title)}</b> &nbsp; <span style='color:{_GREY}'>"
                        f"{html.escape(task.handled_by)}</span>")
            if task.confirm:
                info.append(f"<span style='color:{_RED}'>! {html.escape(task.confirm)}</span>")
            if ticket and ticket.options:
                info.append(html.escape(ticket.options))
            if ticket and ticket.attachments:
                info.append("Attach: " + html.escape(", ".join(ticket.attachments)))
        self._info.setText("<br>".join(info))

        for w in (self._subject_label, self._subject, self._subject_copy):
            w.setVisible(bool(ticket))
        self._subject.setText(ticket.subject if ticket else "")
        self._subject.setCursorPosition(0)
        self._body_label.setText("<b>Body</b>" if ticket else "<b>Details</b>")
        self._body.setPlainText(ticket.body if ticket else (ft.task_text(task) if task else ""))

        has_reply = bool(ticket and ticket.reply)
        for w in (self._reply_label, self._reply):
            w.setVisible(has_reply)
        for i in range(self._reply_row.count()):
            w = self._reply_row.itemAt(i).widget()
            if w:
                w.setVisible(has_reply)
        self._reply.setPlainText(ticket.reply if has_reply else "")


def _copy(text: str):
    QApplication.clipboard().setText(text)
