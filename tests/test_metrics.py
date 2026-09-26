"""Tests for the high-level metrics module + DVH config."""

from __future__ import annotations

import math

import numpy as np
import pytest
import SimpleITK as sitk

from autoseg_evaluator.core.dvh import DVHConfig
from autoseg_evaluator.core.metrics import (
    centroid_physical,
    compute_geometric_metrics,
    dice,
    hausdorff,
    mask_audit_detail,
    mean_surface_distance,
    precision_recall,
    surface_dice,
    volume_and_com_metrics,
    volume_cc,
)


def _cube_sitk(shape_xyz, x0, x1, y0, y1, z0, z1, spacing=(1.0, 1.0, 1.0)) -> sitk.Image:
    """Build a SimpleITK uint8 mask with a filled cube at the given index range."""
    sx, sy, sz = shape_xyz
    arr = np.zeros((sz, sy, sx), dtype=np.uint8)
    arr[z0:z1, y0:y1, x0:x1] = 1
    img = sitk.GetImageFromArray(arr)
    img.SetSpacing(spacing)
    return img


# ---- Individual metric wrappers ------------------------------------------


def test_dice_identical():
    m = _cube_sitk((20, 20, 20), 5, 15, 5, 15, 5, 15)
    arr = sitk.GetArrayFromImage(m)
    assert dice(arr, arr) == 1.0


def test_hausdorff_shifted_by_one_voxel_is_one_mm():
    a = _cube_sitk((20, 20, 20), 5, 15, 5, 15, 5, 15)
    b = _cube_sitk((20, 20, 20), 6, 16, 5, 15, 5, 15)
    aa = sitk.GetArrayFromImage(a)
    bb = sitk.GetArrayFromImage(b)
    assert hausdorff(aa, bb, (1.0, 1.0, 1.0), 100) == 1.0


def test_mean_surface_distance_is_finite():
    a = _cube_sitk((20, 20, 20), 5, 15, 5, 15, 5, 15)
    b = _cube_sitk((20, 20, 20), 6, 16, 5, 15, 5, 15)
    aa = sitk.GetArrayFromImage(a)
    bb = sitk.GetArrayFromImage(b)
    msd = mean_surface_distance(aa, bb, (1.0, 1.0, 1.0))
    assert math.isfinite(msd)
    assert 0 < msd <= 1.0


def test_surface_dice_identical_is_one():
    m = _cube_sitk((20, 20, 20), 5, 15, 5, 15, 5, 15)
    arr = sitk.GetArrayFromImage(m)
    assert surface_dice(arr, arr, (1.0, 1.0, 1.0), tolerance_mm=0.0) == 1.0


# ---- Precision and recall ------------------------------------------------


def _arr(*box):
    return sitk.GetArrayFromImage(_cube_sitk((30, 30, 30), *box))


def test_over_segmentation_costs_precision_and_not_recall():
    """A test that contains the ground truth and 20 % more besides."""
    gt = _arr(5, 15, 5, 15, 5, 15)  # 1000 voxels
    test = _arr(5, 17, 5, 15, 5, 15)  # 1200 voxels, all of gt inside
    precision, recall = precision_recall(gt, test)
    assert precision == pytest.approx(1000 / 1200)
    assert recall == 1.0


def test_under_segmentation_costs_recall_and_not_precision():
    gt = _arr(5, 15, 5, 15, 5, 15)  # 1000 voxels
    test = _arr(5, 13, 5, 15, 5, 15)  # 800 voxels, all inside gt
    precision, recall = precision_recall(gt, test)
    assert precision == 1.0
    assert recall == pytest.approx(0.8)


def test_dice_is_their_harmonic_mean():
    """Why F1 is not reported: on binary masks it is Dice."""
    gt = _arr(5, 15, 5, 15, 5, 15)
    test = _arr(7, 19, 4, 13, 5, 16)
    precision, recall = precision_recall(gt, test)
    f1 = 2 * precision * recall / (precision + recall)
    assert f1 == pytest.approx(dice(gt, test))


def test_a_share_of_nothing_is_not_a_number():
    """An empty test has no precision and an empty ground truth no recall.

    Zero would read as a complete failure; it is an undefined share instead.
    """
    gt = _arr(5, 15, 5, 15, 5, 15)
    empty = np.zeros_like(gt)
    precision, recall = precision_recall(gt, empty)
    assert math.isnan(precision)
    assert recall == 0.0
    precision, recall = precision_recall(empty, gt)
    assert precision == 0.0
    assert math.isnan(recall)


