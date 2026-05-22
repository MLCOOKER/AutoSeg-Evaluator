"""Widget-level tests for Tab 2 components — collapsible box, organ drawer, tree."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout

# Re-use the synthetic DICOM writers from test_metadata.py
from test_metadata import _write_ct_slice, _write_rtstruct  # noqa: E402

from autoseg_evaluator.data.metadata import MetadataLibrary  # noqa: E402
from autoseg_evaluator.ui.widgets.collapsible_box import CollapsibleBox  # noqa: E402
from autoseg_evaluator.ui.widgets.loaded_contours_tree import LoadedContoursTree  # noqa: E402
from autoseg_evaluator.ui.widgets.organ_drawer import (  # noqa: E402
    OrganDrawer,
    PatientSubsection,
    TestRow,
)
from autoseg_evaluator.ui.widgets.signal_bar import SignalBar  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---- CollapsibleBox -------------------------------------------------------


def test_collapsible_box_starts_collapsed(qapp):
    box = CollapsibleBox()
    layout = QVBoxLayout()
    layout.addWidget(QLabel("content"))
    box.setContentLayout(layout)
    assert box.isExpanded() is False


def test_collapsible_box_expand_collapse(qapp):
    box = CollapsibleBox()
    layout = QVBoxLayout()
    layout.addWidget(QLabel("content"))
    box.setContentLayout(layout)

    received: list[bool] = []
    box.toggled.connect(received.append)

    box.expand()
    assert box.isExpanded()
    box.collapse()
    assert not box.isExpanded()
    assert received == [True, False]


def test_collapsible_box_swap_header(qapp):
    box = CollapsibleBox()
    h1 = QLabel("h1")
    box.setHeaderWidget(h1)
    assert box.headerWidget() is h1

    h2 = QLabel("h2")
    box.setHeaderWidget(h2)
    assert box.headerWidget() is h2


# ---- OrganDrawer ----------------------------------------------------------


def _make_subsection(patient_id: str, with_tests: bool = True) -> PatientSubsection:
    tests = []
    if with_tests:
        tests = [
            TestRow("Limbus AI", "Prostate", "sop-limbus", 1, 0.92),
            TestRow("MiM", "Prostate", "sop-mim", 2, 0.88),
            TestRow("VendorE", "ProstateBed", "sop-e", 3, 0.31, below_threshold=True),
        ]
    return PatientSubsection(
        patient_id=patient_id,
        gt_rtstruct_sop_uid="sop-gt",
        gt_rtstruct_filename="manual.dcm",
        gt_source_label="Varian",
        gt_roi_number=1,
        gt_roi_name="Prostate",
        tests=tests,
    )


def test_organ_drawer_starts_empty(qapp):
    d = OrganDrawer("Prostate")
    assert d.organ_name() == "Prostate"
    assert d.patient_count() == 0
    assert d.patient_ids() == []


def test_organ_drawer_add_patient(qapp):
    d = OrganDrawer("Prostate")
    d.add_patient(_make_subsection("HN1"))
    assert d.patient_count() == 1
    assert d.patient_ids() == ["HN1"]


def test_organ_drawer_add_multiple_patients(qapp):
    d = OrganDrawer("Prostate")
    d.add_patient(_make_subsection("HN1"))
    d.add_patient(_make_subsection("HN2"))
    d.add_patient(_make_subsection("HN3", with_tests=False))
    assert d.patient_count() == 3
    assert set(d.patient_ids()) == {"HN1", "HN2", "HN3"}


def test_organ_drawer_remove_patient(qapp):
    d = OrganDrawer("Prostate")
    d.add_patient(_make_subsection("HN1"))
    d.add_patient(_make_subsection("HN2"))
    d.remove_patient("HN1")
    assert d.patient_count() == 1
    assert d.patient_ids() == ["HN2"]


def test_organ_drawer_update_patient_replaces_widget(qapp):
    d = OrganDrawer("Prostate")
    d.add_patient(_make_subsection("HN1", with_tests=False))
    # Now update with a version that has tests
    d.update_patient(_make_subsection("HN1", with_tests=True))
    assert d.patient_count() == 1
    assert d.patient_ids() == ["HN1"]


def test_organ_drawer_truncate_signal(qapp):
    d = OrganDrawer("SpinalCord")
    received: list[bool] = []
    d.truncateChanged.connect(received.append)
    d.set_truncate(True)
    d.set_truncate(False)
    assert received == [True, False]


def test_organ_drawer_focus_toggle_updates_button_label(qapp):
    d = OrganDrawer("Heart")
    # Default label
    assert "(auto" not in d._add_selected_btn.text() if hasattr(d, "_add_selected_btn") else True
    d.set_focused(True)
    assert "Heart" in d._add_selected_btn.text()
    d.set_focused(False)
    assert "Heart" not in d._add_selected_btn.text()


def test_organ_drawer_remove_signal(qapp):
    d = OrganDrawer("Lung")
    fired: list[int] = []
    d.removeRequested.connect(lambda: fired.append(1))
    d._remove_btn.click()
    assert fired == [1]


# ---- SignalBar -----------------------------------------------------------


def test_signal_bar_zero_similarity_has_no_filled_pips(qapp):
    bar = SignalBar(0.0)
    assert bar.filled_pips() == 0


def test_signal_bar_full_similarity_fills_all_pips(qapp):
    bar = SignalBar(1.0)
    assert bar.filled_pips() == SignalBar.PIP_COUNT


def test_signal_bar_clamps_out_of_range(qapp):
    assert SignalBar(-0.5).filled_pips() == 0
    assert SignalBar(1.5).filled_pips() == SignalBar.PIP_COUNT


def test_signal_bar_intermediate_similarity_round_trips(qapp):
    bar = SignalBar(0.5)
    # 0.5 * 8 = 4 pips
    assert bar.filled_pips() == 4
    bar.set_similarity(0.93)
    # 0.93 * 8 = 7.44 → 7 pips
    assert bar.filled_pips() == 7


def test_signal_bar_below_threshold_flag_round_trips(qapp):
    bar = SignalBar(0.3, below_threshold=True)
    assert bar.below_threshold() is True
    bar.set_similarity(0.9, below_threshold=False)
    assert bar.below_threshold() is False


def test_signal_bar_has_fixed_size(qapp):
    bar = SignalBar(0.5)
    # Fixed size so test rows align consistently across drawers
    assert bar.minimumSize() == bar.maximumSize()
    assert bar.minimumSize().width() > 0


# ---- LoadedContoursTree --------------------------------------------------


def test_loaded_contours_tree_populates_from_library(qapp, tmp_path):
    """Build a tiny synthetic library and verify the tree has the right shape."""
    from pydicom.uid import generate_uid

    folder = tmp_path
    study_uid = generate_uid()
    for_uid = generate_uid()
    _write_ct_slice(folder, patient_id="HN1", study_uid=study_uid, series_uid=generate_uid(), for_uid=for_uid)
    _write_rtstruct(
        folder,
        patient_id="HN1",
        study_uid=study_uid,
        for_uid=for_uid,
        manufacturer="Varian",
        organs=["Prostate", "Bladder"],
        filename="manual.dcm",
    )
    _write_rtstruct(
        folder,
        patient_id="HN1",
        study_uid=study_uid,
        for_uid=for_uid,
        manufacturer="Limbus",
        organs=["Prostate", "Rectum"],
        filename="limbus.dcm",
    )
    lib = MetadataLibrary()
    lib.scan_folder(str(folder))

    tree = LoadedContoursTree()
    tree.populate(lib)
    assert tree.topLevelItemCount() == 1  # one patient

    patient_item = tree.topLevelItem(0)
    # Single context → context layer is skipped, RTSTRUCT children attach directly
    assert patient_item.childCount() == 2  # two RTSTRUCTs
    # Each RTSTRUCT carries its organs as children
    rtss_item = patient_item.child(0)
    organ_names = [rtss_item.child(i).data(0, 261) for i in range(rtss_item.childCount())]  # ROLE_ROI_NAME
    assert set(organ_names) >= {"Prostate"}  # Prostate appears in both


def test_loaded_contours_tree_marks_organs(qapp, tmp_path):
    from pydicom.uid import generate_uid

    folder = tmp_path
    study_uid = generate_uid()
    for_uid = generate_uid()
    _write_ct_slice(folder, patient_id="HN1", study_uid=study_uid, series_uid=generate_uid(), for_uid=for_uid)
    _write_rtstruct(
        folder,
        patient_id="HN1",
        study_uid=study_uid,
        for_uid=for_uid,
        manufacturer="Varian",
        organs=["Prostate"],
        filename="manual.dcm",
    )
    lib = MetadataLibrary()
    lib.scan_folder(str(folder))
    rtss = lib.all_rtstructs()[0]
    organ = rtss.organs[0]

    tree = LoadedContoursTree()
    tree.populate(lib)
    tree.mark_organ_assigned("HN1", rtss.sop_instance_uid, organ.roi_number)

    # Walk the tree to find the organ leaf and confirm it now has the ✓ prefix
    found = False
    p = tree.topLevelItem(0)
    for i in range(p.childCount()):
        rtss_item = p.child(i)
        for j in range(rtss_item.childCount()):
            org = rtss_item.child(j)
            if "✓" in org.text(0):
                found = True
    assert found
