"""Review Organ Groups — confirm which ROI names mean the same organ.

Most names place themselves: they resolve through the TG-263 dictionary, or do
so once a vendor decoration is removed. What is left over is free text that no
dictionary knows — ``Bowel_Bag``, ``Aorte_Thx_Asc``, ``BonesThoraxOPT`` — and
only a person can say whether two of those are one organ.

This dialog is deliberately **not** a gate. Nothing here blocks a computation;
an unreviewed name simply forms a group of its own, and statistics over it
still work. The only thing gained by reviewing is pooling, so the effort is
proportional to how much pooling an analysis actually wants.

It is built for scale rather than for elegance. A real cohort leaves several
hundred names unresolved, which rules out a one-row-at-a-time dialog — but the
effort is heavily front-loaded, with the hundred most frequent names covering
roughly two thirds of all unresolved contours. So: ranked by occurrence,
searchable, multi-select, with the automatic suggestions acceptable in one
action.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from autoseg_evaluator.core.organ_groups import (
    QUALIFIER_OAR,
    TIER_DESCRIPTIONS,
    TIER_MANUAL,
    TIER_UNASSIGNED,
)

_COL_COUNT = 0
_COL_NAME = 1
_COL_ORGAN = 2
_COL_SIDE = 3
_COL_TYPE = 4
_COL_HOW = 5
_COL_NOTES = 6

_HEADERS = ["Seen", "ROI name", "Organ", "Side", "Structure type", "How", "Notes"]

#: Marks a name deliberately kept out of per-organ statistics. Stored as an
#: ordinary assignment so it round-trips through the session unchanged.
EXCLUDED = "(excluded)"


class OrganGroupsDialog(QDialog):
    """Confirm or override which organ each ROI name belongs to.

    Returns ``{roi_name: base}`` from :meth:`assignments` after an accepted
    exec(); those outrank every automatic tier when the index is rebuilt.
    """

    def __init__(
        self,
        index,
        existing: dict[str, str] | None = None,
        synonyms_flat: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review Organ Groups")
        self.setModal(True)
        self.resize(1140, 640)

        self._index = index
        self._synonyms = dict(synonyms_flat or {})
        self._manual: dict[str, str] = dict(existing or {})
        self._proposals = list(index.proposals(synonyms_flat=self._synonyms, oar_only=True))

        self._build_ui()
        self._populate()

    # ---- UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        blurb = QLabel(
            "Names that resolve through the TG-263 dictionary are grouped for you. "
            "Anything marked <b>Stands alone</b> is free text no dictionary knows — "
            "review it only if you want it pooled with other spellings of the same "
            "organ. Nothing here is required: an unreviewed name still appears in "
            "results, just as a group of its own.",
            self,
        )
        blurb.setWordWrap(True)
        blurb.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(blurb)

        filters = QHBoxLayout()
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Filter by name or organ…")
        self._search.textChanged.connect(self._apply_filter)
        filters.addWidget(self._search, stretch=1)

        self._only_review = QCheckBox("Only names needing review", self)
        self._only_review.setChecked(True)
        self._only_review.toggled.connect(self._apply_filter)
        filters.addWidget(self._only_review)

        self._only_oar = QCheckBox("Organs only", self)
        self._only_oar.setChecked(True)
        self._only_oar.setToolTip(
            "Hide targets, support structures, composites and dose levels. They are "
            "still classified and still appear in results."
        )
        self._only_oar.toggled.connect(self._apply_filter)
        filters.addWidget(self._only_oar)
        outer.addLayout(filters)

        self._table = QTableWidget(0, len(_HEADERS), self)
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(_COL_COUNT, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_ORGAN, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_SIDE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_TYPE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_HOW, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_NOTES, QHeaderView.ResizeMode.Stretch)
        outer.addWidget(self._table, stretch=1)

        actions = QHBoxLayout()
        self._assign_btn = QPushButton("Group selected as…", self)
        self._assign_btn.setToolTip(
            "Put every selected name into one organ group. Laterality is kept from "
            "each name, so selecting a left and a right structure still leaves them "
            "on their own sides."
        )
        self._assign_btn.clicked.connect(self._on_assign)
        actions.addWidget(self._assign_btn)

        self._exclude_btn = QPushButton("Exclude selected", self)
        self._exclude_btn.setToolTip("Keep these out of per-organ statistics.")
        self._exclude_btn.clicked.connect(self._on_exclude)
        actions.addWidget(self._exclude_btn)

        self._clear_btn = QPushButton("Reset selected", self)
        self._clear_btn.clicked.connect(self._on_reset)
        actions.addWidget(self._clear_btn)

        actions.addStretch(1)
        self._accept_btn = QPushButton("", self)
        self._accept_btn.clicked.connect(self._on_accept_suggestions)
        actions.addWidget(self._accept_btn)
        outer.addLayout(actions)

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

    # ---- Population -------------------------------------------------------

    def _rows(self) -> list[Any]:
        return sorted(
            self._index.assignments.values(),
            key=lambda a: (-self._index.frequencies.get(a.roi_name, 0), a.roi_name),
        )

    def _effective(self, assignment) -> tuple[str, str, str]:
        """``(organ_label, side, how)`` under the pending manual answers."""
        chosen = self._manual.get(assignment.roi_name)
        if chosen == EXCLUDED:
            return EXCLUDED, "", TIER_DESCRIPTIONS[TIER_MANUAL]
        if chosen:
            side = assignment.key.laterality
            label = chosen.replace("_", " ").title()
            if side:
                label = f"{label} ({side})"
            return label, side, TIER_DESCRIPTIONS[TIER_MANUAL]
        return (
            assignment.key.label(),
            assignment.key.laterality,
            TIER_DESCRIPTIONS.get(assignment.tier, assignment.tier),
        )

    def _populate(self) -> None:
        was_sorting = self._table.isSortingEnabled()
        self._table.setSortingEnabled(False)
        rows = self._rows()
        self._table.setRowCount(len(rows))
        for row, assignment in enumerate(rows):
            count = self._index.frequencies.get(assignment.roi_name, 0)
            count_item = QTableWidgetItem()
            # Numeric data so the column sorts by value, not lexicographically.
            count_item.setData(Qt.ItemDataRole.DisplayRole, int(count))
            self._table.setItem(row, _COL_COUNT, count_item)

            name_item = QTableWidgetItem(assignment.roi_name)
            name_item.setData(Qt.ItemDataRole.UserRole, assignment.roi_name)
            self._table.setItem(row, _COL_NAME, name_item)

            organ, side, how = self._effective(assignment)
            self._table.setItem(row, _COL_ORGAN, QTableWidgetItem(organ))
            self._table.setItem(row, _COL_SIDE, QTableWidgetItem(side))
            self._table.setItem(row, _COL_TYPE, QTableWidgetItem(assignment.key.qualifier))
            self._table.setItem(row, _COL_HOW, QTableWidgetItem(how))

            notes = list(assignment.removed)
            if assignment.type_conflict:
                notes.append("structure type differs between files")
            note_item = QTableWidgetItem("; ".join(notes))
            if assignment.type_conflict:
                note_item.setToolTip(
                    "Producers disagree about what kind of structure this is — "
                    "the most common value was used."
                )
            self._table.setItem(row, _COL_NOTES, note_item)
        self._table.setSortingEnabled(was_sorting)
        self._apply_filter()
        self._refresh_status()

    def _apply_filter(self) -> None:
        needle = self._search.text().strip().lower()
        only_review = self._only_review.isChecked()
        only_oar = self._only_oar.isChecked()
        for row in range(self._table.rowCount()):
            name = self._table.item(row, _COL_NAME).text()
            assignment = self._index.assignments.get(name)
            visible = True
            if only_oar and assignment is not None and assignment.key.qualifier != QUALIFIER_OAR:
                visible = False
            if only_review and assignment is not None:
                # Hide only what resolved on its own and nobody has touched.
                # A name the user has answered stays on screen: it is part of
                # the review, and hiding it the moment it is answered would
                # leave no way to see or undo a mistake.
                answered = name in self._manual
                if assignment.tier != TIER_UNASSIGNED and not answered:
                    visible = False
            if visible and needle:
                organ = self._table.item(row, _COL_ORGAN).text().lower()
                visible = needle in name.lower() or needle in organ
            self._table.setRowHidden(row, not visible)

    # ---- Actions ----------------------------------------------------------

    def _selected_names(self) -> list[str]:
        names = []
        for index in self._table.selectionModel().selectedRows(_COL_NAME):
            if not self._table.isRowHidden(index.row()):
                names.append(self._table.item(index.row(), _COL_NAME).text())
        return names

    def _existing_bases(self) -> list[str]:
        bases = {a.key.base for a in self._index.assignments.values() if a.key.base}
        bases.update(v for v in self._manual.values() if v and v != EXCLUDED)
        return sorted(bases)

    def _on_assign(self) -> None:
        names = self._selected_names()
        if not names:
            QMessageBox.information(self, "Group organs", "Select one or more names first.")
            return
        suggestion = ""
        first = self._index.assignments.get(names[0])
        if first is not None:
            suggestion = first.key.base
        choices = self._existing_bases()
        if suggestion and suggestion in choices:
            choices.remove(suggestion)
            choices.insert(0, suggestion)
        base, ok = QInputDialog.getItem(
            self,
            "Group organs",
            f"Group {len(names)} name(s) as which organ?",
            choices or [suggestion],
            0,
            True,
        )
        if not ok or not base.strip():
            return
        for name in names:
            self._manual[name] = base.strip()
        self._populate()

    def _on_exclude(self) -> None:
        names = self._selected_names()
        if not names:
            QMessageBox.information(self, "Exclude", "Select one or more names first.")
            return
        for name in names:
            self._manual[name] = EXCLUDED
        self._populate()

    def _on_reset(self) -> None:
        for name in self._selected_names():
            self._manual.pop(name, None)
        self._populate()

    def _pending_suggestions(self) -> list[tuple[str, str]]:
        """``(roi_name, base)`` pairs the fuzzy tier proposes and nobody has answered."""
        out: list[tuple[str, str]] = []
        for proposal in self._proposals:
            if not proposal.members:
                continue
            seed = proposal.members[0]
            seed_assignment = self._index.assignments.get(seed)
            base = self._manual.get(seed) or (seed_assignment.key.base if seed_assignment else "")
            if not base or base == EXCLUDED:
                continue
            for member in proposal.members[1:]:
                if member not in self._manual:
                    out.append((member, base))
        return out

    def _on_accept_suggestions(self) -> None:
        pending = self._pending_suggestions()
        if not pending:
            return
        for name, base in pending:
            self._manual[name] = base
        self._populate()

    # ---- Status -----------------------------------------------------------

    def _refresh_status(self) -> None:
        total = len(self._index.assignments)
        unresolved = sum(
            1
            for a in self._index.assignments.values()
            if a.tier == TIER_UNASSIGNED
            and a.key.qualifier == QUALIFIER_OAR
            and a.roi_name not in self._manual
        )
        pending = len(self._pending_suggestions())
        self._accept_btn.setText(f"Accept {pending} suggestion(s)" if pending else "No suggestions")
        self._accept_btn.setEnabled(bool(pending))

        answered = len(self._manual)
        self._status.setText(
            f"<span style='color:#666'>{total} names · {answered} answered by you · "
            f"{unresolved} organ name(s) still standing alone. "
            f"Leaving them is fine — they simply are not pooled.</span>"
        )
        self._status.setTextFormat(Qt.TextFormat.RichText)

    # ---- Result -----------------------------------------------------------

    def assignments(self) -> dict[str, str]:
        """``{roi_name: base}``, ready to pass back as ``manual`` on rebuild."""
        return dict(self._manual)


__all__ = ["EXCLUDED", "OrganGroupsDialog"]
