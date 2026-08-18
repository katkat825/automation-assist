"""
Settings dialog — lets you enter WordPress credentials and QA options
without editing a JSON file directly.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTabWidget, QWidget, QFormLayout,
    QLineEdit, QCheckBox, QPushButton, QHBoxLayout, QFileDialog, QLabel,
    QListWidget, QListWidgetItem, QMessageBox,
)
from PySide6.QtCore import Qt

from app.config import settings as cfg


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)
        self._settings = cfg.load()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        tabs.addTab(self._make_qa_tab(), "QA Options")
        tabs.addTab(self._make_website_tab(), "Website")

        layout.addWidget(tabs)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _make_qa_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        qa = self._settings["qa"]

        self._screenshot_dir = QLineEdit(qa.get("screenshot_dir", ""))
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_screenshot_dir)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self._screenshot_dir)
        dir_row.addWidget(browse_btn)
        dir_widget = QWidget()
        dir_widget.setLayout(dir_row)

        self._headless = QCheckBox("Run browser headless (no visible window)")
        self._headless.setChecked(qa.get("headless", False))

        form.addRow("Screenshot folder:", dir_widget)
        form.addRow(self._headless)
        return w

    def _make_website_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        form = QFormLayout()
        site = self._settings["website"]

        self._wp_admin_url = QLineEdit(site.get("wp_admin_url", ""))
        self._wp_admin_url.setPlaceholderText("https://yoursite.com")
        self._wp_user = QLineEdit(site.get("wp_username", ""))
        self._wp_user.setPlaceholderText("WordPress admin username or email")
        self._wp_pass = QLineEdit(site.get("wp_password", ""))
        self._wp_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self._live_url = QLineEdit(site.get("live_url", ""))
        self._live_url.setPlaceholderText("https://yoursite.com")
        self._staging_url = QLineEdit(site.get("staging_url", ""))
        self._staging_url.setPlaceholderText("https://staging.yoursite.com (optional)")

        form.addRow("WordPress URL:", self._wp_admin_url)
        form.addRow("WP Username:", self._wp_user)
        form.addRow("WP Password:", self._wp_pass)
        form.addRow("Live Site URL:", self._live_url)
        form.addRow("Staging URL:", self._staging_url)
        layout.addLayout(form)

        # Sample pages list
        pages_label = QLabel("Sample Pages for Health Check:")
        pages_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout.addWidget(pages_label)

        note = QLabel(
            "Add the URLs you want checked after each update. "
            "Include a mix of page types — home, key content pages, contact, etc."
        )
        note.setStyleSheet("color: #6c757d;")
        note.setWordWrap(True)
        layout.addWidget(note)

        self._sample_pages_list = QListWidget()
        self._sample_pages_list.setFixedHeight(140)
        for url in site.get("sample_pages", []):
            self._sample_pages_list.addItem(QListWidgetItem(url))
        layout.addWidget(self._sample_pages_list)

        # Add/remove row
        add_row = QHBoxLayout()
        self._new_page_input = QLineEdit()
        self._new_page_input.setPlaceholderText("https://yoursite.com/some-page/")
        self._new_page_input.returnPressed.connect(self._add_sample_page)
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_sample_page)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_sample_page)
        add_row.addWidget(self._new_page_input)
        add_row.addWidget(add_btn)
        add_row.addWidget(remove_btn)
        layout.addLayout(add_row)

        layout.addStretch()
        return w

    def _add_sample_page(self):
        url = self._new_page_input.text().strip()
        if not url:
            return
        if not url.startswith(("http://", "https://")):
            QMessageBox.warning(self, "Invalid URL", "URL must start with http:// or https://")
            return
        # Check for duplicates
        for i in range(self._sample_pages_list.count()):
            if self._sample_pages_list.item(i).text() == url:
                self._new_page_input.clear()
                return
        self._sample_pages_list.addItem(QListWidgetItem(url))
        self._new_page_input.clear()

    def _remove_sample_page(self):
        for item in self._sample_pages_list.selectedItems():
            self._sample_pages_list.takeItem(self._sample_pages_list.row(item))

    def _browse_screenshot_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Screenshot Folder")
        if d:
            self._screenshot_dir.setText(d)

    def _save(self):
        s = self._settings
        s["qa"]["screenshot_dir"] = self._screenshot_dir.text().strip()
        s["qa"]["headless"] = self._headless.isChecked()
        s["website"]["wp_admin_url"] = self._wp_admin_url.text().strip()
        s["website"]["wp_username"] = self._wp_user.text().strip()
        s["website"]["wp_password"] = self._wp_pass.text()
        s["website"]["live_url"] = self._live_url.text().strip()
        s["website"]["staging_url"] = self._staging_url.text().strip()
        s["website"]["sample_pages"] = [
            self._sample_pages_list.item(i).text()
            for i in range(self._sample_pages_list.count())
        ]

        cfg.save(s)
        self.accept()
