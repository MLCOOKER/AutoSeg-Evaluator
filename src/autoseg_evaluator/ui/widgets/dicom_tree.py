"""Cohort overview tree for Tab 1 (Load Data).

Renders a modernised view of a populated :class:`MetadataLibrary`:

    Patient                       N RTSS · M organs
      (Imaging context — date)    (only shown when >1 per patient)
        CT series                 [CT]    N slices
        RTSTRUCT filename         [Source]  N organs
        RTDOSE filename           [PLAN]

DICOM ``StudyInstanceUID`` is intentionally hidden — it adds noise without
clinical meaning. Multi-context patients are surfaced with human-readable
study dates instead.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from autoseg_evaluator.data.metadata import (
    ImagingContext,
    MetadataLibrary,
    PatientEntry,
    RTDOSEEntry,
    RTSTRUCTEntry,
    ImageSeriesEntry,
)


# Visual prefixes — kept as plain text glyphs so they render even without an icon font.
_PATIENT_GLYPH = "👤"
_CONTEXT_GLYPH = "🗂"
_IMAGE_GLYPH = "🖼"
_RTSS_GLYPH = "📄"
_DOSE_GLYPH = "⚡"

_COLUMN_ITEM = 0
_COLUMN_SOURCE = 1
_COLUMN_COUNT = 2


class CohortTreeWidget(QTreeWidget):
    """Read-only overview tree. For the multi-select selection tree on Tab 2, see step 3."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderLabels(["Item", "Source / Modality", "Count"])
        self.setRootIsDecorated(True)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        self.setIndentation(10)
        self.setColumnWidth(_COLUMN_ITEM, 360)
        self.setColumnWidth(_COLUMN_SOURCE, 200)
        self.setColumnWidth(_COLUMN_COUNT, 100)
        header = self.header()
        if header is not None:
            header.setStretchLastSection(False)

        # Dense layout to fit large cohorts on one screen.
        font = self.font()
        font.setPointSize(max(8, font.pointSize() - 2))
        self.setFont(font)
        self.setStyleSheet(
            "QTreeWidget::item { padding: 0px 1px; }"
            "QTreeWidget::branch { padding: 0px; }"
        )

    # ---- Public API -------------------------------------------------------

    def populate(self, library: MetadataLibrary) -> None:
        self.clear()
        for patient_id in sorted(library.patients.keys()):
            patient = library.patients[patient_id]
            self.addTopLevelItem(self._make_patient_item(patient))
        # Leave everything collapsed on load — user expands what they need.

    # ---- Item construction ------------------------------------------------

    def _make_patient_item(self, patient: PatientEntry) -> QTreeWidgetItem:
        n_rtss = patient.total_rtstructs()
        n_organs = patient.total_organs()
        title = f"{_PATIENT_GLYPH}  {patient.patient_id}"
        if patient.merged_aliases:
            aliases = ", ".join(sorted(patient.merged_aliases))
            title += f"  (merged: {aliases})"
        item = QTreeWidgetItem(
            [
                title,
                "",
                _format_count(n_rtss, "RTSS") + " · " + _format_count(n_organs, "organs"),
            ]
        )
        item.setData(_COLUMN_ITEM, Qt.ItemDataRole.UserRole, ("patient", patient.patient_id))
        _bold(item, _COLUMN_ITEM)

        multi_context = len(patient.contexts) > 1
        for ctx in patient.contexts:
            if multi_context:
                ctx_item = self._make_context_item(ctx)
                item.addChild(ctx_item)
                self._attach_context_children(ctx_item, ctx)
            else:
                # Single context — skip the context layer entirely
                self._attach_context_children(item, ctx)
        return item

    def _make_context_item(self, ctx: ImagingContext) -> QTreeWidgetItem:
        date = ctx.display_date()
        title = f"{_CONTEXT_GLYPH}  Context  {date}".rstrip()
        item = QTreeWidgetItem([title, "", _format_count(ctx.organ_count(), "organs")])
        item.setData(
            _COLUMN_ITEM, Qt.ItemDataRole.UserRole, ("context", ctx.frame_of_reference_uid)
        )
        return item

    def _attach_context_children(
        self, parent_item: QTreeWidgetItem, ctx: ImagingContext
    ) -> None:
        for series in ctx.image_series:
            parent_item.addChild(self._make_image_series_item(series))
        for rtss in ctx.rtstructs:
            parent_item.addChild(self._make_rtstruct_item(rtss))
        for dose in ctx.rtdoses:
            parent_item.addChild(self._make_rtdose_item(dose))

    @staticmethod
    def _make_image_series_item(series: ImageSeriesEntry) -> QTreeWidgetItem:
        item = QTreeWidgetItem(
            [
                f"{_IMAGE_GLYPH}  {series.modality} series",
                f"[{series.modality}]",
                f"{len(series.files)} slices",
            ]
        )
        item.setData(
            _COLUMN_ITEM, Qt.ItemDataRole.UserRole, ("image_series", series.series_instance_uid)
        )
        return item

    @staticmethod
    def _make_rtstruct_item(rtss: RTSTRUCTEntry) -> QTreeWidgetItem:
        item = QTreeWidgetItem(
            [
                f"{_RTSS_GLYPH}  {rtss.filename}",
                f"[{rtss.source_label}]",
                _format_count(len(rtss.organs), "organs"),
            ]
        )
        item.setData(_COLUMN_ITEM, Qt.ItemDataRole.UserRole, ("rtstruct", rtss.sop_instance_uid))
        if rtss.source_origin != "mfr" and rtss.source_origin != "custom":
            # Faint hint that the source label is inferred, not from the official tag
            item.setForeground(_COLUMN_SOURCE, QBrush(QColor("#666")))
        return item

    @staticmethod
    def _make_rtdose_item(dose: RTDOSEEntry) -> QTreeWidgetItem:
        dose_type = dose.dose_summation_type or "DOSE"
        item = QTreeWidgetItem(
            [
                f"{_DOSE_GLYPH}  {dose.filename}",
                f"[{dose_type}]",
                "",
            ]
        )
        item.setData(_COLUMN_ITEM, Qt.ItemDataRole.UserRole, ("rtdose", dose.sop_instance_uid))
        return item


# ---- Small formatting helpers --------------------------------------------


def _format_count(n: int, suffix: str) -> str:
    return f"{n} {suffix}" if n != 1 else f"1 {suffix.rstrip('s')}"


def _bold(item: QTreeWidgetItem, column: int) -> None:
    font = item.font(column)
    font.setWeight(QFont.Weight.DemiBold)
    item.setFont(column, font)
