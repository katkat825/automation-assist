"""
Scheduler tab — read-only overview of all automatically scheduled tasks.
"""

from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
)

from app.db import history


_TASKS = [
    {
        "name": "WordPress Update Check",
        "frequency": "Every 14 days (on app launch)",
        "get_last": history.get_last_wp_update_run,
        "interval_days": 14,
        "note": "Triggered at startup; skipped if already ran within 14 days.",
    },
    {
        "name": "Cache Clear",
        "frequency": "Every 24 hours",
        "get_last": history.get_last_cache_clear_run,
        "interval_days": 1,
        "note": "Runs automatically while the app is open; also checked at startup.",
    },
]

_COLUMNS = ["Task", "Frequency", "Last Run", "Next Scheduled", "Notes"]


class SchedulerTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header = QLabel("Scheduled Tasks")
        header.setStyleSheet("font-weight: bold; color: #212529;")
        layout.addWidget(header)

        sub = QLabel(
            "These tasks run automatically in the background. "
            "Manual runs can be triggered from the Website tab."
        )
        sub.setStyleSheet("color: #6c757d;")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table)

        self._populate()

    def _populate(self):
        self._table.setRowCount(0)
        for task in _TASKS:
            row = self._table.rowCount()
            self._table.insertRow(row)

            last_run_str = "Never"
            next_run_str = "On next app launch"

            try:
                last = task["get_last"]()
                if last:
                    ts = last["timestamp"]
                    last_dt = datetime.fromisoformat(ts)
                    last_run_str = last_dt.strftime("%Y-%m-%d %H:%M")
                    next_dt = last_dt + timedelta(days=task["interval_days"])
                    now = datetime.now()
                    if next_dt <= now:
                        next_run_str = "Overdue — will run at next opportunity"
                    else:
                        next_run_str = next_dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                last_run_str = "Unknown"
                next_run_str = "Unknown"

            values = [
                task["name"],
                task["frequency"],
                last_run_str,
                next_run_str,
                task["note"],
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                self._table.setItem(row, col, item)

    def showEvent(self, event):
        """Refresh the table each time the tab is shown so timestamps stay current."""
        super().showEvent(event)
        self._populate()
