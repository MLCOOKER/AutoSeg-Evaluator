"""Tests for the v2.4 Build Consensus GT tab grouping model.

Covers the inverted grouping (per-patient, members = selected observer
labels), rater identity = source label, duplicate-label warnings, and the
synthetic-entry / session round-trip. Organ matching is by ROI name only
(no rasterisation), so the synthetic RTSSes here carry organ names but no
contour geometry — enough to exercise grouping and consensus assembly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from pydicom.uid import generate_uid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from autoseg_evaluator.data.metadata import MetadataLibrary  # noqa: E402
from autoseg_evaluator.ui.tabs.build_consensus import (  # noqa: E402
    CONSENSUS_SOURCE_LABEL,
    BuildConsensusTab,
)
from test_metadata import _write_ct_slice, _write_rtstruct  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def observer_library(tmp_path):
    """One patient, three manual observers with distinct source labels.

    Observer A + B contour both parotids; Observer C only the left.
    Source label is derived from Manufacturer (cascade step 1).
    """
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
        manufacturer="Observer A",
        organs=["Parotid_L", "Parotid_R"],
        filename="a.dcm",
    )
    _write_rtstruct(
        folder,
        patient_id="HN1",
        study_uid=study_uid,
        for_uid=for_uid,
        manufacturer="Observer B",
        organs=["Parotid_L", "Parotid_R"],
        filename="b.dcm",
    )
    _write_rtstruct(
        folder,
        patient_id="HN1",
        study_uid=study_uid,
        for_uid=for_uid,
        manufacturer="Observer C",
        organs=["Parotid_L"],
        filename="c.dcm",
    )
    lib = MetadataLibrary()
    lib.scan_folder(str(folder))
    return lib


def _make_tab(qapp, library, observers=None):
    tab = BuildConsensusTab(settings={"consensus_observer_labels": list(observers or [])})
    tab.set_library(library)
    return tab


# ---- Observer selection drives eligibility -------------------------------


def test_no_observers_selected_no_groups(qapp, observer_library):
    tab = _make_tab(qapp, observer_library, observers=[])
    assert tab._eligible_groups() == {}


def test_two_observers_form_a_patient_group(qapp, observer_library):
    tab = _make_tab(qapp, observer_library, observers=["Observer A", "Observer B"])
    groups = tab._eligible_groups()
    assert set(groups) == {"HN1"}
    labels = sorted(r.source_label for r in groups["HN1"])
    assert labels == ["Observer A", "Observer B"]


def test_unselected_observer_excluded(qapp, observer_library):
    # Only A + B selected → C must not appear as a member.
    tab = _make_tab(qapp, observer_library, observers=["Observer A", "Observer B"])
    members = tab._eligible_groups()["HN1"]
    assert all(r.source_label != "Observer C" for r in members)


def test_all_source_labels_lists_distinct(qapp, observer_library):
    tab = _make_tab(qapp, observer_library, observers=[])
    assert tab._all_source_labels() == ["Observer A", "Observer B", "Observer C"]


# ---- Organ matching keyed by patient -------------------------------------


def test_auto_match_buckets_keyed_by_patient(qapp, observer_library):
    tab = _make_tab(qapp, observer_library, observers=["Observer A", "Observer B", "Observer C"])
    tab._auto_match_group("HN1")
    drawers = tab._group_drawers["HN1"]
    # Parotid_L has all three; Parotid_R has A + B only.
    assert "Parotid_L" in drawers
    assert "Parotid_R" in drawers
    assert len(drawers["Parotid_L"]) == 3
    assert len(drawers["Parotid_R"]) == 2


def test_rater_identity_is_source_label(qapp, observer_library):
    tab = _make_tab(qapp, observer_library, observers=["Observer A", "Observer B"])
    tab._auto_match_group("HN1")
    members = tab._group_drawers["HN1"]["Parotid_L"]
    observers = sorted(tab._source_label_for(sop) for sop, _roi, _name in members)
    assert observers == ["Observer A", "Observer B"]


# ---- Duplicate-label warning ---------------------------------------------


def test_duplicate_observer_label_warns_and_dedupes(qapp, tmp_path):
    folder = tmp_path
    study_uid = generate_uid()
    for_uid = generate_uid()
    _write_ct_slice(
        folder, patient_id="HN1", study_uid=study_uid, series_uid=generate_uid(), for_uid=for_uid
    )
    # Two RTSSes share the SAME label "Observer A" — a Tab-1 labelling error.
    for fname in ("a1.dcm", "a2.dcm"):
        _write_rtstruct(
            folder,
            patient_id="HN1",
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="Observer A",
            organs=["Parotid_L"],
            filename=fname,
        )
    _write_rtstruct(
        folder,
        patient_id="HN1",
        study_uid=study_uid,
        for_uid=for_uid,
        manufacturer="Observer B",
        organs=["Parotid_L"],
        filename="b.dcm",
    )
    lib = MetadataLibrary()
    lib.scan_folder(str(folder))
    tab = _make_tab(qapp, lib, observers=["Observer A", "Observer B"])
    groups = tab._eligible_groups()
    # Observer A deduped to one member → 2 members total (A + B).
    assert len(groups["HN1"]) == 2
    assert any("Observer A" in w for w in tab._eligibility_warnings)


# ---- Synthetic entry + session round-trip --------------------------------


def test_build_synthetic_entry_and_session_roundtrip(qapp, observer_library):
    tab = _make_tab(qapp, observer_library, observers=["Observer A", "Observer B", "Observer C"])
    tab._auto_match_group("HN1")
    created, skipped = tab._register_consensus_for(["HN1"])
    assert created == 1, skipped

    # The synthetic entry is registered in the library with the consensus label.
    syns = observer_library.synthetic_consensus_entries()
    assert len(syns) == 1
    _pid, _for, syn = syns[0]
    assert syn.source_label == CONSENSUS_SOURCE_LABEL
    assert syn.is_synthetic_consensus

    # session_state emits the v4 observer_labels payload.
    state = tab.session_state()
    assert len(state) == 1
    grp = state[0]
    assert grp["patient_id"] == "HN1"
    assert grp["observer_labels"] == ["Observer A", "Observer B", "Observer C"]
    assert "source_label" not in grp  # reshaped away in v4

    # Re-applying restores the synthetic entry.
    observer_library.clear_synthetic_consensus()
    assert tab.apply_session_state(state) == 1
    assert len(observer_library.synthetic_consensus_entries()) == 1


def test_apply_session_state_back_compat_source_label(qapp, observer_library):
    """A pre-v4 payload carrying 'source_label' (no observer_labels) still loads."""
    members = observer_library.patients["HN1"].contexts[0].rtstructs
    a_sop = next(r.sop_instance_uid for r in members if r.source_label == "Observer A")
    b_sop = next(r.sop_instance_uid for r in members if r.source_label == "Observer B")
    for_uid = observer_library.patients["HN1"].contexts[0].frame_of_reference_uid
    tab = _make_tab(qapp, observer_library, observers=["Observer A", "Observer B"])
    old_payload = [
        {
            "patient_id": "HN1",
            "source_label": "Manual",  # pre-v4 key
            "for_uid": for_uid,
            "synthetic_sop_uid": "SYN_OLD",
            "organs": [
                {
                    "roi_number": 1,
                    "roi_name": "Parotid_L",
                    "constituents": [
                        {"sop_uid": a_sop, "roi_number": 1},
                        {"sop_uid": b_sop, "roi_number": 1},
                    ],
                }
            ],
        }
    ]
    assert tab.apply_session_state(old_payload) == 1
    syn = observer_library.synthetic_consensus_entries()[0][2]
    assert syn.source_label == CONSENSUS_SOURCE_LABEL
