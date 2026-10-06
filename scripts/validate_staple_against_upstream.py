"""Validate AutoSeg Evaluator's STAPLE consensus against the reference filter.

AutoSeg's :func:`autoseg_evaluator.core.staple.compute_staple` wraps
``SimpleITK.STAPLEImageFilter`` (Warfield, Zou & Wells, IEEE TMI 2004 — the
canonical reference implementation of STAPLE) and adds: a union bounding-box
crop, so STAPLE estimates in the structure's neighbourhood rather than over the
scan's field of view, a P >= 0.5 threshold, and a pad-back to the original
image extent.

This script demonstrates that AutoSeg applies those additions exactly as
described, and that STAPLE itself is SimpleITK's, then measures what the crop
changes. For every organ contoured by two or more raters in a folder of CT +
RTSS files, it

1. rasterises each rater's contour to a binary mask (AutoSeg's default
   rasteriser; see Supplementary 2);
2. runs AutoSeg's ``compute_staple`` on the rater masks;
3. **independently** reconstructs the consensus using only upstream
   primitives — it crops the same masks to the union bounding box, invokes
   a fresh ``SimpleITK.STAPLEImageFilter``, applies the P >= 0.5 threshold,
   and pads back — then compares; and
4. runs SimpleITK's filter once more on the same input with **its own
   defaults**, which set no practical iteration limit, and compares again: the
   test that AutoSeg's iteration cap leaves STAPLE to converge as SimpleITK
   would; and
5. runs it capped at 100 iterations, AutoSeg's cap before v3, to show what
   that cap changed; and
6. runs it over the whole image, and over a box widened by 25 voxels, to show
   what the crop itself changes (skip with ``--no-crop-comparison``; it is the
   slow part).

The per-rater sensitivity / specificity (the EM estimates that *are* the
STAPLE algorithm) and the binary consensus mask are compared bit-for-bit;
the derived volume and mean-entropy summaries are recomputed independently
and compared. The only quantity carried over from AutoSeg is the single
integer bounding-box padding it selected (a reported diagnostic), so that
both paths feed the upstream filter the identical prepared input — the
STAPLE estimator, the threshold, and the summaries are all computed fresh
from the reference library.

All output is PHI-safe: no SOPInstanceUIDs, filenames, patient identifiers,
dates, or institution metadata. ROIs are referenced by clinical name only.

Usage::

    python scripts/validate_staple_against_upstream.py \\
        --data /path/to/folder/containing/CT/and/RTSS/files

It writes Supplementary 5, a short report (aim, method, results, findings), and
its full results, and keeps what it computed so ``--render-only`` can rewrite
both without computing again.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import importlib.metadata
import math
import platform
import sys
from pathlib import Path
from typing import Any

import numpy as np
import SimpleITK as sitk

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "src"))

# AutoSeg
from validation_common import (  # noqa: E402
    VALIDATION_DOCS,
    load_results,
    md_table,
    run_info,
    save_results,
    sci,
    stamp,
    unwrap,
)

from autoseg_evaluator.core.masks import (  # noqa: E402
    extract_mask_for_roi,
    read_dicom_image,
    read_rtstruct,
)
from autoseg_evaluator.core.staple import StapleConfig, compute_staple  # noqa: E402

CONFIG = StapleConfig()  # the application's defaults


def _normalise_name(n: str) -> str:
    """Match ROI names case-insensitively + whitespace-collapsed."""
    return "_".join(str(n).split()).lower()


# ------------------------- Independent reference --------------------------


def _union_bbox(masks: list[sitk.Image], padding: int) -> tuple[int, int, int, int, int, int]:
    """Padded ``(x0, y0, z0, x1, y1, z1)`` union bbox — pure NumPy geometry."""
    union: np.ndarray | None = None
    for m in masks:
        arr = sitk.GetArrayFromImage(m) > 0  # (z, y, x)
        union = arr if union is None else np.logical_or(union, arr)
    assert union is not None
    size = masks[0].GetSize()  # (x, y, z)
    zs, ys, xs = np.where(union)
    x0 = max(0, int(xs.min()) - padding)
    y0 = max(0, int(ys.min()) - padding)
    z0 = max(0, int(zs.min()) - padding)
    x1 = min(size[0] - 1, int(xs.max()) + padding)
    y1 = min(size[1] - 1, int(ys.max()) + padding)
    z1 = min(size[2] - 1, int(zs.max()) + padding)
    return x0, y0, z0, x1, y1, z1


def reference_consensus(masks: list[sitk.Image], padding: int) -> dict[str, Any]:
    """Reconstruct STAPLE consensus from upstream primitives only.

    Mirrors AutoSeg's documented pipeline (union-bbox crop -> SimpleITK
    STAPLE -> P >= 0.5 threshold -> pad back) but is implemented here
    independently so the comparison is a genuine cross-check.
    """
    x0, y0, z0, x1, y1, z1 = _union_bbox(masks, padding)
    cropped = [sitk.Cast(m[x0 : x1 + 1, y0 : y1 + 1, z0 : z1 + 1], sitk.sitkUInt8) for m in masks]

    f = sitk.STAPLEImageFilter()
    f.SetForegroundValue(1)
    f.SetMaximumIterations(int(CONFIG.max_iterations))
    f.SetConfidenceWeight(float(CONFIG.confidence_weight))
    prob_cropped = f.Execute(cropped)
    sens = [float(s) for s in f.GetSensitivity()]
    spec = [float(s) for s in f.GetSpecificity()]

    bin_cropped = sitk.Cast(
        sitk.BinaryThreshold(prob_cropped, lowerThreshold=0.5, upperThreshold=1.0), sitk.sitkUInt8
    )

    ref = masks[0]
    size = ref.GetSize()  # (x, y, z)
    cons_arr = np.zeros((size[2], size[1], size[0]), dtype=np.uint8)
    cons_arr[z0 : z1 + 1, y0 : y1 + 1, x0 : x1 + 1] = sitk.GetArrayFromImage(bin_cropped)
    prob_arr = np.zeros((size[2], size[1], size[0]), dtype=np.float32)
    prob_arr[z0 : z1 + 1, y0 : y1 + 1, x0 : x1 + 1] = sitk.GetArrayFromImage(prob_cropped)

    sx, sy, sz = ref.GetSpacing()
    voxel_cc = (sx * sy * sz) / 1000.0
    volume_cc = float(int(cons_arr.sum()) * voxel_cc)

    # Mean binary entropy over P > 0.05 (AutoSeg's formula, recomputed).
    p64 = prob_arr.astype(np.float64)
    relevant = p64[p64 > 0.05]
    if relevant.size == 0:
        mean_entropy = 0.0
    else:
        p = np.clip(relevant, 1e-9, 1.0 - 1e-9)
        mean_entropy = float((-(p * np.log(p) + (1.0 - p) * np.log(1.0 - p))).mean())

    return {
        "sensitivities": sens,
        "specificities": spec,
        "consensus_arr": cons_arr,
        "volume_cc": volume_cc,
        "mean_entropy": mean_entropy,
    }


def sitk_defaults(
    masks: list[sitk.Image], padding: int, max_iterations: int | None = None
) -> dict[str, Any]:
    """SimpleITK's STAPLE on the same prepared input, with its own defaults.

    Only the foreground value is set, which the masks require; the iteration
    limit is left at SimpleITK's default, which is the largest unsigned integer,
    so the filter stops only when its estimates converge. ``max_iterations``
    sets a limit instead, to see what one would have changed.
    """
    x0, y0, z0, x1, y1, z1 = _union_bbox(masks, padding)
    cropped = [sitk.Cast(m[x0 : x1 + 1, y0 : y1 + 1, z0 : z1 + 1], sitk.sitkUInt8) for m in masks]
    f = sitk.STAPLEImageFilter()
    f.SetForegroundValue(1)
    if max_iterations is not None:
        f.SetMaximumIterations(int(max_iterations))
    prob = f.Execute(cropped)
    size = masks[0].GetSize()
    cons = np.zeros((size[2], size[1], size[0]), dtype=np.uint8)
    cons[z0 : z1 + 1, y0 : y1 + 1, x0 : x1 + 1] = sitk.GetArrayFromImage(
        sitk.BinaryThreshold(prob, lowerThreshold=0.5, upperThreshold=1.0)
    )
    return {
        "sensitivities": [float(s) for s in f.GetSensitivity()],
        "specificities": [float(s) for s in f.GetSpecificity()],
        "consensus_arr": cons,
        "iterations": int(f.GetElapsedIterations()),
        "max_iterations": int(f.GetMaximumIterations()),
    }


#: Set by ``main``: whether to run the crop comparison, which takes the whole
#: CT through STAPLE for every organ and so dominates the run time.
COMPARE_CROP = True
#: The fixed margin the crop comparison also tries, in voxels: AutoSeg's cap.
WIDE_PADDING = 25
#: AutoSeg's iteration cap before v3, raised to 500 when this validation found
#: STAPLE stopped short of convergence under it.
OLD_MAX_ITERATIONS = 100


def _consensus(masks: list[sitk.Image], box: tuple | None) -> tuple[np.ndarray, float]:
    """STAPLE's binary consensus over ``box`` (or the whole image), and seconds taken.

    SimpleITK's own defaults apart from the foreground value: STAPLE as anyone
    would run it without AutoSeg.
    """
    started = datetime.datetime.now()
    if box is None:
        inputs = [sitk.Cast(m, sitk.sitkUInt8) for m in masks]
    else:
        x0, y0, z0, x1, y1, z1 = box
        inputs = [
            sitk.Cast(m[x0 : x1 + 1, y0 : y1 + 1, z0 : z1 + 1], sitk.sitkUInt8) for m in masks
        ]
    f = sitk.STAPLEImageFilter()
    f.SetForegroundValue(1)
    inside = sitk.GetArrayFromImage(sitk.BinaryThreshold(f.Execute(inputs), 0.5, 1.0)) > 0
    seconds = (datetime.datetime.now() - started).total_seconds()
    if box is None:
        return inside, seconds
    size = masks[0].GetSize()
    full = np.zeros((size[2], size[1], size[0]), dtype=bool)
    full[z0 : z1 + 1, y0 : y1 + 1, x0 : x1 + 1] = inside
    return full, seconds


def _dice(a: np.ndarray, b: np.ndarray) -> float:
    total = int(a.sum()) + int(b.sum())
    return 1.0 if total == 0 else 2.0 * int(np.logical_and(a, b).sum()) / total


def crop_comparison(masks: list[sitk.Image], autoseg: np.ndarray, seconds: float) -> dict:
    """AutoSeg's cropped consensus against STAPLE over the whole image and a wide box."""
    whole, whole_seconds = _consensus(masks, None)
    wide, _ = _consensus(masks, _union_bbox(masks, WIDE_PADDING))
    voxels = int(autoseg.sum())
    return {
        "whole_voxels": int(whole.sum()),
        "whole_dice": _dice(whole, autoseg),
        "whole_volume_pct": (int(whole.sum()) - voxels) / voxels * 100 if voxels else math.nan,
        "wide_dice": _dice(wide, autoseg),
        "wide_is_whole": bool(np.array_equal(wide, whole)),
        "whole_seconds": whole_seconds,
        "crop_seconds": seconds,
    }


