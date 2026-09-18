"""Tests for the report model — the layer that decides which numbers get tested.

The statistics are checked in ``test_statistics.py``. What is checked here is
the interpretive part: that a patient cannot vote twice, that ground truth is
never compared with itself, that a comparison reports how much of each source it
had to discard, and that absence is classified only as far as the data can say.
"""

from __future__ import annotations

import pytest

from autoseg_evaluator.data.report import (
    CoverageCell,
    ReportModel,
    build_report_model,
    favours,
    metric_direction,
)


def _row(patient, organ, source, metrics, *, gt="manual", drawer=None, mode="vs gt"):
    return {
        "patient_id": patient,
        "canonical_organ": organ,
        "drawer": drawer or organ,
        "test_source_label": source,
        "gt_source_label": gt,
        "comparison_mode": mode,
        "metrics": dict(metrics),
    }


def _cohort(values, organ="Parotid (L)", metric="dice"):
    """``{source: {patient: value}}`` -> rows."""
    rows = []
    for source, per_patient in values.items():
        for patient, value in per_patient.items():
            rows.append(_row(patient, organ, source, {metric: value}))
    return rows


# ---- Inventory ------------------------------------------------------------


def test_ground_truth_is_never_a_comparator():
    """Every metric already measures agreement with the reference."""
    rows = _cohort({"VendorA": {"P1": 0.8}, "VendorB": {"P1": 0.7}})
    model = build_report_model(rows)
    assert model.sources() == ["VendorA", "VendorB"]
    assert "manual" in model.reference_sources
    assert "manual" not in model.sources()


def test_organs_come_from_the_canonical_grouping():
    """Two drawers labelled as one organ pool into one row of the report."""
    rows = [
        _row("P1", "Glnd Submand (R)", "VendorA", {"dice": 0.80}, drawer="SubmanG_R"),
        _row("P2", "Glnd Submand (R)", "VendorA", {"dice": 0.82}, drawer="Glnd_Submand_R"),
    ]
    model = build_report_model(rows)
    assert model.organs() == ["Glnd Submand (R)"]
    assert len(model.values("Glnd Submand (R)", "VendorA", "dice")) == 2


def test_rows_without_a_canonical_organ_fall_back_to_the_drawer():
    rows = [
        {
            "patient_id": "P1",
            "drawer": "Wibble_Xyz",
            "test_source_label": "VendorA",
            "gt_source_label": "manual",
            "metrics": {"dice": 0.7},
        }
    ]
    assert build_report_model(rows).organs() == ["Wibble_Xyz"]


def test_summary_rows_are_excluded():
    rows = _cohort({"VendorA": {"P1": 0.8}})
    rows.append(_row("P1", "Parotid (L)", "VendorA", {"dice": 0.9}, mode="STAPLE Details"))
    model = build_report_model(rows)
    assert len(model.values("Parotid (L)", "VendorA", "dice")) == 1


# ---- One patient, one vote ------------------------------------------------


def test_a_patient_cannot_contribute_twice_to_the_same_cell():
    """A repeat export would otherwise give one patient two votes in a paired test."""
    rows = [
        _row("P1", "Parotid (L)", "VendorA", {"dice": 0.80}),
        _row("P1", "Parotid (L)", "VendorA", {"dice": 0.80}),
    ]
    model = build_report_model(rows)
    assert len(model.values("Parotid (L)", "VendorA", "dice")) == 1
    assert model.duplicates_collapsed == 1
    assert model.conflicting_observations == 0


def test_a_repeat_carrying_a_different_value_is_not_a_duplicate():
    """Two answers for one organ, source and treatment context.

    A second course is not this — it carries its own linkage and becomes its own
    case. What is left here is a genuine collision inside one context, where
    first-wins decides which number is analysed, so it cannot be counted
    alongside harmless duplicated exports.
    """
    rows = [
        _row("P1", "Parotid (L)", "VendorA", {"dice": 0.80}),
        _row("P1", "Parotid (L)", "VendorA", {"dice": 0.84}),
    ]
    model = build_report_model(rows)
    assert len(model.values("Parotid (L)", "VendorA", "dice")) == 1
    assert model.duplicates_collapsed == 0
    assert model.conflicting_observations == 1


