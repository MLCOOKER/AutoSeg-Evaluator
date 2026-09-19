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


def test_a_comparison_does_not_change_because_another_is_displayed():
    """The defect that ended the Holm correction here.

    Correcting across whichever organs were selected made the divisor a view
    setting: one organ selected gave a significant result, adding organs took it
    away, and the same data supported both. Each organ is its own question now,
    and its answer is the same whatever else is on screen.
    """
    rows = []
    for organ, offset in (("Parotid (L)", 0.06), ("Parotid (R)", 0.05), ("Brainstem", 0.001)):
        for i in range(10):
            rows.append(_row(f"P{i}", organ, "VendorA", {"dice": 0.80 + i * 0.005}))
            rows.append(_row(f"P{i}", organ, "VendorB", {"dice": 0.80 + i * 0.005 - offset}))
    model = build_report_model(rows)

    alone = model.family("dice", "VendorB", "VendorA", organs=["Parotid (L)"])
    with_others = model.family("dice", reference="VendorB", challenger="VendorA")

    assert set(with_others) == {"Parotid (L)", "Parotid (R)", "Brainstem"}
    assert alone["Parotid (L)"].p_value == with_others["Parotid (L)"].p_value


def test_no_multiplicity_adjustment_is_applied():
    """Reported unadjusted, so nothing has to be unpicked to read a row."""
    rows = []
    for organ in ("Parotid (L)", "Parotid (R)", "Brainstem"):
        for i in range(10):
            rows.append(_row(f"P{i}", organ, "VendorA", {"dice": 0.80 + i * 0.005}))
            rows.append(_row(f"P{i}", organ, "VendorB", {"dice": 0.74 + i * 0.005}))
    family = build_report_model(rows).family("dice", "VendorB", "VendorA")
    assert all(r.p_adjusted is None for r in family.values())
    assert all(r.p_value > 0 for r in family.values())


def test_the_multiplicity_cost_is_stated_rather_than_applied():
    """Scanning twenty rows for the ones below 0.05 is still a selection."""
    from autoseg_evaluator.data.report import expected_false_positives

    assert expected_false_positives(20, 0.05) == pytest.approx(1.0)
    assert expected_false_positives(1, 0.05) == pytest.approx(0.05)
    assert expected_false_positives(0) == 0


def test_nothing_can_be_detected_below_six_paired_patients():
    """Uncorrected, the bound is arithmetic: 2/2^n must not exceed 0.05.

    Five pairs give 0.0625 at best, so no arrangement of contours produces a
    significant result. Six give 0.03125 and can.
    """

    def cohort(patients):
        rows = []
        for i in range(patients):
            rows.append(_row(f"P{i}", "Parotid (L)", "VendorA", {"dice": 0.80}))
            rows.append(_row(f"P{i}", "Parotid (L)", "VendorB", {"dice": 0.70}))
        return build_report_model(rows)

    for patients in (2, 3, 4, 5):
        model = cohort(patients)
        family = model.family("dice", "VendorB", "VendorA")
        assert model.family_can_detect(family.values()) is False, patients

    for patients in (6, 8, 10):
        model = cohort(patients)
        family = model.family("dice", "VendorB", "VendorA")
        assert model.family_can_detect(family.values()) is True, patients


