"""Tests for Label Organs — the last curation step in the matching tab.

Two properties carry the design:

*It labels, it does not merge.* No drawer is renamed, moved or combined, so the
drawer name stays intact as the key it already is for the denylist, Likert item
identity and qualitative scores. A labelling decision can be taken at any point
without orphaning anything recorded earlier.

*A suggestion is only pre-selected when the evidence is unanimous.* Measured on
real data, a split vote put ``Inner Ear_R`` down as a cornea while
``Ear_Internal_R`` sat among the losing candidates.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from autoseg_evaluator.data.organ_index import (  # noqa: E402
    build_organ_index,
    suggest_organ_from_tests,
)
from autoseg_evaluator.data.synonyms import flatten_synonyms, load_synonyms  # noqa: E402
from autoseg_evaluator.ui.dialogs.organ_labels import OrganLabelsDialog  # noqa: E402
from autoseg_evaluator.ui.tabs.match_contours import (  # noqa: E402
    MatchContoursTab,
    PatientSubsection,
)
from autoseg_evaluator.ui.widgets.organ_drawer import TestRow  # noqa: E402
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


def _tests(*names):
    return [
        TestRow(
            source_label=f"vendor{i}",
            organ_name=name,
            rtstruct_sop_uid=f"9.9.{i}",
            roi_number=i + 1,
            similarity=0.7,
            below_threshold=False,
            match_method="fuzzy",
        )
        for i, name in enumerate(names)
    ]


def _drawer(tab, name, gt_name, test_names, patient_id="P1"):
    drawer = tab.add_drawer(name)
    drawer.update_patient(
        PatientSubsection(
            patient_id=patient_id,
            gt_rtstruct_sop_uid="1.2.3",
            gt_rtstruct_filename="gt.dcm",
            gt_source_label="manual",
            gt_roi_number=1,
            gt_roi_name=gt_name,
            tests=_tests(*test_names),
        )
    )
    tab._resync_tree_marks()
    return drawer


def _setup(syn, names):
    lib = _FakeLibrary([("A", [(n, "ORGAN") for n in names])])
    tab = MatchContoursTab()
    tab.set_organ_index(build_organ_index(lib, synonyms_flat=syn))
    return tab


# ---- The suggestion signal ------------------------------------------------


def test_unanimous_tests_identify_the_organ(syn):
    """The reported case: SubmanG_R is not in TG-263, its contours are."""
    lib = _FakeLibrary([("A", [("Glnd_Submand_R", "ORGAN"), ("Submand_Gland_R", "ORGAN")])])
    index = build_organ_index(lib, synonyms_flat=syn)
    suggestion = suggest_organ_from_tests(index, ["Glnd_Submand_R", "Glnd_Submand_R"])
    assert suggestion is not None
    assert suggestion.base == "glnd_submand"
    assert suggestion.unanimous


def test_a_split_vote_is_not_unanimous(syn):
    lib = _FakeLibrary([("A", [("Cornea_R", "ORGAN"), ("Parotid_R", "ORGAN")])])
    index = build_organ_index(lib, synonyms_flat=syn)
    suggestion = suggest_organ_from_tests(index, ["Cornea_R", "Parotid_R"])
    assert suggestion is not None
    assert not suggestion.unanimous
    assert suggestion.tally == "1/2"


def test_unrecognised_tests_cast_no_vote(syn):
    lib = _FakeLibrary([("A", [("Wibble_Xyz", "ORGAN")])])
    index = build_organ_index(lib, synonyms_flat=syn)
    assert suggest_organ_from_tests(index, ["Wibble_Xyz"]) is None


def test_voting_ignores_a_different_kind_of_structure(syn):
    """A target among the contours must not be proposed as the organ."""
    lib = _FakeLibrary([("A", [("Parotid_R", "ORGAN"), ("1CTV_", "CTV")])])
    index = build_organ_index(lib, synonyms_flat=syn)
    suggestion = suggest_organ_from_tests(index, ["Parotid_R", "1CTV_"])
    assert suggestion.base == "parotid"
    assert suggestion.total == 1


# ---- The dialog -----------------------------------------------------------


def test_only_unrecognised_drawers_are_listed(qapp, syn):
    tab = _setup(syn, ["SubmanG_R", "Parotid_L", "Glnd_Submand_R"])
    _drawer(tab, "SubmanG_R", "SubmanG_R", ["Glnd_Submand_R"])
    _drawer(tab, "Parotid_L", "Parotid_L", ["Parotid_L"], patient_id="P2")

    dialog = OrganLabelsDialog(tab._organ_index, tab.drawers(), {})
    listed = {dialog._table.item(r, 0).text() for r in range(dialog._table.rowCount())}
    assert listed == {"SubmanG_R"}, "a recognised drawer needs no labelling"
    dialog.deleteLater()
    tab.deleteLater()


def test_a_unanimous_suggestion_is_preselected(qapp, syn):
    tab = _setup(syn, ["SubmanG_R", "Glnd_Submand_R"])
    _drawer(tab, "SubmanG_R", "SubmanG_R", ["Glnd_Submand_R", "Glnd_Submand_R"])

    dialog = OrganLabelsDialog(tab._organ_index, tab.drawers(), {})
    assert dialog.labels() == {"SubmanG_R": "glnd_submand"}
    dialog.deleteLater()
    tab.deleteLater()


def test_a_split_suggestion_is_offered_but_not_preselected(qapp, syn):
    """It is shown so it can be chosen, never applied on the user's behalf."""
    tab = _setup(syn, ["Inner Ear_R", "Cornea_R", "Parotid_R"])
    _drawer(tab, "Inner Ear_R", "Inner Ear_R", ["Cornea_R", "Parotid_R"])

    dialog = OrganLabelsDialog(tab._organ_index, tab.drawers(), {})
    assert dialog.labels() == {}, "a split vote must not label anything by itself"
    combo = dialog._table.cellWidget(0, 3)
    assert combo.count() > 1, "but the candidate is still there to pick"
    dialog.deleteLater()
    tab.deleteLater()