def test_the_same_patient_may_appear_for_different_organs():
    rows = [
        _row("P1", "Parotid (L)", "VendorA", {"dice": 0.80}),
        _row("P1", "Parotid (R)", "VendorA", {"dice": 0.82}),
    ]
    model = build_report_model(rows)
    assert model.duplicates_collapsed == 0
    assert len(model.organs()) == 2


# ---- Coverage -------------------------------------------------------------


def test_coverage_separates_not_run_from_not_produced():
    """The distinction that decides whether an absence is a finding.

    VendorB ran on P1 and P2 and produced this organ only for P1. It never ran
    on P3 at all. Those mean different things about the model.
    """
    rows = [
        _row("P1", "Parotid (L)", "VendorA", {"dice": 0.80}),
        _row("P2", "Parotid (L)", "VendorA", {"dice": 0.81}),
        _row("P3", "Parotid (L)", "VendorA", {"dice": 0.82}),
        _row("P1", "Parotid (L)", "VendorB", {"dice": 0.74}),
        _row("P2", "Brainstem", "VendorB", {"dice": 0.88}),
    ]
    model = build_report_model(rows)
    cell = model.coverage("Parotid (L)", "dice", "VendorB")
    assert cell.produced == 1
    assert cell.not_produced == 1  # ran on P2, no parotid
    assert cell.source_absent == 1  # never ran on P3
    assert cell.attempted == 2
    assert cell.eligible == 3
    assert cell.fraction == pytest.approx(0.5)


def test_coverage_cell_text_calls_out_absence_separately():
    cell = CoverageCell(produced=8, not_produced=0, source_absent=2)
    assert cell.summary() == "8 / 8 · 2 not run"
    assert CoverageCell().summary() == "—"
    assert CoverageCell(source_absent=3).summary() == "— (3 not run)"


def test_full_coverage_reads_plainly():
    assert CoverageCell(produced=10, not_produced=0).summary() == "10 / 10"


# ---- Comparison -----------------------------------------------------------


def test_comparison_uses_only_patients_both_sources_contoured():
    model = build_report_model(
        _cohort(
            {
                "VendorA": {f"P{i}": 0.80 + i * 0.01 for i in range(10)},
                "VendorB": {f"P{i}": 0.74 + i * 0.01 for i in range(4)},
            }
        )
    )
    result = model.compare("Parotid (L)", "dice", "VendorA", "VendorB")
    assert result.n_pairs == 4
    assert result.n_a == 10
    assert result.n_b == 4
    assert result.coverage_fraction == pytest.approx(0.4)


def test_a_comparison_with_no_shared_patients_is_none():
    model = build_report_model(_cohort({"VendorA": {"P1": 0.8, "P2": 0.8}, "VendorB": {"P3": 0.7}}))
    assert model.compare("Parotid (L)", "dice", "VendorA", "VendorB") is None


def test_family_adjusts_across_the_declared_organs_only():
    rows = []
    for organ, offset in (("Parotid (L)", 0.06), ("Parotid (R)", 0.05), ("Brainstem", 0.001)):
        for i in range(10):
            rows.append(_row(f"P{i}", organ, "VendorA", {"dice": 0.80 + i * 0.005}))
            rows.append(_row(f"P{i}", organ, "VendorB", {"dice": 0.80 + i * 0.005 - offset}))
    model = build_report_model(rows)

    all_three = model.family("dice", reference="VendorB", challenger="VendorA")
    assert set(all_three) == {"Parotid (L)", "Parotid (R)", "Brainstem"}
    assert all(r.p_adjusted is not None for r in all_three.values())

    # A smaller declared family corrects less harshly — which is exactly why the
    # family has to be chosen deliberately rather than inferred from the view.
    just_one = model.family("dice", "VendorB", "VendorA", organs=["Parotid (L)"])
    assert just_one["Parotid (L)"].p_adjusted <= all_three["Parotid (L)"].p_adjusted


