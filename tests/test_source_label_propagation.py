"""Tests for the 'Select similar' assisted-propagation field cascade.

Covers the pure, Qt-free helpers in the Manage Source Labels dialog that
decide which distinguishing field identifies an observer:
:func:`best_distinguishing_field` and :func:`_field_value_of`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
from pydicom.uid import generate_uid  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from autoseg_evaluator.data.metadata import MetadataLibrary, RTSTRUCTEntry  # noqa: E402
from autoseg_evaluator.ui.dialogs.source_labels import (  # noqa: E402
    ManageSourceLabelsDialog,
    _field_value_of,
    _GroupByColumnDialog,
    best_distinguishing_field,
    recommend_group_attr,
)
from test_metadata import _write_ct_slice, _write_rtstruct  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _rtss(**overrides) -> RTSTRUCTEntry:
    base = {
        "sop_instance_uid": "1.2.3",
        "file_path": "/data/ObserverA/rs_file.dcm",
        "manufacturer": "",
        "source_label": "x",
        "source_origin": "mfr",
        "frame_of_reference_uid": "for",
        "study_instance_uid": "study",
    }
    base.update(overrides)
    return RTSTRUCTEntry(**base)


# ---- _field_value_of -----------------------------------------------------


def test_field_value_reads_plain_attr():
    r = _rtss(operators_name="  JK  ")
    assert _field_value_of(r, "operators_name") == "JK"  # stripped


def test_field_value_filename_stem_drops_extension():
    r = _rtss(file_path="/data/x/rs_observerA.dcm")
    assert _field_value_of(r, "_filename_stem") == "rs_observerA"


def test_field_value_missing_attr_is_empty():
    r = _rtss()
    assert _field_value_of(r, "reviewer_name") == ""


# ---- best_distinguishing_field cascade -----------------------------------


def test_cascade_prefers_parent_folder():
    # file lives directly under ObserverA → parent_folder == "ObserverA"
    r = _rtss(file_path="/data/ObserverA/rs.dcm", operators_name="JK", reviewer_name="Smith")
    label, attr, value = best_distinguishing_field(r)
    assert attr == "parent_folder"
    assert value == "ObserverA"
    assert label == "Parent folder"


def test_cascade_falls_through_to_operators_when_no_folder():
    # parent dir empty string only when file_path has no dir; emulate by
    # using a bare filename so dirname is "" → parent_folder == "".
    r = _rtss(file_path="rs.dcm", operators_name="JK", reviewer_name="Smith")
    label, attr, value = best_distinguishing_field(r)
    assert attr == "operators_name"
    assert value == "JK"


def test_cascade_falls_through_to_reviewer_then_structname():
    r = _rtss(file_path="rs.dcm", reviewer_name="Dr Lee", structure_set_name="LEE_HN")
    _label, attr, value = best_distinguishing_field(r)
    assert attr == "reviewer_name"
    assert value == "Dr Lee"


def test_cascade_falls_through_to_manufacturer():
    r = _rtss(file_path="rs.dcm", manufacturer="Eclipse")
    _label, attr, value = best_distinguishing_field(r)
    assert attr == "manufacturer"
    assert value == "Eclipse"


def test_cascade_last_resort_filename_stem():
    # Nothing but a filename → filename stem.
    r = _rtss(file_path="rs_unique.dcm")
    _label, attr, value = best_distinguishing_field(r)
    assert attr == "_filename_stem"
    assert value == "rs_unique"


def test_cascade_empty_when_nothing_present():
    r = _rtss(file_path="")
    label, attr, value = best_distinguishing_field(r)
    assert (label, attr, value) == ("", "", "")


# ---- Simulated propagation: matching across files ------------------------


def test_operators_name_identifies_one_observer_across_patients():
    """Three patients × two observers; OperatorsName is the only consistent
    token. Matching on it selects exactly one observer's files."""
    observer_a = [
        _rtss(sop_instance_uid=f"A{i}", file_path=f"/data/HN{i}/a.dcm", operators_name="JK")
        for i in range(3)
    ]
    observer_b = [
        _rtss(sop_instance_uid=f"B{i}", file_path=f"/data/HN{i}/b.dcm", operators_name="PR")
        for i in range(3)
    ]
    everyone = observer_a + observer_b

    # Anchor on one Observer-A file; match the rest by OperatorsName.
    anchor = observer_a[0]
    _label, attr, value = ("OperatorsName", "operators_name", anchor.operators_name)
    matched = [r for r in everyone if _field_value_of(r, attr) == value]
    assert len(matched) == 3
    assert {r.sop_instance_uid for r in matched} == {"A0", "A1", "A2"}


# ---- Dialog _rows_matching: single-field + AND combinations --------------


