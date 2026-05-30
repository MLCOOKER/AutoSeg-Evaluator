"""Tests for the 'Select similar' assisted-propagation field cascade.

Covers the pure, Qt-free helpers in the Manage Source Labels dialog that
decide which distinguishing field identifies an observer:
:func:`best_distinguishing_field` and :func:`_field_value_of`.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from autoseg_evaluator.data.metadata import RTSTRUCTEntry  # noqa: E402
from autoseg_evaluator.ui.dialogs.source_labels import (  # noqa: E402
    _field_value_of,
    best_distinguishing_field,
)


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