def test_family_reports_when_it_cannot_detect_anything():
    """At ten pairs, a family of 26 cannot reject however the data fall."""
    rows = []
    for organ_index in range(26):
        for i in range(10):
            rows.append(_row(f"P{i}", f"Organ{organ_index}", "VendorA", {"dice": 0.80}))
            rows.append(_row(f"P{i}", f"Organ{organ_index}", "VendorB", {"dice": 0.70}))
    model = build_report_model(rows)
    family = model.family("dice", "VendorB", "VendorA")
    assert len(family) == 26
    assert model.family_can_detect(family.values()) is False

    smaller = model.family("dice", "VendorB", "VendorA", organs=[f"Organ{i}" for i in range(5)])
    assert model.family_can_detect(smaller.values()) is True


# ---- Direction ------------------------------------------------------------


@pytest.mark.parametrize(
    ("metric", "expected"),
    [
        ("dice", 1),
        ("surface_dice", 1),
        ("hausdorff95", -1),
        ("mean_surface_distance", -1),
        ("volume_diff_cc", 0),
        ("volume_ratio", 0),
    ],
)
def test_metric_direction(metric, expected):
    assert metric_direction(metric) == expected


def test_favours_reads_the_direction_not_the_sign():
    """A smaller Hausdorff is better, so a negative difference favours A."""
    assert favours("dice", 0.04) == "a"
    assert favours("dice", -0.04) == "b"
    assert favours("hausdorff95", -1.2) == "a"
    assert favours("hausdorff95", 1.2) == "b"


def test_an_undirected_metric_favours_neither():
    """Signed volume difference is best at a target, not at an extreme."""
    assert favours("volume_diff_cc", 5.0) == ""
    assert favours("volume_ratio", -0.2) == ""


# ---- Empty and degenerate -------------------------------------------------


def test_an_empty_model_is_usable():
    model = build_report_model([])
    assert model.sources() == []
    assert model.organs() == []
    assert model.coverage_matrix("dice") == {}


def test_non_numeric_metric_values_are_ignored():
    rows = [_row("P1", "Parotid (L)", "VendorA", {"dice": 0.8, "error": "failed", "flag": True})]
    model = build_report_model(rows)
    assert model.metrics() == ["dice"]


def test_describe_cell_returns_none_for_an_unknown_cell():
    model = ReportModel()
    assert model.describe_cell("nothing", "nobody", "dice") is None


def test_the_ceiling_is_judged_on_the_most_favourable_member():
    """One thin organ must not declare the whole family undetectable.

    Caught by smoke-testing the tab: the banner said nothing could reach
    significance while a ten-patient organ in the same family sat on screen at
    Holm-adjusted p = 0.008. Holm's strictest threshold applies to the smallest
    p in the family, so the comparison with the most pairs decides.
    """
    rows = []
    for i in range(10):
        rows.append(_row(f"P{i}", "Parotid (L)", "VendorA", {"dice": 0.80 + i * 0.004}))
        rows.append(_row(f"P{i}", "Parotid (L)", "VendorB", {"dice": 0.74 + i * 0.004}))
    for i in range(4):  # a thin organ in the same family
        rows.append(_row(f"P{i}", "Glnd Submand (R)", "VendorA", {"dice": 0.79 + i * 0.004}))
        rows.append(_row(f"P{i}", "Glnd Submand (R)", "VendorB", {"dice": 0.75 + i * 0.004}))
    model = build_report_model(rows)
    family = model.family("dice", "VendorB", "VendorA")

    assert min(r.n_pairs for r in family.values()) == 4
    assert model.family_can_detect(family.values()) is True
    # …and the claim is consistent with what the family actually produced.
    assert any(r.p_adjusted <= 0.05 for r in family.values())


