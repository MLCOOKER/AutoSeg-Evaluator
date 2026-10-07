"""Overlap with the PTV: docs/V3_PTV_OVERLAP_SPEC.md.

The worker's volumes are checked on structures whose outlines lie on voxel
edges, so every mask is exact and every expected volume is a voxel count.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import SimpleITK as sitk  # noqa: E402
from pydicom.dataset import Dataset  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from autoseg_evaluator.core.masks import mask_with_reading  # noqa: E402
from autoseg_evaluator.core.metrics import overlap_cc  # noqa: E402
from autoseg_evaluator.core.organ_groups import is_ptv  # noqa: E402
from autoseg_evaluator.data.metadata import OrganEntry  # noqa: E402
from autoseg_evaluator.data.report import (  # noqa: E402
    FAMILY_PTV,
    build_report_model,
    metric_direction,
    metric_family,
)
from autoseg_evaluator.workers.metrics_worker import (  # noqa: E402
    PTV_STATUS_CONSENSUS,
    PTV_STATUS_MEASURED,
    PTV_STATUS_NONE,
    MetricsWorker,
)

#: One voxel: 1 x 1 x 2 mm.
VOXEL_CC = 0.002


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication(sys.argv)


def _image():
    """A CT geometry with voxel centres on whole millimetres in x and y."""
    image = sitk.Image(80, 80, 30, sitk.sitkUInt8)
    image.SetSpacing((1.0, 1.0, 2.0))
    image.SetOrigin((-40.0, -40.0, -30.0))
    return image


def _box(x0, x1, y0=-5.5, y1=4.5):
    """A rectangle with edges on voxel edges, so its mask is exact."""
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], float)


def _structure_set(rois: dict[int, tuple[str, dict[float, np.ndarray]]]) -> Dataset:
    """An RTSTRUCT holding each ``{number: (name, {z: outline})}``."""
    ds = Dataset()
    ds.StructureSetROISequence = []
    ds.ROIContourSequence = []
    for number, (name, planes) in rois.items():
        roi = Dataset()
        roi.ROINumber = number
        roi.ROIName = name
        ds.StructureSetROISequence.append(roi)
        item = Dataset()
        item.ReferencedROINumber = number
        item.ContourSequence = []
        for z, loop in planes.items():
            contour = Dataset()
            contour.ContourGeometricType = "CLOSED_PLANAR"
            contour.NumberOfContourPoints = len(loop)
            contour.ContourData = [
                f"{v:.6f}" for v in np.column_stack([loop, np.full(len(loop), z)]).ravel()
            ]
            item.ContourSequence.append(contour)
        ds.ROIContourSequence.append(item)
    return ds


SLICES = (-2.0, 0.0, 2.0)

#: The ground truth's structure set. Two PTVs, typed PTV and named anything,
#: which share the voxels at x = 3 on the middle slice; and a structure named
#: like a PTV but typed as an organ, covering everything, which must not count.
GT_ROIS = {
    1: ("Parotid_L", {z: _box(-5.5, 4.5) for z in SLICES}),
    2: ("Boost", {z: _box(2.5, 12.5) for z in SLICES}),
    3: ("PTV_56", {0.0: _box(-15.5, 3.5)}),
    4: ("PTV_decoy", {z: _box(-20.5, 20.5, -20.5, 20.5) for z in SLICES}),
}
GT_TYPES = {1: "ORGAN", 2: "PTV", 3: "ptv", 4: "ORGAN"}

#: The union inside the ground truth: 2 columns x 10 rows on the outer slices,
#: the whole 10 x 10 on the middle one. Adding the PTVs instead of combining
#: them would give 60 + 90 = 150 voxels.
GT_IN_PTV_CC = (20 + 100 + 20) * VOXEL_CC
#: The test, shifted 4 mm into the boost: 6 x 10 on the outer slices, 10 x 10
#: on the middle one.
TEST_IN_PTV_CC = (60 + 100 + 60) * VOXEL_CC


def _worker(*, audit=True, types=GT_TYPES, rois=GT_ROIS):
    worker = MetricsWorker(None, [], {"audit": {"sidecar": True}} if audit else {})
    entry = SimpleNamespace(
        organs=[
            OrganEntry(roi_number=n, roi_name=name, interpreted_type=types.get(n, ""))
            for n, (name, _planes) in rois.items()
        ]
    )
    worker._find_rtstruct_entry = lambda _patient, _sop: entry  # type: ignore[method-assign]
    return worker


def _group(synthetic=False):
    return {"patient_id": "P1", "gt_sop": "gt.1", "_gt_synthetic": synthetic}


def _measure(worker, group=None, rois=GT_ROIS):
    ct = _image()
    gt_rtss = _structure_set(rois)
    gt_mask, _ = mask_with_reading(ct, gt_rtss, 1)
    test_rtss = _structure_set({1: ("Parotid_L", {z: _box(-1.5, 8.5) for z in SLICES})})
    test_mask, _ = mask_with_reading(ct, test_rtss, 1)
    basis = worker._ptv_basis(group or _group(), ct, gt_rtss, gt_mask)
    row = {"metrics": {}}
    worker._ptv_into_row(row, basis, test_mask)
    return row, basis


# ---- Pieces --------------------------------------------------------------------


def test_overlap_is_a_voxel_count_times_the_voxel_volume():
    image = _image()
    filled = np.zeros((30, 80, 80), np.uint8)  # (z, y, x)
    filled[5:8, 10:20, 10:20] = 1  # 300 voxels
    mask = sitk.GetImageFromArray(filled)
    mask.CopyInformation(image)
    region = np.zeros(filled.shape, bool)
    region[5:8, 10:20, 15:30] = True  # overlaps 5 columns of it: 150 voxels
    assert overlap_cc(mask, region) == pytest.approx(150 * VOXEL_CC)
    assert overlap_cc(mask, np.zeros_like(region)) == 0.0
    with pytest.raises(ValueError, match="same grid"):
        overlap_cc(mask, region[:-1])


def test_only_the_structure_type_makes_a_ptv():
    assert is_ptv("PTV") and is_ptv(" ptv ")
    for other in ("CTV", "GTV", "ORGAN", "", None):
        assert not is_ptv(other)


# ---- The worker ----------------------------------------------------------------


def test_the_three_columns_are_measured_against_the_combined_ptvs():
    row, basis = _measure(_worker())
    assert row["metrics"]["gt_ptv_overlap_cc"] == pytest.approx(GT_IN_PTV_CC)
    assert row["metrics"]["test_ptv_overlap_cc"] == pytest.approx(TEST_IN_PTV_CC)
    assert row["metrics"]["ptv_overlap_diff_cc"] == pytest.approx(TEST_IN_PTV_CC - GT_IN_PTV_CC)
    # Typed PTV, whatever the name; named PTV but typed otherwise, never.
    assert basis["structures"] == ["Boost", "PTV_56"]
    assert row["audit"]["ptv"] == {"status": PTV_STATUS_MEASURED, "structures": ["Boost", "PTV_56"]}


def test_no_structure_typed_ptv_leaves_the_columns_blank():
    """Named PTV_56 and PTV_decoy, but typed as organs: there is no PTV."""
    types = {1: "ORGAN", 2: "", 3: "ORGAN", 4: "ORGAN"}
    row, _ = _measure(_worker(types=types))
    assert row["metrics"] == {}  # blank, not zero
    assert row["audit"]["ptv"]["status"] == PTV_STATUS_NONE


def test_a_consensus_ground_truth_gets_no_ptv():
    row, _ = _measure(_worker(), group=_group(synthetic=True))
    assert row["metrics"] == {}
    assert row["audit"]["ptv"]["status"] == PTV_STATUS_CONSENSUS


def test_a_ptv_that_cannot_be_rasterised_blanks_the_columns():
    """A union missing one PTV would understate the overlap."""
    rois = {**GT_ROIS, 5: ("PTV_empty", {})}
    worker = _worker(types={**GT_TYPES, 5: "PTV"}, rois=rois)
    row, _ = _measure(worker, rois=rois)
    assert row["metrics"] == {}
    assert "PTV_empty" in row["audit"]["ptv"]["status"]
    assert "could not be rasterised" in row["audit"]["ptv"]["status"]


def test_the_union_is_built_once_per_structure_set():
    worker = _worker()
    _measure(worker)
    assert ("P1", "gt.1") in worker._ptv_cache
    worker._evict_patient_caches("P1")
    assert worker._ptv_cache == {}


def test_both_checks_are_recorded_whatever_is_ticked(qapp, monkeypatch):
    """Only Dice is asked for. The PTV overlap and Contour Discontinuity come
    anyway, through the worker's own pass over one organ."""
    from autoseg_evaluator.workers import metrics_worker as module

    worker = MetricsWorker(None, [], {"geometric": {"dice": True}})
    # The test reaches 4 mm into the boost and skips the slice at z = 2 mm.
    test_planes = {z: _box(-1.5, 8.5) for z in (-2.0, 0.0, 4.0)}
    datasets = {
        "gt.1": _structure_set(GT_ROIS),
        "test.1": _structure_set({1: ("Parotid_L", test_planes)}),
    }
    entries = {
        "gt.1": SimpleNamespace(
            organs=[
                OrganEntry(roi_number=n, roi_name=name, interpreted_type=GT_TYPES[n])
                for n, (name, _planes) in GT_ROIS.items()
            ],
            is_synthetic_consensus=False,
            linkage_id="",
        ),
        "test.1": SimpleNamespace(organs=[], is_synthetic_consensus=False, linkage_id=""),
    }
    monkeypatch.setattr(worker, "_find_rtstruct_entry", lambda _patient, sop: entries.get(sop))
    monkeypatch.setattr(worker, "_load_ct", lambda *_: _image())
    monkeypatch.setattr(worker, "_load_rtstruct", lambda _patient, sop: datasets[sop])
    monkeypatch.setattr(module, "planning_series_uid", lambda *_: None)
    group = {
        "organ_name": "Parotid_L",
        "truncate": False,
        "gt_comparison": True,
        "staple_consensus": False,
        "staple_include_gt": True,
        "patient_id": "P1",
        "gt_sop": "gt.1",
        "gt_filename": "",
        "gt_source": "Manual",
        "gt_roi_number": 1,
        "gt_roi_name": "Parotid_L",
        "tests": [
            {
                "rtstruct_sop_uid": "test.1",
                "source_label": "VendorA",
                "organ_name": "Parotid_L",
                "roi_number": 1,
                "similarity": 1.0,
            }
        ],
    }

    rows = worker._compute_group(
        group, {"patient": "", "drawer": "", "test": "", "metric": ""}, 0, 1
    )

    (row,) = [r for r in rows if r.get("test_source_label") == "VendorA"]
    assert row["error"] == ""
    assert "dice" in row["metrics"]
    assert row["metrics"]["gt_ptv_overlap_cc"] == pytest.approx(GT_IN_PTV_CC)
    # 60 + 100 voxels on the two slices the boost reaches; none at z = 4 mm.
    assert row["metrics"]["test_ptv_overlap_cc"] == pytest.approx(160 * VOXEL_CC)
    assert row["metrics"]["contour_discontinuity"] is True


