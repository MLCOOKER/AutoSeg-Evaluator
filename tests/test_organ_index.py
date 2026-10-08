"""Tests for building the organ index over a library, and tagging results.

Covers the two things that only make sense in aggregate — reconciling an
interpreted type that differs between files, and ranking a review worklist by
how often a name actually occurs — plus the results-row tagging that the
statistics layer will group on.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from autoseg_evaluator.core.organ_groups import (
    LATERALITY_L,
    LATERALITY_R,
    QUALIFIER_NONANATOMIC,
    QUALIFIER_OAR,
    QUALIFIER_TARGET,
    TIER_DICTIONARY,
    TIER_UNASSIGNED,
)
from autoseg_evaluator.data.metadata import (
    ImagingContext,
    OrganEntry,
    PatientEntry,
    RTSTRUCTEntry,
)
from autoseg_evaluator.data.organ_index import (
    build_organ_index,
    collect_roi_names,
    drawer_consensus,
)
from autoseg_evaluator.data.results import META_COLUMNS, ResultsManager
from autoseg_evaluator.data.synonyms import flatten_synonyms, load_synonyms

SYNONYMS_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "autoseg_evaluator"
    / "resources"
    / "synonyms.json"
)


@pytest.fixture(scope="module")
def syn():
    return flatten_synonyms(load_synonyms(SYNONYMS_PATH))


class _FakeLibrary:
    """Minimal stand-in — the index only ever walks patients/contexts/rtstructs."""

    def __init__(self, structure_sets):
        ctx = ImagingContext(frame_of_reference_uid="1.2.3")
        for sop, organs in structure_sets:
            ctx.rtstructs.append(
                RTSTRUCTEntry(
                    sop_instance_uid=sop,
                    file_path=f"{sop}.dcm",
                    manufacturer="",
                    source_label=sop,
                    source_origin="test",
                    frame_of_reference_uid="1.2.3",
                    study_instance_uid="9.9.9",
                    organs=[
                        OrganEntry(roi_number=i + 1, roi_name=n, interpreted_type=t)
                        for i, (n, t) in enumerate(organs)
                    ],
                )
            )
        self.patients = {"P1": PatientEntry(patient_id="P1", contexts=[ctx])}


# ---- Collection -----------------------------------------------------------


def test_collect_counts_names_across_structure_sets():
    lib = _FakeLibrary(
        [
            ("A", [("Parotid_L", "ORGAN"), ("Brainstem", "ORGAN")]),
            ("B", [("Parotid_L", "ORGAN")]),
        ]
    )
    types, freq = collect_roi_names(lib)
    assert freq["Parotid_L"] == 2
    assert freq["Brainstem"] == 1
    assert types["Parotid_L"] == Counter({"ORGAN": 2})


def test_missing_interpreted_type_is_recorded_as_blank():
    lib = _FakeLibrary([("A", [("Parotid_L", "")])])
    types, _freq = collect_roi_names(lib)
    assert types["Parotid_L"] == Counter({"(blank)": 1})


# ---- Type reconciliation --------------------------------------------------


def test_conflicting_types_pick_the_dominant_and_flag_it(syn):
    """Nodal levels really do come back ORGAN from one vendor and CTV another."""
    lib = _FakeLibrary(
        [
            ("A", [("LN_Neck_IB_L", "ORGAN")]),
            ("B", [("LN_Neck_IB_L", "ORGAN")]),
            ("C", [("LN_Neck_IB_L", "CTV")]),
        ]
    )
    index = build_organ_index(lib, synonyms_flat=syn)
    found = index.get("LN_Neck_IB_L")
    assert found.interpreted_type == "ORGAN"
    assert found.type_conflict is True
    assert index.summary()["conflicts"] == 1


def test_agreeing_types_are_not_flagged(syn):
    lib = _FakeLibrary([("A", [("Parotid_L", "ORGAN")]), ("B", [("Parotid_L", "ORGAN")])])
    assert build_organ_index(lib, synonyms_flat=syn).get("Parotid_L").type_conflict is False


def test_blanks_do_not_count_as_a_conflict(syn):
    """MIM and MVision leave the type empty; that is absence, not disagreement."""
    lib = _FakeLibrary([("A", [("Parotid_L", "ORGAN")]), ("B", [("Parotid_L", "")])])
    found = build_organ_index(lib, synonyms_flat=syn).get("Parotid_L")
    assert found.type_conflict is False
    assert found.interpreted_type == "ORGAN"


# ---- Grouping over a library ----------------------------------------------


def test_spellings_across_producers_reach_one_group(syn):
    lib = _FakeLibrary(
        [
            ("manual", [("Optic Nerve Left", "ORGAN")]),
            ("vendorA", [("OpticNrv_L", "ORGAN")]),
            ("vendorB", [("OpticNerve_L_Experimental", "ORGAN")]),
        ]
    )
    index = build_organ_index(lib, synonyms_flat=syn)
    keys = {a.key for a in index.assignments.values()}
    assert len(keys) == 1
    assert next(iter(keys)).laterality == LATERALITY_L


def test_left_and_right_stay_apart_across_a_library(syn):
    lib = _FakeLibrary([("A", [("Parotid_L", "ORGAN"), ("Parotid_R", "ORGAN")])])
    index = build_organ_index(lib, synonyms_flat=syn)
    assert len(index.groups()) == 2
    assert len(index.groups(pool_laterality=True)) == 1


def test_summary_reports_tiers_and_qualifiers(syn):
    lib = _FakeLibrary(
        [
            (
                "A",
                [
                    ("Parotid_L", "ORGAN"),
                    ("1CTV_", "CTV"),
                    ("Board", "SUPPORT"),
                    ("Wibble_Xyz", "ORGAN"),
                ],
            )
        ]
    )
    summary = build_organ_index(lib, synonyms_flat=syn).summary()
    assert summary["names"] == 4
    assert summary["tiers"][TIER_DICTIONARY] >= 1
    assert summary["tiers"][TIER_UNASSIGNED] >= 1
    assert summary["qualifiers"][QUALIFIER_TARGET] == 1
    assert summary["qualifiers"][QUALIFIER_NONANATOMIC] == 1


# ---- Review worklist ------------------------------------------------------


def test_worklist_is_ranked_by_frequency(syn):
    lib = _FakeLibrary(
        [
            ("A", [("Rare_Unknown_Thing", "ORGAN"), ("Common_Unknown_Thing", "ORGAN")]),
            ("B", [("Common_Unknown_Thing", "ORGAN")]),
            ("C", [("Common_Unknown_Thing", "ORGAN")]),
        ]
    )
    worklist = build_organ_index(lib, synonyms_flat=syn).needing_review()
    assert worklist[0].roi_name == "Common_Unknown_Thing"


def test_worklist_can_exclude_non_organs(syn):
    lib = _FakeLibrary([("A", [("Wibble_Xyz", "ORGAN"), ("Zzz_Target", "CTV")])])
    index = build_organ_index(lib, synonyms_flat=syn)
    assert {a.roi_name for a in index.needing_review(oar_only=True)} == {"Wibble_Xyz"}
    assert len(index.needing_review(oar_only=False)) == 2


def test_manual_answers_survive_a_rebuild(syn):
    """Re-running after a review must not discard what the user decided."""
    lib = _FakeLibrary([("A", [("Wibble_Xyz", "ORGAN")])])
    index = build_organ_index(lib, synonyms_flat=syn, manual={"Wibble_Xyz": "parotid"})
    assert index.get("Wibble_Xyz").key.base == "parotid"
    assert index.needing_review() == []


# ---- Drawer consensus -----------------------------------------------------


def test_drawer_consensus_agrees_on_one_organ(syn):
    lib = _FakeLibrary([("A", [("Parotid_L", "ORGAN"), ("Left Parotid", "ORGAN")])])
    index = build_organ_index(lib, synonyms_flat=syn)
    key, disagreed = drawer_consensus(index, ["Parotid_L", "Left Parotid"])
    assert disagreed is False
    assert key.laterality == LATERALITY_L


def test_drawer_consensus_flags_a_mixed_drawer(syn):
    """A drawer holding left for one patient and right for another is an error."""
    lib = _FakeLibrary([("A", [("Parotid_L", "ORGAN"), ("Parotid_R", "ORGAN")])])
    index = build_organ_index(lib, synonyms_flat=syn)
    _key, disagreed = drawer_consensus(index, ["Parotid_L", "Parotid_R"])
    assert disagreed is True


def test_drawer_consensus_with_nothing_known(syn):
    lib = _FakeLibrary([("A", [("Parotid_L", "ORGAN")])])
    index = build_organ_index(lib, synonyms_flat=syn)
    assert drawer_consensus(index, ["Never_Seen"]) == (None, False)


# ---- Results tagging ------------------------------------------------------


def _row(gt_roi_name, drawer="Parotid_L"):
    return {
        "drawer": drawer,
        "patient_id": "P1",
        "gt_roi_name": gt_roi_name,
        "gt_source_label": "manual",
        "test_source_label": "vendorA",
        "test_organ": gt_roi_name,
        "metrics": {"dice": 0.9},
    }


def test_organ_columns_exist_in_the_table_layout():
    keys = [k for k, _label in META_COLUMNS]
    for column in ("canonical_organ", "organ_laterality", "organ_qualifier", "organ_tier"):
        assert column in keys


def test_rows_are_tagged_with_their_organ(syn):
    lib = _FakeLibrary([("A", [("Parotid_L", "ORGAN")])])
    store = ResultsManager()
    store.set_organ_index(build_organ_index(lib, synonyms_flat=syn))
    store.add_row(_row("Parotid_L"))
    row = store.rows()[0]
    assert row["organ_laterality"] == LATERALITY_L
    assert row["organ_qualifier"] == QUALIFIER_OAR
    assert row["organ_tier"] == TIER_DICTIONARY
    assert "Parotid" in row["canonical_organ"]


def test_differently_named_drawers_share_one_organ_tag(syn):
    """The whole point: two drawers, one organ, so a cohort statistic works."""
    lib = _FakeLibrary([("A", [("Parotid_L", "ORGAN")]), ("B", [("Left Parotid", "ORGAN")])])
    store = ResultsManager()
    store.set_organ_index(build_organ_index(lib, synonyms_flat=syn))
    store.add_row(_row("Parotid_L", drawer="Parotid_L"))
    store.add_row(_row("Left Parotid", drawer="Left Parotid"))
    rows = store.rows()
    assert rows[0]["drawer"] != rows[1]["drawer"]
    assert rows[0]["canonical_organ"] == rows[1]["canonical_organ"]


def test_left_and_right_rows_keep_different_tags(syn):
    lib = _FakeLibrary([("A", [("Parotid_L", "ORGAN"), ("Parotid_R", "ORGAN")])])
    store = ResultsManager()
    store.set_organ_index(build_organ_index(lib, synonyms_flat=syn))
    store.add_row(_row("Parotid_L"))
    store.add_row(_row("Parotid_R"))
    rows = store.rows()
    assert rows[0]["canonical_organ"] != rows[1]["canonical_organ"]
    assert {rows[0]["organ_laterality"], rows[1]["organ_laterality"]} == {
        LATERALITY_L,
        LATERALITY_R,
    }


def test_untagged_rows_are_left_alone(syn):
    """A name the index never saw must not break the row."""
    lib = _FakeLibrary([("A", [("Parotid_L", "ORGAN")])])
    store = ResultsManager()
    store.set_organ_index(build_organ_index(lib, synonyms_flat=syn))
    store.add_row(_row("Something_Never_Scanned"))
    row = store.rows()[0]
    assert row.get("canonical_organ", "") == ""
    assert row["metrics"]["dice"] == 0.9


def test_tagging_is_applied_at_read_time(syn):
    """Relabelling an organ must not require recomputing any metric."""
    lib = _FakeLibrary([("A", [("Wibble_Xyz", "ORGAN")])])
    store = ResultsManager()
    store.add_row(_row("Wibble_Xyz", drawer="Wibble_Xyz"))
    assert store.rows()[0].get("canonical_organ", "") == ""

    store.set_organ_index(
        build_organ_index(lib, synonyms_flat=syn, manual={"Wibble_Xyz": "parotid"})
    )
    assert store.rows()[0]["canonical_organ"] == "Parotid"


def test_clearing_results_keeps_the_organ_index(syn):
    """The index belongs to the cohort, not to a batch of results."""
    lib = _FakeLibrary([("A", [("Parotid_L", "ORGAN")])])
    store = ResultsManager()
    store.set_organ_index(build_organ_index(lib, synonyms_flat=syn))
    store.add_row(_row("Parotid_L"))
    store.clear()
    store.add_row(_row("Parotid_L"))
    assert store.rows()[0]["organ_laterality"] == LATERALITY_L


def test_organ_index_can_be_cleared_explicitly(syn):
    lib = _FakeLibrary([("A", [("Parotid_L", "ORGAN")])])
    store = ResultsManager()
    store.set_organ_index(build_organ_index(lib, synonyms_flat=syn))
    store.set_organ_index(None)
    store.add_row(_row("Parotid_L"))
    assert store.rows()[0].get("canonical_organ", "") == ""
