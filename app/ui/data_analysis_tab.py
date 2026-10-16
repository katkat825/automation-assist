"""
Data Analysis tab — report sorting, separation and summaries.

One sub-tab per report type. To add one: build a QWidget for it (see
attack_sim_view.py for the pattern — logic in app/automation/data_analysis/,
UI only here) and add it to _REPORTS below.
"""

from __future__ import annotations

from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from .attack_sim_view import AttackSimView

# (sub-tab label, widget class)
_REPORTS = [
    ("MS Attack Sim", AttackSimView),
]


class DataAnalysisTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        self._tabs = QTabWidget()
        for label, cls in _REPORTS:
            self._tabs.addTab(cls(), label)
        root.addWidget(self._tabs)