# ---- Table, report and audit ----------------------------------------------------


def test_the_columns_have_their_names_and_sit_with_the_checks():
    from autoseg_evaluator.data.results import CANONICAL_METRIC_COLUMNS, metric_display_label
    from autoseg_evaluator.ui.tabs.results import _band_for_metric_key

    labels = {
        "gt_ptv_overlap_cc": "GT overlap with PTV (cc)",
        "test_ptv_overlap_cc": "Test overlap with PTV (cc)",
        "ptv_overlap_diff_cc": "PTV overlap difference (cc)",
    }
    for key, label in labels.items():
        assert key in CANONICAL_METRIC_COLUMNS
        assert metric_display_label(key) == label
        assert _band_for_metric_key(key) == "checks"


def test_the_report_compares_the_test_overlap_and_the_difference_only():
    """The ground truth's overlap is the same for every source."""
    for key in ("test_ptv_overlap_cc", "ptv_overlap_diff_cc"):
        assert metric_family(key) == FAMILY_PTV
        assert metric_direction(key) == 0  # the target is the GT's overlap
    rows = [_report_row("P01", 0.2, 0.0)]
    model = build_report_model(rows)
    assert "gt_ptv_overlap_cc" not in model.metrics()
    assert {"test_ptv_overlap_cc", "ptv_overlap_diff_cc"} <= set(model.metrics())