# ---- Treatment context (external review: unsafe observation key) -----------


def _linked_row(patient, organ, source, value, linkage):
    row = _row(patient, organ, source, {"dice": value})
    row["linkage_id"] = linkage
    return row


def test_a_case_is_a_patient_and_a_treatment_context():
    """A patient identifier does not identify an observation.

    Re-irradiation and replans give one patient two planning images and two sets
    of structure sets, all carrying the same PatientID. Keyed on the patient
    alone, the second silently overwrote nothing and was discarded.
    """
    rows = [
        _linked_row("P1", "Parotid (L)", "VendorA", 0.80, "course-1"),
        _linked_row("P1", "Parotid (L)", "VendorA", 0.62, "course-2"),
    ]
    model = build_report_model(rows)
    assert model.conflicting_observations == 0
    assert set(model.cases("Parotid (L)", "VendorA", "dice")) == {
        ("P1", "course-1"),
        ("P1", "course-2"),
    }


def test_a_patient_with_two_cases_contributes_neither():
    """Two courses share an anatomy, so they are not independent observations.

    Analysing both would breach one-observation-per-patient; analysing whichever
    sorted first would be an undeclared study-design choice.
    """
    rows = [
        _linked_row("P1", "Parotid (L)", "VendorA", 0.80, "course-1"),
        _linked_row("P1", "Parotid (L)", "VendorA", 0.62, "course-2"),
        _linked_row("P2", "Parotid (L)", "VendorA", 0.81, "course-1"),
    ]
    model = build_report_model(rows)
    assert model.multi_case_patients("Parotid (L)", "VendorA", "dice") == {"P1"}
    assert model.values("Parotid (L)", "VendorA", "dice") == {"P2": 0.81}


def test_the_exclusion_covers_either_side_of_a_comparison():
    """A patient ambiguous for one source is unusable for the pair."""
    rows = [
        _linked_row("P1", "Parotid (L)", "VendorA", 0.80, "course-1"),
        _linked_row("P1", "Parotid (L)", "VendorA", 0.62, "course-2"),
        _linked_row("P1", "Parotid (L)", "VendorB", 0.74, "course-1"),
        _linked_row("P2", "Parotid (L)", "VendorA", 0.81, "course-1"),
        _linked_row("P2", "Parotid (L)", "VendorB", 0.75, "course-1"),
    ]
    model = build_report_model(rows)
    assert model.excluded_patients("Parotid (L)", "dice", "VendorA", "VendorB") == {"P1"}
    result = model.compare("Parotid (L)", "dice", "VendorA", "VendorB")
    assert result.n_pairs == 1  # only P2 survives


def test_an_absent_linkage_keeps_the_previous_behaviour():
    """Rows from sessions predating the stamp are one case per patient."""
    rows = [
        _row("P1", "Parotid (L)", "VendorA", {"dice": 0.80}),
        _row("P2", "Parotid (L)", "VendorA", {"dice": 0.81}),
    ]
    model = build_report_model(rows)
    assert model.multi_case_patients("Parotid (L)", "VendorA", "dice") == set()
    assert len(model.values("Parotid (L)", "VendorA", "dice")) == 2


def test_coverage_counts_a_withheld_patient_as_not_produced():
    """An excluded patient is not silently treated as fully covered."""
    rows = [
        _linked_row("P1", "Parotid (L)", "VendorA", 0.80, "course-1"),
        _linked_row("P1", "Parotid (L)", "VendorA", 0.62, "course-2"),
        _linked_row("P2", "Parotid (L)", "VendorA", 0.81, "course-1"),
    ]
    model = build_report_model(rows)
    cell = model.coverage("Parotid (L)", "dice", "VendorA")
    assert cell.produced == 1
    assert cell.attempted == 2