def test_accepting_suggestions_applies_the_split_ones_too(qapp, syn):
    """Explicitly asking for them is different from having them applied."""
    tab = _setup(syn, ["Inner Ear_R", "Cornea_R", "Parotid_R"])
    _drawer(tab, "Inner Ear_R", "Inner Ear_R", ["Cornea_R", "Parotid_R"])

    dialog = OrganLabelsDialog(tab._organ_index, tab.drawers(), {})
    dialog._on_accept_suggestions()
    assert dialog.labels()
    dialog.deleteLater()
    tab.deleteLater()


def test_leaving_a_drawer_unlabelled_is_valid(qapp, syn):
    tab = _setup(syn, ["Wibble_Xyz"])
    _drawer(tab, "Wibble_Xyz", "Wibble_Xyz", [])

    dialog = OrganLabelsDialog(tab._organ_index, tab.drawers(), {})
    assert dialog.labels() == {}
    assert "1 unrecognised drawer" in dialog._status.text()
    dialog.deleteLater()
    tab.deleteLater()


def test_existing_labels_are_carried_in_and_back(qapp, syn):
    tab = _setup(syn, ["SubmanG_R", "Glnd_Submand_R"])
    _drawer(tab, "SubmanG_R", "SubmanG_R", ["Glnd_Submand_R"])

    existing = {"SubmanG_R": "something_else"}
    dialog = OrganLabelsDialog(tab._organ_index, tab.drawers(), existing)
    assert dialog.labels() == existing, "a previous answer outranks the suggestion"
    dialog.deleteLater()
    tab.deleteLater()


def test_labelling_groups_two_drawers_without_touching_them(qapp, syn):
    """The whole point: pooled for analysis, untouched in the tree."""
    tab = _setup(syn, ["SubmanG_R", "SubmandGland_R", "Glnd_Submand_R"])
    a = _drawer(tab, "SubmanG_R", "SubmanG_R", ["Glnd_Submand_R"], patient_id="P1")
    b = _drawer(tab, "SubmandGland_R", "SubmandGland_R", ["Glnd_Submand_R"], patient_id="P2")

    dialog = OrganLabelsDialog(tab._organ_index, tab.drawers(), {})
    labels = dialog.labels()
    dialog.deleteLater()
    assert labels["SubmanG_R"] == labels["SubmandGland_R"]

    # Both drawers still exist, still named as they were.
    assert {d.organ_name() for d in tab.drawers()} == {"SubmanG_R", "SubmandGland_R"}
    assert a.organ_name() == "SubmanG_R"
    assert b.organ_name() == "SubmandGland_R"

    # And once applied, they carry the same badge.
    lib = _FakeLibrary(
        [("A", [(n, "ORGAN") for n in ("SubmanG_R", "SubmandGland_R", "Glnd_Submand_R")])]
    )
    tab.set_organ_index(build_organ_index(lib, synonyms_flat=syn, manual=labels))
    assert a._organ_badge.text() == b._organ_badge.text()
    assert not a._organ_badge.text().startswith("?")
    tab.deleteLater()


