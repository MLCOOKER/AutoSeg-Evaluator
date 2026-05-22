"""Tests for the truncate-to-GT-Z-extent helper used when comparing structures
with high cranio-caudal variability (spinal cord, rectum, etc.).
"""

from __future__ import annotations

import numpy as np
import SimpleITK as sitk

from autoseg_evaluator.core.masks import truncate_to_gt_z_extent
from autoseg_evaluator.core.metrics import compute_geometric_metrics


def _sitk_from_arr(arr: np.ndarray, spacing=(1.0, 1.0, 1.0)) -> sitk.Image:
    img = sitk.GetImageFromArray(arr.astype(np.uint8))
    img.SetSpacing(spacing)
    return img


def test_truncate_zeros_slices_outside_gt():
    # GT occupies slices 4..10 (inclusive). Test extends slices 0..14.
    gt_arr = np.zeros((20, 8, 8), dtype=np.uint8)
    gt_arr[4:11, 2:6, 2:6] = 1
    test_arr = np.zeros((20, 8, 8), dtype=np.uint8)
    test_arr[0:15, 2:6, 2:6] = 1

    gt = _sitk_from_arr(gt_arr)
    test = _sitk_from_arr(test_arr)

    truncated, extent = truncate_to_gt_z_extent(test, gt)
    out = sitk.GetArrayFromImage(truncated)

    # Outside the GT extent should be all-zero
    assert out[:4].sum() == 0
    assert out[11:].sum() == 0
    # Inside the GT extent should equal the original test mask
    assert np.array_equal(out[4:11], test_arr[4:11])
    # Test occupied slices 0..14, GT extent 4..10 — so 4 (below) + 4 (11..14) = 8 trimmed
    assert extent["slices_removed"] == 8
    assert extent["extent_removed_mm"] == 8.0


def test_truncate_with_no_gt_voxels_returns_unchanged():
    gt = _sitk_from_arr(np.zeros((10, 8, 8), dtype=np.uint8))
    test = _sitk_from_arr(np.ones((10, 8, 8), dtype=np.uint8))
    truncated, extent = truncate_to_gt_z_extent(test, gt)
    # The function returns the original mask object when GT is empty
    assert sitk.GetArrayFromImage(truncated).sum() == sitk.GetArrayFromImage(test).sum()
    assert extent["slices_removed"] == 0
    assert extent["extent_removed_mm"] == 0.0


def test_truncate_preserves_spacing_and_geometry():
    gt_arr = np.zeros((10, 6, 6), dtype=np.uint8)
    gt_arr[2:5, 1:5, 1:5] = 1
    test_arr = np.zeros((10, 6, 6), dtype=np.uint8)
    test_arr[0:8, 1:5, 1:5] = 1
    gt = _sitk_from_arr(gt_arr, spacing=(0.7, 0.7, 2.5))
    test = _sitk_from_arr(test_arr, spacing=(0.7, 0.7, 2.5))
    truncated, extent = truncate_to_gt_z_extent(test, gt)
    assert truncated.GetSpacing() == test.GetSpacing()
    assert truncated.GetSize() == test.GetSize()
    # Test occupies z=0..7, GT extent z=2..4 → 2 (below) + 3 (5,6,7 above) = 5 slices trimmed,
    # 5 × 2.5 mm spacing = 12.5 mm
    assert extent["slices_removed"] == 5
    assert extent["extent_removed_mm"] == 12.5


def test_truncation_changes_geometric_metrics():
    """A test mask that extends beyond GT should score better after truncation."""
    # GT slices 5..10, test slices 0..15 (overshooting symmetrically)
    gt_arr = np.zeros((20, 20, 20), dtype=np.uint8)
    gt_arr[5:11, 5:15, 5:15] = 1
    test_arr = np.zeros((20, 20, 20), dtype=np.uint8)
    test_arr[0:16, 5:15, 5:15] = 1

    gt = _sitk_from_arr(gt_arr)
    test = _sitk_from_arr(test_arr)
    truncated, _ = truncate_to_gt_z_extent(test, gt)

    config = {
        "geometric": {
            "dice": True,
            "hausdorff100": True,
            "hausdorff95": False,
            "mean_surface_distance": True,
            "surface_dice": True,
            "apl_mean": False,
            "apl_total": False,
        },
        "tolerances": {"surface_dice_tau_mm": 1.0, "apl_tolerance_mm": 3.0},
    }
    raw = compute_geometric_metrics(gt, test, config)
    trunc = compute_geometric_metrics(gt, truncated, config)

    # Truncation should restore a near-perfect Dice (test == GT inside the extent)
    assert raw["dice"] < trunc["dice"]
    assert trunc["dice"] == 1.0  # exact overlap after truncation
    # And dramatically reduce the Hausdorff distance (was ~5 voxels of overshoot each end)
    assert trunc["hausdorff100"] < raw["hausdorff100"]
