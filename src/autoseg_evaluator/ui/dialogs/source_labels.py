"""Manage Source Labels dialog (accessed from Tab 1).

Auto-detection covers most RTSTRUCT files, but in-house models or anonymised
exports may need a manual label. This dialog presents every loaded RTSTRUCT
in a table where the user can type a custom label that overrides the cascade.
Custom labels persist in ``settings.json`` keyed by SOPInstanceUID and survive
across sessions and re-loads of the same data.
"""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
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

# Column indices — when bumping these, also update _populate_table,
# _on_reset_all, _on_bulk_apply, and _on_apply (they read/write the
# editable column by index).
_COL_FILE = 0
_COL_PATIENT = 1
_COL_DETECTED = 2
_COL_ORIGIN = 3
_COL_MANUFACTURER = 4
_COL_STRUCT_SET_LABEL = 5
_COL_SOFTWARE_VERSIONS = 6
_COL_STRUCT_SET_NAME = 7
_COL_STRUCT_SET_DESCRIPTION = 8
_COL_MANUFACTURER_MODEL = 9
_COL_REVIEWER_NAME = 10
_COL_OPERATORS_NAME = 11
_COL_PARENT_FOLDER = 12
_COL_CUSTOM = 13
_COL_HEADERS = [
    "File",
    "Patient",
    "Detected source",
    "Origin",
    "Manufacturer",
    "StructureSetLabel",
    "SoftwareVersions",
    "StructureSetName",
    "StructureSetDescription",
    "ManufacturerModelName",
    "ReviewerName",
    "OperatorsName",
    "Parent folder",
    "Custom label",
]

# Initial column widths (px). Applied on first open; the user can drag
# any column divider to adjust. Numbers picked to fit typical DICOM
# string lengths without horizontal scrolling on the 1400-px default
# dialog width.
_DEFAULT_COLUMN_WIDTHS: list[int] = [
    220,  # File
    80,  # Patient
    180,  # Detected source
    130,  # Origin
    130,  # Manufacturer
    150,  # StructureSetLabel
    130,  # SoftwareVersions
    130,  # StructureSetName
    180,  # StructureSetDescription
    170,  # ManufacturerModelName
    140,  # ReviewerName
    140,  # OperatorsName
    140,  # Parent folder
    180,  # Custom label
]

# Identity / interaction columns are always shown — hiding them would
# leave the dialog unusable. Every other column is toggleable via the
# header context menu.
_NON_HIDEABLE_COLUMNS: frozenset[int] = frozenset({_COL_FILE, _COL_PATIENT, _COL_CUSTOM})

# Field cascade for the "Select similar" assisted-propagation helper. When
# the user picks one row and clicks "Select similar", the first field below
# with a non-empty value on that row is used to find every other row sharing
# the same value. Ordered most-reliable-first for identifying which manual
# observer produced a file. ``_filename_stem`` is a synthetic attr handled
# specially in :func:`_field_value_of`.
_SIMILARITY_FIELDS: list[tuple[str, str]] = [
    ("Parent folder", "parent_folder"),
    ("OperatorsName", "operators_name"),
    ("ReviewerName", "reviewer_name"),
    ("StructureSetName", "structure_set_name"),
    ("Manufacturer", "manufacturer"),
    ("Filename", "_filename_stem"),
]


def _field_value_of(rtss: RTSTRUCTEntry, attr: str) -> str:
    """Return the (stripped) value of ``attr`` on an RTSS, '' if absent.

    ``_filename_stem`` is a synthetic attribute returning the filename with
    its extension dropped — the lowest-priority distinguishing token.
    """
    if attr == "_filename_stem":
        name = rtss.filename or ""
        return name.rsplit(".", 1)[0] if name else ""
    return (getattr(rtss, attr, "") or "").strip()


