"""
Website tab — WordPress update checker and site health check.

WordPress Updates section:
  On startup, auto-checks if it has been 14+ days since the last check.
  "Check Now" button for manual runs.
  Results shown in a read-only text area with a Copy to Clipboard button.

Site Health Check section:
  "Check Live" and "Check Staging" buttons.
  Results table (URL | Status | Issues) plus a scrollable log.
  Copy Report button puts the full plain-text report on the clipboard.
"""

import threading
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QObject, Signal, Slot, QTimer
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QTextEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QSplitter, QMessageBox,
)

from app.automation.website.cache_clear import clear_wp_cache
from app.automation.website.wp_updates import check_wp_updates, WpUpdateResult
from app.automation.website.site_checker import check_site, SiteCheckResult
from app.config import settings as cfg
from app.db import history

# Days between automatic WP update checks
_AUTO_CHECK_INTERVAL_DAYS = 14


class _Signals(QObject):
    wp_log = Signal(str)
    wp_finished = Signal(object)          # WpUpdateResult
    site_log = Signal(str)
    site_finished = Signal(object)        # SiteCheckResult
    site_page_done = Signal(int, object)  # (row_index, PageResult)
    cache_log = Signal(str)
    cache_finished = Signal(object)       # dict with cache clear results


class WebsiteTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._signals = _Signals()
        self._signals.wp_log.connect(self._on_wp_log)
        self._signals.wp_finished.connect(self._on_wp_finished)
        self._signals.site_log.connect(self._on_site_log)
        self._signals.site_finished.connect(self._on_site_finished)
        self._signals.cache_log.connect(self._on_cache_log)
        self._signals.cache_finished.connect(self._on_cache_finished)

        self._wp_running = False
        self._site_running = False
        self._current_wp_result: WpUpdateResult | None = None
        self._current_site_result: SiteCheckResult | None = None

        self._setup_ui()

        # Schedule auto-check slightly after startup so the UI is fully rendered
        QTimer.singleShot(1500, self._maybe_auto_check_wp)
        self._setup_daily_cache_timer()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._build_wp_section())
        splitter.addWidget(self._build_cache_section())
        splitter.addWidget(self._build_site_section())
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        root.addWidget(splitter)

    def _build_wp_section(self) -> QGroupBox:
        group = QGroupBox("WordPress Updates")
        layout = QVBoxLayout(group)

        # Top row: status label + Check Now button
        top_row = QHBoxLayout()
        self._wp_status_label = QLabel("Last checked: never")
        self._wp_status_label.setStyleSheet("color: #6c757d;")
        self._wp_check_btn = QPushButton("Check Now")
        self._wp_check_btn.setFixedWidth(110)
        self._wp_check_btn.clicked.connect(self._on_wp_check_clicked)
        self._wp_check_btn.setStyleSheet(
            "QPushButton { background: #0d6efd; color: white; border-radius: 4px; }"
            "QPushButton:hover { background: #0b5ed7; }"
            "QPushButton:disabled { background: #6c757d; }"
        )
        top_row.addWidget(self._wp_status_label)
        top_row.addStretch()
        top_row.addWidget(self._wp_check_btn)
        layout.addLayout(top_row)

        # Results text area
        self._wp_results = QTextEdit()
        self._wp_results.setReadOnly(True)
        self._wp_results.setPlaceholderText(
            "Update check results will appear here.\n"
            "Checks run automatically every 14 days when you open the app."
        )
        self._wp_results.setStyleSheet(
            "font-family: monospace; background: #f8f9fa; color: #222;"
        )
        layout.addWidget(self._wp_results)

        # Copy button
        btn_row = QHBoxLayout()
        self._wp_copy_btn = QPushButton("Copy to Clipboard")
        self._wp_copy_btn.setEnabled(False)
        self._wp_copy_btn.clicked.connect(self._copy_wp_report)
        btn_row.addStretch()
        btn_row.addWidget(self._wp_copy_btn)
        layout.addLayout(btn_row)

        return group
    def _build_cache_section(self) -> QGroupBox:
        group = QGroupBox("Cache Management")
        layout = QVBoxLayout(group)

        # top row
        top_row = QHBoxLayout()

        self._cache_status_label = QLabel("Last cache clear: never")
        self._cache_status_label.setStyleSheet("color: #6c757d;")

        self._cache_btn = QPushButton("Clear Cache")
        self._cache_btn.setFixedWidth(140)
        self._cache_btn.clicked.connect(self._on_cache_clicked)
        self._cache_btn.setStyleSheet(
            "QPushButton { background: #fd7e14; color: white; border-radius: 4px; }"
            "QPushButton:hover { background: #e8590c; }"
            "QPushButton:disabled { background: #6c757d; }"
        )

        top_row.addWidget(self._cache_status_label)
        top_row.addStretch()
        top_row.addWidget(self._cache_btn)

        layout.addLayout(top_row)

        # results box (separate from WP logs)
        self._cache_results = QTextEdit()
        self._cache_results.setReadOnly(True)
        self._cache_results.setStyleSheet(
            "font-family: monospace; background: #f8f9fa; color: black;"
        )

        layout.addWidget(self._cache_results)

        return group

    def _build_site_section(self) -> QGroupBox:
        group = QGroupBox("Site Health Check")
        layout = QVBoxLayout(group)

        # Button row
        btn_row = QHBoxLayout()
        self._live_btn = QPushButton("▶  Check Live")
        self._live_btn.setFixedHeight(32)
        self._live_btn.clicked.connect(lambda: self._on_site_check_clicked("live"))
        self._live_btn.setStyleSheet(
            "QPushButton { background: #198754; color: white; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #157347; }"
            "QPushButton:disabled { background: #6c757d; }"
        )

        self._staging_btn = QPushButton("▶  Check Staging")
        self._staging_btn.setFixedHeight(32)
        self._staging_btn.clicked.connect(lambda: self._on_site_check_clicked("staging"))
        self._staging_btn.setStyleSheet(
            "QPushButton { background: #6f42c1; color: white; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #59359a; }"
            "QPushButton:disabled { background: #6c757d; }"
        )
        self._staging_btn.setToolTip("Configure a Staging URL in Settings → Website to enable this.")

        self._site_status_label = QLabel("")
        self._site_status_label.setStyleSheet("color: #6c757d;")

        btn_row.addWidget(self._live_btn)
        btn_row.addWidget(self._staging_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._site_status_label)
        layout.addLayout(btn_row)

        # Results splitter: table on top, log on bottom
        inner_splitter = QSplitter(Qt.Orientation.Vertical)

        # Results table
        self._site_table = QTableWidget(0, 3)
        self._site_table.setHorizontalHeaderLabels(["Page URL", "Status", "Issues"])
        self._site_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._site_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._site_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._site_table.verticalHeader().setVisible(False)
        self._site_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._site_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._site_table.setAlternatingRowColors(True)
        inner_splitter.addWidget(self._site_table)

        # Log
        log_wrap = QWidget()
        log_layout = QVBoxLayout(log_wrap)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_label = QLabel("Log")
        log_label.setStyleSheet("font-weight: bold; color: #495057;")
        self._site_log = QTextEdit()
        self._site_log.setReadOnly(True)
        self._site_log.setStyleSheet(
            "font-family: monospace; background: #f8f9fa; color: #222;"
        )
        log_layout.addWidget(log_label)
        log_layout.addWidget(self._site_log)
        inner_splitter.addWidget(log_wrap)

        inner_splitter.setStretchFactor(0, 3)
        inner_splitter.setStretchFactor(1, 1)
        layout.addWidget(inner_splitter)

        # Copy report button
        copy_row = QHBoxLayout()
        self._site_copy_btn = QPushButton("Copy Report to Clipboard")
        self._site_copy_btn.setEnabled(False)
        self._site_copy_btn.clicked.connect(self._copy_site_report)
        copy_row.addStretch()
        copy_row.addWidget(self._site_copy_btn)
        layout.addLayout(copy_row)

        return group

    # -----------------------------------------------------------------------
    # Startup auto-check
    # -----------------------------------------------------------------------

    def _maybe_auto_check_wp(self):
        """Auto-run the WP update check if it's been 14+ days since the last one."""
        last = history.get_last_wp_update_run()
        if last:
            try:
                last_dt = datetime.fromisoformat(last["timestamp"])
                if datetime.now() - last_dt < timedelta(days=_AUTO_CHECK_INTERVAL_DAYS):
                    # Show the last result without re-running
                    self._display_wp_result_from_dict(last["result"])
                    return
            except Exception:
                pass

        # No recent run — auto-check
        s = cfg.load()
        wp_url = s.get("website", {}).get("wp_admin_url", "")
        if not wp_url:
            self._wp_status_label.setText("Last checked: never  (configure WordPress URL in Settings → Website)")
            return

        self._wp_results.setPlaceholderText("")
        self._wp_results.setText("Checking for updates...")
        self._wp_status_label.setText("Checking...")
        self._run_wp_check()
        self._wp_results.clear()
        self._wp_results.setText("Checking for updates...")

    def _display_wp_result_from_dict(self, d: dict):
        """Render a run replayed from history using the same renderer as a live run."""
        result = WpUpdateResult.from_dict(d)
        self._wp_status_label.setText(f"Last checked: {result.checked_at}")
        self._wp_results.setText(result.to_report_text())
        self._wp_copy_btn.setEnabled(True)

    # -----------------------------------------------------------------------
    # Auto cache clear timer: run every 24 hours
    # -----------------------------------------------------------------------       

    def _setup_daily_cache_timer(self):
        # run once shortly after startup
        QTimer.singleShot(5000, self._maybe_run_daily_cache_clear)

        # then run every 24 hours
        self._cache_timer = QTimer(self)
        self._cache_timer.timeout.connect(self._maybe_run_daily_cache_clear)
        self._cache_timer.start(24 * 60 * 60 * 1000)  # 24 hours in ms
    
    def _maybe_run_daily_cache_clear(self):
        try:
            last = history.get_last_cache_clear_run()

            if last:
                last_dt = datetime.fromisoformat(last["timestamp"])
                if datetime.now() - last_dt < timedelta(days=1):
                    self._cache_status_label.setText(f"Last cache clear: {last['timestamp']}")
                    return  # already ran in last 24h

            self._cache_results.append("\n[Auto] Running daily cache clear...")
            self._run_cache_clear()

        except Exception as e:
            self._cache_results.append(f"\n[Auto] Cache clear failed to start: {e}")

    # -----------------------------------------------------------------------
    # WordPress update check
    # -----------------------------------------------------------------------

    def _on_wp_check_clicked(self):
        s = cfg.load()
        self._wp_results.clear()
        self._wp_results.setText("Checking for updates...")

        wp_cfg = s.get("website", {})
        if not wp_cfg.get("wp_admin_url"):
            QMessageBox.warning(
                self, "Not Configured",
                "Please configure the WordPress URL and credentials in Settings → Website."
            )
            return
        self._run_wp_check()

    def _run_wp_check(self):
        if self._wp_running:
            return
        self._wp_running = True
        self._wp_check_btn.setEnabled(False)
        self._wp_check_btn.setText("Checking…")
        self._wp_copy_btn.setEnabled(False)

        def worker():
            s = cfg.load()
            wp_cfg = s.get("website", {})
            result = check_wp_updates(
                wp_admin_url=wp_cfg.get("wp_admin_url", ""),
                username=wp_cfg.get("wp_username", ""),
                password=wp_cfg.get("wp_password", ""),
                headless=True,
                log=lambda msg: self._signals.wp_log.emit(msg),
            )
            self._signals.wp_finished.emit(result)

        threading.Thread(target=worker, daemon=True).start()

    @Slot(str)
    def _on_wp_log(self, msg: str):
        self._wp_results.append(msg)

    @Slot(object)
    def _on_wp_finished(self, result: WpUpdateResult):
        self._wp_running = False
        self._wp_check_btn.setEnabled(True)
        self._wp_check_btn.setText("Check Now")
        self._current_wp_result = result

        self._wp_results.setText(result.to_report_text())
        self._wp_status_label.setText(f"Last checked: {result.checked_at}")
        self._wp_copy_btn.setEnabled(True)

        if result.error:
            QMessageBox.critical(
                self,
                "WordPress Update Check Failed",
                f"The update check could not complete:\n\n{result.error}",
            )

        # Save to history
        try:
            history.save_wp_update_run(result.to_dict())
        except Exception as e:
            self._wp_results.append(f"\n(Could not save to history: {e})")

    def _copy_wp_report(self):
        text = self._wp_results.toPlainText()
        if text:
            QGuiApplication.clipboard().setText(text)
    
    # -----------------------------------------------------------------------
    # Cache clear
    # -----------------------------------------------------------------------

    def _on_cache_clicked(self):
        self._cache_results.clear()
        self._cache_results.setText("Clearing cache...")
        if self._wp_running:
            return

        s = cfg.load()
        wp_cfg = s.get("website", {})

        if not wp_cfg.get("wp_admin_url"):
            QMessageBox.warning(
                self, "Not Configured",
                "Please configure WordPress settings in Settings → Website."
            )
            return

        self._run_cache_clear()


    def _run_cache_clear(self):
        self._cache_btn.setEnabled(False)
        self._cache_btn.setText("Clearing…")

        def worker():
            s = cfg.load()
            wp_cfg = s.get("website", {})

            result = clear_wp_cache(
                wp_admin_url=wp_cfg.get("wp_admin_url", ""),
                username=wp_cfg.get("wp_username", ""),
                password=wp_cfg.get("wp_password", ""),
                headless=wp_cfg.get("headless", True),
                log=lambda msg: self._signals.cache_log.emit(msg),
            )

            self._signals.cache_finished.emit(result)

        threading.Thread(target=worker, daemon=True).start()


    @Slot(str)
    def _on_cache_log(self, msg: str):
        self._cache_results.append(msg)


    @Slot(object)
    def _on_cache_finished(self, result: dict):
        self._cache_btn.setEnabled(True)
        self._cache_btn.setText("Clear Cache")

        if result.get("success"):
            self._cache_results.append("\nCache cleared successfully.")

            try:
                history.save_cache_clear_run({
                    "timestamp": datetime.now().isoformat(),
                    "result": result
                })
            except Exception as e:
                self._cache_results.append(f"(Could not save cache clear history: {e})")

        else:
            error_msg = result.get("error", "Unknown error")
            self._cache_results.append(f"\nCache clear failed: {error_msg}")
            QMessageBox.critical(
                self,
                "Cache Clear Failed",
                f"The cache could not be cleared:\n\n{error_msg}",
            )

        # update label
        try:
            last = history.get_last_cache_clear_run()
            if last:
                self._cache_status_label.setText(f"Last cache clear: {last['timestamp']}")
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Site health check
    # -----------------------------------------------------------------------

    def _on_site_check_clicked(self, target: str):
        s = cfg.load()
        site_cfg = s.get("website", {})

        if target == "live":
            base_url = site_cfg.get("live_url", "").strip()
            if not base_url:
                QMessageBox.warning(
                    self, "Not Configured",
                    "Please configure the Live Site URL in Settings → Website."
                )
                return
        else:
            base_url = site_cfg.get("staging_url", "").strip()
            if not base_url:
                QMessageBox.warning(
                    self, "Not Configured",
                    "Please configure the Staging URL in Settings → Website."
                )
                return

        sample_pages = site_cfg.get("sample_pages", [])
        if not sample_pages:
            QMessageBox.warning(
                self, "No Sample Pages",
                "No sample pages are configured.\n\n"
                "Go to Settings → Website and add the URLs you want checked."
            )
            return

        self._start_site_check(target, base_url, sample_pages)

    def _start_site_check(self, target: str, base_url: str, sample_pages: list):
        if self._site_running:
            return
        self._site_running = True
        self._live_btn.setEnabled(False)
        self._staging_btn.setEnabled(False)
        self._site_copy_btn.setEnabled(False)
        self._site_log.clear()
        self._site_status_label.setText("Running…")

        # Pre-populate table with pending rows
        self._site_table.setRowCount(0)
        for url in sample_pages:
            row = self._site_table.rowCount()
            self._site_table.insertRow(row)
            self._site_table.setItem(row, 0, QTableWidgetItem(url))
            status_item = QTableWidgetItem("…")
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._site_table.setItem(row, 1, status_item)
            self._site_table.setItem(row, 2, QTableWidgetItem(""))

        def worker():
            result = check_site(
                target=target,
                base_url=base_url,
                sample_pages=sample_pages,
                log=lambda msg: self._signals.site_log.emit(msg),
            )
            self._signals.site_finished.emit(result)

        threading.Thread(target=worker, daemon=True).start()

    @Slot(str)
    def _on_site_log(self, msg: str):
        self._site_log.append(msg)
        # Update table row status live as log messages arrive
        # Format: "[N/M] Checking: <url>" → find that row and mark as running
        if msg.startswith("[") and "Checking:" in msg:
            url = msg.split("Checking:", 1)[-1].strip()
            self._set_row_status(url, "…", "#cce5ff", "#004085", "")

    @Slot(object)
    def _on_site_finished(self, result: SiteCheckResult):
        self._site_running = False
        self._live_btn.setEnabled(True)
        self._current_site_result = result

        s = cfg.load()
        staging_url = s.get("website", {}).get("staging_url", "").strip()
        self._staging_btn.setEnabled(bool(staging_url))

        # Populate table with final results
        for pr in result.pages:
            if pr.has_issues:
                summary = pr.issue_summary()
                self._set_row_status(pr.url, "Issues", "#f8d7da", "#721c24", summary)
            else:
                self._set_row_status(pr.url, "OK", "#d4edda", "#155724", "")

        issues = sum(1 for p in result.pages if p.has_issues)
        if result.error:
            self._site_status_label.setText(f"Error: {result.error[:60]}")
            self._site_status_label.setStyleSheet("color: #721c24;")
        elif issues:
            self._site_status_label.setText(f"{issues} page(s) with issues")
            self._site_status_label.setStyleSheet("color: #721c24; font-weight: bold;")
        else:
            self._site_status_label.setText("All clear")
            self._site_status_label.setStyleSheet("color: #155724; font-weight: bold;")

        self._site_copy_btn.setEnabled(True)

        # Save to history
        try:
            history.save_site_check_run(result.target, result.base_url, result.to_dict())
        except Exception as e:
            self._site_log.append(f"(Could not save to history: {e})")

    def _set_row_status(
        self, url: str, status_text: str, bg: str, fg: str, issues_text: str
    ):
        """Find the table row matching url and update its Status and Issues cells."""
        for row in range(self._site_table.rowCount()):
            item = self._site_table.item(row, 0)
            if item and item.text() == url:
                status_item = QTableWidgetItem(status_text)
                status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                status_item.setBackground(QColor(bg))
                status_item.setForeground(QColor(fg))
                status_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                self._site_table.setItem(row, 1, status_item)
                self._site_table.setItem(row, 2, QTableWidgetItem(issues_text))
                break

    def _copy_site_report(self):
        if self._current_site_result:
            QGuiApplication.clipboard().setText(self._current_site_result.to_report_text())

    # -----------------------------------------------------------------------
    # Visibility: disable staging button if no staging URL configured
    # -----------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        s = cfg.load()
        staging_url = s.get("website", {}).get("staging_url", "").strip()
        self._staging_btn.setEnabled(bool(staging_url) and not self._site_running)
        if not staging_url:
            self._staging_btn.setToolTip("Configure a Staging URL in Settings → Website to enable.")
        else:
            self._staging_btn.setToolTip("")
