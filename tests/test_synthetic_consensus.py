"""Tests for the synthetic STAPLE-consensus RTSS feature.

Covers:

* Data-model invariants on ``RTSTRUCTEntry`` synthetic flag + constituent
  groups.
* ``MetadataLibrary`` register / unregister / clear / list helpers,
  including the "same SOPInstanceUID replaces in place" rule.
* Session schema round-trip — saving a v2 session with consensus_groups
  and loading it back via ``load_session_file``.
* The Build Consensus tab's threshold-based clustering logic (importing
  the function only, no Qt construction).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoseg_evaluator.data.metadata import (
    ImagingContext,
    MetadataLibrary,
    OrganEntry,
    PatientEntry,
    RTSTRUCTEntry,
)
from autoseg_evaluator.data.session import (
    DEFAULT_SUFFIX,
    SCHEMA_VERSION,
    build_session_dict,
    load_session_file,
    save_session,
)

# ---- Fixtures ------------------------------------------------------------


def _rtss(sop: str, label: str, for_uid: str = "FOR1") -> RTSTRUCTEntry:
    return RTSTRUCTEntry(
        sop_instance_uid=sop,
        file_path=f"/fake/{sop}.dcm",
        manufacturer=label,
        source_label=label,
        source_origin="mfr",
        frame_of_reference_uid=for_uid,
        study_instance_uid="STUDY1",
        organs=[OrganEntry(roi_number=1, roi_name="Parotid_L")],
    )


def _library_with_two_manuals() -> MetadataLibrary:
    lib = MetadataLibrary()
    ctx = ImagingContext(frame_of_reference_uid="FOR1")
    ctx.rtstructs = [_rtss("M1", "Manual"), _rtss("M2", "Manual")]
    lib.patients = {"HN1": PatientEntry(patient_id="HN1", contexts=[ctx])}
    return lib


# ---- RTSTRUCTEntry synthetic fields --------------------------------------


def test_rtstructentry_defaults_are_not_synthetic():
    r = _rtss("X", "Manual")
    assert r.is_synthetic_consensus is False
    assert r.constituent_groups == {}
    # filename falls back to basename of file_path
    assert r.filename == "X.dcm"


def test_rtstructentry_synthetic_filename_is_marker_string():
    r = RTSTRUCTEntry(
        sop_instance_uid="SYN",
        file_path="",
        manufacturer="STAPLE Consensus",
        source_label="STAPLE Consensus",
        source_origin="synthetic",
        frame_of_reference_uid="FOR1",
        study_instance_uid="",
        organs=[],
        is_synthetic_consensus=True,
        constituent_groups={1: [("A", 1), ("B", 1)]},
    )
    assert r.filename == "(STAPLE consensus)"
    assert r.is_synthetic_consensus is True
    assert r.constituent_groups[1] == [("A", 1), ("B", 1)]


# ---- MetadataLibrary synthetic helpers ----------------------------------


def test_register_synthetic_consensus_appends_to_matching_context():
    lib = _library_with_two_manuals()
    syn = RTSTRUCTEntry(
        sop_instance_uid="SYN1",
        file_path="",
        manufacturer="STAPLE Consensus",
        source_label="STAPLE Consensus",
        source_origin="synthetic",
        frame_of_reference_uid="FOR1",
        study_instance_uid="",
        organs=[OrganEntry(roi_number=1, roi_name="Parotid_L")],
        is_synthetic_consensus=True,
        constituent_groups={1: [("M1", 1), ("M2", 1)]},
    )
    assert lib.register_synthetic_consensus("HN1", "FOR1", syn) is True
    # Now 3 RTSSes total in the context (2 real + 1 synthetic)
    ctx = lib.patients["HN1"].contexts[0]
    assert len(ctx.rtstructs) == 3
    assert ctx.rtstructs[-1].is_synthetic_consensus is True


def test_register_synthetic_consensus_refuses_non_synthetic_entries():
    lib = _library_with_two_manuals()
    non_syn = _rtss("X", "Manual")  # is_synthetic_consensus is False by default
    assert lib.register_synthetic_consensus("HN1", "FOR1", non_syn) is False


def test_register_synthetic_consensus_replaces_existing_uid_in_place():
    lib = _library_with_two_manuals()
    syn_v1 = RTSTRUCTEntry(
        sop_instance_uid="SYN1",
        file_path="",
        manufacturer="STAPLE Consensus",
        source_label="STAPLE Consensus",
        source_origin="synthetic",
        frame_of_reference_uid="FOR1",
        study_instance_uid="",
        organs=[],
        is_synthetic_consensus=True,
        constituent_groups={1: [("M1", 1), ("M2", 1)]},
    )
    syn_v2 = RTSTRUCTEntry(
        sop_instance_uid="SYN1",
        file_path="",
        manufacturer="STAPLE Consensus",
        source_label="STAPLE Consensus",
        source_origin="synthetic",
        frame_of_reference_uid="FOR1",
        study_instance_uid="",
        organs=[],
        is_synthetic_consensus=True,
        constituent_groups={2: [("M1", 2), ("M2", 2)]},
    )
    lib.register_synthetic_consensus("HN1", "FOR1", syn_v1)
    lib.register_synthetic_consensus("HN1", "FOR1", syn_v2)
    # Still only 1 synthetic in the context — second registration replaced the first
    syn_list = [r for r in lib.patients["HN1"].contexts[0].rtstructs if r.is_synthetic_consensus]
    assert len(syn_list) == 1
    assert syn_list[0].constituent_groups == {2: [("M1", 2), ("M2", 2)]}


def test_register_synthetic_consensus_returns_false_on_bad_patient_or_for():
    lib = _library_with_two_manuals()
    syn = RTSTRUCTEntry(
        sop_instance_uid="SYN1",
        file_path="",
        manufacturer="STAPLE Consensus",
        source_label="STAPLE Consensus",
        source_origin="synthetic",
        frame_of_reference_uid="FOR1",
        study_instance_uid="",
        organs=[],
        is_synthetic_consensus=True,
        constituent_groups={1: [("M1", 1), ("M2", 1)]},
    )
    assert lib.register_synthetic_consensus("NOPE", "FOR1", syn) is False
    assert lib.register_synthetic_consensus("HN1", "FOR_NOPE", syn) is False


def test_unregister_synthetic_consensus_removes_by_uid():
    lib = _library_with_two_manuals()
    syn = RTSTRUCTEntry(
        sop_instance_uid="SYN1",
        file_path="",
        manufacturer="STAPLE Consensus",
        source_label="STAPLE Consensus",
        source_origin="synthetic",
        frame_of_reference_uid="FOR1",
        study_instance_uid="",
        organs=[],
        is_synthetic_consensus=True,
        constituent_groups={1: [("M1", 1), ("M2", 1)]},
    )
    lib.register_synthetic_consensus("HN1", "FOR1", syn)
    assert lib.unregister_synthetic_consensus("SYN1") is True
    assert lib.unregister_synthetic_consensus("SYN1") is False  # already gone


def test_clear_synthetic_consensus_only_removes_synthetic_entries():
    lib = _library_with_two_manuals()
    syn = RTSTRUCTEntry(
        sop_instance_uid="SYN1",
        file_path="",
        manufacturer="STAPLE Consensus",
        source_label="STAPLE Consensus",
        source_origin="synthetic",
        frame_of_reference_uid="FOR1",
        study_instance_uid="",
        organs=[],
        is_synthetic_consensus=True,
        constituent_groups={1: [("M1", 1), ("M2", 1)]},
    )
    lib.register_synthetic_consensus("HN1", "FOR1", syn)
    lib.clear_synthetic_consensus()
    ctx = lib.patients["HN1"].contexts[0]
    # Real RTSSes survive; synthetic ones are gone
    assert len(ctx.rtstructs) == 2
    assert all(not r.is_synthetic_consensus for r in ctx.rtstructs)


def test_synthetic_consensus_entries_enumerates_all():
    lib = _library_with_two_manuals()
    syn = RTSTRUCTEntry(
        sop_instance_uid="SYN1",
        file_path="",
        manufacturer="STAPLE Consensus",
        source_label="STAPLE Consensus",
        source_origin="synthetic",
        frame_of_reference_uid="FOR1",
        study_instance_uid="",
        organs=[],
        is_synthetic_consensus=True,
        constituent_groups={1: [("M1", 1), ("M2", 1)]},
    )
    lib.register_synthetic_consensus("HN1", "FOR1", syn)
    entries = lib.synthetic_consensus_entries()
    assert len(entries) == 1
    pid, for_uid, entry = entries[0]
    assert pid == "HN1"
    assert for_uid == "FOR1"
    assert entry.sop_instance_uid == "SYN1"


# ---- Session round-trip with consensus_groups ---------------------------


def test_session_schema_version_at_least_2():
    """v2 introduced consensus_groups; v3 added per-drawer denylist."""
    assert SCHEMA_VERSION >= 2


def test_build_session_dict_includes_consensus_groups_key():
    data = build_session_dict(
        folder="/tmp",
        drawers_state=[],
        replacement_rules=[],
        template={},
    )
    # Always present (empty when omitted) — makes the schema stable on disk.
    assert "consensus_groups" in data
    assert data["consensus_groups"] == []


def test_session_roundtrip_preserves_consensus_groups(tmp_path: Path):
    payload = build_session_dict(
        folder="/tmp",
        drawers_state=[],
        replacement_rules=[],
        template={},
        consensus_groups=[
            {
                "patient_id": "HN1",
                "source_label": "Manual",
                "for_uid": "FOR1",
                "synthetic_sop_uid": "SYN1",
                "organs": [
                    {
                        "roi_number": 1,
                        "roi_name": "Parotid_L",
                        "constituents": [
                            {"sop_uid": "M1", "roi_number": 5},
                            {"sop_uid": "M2", "roi_number": 9},
                        ],
                    }
                ],
            }
        ],
    )
    path = tmp_path / f"test{DEFAULT_SUFFIX}"
    save_session(path, payload)
    loaded = load_session_file(path)
    assert loaded["consensus_groups"] == payload["consensus_groups"]


def test_session_v1_files_still_load_without_consensus_groups(tmp_path: Path):
    """A session saved against schema v1 lacks consensus_groups; loading must succeed."""
    v1_payload = {
        "schema_version": 1,
        "folder": "/tmp",
        "replacement_rules": [],
        "last_template": {},
        "drawers": [],
        # NOTE: no consensus_groups key
    }
    path = tmp_path / f"old{DEFAULT_SUFFIX}"
    path.write_text(json.dumps(v1_payload), encoding="utf-8")
    loaded = load_session_file(path)
    # Loader is permissive — missing field becomes "not present"
    assert "consensus_groups" not in loaded
    # Callers default to [] when the key is absent; verify that's safe
    assert (loaded.get("consensus_groups") or []) == []


def test_session_future_schema_raises():
    """Files claiming a version greater than ours must be refused, not silently misread."""
    # The version check happens in load_session_file
    payload = {"schema_version": SCHEMA_VERSION + 99}
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=DEFAULT_SUFFIX, delete=False, encoding="utf-8"
    ) as fp:
        json.dump(payload, fp)
        tmp_name = fp.name
    try:
        with pytest.raises(ValueError, match="schema version"):
            load_session_file(tmp_name)
    finally:
        Path(tmp_name).unlink(missing_ok=True)