def _report_row(patient, test_cc, gt_cc, organ="Parotid (L)", source="VendorA"):
    metrics = {"dice": 0.8}
    if test_cc is not None:
        metrics.update(
            test_ptv_overlap_cc=test_cc,
            gt_ptv_overlap_cc=gt_cc,
            ptv_overlap_diff_cc=test_cc - gt_cc,
        )
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


#: Only P01 overlaps where the ground truth does not. P05 was not measured.
COUNTED_ROWS = [
    _report_row("P01", 0.2, 0.0),
    _report_row("P02", 0.2, 0.1),
    _report_row("P03", 0.0, 0.0),
    _report_row("P04", 0.0, 0.3),
    _report_row("P05", None, None),
]


def test_only_test_overlap_where_the_gt_has_none_is_counted():
    model = build_report_model(COUNTED_ROWS)
    assert model.measured_ptv_overlap()
    assert model.ptv_only_overlap("Parotid (L)", "VendorA") == (1, 4)
    assert model.ptv_only_overlap("Parotid (L)", "VendorB") is None
    assert not build_report_model([_report_row("P01", None, None)]).measured_ptv_overlap()


def _report_tab(rows):
    from autoseg_evaluator.data.results import ResultsManager
    from autoseg_evaluator.ui.tabs.report import ReportTab

    tab = ReportTab()
    manager = ResultsManager()
    manager.add_rows(rows)
    tab.set_results_manager(manager)
    tab.refresh()
    tab._metric_combo.setCurrentText("dice")
    return tab


