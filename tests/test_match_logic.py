"""Step 4 interaction tests for Tab 2 — GT setting, Add Selected routing,
drag-drop, focus-aware Add, remove handlers, and Remove All Poor Matches.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication

from autoseg_evaluator.data.metadata import MetadataLibrary  # noqa: E402
from autoseg_evaluator.ui.tabs.match_contours import MatchContoursTab  # noqa: E402
from autoseg_evaluator.ui.widgets.organ_drawer import ORGAN_MIME_TYPE  # noqa: E402
from test_metadata import _write_ct_slice, _write_rtstruct  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def populated_library(tmp_path):
    """Build a small library: 2 patients, each with a manual GT and 2 AI test RTSTRUCTs."""
    from pydicom.uid import generate_uid

    folder = tmp_path
    libs_organs = ["Prostate", "Bladder", "Rectum"]
    for pid in ("HN1", "HN2"):
        study_uid = generate_uid()
        for_uid = generate_uid()
        _write_ct_slice(
            folder, patient_id=pid, study_uid=study_uid, series_uid=generate_uid(), for_uid=for_uid
        )
        _write_rtstruct(
            folder,
            patient_id=pid,
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="Varian",
            organs=libs_organs,
            filename=f"manual_{pid}.dcm",
        )
        _write_rtstruct(
            folder,
            patient_id=pid,
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="Limbus",
            organs=libs_organs + ["ProstateCTV"],  # extra organ
            filename=f"limbus_{pid}.dcm",
        )
        _write_rtstruct(
            folder,
            patient_id=pid,
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="MiM",
            organs=libs_organs,
            filename=f"mim_{pid}.dcm",
        )

    lib = MetadataLibrary()
    lib.scan_folder(str(folder))
    return lib


def _make_tab(qapp, library):
    tab = MatchContoursTab(settings={"tolerances": {"similarity_threshold": 0.6}})
    tab.set_library(library)
    return tab


def _select_organs(tree, predicate):
    """Programmatically select organ leaves where ``predicate(item)`` is True."""
    tree.clearSelection()
    for top_idx in range(tree.topLevelItemCount()):
        _walk_select(tree.topLevelItem(top_idx), predicate)


def _walk_select(item, predicate):
    if item.data(0, Qt.ItemDataRole.UserRole) == "organ":
        if predicate(item):
            item.setSelected(True)
    for i in range(item.childCount()):
        _walk_select(item.child(i), predicate)


def _organ_label(item) -> str:
    return str(item.data(0, Qt.ItemDataRole.UserRole + 5) or "")


def _patient_id(item) -> str:
    return str(item.data(0, Qt.ItemDataRole.UserRole + 1) or "")


def _rtss_filename(item):
    # Walk up to RTSTRUCT parent and read its display text
    parent = item.parent()
    return parent.text(0) if parent else ""


# ---- Set Ground Truth ----------------------------------------------------


def test_set_gt_creates_drawer_and_marks_organ(qapp, populated_library):
    tab = _make_tab(qapp, populated_library)
    # Select the Prostate organ from HN1's manual.dcm only
    _select_organs(
        tab._tree,
        lambda it: (
            _patient_id(it) == "HN1"
            and _organ_label(it) == "Prostate"
            and "manual_HN1" in _rtss_filename(it)
        ),
    )
    tab._on_set_ground_truth_clicked()
    assert "Prostate" in tab._drawers
    drawer = tab._drawers["Prostate"]
    assert drawer.patient_count() == 1
    assert drawer.patient_ids() == ["HN1"]
    sub = drawer.patient_subsection("HN1")
    assert sub is not None
    assert sub.gt_source_label == "Varian"
    assert sub.gt_roi_name == "Prostate"


def test_set_gt_for_multiple_patients_groups_into_same_drawer(qapp, populated_library):
    tab = _make_tab(qapp, populated_library)
    _select_organs(
        tab._tree,
        lambda it: _organ_label(it) == "Prostate" and "manual_" in _rtss_filename(it),
    )
    tab._on_set_ground_truth_clicked()
    drawer = tab._drawers["Prostate"]
    assert set(drawer.patient_ids()) == {"HN1", "HN2"}


def test_set_gt_idempotent_preserves_tests(qapp, populated_library):
    """Re-running Set GT for a patient that already has one preserves its tests."""
    tab = _make_tab(qapp, populated_library)
    _set_basic_gt(tab, "Prostate", "HN1")  # auto-match populates tests
    sub = tab._drawers["Prostate"].patient_subsection("HN1")
    initial_count = len(sub.tests)
    initial_sops = sorted(t.rtstruct_sop_uid for t in sub.tests)
    assert initial_count > 0  # sanity: auto-match ran
    # Re-set GT for the same patient — must not duplicate or wipe tests
    _set_basic_gt(tab, "Prostate", "HN1")
    sub = tab._drawers["Prostate"].patient_subsection("HN1")
    assert len(sub.tests) == initial_count
    assert sorted(t.rtstruct_sop_uid for t in sub.tests) == initial_sops


# ---- Add Selected --------------------------------------------------------


def _set_basic_gt(tab, organ: str = "Prostate", patient_id: str = "HN1"):
    _select_organs(
        tab._tree,
        lambda it: (
            _patient_id(it) == patient_id
            and _organ_label(it) == organ
            and "manual_" in _rtss_filename(it)
        ),
    )
    tab._on_set_ground_truth_clicked()


def test_set_gt_auto_matches_tests_from_other_rtss(qapp, populated_library):
    """Setting a GT should immediately auto-match the closest organ in every other RTSS file."""
    tab = _make_tab(qapp, populated_library)
    _set_basic_gt(tab, "Prostate", "HN1")
    sub = tab._drawers["Prostate"].patient_subsection("HN1")
    # 3 RTSS files total per patient (manual + limbus + mim); GT is from manual,
    # so 2 other RTSS files contribute one test each.
    assert len(sub.tests) == 2
    sources = sorted(t.source_label for t in sub.tests)
    assert sources == ["Limbus", "MiM"]
    # Both have exact "Prostate" in them → similarity 1.0
    assert all(t.similarity == pytest.approx(1.0) for t in sub.tests)


def test_set_gt_auto_matches_have_no_threshold_filter(qapp, populated_library):
    """Best match is always added even when similarity is very low."""
    import tempfile
    from pathlib import Path

    from pydicom.uid import generate_uid

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        study_uid = generate_uid()
        for_uid = generate_uid()
        _write_ct_slice(
            folder,
            patient_id="HN1",
            study_uid=study_uid,
            series_uid=generate_uid(),
            for_uid=for_uid,
        )
        _write_rtstruct(
            folder,
            patient_id="HN1",
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="Varian",
            organs=["Prostate"],
            filename="manual_HN1.dcm",
        )
        _write_rtstruct(
            folder,
            patient_id="HN1",
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="Junk",
            organs=["xyzqrt"],
            filename="junk_HN1.dcm",
        )
        lib = MetadataLibrary()
        lib.scan_folder(str(folder))
        tab = _make_tab(qapp, lib)
        _set_basic_gt(tab, "Prostate", "HN1")
        sub = tab._drawers["Prostate"].patient_subsection("HN1")
        assert len(sub.tests) == 1
        assert sub.tests[0].organ_name == "xyzqrt"
        assert sub.tests[0].below_threshold is True


def test_add_selected_disabled_without_focus(qapp, populated_library):
    """The Add Selected button is disabled when no drawer is focused."""
    tab = _make_tab(qapp, populated_library)
    assert not tab._add_selected_btn.isEnabled()
    _set_basic_gt(tab, "Prostate", "HN1")
    # Still no focus → still disabled
    assert not tab._add_selected_btn.isEnabled()
    tab._drawers["Prostate"].set_focused(True)
    assert tab._add_selected_btn.isEnabled()
    tab._drawers["Prostate"].set_focused(False)
    assert not tab._add_selected_btn.isEnabled()


def test_add_selected_no_focus_is_a_noop(qapp, populated_library):
    tab = _make_tab(qapp, populated_library)
    _set_basic_gt(tab, "Prostate", "HN1")
    initial_test_count = len(tab._drawers["Prostate"].patient_subsection("HN1").tests)
    _select_organs(
        tab._tree,
        lambda it: (
            _patient_id(it) == "HN1"
            and _organ_label(it) == "Prostate"
            and "mim_HN1" in _rtss_filename(it)
        ),
    )
    tab._on_add_selected_clicked()  # no focus
    assert len(tab._drawers["Prostate"].patient_subsection("HN1").tests) == initial_test_count
    assert "Focus a drawer" in tab._status_label.text()


def test_add_selected_focused_drawer_routes_regardless_of_name(qapp, populated_library):
    """With Prostate drawer focused, an organ named differently should land in it."""
    tab = _make_tab(qapp, populated_library)
    _set_basic_gt(tab, "Prostate", "HN1")
    drawer = tab._drawers["Prostate"]
    drawer.set_focused(True)
    # Select an organ whose name doesn't match "Prostate"
    _select_organs(
        tab._tree,
        lambda it: (
            _patient_id(it) == "HN1"
            and _organ_label(it) == "ProstateCTV"
            and "limbus_HN1" in _rtss_filename(it)
        ),
    )
    tab._on_add_selected_clicked()
    sub = drawer.patient_subsection("HN1")
    # 2 auto-matched + 1 manually added = 3
    assert any(t.organ_name == "ProstateCTV" for t in sub.tests)


def test_add_selected_skips_when_no_gt_for_patient(qapp, populated_library):
    """With a drawer focused but no GT for the target patient, the add is skipped."""
    tab = _make_tab(qapp, populated_library)
    _set_basic_gt(tab, "Prostate", "HN1")
    tab._drawers["Prostate"].set_focused(True)
    # Try to add HN2's Prostate (no GT for HN2 in this drawer)
    _select_organs(
        tab._tree,
        lambda it: (
            _patient_id(it) == "HN2"
            and _organ_label(it) == "Prostate"
            and "limbus_HN2" in _rtss_filename(it)
        ),
    )
    tab._on_add_selected_clicked()
    sub_hn2 = tab._drawers["Prostate"].patient_subsection("HN2")
    assert sub_hn2 is None
    assert "no GT" in tab._status_label.text()


def test_add_selected_dedupes(qapp, populated_library):
    """Adding the same test row twice should not create duplicates."""
    tab = _make_tab(qapp, populated_library)
    _set_basic_gt(tab, "Prostate", "HN1")
    # Auto-match already added Limbus + MiM. Try to add Limbus Prostate again manually.
    tab._drawers["Prostate"].set_focused(True)
    _select_organs(
        tab._tree,
        lambda it: (
            _patient_id(it) == "HN1"
            and _organ_label(it) == "Prostate"
            and "limbus_HN1" in _rtss_filename(it)
        ),
    )
    tab._on_add_selected_clicked()
    sub = tab._drawers["Prostate"].patient_subsection("HN1")
    assert len([t for t in sub.tests if t.source_label == "Limbus"]) == 1
    assert "already added" in tab._status_label.text()


def test_focus_does_not_change_expansion(qapp, populated_library):
    """Toggling focus must not collapse or expand the drawer."""
    tab = _make_tab(qapp, populated_library)
    _set_basic_gt(tab, "Prostate", "HN1")
    drawer = tab._drawers["Prostate"]
    # New drawers start collapsed; verify focus toggling alone never expands.
    assert not drawer.isExpanded()
    drawer.set_focused(True)
    assert not drawer.isExpanded()
    assert drawer.is_focused()
    drawer.set_focused(False)
    assert not drawer.isExpanded()


def test_run_auto_match_populates_drawers_for_template_organs(qapp, populated_library):
    """Given a template + GT criterion, Run Auto-Match should set GTs for every patient."""
    tab = _make_tab(qapp, populated_library)
    tab._settings["last_template"] = {
        "organs": ["Prostate", "Bladder"],
        "gt_manufacturer": "Varian",
        "gt_filename": "",
        "similarity_threshold": 0.6,
    }
    tab._on_run_auto_match_clicked()
    assert "Prostate" in tab._drawers
    assert "Bladder" in tab._drawers
    # Both patients (HN1 + HN2) should have their GT set in each drawer
    for organ in ("Prostate", "Bladder"):
        d = tab._drawers[organ]
        assert set(d.patient_ids()) == {"HN1", "HN2"}
        # Each patient gets auto-matched tests from the 2 non-Varian RTSS
        for pid in d.patient_ids():
            sub = d.patient_subsection(pid)
            assert len(sub.tests) == 2


def test_run_auto_match_warns_when_no_template_defined(qapp, populated_library):
    tab = _make_tab(qapp, populated_library)
    tab._settings["last_template"] = {"organs": [], "gt_manufacturer": "", "gt_filename": ""}
    tab._on_run_auto_match_clicked()
    assert tab._drawers == {}
    assert "No template defined" in tab._status_label.text()


def test_run_auto_match_warns_when_no_gt_criterion(qapp, populated_library):
    tab = _make_tab(qapp, populated_library)
    tab._settings["last_template"] = {
        "organs": ["Prostate"],
        "gt_manufacturer": "",
        "gt_filename": "",
        "similarity_threshold": 0.6,
    }
    tab._on_run_auto_match_clicked()
    assert tab._drawers == {}
    assert "Manufacturer or filename criterion" in tab._status_label.text()


def test_run_auto_match_skips_patients_with_no_gt_rtss(qapp, populated_library):
    tab = _make_tab(qapp, populated_library)
    tab._settings["last_template"] = {
        "organs": ["Prostate"],
        "gt_manufacturer": "NonExistentVendor",
        "gt_filename": "",
        "similarity_threshold": 0.6,
    }
    tab._on_run_auto_match_clicked()
    # No GT can be identified → no drawers
    assert tab._drawers == {}
    assert "no matching GTs" in tab._status_label.text()


def test_replacement_rules_affect_auto_match(qapp, tmp_path):
    """A replacement rule should make a mismatched organ score 1.0."""
    from pydicom.uid import generate_uid

    folder = tmp_path
    study_uid = generate_uid()
    for_uid = generate_uid()
    _write_ct_slice(
        folder, patient_id="HN1", study_uid=study_uid, series_uid=generate_uid(), for_uid=for_uid
    )
    _write_rtstruct(
        folder,
        patient_id="HN1",
        study_uid=study_uid,
        for_uid=for_uid,
        manufacturer="Varian",
        organs=["Musc_Constrict"],
        filename="manual_HN1.dcm",
    )
    _write_rtstruct(
        folder,
        patient_id="HN1",
        study_uid=study_uid,
        for_uid=for_uid,
        manufacturer="VendorB",
        organs=["Pharyngeal_Constrictor"],
        filename="ai_HN1.dcm",
    )
    lib = MetadataLibrary()
    lib.scan_folder(str(folder))

    # Without a rule, similarity is poor and the test row carries the warning flag.
    tab_no_rules = _make_tab(qapp, lib)
    _select_organs(tab_no_rules._tree, lambda it: _organ_label(it) == "Musc_Constrict")
    tab_no_rules._on_set_ground_truth_clicked()
    sub_no_rules = tab_no_rules._drawers["Musc_Constrict"].patient_subsection("HN1")
    assert sub_no_rules.tests[0].similarity < 0.6
    assert sub_no_rules.tests[0].below_threshold

    # With a rule rewriting Musc_Constrict → Pharyngeal_Constrictor, score becomes 1.0
    tab_with_rules = _make_tab(qapp, lib)
    tab_with_rules._settings["replacement_rules"] = [
        {"find": "musc_constrict", "replace": "pharyngeal_constrictor"}
    ]
    _select_organs(tab_with_rules._tree, lambda it: _organ_label(it) == "Musc_Constrict")
    tab_with_rules._on_set_ground_truth_clicked()
    sub_with_rules = tab_with_rules._drawers["Musc_Constrict"].patient_subsection("HN1")
    assert sub_with_rules.tests[0].similarity == 1.0
    assert not sub_with_rules.tests[0].below_threshold


def test_loaded_contours_tree_starts_collapsed(qapp, populated_library):
    """After populate(), all patients in the tree should be collapsed (not auto-expanded)."""
    tab = _make_tab(qapp, populated_library)
    for i in range(tab._tree.topLevelItemCount()):
        assert not tab._tree.topLevelItem(i).isExpanded()


def test_set_gt_uses_strict_literal_drawer_keys(qapp, tmp_path):
    """Differently-spelled GT names produce separate drawers (no fuzzy/normalisation merge).

    Some superficially-similar names denote anatomically distinct organs
    (e.g. ``Eye_L`` vs ``Eye_R``, ``OpticNerve_L`` vs ``OpticNerve_R``), so
    drawer assignment uses the exact ROI name as the key.
    """
    from pydicom.uid import generate_uid

    folder = tmp_path
    study_uid_hn1 = generate_uid()
    for_uid_hn1 = generate_uid()
    _write_ct_slice(
        folder,
        patient_id="HN1",
        study_uid=study_uid_hn1,
        series_uid=generate_uid(),
        for_uid=for_uid_hn1,
    )
    _write_rtstruct(
        folder,
        patient_id="HN1",
        study_uid=study_uid_hn1,
        for_uid=for_uid_hn1,
        manufacturer="VendorA",
        organs=["Brainstem"],
        filename="manual_HN1.dcm",
    )
    study_uid_hn2 = generate_uid()
    for_uid_hn2 = generate_uid()
    _write_ct_slice(
        folder,
        patient_id="HN2",
        study_uid=study_uid_hn2,
        series_uid=generate_uid(),
        for_uid=for_uid_hn2,
    )
    _write_rtstruct(
        folder,
        patient_id="HN2",
        study_uid=study_uid_hn2,
        for_uid=for_uid_hn2,
        manufacturer="VendorB",
        organs=["Brain Stem"],
        filename="manual_HN2.dcm",  # space variant
    )
    lib = MetadataLibrary()
    lib.scan_folder(str(folder))
    tab = _make_tab(qapp, lib)

    _select_organs(
        tab._tree,
        lambda it: _patient_id(it) == "HN1" and _organ_label(it) == "Brainstem",
    )
    tab._on_set_ground_truth_clicked()
    _select_organs(
        tab._tree,
        lambda it: _patient_id(it) == "HN2" and _organ_label(it) == "Brain Stem",
    )
    tab._on_set_ground_truth_clicked()

    # Strict literal keying means two distinct drawers.
    assert set(tab._drawers) == {"Brainstem", "Brain Stem"}


def test_set_gt_does_not_merge_genuinely_different_organs(qapp, populated_library):
    """Drawers for distinct organs (Prostate, Bladder) should stay separate."""
    tab = _make_tab(qapp, populated_library)
    _set_basic_gt(tab, "Prostate", "HN1")
    _set_basic_gt(tab, "Bladder", "HN1")
    assert set(tab._drawers) == {"Prostate", "Bladder"}


def test_loaded_contours_tree_sorts_organs_alphabetically(qapp, tmp_path):
    """Organs under each RTSTRUCT must be displayed in alphabetical order regardless of file order."""
    from pydicom.uid import generate_uid

    folder = tmp_path
    study_uid = generate_uid()
    for_uid = generate_uid()
    _write_ct_slice(
        folder, patient_id="HN1", study_uid=study_uid, series_uid=generate_uid(), for_uid=for_uid
    )
    # Write the RTSTRUCT with organs in deliberately-jumbled order
    _write_rtstruct(
        folder,
        patient_id="HN1",
        study_uid=study_uid,
        for_uid=for_uid,
        manufacturer="Varian",
        organs=["Rectum", "Bladder", "Prostate", "Cord"],
        filename="manual.dcm",
    )
    lib = MetadataLibrary()
    lib.scan_folder(str(folder))
    tab = _make_tab(qapp, lib)
    patient_item = tab._tree.topLevelItem(0)
    rtss_item = patient_item.child(0)
    names = [rtss_item.child(i).text(0).replace("✓ ", "") for i in range(rtss_item.childCount())]
    assert names == sorted(names, key=str.lower)
    assert names == ["Bladder", "Cord", "Prostate", "Rectum"]


# ---- Drag-and-drop -------------------------------------------------------


def test_drop_on_drawer_routes_organs_to_that_drawer(qapp, populated_library):
    """Dropping ProstateCTV from limbus_HN1 onto the Prostate drawer adds a new test row."""
    tab = _make_tab(qapp, populated_library)
    _set_basic_gt(tab, "Prostate", "HN1")
    drawer = tab._drawers["Prostate"]
    initial_count = len(drawer.patient_subsection("HN1").tests)

    sop = _find_first_test_sop(populated_library, "HN1", "limbus")
    roi_n = _find_organ_roi_number(populated_library, "HN1", "limbus", "ProstateCTV")
    payload = json.dumps(
        [
            {
                "patient_id": "HN1",
                "rtstruct_sop_uid": sop,
                "roi_number": roi_n,
                "roi_name": "ProstateCTV",
            }
        ]
    ).encode("utf-8")
    mime = QMimeData()
    mime.setData(ORGAN_MIME_TYPE, payload)

    from PySide6.QtCore import QPointF

    drop = QDropEvent(
        QPointF(0, 0),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    drawer.dropEvent(drop)

    sub = drawer.patient_subsection("HN1")
    assert len(sub.tests) == initial_count + 1
    assert any(t.organ_name == "ProstateCTV" for t in sub.tests)


def _find_first_test_sop(library, patient_id: str, source_marker: str) -> str:
    for ctx in library.patients[patient_id].contexts:
        for rtss in ctx.rtstructs:
            if source_marker in rtss.filename:
                return rtss.sop_instance_uid
    raise LookupError(f"No RTSS matching {source_marker} for {patient_id}")


def _find_organ_roi_number(library, patient_id: str, source_marker: str, roi_name: str) -> int:
    for ctx in library.patients[patient_id].contexts:
        for rtss in ctx.rtstructs:
            if source_marker in rtss.filename:
                for organ in rtss.organs:
                    if organ.roi_name == roi_name:
                        return organ.roi_number
    raise LookupError(f"No organ {roi_name!r} in {source_marker} for {patient_id}")


# ---- Remove handlers ----------------------------------------------------


def test_remove_drawer_unmarks_all_organs(qapp, populated_library):
    tab = _make_tab(qapp, populated_library)
    _set_basic_gt(tab, "Prostate", "HN1")
    # Auto-match populated GT + 2 test rows → 3 marked organs
    assert len(tab._tree._marked_organs) == 3
    tab._on_remove_drawer("Prostate")
    assert "Prostate" not in tab._drawers
    assert tab._tree._marked_organs == set()


def test_remove_test_unmarks_only_that_test(qapp, populated_library):
    tab = _make_tab(qapp, populated_library)
    _set_basic_gt(tab, "Prostate", "HN1")
    sub = tab._drawers["Prostate"].patient_subsection("HN1")
    initial_count = len(sub.tests)
    target = sub.tests[0]
    sop, roi_n = target.rtstruct_sop_uid, target.roi_number

    tab._on_remove_test("Prostate", "HN1", sop, roi_n)

    sub_after = tab._drawers["Prostate"].patient_subsection("HN1")
    assert len(sub_after.tests) == initial_count - 1
    assert ("HN1", sop, roi_n) not in tab._tree._marked_organs
    # GT is still marked
    assert (
        "HN1",
        sub_after.gt_rtstruct_sop_uid,
        sub_after.gt_roi_number,
    ) in tab._tree._marked_organs


def test_remove_patient_auto_collapses_empty_drawer(qapp, populated_library):
    tab = _make_tab(qapp, populated_library)
    _set_basic_gt(tab, "Prostate", "HN1")
    tab._on_remove_patient("Prostate", "HN1")
    assert "Prostate" not in tab._drawers  # auto-cleanup


# ---- Remove All Poor Matches --------------------------------------------


def test_remove_all_poor_matches_strips_below_threshold(qapp, populated_library):
    tab = _make_tab(qapp, populated_library)
    _set_basic_gt(tab, "Prostate", "HN1")
    drawer = tab._drawers["Prostate"]
    sub = drawer.patient_subsection("HN1")
    # Inject one good and one poor test directly
    from autoseg_evaluator.ui.widgets.organ_drawer import TestRow

    sub.tests = [
        TestRow("Good", "Prostate", "sop-good", 1, 0.95, below_threshold=False),
        TestRow("Bad", "Prostate", "sop-bad", 2, 0.30, below_threshold=True),
    ]
    drawer.update_patient(sub)
    tab._tree.mark_organ_assigned("HN1", "sop-good", 1)
    tab._tree.mark_organ_assigned("HN1", "sop-bad", 2)

    tab._on_remove_all_red()
    sub_after = drawer.patient_subsection("HN1")
    assert len(sub_after.tests) == 1
    assert sub_after.tests[0].source_label == "Good"
    # The bad test should no longer be ✓-marked in the tree
    assert ("HN1", "sop-bad", 2) not in tab._tree._marked_organs
    assert ("HN1", "sop-good", 1) in tab._tree._marked_organs


# ---- Focus-aware Add Selected button label ------------------------------


def test_focusing_drawer_updates_button_label(qapp, populated_library):
    tab = _make_tab(qapp, populated_library)
    _set_basic_gt(tab, "Prostate", "HN1")
    drawer = tab._drawers["Prostate"]
    drawer.set_focused(True)
    assert "Prostate" in tab._add_selected_btn.text()
    assert tab._add_selected_btn.isEnabled()
    drawer.set_focused(False)
    assert "focus a drawer" in tab._add_selected_btn.text()
    assert not tab._add_selected_btn.isEnabled()


def test_focus_is_exclusive_across_drawers(qapp, populated_library):
    tab = _make_tab(qapp, populated_library)
    _set_basic_gt(tab, "Prostate", "HN1")
    _set_basic_gt(tab, "Bladder", "HN1")
    a = tab._drawers["Prostate"]
    b = tab._drawers["Bladder"]
    a.set_focused(True)
    assert tab._focused_drawer is a
    b.set_focused(True)
    assert tab._focused_drawer is b
    assert not a.is_focused()
