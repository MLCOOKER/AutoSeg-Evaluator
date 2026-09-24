"""High-level 3D mask metric functions.

These are the public entry points the worker calls per (GT, test) pair. They
wrap :mod:`autoseg_evaluator.core.surface_distance` for surface-based metrics.

Added path length is not here. It measures boundary to be redrawn, which needs
the edge as drawn rather than as a voxel staircase, so it lives in the 2D
contour stream (:mod:`autoseg_evaluator.core.polygon_metrics`). The mask
version v1 shipped — a port of PlatiPy's per-slice dilation — was removed in v3
(spec D3), which makes v1's APL values historical rather than reproducible.

The :func:`compute_geometric_metrics` aggregator returns a flat ``{metric: value}``
dict honouring the user's checkbox/tolerance configuration; the worker just
forwards its config and writes the result row.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import SimpleITK as sitk

from autoseg_evaluator.core.masks import default_rasteriser_name
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


def precision_recall(gt_arr: np.ndarray, test_arr: np.ndarray) -> tuple[float, float]:
    """``(precision, recall)`` of a test mask against the ground truth.

    Precision is the share of the test's volume that lies inside the ground
    truth; it falls when the test over-segments. Recall is the share of the
    ground truth's volume the test covers; it falls when the test
    under-segments. Dice cannot tell those two failures apart — a contour
    drawn 20 % too large and one drawn 20 % too small can score the same —
    and these two can.

    Dice is their harmonic mean, which is why F1 is not reported: on binary
    masks it *is* Dice.

    Each is NaN when its denominator is empty — precision for an empty test,
    recall for an empty ground truth — because a share of nothing is not a
    number, and zero would read as a real, complete failure.
    """
    gt = np.asarray(gt_arr).astype(bool)
    test = np.asarray(test_arr).astype(bool)
    overlap = float(np.count_nonzero(gt & test))
    test_voxels = float(np.count_nonzero(test))
    gt_voxels = float(np.count_nonzero(gt))
    precision = overlap / test_voxels if test_voxels else math.nan
    recall = overlap / gt_voxels if gt_voxels else math.nan
    return precision, recall


def hausdorff(
    gt_arr: np.ndarray, test_arr: np.ndarray, spacing_mm, percent: float = 100.0
) -> float:
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


# ---- Aggregator ----------------------------------------------------------


def mask_audit_detail(
    gt_mask: sitk.Image,
    test_mask: sitk.Image,
) -> dict[str, Any]:
    """What the mask metrics were measured over, for the audit record.

    The table reports one symmetric number per metric; this reports the two
    directions it came from, what each was weighted by, and the lattice it was
    all quantised to. The asymmetry is often the finding — a ground truth
    reaching far from anything the test drew, against a test that stays close to
    the ground truth, is under-segmentation, and the symmetric maximum conceals
    which side it came from.

    Also records the rasteriser backend, because switching it moves every
    mask-derived number in the run and a result that does not say which one
    produced it cannot be reproduced.
    """
    gt_arr = sitk.GetArrayFromImage(gt_mask).astype(bool)
    test_arr = sitk.GetArrayFromImage(test_mask).astype(bool)
    spacing_xyz = tuple(float(v) for v in gt_mask.GetSpacing())
    spacing_for_array = (spacing_xyz[2], spacing_xyz[1], spacing_xyz[0])
    voxel_mm3 = float(np.prod(spacing_xyz))

    detail: dict[str, Any] = {
        "rasteriser_backend": str(default_rasteriser_name()),
        "voxel_spacing_mm": list(spacing_xyz),
        "voxel_volume_mm3": voxel_mm3,
        "gt_voxels": int(gt_arr.sum()),
        "test_voxels": int(test_arr.sum()),
        # With the two counts above, enough to recompute Dice, precision and
        # recall from the record alone.
        "overlap_voxels": int((gt_arr & test_arr).sum()),
        "gt_slices_touched": int((gt_arr.sum(axis=(1, 2)) > 0).sum()),
        "test_slices_touched": int((test_arr.sum(axis=(1, 2)) > 0).sum()),
        "measure": "surface area of each surface element, mm^2",
        "quantisation": "distances are quantised to the voxel lattice above",
    }
    if not gt_arr.any() or not test_arr.any():
        detail["note"] = "one mask is empty; no surface distances to report"
        return detail

    sd = compute_surface_distances(gt_arr, test_arr, spacing_for_array)
    for label, distances, areas in (
        ("gt_to_test", sd["distances_gt_to_pred"], sd["surfel_areas_gt"]),
        ("test_to_gt", sd["distances_pred_to_gt"], sd["surfel_areas_pred"]),
    ):
        if len(distances) == 0 or float(np.sum(areas)) == 0.0:
            detail[label] = {"surfels": 0}
            continue
        cumulative = np.cumsum(areas) / np.sum(areas)
        index = min(int(np.searchsorted(cumulative, 0.95)), len(distances) - 1)
        detail[label] = {
            "max_mm": float(distances.max()),
            "hd95_mm": float(distances[index]),
            "mean_mm": float(np.sum(distances * areas) / np.sum(areas)),
            "surfels": int(len(distances)),
            "surface_area_mm2": float(np.sum(areas)),
        }
    return detail


def compute_geometric_metrics(
    gt_mask: sitk.Image,
    test_mask: sitk.Image,
    config: dict[str, Any],
) -> dict[str, float]:
    """Compute every geometric metric the user has enabled, in one pass.

    ``config`` has the same shape as :meth:`ComputeTab.config`:

    * ``geometric``: ``{dice: bool, precision_recall: bool, hausdorff100: bool,
      hausdorff95: bool, mean_surface_distance: bool, surface_dice: bool,
      volume: bool, com_offset: bool}``
    * ``tolerances``: ``{surface_dice_tau_mm: float}``

    Returns a flat ``{metric_name: float}`` dict. Surface-distance derived
    metrics share a single ``compute_surface_distances`` call so we don't pay
    the cost twice.
    """
    geom = dict(config.get("geometric", {}) or {})
    tols = dict(config.get("tolerances", {}) or {})
    sd_tau = float(tols.get("surface_dice_tau_mm", 3.0))

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
    if geom.get("precision_recall"):
        out["precision"], out["recall"] = precision_recall(gt_arr, test_arr)

    needs_sd = (
        geom.get("hausdorff100")
        or geom.get("hausdorff95")
        or geom.get("mean_surface_distance")
        or geom.get("surface_dice")
    )
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
