"""Tests for the high-level metrics module + DVH config."""

from __future__ import annotations

import math

import numpy as np
import SimpleITK as sitk

from autoseg_evaluator.core.dvh import DVHConfig
from autoseg_evaluator.core.metrics import (
    apl_mean,
    apl_total,
    centroid_physical,
    compute_geometric_metrics,
    dice,
    hausdorff,
    mean_surface_distance,
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


# ---- APL ------------------------------------------------------------------


def test_apl_identical_masks_is_zero():
    m = _cube_sitk((20, 20, 20), 5, 15, 5, 15, 5, 15)
    assert apl_total(m, m, 0.0) == 0.0
    assert apl_mean(m, m, 0.0) == 0.0


def test_apl_disjoint_masks_is_positive():
    a = _cube_sitk((20, 20, 20), 0, 5, 0, 5, 0, 5)
    b = _cube_sitk((20, 20, 20), 10, 15, 10, 15, 10, 15)
    # With a tolerance smaller than the gap, every reference voxel is "added path"
    total = apl_total(a, b, 1.0)
    assert total > 0


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
            "apl_mean": False,
            "apl_total": False,
        },
        "tolerances": {"surface_dice_tau_mm": 3.0, "apl_tolerance_mm": 3.0},
    }
    out = compute_geometric_metrics(m, m, config)
    assert "dice" in out
    assert "surface_dice" in out
    assert "hausdorff100" not in out
    assert "apl_total" not in out


def test_compute_geometric_metrics_identical_masks_score_perfect():
    m = _cube_sitk((20, 20, 20), 5, 15, 5, 15, 5, 15)
    config = {
        "geometric": {
            "dice": True,
            "hausdorff100": True,
            "hausdorff95": True,
            "mean_surface_distance": True,
            "surface_dice": True,
            "apl_mean": True,
            "apl_total": True,
        },
        "tolerances": {"surface_dice_tau_mm": 0.0, "apl_tolerance_mm": 0.0},
    }
    out = compute_geometric_metrics(m, m, config)
    assert out["dice"] == 1.0
    assert out["hausdorff100"] == 0.0
    assert out["hausdorff95"] == 0.0
    assert out["mean_surface_distance"] == 0.0
    assert out["surface_dice"] == 1.0
    assert out["apl_total"] == 0.0


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
