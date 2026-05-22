"""Tab 4 — Results.

Displays the rows accumulated by :class:`ResultsManager` in a sortable
table whose columns are: fixed metadata (drawer, patient, GT/test source
and RTSS, truncated, name-similarity, error), followed by every metric
key that appears in any row. Rows that carry an error message are
highlighted so they're easy to spot.

The table is rebuilt on every new row by default — for typical cohorts
(<2000 rows) this is plenty fast and keeps the column set consistent
when new metrics (e.g. DVH points) appear mid-run.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from autoseg_evaluator.data.results import META_COLUMNS, ResultsManager, metric_display_label


_ERROR_BG = QColor("#FFE0E0")


class ResultsTab(QWidget):
    """Results table view + CSV export controls."""

    cleared = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._results_mgr: ResultsManager | None = None
        self._build_ui()

    # ---- Public API -------------------------------------------------------

    def set_results_manager(self, manager: ResultsManager) -> None:
        self._results_mgr = manager
        self.refresh()

    def append_row(self, row: dict[str, Any]) -> None:
        """Called for each row emitted by the metrics worker — refreshes the view."""
        # Row is already appended to the ResultsManager by MainWindow; just refresh.
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the table from the current ResultsManager state."""
        if self._results_mgr is None:
            self._table.setRowCount(0)
            self._table.setColumnCount(0)
            self._row_count_label.setText("0 rows")
            self._export_btn.setEnabled(False)
            self._clear_btn.setEnabled(False)
            return

        rows = self._results_mgr.rows()
        metric_cols = self._results_mgr.metric_columns()
        meta_keys = [k for k, _ in META_COLUMNS]
        meta_labels = [label for _, label in META_COLUMNS]
        headers = meta_labels + [metric_display_label(k) for k in metric_cols]

        # Re-build with sorting disabled to keep insertion order stable
        was_sorted = self._table.isSortingEnabled()
        self._table.setSortingEnabled(False)
        self._table.clear()
        self._table.setColumnCount(len(headers))
        self._table.setRowCount(len(rows))
        self._table.setHorizontalHeaderLabels(headers)

        for r, row in enumerate(rows):
            is_error = bool(row.get("error"))
            # Meta cells
            for c, key in enumerate(meta_keys):
                value = row.get(key, "")
                self._table.setItem(r, c, _make_item(value, error_bg=is_error))
            # Metric cells
            m = row.get("metrics") or {}
            for c_off, key in enumerate(metric_cols):
                value = m.get(key, "")
                self._table.setItem(
                    r, len(meta_keys) + c_off, _make_item(value, error_bg=is_error)
                )

        self._autosize_columns()
        if was_sorted:
            self._table.setSortingEnabled(True)

        n = len(rows)
        n_err = sum(1 for r in rows if r.get("error"))
        suffix = f"  ({n_err} with errors)" if n_err else ""
        self._row_count_label.setText(f"{n} row{'s' if n != 1 else ''}{suffix}")
        self._export_btn.setEnabled(n > 0)
        self._clear_btn.setEnabled(n > 0)

    # ---- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("<b>Results</b>"))
        toolbar.addStretch(1)
        self._row_count_label = QLabel("0 rows", self)
        self._row_count_label.setStyleSheet("color: #666;")
        toolbar.addWidget(self._row_count_label)
        self._clear_btn = QPushButton("Clear", self)
        self._clear_btn.setEnabled(False)
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        toolbar.addWidget(self._clear_btn)
        self._export_btn = QPushButton("Export CSV…", self)
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._on_export_clicked)
        toolbar.addWidget(self._export_btn)
        outer.addLayout(toolbar)

        self._table = _ResultsTable(self)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        # SelectItems (not SelectRows) so users can copy a sub-rectangle if they want.
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(False)
        # Dense font matches the tree/drawer density elsewhere
        font = self._table.font()
        font.setPointSize(max(8, font.pointSize() - 1))
        self._table.setFont(font)
        outer.addWidget(self._table, stretch=1)

    # ---- Slots ------------------------------------------------------------

    def _on_clear_clicked(self) -> None:
        if self._results_mgr is None or len(self._results_mgr) == 0:
            return
        reply = QMessageBox.question(
            self,
            "Clear results",
            f"Discard all {len(self._results_mgr)} stored result row(s)? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._results_mgr.clear()
        self.refresh()
        self.cleared.emit()

    def _autosize_columns(self) -> None:
        """Fit each column to the larger of its header text and its cell contents,
        then cap at 240 px so long file paths don't blow out the layout."""
        header = self._table.horizontalHeader()
        # ResizeToContents uses max(header sizeHint, cells sizeHint).
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        # Snapshot the auto-sized widths
        widths = [self._table.columnWidth(c) for c in range(self._table.columnCount())]
        # Switch back to Interactive so the user can drag columns wider later.
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for c, w in enumerate(widths):
            # Add a little breathing room; cap at 240 px.
            self._table.setColumnWidth(c, min(240, w + 6))

    def _on_export_clicked(self) -> None:
        if self._results_mgr is None or len(self._results_mgr) == 0:
            return
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Export results to CSV",
            "results.csv",
            "CSV files (*.csv);;All files (*)",
        )
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix.lower() != ".csv":
            path = path.with_suffix(".csv")
        try:
            n = self._results_mgr.export_csv(path)
        except OSError as exc:
            QMessageBox.critical(self, "Export CSV", f"Could not write file:\n{exc}")
            return
        QMessageBox.information(
            self,
            "Export CSV",
            f"Exported {n} row(s) to {path}.",
        )


