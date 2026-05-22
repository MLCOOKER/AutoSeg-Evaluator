"""High-level geometric metric functions + Added Path Length (APL).

These are the public entry points the worker calls per (GT, test) pair. They
wrap :mod:`autoseg_evaluator.core.surface_distance` for surface-based
metrics, and port the v1 / PlatiPy APL implementation directly.

The :func:`compute_geometric_metrics` aggregator returns a flat ``{metric: value}``
dict honouring the user's checkbox/tolerance configuration; the worker just
forwards its config and writes the result row.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import SimpleITK as sitk

from autoseg_evaluator.core.surface_distance import (
    compute_average_surface_distance,
    compute_dice_coefficient,
    compute_robust_hausdorff,
    compute_surface_dice_at_tolerance,
    compute_surface_distances,
)


# ---- Individual metric wrappers ------------------------------------------


def dice(gt_arr: np.ndarray, test_arr: np.ndarray) -> float:
    return compute_dice_coefficient(gt_arr, test_arr)


def hausdorff(gt_arr: np.ndarray, test_arr: np.ndarray, spacing_mm, percent: float = 100.0) -> float:
    sd = compute_surface_distances(gt_arr, test_arr, spacing_mm)
    return compute_robust_hausdorff(sd, percent)


def mean_surface_distance(gt_arr: np.ndarray, test_arr: np.ndarray, spacing_mm) -> float:
    """Symmetric mean surface distance — the average of the two directional means.

    Returns NaN if EITHER direction is NaN (matches v1's behaviour exactly).
    """
    sd = compute_surface_distances(gt_arr, test_arr, spacing_mm)
    a, b = compute_average_surface_distance(sd)
    if math.isnan(a) or math.isnan(b):
        return math.nan
    return float(0.5 * (a + b))


def surface_dice(
    gt_arr: np.ndarray,
    test_arr: np.ndarray,
    spacing_mm,
    tolerance_mm: float = 3.0,
) -> float:
    sd = compute_surface_distances(gt_arr, test_arr, spacing_mm)
    return compute_surface_dice_at_tolerance(sd, tolerance_mm)


# ---- Volume + centre-of-mass offset --------------------------------------


def _voxel_volume_cc(image: sitk.Image) -> float:
    sx, sy, sz = image.GetSpacing()
    return float(sx * sy * sz) / 1000.0


def volume_cc(mask: sitk.Image) -> float:
    """Absolute volume of a binary mask in cubic centimetres."""
    arr = sitk.GetArrayViewFromImage(mask)
    return float(int(arr.sum()) * _voxel_volume_cc(mask))


def centroid_physical(mask: sitk.Image) -> tuple[float, float, float] | None:
    """Return the physical (x, y, z) mm centroid of a binary mask, or None if empty.

    Uses ``TransformContinuousIndexToPhysicalPoint`` so the result honours the
    image's origin and direction cosines — i.e. the centroid is reported in
    the same patient-coordinate frame as the source CT.
    """
    arr = sitk.GetArrayViewFromImage(mask)
    if int(arr.sum()) == 0:
        return None
    # numpy arr shape is (z, y, x); average each axis where the mask is set.
    zs, ys, xs = np.nonzero(arr)
    cont_index = (float(xs.mean()), float(ys.mean()), float(zs.mean()))
    return tuple(mask.TransformContinuousIndexToPhysicalPoint(cont_index))


def volume_and_com_metrics(gt_mask: sitk.Image, test_mask: sitk.Image) -> dict[str, float]:
    """Compute volume (cc), volume difference / ratio, and centroid offset (mm).

    Returns a flat dict ready for merging into the per-row metrics output. Any
    value that cannot be defined (e.g. ratio when GT is empty) is set to NaN.
    """
    v_gt = volume_cc(gt_mask)
    v_test = volume_cc(test_mask)
    v_diff = v_test - v_gt
    v_ratio = (v_test / v_gt) if v_gt > 0 else math.nan

    c_gt = centroid_physical(gt_mask)
    c_test = centroid_physical(test_mask)
    if c_gt is None or c_test is None:
        dx = dy = dz = math.nan
        offset = math.nan
    else:
        dx = c_test[0] - c_gt[0]
        dy = c_test[1] - c_gt[1]
        dz = c_test[2] - c_gt[2]
        offset = float(math.sqrt(dx * dx + dy * dy + dz * dz))

    return {
        "volume_gt_cc": v_gt,
        "volume_test_cc": v_test,
        "volume_diff_cc": v_diff,
        "volume_ratio": v_ratio,
        "com_offset_mm": offset,
        "com_dx_mm": float(dx) if not math.isnan(dx) else math.nan,
        "com_dy_mm": float(dy) if not math.isnan(dy) else math.nan,
        "com_dz_mm": float(dz) if not math.isnan(dz) else math.nan,
    }


# ---- APL (Added Path Length) ---------------------------------------------


def _apl_per_slice(label_ref: sitk.Image, label_test: sitk.Image, distance_threshold_mm: float):
    """Port of v1's per-slice APL — returns a list of per-slice voxel counts."""
    result: list[int] = []
    n_slices = label_ref.GetSize()[2]
    avg_xy = float(np.mean(label_ref.GetSpacing()[:2]))
    dilate_kernel_size = int(np.ceil(distance_threshold_mm / avg_xy)) if avg_xy else 0
    for i in range(n_slices):
        ref_view = sitk.GetArrayViewFromImage(label_ref)[i]
        test_view = sitk.GetArrayViewFromImage(label_test)[i]
        if (ref_view.sum() + test_view.sum()) == 0:
            continue
        ref_contour = sitk.LabelContour(label_ref[:, :, i])
        test_contour = sitk.LabelContour(label_test[:, :, i])
        if distance_threshold_mm > 0:
            kernel = [dilate_kernel_size, dilate_kernel_size]
            test_contour = sitk.BinaryDilate(test_contour, kernel)
        test_contour.CopyInformation(ref_contour)
        added_path = sitk.MaskNegated(ref_contour, test_contour)
        result.append(int(sitk.GetArrayViewFromImage(added_path).sum()))
    return result


def apl_total(
    label_ref: sitk.Image, label_test: sitk.Image, distance_threshold_mm: float = 3.0
) -> float:
    """Total APL in mm. Faithful to PlatiPy's ``compute_metric_total_apl``."""
    arr = _apl_per_slice(label_ref, label_test, distance_threshold_mm)
    avg_xy = float(np.mean(label_ref.GetSpacing()[:2]))
    # np.sum([]) → 0.0, so an empty result naturally yields 0 (matches PlatiPy).
    return float(np.sum(arr) * avg_xy)


def apl_mean(
    label_ref: sitk.Image, label_test: sitk.Image, distance_threshold_mm: float = 3.0
) -> float:
    """Mean APL in mm. Faithful to PlatiPy's ``compute_metric_mean_apl``.

    Returns NaN when no slice contributed (both masks empty across every Z
    slice) — matching ``np.mean([])`` behaviour in PlatiPy, just without the
    runtime warning.
    """
    arr = _apl_per_slice(label_ref, label_test, distance_threshold_mm)
    if not arr:
        return math.nan
    avg_xy = float(np.mean(label_ref.GetSpacing()[:2]))
    return float(np.mean(arr) * avg_xy)


# ---- Aggregator ----------------------------------------------------------


def compute_geometric_metrics(
    gt_mask: sitk.Image,
    test_mask: sitk.Image,
    config: dict[str, Any],
) -> dict[str, float]:
    """Compute every geometric metric the user has enabled, in one pass.

    ``config`` has the same shape as :meth:`ComputeTab.config`:

    * ``geometric``: ``{dice: bool, hausdorff100: bool, hausdorff95: bool,
      mean_surface_distance: bool, surface_dice: bool, apl_mean: bool,
      apl_total: bool}``
    * ``tolerances``: ``{surface_dice_tau_mm: float, apl_tolerance_mm: float}``

    Returns a flat ``{metric_name: float}`` dict. Surface-distance derived
    metrics share a single ``compute_surface_distances`` call so we don't pay
    the cost twice.
    """
    geom = dict(config.get("geometric", {}) or {})
    tols = dict(config.get("tolerances", {}) or {})
    sd_tau = float(tols.get("surface_dice_tau_mm", 3.0))
    apl_tau = float(tols.get("apl_tolerance_mm", 3.0))

    gt_arr = sitk.GetArrayFromImage(gt_mask).astype(np.uint8)
    test_arr = sitk.GetArrayFromImage(test_mask).astype(np.uint8)

    # SimpleITK reports spacing as (x, y, z) but the numpy array shape is
    # (z, y, x). The surface-distance algorithm (and the underlying
    # ``ndimage.distance_transform_edt(sampling=…)``) expects ``sampling``
    # in the SAME axis order as the input array — so we reorder here.
    # Note: v1 (Rusanov et al. 2025) inadvertently passed (x, y, z) to a
    # (z, y, x) array, which produced incorrect physical distances for
    # anisotropic CT (e.g. 0.98 × 0.98 × 3 mm). The v2 ordering is the
    # mathematically correct one for clinical use.
    spacing_xyz = tuple(gt_mask.GetSpacing())  # (x, y, z)
    spacing_for_array = (spacing_xyz[2], spacing_xyz[1], spacing_xyz[0])  # (z, y, x)

    out: dict[str, float] = {}

    if geom.get("dice"):
        out["dice"] = dice(gt_arr, test_arr)

    needs_sd = geom.get("hausdorff100") or geom.get("hausdorff95") or geom.get(
        "mean_surface_distance"
    ) or geom.get("surface_dice")
    if needs_sd:
        sd = compute_surface_distances(gt_arr, test_arr, spacing_for_array)
        if geom.get("hausdorff100"):
            out["hausdorff100"] = compute_robust_hausdorff(sd, 100)
        if geom.get("hausdorff95"):
            out["hausdorff95"] = compute_robust_hausdorff(sd, 95)
        if geom.get("mean_surface_distance"):
            a, b = compute_average_surface_distance(sd)
            # v1 returns NaN if EITHER direction is NaN (e.g. one mask is empty).
            # Keep that behaviour for exact compatibility with v1's published values.
            if math.isnan(a) or math.isnan(b):
                out["mean_surface_distance"] = math.nan
            else:
                out["mean_surface_distance"] = float(0.5 * (a + b))
        if geom.get("surface_dice"):
            out["surface_dice"] = compute_surface_dice_at_tolerance(sd, sd_tau)

    if geom.get("apl_mean"):
        out["apl_mean"] = apl_mean(gt_mask, test_mask, apl_tau)
    if geom.get("apl_total"):
        out["apl_total"] = apl_total(gt_mask, test_mask, apl_tau)

    # Volume + centre-of-mass are computed together (single mask traversal each
    # under the hood), but exposed via two independent checkboxes so users can
    # opt into volume-only or COM-only outputs.
    if geom.get("volume") or geom.get("com_offset"):
        vc = volume_and_com_metrics(gt_mask, test_mask)
        if geom.get("volume"):
            for k in ("volume_gt_cc", "volume_test_cc", "volume_diff_cc", "volume_ratio"):
                out[k] = vc[k]
        if geom.get("com_offset"):
            for k in ("com_offset_mm", "com_dx_mm", "com_dy_mm", "com_dz_mm"):
                out[k] = vc[k]

    return out
