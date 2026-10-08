"""Label Organs — say which organ a drawer holds when its name does not.

The last step of curation, after the data has been cleaned: every drawer whose
ground-truth name the TG-263 dictionary does not recognise is listed here so it
can be told what it actually is.

This **labels only**. No drawer is merged, renamed, moved or altered; the
labelling is a separate fact attached to the ground-truth name. That keeps the
drawer name intact as the key it already is — for the rejection denylist, for
Likert item identity and for qualitative scores — so nothing recorded against a
drawer can be orphaned by a labelling decision, whenever it is taken.

Two drawers given the same label are grouped for cohort statistics while
staying separate in the tree, which is the whole point: the analysis pools
them, the curation record stays honest about how they were named.

Suggestions come from the contours already matched into each drawer. Hand-drawn
ground truth carries local shorthand that no dictionary knows; the vendor
contours beside it usually resolve cleanly, so the drawer's own contents
identify the organ its ground truth failed to name. Unanimous suggestions are
pre-selected. Split ones are offered but never pre-selected — measured on real
data, a 1/2 vote put ``Inner Ear_R`` down as a cornea with ``Ear_Internal_R``
among the losing candidates.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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

from autoseg_evaluator.core.organ_groups import (
    AUTOMATIC_TIERS,
    QUALIFIER_OAR,
    OrganKey,
)
from autoseg_evaluator.data.organ_index import suggest_organ_from_tests

_COL_DRAWER = 0
_COL_PATIENTS = 1
_COL_GT = 2
_COL_ORGAN = 3
_COL_EVIDENCE = 4

_HEADERS = ["Drawer", "Patients", "Ground-truth name(s)", "Is this organ…", "Suggested from"]

_LEAVE = ""  # combo data meaning "no label"


class OrganLabelsDialog(QDialog):
    """Assign a canonical organ to drawers the dictionary could not name.

    After an accepted exec(), :meth:`labels` returns ``{gt_roi_name: base}``
    ready to pass to ``build_organ_index(manual=…)``.
    """

    def __init__(
        self,
        index,
        drawers,
        existing: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Label Organs")
        self.setModal(True)
        self.resize(1080, 560)

        self._index = index
        self._labels: dict[str, str] = dict(existing or {})
        self._rows: list[dict] = []
        self._combos: list[tuple[list[str], QComboBox]] = []

        self._collect(drawers)
        self._build_ui()
        self._populate()

    # ---- Data -------------------------------------------------------------

    def _collect(self, drawers) -> None:
        """One row per drawer whose ground truth the dictionary cannot name."""
        for drawer in drawers:
            subs = list(drawer.all_subsections())
            gt_names = sorted({s.gt_roi_name for s in subs if s.gt_roi_name})
            if not gt_names:
                continue
            recognised = any(
                (placed := self._index.assignments.get(name)) is not None
                and placed.tier in AUTOMATIC_TIERS
                for name in gt_names
            )
            if recognised:
                continue
            # Only organs. Targets, couch and board, rings, dose levels, PRVs
            # and composites are already classified from the DICOM structure
            # type and the name, and none of them wants an organ label — on a
            # real head-and-neck case they were 22 of the 26 rows here.
            qualifier = _qualifier_of(self._index, gt_names)
            if qualifier != QUALIFIER_OAR:
                continue
            test_names = [t.organ_name for s in subs for t in s.tests if t.organ_name]
            suggestion = suggest_organ_from_tests(self._index, test_names, qualifier=qualifier)
            self._rows.append(
                {
                    "drawer": drawer.organ_name(),
                    "patients": len(subs),
                    "gt_names": gt_names,
                    "suggestion": suggestion,
                }
            )
        self._rows.sort(key=lambda r: (-r["patients"], r["drawer"]))

    def _known_bases(self) -> list[str]:
        """Every organ the dictionary recognised anywhere in this cohort."""
        bases = {
            placed.key.base
            for placed in self._index.assignments.values()
            if placed.tier in AUTOMATIC_TIERS and placed.key.base
        }
        bases.update(v for v in self._labels.values() if v)
        return sorted(bases)

    # ---- UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        blurb = QLabel(
            "These drawers have ground-truth names the TG-263 dictionary does not "
            "recognise, so each is currently its own organ and pools with nothing. "
            "Saying what they are lets results from differently-named drawers be "
            "analysed together.<br><br>"
            "<b>Nothing is merged, renamed or moved.</b> This attaches a label; the "
            "drawers stay exactly as they are.<br><br>"
            "Only organs are listed — targets, couch, rings, dose levels and PRVs are "
            "already classified and are left alone.",
            self,
        )
        blurb.setWordWrap(True)
        blurb.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(blurb)

        filters = QHBoxLayout()
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Filter drawers…")
        self._search.textChanged.connect(self._apply_filter)
        filters.addWidget(self._search, stretch=1)
        self._accept_btn = QPushButton("", self)
        self._accept_btn.clicked.connect(self._on_accept_suggestions)
        filters.addWidget(self._accept_btn)
        outer.addLayout(filters)

        self._table = QTableWidget(0, len(_HEADERS), self)
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self._table.horizontalHeader()
        for column in (_COL_DRAWER, _COL_PATIENTS, _COL_GT):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_ORGAN, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_EVIDENCE, QHeaderView.ResizeMode.Stretch)
        outer.addWidget(self._table, stretch=1)

        self._status = QLabel("", self)
        self._status.setWordWrap(True)
        outer.addWidget(self._status)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _populate(self) -> None:
        self._table.setRowCount(len(self._rows))
        self._combos.clear()
        known = self._known_bases()

        for row, entry in enumerate(self._rows):
            self._table.setItem(row, _COL_DRAWER, _text(entry["drawer"]))
            count = entry["patients"]
            self._table.setItem(
                row, _COL_PATIENTS, _text(f"{count} patient{'s' if count != 1 else ''}")
            )
            self._table.setItem(row, _COL_GT, _text(", ".join(entry["gt_names"])))

            combo = QComboBox(self)
            combo.setEditable(True)
            combo.addItem("— leave unlabelled —", _LEAVE)
            suggestion = entry["suggestion"]
            if suggestion is not None:
                label = OrganKey(suggestion.base).label()
                mark = "✓" if suggestion.unanimous else "?"
                combo.addItem(
                    f"{mark} {label}  ({suggestion.tally} of its contours)", suggestion.base
                )
            for base in known:
                if suggestion is None or base != suggestion.base:
                    combo.addItem(OrganKey(base).label(), base)

            chosen = self._labels.get(entry["gt_names"][0], _LEAVE)
            if chosen:
                position = combo.findData(chosen)
                combo.setCurrentIndex(position if position >= 0 else 0)
            elif suggestion is not None and suggestion.unanimous:
                # Pre-selected only when every contour in the drawer agreed.
                combo.setCurrentIndex(1)
            else:
                combo.setCurrentIndex(0)
            combo.currentIndexChanged.connect(self._on_changed)
            self._table.setCellWidget(row, _COL_ORGAN, combo)
            self._combos.append((entry["gt_names"], combo))

            evidence = ", ".join(suggestion.evidence[:4]) if suggestion else ""
            item = _text(evidence)
            if suggestion is not None and not suggestion.unanimous:
                item.setToolTip(
                    "Its contours did not agree, so this is a guess rather than a "
                    "reading — check it before accepting."
                )
            self._table.setItem(row, _COL_EVIDENCE, item)

        self._table.resizeRowsToContents()
        self._sync()
        self._apply_filter()

    # ---- Handlers ---------------------------------------------------------

    def _apply_filter(self) -> None:
        needle = self._search.text().strip().lower()
        for row in range(self._table.rowCount()):
            text = self._table.item(row, _COL_DRAWER).text().lower()
            gt = self._table.item(row, _COL_GT).text().lower()
            self._table.setRowHidden(row, bool(needle) and needle not in text and needle not in gt)

    def _on_changed(self, _index: int) -> None:
        self._sync()

    def _on_accept_suggestions(self) -> None:
        for row, entry in enumerate(self._rows):
            suggestion = entry["suggestion"]
            if suggestion is None:
                continue
            combo = self._table.cellWidget(row, _COL_ORGAN)
            if combo is not None and not combo.currentData():
                position = combo.findData(suggestion.base)
                if position >= 0:
                    combo.setCurrentIndex(position)
        self._sync()

    def _sync(self) -> None:
        self._labels = {}
        for gt_names, combo in self._combos:
            base = combo.currentData()
            if base is None and combo.isEditable():
                typed = combo.currentText().strip()
                base = typed if typed and not typed.startswith(("—", "✓", "?")) else ""
            if base:
                for name in gt_names:
                    self._labels[name] = str(base)

        pending = sum(
            1
            for row, entry in enumerate(self._rows)
            if entry["suggestion"] is not None
            and (c := self._table.cellWidget(row, _COL_ORGAN)) is not None
            and not c.currentData()
        )
        self._accept_btn.setText(
            f"Accept {pending} suggestion(s)" if pending else "No suggestions pending"
        )
        self._accept_btn.setEnabled(bool(pending))

        labelled = len({tuple(n) for n, c in self._combos if c.currentData()})
        self._status.setText(
            f"<span style='color:#666'>{len(self._rows)} unrecognised drawer(s) · "
            f"{labelled} labelled. Leaving one unlabelled is fine — it simply stays "
            f"its own organ.</span>"
        )
        self._status.setTextFormat(Qt.TextFormat.RichText)

    # ---- Result -----------------------------------------------------------

    def labels(self) -> dict[str, str]:
        """``{ground-truth ROI name: organ base}``, for the index rebuild."""
        self._sync()
        return dict(self._labels)


def _qualifier_of(index, gt_names) -> str:
    """What kind of structure a drawer holds, from its ground-truth names."""
    for name in gt_names:
        placed = index.assignments.get(name)
        if placed is not None:
            return placed.key.qualifier
    return QUALIFIER_OAR


def _text(value: str) -> QTableWidgetItem:
    item = QTableWidgetItem(value)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
    return item


__all__ = ["OrganLabelsDialog"]
