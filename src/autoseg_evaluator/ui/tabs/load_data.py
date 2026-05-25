"""Tab 1 — Load Data.

The entry point of the workflow. Recursively scans a DICOM folder, groups
files by ``FrameOfReferenceUID``, derives source labels via the cascade, and
displays the cohort overview, summary statistics, and any detected issues.
"""

from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from autoseg_evaluator.data.metadata import MetadataLibrary, ScanIssue
from autoseg_evaluator.ui.dialogs.source_labels import ManageSourceLabelsDialog
from autoseg_evaluator.ui.widgets.dicom_tree import CohortTreeWidget
from autoseg_evaluator.workers.scan_worker import ScanWorker


class LoadDataTab(QWidget):
    """Folder loader + cohort overview.

    Signals
    -------
    libraryLoaded(MetadataLibrary)
        Emitted whenever a folder finishes loading successfully.
    overridesChanged(dict)
        Emitted when the user changes custom source labels via the dialog.
    """

    libraryLoaded = Signal(object)
    overridesChanged = Signal(dict)

    def __init__(
        self,
        settings: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings: dict[str, Any] = settings if settings is not None else {}
        self._library: MetadataLibrary | None = None
        self._worker: ScanWorker | None = None
        self._thread: QThread | None = None

        self._build_ui()
        # Remember the last-used folder across sessions, but do not auto-load it —
        # the user clicks Load Folder to begin.
        self._update_path_display(self._settings.get("last_folder", "") or "")

    # ---- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        # Header: load button, current path, reload
        header = QHBoxLayout()
        self._load_btn = QPushButton("Load Folder…", self)
        self._load_btn.clicked.connect(self._on_load_clicked)
        header.addWidget(self._load_btn)

        self._path_label = QLabel("(no folder loaded)", self)
        self._path_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header.addWidget(self._path_label, stretch=1)

        self._reload_btn = QPushButton("Reload", self)
        self._reload_btn.setEnabled(False)
        self._reload_btn.clicked.connect(self._on_reload_clicked)
        header.addWidget(self._reload_btn)
        outer.addLayout(header)

        outer.addWidget(_h_divider())

        # Summary block
        self._summary_box = QGroupBox("Summary", self)
        self._summary_layout = QVBoxLayout(self._summary_box)
        self._summary_label = QLabel("Load a folder to see cohort statistics.", self)
        self._summary_label.setWordWrap(True)
        self._summary_layout.addWidget(self._summary_label)
        outer.addWidget(self._summary_box)

        # Cohort tree (left) + Issues panel (right) — side-by-side via a
        # horizontal splitter so the user can rebalance the panes. The
        # Manage Source Labels button stays in the cohort column header.
        body_split = QSplitter(Qt.Orientation.Horizontal, self)
        body_split.setChildrenCollapsible(False)

        cohort_pane = QWidget(body_split)
        cohort_layout = QVBoxLayout(cohort_pane)
        cohort_layout.setContentsMargins(0, 0, 0, 0)
        cohort_layout.setSpacing(4)
        tree_header = QHBoxLayout()
        tree_header.addWidget(QLabel("<b>Cohort</b>", cohort_pane))
        tree_header.addStretch(1)
        self._source_labels_btn = QPushButton("Manage Source Labels…", cohort_pane)
        self._source_labels_btn.setEnabled(False)
        self._source_labels_btn.clicked.connect(self._on_manage_sources_clicked)
        tree_header.addWidget(self._source_labels_btn)
        cohort_layout.addLayout(tree_header)
        self._tree = CohortTreeWidget(cohort_pane)
        cohort_layout.addWidget(self._tree, stretch=1)
        body_split.addWidget(cohort_pane)

        self._issues_box = QGroupBox("Issues", body_split)
        issues_layout = QVBoxLayout(self._issues_box)
        self._issues_list = QListWidget(self._issues_box)
        # Drop the fixed cap — the side-by-side layout means the list takes
        # whatever vertical space the splitter pane has, not a strip below
        # the cohort tree.
        issues_layout.addWidget(self._issues_list)
        body_split.addWidget(self._issues_box)

        # Default split: cohort gets ~2/3, issues gets ~1/3 of the width.
        body_split.setStretchFactor(0, 2)
        body_split.setStretchFactor(1, 1)
        body_split.setSizes([700, 350])
        outer.addWidget(body_split, stretch=2)

        # Progress bar (visible only during a scan)
        progress_row = QHBoxLayout()
        self._progress_bar = QProgressBar(self)
        self._progress_bar.setVisible(False)
        progress_row.addWidget(self._progress_bar, stretch=1)
        self._cancel_btn = QPushButton("Cancel", self)
        self._cancel_btn.setVisible(False)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        progress_row.addWidget(self._cancel_btn)
        outer.addLayout(progress_row)

    # ---- Public access ---------------------------------------------------

    def library(self) -> MetadataLibrary | None:
        return self._library

    def load_folder(self, folder_path: str) -> None:
        """Programmatic entry point — used by session restore in MainWindow."""
        if folder_path:
            self._start_scan(folder_path)

    # ---- Slots / handlers -------------------------------------------------

    def _on_load_clicked(self) -> None:
        start_dir = self._settings.get("last_folder", "") or ""
        folder = QFileDialog.getExistingDirectory(
            self, "Select a DICOM folder", start_dir or os.path.expanduser("~")
        )
        if folder:
            self._start_scan(folder)

    def _on_reload_clicked(self) -> None:
        if self._library is not None and self._library.root_folder:
            self._start_scan(self._library.root_folder)

    def _on_cancel_clicked(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
        self._cancel_btn.setEnabled(False)

    def _on_manage_sources_clicked(self) -> None:
        if self._library is None:
            return
        existing = dict(self._settings.get("custom_source_labels", {}))
        dialog = ManageSourceLabelsDialog(self._library, existing, parent=self)
        if dialog.exec() == ManageSourceLabelsDialog.DialogCode.Accepted:
            new_overrides = dialog.overrides()
            self._settings["custom_source_labels"] = new_overrides
            self.overridesChanged.emit(new_overrides)
            # Re-scan so the new overrides apply to the in-memory library
            if self._library.root_folder:
                self._start_scan(self._library.root_folder)

    # ---- Scan lifecycle ---------------------------------------------------

    def _start_scan(self, folder: str) -> None:
        # Tear down any previous scan first
        self._teardown_thread()

        self._update_path_display(folder)
        self._set_busy_ui(True)

        overrides = dict(self._settings.get("custom_source_labels", {}))
        self._worker = ScanWorker(folder, custom_overrides=overrides)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_scan_finished)
        self._worker.error.connect(self._on_scan_error)
        # Clean up the thread once it returns
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)

        self._thread.start()

    def _teardown_thread(self) -> None:
        if self._thread is not None:
            if self._thread.isRunning():
                if self._worker is not None:
                    self._worker.cancel()
                self._thread.quit()
                self._thread.wait(2000)
            self._thread.deleteLater()
            self._thread = None
        self._worker = None

    def _on_progress(self, current: int, total: int, _path: str) -> None:
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(current)
            self._progress_bar.setFormat(f"{current} / {total} files")

    def _on_scan_finished(self, library: MetadataLibrary) -> None:
        self._library = library
        self._settings["last_folder"] = library.root_folder
        self._populate_results(library)
        self._set_busy_ui(False)
        self.libraryLoaded.emit(library)

    def _on_scan_error(self, message: str) -> None:
        self._set_busy_ui(False)
        self._issues_list.clear()
        item = QListWidgetItem(f"Scan error: {message.splitlines()[0]}")
        item.setForeground(_palette_color(self, QPalette.ColorRole.LinkVisited))
        self._issues_list.addItem(item)

    # ---- Display population ----------------------------------------------

    def _populate_results(self, library: MetadataLibrary) -> None:
        self._tree.populate(library)
        self._render_summary(library)
        self._render_issues(library.issues)
        self._reload_btn.setEnabled(True)
        self._source_labels_btn.setEnabled(bool(library.all_rtstructs()))

    def _render_summary(self, library: MetadataLibrary) -> None:
        s = library.summary()
        sources = ", ".join(s["sources"]) if s["sources"] else "(none detected)"
        scanned = s["files_scanned"]
        failed = s["files_failed"]
        text = (
            f"<b>Patients:</b> {s['patients']}     "
            f"<b>Contexts:</b> {s['contexts']}     "
            f"<b>RTSS files:</b> {s['rtstructs']}     "
            f"<b>RTDOSE files:</b> {s['rtdoses']}<br>"
            f"<b>Total organs:</b> {s['total_organs']}     "
            f"<b>Unique organs:</b> {s['unique_organs']}<br>"
            f"<b>Sources detected:</b> {sources}<br>"
            f"<span style='color: #666'>Files scanned: {scanned}"
            f"{' · ' + str(failed) + ' skipped (non-DICOM or unreadable)' if failed else ''}</span>"
        )
        self._summary_label.setText(text)
        self._summary_label.setTextFormat(Qt.TextFormat.RichText)

    def _render_issues(self, issues: list[ScanIssue]) -> None:
        self._issues_list.clear()
        if not issues:
            ok_item = QListWidgetItem("No issues detected. ✓")
            self._issues_list.addItem(ok_item)
            return
        for issue in issues:
            prefix = "⚠ " if issue.severity == "warning" else "✗ "
            item = QListWidgetItem(prefix + issue.message)
            self._issues_list.addItem(item)

    # ---- Misc -------------------------------------------------------------

    def _set_busy_ui(self, busy: bool) -> None:
        self._load_btn.setEnabled(not busy)
        self._reload_btn.setEnabled(not busy and self._library is not None)
        self._source_labels_btn.setEnabled(not busy and bool(self._library and self._library.all_rtstructs()))
        self._progress_bar.setVisible(busy)
        self._cancel_btn.setVisible(busy)
        self._cancel_btn.setEnabled(busy)
        if busy:
            self._progress_bar.setRange(0, 0)  # indeterminate until first progress arrives
            self._progress_bar.setFormat("Scanning…")
            self._issues_list.clear()

    def _update_path_display(self, path: str) -> None:
        if path:
            self._path_label.setText(path)
        else:
            self._path_label.setText("(no folder loaded)")


# ---- Helpers --------------------------------------------------------------


def _h_divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


def _palette_color(widget: QWidget, role: QPalette.ColorRole):
    return widget.palette().color(role)