# ---- The button -----------------------------------------------------------


def test_the_button_counts_what_still_needs_labelling(qapp, syn):
    tab = _setup(syn, ["SubmanG_R", "Parotid_L", "Glnd_Submand_R"])
    _drawer(tab, "Parotid_L", "Parotid_L", ["Parotid_L"])
    assert tab._label_organs_btn.text() == "4. Label Organs…"
    assert not tab._label_organs_btn.isEnabled()

    _drawer(tab, "SubmanG_R", "SubmanG_R", ["Glnd_Submand_R"], patient_id="P2")
    assert "(1)" in tab._label_organs_btn.text()
    assert tab._label_organs_btn.isEnabled()
    tab.deleteLater()


def test_labelling_clears_the_count(qapp, syn):
    names = ["SubmanG_R", "Glnd_Submand_R"]
    tab = _setup(syn, names)
    _drawer(tab, "SubmanG_R", "SubmanG_R", ["Glnd_Submand_R"])
    assert "(1)" in tab._label_organs_btn.text()

    lib = _FakeLibrary([("A", [(n, "ORGAN") for n in names])])
    tab.set_organ_index(
        build_organ_index(lib, synonyms_flat=syn, manual={"SubmanG_R": "glnd_submand"})
    )
    assert tab.unrecognised_drawers() == []
    assert "(" not in tab._label_organs_btn.text()
    tab.deleteLater()


def test_only_organs_are_listed(qapp, syn):
    """Targets, couch, rings, dose levels and PRVs want no organ label.

    On a real head-and-neck case those were 22 of the 26 rows, which buries the
    handful that actually need an answer.
    """
    lib = _FakeLibrary(
        [
            (
                "A",
                [
                    ("SubmanG_R", "ORGAN"),
                    ("1GTV_", "GTV"),
                    ("CouchSurface", "SUPPORT"),
                    ("Ring 48Gy", "CONTROL"),
                    ("BrainstemPRV", "ORGAN"),
                    ("BODY -1.0", "ORGAN"),
                ],
            )
        ]
    )
    tab = MatchContoursTab()
    tab.set_organ_index(build_organ_index(lib, synonyms_flat=syn))
    for index, name in enumerate(
        ["SubmanG_R", "1GTV_", "CouchSurface", "Ring 48Gy", "BrainstemPRV", "BODY -1.0"]
    ):
        _drawer(tab, name, name, [], patient_id=f"P{index}")

    dialog = OrganLabelsDialog(tab._organ_index, tab.drawers(), {})
    listed = {dialog._table.item(r, 0).text() for r in range(dialog._table.rowCount())}
    assert listed == {"SubmanG_R"}
    dialog.deleteLater()
    tab.deleteLater()


def test_the_button_count_matches_what_the_dialog_shows(qapp, syn):
    """A count that disagreed with the list would be worse than no count."""
    lib = _FakeLibrary([("A", [("SubmanG_R", "ORGAN"), ("1GTV_", "GTV"), ("Board", "SUPPORT")])])
    tab = MatchContoursTab()
    tab.set_organ_index(build_organ_index(lib, synonyms_flat=syn))
    for index, name in enumerate(["SubmanG_R", "1GTV_", "Board"]):
        _drawer(tab, name, name, [], patient_id=f"P{index}")

    dialog = OrganLabelsDialog(tab._organ_index, tab.drawers(), {})
    assert dialog._table.rowCount() == len(tab.unrecognised_drawers())
    assert "(1)" in tab._label_organs_btn.text()
    dialog.deleteLater()
    tab.deleteLater()