class _ResultsTable(QTableWidget):
    """QTableWidget subclass with Excel-friendly copy semantics.

    Ctrl+C copies the current selection as **tab-separated** text, ready
    for ``Paste`` into Excel/Sheets. When the user has selected the entire
    table (Ctrl+A or click the top-left corner), the column headers are
    included as the first line so the paste target lands a complete table.
    """

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.matches(QKeySequence.StandardKey.Copy):
            self._copy_selection_to_clipboard()
            event.accept()
            return
        super().keyPressEvent(event)

    def _copy_selection_to_clipboard(self) -> None:
        ranges = self.selectedRanges()
        if not ranges:
            return
        top = min(r.topRow() for r in ranges)
        bottom = max(r.bottomRow() for r in ranges)
        left = min(r.leftColumn() for r in ranges)
        right = max(r.rightColumn() for r in ranges)

        # If the entire table is in the selection rectangle, prepend the headers.
        all_rows = top == 0 and bottom == self.rowCount() - 1
        all_cols = left == 0 and right == self.columnCount() - 1
        include_headers = all_rows and all_cols

        lines: list[str] = []
        if include_headers:
            headers = []
            for c in range(left, right + 1):
                item = self.horizontalHeaderItem(c)
                headers.append(item.text() if item is not None else "")
            lines.append("\t".join(headers))
        for r in range(top, bottom + 1):
            cells = []
            for c in range(left, right + 1):
                item = self.item(r, c)
                cells.append(item.text() if item is not None else "")
            lines.append("\t".join(cells))
        QGuiApplication.clipboard().setText("\n".join(lines))


def _make_item(value: Any, *, error_bg: bool = False) -> QTableWidgetItem:
    """Build a QTableWidgetItem with sort-aware numeric data and optional error tint."""
    item = QTableWidgetItem()
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if value is None or value == "":
        display = ""
        sort_key = ""
    elif isinstance(value, bool):
        display = "True" if value else "False"
        sort_key = 1 if value else 0
    elif isinstance(value, float):
        if math.isnan(value):
            display = ""
            sort_key = float("-inf")
        else:
            display = f"{value:.4g}"
            sort_key = float(value)
    elif isinstance(value, int):
        display = str(value)
        sort_key = int(value)
    else:
        display = str(value)
        sort_key = str(value).lower()
    item.setData(Qt.ItemDataRole.DisplayRole, display)
    item.setData(Qt.ItemDataRole.UserRole, sort_key)
    if error_bg:
        item.setBackground(QBrush(_ERROR_BG))
    return item
