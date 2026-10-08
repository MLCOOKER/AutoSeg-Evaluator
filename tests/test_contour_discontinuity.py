"""Contour Discontinuity: docs/V3_SKIPPED_SLICE_SPEC.md.

Slices of the synthetic CT sit every 2 mm from z = -30, so z = -2, 0, 2 and 4 mm
are slices 14, 15, 16 and 17.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import SimpleITK as sitk  # noqa: E402
from pydicom.dataset import Dataset  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from autoseg_evaluator.core.masks import (  # noqa: E402
    MaskConversionError,
    mask_with_reading,
    skipped_slice_positions,
)
from autoseg_evaluator.data.report import build_report_model  # noqa: E402
from autoseg_evaluator.workers.metrics_worker import MetricsWorker  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication(sys.argv)


def _image():
    image = sitk.Image(80, 80, 30, sitk.sitkUInt8)
    image.SetSpacing((1.0, 1.0, 2.0))
    image.SetOrigin((-40.0, -40.0, -30.0))
    return image


def _box(x0=-5.5, x1=4.5, y0=-5.5, y1=4.5):
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], float)


def _structure(planes: dict[float, list[np.ndarray]]) -> Dataset:
    """An RTSTRUCT whose ROI 1 holds ``{z: [outline, …]}``."""
    roi = Dataset()
    roi.ROINumber = 1
    roi.ROIName = "Organ"
    item = Dataset()
    item.ReferencedROINumber = 1
    item.ContourSequence = []
    for z, loops in planes.items():
        for loop in loops:
            contour = Dataset()
            contour.ContourGeometricType = "CLOSED_PLANAR"
            contour.NumberOfContourPoints = len(loop)
            contour.ContourData = [
                f"{v:.6f}" for v in np.column_stack([loop, np.full(len(loop), z)]).ravel()
            ]
            item.ContourSequence.append(contour)
    ds = Dataset()
    ds.StructureSetROISequence = [roi]
    ds.ROIContourSequence = [item]
    return ds


def _skipped(planes):
    return skipped_slice_positions(_image(), _structure(planes), 1)


# ---- What counts --------------------------------------------------------------


def test_a_missing_slice_between_the_first_and_last_is_found():
    assert _skipped({z: [_box()] for z in (-2.0, 0.0, 4.0)}) == [2.0]


def test_two_missing_slices_are_both_found():
    assert _skipped({z: [_box()] for z in (-4.0, 4.0)}) == [-2.0, 0.0, 2.0]


def test_contiguous_contours_skip_nothing():
    assert _skipped({z: [_box()] for z in (-2.0, 0.0, 2.0)}) == []


def test_one_slice_cannot_skip_one():
    assert _skipped({0.0: [_box()]}) == []


def test_pieces_on_one_slice_are_not_a_discontinuity():
    """Islands within a slice can arise for many reasons, and are ignored."""
    islands = [_box(-10.5, -6.5), _box(6.5, 10.5)]
    assert _skipped({z: islands for z in (-2.0, 0.0, 2.0)}) == []


def test_a_slice_holding_only_an_outline_with_no_area_is_skipped():
    """It holds no tissue: the reading drops it."""
    flat = np.array([[-5.0, 0.0], [5.0, 0.0], [5.0, 0.0], [-5.0, 0.0]])
    assert _skipped({-2.0: [_box()], 0.0: [flat], 2.0: [_box()]}) == [0.0]


def test_a_contour_too_small_to_fill_a_voxel_still_holds_its_slice():
    """Why it is read from the contours: the mask would show a gap here."""
    tiny = _box(0.1, 0.4, 0.1, 0.4)  # between voxel centres
    planes = {-2.0: [_box()], 0.0: [tiny], 2.0: [_box()]}
    mask, _ = mask_with_reading(_image(), _structure(planes), 1)
    assert not sitk.GetArrayViewFromImage(mask)[15].any()  # the mask's slice is empty
    assert _skipped(planes) == []


def test_a_structure_with_no_contours_cannot_be_checked():
    with pytest.raises(MaskConversionError):
        _skipped({})


# ---- The worker -------------------------------------------------------------------


def _worker(audit=True):
    return MetricsWorker(None, [], {"audit": {"sidecar": True}} if audit else {})


def test_the_worker_writes_the_flag_and_the_skipped_slices():
    worker = _worker()
    rtss = _structure({z: [_box()] for z in (-2.0, 0.0, 4.0)})
    result = worker._contour_discontinuity("P1", "t.1", 1, _image(), rtss)
    row = {"metrics": {}}
    worker._discontinuity_into_row(row, result)
    assert row["metrics"]["contour_discontinuity"] is True
    assert row["audit"]["discontinuity"] == {"status": "checked", "skipped_slices_mm": [2.0]}

    whole = _structure({z: [_box()] for z in (-2.0, 0.0, 2.0)})
    row = {"metrics": {}}
    worker._discontinuity_into_row(
        row, worker._contour_discontinuity("P1", "t.2", 1, _image(), whole)
    )
    assert row["metrics"]["contour_discontinuity"] is False


def test_a_contour_that_cannot_be_read_gets_no_flag():
    worker = _worker()
    row = {"metrics": {}}
    worker._discontinuity_into_row(
        row, worker._contour_discontinuity("P1", "t.1", 1, _image(), _structure({}))
    )
    assert "contour_discontinuity" not in row["metrics"]
    assert row["audit"]["discontinuity"]["status"].startswith("not checked")


def test_the_check_is_cached_per_structure_and_dropped_with_the_patient():
    worker = _worker()
    worker._contour_discontinuity("P1", "t.1", 1, _image(), _structure({0.0: [_box()]}))
    assert ("P1", "t.1", 1) in worker._discontinuity_cache
    worker._evict_patient_caches("P1")
    assert worker._discontinuity_cache == {}


# ---- Table, report and PDF -----------------------------------------------------------


def _row(patient, flag, organ="Parotid (L)", source="VendorA"):
    metrics = {"dice": 0.8}
    if flag is not None:
        metrics["contour_discontinuity"] = flag
    return {
        "patient_id": patient,
        "drawer": organ,
        "canonical_organ": organ,
        "comparison_mode": "vs GT",
        "gt_source_label": "Manual",
        "test_source_label": source,
        "gt_roi_name": organ,
        "metrics": metrics,
    }


ROWS = [_row("P01", True), _row("P02", False), _row("P03", False), _row("P04", None)]


def test_the_column_is_named_and_banded():
    from autoseg_evaluator.data.results import CANONICAL_METRIC_COLUMNS, metric_display_label
    from autoseg_evaluator.ui.tabs.results import _band_for_metric_key

    assert "contour_discontinuity" in CANONICAL_METRIC_COLUMNS
    assert metric_display_label("contour_discontinuity") == "Contour Discontinuity"
    assert _band_for_metric_key("contour_discontinuity") == "checks"


def test_the_report_counts_discontinuous_contours_and_does_not_analyse_them():
    model = build_report_model(ROWS)
    assert model.checked_discontinuity()
    assert model.discontinuity("Parotid (L)", "VendorA") == (1, 3)
    assert model.discontinuity("Parotid (L)", "VendorB") is None
    assert "contour_discontinuity" not in model.metrics()  # a yes/no, not a metric


def _tab(rows):
    from autoseg_evaluator.data.results import ResultsManager
    from autoseg_evaluator.ui.tabs.report import ReportTab

    tab = ReportTab()
    manager = ResultsManager()
    manager.add_rows(rows)
    tab.set_results_manager(manager)
    tab.refresh()
    tab._metric_combo.setCurrentText("dice")
    return tab


def test_the_coverage_table_and_pdf_carry_the_count(qapp, tmp_path):
    from autoseg_evaluator.ui.tabs.report import COVERAGE_DISCONTINUITY_COLUMN

    tab = _tab(ROWS)
    table = tab._coverage_table
    assert table.horizontalHeaderItem(COVERAGE_DISCONTINUITY_COLUMN).text() == (
        "Contour Discontinuity"
    )
    assert not table.isColumnHidden(COVERAGE_DISCONTINUITY_COLUMN)
    assert table.item(0, COVERAGE_DISCONTINUITY_COLUMN).text() == "1 of 3"
    html = tab._pdf_html(Path(tmp_path))
    assert ">Contour Discontinuity</th>" in html and ">1 of 3</td>" in html
    tab.deleteLater()


def test_the_count_is_hidden_when_the_check_was_not_run(qapp, tmp_path):
    from autoseg_evaluator.ui.tabs.report import COVERAGE_DISCONTINUITY_COLUMN

    tab = _tab([_row(f"P0{p}", None) for p in range(3)])
    assert tab._coverage_table.isColumnHidden(COVERAGE_DISCONTINUITY_COLUMN)
    assert "Contour Discontinuity" not in tab._pdf_html(Path(tmp_path))
    tab.deleteLater()


def test_the_compute_tab_has_no_switch_for_it_and_says_so(qapp):
    """Checked on every run (see test_ptv_overlap for the run itself)."""
    from PySide6.QtWidgets import QLabel

    from autoseg_evaluator.ui.tabs.compute import ComputeTab

    tab = ComputeTab()
    assert "contour_discontinuity" not in tab._geom_checks
    notes = " ".join(label.text() for label in tab.findChildren(QLabel))
    assert "Contour Discontinuity" in notes
    tab.deleteLater()


def test_the_audit_sidecar_carries_the_skipped_slices():
    from autoseg_evaluator.data import sidecar

    row = {
        "patient_id": "P01",
        "metrics": {},
        "audit": {"discontinuity": {"status": "checked", "skipped_slices_mm": [2.0]}},
    }
    document = sidecar.build([row])
    assert document["records"][0]["discontinuity"]["skipped_slices_mm"] == [2.0]
    assert "discontinuity" in document["notes"]