def test_the_number_of_organs_no_longer_affects_detectability():
    """It used to: 26 organs put every result past the Holm threshold."""
    rows = []
    for organ_index in range(26):
        for i in range(10):
            rows.append(_row(f"P{i}", f"Organ{organ_index}", "VendorA", {"dice": 0.80}))
            rows.append(_row(f"P{i}", f"Organ{organ_index}", "VendorB", {"dice": 0.70}))
    model = build_report_model(rows)
    family = model.family("dice", "VendorB", "VendorA")
    assert len(family) == 26
    assert model.family_can_detect(family.values()) is True


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
    """One thin organ must not declare the whole table undetectable.

    Caught by smoke-testing the tab: the banner said nothing could reach
    significance while a ten-patient organ sat on screen at p = 0.002. The
    comparison with the most pairs decides whether anything shown can reject.
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
    # …and the claim is consistent with what the table actually produced.
    assert any(r.p_value <= 0.05 for r in family.values())


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


# ---- Reporting the confidence set -----------------------------------------


def test_every_confidence_status_reads_differently():
    """The four absences are distinct claims and must not merge back into one dash.

    An earlier version printed "— not estimable" for all of them, so "no shift
    is rejectable at this sample size" was indistinguishable from "the accepted
    set is a single point" — opposite situations.
    """
    from autoseg_evaluator.core.statistics import ConfidenceSet, IntervalStatus
    from autoseg_evaluator.data.report import interval_text

    rendered = {
        IntervalStatus.INTERVAL: interval_text(
            ConfidenceSet(IntervalStatus.INTERVAL, -0.038, -0.034, 1)
        ),
        IntervalStatus.SINGLETON: interval_text(
            ConfidenceSet(IntervalStatus.SINGLETON, 0.1, 0.1, 1)
        ),
        IntervalStatus.DISCONNECTED: interval_text(
            ConfidenceSet(IntervalStatus.DISCONNECTED, -0.02, 0.03, 2)
        ),
        IntervalStatus.UNBOUNDED: interval_text(ConfidenceSet(IntervalStatus.UNBOUNDED)),
        IntervalStatus.NO_DATA: interval_text(ConfidenceSet(IntervalStatus.NO_DATA)),
    }
    assert len(set(rendered.values())) == len(rendered)
    assert rendered[IntervalStatus.INTERVAL] == "-0.0380, -0.0340"
    assert rendered[IntervalStatus.SINGLETON] == "+0.1000 only"
    assert "enclosing" in rendered[IntervalStatus.DISCONNECTED]
    assert rendered[IntervalStatus.UNBOUNDED] == "— unbounded at this n"


def test_the_register_prints_what_the_tab_prints():
    """The document is published so an auditor can check the software.

    That only works if both render a confidence set the same way, which is why
    there is one formatter rather than a copy in the generator script.
    """
    from pathlib import Path

    register = Path("docs/V3_REPORT_STATISTICS_REGISTER.md")
    if not register.exists():  # pragma: no cover - docs absent in a sdist
        pytest.skip("register not present")
    text = register.read_text(encoding="utf-8")
    # The n = 4 row of the worked example exercises the status that changed.
    assert "— unbounded at this n" in text
    assert "| Glnd Submand (R) | 4 | 4 / 10 |" in text


# ---- Family axis ----------------------------------------------------------


def _multi_vendor_rows(organ="Parotid (L)"):
    rows = []
    for i in range(10):
        rows.append(_row(f"P{i}", organ, "Limbus", {"dice": 0.84 + i * 0.003}))
        rows.append(_row(f"P{i}", organ, "MVision", {"dice": 0.81 + i * 0.003}))
        rows.append(_row(f"P{i}", organ, "Radformation", {"dice": 0.835 + i * 0.003}))
        rows.append(_row(f"P{i}", organ, "TheraPanacea", {"dice": 0.82 + i * 0.003}))
    return rows


def test_a_source_wise_family_holds_every_other_source():
    """ "All the other vendors" is fixed by the data, not chosen — unlike organs."""
    model = build_report_model(_multi_vendor_rows())
    family = model.family_across_sources("dice", "Limbus", "Parotid (L)")
    assert set(family) == {"MVision", "Radformation", "TheraPanacea"}
    assert "Limbus" not in family  # never compared with itself
    assert all(r is not None for r in family.values())


def test_the_source_wise_rows_are_also_unadjusted():
    """Same rule on both axes: each row answers its own question."""
    model = build_report_model(_multi_vendor_rows())
    family = model.family_across_sources("dice", "Limbus", "Parotid (L)")
    assert all(r.p_adjusted is None for r in family.values())

    # And a row is unchanged by how many sources sit beside it.
    pair = model.family_across_sources("dice", "Limbus", "Parotid (L)", sources=["MVision"])
    assert pair["MVision"].p_value == family["MVision"].p_value


def test_the_two_axes_agree_on_a_shared_cell():
    """Same pair, same organ, same numbers, whichever way you got there."""
    model = build_report_model(_multi_vendor_rows())
    by_organ = model.family("dice", "Limbus", "MVision", organs=["Parotid (L)"])
    by_source = model.family_across_sources("dice", "Limbus", "Parotid (L)")

    organ_row = by_organ["Parotid (L)"]
    source_row = by_source["MVision"]
    assert organ_row.hl_estimate == pytest.approx(source_row.hl_estimate)
    # Identical in every respect: the same pair, the same organ, the same test.
    # Which axis it was reached by is a navigation choice, not a statistical one.
    assert organ_row.p_value == pytest.approx(source_row.p_value)
    assert organ_row.p_adjusted is None and source_row.p_adjusted is None


def test_a_source_that_never_produced_the_organ_stays_in_the_divisor():
    rows = _multi_vendor_rows()
    rows = [r for r in rows if r["test_source_label"] != "TheraPanacea"]
    rows.append(_row("P0", "Brainstem", "TheraPanacea", {"dice": 0.9}))
    model = build_report_model(rows)
    family = model.family_across_sources("dice", "Limbus", "Parotid (L)")
    assert family["TheraPanacea"] is None
    assert len(family) == 3


def test_the_axis_nouns_are_usable_for_prose():
    from autoseg_evaluator.data.report import FamilyAxis

    assert FamilyAxis.ORGANS.noun == "organ"
    assert FamilyAxis.ORGANS.plural == "organs"
    assert FamilyAxis.SOURCES.noun == "source"
    assert FamilyAxis.SOURCES.plural == "sources"


# ---- Metric families ------------------------------------------------------


@pytest.mark.parametrize(
    ("metric", "expected"),
    [
        ("dice", "Geometric"),
        ("surface_dice", "Geometric"),
        ("hausdorff95", "Geometric"),
        ("volume_diff_cc", "Geometric"),
        ("com_offset_mm", "Geometric"),
        ("precision", "Geometric"),
        ("dmin_gy", "Dosimetric"),
        ("dmean_gy", "Dosimetric"),
        ("D95_gy", "Dosimetric"),
        ("V20gy_cc", "Dosimetric"),
        ("V40Gy_pct", "Dosimetric"),
        # Per-vendor agreement with the consensus, so comparable like Dice.
        ("staple_sensitivity", "Geometric"),
        ("staple_specificity", "Geometric"),
        # Properties of how the consensus was built, one per organ.
        ("mean_entropy", "Consensus"),
        ("n_raters", "Consensus"),
        ("something_else", "Other"),
    ],
)
def test_metric_family(metric, expected):
    from autoseg_evaluator.data.report import metric_family

    assert metric_family(metric) == expected


def test_metrics_are_grouped_for_the_selector():
    """Geometric before dosimetric, and no empty group offered."""
    rows = [
        _row("P1", "Parotid (L)", "VendorA", {"dice": 0.8, "hausdorff95": 3.0, "dmean_gy": 30.0}),
    ]
    model = build_report_model(rows)
    assert model.metrics_by_family() == [
        ("Geometric", ["dice", "hausdorff95"]),
        ("Dosimetric", ["dmean_gy"]),
    ]


def test_a_cohort_with_no_dose_offers_no_dose_group():
    model = build_report_model([_row("P1", "Parotid (L)", "VendorA", {"dice": 0.8})])
    assert model.metrics_by_family() == [("Geometric", ["dice"])]


# ---- A consensus is a ground truth, not a contaminant ----------------------


def _staple_row(patient, organ, source, metrics, mode="Multi-observer STAPLE"):
    row = _row(patient, organ, source, metrics)
    row["comparison_mode"] = mode
    row["gt_source_label"] = "STAPLE Consensus"
    return row


def test_the_reference_is_part_of_the_observation():
    """The same contour measured against two references is two measurements.

    With the reference missing from the key they collided, and one was dropped
    as a conflicting observation with the winner decided by row order. Both are
    now kept, and the report shows one reference at a time.
    """
    rows = [
        _row("P1", "Parotid (L)", "VendorA", {"dice": 0.80}),
        _staple_row("P1", "Parotid (L)", "VendorA", {"dice": 0.99}),
    ]
    model = build_report_model(rows)
    assert model.conflicting_observations == 0
    assert len(model.observations) == 2
    assert set(model.ground_truths()) == {"manual", "STAPLE Consensus"}

    # And the order they arrive in changes nothing.
    reversed_model = build_report_model(list(reversed(rows)))
    assert len(reversed_model.observations) == 2
    assert reversed_model.conflicting_observations == 0


def test_a_view_shows_one_reference_at_a_time():
    rows = [
        _row("P1", "Parotid (L)", "VendorA", {"dice": 0.80}),
        _staple_row("P1", "Parotid (L)", "VendorA", {"dice": 0.99}),
    ]
    model = build_report_model(rows)
    assert model.for_ground_truth("manual").values("Parotid (L)", "VendorA", "dice") == {"P1": 0.80}
    assert model.for_ground_truth("STAPLE Consensus").values("Parotid (L)", "VendorA", "dice") == {
        "P1": 0.99
    }


def test_a_consensus_is_preferred_when_one_exists():
    """User ruling: a consensus built on Tab 2 is what the cohort is measured against."""
    rows = [
        _row("P1", "Parotid (L)", "VendorA", {"dice": 0.80}),
        _staple_row("P1", "Parotid (L)", "VendorA", {"dice": 0.99}),
    ]
    model = build_report_model(rows)
    assert model.preferred_ground_truth() == "STAPLE Consensus"
    assert model.ground_truths()[0] == "STAPLE Consensus"


def test_without_a_consensus_the_busiest_reference_wins():
    rows = [_row(f"P{i}", "Parotid (L)", "VendorA", {"dice": 0.8}) for i in range(5)]
    sparse = _row("P9", "Parotid (L)", "VendorA", {"dice": 0.8}, gt="second reader")
    model = build_report_model([*rows, sparse])
    assert model.preferred_ground_truth() == "manual"


@pytest.mark.parametrize(
    "mode", ["Multi-observer STAPLE", "Generic STAPLE with GT", "Generic STAPLE no GT"]
)
def test_consensus_comparisons_are_analysed_not_discarded(mode):
    """Measuring every source against a consensus is a real analysis."""
    rows = [
        _staple_row(f"P{i}", "Parotid (L)", "VendorA", {"dice": 0.8}, mode=mode) for i in range(4)
    ]
    model = build_report_model(rows)
    assert len(model.values("Parotid (L)", "VendorA", "dice")) == 4
    assert model.ground_truths() == ["STAPLE Consensus"]


def test_the_details_row_is_still_not_a_comparison():
    """One row per organ describing how the consensus was built, not per source."""
    rows = [
        _staple_row("P1", "Parotid (L)", "VendorA", {"dice": 0.8}),
        _staple_row("P1", "Parotid (L)", "VendorA", {"mean_entropy": 0.2}, mode="STAPLE Details"),
    ]
    model = build_report_model(rows)
    assert model.metrics() == ["dice"]


def test_construction_metrics_are_dropped_but_per_vendor_ones_are_kept():
    """Sensitivity against the consensus describes a vendor; entropy describes the build."""
    rows = [
        _staple_row(
            "P1",
            "Parotid (L)",
            "VendorA",
            {
                "dice": 0.80,
                "staple_sensitivity": 0.91,
                "staple_specificity": 0.99,
                "mean_entropy": 0.2,
                "n_raters": 4,
                "consensus_volume_cc": 12.5,
            },
        )
    ]
    model = build_report_model(rows)
    assert model.metrics() == ["dice", "staple_sensitivity", "staple_specificity"]


def test_a_single_reference_view_is_the_model_itself():
    """The ordinary case must not pay for the feature."""
    model = build_report_model([_row("P1", "Parotid (L)", "VendorA", {"dice": 0.8})])
    assert model.for_ground_truth("manual") is model
    assert model.for_ground_truth("") is model


def test_coverage_does_not_borrow_patients_from_another_reference():
    """A patient seen only under the manual GT is not eligible under the consensus."""
    rows = [
        _staple_row("P1", "Parotid (L)", "VendorA", {"dice": 0.8}),
        _row("P2", "Parotid (L)", "VendorA", {"dice": 0.8}),
        _row("P3", "Parotid (L)", "VendorA", {"dice": 0.8}),
    ]
    model = build_report_model(rows).for_ground_truth("STAPLE Consensus")
    cell = model.coverage("Parotid (L)", "dice", "VendorA")
    assert cell.produced == 1
    assert cell.eligible == 1  # P2 and P3 belong to the other reference


# ---- The two consensus references are different analyses -------------------


def test_the_two_consensus_labels_differ_by_more_than_capitalisation():
    """They lived as "STAPLE Consensus" and "STAPLE consensus".

    Telling a multi-observer consensus from a drawer-pool one rested on nobody
    tidying the capitalisation, and the reference selector showed two entries a
    reader could not distinguish.
    """
    from autoseg_evaluator.core.staple import DRAWER_POOL_LABEL, MULTI_OBSERVER_LABEL

    assert DRAWER_POOL_LABEL.lower() != MULTI_OBSERVER_LABEL.lower()
    assert "drawer pool" in DRAWER_POOL_LABEL.lower()


def test_both_consensus_kinds_are_recognised_as_consensus():
    from autoseg_evaluator.core.staple import DRAWER_POOL_LABEL, MULTI_OBSERVER_LABEL
    from autoseg_evaluator.data.report import is_consensus_reference

    assert is_consensus_reference(MULTI_OBSERVER_LABEL)
    assert is_consensus_reference(DRAWER_POOL_LABEL)
    assert not is_consensus_reference("Manual")


def test_a_legacy_drawer_pool_label_is_mapped_forward():
    """Sessions saved before the rename must not become a third reference."""
    from autoseg_evaluator.core.staple import DRAWER_POOL_LABEL, LEGACY_DRAWER_POOL_LABEL

    old = _row("P1", "Parotid (L)", "VendorA", {"dice": 0.80}, gt=LEGACY_DRAWER_POOL_LABEL)
    old["comparison_mode"] = "Generic STAPLE with GT"
    new = _row("P2", "Parotid (L)", "VendorA", {"dice": 0.82}, gt=DRAWER_POOL_LABEL)
    new["comparison_mode"] = "Generic STAPLE with GT"

    model = build_report_model([old, new])
    assert model.ground_truths() == [DRAWER_POOL_LABEL]
    assert len(model.values("Parotid (L)", "VendorA", "dice")) == 2


def test_the_two_consensus_kinds_stay_separate_references():
    """Different pools, different caveats — they must not merge."""
    from autoseg_evaluator.core.staple import DRAWER_POOL_LABEL, MULTI_OBSERVER_LABEL

    observers = _row("P1", "Parotid (L)", "VendorA", {"dice": 0.80}, gt=MULTI_OBSERVER_LABEL)
    observers["comparison_mode"] = "Multi-observer STAPLE"
    drawer = _row("P1", "Parotid (L)", "VendorA", {"dice": 0.91}, gt=DRAWER_POOL_LABEL)
    drawer["comparison_mode"] = "Generic STAPLE with GT"

    model = build_report_model([observers, drawer])
    assert set(model.ground_truths()) == {MULTI_OBSERVER_LABEL, DRAWER_POOL_LABEL}
    assert model.conflicting_observations == 0
    assert model.for_ground_truth(MULTI_OBSERVER_LABEL).values(
        "Parotid (L)", "VendorA", "dice"
    ) == {"P1": 0.80}
    assert model.for_ground_truth(DRAWER_POOL_LABEL).values("Parotid (L)", "VendorA", "dice") == {
        "P1": 0.91
    }


def test_the_multi_observer_consensus_is_preferred_over_the_drawer_pool():
    """The vendors are not in the multi-observer pool, so it is the cleaner reference."""
    from autoseg_evaluator.core.staple import DRAWER_POOL_LABEL, MULTI_OBSERVER_LABEL

    observers = _row("P1", "Parotid (L)", "VendorA", {"dice": 0.80}, gt=MULTI_OBSERVER_LABEL)
    observers["comparison_mode"] = "Multi-observer STAPLE"
    drawer = _row("P1", "Parotid (L)", "VendorA", {"dice": 0.91}, gt=DRAWER_POOL_LABEL)
    drawer["comparison_mode"] = "Generic STAPLE with GT"
    manual = _row("P1", "Parotid (L)", "VendorA", {"dice": 0.75})

    model = build_report_model([drawer, manual, observers])
    assert model.preferred_ground_truth() == MULTI_OBSERVER_LABEL


def test_the_drawer_pool_ranks_below_the_manual_contours():
    """Its pool contains the very sources being scored against it.

    Every other consensus outranks the manual ground truth; this one does not,
    because a vendor is partly measured against itself. It stays available as a
    supplementary analysis but must never be the default.
    """
    from autoseg_evaluator.core.staple import DRAWER_POOL_LABEL, MULTI_OBSERVER_LABEL
    from autoseg_evaluator.data.report import reference_rank

    assert reference_rank(MULTI_OBSERVER_LABEL) == 0
    assert reference_rank("Manual") == 1
    assert reference_rank(DRAWER_POOL_LABEL) == 2

    drawer = _row("P1", "Parotid (L)", "VendorA", {"dice": 0.91}, gt=DRAWER_POOL_LABEL)
    drawer["comparison_mode"] = "Generic STAPLE with GT"
    manual = _row("P1", "Parotid (L)", "VendorA", {"dice": 0.75})
    model = build_report_model([drawer, manual])
    assert model.preferred_ground_truth() == "manual"
    assert model.ground_truths() == ["manual", DRAWER_POOL_LABEL]
