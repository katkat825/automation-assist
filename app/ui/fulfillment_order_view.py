"""
Order sub-tab of the Fulfillment tab: the portal expiration date (copyable),
then warnings and the order's facts / SKUs / archive instructions, each with
the document it came from.

Display only. The checks live in app/automation/fulfillment/order_check.py.
"""

from __future__ import annotations

import html
from typing import Callable

from PySide6.QtWidgets import (
    QApplication, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTextEdit, QVBoxLayout, QWidget,
)

from app.automation.fulfillment import order_check as oc

_GREY = "#6c757d"
_RED = "#b02a37"


class OrderView(QWidget):
    def __init__(self, make_button: Callable[..., QPushButton], parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        exp_group = QGroupBox()
        eg = QVBoxLayout(exp_group)
        eg.addWidget(QLabel("<b>Portal exp date</b>"))
        row = QHBoxLayout()
        self._exp_field = QLineEdit()
        self._exp_field.setReadOnly(True)
        self._exp_field.setStyleSheet("font-family: Consolas, monospace;")
        row.addWidget(self._exp_field, stretch=1)
        row.addWidget(make_button(
            "Copy", lambda _=False: QApplication.clipboard().setText(self._exp_field.text())))
        eg.addLayout(row)
        self._exp_from = QLabel("")
        self._exp_from.setWordWrap(True)
        self._exp_from.setStyleSheet(f"color: {_GREY};")
        eg.addWidget(self._exp_from)
        layout.addWidget(exp_group)

        self._body = QTextEdit()
        self._body.setReadOnly(True)
        self._body.setPlaceholderText(
            "Read the PDFs and paste the Order Info email. Mismatches between the "
            "email, SOW, estimate and portal list show here."
        )
        layout.addWidget(self._body, stretch=1)

    def show_check(self, check: oc.OrderCheck):
        exp = check.exp
        self._exp_field.setText(exp.date or "n/a")
        if exp.date:
            self._exp_from.setText("From: " + "; ".join(
                sorted({f"{c.found_as} [{c.source}]" for c in exp.candidates})))
        elif exp.candidates:
            self._exp_from.setText("The sources disagree. See warnings.")
        else:
            self._exp_from.setText("No license dates found yet.")
        self._body.setHtml(_check_html(check))

    def clear(self):
        self._exp_field.clear()
        self._exp_from.clear()
        self._body.clear()


def _check_html(check: oc.OrderCheck) -> str:
    parts = []
    if check.warnings:
        items = "".join(f"<li>{_esc(w)}</li>" for w in check.warnings)
        parts.append(f"<h4 style='color:{_RED}'>Check these ({len(check.warnings)})</h4><ul>{items}</ul>")
    else:
        parts.append("<p><b>No mismatches found.</b></p>")
    parts.append(_table("Order", ("", "", "From"),
                        [(f.label, f.value, f.source) for f in check.facts]))
    parts.append(_table("SKUs", ("SKU", "Users", "Description", "From"),
                        [(s.sku, "" if s.users is None else str(s.users), s.description, s.source)
                         for s in check.skus]))
    parts.append(_table("Course archive (SOW)", ("", ""), check.archive))
    return "".join(p for p in parts if p)


def _table(title: str, headers: tuple[str, ...], rows) -> str:
    rows = list(rows)
    if not rows:
        return ""
    head = "".join(f"<th align='left'>{_esc(h)}</th>" for h in headers) if any(headers) else ""
    body = "".join(
        "<tr>" + "".join(
            f"<td style='color:{_GREY}'>{_esc(c)}</td>" if i == len(r) - 1 and headers[-1] == "From"
            else f"<td>{_esc(c)}</td>"
            for i, c in enumerate(r)) + "</tr>"
        for r in rows
    )
    return (f"<h4>{_esc(title)}</h4><table cellspacing='0' cellpadding='3'>"
            f"{'<tr>' + head + '</tr>' if head else ''}{body}</table>")


def _esc(text: str) -> str:
    return html.escape(text).replace("\n", "<br>")
