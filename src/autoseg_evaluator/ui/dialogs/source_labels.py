"""Manage Source Labels dialog (accessed from Tab 1).

Auto-detection covers most RTSTRUCT files, but in-house models or anonymised
exports may need a manual label. This dialog presents every loaded RTSTRUCT
in a table where the user can type a custom label that overrides the cascade.
Custom labels persist in ``settings.json`` keyed by SOPInstanceUID and survive
across sessions and re-loads of the same data.
"""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from autoseg_evaluator.data.metadata import MetadataLibrary, RTSTRUCTEntry

_ORIGIN_DESCRIPTIONS = {
    "mfr": "Manufacturer tag",
    "label": "StructureSetLabel",
    "software": "SoftwareVersions",
    "name": "StructureSetName",
    "filename": "filename",
    "folder": "folder name",
    "custom": "user-defined",
    "unknown": "no source",
}


class ManageSourceLabelsDialog(QDialog):
    """Edit the SOPInstanceUID → custom source-label mapping.

    On accept, :meth:`overrides` returns the new mapping (empty entries removed).
    """

    def __init__(
        self,
        library: MetadataLibrary,
        existing_overrides: Mapping[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage Source Labels")
        self.resize(900, 500)

        self._library = library
        self._existing = dict(existing_overrides)
        self._result_overrides: dict[str, str] = dict(existing_overrides)

        self._build_ui()
        self._populate_table()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        info = QLabel(
            "Override the detected source label for any RTSTRUCT below. Custom "
            "labels persist across sessions and apply everywhere the source is "
            "shown (drawers, results CSV, etc.). Leave the field blank to keep "
            "the auto-detected value."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self._table = QTableWidget(self)
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            ["File", "Patient", "Detected source", "Origin", "Custom label"]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.SelectedClicked
            | QTableWidget.EditTrigger.EditKeyPressed
        )
        # Click-to-sort on column headers — useful for bulk-selecting all
        # rows from one vendor / one origin / one patient before applying
        # an override. Each cell carries its SOPInstanceUID in UserRole,
        # so the binding survives row reordering.
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().setSortIndicatorShown(True)
        self._table.horizontalHeader().setSectionsClickable(True)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.itemSelectionChanged.connect(self._refresh_bulk_button)
        layout.addWidget(self._table, stretch=1)

        # Bulk-apply row: select rows in the table, type a label, click apply.
        # Lets the user retag a whole vendor's worth of RTSSes in one step.
        bulk_row = QHBoxLayout()
        bulk_row.addWidget(QLabel("Bulk apply:", self))
        self._bulk_edit = QLineEdit(self)
        self._bulk_edit.setPlaceholderText(
            "New label for selected rows (leave empty to clear overrides)…"
        )
        self._bulk_edit.returnPressed.connect(self._on_bulk_apply)
        bulk_row.addWidget(self._bulk_edit, stretch=1)
        self._bulk_btn = QPushButton("Apply to selected (0)", self)
        self._bulk_btn.setEnabled(False)
        self._bulk_btn.clicked.connect(self._on_bulk_apply)
        bulk_row.addWidget(self._bulk_btn)
        layout.addLayout(bulk_row)

        reset_btn = QPushButton("Reset all overrides", self)
        reset_btn.clicked.connect(self._on_reset_all)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        apply_button.setText("Apply")
        apply_button.clicked.connect(self._on_apply)
        buttons.rejected.connect(self.reject)

        bottom = QHBoxLayout()
        bottom.addWidget(reset_btn)
        bottom.addStretch(1)
        bottom.addWidget(buttons)
        layout.addLayout(bottom)

    def _populate_table(self) -> None:
        rtstructs: list[tuple[str, RTSTRUCTEntry]] = []
        for patient_id, patient in self._library.patients.items():
            for ctx in patient.contexts:
                for rtss in ctx.rtstructs:
                    rtstructs.append((patient_id, rtss))
        # Disable sorting during bulk insert so each setItem doesn't trigger
        # a re-sort. Re-enable at the end.
        was_sorted = self._table.isSortingEnabled()
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rtstructs))

        for row, (patient_id, rtss) in enumerate(rtstructs):
            # The "detected" label is whatever the cascade would produce without
            # the user's override — we show this as a baseline so the user knows
            # what they're replacing.
            detected_label, detected_origin = _detected_for(rtss)

            self._set_readonly_cell(row, 0, rtss.filename)
            self._set_readonly_cell(row, 1, patient_id)
            self._set_readonly_cell(row, 2, detected_label)
            self._set_readonly_cell(
                row, 3, _ORIGIN_DESCRIPTIONS.get(detected_origin, detected_origin)
            )

            custom = self._existing.get(rtss.sop_instance_uid, "")
            edit_item = QTableWidgetItem(custom)
            edit_item.setData(Qt.ItemDataRole.UserRole, rtss.sop_instance_uid)
            self._table.setItem(row, 4, edit_item)

        self._table.setSortingEnabled(was_sorted)

    def _set_readonly_cell(self, row: int, col: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(row, col, item)

    def _on_reset_all(self) -> None:
        for row in range(self._table.rowCount()):
            edit_item = self._table.item(row, 4)
            if edit_item is not None:
                edit_item.setText("")

    def _selected_data_rows(self) -> list[int]:
        """Return the unique row indices touched by the current selection."""
        return sorted({idx.row() for idx in self._table.selectionModel().selectedIndexes()})

    def _refresh_bulk_button(self) -> None:
        n = len(self._selected_data_rows())
        self._bulk_btn.setText(f"Apply to selected ({n})")
        self._bulk_btn.setEnabled(n > 0)

    def _on_bulk_apply(self) -> None:
        rows = self._selected_data_rows()
        if not rows:
            return
        value = (self._bulk_edit.text() or "").strip()
        for row in rows:
            edit_item = self._table.item(row, 4)
            if edit_item is not None:
                edit_item.setText(value)
        self._bulk_edit.clear()

    def _on_apply(self) -> None:
        overrides: dict[str, str] = {}
        for row in range(self._table.rowCount()):
            edit_item = self._table.item(row, 4)
            if edit_item is None:
                continue
            sop_uid = edit_item.data(Qt.ItemDataRole.UserRole)
            value = (edit_item.text() or "").strip()
            if sop_uid and value:
                overrides[sop_uid] = value
        self._result_overrides = overrides
        self.accept()

    def overrides(self) -> dict[str, str]:
        """The final override mapping (only present if accepted)."""
        return dict(self._result_overrides)


def _detected_for(rtss: RTSTRUCTEntry) -> tuple[str, str]:
    """Best effort at reconstructing the cascade origin from the stored entry.

    We don't re-read the DICOM file here — instead, we use the data the scanner
    captured (Manufacturer, source_label, source_origin). If ``source_origin``
    is ``"custom"`` the caller is asking what the *raw* detection was, so we
    walk the cascade manually using the stored Manufacturer field only.
    """
    # The scanner stored both the raw Manufacturer and the cascade-derived label.
    # If the stored origin is "custom" we lost the raw detection — re-derive
    # what we can from Manufacturer (the most likely raw source).
    if rtss.source_origin == "custom":
        if rtss.manufacturer:
            return rtss.manufacturer, "mfr"
        return rtss.filename.rsplit(".", 1)[0] or rtss.filename, "filename"
    return rtss.source_label, rtss.source_origin