def test_the_coverage_table_counts_test_only_overlaps(qapp, tmp_path):
    from autoseg_evaluator.ui.tabs.report import COVERAGE_PTV_COLUMN

    tab = _report_tab(COUNTED_ROWS + [_report_row(p, 0.0, 0.0, source="VendorB") for p in "AB"])
    table = tab._coverage_table
    assert not table.isColumnHidden(COVERAGE_PTV_COLUMN)
    said = {table.item(r, 1).text(): table.item(r, COVERAGE_PTV_COLUMN).text() for r in range(2)}
    assert said == {"VendorA": "1 of 4", "VendorB": "0 of 2"}

    html = tab._pdf_html(Path(tmp_path))
    assert ">Test-only PTV overlap</th>" in html
    assert ">1 of 4</td>" in html
    tab.deleteLater()


def test_the_count_is_hidden_where_no_overlap_was_measured(qapp, tmp_path):
    from autoseg_evaluator.ui.tabs.report import COVERAGE_PTV_COLUMN

    tab = _report_tab([_report_row(f"P0{p}", None, None) for p in range(3)])
    assert tab._coverage_table.isColumnHidden(COVERAGE_PTV_COLUMN)
    assert "Test-only PTV overlap" not in tab._pdf_html(Path(tmp_path))
    tab.deleteLater()


def test_the_audit_sidecar_carries_the_ptv_record():
    from autoseg_evaluator.data import sidecar

    row = {
        "patient_id": "P01",
        "test_source_label": "VendorA",
        "metrics": {},
        "audit": {"ptv": {"status": PTV_STATUS_NONE, "structures": []}},
    }
    document = sidecar.build([row])
    assert document["records"][0]["ptv"]["status"] == PTV_STATUS_NONE
    assert "ptv" in document["notes"]


def test_the_compute_tab_has_no_switch_for_it_and_says_so(qapp):
    """A check on every run, not a metric to choose."""
    from PySide6.QtWidgets import QLabel

    from autoseg_evaluator.ui.tabs.compute import ComputeTab

    tab = ComputeTab()
    assert "ptv_overlap" not in tab._geom_checks
    assert "ptv_overlap" not in tab.config()["geometric"]
    notes = " ".join(label.text() for label in tab.findChildren(QLabel))
    assert "Recorded on every run" in notes and "PTV" in notes
    tab.deleteLater()
