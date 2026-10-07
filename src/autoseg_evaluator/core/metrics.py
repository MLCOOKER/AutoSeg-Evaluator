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
from autoseg_evaluator.core.tolerance_keys import normalise_tolerances, tolerance_key

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


def overlap_cc(mask: sitk.Image, region: np.ndarray) -> float:
    """Volume of ``mask`` inside ``region``, in cubic centimetres.

    ``region`` is a boolean array on the mask's own grid, in its (z, y, x)
    order: the PTV, rasterised on the same CT. Counted as ``volume_cc`` counts,
    so the two are comparable.
    """
    arr = sitk.GetArrayViewFromImage(mask)
    if arr.shape != region.shape:
        raise ValueError(
            f"the mask ({arr.shape}) and the region ({region.shape}) are not on the same grid"
        )
    return float(int(np.count_nonzero(np.logical_and(arr, region))) * _voxel_volume_cc(mask))


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
    return {
        **_volume_metrics(volume_cc(gt_mask), volume_cc(test_mask)),
        **_offset_metrics(centroid_physical(gt_mask), centroid_physical(test_mask)),
    }


def _volume_metrics(v_gt: float, v_test: float) -> dict[str, float]:
    return {
        "volume_gt_cc": v_gt,
        "volume_test_cc": v_test,
        "volume_diff_cc": v_test - v_gt,
        "volume_ratio": (v_test / v_gt) if v_gt > 0 else math.nan,
    }


def _cropped_centroid(
    image: sitk.Image, arr: np.ndarray, corner_zyx: tuple[int, int, int]
) -> tuple[float, float, float] | None:
    """:func:`centroid_physical` of a mask given as a crop of its voxels.

    ``corner_zyx`` is the index of the crop's first voxel in ``image``. It is
    added to the integer indices before they are averaged, so the centroid
    comes out bit for bit as the uncropped one: the same indices, in the same
    order, in the same sum.
    """
    if int(arr.sum()) == 0:
        return None
    zs, ys, xs = np.nonzero(arr)
    z0, y0, x0 = corner_zyx
    index = (float((xs + x0).mean()), float((ys + y0).mean()), float((zs + z0).mean()))
    return tuple(image.TransformContinuousIndexToPhysicalPoint(index))


def _offset_metrics(c_gt, c_test) -> dict[str, float]:
    """Offset of the test's centroid from the ground truth's, in mm."""
    if c_gt is None or c_test is None:
        dx = dy = dz = math.nan
        offset = math.nan
    else:
        dx = c_test[0] - c_gt[0]
        dy = c_test[1] - c_gt[1]
        dz = c_test[2] - c_gt[2]
        offset = float(math.sqrt(dx * dx + dy * dy + dz * dz))
    return {
        "com_offset_mm": offset,
        "com_dx_mm": float(dx) if not math.isnan(dx) else math.nan,
        "com_dy_mm": float(dy) if not math.isnan(dy) else math.nan,
        "com_dz_mm": float(dz) if not math.isnan(dz) else math.nan,
    }


# ---- The pair's shared region ----------------------------------------------


def _voxels(mask: sitk.Image) -> np.ndarray:
    """A mask's voxels as ``(z, y, x)`` uint8, without a copy when they already are.

    A read-only view of the image's own buffer, so it is only valid while the
    image is alive; every caller holds the image for the length of the call.
    """
    view = sitk.GetArrayViewFromImage(mask)
    return view if view.dtype == np.uint8 else view.astype(np.uint8)


def _extent(arr: np.ndarray) -> tuple[int, int, int, int, int, int] | None:
    """``(z0, z1, y0, y1, x0, x1)``, half-open, holding every foreground voxel.

    One pass over the volume finds the occupied slices; the other two axes are
    then searched within those slices only.
    """
    zs = np.flatnonzero(arr.max(axis=(1, 2)))
    if zs.size == 0:
        return None
    z0, z1 = int(zs[0]), int(zs[-1]) + 1
    slab = arr[z0:z1]
    ys = np.flatnonzero(slab.max(axis=(0, 2)))
    y0, y1 = int(ys[0]), int(ys[-1]) + 1
    xs = np.flatnonzero(slab[:, y0:y1].max(axis=(0, 1)))
    x0, x1 = int(xs[0]), int(xs[-1]) + 1
    return z0, z1, y0, y1, x0, x1


def _union_extent(a, b) -> tuple[int, int, int, int, int, int] | None:
    if a is None or b is None:
        return a if b is None else b
    return (
        min(a[0], b[0]),
        max(a[1], b[1]),
        min(a[2], b[2]),
        max(a[3], b[3]),
        min(a[4], b[4]),
        max(a[5], b[5]),
    )


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
    return _measure(gt_mask, test_mask, {}, audit=True)[1]