def test_the_audit_record_can_recompute_the_overlap_metrics():
    gt = _cube_sitk((30, 30, 30), 5, 15, 5, 15, 5, 15)
    test = _cube_sitk((30, 30, 30), 7, 17, 5, 15, 5, 15)
    detail = mask_audit_detail(gt, test)
    assert detail["overlap_voxels"] == 800
    assert detail["overlap_voxels"] / detail["test_voxels"] == pytest.approx(0.8)


# ---- Volume + centre-of-mass ---------------------------------------------


def test_volume_cc_matches_voxel_count():
    # 10×10×10 cube = 1000 mm³ = 1.0 cc at 1 mm isotropic
    m = _cube_sitk((20, 20, 20), 5, 15, 5, 15, 5, 15)
    assert math.isclose(volume_cc(m), 1.0, rel_tol=1e-9)


def test_volume_cc_scales_with_anisotropic_spacing():
    # 10×10×10 voxels at 1×1×3 mm = 3000 mm³ = 3.0 cc
    m = _cube_sitk((20, 20, 20), 5, 15, 5, 15, 5, 15, spacing=(1.0, 1.0, 3.0))
    assert math.isclose(volume_cc(m), 3.0, rel_tol=1e-9)


def test_centroid_physical_of_centred_cube():
    # Cube centred on (10, 10, 10) in index space → physical (10, 10, 10) at unit spacing
    m = _cube_sitk((20, 20, 20), 5, 15, 5, 15, 5, 15)
    c = centroid_physical(m)
    assert c is not None
    assert all(math.isclose(c[i], 9.5, rel_tol=0, abs_tol=1e-9) for i in range(3))


def test_centroid_returns_none_for_empty_mask():
    m = _cube_sitk((10, 10, 10), 0, 0, 0, 0, 0, 0)  # empty
    assert centroid_physical(m) is None


def test_volume_and_com_metrics_identical_masks():
    m = _cube_sitk((20, 20, 20), 5, 15, 5, 15, 5, 15)
    out = volume_and_com_metrics(m, m)
    assert math.isclose(out["volume_gt_cc"], out["volume_test_cc"])
    assert out["volume_diff_cc"] == 0.0
    assert out["volume_ratio"] == 1.0
    assert out["com_offset_mm"] == 0.0


def test_volume_and_com_metrics_shifted_cube():
    a = _cube_sitk((30, 30, 30), 5, 15, 5, 15, 5, 15)
    b = _cube_sitk((30, 30, 30), 8, 18, 5, 15, 5, 15)  # shifted by 3 voxels in x
    out = volume_and_com_metrics(a, b)
    # Same shape → equal volume, zero diff
    assert math.isclose(out["volume_diff_cc"], 0.0)
    assert math.isclose(out["volume_ratio"], 1.0)
    # Centroid offset purely in x
    assert math.isclose(out["com_offset_mm"], 3.0, abs_tol=1e-9)
    assert math.isclose(out["com_dx_mm"], 3.0, abs_tol=1e-9)
    assert math.isclose(out["com_dy_mm"], 0.0, abs_tol=1e-9)
    assert math.isclose(out["com_dz_mm"], 0.0, abs_tol=1e-9)


def test_volume_ratio_is_nan_when_gt_empty():
    empty = _cube_sitk((10, 10, 10), 0, 0, 0, 0, 0, 0)
    test = _cube_sitk((10, 10, 10), 0, 5, 0, 5, 0, 5)
    out = volume_and_com_metrics(empty, test)
    assert math.isnan(out["volume_ratio"])
    assert math.isnan(out["com_offset_mm"])


# ---- compute_geometric_metrics aggregator --------------------------------


def test_compute_geometric_metrics_respects_config_flags():
    m = _cube_sitk((20, 20, 20), 5, 15, 5, 15, 5, 15)
    config = {
        "geometric": {
            "dice": True,
            "hausdorff100": False,
            "hausdorff95": False,
            "mean_surface_distance": False,
            "surface_dice": True,
        },
        "tolerances": {"surface_dice_tau_mm": 3.0},
    }
    out = compute_geometric_metrics(m, m, config)
    assert "dice" in out
    # Keyed by the tolerance it was computed at.
    assert "surface_dice@3mm" in out
    assert "hausdorff100" not in out


def test_a_configuration_still_asking_for_mask_apl_gets_none():
    """Mask APL was removed in v3; an old configuration must not break a run.

    Added path length now comes only from the 2D stream. A settings file or a
    caller from before the removal may still switch the mask version on; it is
    ignored rather than raising, and nothing named like it reaches a row.
    """
    m = _cube_sitk((20, 20, 20), 5, 15, 5, 15, 5, 15)
    config = {
        "geometric": {"dice": True, "apl_mean": True, "apl_total": True},
        "tolerances": {"surface_dice_tau_mm": 3.0, "apl_tolerance_mm": 3.0},
    }
    out = compute_geometric_metrics(m, m, config)
    assert out == {"dice": 1.0}


