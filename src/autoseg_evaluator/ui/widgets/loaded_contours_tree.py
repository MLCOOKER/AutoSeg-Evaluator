"""Selection tree for the left side of Tab 2 (Match Contours).

Same modernised look as :class:`CohortTreeWidget` (icons, source badges,
counts) but drills down to the organ level so the user can multi-select
contours for GT assignment or test addition. Drag-and-drop source support
is wired in step 4.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget, QTreeWidgetItem

from autoseg_evaluator.data.metadata import (
    ImagingContext,
    MetadataLibrary,
    PatientEntry,
    RTSTRUCTEntry,
)
from autoseg_evaluator.ui.widgets.organ_drawer import ORGAN_MIME_TYPE


_PATIENT_GLYPH = "👤"
_CONTEXT_GLYPH = "🗂"
_RTSS_GLYPH = "📄"
_CONSTITUENT_SUFFIX = "  ▸ in STAPLE consensus"  # marker for files that fed a consensus


# ItemDataRole keys we tuck onto QTreeWidgetItem ----
# Each leaf carries enough information for downstream code to look up the
# corresponding RTSTRUCT + organ inside the MetadataLibrary.
ROLE_NODE_KIND = Qt.ItemDataRole.UserRole          # "patient" | "context" | "rtstruct" | "organ"
ROLE_PATIENT_ID = Qt.ItemDataRole.UserRole + 1
ROLE_FOR_UID = Qt.ItemDataRole.UserRole + 2
ROLE_RTSS_SOP_UID = Qt.ItemDataRole.UserRole + 3
ROLE_ROI_NUMBER = Qt.ItemDataRole.UserRole + 4
ROLE_ROI_NAME = Qt.ItemDataRole.UserRole + 5


class LoadedContoursTree(QTreeWidget):
    """Patient → (Context →) RTSTRUCT → Organ hierarchy with multi-select."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderLabels(["Item", "Source / Modality"])
        self.setRootIsDecorated(True)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        self.setIndentation(10)
        self.setColumnWidth(0, 360)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # Drag-source plumbing arrives in step 4; we set the flags here so the
        # tree is dragMOVE/dragCOPY-aware by default.
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)

        # Dense layout so large cohorts fit on one screen.
        font = self.font()
        font.setPointSize(max(8, font.pointSize() - 2))
        self.setFont(font)
        self.setStyleSheet(
            "QTreeWidget::item { padding: 0px 1px; }"
            "QTreeWidget::branch { padding: 0px; }"
        )

        # Track which organ items are already assigned somewhere (so we can ✓-mark them)
        self._marked_organs: set[tuple[str, str, int]] = set()  # (patient_id, sop_uid, roi_number)
        # SOP UIDs of RTSSes that contributed to a synthetic STAPLE consensus
        # — these are flagged with a visual marker so the user can see at a
        # glance which files fed which consensus, without losing the ability
        # to use them individually as GT or test contours.
        self._constituent_uids: set[str] = set()

    # ---- Public API -------------------------------------------------------

    def populate(self, library: MetadataLibrary) -> None:
        self.clear()
        # Collect every SOPInstanceUID that's a constituent of some synthetic
        # STAPLE consensus, so the per-RTSS rendering can flag them. Stored
        # per-populate so it stays in sync with whatever Build Consensus GT
        # generated most recently.
        self._constituent_uids = set()
        for _pid, _for, syn in library.synthetic_consensus_entries():
            for members in syn.constituent_groups.values():
                for sop, _roi in members:
                    self._constituent_uids.add(sop)
        for patient_id in sorted(library.patients):
            self.addTopLevelItem(self._make_patient_item(library.patients[patient_id]))
        # Leave everything collapsed on load — user expands what they need.

    def mark_organ_assigned(self, patient_id: str, sop_uid: str, roi_number: int) -> None:
        self._marked_organs.add((patient_id, sop_uid, roi_number))
        self._refresh_marks()

    def unmark_organ(self, patient_id: str, sop_uid: str, roi_number: int) -> None:
        self._marked_organs.discard((patient_id, sop_uid, roi_number))
        self._refresh_marks()

    def set_marks(self, marks) -> None:  # marks: Iterable[tuple[str, str, int]]
        """Authoritatively replace the entire mark set.

        Callers can compute the full set of currently-assigned organs (from the
        drawers) and pass it here in one go — far less error-prone than
        threading incremental ``mark`` / ``unmark`` calls through every UI
        operation, where a missed call leaves a stale tick behind.
        """
        new_marks = set(marks)
        if new_marks == self._marked_organs:
            return
        self._marked_organs = new_marks
        self._refresh_marks()

    def clear_marks(self) -> None:
        self._marked_organs.clear()
        self._refresh_marks()

    def selected_organs(self) -> list[tuple[str, str, int, str]]:
        """Return all currently-selected organ leaves as
        ``(patient_id, rtstruct_sop_uid, roi_number, roi_name)`` tuples."""
        out: list[tuple[str, str, int, str]] = []
        for item in self.selectedItems():
            if item.data(0, ROLE_NODE_KIND) != "organ":
                continue
            out.append(
                (
                    str(item.data(0, ROLE_PATIENT_ID) or ""),
                    str(item.data(0, ROLE_RTSS_SOP_UID) or ""),
                    int(item.data(0, ROLE_ROI_NUMBER) or 0),
                    str(item.data(0, ROLE_ROI_NAME) or ""),
                )
            )
        return out

    # ---- Drag-source payload ---------------------------------------------

    def mimeData(self, items):
        organs = []
        for item in items:
            if item.data(0, ROLE_NODE_KIND) != "organ":
                continue
            organs.append(
                {
                    "patient_id": str(item.data(0, ROLE_PATIENT_ID) or ""),
                    "rtstruct_sop_uid": str(item.data(0, ROLE_RTSS_SOP_UID) or ""),
                    "roi_number": int(item.data(0, ROLE_ROI_NUMBER) or 0),
                    "roi_name": str(item.data(0, ROLE_ROI_NAME) or ""),
                }
            )
        if not organs:
            return None
        mime = QMimeData()
        mime.setData(ORGAN_MIME_TYPE, json.dumps(organs).encode("utf-8"))
        return mime

    # ---- Construction ----------------------------------------------------

    def _make_patient_item(self, patient: PatientEntry) -> QTreeWidgetItem:
        title = f"{_PATIENT_GLYPH}  {patient.patient_id}"
        if patient.merged_aliases:
            aliases = ", ".join(sorted(patient.merged_aliases))
            title += f"  (merged: {aliases})"
        item = QTreeWidgetItem([title, ""])
        item.setData(0, ROLE_NODE_KIND, "patient")
        item.setData(0, ROLE_PATIENT_ID, patient.patient_id)
        # Patients are not draggable themselves (only organs are)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
        _bold(item, 0)

        if len(patient.contexts) > 1:
            for ctx in patient.contexts:
                item.addChild(self._make_context_item(ctx, patient.patient_id))
        elif patient.contexts:
            self._attach_rtstructs(item, patient.contexts[0], patient.patient_id)
        return item

    def _make_context_item(self, ctx: ImagingContext, patient_id: str) -> QTreeWidgetItem:
        date = ctx.display_date()
        item = QTreeWidgetItem([f"{_CONTEXT_GLYPH}  Context  {date}".rstrip(), ""])
        item.setData(0, ROLE_NODE_KIND, "context")
        item.setData(0, ROLE_FOR_UID, ctx.frame_of_reference_uid)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
        self._attach_rtstructs(item, ctx, patient_id)
        return item

    def _attach_rtstructs(self, parent_item: QTreeWidgetItem, ctx: ImagingContext, patient_id: str) -> None:
        for rtss in ctx.rtstructs:
            parent_item.addChild(self._make_rtstruct_item(rtss, patient_id))

    def _make_rtstruct_item(self, rtss: RTSTRUCTEntry, patient_id: str) -> QTreeWidgetItem:
        # Distinguish three rendering states:
        # - Synthetic STAPLE consensus entry → distinct glyph + bold title,
        #   informative tooltip.
        # - Constituent of a synthetic consensus → trailing marker suffix +
        #   tooltip explaining the relationship.
        # - Plain RTSS → unchanged from before.
        if rtss.is_synthetic_consensus:
            title = f"{_RTSS_GLYPH}  {rtss.filename}"
            tooltip = (
                "Synthetic STAPLE consensus RTSS generated from "
                f"{len(rtss.constituent_groups)} organ(s) across multiple raters. "
                "Designate this as the GT in a drawer to compare AI contours "
                "against the consensus reference."
            )
        elif rtss.sop_instance_uid in self._constituent_uids:
            title = f"{_RTSS_GLYPH}  {rtss.filename}{_CONSTITUENT_SUFFIX}"
            tooltip = (
                "This RTSS contributed to a synthetic STAPLE consensus generated "
                "in the Build Consensus GT tab. The consensus is also present "
                "in this tree (with source label 'STAPLE Consensus'). You can "
                "still use this file directly as a GT or test contour if you "
                "want to compare individual raters against the consensus."
            )
        else:
            title = f"{_RTSS_GLYPH}  {rtss.filename}"
            tooltip = ""
        item = QTreeWidgetItem([title, f"[{rtss.source_label}]"])
        item.setData(0, ROLE_NODE_KIND, "rtstruct")
        item.setData(0, ROLE_PATIENT_ID, patient_id)
        item.setData(0, ROLE_RTSS_SOP_UID, rtss.sop_instance_uid)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
        if tooltip:
            item.setToolTip(0, tooltip)
            item.setToolTip(1, tooltip)
        if rtss.is_synthetic_consensus:
            _bold(item, 0)
        # Sort organs alphabetically (case-insensitive) regardless of file order
        for organ in sorted(rtss.organs, key=lambda o: o.roi_name.lower()):
            item.addChild(
                self._make_organ_item(organ.roi_name, organ.roi_number, patient_id, rtss.sop_instance_uid)
            )
        return item

    def _make_organ_item(
        self, roi_name: str, roi_number: int, patient_id: str, sop_uid: str
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem([roi_name, ""])
        item.setData(0, ROLE_NODE_KIND, "organ")
        item.setData(0, ROLE_PATIENT_ID, patient_id)
        item.setData(0, ROLE_RTSS_SOP_UID, sop_uid)
        item.setData(0, ROLE_ROI_NUMBER, roi_number)
        item.setData(0, ROLE_ROI_NAME, roi_name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsDragEnabled)
        # Apply existing assignment mark, if any
        if (patient_id, sop_uid, roi_number) in self._marked_organs:
            item.setText(0, f"✓ {roi_name}")
        return item

    # ---- Marks ------------------------------------------------------------

    def _refresh_marks(self) -> None:
        """Re-iterate all leaves and update prefixes from self._marked_organs."""
        for top_idx in range(self.topLevelItemCount()):
            self._refresh_marks_recursive(self.topLevelItem(top_idx))

    def _refresh_marks_recursive(self, item: QTreeWidgetItem) -> None:
        kind = item.data(0, ROLE_NODE_KIND)
        if kind == "organ":
            pid = str(item.data(0, ROLE_PATIENT_ID) or "")
            sop = str(item.data(0, ROLE_RTSS_SOP_UID) or "")
            roi_n = int(item.data(0, ROLE_ROI_NUMBER) or 0)
            roi_name = str(item.data(0, ROLE_ROI_NAME) or "")
            prefix = "✓ " if (pid, sop, roi_n) in self._marked_organs else ""
            item.setText(0, f"{prefix}{roi_name}")
        for child_idx in range(item.childCount()):
            self._refresh_marks_recursive(item.child(child_idx))


# ---- Helpers --------------------------------------------------------------


def _bold(item: QTreeWidgetItem, column: int) -> None:
    font = item.font(column)
    font.setWeight(QFont.Weight.DemiBold)
    item.setFont(column, font)