def geometric_metrics_with_audit(
    gt_mask: sitk.Image,
    test_mask: sitk.Image,
    config: dict[str, Any],
) -> tuple[dict[str, float], dict[str, Any]]:
    """:func:`compute_geometric_metrics` and :func:`mask_audit_detail` in one pass.

    The audit record reads its two directions from the surface distances the
    metrics computed, and its counts from the same cropped masks. Asking for
    them separately computed the distance transforms twice per pair, and copied
    the whole CT again: roughly half as much time again as the metrics.
    """
    return _measure(gt_mask, test_mask, config, audit=True)


def _audit_detail(
    gt_mask: sitk.Image, gt_arr: np.ndarray, test_arr: np.ndarray, sd: dict | None
) -> dict[str, Any]:
    """The audit record from a pair's (possibly cropped) masks and distances.

    ``sd`` is ``None`` when a mask is empty. Counts are of nonzero voxels, as
    over boolean masks, and slices are those with any.
    """
    spacing_xyz = tuple(float(v) for v in gt_mask.GetSpacing())
    detail: dict[str, Any] = {
        "rasteriser_backend": str(default_rasteriser_name()),
        "voxel_spacing_mm": list(spacing_xyz),
        "voxel_volume_mm3": float(np.prod(spacing_xyz)),
        "gt_voxels": int(np.count_nonzero(gt_arr)),
        "test_voxels": int(np.count_nonzero(test_arr)),
        # With the two counts above, enough to recompute Dice, precision and
        # recall from the record alone.
        "overlap_voxels": int(np.count_nonzero(np.logical_and(gt_arr, test_arr))),
        "gt_slices_touched": int(np.count_nonzero(gt_arr.any(axis=(1, 2)))),
        "test_slices_touched": int(np.count_nonzero(test_arr.any(axis=(1, 2)))),
        "measure": "surface area of each surface element, mm^2",
        "quantisation": "distances are quantised to the voxel lattice above",
    }
    if sd is None:
        detail["note"] = "one mask is empty; no surface distances to report"
        return detail

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
    * ``tolerances``: ``{surface_dice_tau_mm: float | list[float]}``

    Returns a flat ``{metric_name: float}`` dict. Surface-distance derived
    metrics share a single ``compute_surface_distances`` call so we don't pay
    the cost twice. Surface Dice is keyed by its tolerance —
    ``surface_dice@3mm`` — one entry per tolerance requested.
    """
    return _measure(gt_mask, test_mask, config, audit=False)[0]


def _measure(
    gt_mask: sitk.Image,
    test_mask: sitk.Image,
    config: dict[str, Any],
    *,
    audit: bool,
) -> tuple[dict[str, float], dict[str, Any] | None]:
    """The metrics ``config`` asks for, and the audit record if ``audit``."""
    geom = dict(config.get("geometric", {}) or {})
    tols = dict(config.get("tolerances", {}) or {})
    # One Surface Dice per tolerance, each under its own key. The surface
    # distances are computed once, so every further tolerance is a threshold.
    sd_taus = normalise_tolerances(tols.get("surface_dice_tau_mm"))

    # Both masks, cropped to the smallest box holding both structures. Every
    # metric here is decided inside it: outside, both masks are empty, which
    # adds nothing to a count, a centroid or a surface, and the surface-distance
    # code already cropped to this same box itself. The whole CT used to be
    # copied four times per pair and scanned by each metric in turn; now each
    # mask is read in place, and scanned once to find the box.
    gt_full = _voxels(gt_mask)
    test_full = _voxels(test_mask)
    box = _union_extent(_extent(gt_full), _extent(test_full))
    if box is None:  # both empty: there is nothing to crop to
        gt_arr, test_arr, corner = gt_full, test_full, (0, 0, 0)
    else:
        z0, z1, y0, y1, x0, x1 = box
        gt_arr = gt_full[z0:z1, y0:y1, x0:x1]
        test_arr = test_full[z0:z1, y0:y1, x0:x1]
        corner = (z0, y0, x0)

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
    # The audit reports distances only between two surfaces that both exist.
    both_drawn = audit and bool(gt_arr.any()) and bool(test_arr.any())
    sd = None
    if needs_sd or both_drawn:
        sd = compute_surface_distances(gt_arr, test_arr, spacing_for_array)
    if needs_sd:
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
            for tau in sd_taus:
                out[tolerance_key("surface_dice", tau)] = compute_surface_dice_at_tolerance(sd, tau)

    # Two independent checkboxes; each is computed only when ticked. A centroid
    # lists the index of every foreground voxel, which volume alone never needs.
    if geom.get("volume"):
        out.update(
            _volume_metrics(
                float(int(gt_arr.sum()) * _voxel_volume_cc(gt_mask)),
                float(int(test_arr.sum()) * _voxel_volume_cc(test_mask)),
            )
        )
    if geom.get("com_offset"):
        out.update(
            _offset_metrics(
                _cropped_centroid(gt_mask, gt_arr, corner),
                _cropped_centroid(test_mask, test_arr, corner),
            )
        )

    if not audit:
        return out, None
    return out, _audit_detail(gt_mask, gt_arr, test_arr, sd if both_drawn else None)
