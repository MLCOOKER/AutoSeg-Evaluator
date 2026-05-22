"""Tests for the surface-distance primitives.

These exercise the public functions on synthetic 3D arrays with known
geometric ground truth so we catch any regression in the port from the
Google DeepMind / AutoSeg Evaluator v1 implementation.
"""

from __future__ import annotations

import math

import numpy as np

from autoseg_evaluator.core.surface_distance import (
    compute_average_surface_distance,
    compute_dice_coefficient,
    compute_robust_hausdorff,
    compute_surface_dice_at_tolerance,
    compute_surface_distances,
    create_table_neighbour_code_to_contour_length,
    create_table_neighbour_code_to_surface_area,
)


SPACING = (1.0, 1.0, 1.0)


def _cube_mask(shape, x0, x1, y0, y1, z0, z1):
    m = np.zeros(shape, dtype=np.uint8)
    m[z0:z1, y0:y1, x0:x1] = 1
    return m


# ---- Lookup tables -------------------------------------------------------


def test_surface_area_table_has_256_entries():
    arr = create_table_neighbour_code_to_surface_area((1.0, 1.0, 1.0))
    assert arr.shape == (256,)
    # First and last entries (all-empty / all-full neighbourhoods) have zero area
    assert arr[0] == 0.0
    assert arr[255] == 0.0


def test_contour_length_table_has_16_entries():
    arr = create_table_neighbour_code_to_contour_length((1.0, 1.0))
    assert arr.shape == (16,)
    # Code 0 (no edges) and code 15 (all four corners) have zero contour length
    assert arr[0] == 0.0
    assert arr[15] == 0.0
    # Horizontal edge (code 0011) length equals spacing[1]
    assert arr[int("0011", 2)] == 1.0


# ---- Dice ----------------------------------------------------------------


def test_dice_identical_cubes_is_one():
    m = _cube_mask((20, 20, 20), 5, 15, 5, 15, 5, 15)
    assert compute_dice_coefficient(m, m) == 1.0


def test_dice_disjoint_cubes_is_zero():
    a = _cube_mask((20, 20, 20), 0, 5, 0, 5, 0, 5)
    b = _cube_mask((20, 20, 20), 10, 15, 10, 15, 10, 15)
    assert compute_dice_coefficient(a, b) == 0.0


def test_dice_both_empty_is_nan():
    z = np.zeros((10, 10, 10), dtype=np.uint8)
    assert math.isnan(compute_dice_coefficient(z, z))


def test_dice_partial_overlap_known_value():
    a = _cube_mask((20, 20, 20), 0, 10, 0, 10, 0, 10)
    b = _cube_mask((20, 20, 20), 5, 15, 0, 10, 0, 10)
    # intersection = 5*10*10 = 500; sums = 1000 + 1000 → Dice = 2*500/2000 = 0.5
    assert compute_dice_coefficient(a, b) == 0.5


# ---- Surface distances ---------------------------------------------------


def test_surface_distance_identical_masks_is_zero():
    m = _cube_mask((20, 20, 20), 5, 15, 5, 15, 5, 15)
    sd = compute_surface_distances(m, m, SPACING)
    # Symmetric Hausdorff at 100% over identical masks is 0
    hd = compute_robust_hausdorff(sd, 100)
    assert hd == 0.0


def test_surface_distance_one_voxel_shift_has_distance_one():
    """Shifting the predicted mask by 1 voxel gives Hausdorff = 1 mm at unit spacing."""
    gt = _cube_mask((20, 20, 20), 5, 15, 5, 15, 5, 15)
    pred = _cube_mask((20, 20, 20), 6, 16, 5, 15, 5, 15)
    sd = compute_surface_distances(gt, pred, SPACING)
    hd100 = compute_robust_hausdorff(sd, 100)
    assert hd100 == 1.0
    # Mean surface distance must be in (0, 1]
    avg_a, avg_b = compute_average_surface_distance(sd)
    assert 0 < avg_a <= 1.0
    assert 0 < avg_b <= 1.0


def test_surface_dice_at_tolerance_identical_is_one():
    m = _cube_mask((20, 20, 20), 5, 15, 5, 15, 5, 15)
    sd = compute_surface_distances(m, m, SPACING)
    assert compute_surface_dice_at_tolerance(sd, tolerance_mm=0.0) == 1.0


def test_surface_dice_with_tolerance_recovers_high_score_on_shifted_mask():
    """At tolerance >= shift distance, surface dice should be 1.0."""
    gt = _cube_mask((20, 20, 20), 5, 15, 5, 15, 5, 15)
    pred = _cube_mask((20, 20, 20), 6, 16, 5, 15, 5, 15)
    sd = compute_surface_distances(gt, pred, SPACING)
    # With 1mm tolerance and a 1-voxel shift, almost the entire surface qualifies
    score = compute_surface_dice_at_tolerance(sd, tolerance_mm=1.0)
    assert 0.9 <= score <= 1.0


def test_surface_distance_disjoint_masks_returns_finite_hd():
    a = _cube_mask((20, 20, 20), 0, 5, 0, 5, 0, 5)
    b = _cube_mask((20, 20, 20), 10, 15, 10, 15, 10, 15)
    sd = compute_surface_distances(a, b, SPACING)
    hd = compute_robust_hausdorff(sd, 100)
    assert hd > 0
    assert math.isfinite(hd)


def test_surface_distance_empty_masks_returns_empty_arrays():
    z = np.zeros((10, 10, 10), dtype=np.uint8)
    sd = compute_surface_distances(z, z, SPACING)
    assert len(sd["distances_gt_to_pred"]) == 0
    assert len(sd["distances_pred_to_gt"]) == 0


def test_compute_surface_distances_rejects_dimension_mismatch():
    a = np.zeros((10, 10, 10), dtype=np.uint8)
    b = np.zeros((10, 10), dtype=np.uint8)
    import pytest

    with pytest.raises(ValueError, match="dimensions"):
        compute_surface_distances(a, b, SPACING)


def test_hausdorff_95_smaller_than_hausdorff_100():
    """The 95th-percentile HD should be ≤ the 100th-percentile (max) HD."""
    gt = _cube_mask((20, 20, 20), 5, 15, 5, 15, 5, 15)
    # Add a small outlier voxel that's distant — drives the 100% HD up
    pred = gt.copy()
    pred[5, 0, 0] = 1
    sd = compute_surface_distances(gt, pred, SPACING)
    hd100 = compute_robust_hausdorff(sd, 100)
    hd95 = compute_robust_hausdorff(sd, 95)
    assert hd95 <= hd100