# ------------------------------- Comparison -------------------------------


def compare_one_organ(organ: str, masks: list[sitk.Image]) -> dict[str, Any] | None:
    """Run AutoSeg + reference STAPLE on one organ's rater masks; compare."""
    started = datetime.datetime.now()
    result = compute_staple(masks, CONFIG)
    seconds = (datetime.datetime.now() - started).total_seconds()
    if result is None:
        return None  # < 2 non-empty raters

    ref = reference_consensus(masks, result.bbox_padding_used)

    a_sens = list(result.sensitivities)
    a_spec = list(result.specificities)
    n = min(len(a_sens), len(ref["sensitivities"]))
    sens_dmax = max((abs(a_sens[i] - ref["sensitivities"][i]) for i in range(n)), default=0.0)
    spec_dmax = max((abs(a_spec[i] - ref["specificities"][i]) for i in range(n)), default=0.0)

    a_cons = sitk.GetArrayFromImage(result.consensus_mask).astype(bool)
    b_cons = ref["consensus_arr"].astype(bool)
    xor = int(np.sum(a_cons != b_cons)) if a_cons.shape == b_cons.shape else None

    vol_delta = abs(result.consensus_volume_cc - ref["volume_cc"])
    ent_delta = abs(result.mean_entropy - ref["mean_entropy"])

    exact = (
        xor == 0 and sens_dmax == 0.0 and spec_dmax == 0.0 and vol_delta == 0.0 and ent_delta < 1e-9
    )
    default = sitk_defaults(masks, result.bbox_padding_used)
    default_xor = int(np.sum(a_cons != default["consensus_arr"].astype(bool)))
    default_sens = max(abs(a - b) for a, b in zip(a_sens, default["sensitivities"], strict=True))
    default_spec = max(abs(a - b) for a, b in zip(a_spec, default["specificities"], strict=True))
    # The iteration cap AutoSeg had before v3, against STAPLE run to convergence.
    old = sitk_defaults(masks, result.bbox_padding_used, OLD_MAX_ITERATIONS)
    old_cap = {
        "stopped_early": default["iterations"] > OLD_MAX_ITERATIONS,
        "xor": int(np.sum(old["consensus_arr"] != default["consensus_arr"])),
        "sens_dmax": max(
            abs(a - b) for a, b in zip(old["sensitivities"], default["sensitivities"], strict=True)
        ),
        "spec_dmax": max(
            abs(a - b) for a, b in zip(old["specificities"], default["specificities"], strict=True)
        ),
    }
    crop = crop_comparison(masks, a_cons, seconds) if COMPARE_CROP else None
    return {
        "old_cap": old_cap,
        "crop": crop,
        "iterations": result.elapsed_iterations,
        "converged": result.converged,
        "default_iterations": default["iterations"],
        "default_xor": default_xor,
        "default_sens_dmax": default_sens,
        "default_spec_dmax": default_spec,
        "default_same": default_xor == 0 and default_sens == 0.0 and default_spec == 0.0,
        "organ": organ,
        "n_raters": result.n_raters,
        "sens_dmax": sens_dmax,
        "spec_dmax": spec_dmax,
        "autoseg_voxels": int(a_cons.sum()),
        "xor": xor,
        "volume_cc": result.consensus_volume_cc,
        "volume_delta": vol_delta,
        "entropy": result.mean_entropy,
        "entropy_delta": ent_delta,
        "padding": result.bbox_padding_used,
        "fg_ratio": result.bbox_fg_ratio,
        "result": "EXACT" if exact else "differs",
    }


