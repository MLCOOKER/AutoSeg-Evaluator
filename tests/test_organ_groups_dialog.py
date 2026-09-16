"""Tests for the Review Organ Groups dialog.

Two properties matter beyond "the buttons work":

*It is never a gate.* Leaving the dialog untouched must not change anything,
because an unreviewed name is a valid state — it forms a group of one and its
results still compute.

*Grouping by hand must not defeat the laterality guarantee.* Selecting a left
and a right structure and grouping them under one organ has to leave them on
their own sides, or the safety property the whole design rests on would be
bypassed by the one path that lets a user type freely.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QItemSelectionModel  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from autoseg_evaluator.core.organ_groups import (  # noqa: E402
    LATERALITY_L,
    LATERALITY_R,
    TIER_MANUAL,
)
from autoseg_evaluator.data.organ_index import build_organ_index  # noqa: E402
from autoseg_evaluator.data.synonyms import flatten_synonyms, load_synonyms  # noqa: E402
from autoseg_evaluator.ui.dialogs.organ_groups import (  # noqa: E402
    EXCLUDED,
    OrganGroupsDialog,
)
from tests.test_organ_index import _FakeLibrary  # noqa: E402

SYNONYMS_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "autoseg_evaluator"
    / "resources"
    / "synonyms.json"
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def syn():
    return flatten_synonyms(load_synonyms(SYNONYMS_PATH))


def _index(structure_sets, syn):
    return build_organ_index(_FakeLibrary(structure_sets), synonyms_flat=syn)


def _select(dialog, names):
    """Select every row whose ROI name is in ``names``.

    ``selectRow`` replaces the selection rather than extending it, so the
    selection model is driven directly.
    """
    model = dialog._table.selectionModel()
    model.clearSelection()
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    for row in range(dialog._table.rowCount()):
        if dialog._table.item(row, 1).text() in names:
            model.select(dialog._table.model().index(row, 1), flags)


# ---- Doing nothing changes nothing ----------------------------------------


def test_untouched_dialog_returns_no_assignments(qapp, syn):
    index = _index([("A", [("Wibble_Xyz", "ORGAN"), ("Parotid_L", "ORGAN")])], syn)
    dialog = OrganGroupsDialog(index, {}, synonyms_flat=syn)
    assert dialog.assignments() == {}
    dialog.deleteLater()


def test_existing_answers_are_carried_in_and_back(qapp, syn):
    index = _index([("A", [("Wibble_Xyz", "ORGAN")])], syn)
    existing = {"Wibble_Xyz": "parotid"}
    dialog = OrganGroupsDialog(index, existing, synonyms_flat=syn)
    assert dialog.assignments() == existing
    dialog.deleteLater()


def test_every_name_is_listed(qapp, syn):
    names = ["Parotid_L", "Wibble_Xyz", "1CTV_", "Board"]
    index = _index([("A", [(n, "ORGAN") for n in names])], syn)
    dialog = OrganGroupsDialog(index, {}, synonyms_flat=syn)
    listed = {dialog._table.item(r, 1).text() for r in range(dialog._table.rowCount())}
    assert listed == set(names)
    dialog.deleteLater()


# ---- Ordering and filtering -----------------------------------------------


def test_rows_are_ranked_by_how_often_a_name_occurs(qapp, syn):
    """The review is only finishable if the common names come first."""
    index = _index(
        [
            ("A", [("Rare_Unknown", "ORGAN"), ("Common_Unknown", "ORGAN")]),
            ("B", [("Common_Unknown", "ORGAN")]),
            ("C", [("Common_Unknown", "ORGAN")]),
        ],
        syn,
    )
    dialog = OrganGroupsDialog(index, {}, synonyms_flat=syn)
    assert dialog._table.item(0, 1).text() == "Common_Unknown"
    dialog.deleteLater()


def test_search_narrows_the_table(qapp, syn):
    index = _index([("A", [("Bowel_Bag", "ORGAN"), ("Wibble_Xyz", "ORGAN")])], syn)
    dialog = OrganGroupsDialog(index, {}, synonyms_flat=syn)
    dialog._search.setText("bowel")
    visible = [
        dialog._table.item(r, 1).text()
        for r in range(dialog._table.rowCount())
        if not dialog._table.isRowHidden(r)
    ]
    assert visible == ["Bowel_Bag"]
    dialog.deleteLater()


def test_resolved_names_are_hidden_by_default(qapp, syn):
    """Only what needs attention is shown until the user asks for the rest."""
    index = _index([("A", [("Parotid_L", "ORGAN"), ("Wibble_Xyz", "ORGAN")])], syn)
    dialog = OrganGroupsDialog(index, {}, synonyms_flat=syn)

    def visible():
        return {
            dialog._table.item(r, 1).text()
            for r in range(dialog._table.rowCount())
            if not dialog._table.isRowHidden(r)
        }

    assert visible() == {"Wibble_Xyz"}
    dialog._only_review.setChecked(False)
    assert visible() == {"Parotid_L", "Wibble_Xyz"}
    dialog.deleteLater()


def test_non_organs_are_hidden_by_default(qapp, syn):
    index = _index([("A", [("Wibble_Xyz", "ORGAN"), ("Zzz_Unknown", "CTV")])], syn)
    dialog = OrganGroupsDialog(index, {}, synonyms_flat=syn)

    def visible():
        return {
            dialog._table.item(r, 1).text()
            for r in range(dialog._table.rowCount())
            if not dialog._table.isRowHidden(r)
        }

    assert "Zzz_Unknown" not in visible()
    dialog._only_oar.setChecked(False)
    assert "Zzz_Unknown" in visible()
    dialog.deleteLater()


# ---- Grouping by hand ------------------------------------------------------


def test_grouping_selected_names_records_one_organ(qapp, syn):
    index = _index([("A", [("Bowel_Bag", "ORGAN"), ("Bag_Bowel", "ORGAN")])], syn)
    dialog = OrganGroupsDialog(index, {}, synonyms_flat=syn)
    _select(dialog, {"Bowel_Bag", "Bag_Bowel"})
    for name in dialog._selected_names():
        dialog._manual[name] = "bowel_bag"
    dialog._populate()
    assert dialog.assignments() == {"Bowel_Bag": "bowel_bag", "Bag_Bowel": "bowel_bag"}
    dialog.deleteLater()


def test_manual_grouping_still_cannot_merge_left_with_right(qapp, syn):
    """The guarantee has to survive the one path where a user types freely."""
    index = _index([("A", [("Wibble_L", "ORGAN"), ("Wibble_R", "ORGAN")])], syn)
    dialog = OrganGroupsDialog(index, {}, synonyms_flat=syn)
    for name in ("Wibble_L", "Wibble_R"):
        dialog._manual[name] = "wibble"
    dialog._populate()
    rebuilt = build_organ_index(
        _FakeLibrary([("A", [("Wibble_L", "ORGAN"), ("Wibble_R", "ORGAN")])]),
        synonyms_flat=syn,
        manual=dialog.assignments(),
    )
    left = rebuilt.get("Wibble_L")
    right = rebuilt.get("Wibble_R")
    assert left.key.base == right.key.base == "wibble"
    assert left.key.laterality == LATERALITY_L
    assert right.key.laterality == LATERALITY_R
    assert left.key != right.key
    assert left.tier == TIER_MANUAL
    dialog.deleteLater()


def test_excluding_marks_a_name_without_deleting_it(qapp, syn):
    index = _index([("A", [("Artefact", "ORGAN")])], syn)
    dialog = OrganGroupsDialog(index, {}, synonyms_flat=syn)
    _select(dialog, {"Artefact"})
    dialog._on_exclude()
    assert dialog.assignments() == {"Artefact": EXCLUDED}
    dialog.deleteLater()


def test_reset_removes_an_answer(qapp, syn):
    index = _index([("A", [("Wibble_Xyz", "ORGAN")])], syn)
    dialog = OrganGroupsDialog(index, {"Wibble_Xyz": "parotid"}, synonyms_flat=syn)
    _select(dialog, {"Wibble_Xyz"})
    dialog._on_reset()
    assert dialog.assignments() == {}
    dialog.deleteLater()


# ---- Suggestions -----------------------------------------------------------


def test_suggestions_are_offered_for_near_identical_spellings(qapp, syn):
    index = _index(
        [
            ("A", [("Bowel_Bag", "ORGAN")]),
            ("B", [("Bowel Bag", "ORGAN")]),
            ("C", [("bowel bag", "ORGAN")]),
        ],
        syn,
    )
    dialog = OrganGroupsDialog(index, {}, synonyms_flat=syn)
    assert dialog._pending_suggestions(), "formatting variants should be suggested"
    dialog.deleteLater()


def test_accepting_suggestions_groups_them_in_one_action(qapp, syn):
    index = _index(
        [
            ("A", [("Bowel_Bag", "ORGAN")]),
            ("B", [("Bowel Bag", "ORGAN")]),
            ("C", [("bowel bag", "ORGAN")]),
        ],
        syn,
    )
    dialog = OrganGroupsDialog(index, {}, synonyms_flat=syn)
    dialog._on_accept_suggestions()
    answers = dialog.assignments()
    assert len(answers) >= 1
    assert len(set(answers.values())) == 1, "all members land on one organ"
    assert not dialog._pending_suggestions()
    dialog.deleteLater()


def test_nothing_is_suggested_for_distinct_structures(qapp, syn):
    """The barriers hold inside the dialog too."""
    index = _index([("A", [("Rib Left 1", "ORGAN"), ("Rib Left 12", "ORGAN")])], syn)
    dialog = OrganGroupsDialog(index, {}, synonyms_flat=syn)
    assert dialog._pending_suggestions() == []
    dialog.deleteLater()


def test_suggestions_do_not_override_an_existing_answer(qapp, syn):
    index = _index(
        [("A", [("Bowel_Bag", "ORGAN")]), ("B", [("Bowel Bag", "ORGAN")])],
        syn,
    )
    dialog = OrganGroupsDialog(index, {"Bowel Bag": "something_else"}, synonyms_flat=syn)
    dialog._on_accept_suggestions()
    assert dialog.assignments()["Bowel Bag"] == "something_else"
    dialog.deleteLater()


# ---- Status ---------------------------------------------------------------


def test_status_counts_what_is_still_standing_alone(qapp, syn):
    index = _index([("A", [("Wibble_Xyz", "ORGAN"), ("Parotid_L", "ORGAN")])], syn)
    dialog = OrganGroupsDialog(index, {}, synonyms_flat=syn)
    assert "1 organ name(s) still standing alone" in dialog._status.text()
    dialog._manual["Wibble_Xyz"] = "parotid"
    dialog._populate()
    assert "0 organ name(s) still standing alone" in dialog._status.text()
    dialog.deleteLater()
