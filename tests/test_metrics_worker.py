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