# ------------------------------ Reports -----------------------------------

SCRIPT = Path(__file__).name
TITLE = "Supplementary 5 - STAPLE Implementation"
REPORT = VALIDATION_DOCS / "Supplementary_5_STAPLE_Implementation.md"
FULL_RESULTS = REPORT.with_name(REPORT.stem + "_Full_Results.md")
#: The name this script's results are cached under (``validation_common``).
RESULTS_NAME = "staple"


def _fmt_delta(delta: float, *, eps: float = 0.0) -> str:
    if delta <= eps:
        return "**0**"
    return f"{delta:.3e}"


def _crop_numbers(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """What the crop changes, summed up over the organs; None if it was not run."""
    crops = [(r, r["crop"]) for r in rows if r.get("crop")]
    if not crops:
        return None
    whole_s = [c["whole_seconds"] for _r, c in crops]
    crop_s = [c["crop_seconds"] for _r, c in crops]
    differ = sorted(
        ((r, c) for r, c in crops if c["whole_dice"] < 1.0), key=lambda rc: rc[1]["whole_dice"]
    )
    return {
        "n": len(crops),
        # Dice is 1 exactly when the two consensus masks are the same voxels.
        "same": sum(1 for _r, c in crops if c["whole_dice"] == 1.0),
        "differ": differ,
        "empty_whole": sum(1 for r, c in crops if c["whole_voxels"] == 0 and r["autoseg_voxels"]),
        "empty_crop": sum(1 for r, c in crops if r["autoseg_voxels"] == 0 and c["whole_voxels"]),
        "wide_same": sum(1 for _r, c in crops if c["wide_is_whole"]),
        "paddings": sorted({r["padding"] for r, _c in crops}),
        "crop_median": float(np.median(crop_s)),
        "whole_median": float(np.median(whole_s)),
        "crop_max": max(crop_s),
        "whole_max": max(whole_s),
    }


def _old_cap_numbers(rows: list[dict[str, Any]]) -> dict[str, Any]:
    early = [r for r in rows if r["old_cap"]["stopped_early"]]
    return {
        "early": early,
        "xor": sum(r["old_cap"]["xor"] for r in early),
        "estimates": max(
            (max(r["old_cap"]["sens_dmax"], r["old_cap"]["spec_dmax"]) for r in early),
            default=0.0,
        ),
    }


def _organs(rows: list[dict[str, Any]]) -> list[str]:
    return [f"{r['organ']} ({r['default_iterations']})" for r in rows]


def write_report(target: Path, data: dict[str, Any]) -> None:
    """Supplementary 5: what was validated, how, what was found, and what it decided."""
    rows, config = data["rows"], data["config"]
    n = len(rows)
    n_exact = sum(1 for r in rows if r["result"] == "EXACT")
    n_default = sum(1 for r in rows if r["default_same"])
    raters = [r["n_raters"] for r in rows]
    iterations = [r["default_iterations"] for r in rows]
    capped = sum(1 for r in rows if not r["converged"])
    old = _old_cap_numbers(rows)
    crop = _crop_numbers(rows)
    (nx, ny, nz), (sx, sy, sz) = data["ct"]["size"], data["ct"]["spacing"]
    versions = data["versions"]

    lines = [
        f"# {TITLE}",
        "",
        f"{stamp(SCRIPT, data['run'])}. Every table behind this report is in its "
        f"[full results]({FULL_RESULTS.name}).",
        "",
        "## Aim",
        "",
        "When several observers contour the same organ, AutoSeg Evaluator can combine their",
        "contours into a consensus with STAPLE (Simultaneous Truth and Performance Level",
        "Estimation) [1]. STAPLE estimates how reliably each observer, or rater, includes the",
        "organ's voxels (sensitivity) and leaves out the rest (specificity), and from these",
        "the probability that each voxel belongs to the organ. AutoSeg runs SimpleITK's",
        "implementation of STAPLE [2] and adds three steps around it:",
        "",
        "1. it crops the raters' masks to the box enclosing all of them, with a small margin;",
        "2. it thresholds STAPLE's probability map at 0.5 to give a binary consensus;",
        "3. it pads the consensus back to the full image.",
        "",
        f"It also stops STAPLE after at most {config['max_iterations']} iterations. This",
        "validation asks three questions:",
        "",
        "1. Is AutoSeg's consensus exactly SimpleITK's STAPLE on the same prepared input, so",
        "   that the added steps are implemented as described?",
        "2. Does the iteration cap ever stop STAPLE before it converges?",
        "3. What does the crop change, compared with running STAPLE over the whole image, and",
        "   is it justified?",
        "",
        "## Method",
        "",
        "**Data.** An anonymised head-and-neck case (Human Research Ethics approval RGS4979):",
        f"a CT of {nx} × {ny} × {nz} voxels of {sx:.3f} × {sy:.3f} × {sz:.3f} mm, with",
        f"{data['n_rtss']} structure sets from several auto-contouring systems and manual",
        "observers. Every organ contoured in at least two structure sets was used:",
        f"{n} organs, with {min(raters)}-{max(raters)} raters each ({sum(raters)} rater",
        "contours). Each rater's contours were converted to a binary mask by AutoSeg's",
        "rasteriser (Supplementary 2). Only organ names and numbers appear in this report.",
        "",
        "**Comparisons.** For each organ:",
        "",
        "1. *AutoSeg against an independent reconstruction.* The validation script rebuilt the",
        "   consensus from SimpleITK alone: the same crop, with the margin AutoSeg chose; a new",
        "   STAPLE filter with AutoSeg's settings; the 0.5 threshold; and the pad-back.",
        "   Compared: each rater's sensitivity and specificity, every consensus voxel, and the",
        "   consensus volume and mean entropy.",
        "2. *AutoSeg against SimpleITK's own defaults.* SimpleITK's STAPLE was run on the same",
        "   cropped input with no iteration limit, its default, so that it stopped only when",
        "   its estimates converged. Compared: each rater's sensitivity and specificity, every",
        "   consensus voxel, and the iterations taken. This was repeated with the cap of",
        f"   {OLD_MAX_ITERATIONS} iterations that AutoSeg used before version 3.",
    ]
    if crop:
        lines += [
            "3. *Cropped against the whole image.* STAPLE was run on SimpleITK's defaults over",
            f"   the whole CT, and over the box widened by {WIDE_PADDING} voxels. Each consensus",
            "   was compared with AutoSeg's by Dice and volume, and the time taken recorded.",
        ]
    lines += [
        "",
        "**Criterion.** The first two comparisons require exact equality, with no tolerance."
        + (" The third measures the difference." if crop else ""),
        "",
        f"**Settings.** Iteration cap {config['max_iterations']}; confidence weight"
        f" {config['confidence_weight']:g}, SimpleITK's default; a crop margin of at least",
        f"{config['bbox_padding_min_voxels']} voxels, widened one voxel at a time until the",
        f"organ fills at most {100 * config['target_fg_ratio_max']:.0f} % of the box, up to",
        f"{config['bbox_padding_max_voxels']} voxels.",
        "",
        f"**Software.** SimpleITK {versions['SimpleITK']}, NumPy {versions['numpy']}, pydicom"
        f" {versions['pydicom']}.",
        "",
        "## Results",
        "",
        *md_table(
            ["Comparison", "Organs", "Identical"],
            [
                ["AutoSeg against the independent reconstruction", f"{n}", f"{n_exact}"],
                ["AutoSeg against SimpleITK's own defaults", f"{n}", f"{n_default}"],
            ],
            "lrr",
        ),
        "Identical means every rater's sensitivity and specificity and every consensus voxel",
        "were equal, bit for bit; against the reconstruction, the consensus volume and mean",
        "entropy were too.",
        "",
        f"Run to convergence, STAPLE took {min(iterations)}-{max(iterations)} iterations"
        f" (median {np.median(iterations):.0f})",
    ]
    lines[-1] += (
        f", within AutoSeg's cap of {config['max_iterations']} for every organ."
        if not capped
        else f"; {capped} organs reached AutoSeg's cap of {config['max_iterations']}."
    )
    if old["early"]:
        lines += [
            f"Under the earlier cap of {OLD_MAX_ITERATIONS}, {len(old['early'])} organs stopped",
            "before converging: " + ", ".join(_organs(old["early"])) + ", with the iterations",
            "each needs in brackets. Stopping early "
            + (
                "left their consensus unchanged"
                if old["xor"] == 0
                else f"changed their consensus by {old['xor']:,} voxels"
            )
            + " and moved their sensitivities and specificities by at most"
            f" {sci(old['estimates'])}.",
        ]
    else:
        lines += [f"No organ needed more than the earlier cap of {OLD_MAX_ITERATIONS}."]
    lines += [""]
    if crop:
        lines += [
            f"Over the whole image, STAPLE gave the same consensus as AutoSeg's crop for"
            f" {crop['same']} of {crop['n']} organs."
            + (f" The other {len(crop['differ'])}:" if crop["differ"] else ""),
            "",
        ]
        if crop["differ"]:
            lines += md_table(
                ["Organ", "Raters", "Whole-image volume against cropped", "Dice"],
                [
                    [
                        r["organ"],
                        f"{r['n_raters']}",
                        f"{c['whole_volume_pct']:+.1f} %",
                        f"{c['whole_dice']:.4f}",
                    ]
                    for r, c in crop["differ"]
                ],
                "lrrr",
            )
        empties = crop["empty_whole"] + crop["empty_crop"]
        lines += [
            (
                "No organ was left empty either way."
                if not empties
                else f"Whole-image STAPLE left {crop['empty_whole']} organs empty, and the crop"
                f" {crop['empty_crop']}."
            ),
            f"A box widened by {WIDE_PADDING} voxels gave the whole image's consensus, voxel for",
            f"voxel, for {crop['wide_same']} of {crop['n']} organs. The margin AutoSeg chose was"
            f" {' or '.join(str(p) for p in crop['paddings'])} voxels. Median time per organ:"
            f" {crop['crop_median']:.2f} s cropped and {crop['whole_median']:.1f} s over the"
            " whole image.",
            "",
        ]

    lines += ["## Findings", ""]
    if n_exact == n_default == n:
        lines += [
            "- **AutoSeg's STAPLE is SimpleITK's STAPLE.** Its consensus matched an independent",
            f"  reconstruction bit for bit on all {n} organs, so the crop, the threshold and the",
            "  pad-back are implemented as described, and the estimates are SimpleITK's own.",
        ]
    else:
        lines += [
            f"- **{n - n_exact} organs differ from the reconstruction and {n - n_default} from",
            "  SimpleITK's defaults;** the full results list them.",
        ]
    if not capped:
        lines += [
            f"- **STAPLE runs to convergence.** No organ reached the cap of"
            f" {config['max_iterations']} (the most",
            f"  any needed was {max(iterations)}), so every consensus is the one SimpleITK gives",
            "  with no limit.",
        ]
        if old["early"]:
            lines[-1] += (
                f" The earlier cap of {OLD_MAX_ITERATIONS} stopped {len(old['early'])} organs"
                " short of convergence"
                + (", without changing their consensus" if old["xor"] == 0 else "")
                + f"; version 3 raised it to {config['max_iterations']}, the largest the Compute"
                " tab allows, so that STAPLE's estimates are always its converged ones."
            )
    if crop:
        larger = [c["whole_volume_pct"] for _r, c in crop["differ"]]
        all_larger = bool(larger) and all(v > 0 for v in larger)
        min_dice = min((c["whole_dice"] for _r, c in crop["differ"]), default=1.0)
        speed = crop["whole_median"] / crop["crop_median"]
        lines += [
            "- **The crop changes the consensus slightly, and is kept deliberately.** STAPLE",
            "  estimates each rater's specificity, and the prior probability that a voxel belongs",
            "  to the organ, from every voxel it is given. Over a whole CT, both are dominated by",
            "  background that every rater agrees on, so they depend on how much air and body the",
            "  scan happens to include, which has nothing to do with the contours. Estimating",
            "  only in the organ's neighbourhood, where the raters' contours actually differ,",
            "  makes the consensus depend on the contours. Other approaches restrict STAPLE's",
            "  estimation differently, for example by ignoring the voxels on which all raters",
            f"  agree [3]. Here the cost was small: {crop['same']} of {crop['n']} organs were",
            "  identical",
        ]
        if crop["differ"]:
            lines[-1] += (
                f", and the other {len(crop['differ'])} were"
                + (" larger" if all_larger else " different")
                + f" over the whole image by {min(abs(v) for v in larger):.1f}-"
                f"{max(abs(v) for v in larger):.1f} % in volume (Dice at least {min_dice:.2f})"
            )
        lines[-1] += "."
        lines += [
            f"  The crop also makes STAPLE about {speed:.0f} times faster.",
        ]
        tight = all(r["padding"] == config["bbox_padding_min_voxels"] for r in rows) and all(
            r["fg_ratio"] <= config["target_fg_ratio_max"] for r in rows
        )
        lines += [
            "- **The consensus depends on how much background the box holds.** A margin of",
            f"  {WIDE_PADDING} voxels already gave the whole image's consensus for"
            f" {crop['wide_same']} of {crop['n']} organs.",
        ]
        if tight:
            lines[-1] += (
                f" AutoSeg's margin stayed at its {config['bbox_padding_min_voxels']}-voxel"
                " minimum on every organ, because each already filled less than"
                f" {config['target_fg_ratio_max']:.0%} of its box, so its consensus is STAPLE"
                " estimated in the organ's immediate neighbourhood."
            )
        lines[-1] += (
            " The specificities AutoSeg reports are therefore relative to that neighbourhood,"
            " not to the whole scan."
        )
    lines += [
        f"- **Limits.** One case, with {min(raters)}-{max(raters)} raters per organ. The",
        "  comparisons test the implementation and the effect of its two settings, not how",
        "  close any consensus is to the true anatomy.",
        "",
        "## References",
        "",
        "1. Warfield SK, Zou KH, Wells WM. Simultaneous truth and performance level estimation",
        "   (STAPLE): an algorithm for the validation of image segmentation. IEEE Trans Med",
        "   Imaging. 2004;23(7):903-921.",
        "2. Lowekamp BC, Chen DT, Ibáñez L, Blezek D. The design of SimpleITK. Front",
        "   Neuroinform. 2013;7:45.",
        "3. Xu Z, Asman AJ, Landman BA. Generalized statistical label fusion using multiple",
        "   consensus levels. Proc SPIE Int Soc Opt Eng. 2012;8314:831411. doi:10.1117/12.910918",
        "",
        "## Reproduce",
        "",
        "```",
        f"python scripts/{SCRIPT} --data <folder of CT and RTSTRUCT files>",
        "```",
        "",
        "Point it at any folder holding a CT and two or more structure sets. The run took",
        f"{data['seconds'] / 60:.0f} minutes, most of it the whole-image comparison, which",
        "`--no-crop-comparison` skips; `--render-only` rewrites this report from the last",
        "run's results. The exit code is 0 only if every comparison was exact. Regenerate this",
        "report rather than editing it.",
        "",
    ]
    target.write_text(unwrap(lines), encoding="utf-8")


def _crop_section(rows: list[dict[str, Any]]) -> list[str]:
    """What the crop changes: AutoSeg against STAPLE over the whole image."""
    crop = _crop_numbers(rows)
    if not crop:
        return []
    lines = [
        "## What the crop changes",
        "",
        "STAPLE estimates each rater's specificity, and the prior probability of foreground,",
        "from every voxel it is given. Over a whole CT those are dominated by background every",
        "rater agrees on, and so depend on the scan's field of view. AutoSeg estimates within",
        "the raters' union bounding box instead. This compares its consensus with STAPLE run,",
        "on SimpleITK's own defaults, over the whole image and over the union box widened by",
        f"{WIDE_PADDING} voxels.",
        "",
        f"- **Same consensus as the whole image:** {crop['same']} / {crop['n']} organs, voxel"
        " for voxel.",
        "- **Whole-image STAPLE left an organ empty that the crop did not:**"
        f" {crop['empty_whole']} / {crop['n']}; the crop one the whole image did not:"
        f" {crop['empty_crop']} / {crop['n']}.",
        f"- **Padding AutoSeg chose:** {', '.join(str(p) for p in crop['paddings'])} voxels.",
        f"- **A {WIDE_PADDING}-voxel box gives the whole image's consensus, voxel for voxel:**"
        f" {crop['wide_same']} / {crop['n']} organs.",
        f"- **Time per organ:** median {crop['crop_median']:.2f} s cropped,"
        f" {crop['whole_median']:.1f} s over the whole image (largest {crop['crop_max']:.1f} s"
        f" and {crop['whole_max']:.1f} s).",
        "",
    ]
    if crop["differ"]:
        lines += ["Organs whose consensus differs over the whole image:", ""]
        lines += md_table(
            ["Organ", "Raters", "AutoSeg voxels", "Whole-image voxels", "Volume", "Dice"],
            [
                [
                    f"`{r['organ']}`",
                    f"{r['n_raters']}",
                    f"{r['autoseg_voxels']:,}",
                    f"{c['whole_voxels']:,}",
                    f"{c['whole_volume_pct']:+.1f} %",
                    f"{c['whole_dice']:.4f}",
                ]
                for r, c in crop["differ"]
            ],
            "lrrrrr",
        )
    return lines


def write_full_results(target: Path, data: dict[str, Any]) -> None:
    """Every table behind Supplementary 5."""
    rows, config, versions = data["rows"], data["config"], data["versions"]
    n_total = len(rows)
    n_exact = sum(1 for r in rows if r["result"] == "EXACT")
    n_default = sum(1 for r in rows if r["default_same"])
    n_converged = sum(1 for r in rows if r["converged"])
    total_raters = sum(r["n_raters"] for r in rows)
    iterations = [r["iterations"] for r in rows]
    default_iterations = [r["default_iterations"] for r in rows]
    old = _old_cap_numbers(rows)
    (nx, ny, nz), (sx, sy, sz) = data["ct"]["size"], data["ct"]["spacing"]

    lines = [
        f"# {TITLE}: full results",
        "",
        f"{stamp(SCRIPT, data['run'])}. The summary is [Supplementary 5]({REPORT.name}).",
        "Regenerate this file rather than editing it.",
        "",
        "**Privacy:** this file contains no PHI. ROI display names (organ names) are shown; no",
        "SOPInstanceUIDs, filenames, patient identifiers, dates, or institution metadata are",
        "included.",
        "",
        "## What is validated",
        "",
        "AutoSeg's `compute_staple` wraps `SimpleITK.STAPLEImageFilter` and adds three steps:",
        "(1) a union bounding-box crop, so STAPLE estimates in the structure's neighbourhood",
        "rather than over the scan's field of view (its effect is measured under *What the",
        "crop changes*), (2) a P >= 0.5 threshold of the probabilistic truth into a binary",
        "consensus, and (3) a pad-back to the original image extent. The validator",
        "reconstructs the same pipeline **independently**, using only NumPy geometry and a",
        "fresh `SimpleITK.STAPLEImageFilter` call, and compares:",
        "",
        "- **Per-rater sensitivity & specificity**, the EM performance parameters that",
        "  constitute the STAPLE algorithm (Warfield 2004, Eqs. 7-8). Compared bit-for-bit.",
        "- **Binary consensus mask**, compared voxel-for-voxel (XOR count).",
        "- **Consensus volume (cc)** and **mean binary entropy**, AutoSeg's derived uncertainty",
        "  summaries, recomputed independently and compared.",
        "",
        "The only quantity carried across from AutoSeg is the single integer bounding-box",
        "padding it selected (a reported diagnostic), so that both paths feed the upstream",
        "filter the *identical* prepared input. The STAPLE estimator, threshold, and summaries",
        "are all computed afresh on the reference side.",
        "",
        "The reconstruction is given AutoSeg's iteration cap, so it checks the wrapper, not the",
        "cap. A second comparison runs SimpleITK's filter on the same input with **its own",
        "defaults**: no practical iteration limit, so it stops only when its estimates",
        "converge, which is STAPLE as published. Agreement there shows that AutoSeg's cap",
        "leaves STAPLE to converge as SimpleITK would. The same filter capped at",
        f"{OLD_MAX_ITERATIONS} iterations, AutoSeg's default before v3, shows what that cap",
        "changed.",
        "",
        "## Software environment",
        "",
        *md_table(
            ["Component", "Version"],
            [[k, f"`{v}`"] for k, v in versions.items()],
            "ll",
        ),
        "## Validation input",
        "",
        f"- **CT volume:** {nx} x {ny} x {nz} voxels, spacing {sx:.3f} x {sy:.3f} x {sz:.3f} mm",
        f"- **RTSTRUCT files (raters) loaded:** {data['n_rtss']}",
        f"- **Organs contoured by 2+ raters and validated:** {n_total} (spanning"
        f" {total_raters} rater contours in total)",
        f"- **STAPLE parameters:** max_iterations = {config['max_iterations']},"
        f" confidence_weight = {config['confidence_weight']}, adaptive bbox upper"
        f" foreground-ratio target = {config['target_fg_ratio_max']}, padding"
        f" {config['bbox_padding_min_voxels']}-{config['bbox_padding_max_voxels']} voxels",
        "",
        "The validation cohort is a single anonymised head-and-neck patient (HN1) used for",
        "development testing, contoured by several independent auto-segmentation systems and",
        "manual observers, each treated as a STAPLE rater. STAPLE requires 2+ raters; each",
        "organ below was contoured by the stated number of them.",
        "",
        "## STAPLE parity vs `SimpleITK.STAPLEImageFilter`",
        "",
        f"**Headline result:** **{n_exact} / {n_total} consensus computations were bit-for-bit",
        "identical**: every per-rater sensitivity and specificity matched to zero absolute",
        "difference, every binary consensus mask was voxel-identical (XOR = 0), and consensus",
        "volume and mean entropy reproduced to within floating-point round-off.",
        "",
        f"**Against SimpleITK's own defaults:** {n_default} / {n_total} identical in every",
        "per-rater sensitivity and specificity and every consensus voxel. STAPLE converged",
        f"within AutoSeg's cap of {config['max_iterations']} iterations on {n_converged} /",
        f"{n_total} organs, taking {min(iterations)}-{max(iterations)} iterations; SimpleITK on",
        f"its own took {min(default_iterations)}-{max(default_iterations)}.",
        "",
        f"**Capped at {OLD_MAX_ITERATIONS} iterations:** {len(old['early'])} / {n_total} organs"
        " stop before converging"
        + (
            f" ({', '.join(_organs(old['early']))}, iterations needed in brackets); their"
            f" consensus differs from the converged one by {old['xor']:,} voxels and their"
            f" estimates by up to {old['estimates']:.3e}."
            if old["early"]
            else "."
        ),
        "",
    ]
    if n_default < n_total:
        lines += ["Organs that differ from SimpleITK's own defaults:", ""]
        lines += [
            f"- `{r['organ']}`: {r['iterations']} iterations against SimpleITK's "
            f"{r['default_iterations']}; consensus XOR {r['default_xor']:,}, "
            f"sens |Δ| {r['default_sens_dmax']:.3e}, spec |Δ| {r['default_spec_dmax']:.3e}"
            for r in rows
            if not r["default_same"]
        ]
        lines += [""]
    lines += [
        "### Per-organ results",
        "",
        "`sens |Δ|` / `spec |Δ|` are the maximum absolute differences across the organ's",
        "raters; `XOR` is the voxel-disagreement count of the binary consensus. `**0**`",
        "denotes exact equality. *Iterations* are AutoSeg's / SimpleITK's with its own",
        "defaults; *Defaults* says whether AutoSeg's result equals the latter exactly;",
        f"*Cap {OLD_MAX_ITERATIONS}* gives the consensus voxels that cap changes, where it stops",
        "STAPLE early.",
        "",
        *md_table(
            [
                "Organ",
                "Raters",
                "sens \\|Δ\\|",
                "spec \\|Δ\\|",
                "Consensus voxels",
                "XOR",
                "Volume (cc)",
                "Vol \\|Δ\\|",
                "Entropy \\|Δ\\|",
                "Match",
                "Iterations",
                "Defaults",
                f"Cap {OLD_MAX_ITERATIONS}",
                "Padding",
            ],
            [
                [
                    f"`{r['organ']}`",
                    f"{r['n_raters']}",
                    _fmt_delta(r["sens_dmax"]),
                    _fmt_delta(r["spec_dmax"]),
                    f"{r['autoseg_voxels']:,}",
                    "—" if r["xor"] is None else f"{r['xor']:,}",
                    f"{r['volume_cc']:.4f}",
                    _fmt_delta(r["volume_delta"]),
                    _fmt_delta(r["entropy_delta"], eps=1e-9),
                    "✅" if r["result"] == "EXACT" else r["result"],
                    f"{r['iterations']} / {r['default_iterations']}",
                    "✅" if r["default_same"] else "differs",
                    f"{r['old_cap']['xor']:,}" if r["old_cap"]["stopped_early"] else "converged",
                    f"{r['padding']}",
                ]
                for r in sorted(rows, key=lambda x: (-x["n_raters"], x["organ"]))
            ],
            "lrrrrrrrrcrcrr".replace("c", "l"),
        ),
        *_crop_section(rows),
        "## Reproducing this report",
        "",
        "```",
        f"python scripts/{SCRIPT} --data /path/to/DICOM/folder",
        "```",
        "",
        "The script reads every `.dcm` in the folder, identifies the CT series and the",
        "RTSTRUCTs, groups ROIs by clinical name, and runs the comparison for every organ",
        "contoured by 2+ raters. Exit code is 0 only if every comparison was exact. The",
        "equivalence is also locked in CI via `tests/test_staple_equivalence.py`.",
        "",
        "## References",
        "",
        "1. **STAPLE**: Warfield SK, Zou KH, Wells WM. *Simultaneous truth and performance level",
        "   estimation (STAPLE): an algorithm for the validation of image segmentation.* IEEE",
        "   Transactions on Medical Imaging, 23(7):903-921, 2004.",
        "2. **SimpleITK**: Lowekamp BC, Chen DT, Ibáñez L, Blezek D. *The Design of SimpleITK.*",
        "   Frontiers in Neuroinformatics, 7:45, 2013. `STAPLEImageFilter` wraps the ITK",
        "   implementation of the algorithm above.",
        "",
    ]
    target.write_text(unwrap(lines), encoding="utf-8")


def render(data: dict[str, Any], report: Path = REPORT, full: Path = FULL_RESULTS) -> None:
    report.parent.mkdir(parents=True, exist_ok=True)
    write_report(report, data)
    write_full_results(full, data)
    print(f"wrote {report} and {full.name}")


# ---------------------------------- Main ----------------------------------


def _load_rows(folder: Path, max_organs: int) -> tuple[sitk.Image, int, list[dict[str, Any]]]:
    """Every organ contoured by 2+ raters in ``folder``, compared."""
    print(f"Loading CT series from {folder} …")
    image = read_dicom_image(str(folder))
    print(f"  CT: size {image.GetSize()}, spacing {tuple(round(s, 3) for s in image.GetSpacing())}")

    # Discover RTSS files by SOP class / modality (not by filename).
    rtss_datasets: list[Any] = []
    for path in sorted(folder.glob("*.dcm")):
        try:
            ds = read_rtstruct(str(path))
        except Exception:  # noqa: BLE001 — skip non-RTSS / unreadable
            continue
        if str(getattr(ds, "Modality", "")) == "RTSTRUCT" or hasattr(ds, "StructureSetROISequence"):
            rtss_datasets.append(ds)
    print(f"  {len(rtss_datasets)} RTSTRUCT files loaded.")

    # Group ROI occurrences by normalised clinical name.
    name_index: dict[str, list[tuple[Any, int]]] = {}
    for ds in rtss_datasets:
        for struct in getattr(ds, "StructureSetROISequence", []):
            name_index.setdefault(_normalise_name(struct.ROIName), []).append(
                (ds, int(struct.ROINumber))
            )

    shared = sorted(name for name, owners in name_index.items() if len(owners) >= 2)
    if max_organs > 0:
        # Keep the most-rated organs first so a capped run is still meaningful.
        shared = sorted(shared, key=lambda n: -len(name_index[n]))[:max_organs]
    print(f"Validating STAPLE on {len(shared)} organs contoured by 2+ raters …")

    rows: list[dict[str, Any]] = []
    for name in shared:
        masks: list[sitk.Image] = []
        for ds, roi_number in name_index[name]:
            m = extract_mask_for_roi(image, ds, roi_number)
            if m is not None and int(sitk.GetArrayViewFromImage(m).sum()) > 0:
                masks.append(m)
        if len(masks) < 2:
            continue
        row = compare_one_organ(name, masks)
        if row is None:
            continue
        rows.append(row)
        flag = "OK " if row["result"] == "EXACT" else "DIFF"
        print(f"  [{flag}] {name:24s} raters={row['n_raters']} xor={row['xor']}")
    return image, len(rtss_datasets), rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, help="Folder of CT + RTSS files")
    parser.add_argument("--max-organs", type=int, default=0, help="Cap organs validated (0 = all)")
    parser.add_argument(
        "--no-crop-comparison",
        action="store_true",
        help="Skip STAPLE over the whole image, which takes most of the run time.",
    )
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="Rewrite the reports from the last run's results, without computing.",
    )
    args = parser.parse_args()

    if args.render_only:
        data = load_results(RESULTS_NAME)
    else:
        global COMPARE_CROP
        COMPARE_CROP = not args.no_crop_comparison
        folder: Path | None = args.data
        if folder is None or not folder.is_dir():
            print(f"Data folder does not exist: {folder}", file=sys.stderr)
            return 2
        started = datetime.datetime.now()
        info = run_info()
        image, n_rtss, rows = _load_rows(folder, args.max_organs)
        if n_rtss < 2:
            print("Need at least 2 RTSTRUCT files for a multi-rater STAPLE.", file=sys.stderr)
            return 2
        if not rows:
            print("No organ had 2+ non-empty rater masks — nothing to validate.", file=sys.stderr)
            return 2
        data = {
            "run": info,
            "seconds": (datetime.datetime.now() - started).total_seconds(),
            "ct": {"size": list(image.GetSize()), "spacing": list(image.GetSpacing())},
            "n_rtss": n_rtss,
            "rows": rows,
            "config": dataclasses.asdict(CONFIG),
            "versions": {
                "AutoSeg Evaluator": f"{info['version']}, {info['revision']}",
                "Python": platform.python_version(),
                "Operating system": f"{platform.system()} {platform.release()}",
                "SimpleITK": importlib.metadata.version("SimpleITK"),
                "pydicom": importlib.metadata.version("pydicom"),
                "numpy": importlib.metadata.version("numpy"),
            },
        }
        print(f"kept the results in {save_results(RESULTS_NAME, data)}")
    render(data)

    rows = data["rows"]
    n_exact = sum(1 for r in rows if r["result"] == "EXACT")
    n_default = sum(1 for r in rows if r["default_same"])
    print(f"{n_exact}/{len(rows)} organs bit-exact; {n_default} equal SimpleITK's own defaults.")
    return 0 if n_exact == n_default == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