def best_distinguishing_field(rtss: RTSTRUCTEntry) -> tuple[str, str, str]:
    """Return ``(display_label, attr, value)`` for the first non-empty field.

    Walks :data:`_SIMILARITY_FIELDS` in priority order. Returns
    ``("", "", "")`` when the RTSS has no usable distinguishing metadata.
    Pure + Qt-free so it can be unit tested directly.
    """
    for label, attr in _SIMILARITY_FIELDS:
        value = _field_value_of(rtss, attr)
        if value:
            return label, attr, value
    return "", "", ""


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
        # Wider default since v2.3 adds 6 raw DICOM columns alongside the
        # detected source. Users can still resize or maximise the dialog.
        self.resize(1400, 560)

        self._library = library
        self._existing = dict(existing_overrides)
        self._result_overrides: dict[str, str] = dict(existing_overrides)
        # SOPInstanceUID → RTSTRUCTEntry, built during _populate_table so the
        # "Select similar" helper can read distinguishing fields per row.
        self._rtss_by_sop: dict[str, RTSTRUCTEntry] = {}

        self._build_ui()
        self._populate_table()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        info = QLabel(
            "Override the detected source label for any RTSTRUCT below. Custom "
            "labels persist across sessions and apply everywhere the source is "
            "shown (drawers, results CSV, etc.). Leave the field blank to keep "
            "the auto-detected value. "
            "<i>Tip: for multi-observer studies, give each observer a distinct "
            "label (e.g. 'Observer A'). Select one of their files and click "
            "<b>Select similar</b> to auto-select that observer's other files, "
            "then Apply. Right-click any column header to show or hide "
            "columns.</i>"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self._table = QTableWidget(self)
        self._table.setColumnCount(len(_COL_HEADERS))
        self._table.setHorizontalHeaderLabels(_COL_HEADERS)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # Explicit ExtendedSelection so bulk-apply and "Select similar" can
        # build a multi-row selection (Ctrl/Shift-click plus programmatic).
        self._table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
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
        # All columns are user-resizable. Sensible initial widths are
        # applied via _DEFAULT_COLUMN_WIDTHS; the user can drag any
        # divider, and the bottom-right corner of the dialog itself is
        # resizable for the full table width.
        for col in range(len(_COL_HEADERS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(col, _DEFAULT_COLUMN_WIDTHS[col])
        # Right-click the header to toggle column visibility — lets users
        # hide DICOM columns they don't need without losing the data
        # (they remain part of the model, just not drawn).
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_column_visibility_menu)
        self._table.itemSelectionChanged.connect(self._refresh_bulk_button)
        # Wider default so the new DICOM-field columns are visible without
        # the user having to resize on first open.
        layout.addWidget(self._table, stretch=1)

        # Bulk-apply row: select rows in the table, type a label, click apply.
        # Lets the user retag a whole vendor's worth of RTSSes in one step.
        bulk_row = QHBoxLayout()
        # "Select similar" feeds this same flow by auto-populating the
        # selection from a distinguishing field on the one selected row.
        self._select_similar_btn = QPushButton("Select similar…", self)
        self._select_similar_btn.setToolTip(
            "Select exactly one row, then click to auto-select every other file "
            "that shares its strongest distinguishing field (parent folder, "
            "OperatorsName, ReviewerName, StructureSetName, Manufacturer, or "
            "filename). Useful for tagging one observer's files across patients."
        )
        self._select_similar_btn.setEnabled(False)
        self._select_similar_btn.clicked.connect(self._on_select_similar)
        bulk_row.addWidget(self._select_similar_btn)
        bulk_row.addSpacing(12)
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

            self._set_readonly_cell(row, _COL_FILE, rtss.filename)
            self._set_readonly_cell(row, _COL_PATIENT, patient_id)
            self._set_readonly_cell(row, _COL_DETECTED, detected_label)
            self._set_readonly_cell(
                row,
                _COL_ORIGIN,
                _ORIGIN_DESCRIPTIONS.get(detected_origin, detected_origin),
            )
            # Raw DICOM fields — surfaced so the user can disambiguate two
            # RTSSes whose detected source labels happen to collide (e.g.
            # two model versions from the same vendor).
            self._set_readonly_cell(row, _COL_MANUFACTURER, rtss.manufacturer or "")
            self._set_readonly_cell(row, _COL_STRUCT_SET_LABEL, rtss.structure_set_label or "")
            self._set_readonly_cell(row, _COL_SOFTWARE_VERSIONS, rtss.software_versions or "")
            self._set_readonly_cell(row, _COL_STRUCT_SET_NAME, rtss.structure_set_name or "")
            self._set_readonly_cell(
                row, _COL_STRUCT_SET_DESCRIPTION, rtss.structure_set_description or ""
            )
            self._set_readonly_cell(
                row, _COL_MANUFACTURER_MODEL, rtss.manufacturer_model_name or ""
            )
            # v2.4 disambiguation columns — help identify which manual
            # observer produced a file when several share a TPS / vendor.
            self._set_readonly_cell(row, _COL_REVIEWER_NAME, rtss.reviewer_name or "")
            self._set_readonly_cell(row, _COL_OPERATORS_NAME, rtss.operators_name or "")
            self._set_readonly_cell(row, _COL_PARENT_FOLDER, rtss.parent_folder or "")

            custom = self._existing.get(rtss.sop_instance_uid, "")
            edit_item = QTableWidgetItem(custom)
            edit_item.setData(Qt.ItemDataRole.UserRole, rtss.sop_instance_uid)
            self._table.setItem(row, _COL_CUSTOM, edit_item)
            self._rtss_by_sop[rtss.sop_instance_uid] = rtss

        self._table.setSortingEnabled(was_sorted)

    def _set_readonly_cell(self, row: int, col: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(row, col, item)

    def _show_column_visibility_menu(self, pos: QPoint) -> None:
        """Header right-click → checkable list of column visibility toggles.

        File / Patient / Custom label are pinned visible (hiding them
        leaves the dialog unusable); every other column can be toggled.
        A 'Show all' action restores everything.
        """
        menu = QMenu(self)
        for col, header_text in enumerate(_COL_HEADERS):
            if col in _NON_HIDEABLE_COLUMNS:
                continue
            action = QAction(header_text, menu)
            action.setCheckable(True)
            action.setChecked(not self._table.isColumnHidden(col))
            # Default-arg trick to bind ``col`` at iteration time rather
            # than capturing the loop variable by reference.
            action.toggled.connect(
                lambda checked, c=col: self._table.setColumnHidden(c, not checked)
            )
            menu.addAction(action)
        menu.addSeparator()
        show_all = QAction("Show all columns", menu)
        show_all.triggered.connect(self._show_all_columns)
        menu.addAction(show_all)
        menu.exec(self._table.horizontalHeader().mapToGlobal(pos))

    def _show_all_columns(self) -> None:
        for col in range(self._table.columnCount()):
            self._table.setColumnHidden(col, False)

    def _on_reset_all(self) -> None:
        for row in range(self._table.rowCount()):
            edit_item = self._table.item(row, _COL_CUSTOM)
            if edit_item is not None:
                edit_item.setText("")

    def _selected_data_rows(self) -> list[int]:
        """Return the unique row indices touched by the current selection."""
        return sorted({idx.row() for idx in self._table.selectionModel().selectedIndexes()})

    def _refresh_bulk_button(self) -> None:
        n = len(self._selected_data_rows())
        self._bulk_btn.setText(f"Apply to selected ({n})")
        self._bulk_btn.setEnabled(n > 0)
        # "Select similar" only makes sense from a single anchor row.
        self._select_similar_btn.setEnabled(n == 1)

    # ---- Assisted propagation ("Select similar") -------------------------

    def _rtss_for_row(self, row: int) -> RTSTRUCTEntry | None:
        item = self._table.item(row, _COL_CUSTOM)
        if item is None:
            return None
        sop = item.data(Qt.ItemDataRole.UserRole)
        return self._rtss_by_sop.get(sop) if sop else None

    def _select_rows(self, rows: list[int]) -> None:
        """Replace the current selection with exactly ``rows`` (full-row)."""
        model = self._table.model()
        last_col = self._table.columnCount() - 1
        selection = QItemSelection()
        for r in rows:
            selection.merge(
                QItemSelection(model.index(r, 0), model.index(r, last_col)),
                QItemSelectionModel.SelectionFlag.Select,
            )
        sel_model = self._table.selectionModel()
        sel_model.clearSelection()
        sel_model.select(selection, QItemSelectionModel.SelectionFlag.Select)

    def _rows_matching(self, attr: str, value: str) -> list[int]:
        """Row indices whose RTSS has ``attr == value`` (the synthetic
        ``_filename_stem`` attr is handled by :func:`_field_value_of`)."""
        return [
            r
            for r in range(self._table.rowCount())
            if (rt := self._rtss_for_row(r)) is not None and _field_value_of(rt, attr) == value
        ]

    def _on_select_similar(self) -> None:
        """Show a menu of distinguishing fields → select rows matching the
        anchor on the chosen field.

        Rather than silently picking one field (parent_folder can be the
        *patient* directory, not the observer), we present every non-empty
        field with its value + how many files share it, so the user chooses
        the one that identifies the observer. The cascade-recommended field
        is marked with ★.
        """
        rows = self._selected_data_rows()
        if len(rows) != 1:
            QMessageBox.information(
                self,
                "Select similar",
                "Select exactly one row first, then click 'Select similar'.",
            )
            return
        anchor = self._rtss_for_row(rows[0])
        if anchor is None:
            return
        _rec_label, rec_attr, _rec_value = best_distinguishing_field(anchor)

        menu = QMenu(self)
        added = False
        for label, attr in _SIMILARITY_FIELDS:
            value = _field_value_of(anchor, attr)
            if not value:
                continue
            matching = self._rows_matching(attr, value)
            text = f"{label} = '{value}'   ({len(matching)} file(s))"
            if attr == rec_attr:
                text += "  ★"
            action = QAction(text, menu)
            # Disable fields that only match the anchor itself.
            action.setEnabled(len(matching) > 1)
            action.triggered.connect(lambda _checked=False, rs=matching: self._select_rows(rs))
            menu.addAction(action)
            added = True

        if not added:
            QMessageBox.information(
                self,
                "Select similar",
                "The selected file has no distinguishing metadata to match on "
                "(no folder, OperatorsName, ReviewerName, StructureSetName, "
                "Manufacturer, or filename).",
            )
            return

        btn = self._select_similar_btn
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _on_bulk_apply(self) -> None:
        rows = self._selected_data_rows()
        if not rows:
            return
        value = (self._bulk_edit.text() or "").strip()
        for row in rows:
            edit_item = self._table.item(row, _COL_CUSTOM)
            if edit_item is not None:
                edit_item.setText(value)
        self._bulk_edit.clear()

    def _on_apply(self) -> None:
        overrides: dict[str, str] = {}
        for row in range(self._table.rowCount()):
            edit_item = self._table.item(row, _COL_CUSTOM)
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
