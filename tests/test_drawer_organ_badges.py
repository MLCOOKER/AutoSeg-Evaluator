"""Tests for the canonical organ badge on each drawer.

The ordering is the thing these exist for. The organ index is built when a
folder loads, which is *before* auto-match has created a single drawer, so a
badge refresh that only runs when the index arrives leaves every drawer blank
forever — which is exactly what shipped and had to be reported by hand.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from autoseg_evaluator.data.organ_index import build_organ_index  # noqa: E402
from autoseg_evaluator.data.synonyms import flatten_synonyms, load_synonyms  # noqa: E402
from autoseg_evaluator.ui.tabs.match_contours import (  # noqa: E402
    MatchContoursTab,
    PatientSubsection,
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


def _add_patient(tab, drawer_name, patient_id, gt_roi_name):
    drawer = tab.add_drawer(drawer_name)
    drawer.update_patient(
        PatientSubsection(
            patient_id=patient_id,
            gt_rtstruct_sop_uid="1.2.3",
            gt_rtstruct_filename="gt.dcm",
            gt_source_label="manual",
            gt_roi_number=1,
            gt_roi_name=gt_roi_name,
            tests=[],
        )
    )
    tab._resync_tree_marks()
    return drawer


def _badge(drawer) -> str:
    return drawer._organ_badge.text()


def test_badge_appears_when_drawers_are_created_after_the_index(qapp, syn):
    """The reported failure: index first, drawers second, badges never."""
    lib = _FakeLibrary([("A", [("Eye_L", "ORGAN")])])
    tab = MatchContoursTab()
    tab.set_organ_index(build_organ_index(lib, synonyms_flat=syn))

    drawer = _add_patient(tab, "Eye_L", "P1", "Eye_L")
    assert _badge(drawer), "a drawer created after the index must still be badged"
    assert "Eye" in _badge(drawer)
    tab.deleteLater()


def test_badge_appears_when_the_index_arrives_after_the_drawers(qapp, syn):
    """The other order — a rescan while drawers already exist."""
    lib = _FakeLibrary([("A", [("Eye_L", "ORGAN")])])
    tab = MatchContoursTab()
    drawer = _add_patient(tab, "Eye_L", "P1", "Eye_L")
    assert _badge(drawer) == ""

    tab.set_organ_index(build_organ_index(lib, synonyms_flat=syn))
    assert "Eye" in _badge(drawer)
    tab.deleteLater()


def test_badge_shows_the_side(qapp, syn):
    lib = _FakeLibrary([("A", [("Eye_L", "ORGAN"), ("Eye_R", "ORGAN")])])
    tab = MatchContoursTab()
    tab.set_organ_index(build_organ_index(lib, synonyms_flat=syn))

    left = _add_patient(tab, "Eye_L", "P1", "Eye_L")
    right = _add_patient(tab, "Eye_R", "P2", "Eye_R")
    assert "(L)" in _badge(left)
    assert "(R)" in _badge(right)
    assert _badge(left) != _badge(right)
    tab.deleteLater()


def test_differently_named_drawers_show_the_same_badge(qapp, syn):
    """The point of the badge: two drawers, one organ, grouped together."""
    lib = _FakeLibrary([("A", [("Oral cavity", "ORGAN"), ("Cavity_Oral", "ORGAN")])])
    tab = MatchContoursTab()
    tab.set_organ_index(build_organ_index(lib, synonyms_flat=syn))

    a = _add_patient(tab, "Oral cavity", "P1", "Oral cavity")
    b = _add_patient(tab, "Cavity_Oral", "P2", "Cavity_Oral")
    assert a.organ_name() != b.organ_name()
    assert _badge(a) == _badge(b)
    tab.deleteLater()


def test_a_drawer_holding_both_sides_is_flagged(qapp, syn):
    """A matching error with no other way of being noticed."""
    lib = _FakeLibrary([("A", [("Eye_L", "ORGAN"), ("Eye_R", "ORGAN")])])
    tab = MatchContoursTab()
    tab.set_organ_index(build_organ_index(lib, synonyms_flat=syn))

    drawer = _add_patient(tab, "Eye_L", "P1", "Eye_L")
    assert "mixed" not in _badge(drawer)

    # Second patient's ground truth is the opposite side.
    drawer.update_patient(
        PatientSubsection(
            patient_id="P2",
            gt_rtstruct_sop_uid="1.2.3",
            gt_rtstruct_filename="gt.dcm",
            gt_source_label="manual",
            gt_roi_number=2,
            gt_roi_name="Eye_R",
            tests=[],
        )
    )
    tab._resync_tree_marks()
    assert "mixed" in _badge(drawer)
    tab.deleteLater()


def test_no_index_means_no_badge(qapp, syn):
    """Before a folder is loaded there is nothing to say, so say nothing."""
    tab = MatchContoursTab()
    drawer = _add_patient(tab, "Eye_L", "P1", "Eye_L")
    assert _badge(drawer) == ""
    tab.deleteLater()


def test_an_unknown_name_still_gets_a_badge(qapp, syn):
    """An unresolved organ is its own group, not an absent one."""
    lib = _FakeLibrary([("A", [("Wibble_Xyz", "ORGAN")])])
    tab = MatchContoursTab()
    tab.set_organ_index(build_organ_index(lib, synonyms_flat=syn))
    drawer = _add_patient(tab, "Wibble_Xyz", "P1", "Wibble_Xyz")
    assert _badge(drawer)
    tab.deleteLater()