@pytest.fixture
def labelled_dialog(qapp, tmp_path):
    """A Manage Source Labels dialog over 4 RTSSes with overlapping fields."""
    folder = tmp_path
    study_uid = generate_uid()
    for_uid = generate_uid()
    _write_ct_slice(
        folder, patient_id="P1", study_uid=study_uid, series_uid=generate_uid(), for_uid=for_uid
    )
    specs = [
        ("Eclipse", "Lee", "a.dcm"),
        ("Eclipse", "Smith", "b.dcm"),
        ("Eclipse", "Lee", "c.dcm"),
        ("MIM", "Lee", "d.dcm"),
    ]
    for mfr, reviewer, fname in specs:
        _write_rtstruct(
            folder,
            patient_id="P1",
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer=mfr,
            reviewer_name=reviewer,
            organs=["Parotid_L"],
            filename=fname,
        )
    lib = MetadataLibrary()
    lib.scan_folder(str(folder))
    return ManageSourceLabelsDialog(lib, {})


def test_rows_matching_single_field(labelled_dialog):
    rows = labelled_dialog._rows_matching([("manufacturer", "Eclipse")])
    assert len(rows) == 3  # a, b, c


def test_rows_matching_and_combination_narrows(labelled_dialog):
    rows = labelled_dialog._rows_matching([("manufacturer", "Eclipse"), ("reviewer_name", "Lee")])
    assert len(rows) == 2  # a, c (b is Smith, d is MIM)


def test_rows_matching_other_single_field(labelled_dialog):
    rows = labelled_dialog._rows_matching([("reviewer_name", "Lee")])
    assert len(rows) == 3  # a, c, d


# ---- Group-by-column: recommendation + assignment ------------------------


def _obs_rtss(sop, pid, **fields):
    base = {
        "sop_instance_uid": sop,
        "file_path": f"/data/{pid}/{sop}.dcm",
        "manufacturer": "",
        "source_label": "x",
        "source_origin": "mfr",
        "frame_of_reference_uid": "for",
        "study_instance_uid": "study",
    }
    base.update(fields)
    return RTSTRUCTEntry(**base)


def test_recommend_prefers_clean_stable_column():
    """OperatorsName has 3 stable values, one per patient → recommended over
    filename (which is also per-patient-distinct but has many global values)."""
    rtss, patient_of = [], {}
    for pid in ("HN1", "HN2", "HN3"):
        for obs in ("JK", "PR", "ML"):
            sop = f"{pid}_{obs}"
            rtss.append(_obs_rtss(sop, pid, operators_name=obs))
            patient_of[sop] = pid
    assert recommend_group_attr(rtss, patient_of) == "operators_name"


def test_recommend_none_when_no_clean_column():
    """If a patient has two files with the SAME value for every column, no
    column cleanly separates them → no recommendation."""
    rtss, patient_of = [], {}
    # HN1 has two files both with operators_name 'JK' (duplicate within patient)
    for sop in ("HN1_a", "HN1_b"):
        rtss.append(_obs_rtss(sop, "HN1", operators_name="JK"))
        patient_of[sop] = "HN1"
    assert recommend_group_attr(rtss, patient_of) is None


def test_group_by_dialog_auto_assigns_observer_labels(qapp, tmp_path):
    folder = tmp_path
    # 2 patients × 2 observers (OperatorsName JK / PR), distinct per patient.
    # Each patient gets its OWN FrameOfReferenceUID — a shared FoR would
    # trigger the scanner's anonymisation merge into one patient.
    for pid in ("HN1", "HN2"):
        study_uid = generate_uid()
        for_uid = generate_uid()
        _write_ct_slice(
            folder, patient_id=pid, study_uid=study_uid, series_uid=generate_uid(), for_uid=for_uid
        )
        for obs in ("JK", "PR"):
            _write_rtstruct(
                folder,
                patient_id=pid,
                study_uid=study_uid,
                for_uid=for_uid,
                operators_name=obs,
                organs=["Parotid_L"],
                filename=f"{pid}_{obs}.dcm",
            )
    lib = MetadataLibrary()
    lib.scan_folder(str(folder))
    rtss_list, patient_of, patients = [], {}, set()
    for pid, patient in lib.patients.items():
        for ctx in patient.contexts:
            for r in ctx.rtstructs:
                rtss_list.append(r)
                patient_of[r.sop_instance_uid] = pid
                patients.add(pid)

    dlg = _GroupByColumnDialog(rtss_list, patient_of, len(patients))
    # Recommended column should be OperatorsName.
    assert dlg._current_attr() == "operators_name"
    # Two groups (JK, PR), auto-filled Observer A / Observer B.
    dlg._on_apply()
    assigned = dlg.assignments()
    # Every file got a label; exactly two distinct labels, one per observer.
    assert len(assigned) == 4
    assert set(assigned.values()) == {"Observer A", "Observer B"}
    # All JK-operator files share one label (the consistent-across-patients
    # observer identity); likewise all PR files.
    op_of = {r.sop_instance_uid: r.operators_name for r in rtss_list}
    jk_labels = {assigned[sop] for sop, op in op_of.items() if op == "JK"}
    pr_labels = {assigned[sop] for sop, op in op_of.items() if op == "PR"}
    assert len(jk_labels) == 1
    assert len(pr_labels) == 1
    assert jk_labels != pr_labels
