"""Unit tests for the metrics worker's STAPLE row helpers.

The full worker requires CT + dose + RTSS DICOM with real geometry, so these
cover the pure, cheaply-testable pieces: the STAPLE-Details metric extractor
and the mode-label constants.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from autoseg_evaluator.workers.metrics_worker import (  # noqa: E402
    _MODE_GENERIC_STAPLE_GT,
    _MODE_GENERIC_STAPLE_NO_GT,
    _MODE_MULTI_OBSERVER,
    _STAPLE_DETAILS_MODE,
    MetricsWorker,
)


def _fake_result():
    return SimpleNamespace(
        consensus_volume_cc=12.5,
        uncertain_band_cc=1.2,
        mean_entropy=0.34,
        rater_disagreement_cc=2.1,
        rater_volume_range_cc=3.0,
        n_raters=4,
        elapsed_iterations=7,
        converged=True,
        bbox_padding_used=5,
        bbox_fg_ratio=0.22,
    )


def test_staple_summary_metrics_extracts_scalars():
    m = MetricsWorker._staple_summary_metrics(_fake_result())
    assert m["consensus_volume_cc"] == 12.5
    assert m["mean_entropy"] == 0.34
    assert m["n_raters"] == 4
    assert m["staple_iterations"] == 7
    assert m["staple_converged"] is True
    assert m["staple_bbox_padding"] == 5
    assert m["staple_bbox_fg_ratio"] == 0.22
    # No dose keys and no per-rater sensitivity/specificity on the details row.
    assert not any(k.endswith("_gy") for k in m)
    assert "staple_sensitivity" not in m
    assert "dmean_gy" not in m


def test_staple_summary_metrics_none_is_empty():
    assert MetricsWorker._staple_summary_metrics(None) == {}


def test_mode_labels_are_distinct():
    modes = {
        _STAPLE_DETAILS_MODE,
        _MODE_MULTI_OBSERVER,
        _MODE_GENERIC_STAPLE_GT,
        _MODE_GENERIC_STAPLE_NO_GT,
    }
    assert len(modes) == 4
    assert _STAPLE_DETAILS_MODE == "STAPLE Details"
    assert _MODE_MULTI_OBSERVER == "Multi-observer STAPLE"


def test_group_weight_tracks_test_rater_count():
    """Progress weight = rater count (floored at 1) so the bar is proportional
    to real work and a row-count estimate can't drift from it."""
    w = MetricsWorker(library=None, drawers_state=[], config={})
    assert w._group_weight({"tests": []}) == 1  # floored at 1
    assert w._group_weight({"tests": [{}, {}, {}]}) == 3
    # The bar total is the sum of weights — exact and known up front.
    groups = [{"tests": [{}, {}]}, {"tests": []}, {"tests": [{}, {}, {}, {}]}]
    assert sum(w._group_weight(g) for g in groups) == 2 + 1 + 4


def test_dvh_diff_metrics_test_minus_gt():
    """Each DVH metric gets a {key}_diff = test - GT, skipping missing pairs."""
    keys = ["dmean_gy", "dmax_gy", "d2cc_gy", "v20gy_cc"]
    test_metrics = {"dmean_gy": 30.0, "dmax_gy": 41.0, "d2cc_gy": 39.5}  # no v20gy_cc
    gt_dvh = {"dmean_gy": 28.0, "dmax_gy": 40.0, "d2cc_gy": 38.0, "v20gy_cc": 12.0}
    out = MetricsWorker._dvh_diff_metrics(test_metrics, gt_dvh, keys)
    assert out["dmean_gy_diff"] == 2.0
    assert out["dmax_gy_diff"] == 1.0
    assert round(out["d2cc_gy_diff"], 6) == 1.5
    # v20gy_cc absent from the test row → no diff emitted (not zero).
    assert "v20gy_cc_diff" not in out


def test_sens_spec_vs_reference_perfect_and_partial():
    """sensitivity_specificity_vs_reference: identity → (1, 1); a test missing
    half the reference → sensitivity 0.5; specificity stays high."""
    import numpy as np
    import SimpleITK as sitk

    from autoseg_evaluator.core.staple import sensitivity_specificity_vs_reference

    ref_arr = np.zeros((10, 20, 20), dtype=np.uint8)
    ref_arr[3:7, 5:15, 5:15] = 1  # a solid block
    ref = sitk.GetImageFromArray(ref_arr)

    # Identical test → sensitivity 1, specificity 1.
    s, sp = sensitivity_specificity_vs_reference(ref, ref)
    assert s == 1.0
    assert sp == 1.0

    # Test covering only the top half of the reference slices → sensitivity 0.5.
    test_arr = np.zeros_like(ref_arr)
    test_arr[3:5, 5:15, 5:15] = 1  # half the z-extent
    test = sitk.GetImageFromArray(test_arr)
    s2, sp2 = sensitivity_specificity_vs_reference(ref, test)
    assert abs(s2 - 0.5) < 1e-9  # recovered half the reference voxels
    assert sp2 == 1.0  # no false positives outside the reference


