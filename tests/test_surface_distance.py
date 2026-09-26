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


# ---- Speed changes that must not move a number ---------------------------


def _tuple_sort(distances, areas):
    """The sort the DeepMind port shipped with, kept here as the oracle."""
    ordered = np.array(sorted(zip(distances, areas)))
    return ordered[:, 0], ordered[:, 1]


def test_surfel_sort_gives_the_tuple_sort_order_exactly():
    """Distance first, then area among tied distances — ties are the norm.

    Distances on a voxel lattice take few distinct values, so most surfels tie
    on distance, and the percentile Hausdorff distances read a position in this
    order. Infinite distances occur when one surface is empty.
    """
    from autoseg_evaluator.core.surface_distance import _sort_distances_surfels

    rng = np.random.default_rng(7)
    for trial in range(200):
        n = int(rng.integers(1, 4000))
        distances = rng.choice(np.round(rng.random(40) * 10, 3), n)
        if trial % 5 == 0:
            distances[rng.random(n) < 0.2] = np.inf
        areas = rng.choice(rng.random(6), n)
        got_d, got_a = _sort_distances_surfels(distances, areas)
        want_d, want_a = _tuple_sort(distances, areas)
        assert np.array_equal(got_d, want_d)
        assert np.array_equal(got_a, want_a)


def test_surface_metrics_unchanged_by_the_sort_on_real_shapes():
    """End to end on shapes with many ties, against the tuple-sorted distances."""
    import autoseg_evaluator.core.surface_distance as sd_module

    gt = np.zeros((24, 40, 40), dtype=np.uint8)
    pred = np.zeros_like(gt)
    zz, yy, xx = np.mgrid[:24, :40, :40]
    gt[((xx - 20) / 12.0) ** 2 + ((yy - 20) / 9.0) ** 2 + ((zz - 12) / 8.0) ** 2 <= 1] = 1
    pred[((xx - 22) / 11.0) ** 2 + ((yy - 19) / 10.0) ** 2 + ((zz - 11) / 7.0) ** 2 <= 1] = 1
    spacing = (3.0, 0.98, 0.98)

    new = compute_surface_distances(gt, pred, spacing)
    original = sd_module._sort_distances_surfels
    try:
        sd_module._sort_distances_surfels = _tuple_sort
        old = compute_surface_distances(gt, pred, spacing)
    finally:
        sd_module._sort_distances_surfels = original
    for key in old:
        assert np.array_equal(new[key], old[key]), key
    for percent in (95, 100):
        assert compute_robust_hausdorff(new, percent) == compute_robust_hausdorff(old, percent)


def test_the_surface_area_table_is_built_once_per_spacing_and_unchanged():
    from autoseg_evaluator.core.surface_distance import _surface_area_table

    spacing = (3.0, 0.9765625, 0.9765625)
    cached = _surface_area_table(spacing)
    assert np.array_equal(cached, create_table_neighbour_code_to_surface_area(spacing))
    # NumPy floats and Python floats name the same spacing, and share a table.
    assert _surface_area_table(tuple(float(v) for v in np.array(spacing))) is cached
    # Shared across calls, so nothing may write into it.
    assert not cached.flags.writeable