def test_compute_geometric_metrics_identical_masks_score_perfect():
    m = _cube_sitk((20, 20, 20), 5, 15, 5, 15, 5, 15)
    config = {
        "geometric": {
            "dice": True,
            "precision_recall": True,
            "hausdorff100": True,
            "hausdorff95": True,
            "mean_surface_distance": True,
            "surface_dice": True,
        },
        "tolerances": {"surface_dice_tau_mm": 0.0},
    }
    out = compute_geometric_metrics(m, m, config)
    assert out["dice"] == 1.0
    assert out["precision"] == 1.0
    assert out["recall"] == 1.0
    assert out["hausdorff100"] == 0.0
    assert out["hausdorff95"] == 0.0
    assert out["mean_surface_distance"] == 0.0
    assert out["surface_dice@0mm"] == 1.0


def test_surface_dice_at_several_tolerances_is_one_column_each():
    """A list of tolerances gives one keyed value per tolerance, from one pass."""
    gt = _cube_sitk((30, 30, 30), 5, 15, 5, 15, 5, 15)
    test = _cube_sitk((30, 30, 30), 7, 17, 5, 15, 5, 15)
    config = {
        "geometric": {"surface_dice": True},
        "tolerances": {"surface_dice_tau_mm": [3.0, 0.5, 1.0]},
    }
    out = compute_geometric_metrics(gt, test, config)
    assert set(out) == {"surface_dice@0.5mm", "surface_dice@1mm", "surface_dice@3mm"}
    for tau in (0.5, 1.0, 3.0):
        alone = compute_geometric_metrics(
            gt,
            test,
            {"geometric": {"surface_dice": True}, "tolerances": {"surface_dice_tau_mm": tau}},
        )
        assert out[f"surface_dice@{tau:g}mm"] == alone[f"surface_dice@{tau:g}mm"]
    # A larger tolerance forgives more of the 2-voxel shift.
    assert out["surface_dice@0.5mm"] < out["surface_dice@3mm"]


def test_precision_and_recall_come_as_a_pair_behind_one_switch():
    """One alone misleads: recall rewards over-drawing, precision under-drawing."""
    m = _cube_sitk((20, 20, 20), 5, 15, 5, 15, 5, 15)
    off = compute_geometric_metrics(m, m, {"geometric": {"dice": True}})
    on = compute_geometric_metrics(m, m, {"geometric": {"precision_recall": True}})
    assert "precision" not in off and "recall" not in off
    assert set(on) == {"precision", "recall"}


# ---- DVH config ----------------------------------------------------------


def test_dvh_config_from_dict_round_trip():
    cfg = DVHConfig.from_dict(
        {
            "include_dmean": True,
            "include_dmax": False,
            "include_dmin": True,
            "d_at_volumes_pct": [95, 50, "5"],
            "d_at_volumes_cc": [2, "0.1"],
            "v_at_doses_gy": [20.0, 30],
        }
    )
    assert cfg.include_dmean is True
    assert cfg.include_dmax is False
    assert cfg.include_dmin is True
    assert cfg.d_at_volumes_pct == [95.0, 50.0, 5.0]
    assert cfg.d_at_volumes_cc == [2.0, 0.1]
    assert cfg.v_at_doses_gy == [20.0, 30.0]
    assert cfg.any_enabled() is True


def test_dvh_config_from_dict_backwards_compatible_no_cc():
    """A pre-2.2 session JSON without ``d_at_volumes_cc`` must still load."""
    cfg = DVHConfig.from_dict(
        {
            "include_dmean": True,
            "d_at_volumes_pct": [95],
            "v_at_doses_gy": [20],
        }
    )
    assert cfg.d_at_volumes_cc == []


def test_dvh_config_disabled_when_all_off():
    cfg = DVHConfig(False, False, False, [], [], [])
    assert cfg.any_enabled() is False


def test_dvh_config_any_enabled_with_only_cc():
    cfg = DVHConfig(False, False, False, [], [2.0], [])
    assert cfg.any_enabled() is True


def test_dvh_config_output_keys_order():
    cfg = DVHConfig(
        include_dmean=True,
        include_dmax=True,
        include_dmin=True,
        d_at_volumes_pct=[95, 50, 5],
        d_at_volumes_cc=[2, 0.1],
        v_at_doses_gy=[20, 30],
    )
    keys = cfg.output_keys()
    assert keys == [
        "dmin_gy",
        "dmean_gy",
        "dmax_gy",
        "d95_gy",
        "d50_gy",
        "d5_gy",
        "d2cc_gy",
        "d0.1cc_gy",
        "v20gy_cc",
        "v30gy_cc",
    ]