# ---- Treatment context stamped on every row -------------------------------


def _worker_with(entries, series_for=None):
    """A worker whose library resolves these RTSS entries for patient P1.

    ``series_for`` maps a structure set UID to the planning series it resolves
    to, standing in for the linkage layer.
    """
    worker = MetricsWorker.__new__(MetricsWorker)
    worker._library = object()
    worker._find_rtstruct_entry = lambda patient_id, sop: next(  # type: ignore[method-assign]
        (e for e in entries if patient_id == "P1" and e.sop_instance_uid == sop), None
    )
    lookup = series_for or {}

    def _resolve(_library, _patient_id, sop):
        uid = lookup.get(sop)
        return SimpleNamespace(
            is_resolved=bool(uid), target=SimpleNamespace(series_instance_uid=uid or "")
        )

    import autoseg_evaluator.workers.metrics_worker as module

    module.resolve_image_series = _resolve
    return worker


def test_a_row_carries_the_linkage_of_its_ground_truth():
    """The GT structure set fixes the context every source is compared against."""
    entries = [
        SimpleNamespace(sop_instance_uid="gt-1", linkage_id="series:course-1"),
        SimpleNamespace(sop_instance_uid="test-1", linkage_id="series:course-1"),
    ]
    worker = _worker_with(entries, {"gt-1": "ct-1", "test-1": "ct-1"})
    group = {"patient_id": "P1", "gt_sop": "gt-1"}
    assert worker._linkage_id(group, "test-1") == "series:ct-1"


def test_a_staple_consensus_borrows_the_raters_linkage():
    """The consensus has no structure set of its own, but was built from theirs."""
    entries = [SimpleNamespace(sop_instance_uid="test-1", linkage_id="link:x")]
    worker = _worker_with(entries, {"test-1": "ct-2"})
    group = {"patient_id": "P1", "gt_sop": "", "_gt_synthetic": True}
    assert worker._linkage_id(group, "test-1") == "series:ct-2"


def test_an_unstamped_library_yields_an_empty_linkage():
    """Which makes the report fall back to one case per patient, as before."""
    entries = [SimpleNamespace(sop_instance_uid="gt-1")]  # no linkage_id at all
    worker = _worker_with(entries)
    assert worker._linkage_id({"patient_id": "P1", "gt_sop": "gt-1"}, "test-1") == ""
    # Unknown structure set, and missing identifiers, are equally non-fatal.
    assert worker._linkage_id({"patient_id": "P1", "gt_sop": "absent"}, "") == ""
    assert worker._linkage_id({"patient_id": "", "gt_sop": ""}, "") == ""


def test_two_courses_of_one_patient_get_different_linkages():
    """The whole point: same PatientID, different treatment context."""
    entries = [
        SimpleNamespace(sop_instance_uid="gt-1", linkage_id="link:a"),
        SimpleNamespace(sop_instance_uid="gt-2", linkage_id="link:b"),
    ]
    worker = _worker_with(entries, {"gt-1": "ct-1", "gt-2": "ct-2"})
    first = worker._linkage_id({"patient_id": "P1", "gt_sop": "gt-1"}, "t")
    second = worker._linkage_id({"patient_id": "P1", "gt_sop": "gt-2"}, "t")
    assert first != second


def test_a_vendors_wrong_frame_of_reference_does_not_split_the_case():
    """Found on this project's own cohort, in Pelvis Male / Prostate4.

    One vendor exported its structure set under a different
    FrameOfReferenceUID for the same CT, so the ingest-time linkage put it in
    its own component. Keyed on that stamp, the patient would have been split
    into two cases and then withheld from every comparison involving that
    vendor. Keyed on the planning image they resolve to, they are one case.
    """
    entries = [
        SimpleNamespace(sop_instance_uid="gt-1", linkage_id="link:cdd22893"),
        SimpleNamespace(sop_instance_uid="odd-vendor", linkage_id="for:1.2.246.352.221.5258"),
    ]
    worker = _worker_with(entries, {"gt-1": "ct-1", "odd-vendor": "ct-1"})
    assert worker._linkage_id({"patient_id": "P1", "gt_sop": "gt-1"}, "t") == "series:ct-1"
    assert worker._linkage_id({"patient_id": "P1", "gt_sop": "odd-vendor"}, "t") == "series:ct-1"
