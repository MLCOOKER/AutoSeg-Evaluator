"""STAPLE consensus contour estimation.

Wraps :class:`SimpleITK.STAPLEImageFilter` (Warfield, Zou & Wells, MICCAI 2002)
into a small dataclass-flavoured API the rest of the app can drive without
re-deriving the bounding-box + thresholding boilerplate every time.

For each ``compute_staple`` call we:

1. Compute the union bounding box of all input masks, pad it (so the
   probabilistic edges aren't clipped), and crop every mask to that ROI.
   This is the "small-structure" workaround — STAPLE's automatic prior is
   the average per-rater volume fraction of the whole image; for small
   organs (lens, optic chiasm, cochlea) that fraction is so tiny the EM
   algorithm collapses the consensus to zero. Cropping rescales the prior
   to a more reasonable fraction of the relevant region.
2. Run STAPLE on the cropped stack and read back per-rater
   sensitivity / specificity and the probabilistic truth.
3. Threshold the probability map at 0.5 → binary consensus mask.
4. Pad both the probability map and the binary consensus back to the
   original image extent so downstream metric code can compare them with
   the rater masks unchanged.

The returned :class:`StapleResult` also carries scalar uncertainty
summaries (uncertain-band volume, mean entropy) that the worker dumps into
the consensus summary row.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import SimpleITK as sitk


# ---- Configuration -------------------------------------------------------


@dataclass(frozen=True)
class StapleConfig:
    """User-facing STAPLE knobs surfaced in the Compute tab.

    Defaults mirror the values most consensus-contour studies cite:
    ``max_iterations=30`` is far above SimpleITK's default of 5 (which is too
    few to converge for clinical OARs), ``confidence_weight=1.0`` is the
    "leave it alone" recommendation from the ITK docstring, and
    ``bbox_padding_voxels=5`` gives the probabilistic edges room to breathe
    without ballooning the work region.
    """

    max_iterations: int = 30
    confidence_weight: float = 1.0
    bbox_padding_voxels: int = 5

    @classmethod
    def from_dict(cls, d: dict | None) -> "StapleConfig":
        d = dict(d or {})
        return cls(
            max_iterations=int(d.get("max_iterations", 30)),
            confidence_weight=float(d.get("confidence_weight", 1.0)),
            bbox_padding_voxels=int(d.get("bbox_padding_voxels", 5)),
        )


# ---- Result --------------------------------------------------------------


@dataclass
class StapleResult:
    """Outputs of one STAPLE run.

    ``sensitivities`` and ``specificities`` are indexed identically to the
    ``masks`` list passed to :func:`compute_staple` — caller is responsible
    for tracking which index belonged to which rater.
    """

    consensus_mask: sitk.Image          # uint8 binary, P ≥ 0.5
    probability_map: sitk.Image         # float32 in [0, 1]
    sensitivities: list[float]
    specificities: list[float]
    elapsed_iterations: int
    max_iterations: int
    n_raters: int
    consensus_volume_cc: float
    uncertain_band_cc: float            # voxels with 0.2 < P < 0.8 (often 0 once STAPLE converges)
    mean_entropy: float                 # binary entropy over P > 0.05 voxels
    rater_disagreement_cc: float        # voxels where raters split (some yes, some no)
    rater_volume_range_cc: float        # max(per-rater volume) − min(per-rater volume)

    @property
    def converged(self) -> bool:
        """``True`` when STAPLE stopped before hitting the iteration cap."""
        return self.elapsed_iterations < self.max_iterations


# ---- Main entry point ----------------------------------------------------


def compute_staple(
    masks: list[sitk.Image],
    config: StapleConfig | None = None,
) -> StapleResult | None:
    """Run STAPLE on a list of binary masks (same geometry).

    Returns ``None`` when fewer than 2 non-empty masks are supplied — STAPLE
    is undefined with 0 or 1 raters. Caller should treat this as "consensus
    not available for this drawer/patient" and skip emitting consensus rows.
    """
    cfg = config or StapleConfig()
    valid = [m for m in masks if m is not None and _is_non_empty(m)]
    if len(valid) < 2:
        return None

    # All masks must share geometry — fail loud if they don't (this would be
    # a programming error upstream, since the worker only passes masks
    # derived from the same CT).
    reference = valid[0]
    for m in valid[1:]:
        if m.GetSize() != reference.GetSize() or m.GetSpacing() != reference.GetSpacing():
            raise ValueError("STAPLE requires all masks to share geometry.")

    # Crop to the union bounding box + padding (small-structure workaround)
    bbox = _union_bounding_box(valid, padding=cfg.bbox_padding_voxels)
    cropped = [_crop(m, bbox) for m in valid]
    cropped_uint8 = [sitk.Cast(c, sitk.sitkUInt8) for c in cropped]

    # Run STAPLE on the cropped stack
    f = sitk.STAPLEImageFilter()
    f.SetForegroundValue(1)
    f.SetMaximumIterations(int(cfg.max_iterations))
    f.SetConfidenceWeight(float(cfg.confidence_weight))
    prob_cropped = f.Execute(cropped_uint8)
    # SimpleITK's STAPLE returns tuples — one entry per input rater, in the
    # same order we supplied them.
    sensitivities = [float(s) for s in f.GetSensitivity()]
    specificities = [float(s) for s in f.GetSpecificity()]
    elapsed = int(f.GetElapsedIterations())

    # Build the binary consensus (cropped), then pad both back to the
    # original image extent so downstream code can compare against masks
    # that were never cropped.
    bin_cropped = sitk.BinaryThreshold(prob_cropped, lowerThreshold=0.5, upperThreshold=1.0)
    bin_cropped = sitk.Cast(bin_cropped, sitk.sitkUInt8)
    probability_map = _pad_back(prob_cropped, reference, bbox, default=0.0, pixel_type=sitk.sitkFloat32)
    consensus_mask = _pad_back(bin_cropped, reference, bbox, default=0, pixel_type=sitk.sitkUInt8)

    # Scalar uncertainty summaries
    consensus_volume_cc = _volume_cc(consensus_mask)
    uncertain_band_cc, mean_entropy = _uncertainty_metrics(probability_map)
    rater_disagreement_cc, rater_volume_range_cc = _rater_disagreement(valid)

    return StapleResult(
        consensus_mask=consensus_mask,
        probability_map=probability_map,
        sensitivities=sensitivities,
        specificities=specificities,
        elapsed_iterations=elapsed,
        max_iterations=int(cfg.max_iterations),
        n_raters=len(valid),
        consensus_volume_cc=consensus_volume_cc,
        uncertain_band_cc=uncertain_band_cc,
        mean_entropy=mean_entropy,
        rater_disagreement_cc=rater_disagreement_cc,
        rater_volume_range_cc=rater_volume_range_cc,
    )


# ---- Helpers --------------------------------------------------------------


def _is_non_empty(mask: sitk.Image) -> bool:
    return int(sitk.GetArrayViewFromImage(mask).sum()) > 0


def _union_bounding_box(masks: list[sitk.Image], padding: int) -> tuple[int, int, int, int, int, int]:
    """Return ``(x0, y0, z0, x1, y1, z1)`` of the padded union bounding box (inclusive)."""
    size = masks[0].GetSize()  # (x, y, z)
    union = None
    for m in masks:
        arr = sitk.GetArrayViewFromImage(m)  # (z, y, x)
        if union is None:
            union = (arr > 0).copy()
        else:
            union = np.logical_or(union, arr > 0)
    if union is None or not union.any():
        # Shouldn't happen — we filter empty masks above — but guard anyway.
        return 0, 0, 0, size[0] - 1, size[1] - 1, size[2] - 1
    zs, ys, xs = np.where(union)
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    z0, z1 = int(zs.min()), int(zs.max())
    # Pad and clamp to image extent
    x0 = max(0, x0 - padding)
    y0 = max(0, y0 - padding)
    z0 = max(0, z0 - padding)
    x1 = min(size[0] - 1, x1 + padding)
    y1 = min(size[1] - 1, y1 + padding)
    z1 = min(size[2] - 1, z1 + padding)
    return x0, y0, z0, x1, y1, z1


def _crop(image: sitk.Image, bbox: tuple[int, int, int, int, int, int]) -> sitk.Image:
    x0, y0, z0, x1, y1, z1 = bbox
    return image[x0:x1 + 1, y0:y1 + 1, z0:z1 + 1]


def _pad_back(
    cropped: sitk.Image,
    reference: sitk.Image,
    bbox: tuple[int, int, int, int, int, int],
    *,
    default,
    pixel_type,
) -> sitk.Image:
    """Place ``cropped`` back into the reference image extent at ``bbox``."""
    ref_size = reference.GetSize()  # (x, y, z)
    x0, y0, z0, x1, y1, z1 = bbox
    # numpy shape order is (z, y, x)
    arr = np.full((ref_size[2], ref_size[1], ref_size[0]),
                  default,
                  dtype=np.float32 if pixel_type == sitk.sitkFloat32 else np.uint8)
    cropped_arr = sitk.GetArrayFromImage(cropped)
    arr[z0:z1 + 1, y0:y1 + 1, x0:x1 + 1] = cropped_arr.astype(arr.dtype)
    out = sitk.GetImageFromArray(arr)
    out.CopyInformation(reference)
    return sitk.Cast(out, pixel_type)


def _volume_cc(mask: sitk.Image) -> float:
    sx, sy, sz = mask.GetSpacing()
    voxel_cc = (sx * sy * sz) / 1000.0
    return float(int(sitk.GetArrayViewFromImage(mask).sum()) * voxel_cc)


def _rater_disagreement(masks: list[sitk.Image]) -> tuple[float, float]:
    """Pre-STAPLE inter-rater disagreement, in physical units.

    Returns ``(disagreement_volume_cc, volume_range_cc)``:

    * ``disagreement_volume_cc`` — volume of voxels where at least one rater
      said yes AND at least one said no (i.e. union − intersection). For
      converged STAPLE this is the most honest single-number uncertainty
      signal — it doesn't require the EM posteriors to stay non-extreme.
    * ``volume_range_cc`` — ``max − min`` of per-rater contour volumes.
      Useful for spotting outlier raters at a glance.
    """
    sx, sy, sz = masks[0].GetSpacing()
    voxel_cc = float(sx * sy * sz) / 1000.0
    union = None
    intersection = None
    per_rater_volumes: list[int] = []
    for m in masks:
        arr = sitk.GetArrayViewFromImage(m) > 0
        per_rater_volumes.append(int(arr.sum()))
        if union is None:
            union = arr.copy()
            intersection = arr.copy()
        else:
            union = np.logical_or(union, arr)
            intersection = np.logical_and(intersection, arr)
    disagreement_voxels = int(union.sum()) - int(intersection.sum())
    disagreement_cc = float(disagreement_voxels * voxel_cc)
    if per_rater_volumes:
        v_range_cc = float((max(per_rater_volumes) - min(per_rater_volumes)) * voxel_cc)
    else:
        v_range_cc = 0.0
    return disagreement_cc, v_range_cc


def _uncertainty_metrics(probability_map: sitk.Image) -> tuple[float, float]:
    """Return ``(uncertain_band_volume_cc, mean_binary_entropy)``.

    * ``uncertain_band_cc`` — volume of voxels with 0.2 < P < 0.8 (cc). These
      are the voxels where raters disagreed enough that STAPLE can't put
      the boundary cleanly.
    * ``mean_entropy`` — average of ``-P·log P − (1−P)·log(1−P)`` over
      voxels with P > 0.05 (excludes the bulk-background that dominates an
      unfiltered mean). 0 = perfect agreement; ln 2 ≈ 0.693 = max disagreement.
    """
    arr = sitk.GetArrayViewFromImage(probability_map).astype(np.float64)
    sx, sy, sz = probability_map.GetSpacing()
    voxel_cc = (sx * sy * sz) / 1000.0

    band = np.logical_and(arr > 0.2, arr < 0.8)
    uncertain_band_cc = float(int(band.sum()) * voxel_cc)

    relevant = arr[arr > 0.05]
    if relevant.size == 0:
        return uncertain_band_cc, 0.0
    # Clip to avoid log(0). Binary entropy: H(p) = -p log p - (1-p) log(1-p).
    p = np.clip(relevant, 1e-9, 1.0 - 1e-9)
    h = -(p * np.log(p) + (1.0 - p) * np.log(1.0 - p))
    return uncertain_band_cc, float(h.mean())
