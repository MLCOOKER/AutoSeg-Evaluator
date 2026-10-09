"""Likert scores in the Report.

Until v3.0.2 a scores-only study never reached the Report, and a score that did
had no direction, so a comparison could not say which source was favoured.
"""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from autoseg_evaluator.core.readable import (  # noqa: E402
    SCALE_LIKERT,
    metric_scale,
    readable_metric,
)
from autoseg_evaluator.data.report import (  # noqa: E402
    FAMILY_QUALITATIVE,
    build_report_model,
    favours,
    metric_direction,
)
from autoseg_evaluator.data.results import ResultsManager  # noqa: E402

PATIENTS = [f"P{i}" for i in range(1, 9)]


def _scored(manager, patient, source, score, *, drawer="Parotid_L", is_gt=False):
    manager.upsert_qualitative_score(
        patient_id=patient,
        drawer=drawer,
        source_label=source,
        roi_name=drawer,
        roi_number=2 if source == "Limbus" else 3,
        is_gt=is_gt,
        rater="Alice",
        score=score,
        blinded=True,
    )


def _scores_only():
    """Eight patients graded on two sources, nothing computed."""
    manager = ResultsManager()
    for i, patient in enumerate(PATIENTS):
        _scored(manager, patient, "Limbus", 5 if i % 3 else 4)
        _scored(manager, patient, "Radformation", 3 if i % 2 else 2)
    return manager


def _computed(patient, source, dice, *, gt, score=None):
    metrics = {"dice": dice}
    if score is not None:
        metrics["likert_Alice"] = score
    return {
        "patient_id": patient,
        "drawer": "Parotid_L",
        "canonical_organ": "Parotid_L",
        "comparison_mode": "vs GT",
        "gt_source_label": gt,
        "test_source_label": source,
        "truncated": True,
        "metrics": metrics,
    }


def test_a_scores_only_study_reaches_the_report():
    model = build_report_model(_scores_only().rows())
    assert model.metrics() == ["likert_Alice"]
    assert model.sources() == ["Limbus", "Radformation"]
    assert len(model.values("Parotid_L", "Limbus", "likert_Alice")) == len(PATIENTS)


def test_likert_is_its_own_family_and_higher_is_better():
    model = build_report_model(_scores_only().rows())
    assert model.metrics_by_family() == [(FAMILY_QUALITATIVE, ["likert_Alice"])]
    assert metric_direction("likert_Alice") == 1
    assert favours("likert_Alice", 1.5) == "a"


def test_the_comparison_favours_the_better_scored_source():
    model = build_report_model(_scores_only().rows())
    result = model.compare("Parotid_L", "likert_Alice", "Limbus", "Radformation")
    assert result is not None and result.n_pairs == len(PATIENTS)
    assert favours("likert_Alice", result.hl_estimate) == "a"


def test_a_score_shows_once_in_every_ground_truth_view():
    """The contour was measured against two references; it was scored once."""
    rows = [
        _computed(p, source, 0.8, gt=gt, score=4)
        for p in PATIENTS[:3]
        for source in ("Limbus", "Radformation")
        for gt in ("Manual", "STAPLE Consensus")
    ]
    model = build_report_model(rows)
    assert model.duplicates_collapsed == 0
    for reference in ("Manual", "STAPLE Consensus"):
        view = model.for_ground_truth(reference)
        assert len(view.values("Parotid_L", "Limbus", "likert_Alice")) == 3
        assert len(view.values("Parotid_L", "Limbus", "dice")) == 3


def test_scores_only_rows_leave_the_truncation_reading_alone():
    manager = ResultsManager()
    manager.add_rows([_computed(p, "Limbus", 0.8, gt="Manual") for p in PATIENTS[:3]])
    _scored(manager, PATIENTS[0], "Manual", 5, is_gt=True)
    model = build_report_model(manager.rows())
    assert model.truncation("Parotid_L") is True


def test_a_likert_score_is_named_and_drawn_on_its_own_scale():
    assert readable_metric("likert_Alice") == "Likert score — Alice"
    assert metric_scale("likert_Alice") == SCALE_LIKERT


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication(sys.argv)


def test_the_report_tab_offers_and_compares_scores(qapp):
    from autoseg_evaluator.ui.tabs.report import ReportTab

    tab = ReportTab()
    try:
        tab.set_results_manager(_scores_only())
        tab.refresh()
        items = [tab._metric_combo.itemText(i) for i in range(tab._metric_combo.count())]
        assert items == [f"— {FAMILY_QUALITATIVE} —", "likert_Alice"]
        tab._metric_combo.setCurrentText("likert_Alice")
        tab._reference_combo.setCurrentText("Limbus")
        tab._challenger_combo.setCurrentText("Radformation")
        assert tab._comparison_table.rowCount() >= 1
    finally:
        tab.deleteLater()
