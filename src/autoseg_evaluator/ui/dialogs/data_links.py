"""Review Data Links dialog — settle which CT and which dose each RTSS uses.

Most datasets never need this: a structure set that names its reference series
outright, alongside a single dose, resolves on its own and the table simply
reports what was decided and why. The dialog exists for the cases that do not
resolve — a Frame of Reference holding two planning CTs, a replan filed under
the original study, a composite dose — where the honest answer is to ask
rather than to pick the first candidate and say nothing.

Every link can be overridden, not only the ambiguous ones, because an
automatic answer can be confidently wrong on data whose reference sequences
were rewritten by an anonymiser or a partial re-export.

Answers are returned as ``{override_key(...): uid}`` and stored on
``MetadataLibrary.link_overrides``, where the resolver consults them ahead of
every automatic tier. They are persisted in the session so a reloaded cohort
does not have to be re-answered.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from autoseg_evaluator.data.linkage import (
    KIND_DOSE,
    KIND_SERIES,
    candidate_label,
    candidate_uid,
    link_candidates,
    override_key,
    resolve_dose,
    resolve_image_series,
)

_AUTOMATIC = ""  # combo item data meaning "no override — leave it automatic"

_COL_PATIENT = 0
_COL_RTSS = 1
_COL_SERIES = 2
_COL_DOSE = 3


class DataLinksDialog(QDialog):
    """Shows every structure set's resolved image series and dose.

    Call :meth:`overrides` after an accepted exec() to get the user's answers.
    """

    def __init__(
        self,
        library,
        existing_overrides: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review Data Links")
        self.setModal(True)
        self.resize(1040, 560)

        self._library = library
        self._overrides: dict[str, str] = dict(existing_overrides or {})
        # (row, kind) -> (patient_id, rtstruct_sop_uid, combo)
        self._combos: list[tuple[str, str, str, QComboBox]] = []

        self._build_ui()
        self._populate()
        self._refresh_status()

    # ---- UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        blurb = QLabel(
            "Each structure set is matched to the image series it was contoured on and, "
            "where dose metrics are used, to a dose distribution. Links are read from the "
            "references inside the DICOM files themselves and fall back to the Frame of "
            "Reference only when those are absent.<br><br>"
            "Anything marked <b>Choose one</b> could not be decided automatically — two or "
            "more candidates were equally good — and must be settled before metrics can be "
            "computed. Confident links can also be overridden if you know them to be wrong.",
            self,
        )
        blurb.setWordWrap(True)
        blurb.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(blurb)

        self._table = QTableWidget(0, 4, self)
        self._table.setHorizontalHeaderLabels(["Patient", "Structure set", "Image series", "Dose"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(_COL_PATIENT, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_RTSS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_SERIES, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_DOSE, QHeaderView.ResizeMode.Stretch)
        outer.addWidget(self._table, stretch=1)

        footer = QHBoxLayout()
        self._status_label = QLabel("", self)
        self._status_label.setWordWrap(True)
        footer.addWidget(self._status_label, stretch=1)
        self._reset_btn = QPushButton("Reset all to automatic", self)
        self._reset_btn.clicked.connect(self._on_reset)
        footer.addWidget(self._reset_btn)
        outer.addLayout(footer)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    # ---- Population -------------------------------------------------------

    def _populate(self) -> None:
        rows = []
        for patient_id, patient in sorted(self._library.patients.items()):
            for ctx in patient.contexts:
                for rtss in ctx.rtstructs:
                    rows.append((patient_id, rtss))

        self._table.setRowCount(len(rows))
        for row, (patient_id, rtss) in enumerate(rows):
            self._table.setItem(row, _COL_PATIENT, _text_item(patient_id))
            label = rtss.source_label or rtss.filename
            item = _text_item(label)
            item.setToolTip(rtss.filename)
            self._table.setItem(row, _COL_RTSS, item)

            if rtss.is_synthetic_consensus:
                # A STAPLE consensus is built from other structure sets' masks;
                # it has no DICOM file and so no image reference of its own.
                self._table.setCellWidget(row, _COL_SERIES, None)
                self._table.setItem(
                    row, _COL_SERIES, _text_item("(consensus — inherits its constituents)")
                )
            else:
                self._table.setCellWidget(
                    row,
                    _COL_SERIES,
                    self._make_combo(patient_id, rtss.sop_instance_uid, KIND_SERIES),
                )
            self._table.setCellWidget(
                row,
                _COL_DOSE,
                self._make_combo(patient_id, rtss.sop_instance_uid, KIND_DOSE),
            )
        self._table.resizeRowsToContents()

    def _make_combo(self, patient_id: str, rtss_sop: str, kind: str) -> QComboBox:
        combo = QComboBox(self)
        resolver = resolve_image_series if kind == KIND_SERIES else resolve_dose
        res = resolver(self._library, patient_id, rtss_sop)
        candidates = link_candidates(self._library, patient_id, kind)

        # Item 0 describes what happens with no override, and is the item the
        # user selects to hand the decision back to the resolver.
        if res.is_ambiguous:
            first = f"⚠ Choose one — {len(res.candidates)} equally good candidates"
        elif not res.is_resolved:
            first = "No dose available" if kind == KIND_DOSE else "⚠ No matching image series found"
        elif res.tier == "override":
            first = "Automatic (currently overridden below)"
        else:
            first = f"Automatic: {candidate_label(res.target, kind)}  ·  {res.description}"
        combo.addItem(first, _AUTOMATIC)

        for entry in candidates:
            combo.addItem(candidate_label(entry, kind), candidate_uid(entry, kind))

        chosen = self._overrides.get(override_key(patient_id, rtss_sop, kind), _AUTOMATIC)
        index = combo.findData(chosen) if chosen else 0
        combo.setCurrentIndex(index if index >= 0 else 0)

        needs_answer = res.is_ambiguous or (kind == KIND_SERIES and not res.is_resolved)
        if needs_answer and combo.currentIndex() == 0:
            combo.setStyleSheet("QComboBox { border: 1px solid #d96b00; }")

        combo.currentIndexChanged.connect(self._on_combo_changed)
        self._combos.append((patient_id, rtss_sop, kind, combo))
        return combo

    # ---- Handlers ---------------------------------------------------------

    def _on_combo_changed(self, _index: int) -> None:
        self._sync_overrides()
        self._refresh_status()

    def _on_reset(self) -> None:
        for _pid, _sop, _kind, combo in self._combos:
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self._overrides.clear()
        self._refresh_status()

    def _sync_overrides(self) -> None:
        for patient_id, rtss_sop, kind, combo in self._combos:
            key = override_key(patient_id, rtss_sop, kind)
            uid = combo.currentData()
            if uid:
                self._overrides[key] = str(uid)
            else:
                self._overrides.pop(key, None)

    def _refresh_status(self) -> None:
        """Count links still awaiting an answer, under the pending overrides.

        Resolution is re-run against a temporary view of the library carrying
        the in-dialog choices, so the count reacts as the user works rather
        than only after the dialog is accepted.
        """
        saved = getattr(self._library, "link_overrides", {})
        self._library.link_overrides = dict(self._overrides)
        try:
            outstanding = 0
            for patient_id, rtss_sop, kind, _combo in self._combos:
                resolver = resolve_image_series if kind == KIND_SERIES else resolve_dose
                res = resolver(self._library, patient_id, rtss_sop)
                if res.is_ambiguous:
                    outstanding += 1
        finally:
            self._library.link_overrides = saved

        if outstanding:
            self._status_label.setText(
                f"<span style='color:#d96b00'><b>{outstanding} link(s) still need a "
                f"choice.</b> Metrics cannot be computed until they are settled.</span>"
            )
        elif self._overrides:
            self._status_label.setText(
                f"<span style='color:#666'>All links resolved · "
                f"{len(self._overrides)} manual override(s).</span>"
            )
        else:
            self._status_label.setText(
                "<span style='color:#666'>All links resolved automatically.</span>"
            )
        self._status_label.setTextFormat(Qt.TextFormat.RichText)

    # ---- Result -----------------------------------------------------------

    def overrides(self) -> dict[str, str]:
        """User-chosen links, ready to assign to ``library.link_overrides``."""
        self._sync_overrides()
        return dict(self._overrides)


def _text_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
    return item


__all__: list[Any] = ["DataLinksDialog"]
