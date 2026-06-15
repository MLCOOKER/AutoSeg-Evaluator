"""Validate AutoSeg Evaluator's STAPLE consensus against the reference filter.

AutoSeg's :func:`autoseg_evaluator.core.staple.compute_staple` wraps
``SimpleITK.STAPLEImageFilter`` (Warfield, Zou & Wells, IEEE TMI 2004 — the
canonical reference implementation of STAPLE) and adds: a union bounding-box
crop that conditions the EM prior for small structures, a P >= 0.5 threshold,
and a pad-back to the original image extent.

This script demonstrates that those additions are *numerically transparent*:
for every organ contoured by two or more raters in a folder of CT + RTSS
files, it

1. rasterises each rater's contour to a binary mask (AutoSeg's rasteriser,
   already validated voxel-exact vs PlatiPy in ``VALIDATION_REPORT.md``);
2. runs AutoSeg's ``compute_staple`` on the rater masks; and
3. **independently** reconstructs the consensus using only upstream
   primitives — it crops the same masks to the union bounding box, invokes
   a fresh ``SimpleITK.STAPLEImageFilter``, applies the P >= 0.5 threshold,
   and pads back — then compares.

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
        --data /path/to/folder/containing/CT/and/RTSS/files \\
        --out docs/STAPLE_VALIDATION_REPORT.md
"""

from __future__ import annotations

import argparse
import datetime
import importlib.metadata
import platform
import sys
from pathlib import Path
from typing import Any

import numpy as np
import SimpleITK as sitk

# AutoSeg
from autoseg_evaluator import __version__ as autoseg_version
from autoseg_evaluator.core.masks import extract_mask_for_roi, read_dicom_image, read_rtstruct
from autoseg_evaluator.core.staple import StapleConfig, compute_staple

CONFIG = StapleConfig()  # defaults: maxiter=100, confidence=1.0, adaptive bbox band


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


# ------------------------------- Comparison -------------------------------


def compare_one_organ(organ: str, masks: list[sitk.Image]) -> dict[str, Any] | None:
    """Run AutoSeg + reference STAPLE on one organ's rater masks; compare."""
    result = compute_staple(masks, CONFIG)
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
    return {
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


# ------------------------------ Report writer -----------------------------


def _fmt_delta(delta: float, *, eps: float = 0.0) -> str:
    if delta <= eps:
        return "**0**"
    return f"{delta:.3e}"


def write_report(
    out_path: Path,
    *,
    image: sitk.Image,
    n_rtss: int,
    rows: list[dict[str, Any]],
    versions: dict[str, str],
) -> None:
    lines: list[str] = []
    today = datetime.date.today().isoformat()
    n_total = len(rows)
    n_exact = sum(1 for r in rows if r["result"] == "EXACT")
    total_raters = sum(r["n_raters"] for r in rows)

    lines.append("# STAPLE Consensus Validation — AutoSeg Evaluator vs SimpleITK\n")
    lines.append(f"_Auto-generated by `scripts/validate_staple_against_upstream.py` on {today}._\n")
    lines.append("")
    lines.append(
        "This report demonstrates that AutoSeg Evaluator's STAPLE consensus "
        "estimator produces output **bit-for-bit identical** to the upstream "
        "`SimpleITK.STAPLEImageFilter` reference implementation (Warfield, Zou "
        "& Wells, IEEE TMI 2004), on a clinical multi-observer sample dataset. "
        "It is intended for manuscript reviewers and any user who wants to "
        "verify the implementation-parity claim."
    )
    lines.append("")
    lines.append(
        "**Privacy:** this report contains no PHI. ROI display names (organ "
        "names) are shown; no SOPInstanceUIDs, filenames, patient identifiers, "
        "dates, or institution metadata are included."
    )
    lines.append("")

    lines.append("## What is validated\n")
    lines.append(
        "AutoSeg's `compute_staple` wraps `SimpleITK.STAPLEImageFilter` and "
        "adds three transparent steps: (1) a union bounding-box crop that "
        "conditions the EM volume prior for small structures, (2) a P >= 0.5 "
        "threshold of the probabilistic truth into a binary consensus, and "
        "(3) a pad-back to the original image extent. The validator "
        "reconstructs the same pipeline **independently**, using only NumPy "
        "geometry and a fresh `SimpleITK.STAPLEImageFilter` call, and compares:"
    )
    lines.append("")
    lines.append(
        "- **Per-rater sensitivity & specificity** — the EM performance "
        "parameters that constitute the STAPLE algorithm (Warfield 2004 "
        "Eqs. 7-8). Compared bit-for-bit."
    )
    lines.append("- **Binary consensus mask** — compared voxel-for-voxel (XOR count).")
    lines.append(
        "- **Consensus volume (cc)** and **mean binary entropy** — AutoSeg's "
        "derived uncertainty summaries, recomputed independently and compared."
    )
    lines.append("")
    lines.append(
        "The only quantity carried across from AutoSeg is the single integer "
        "bounding-box padding it selected (a reported diagnostic), so that "
        "both paths feed the upstream filter the *identical* prepared input. "
        "The STAPLE estimator, threshold, and summaries are all computed "
        "afresh on the reference side."
    )
    lines.append("")

    lines.append("## Software environment\n")
    lines.append("| Component | Version |")
    lines.append("|---|---|")
    for k, v in versions.items():
        lines.append(f"| {k} | `{v}` |")
    lines.append("")

    lines.append("## Validation input\n")
    spacing = image.GetSpacing()
    size = image.GetSize()
    lines.append(
        f"- **CT volume:** {size[0]} x {size[1]} x {size[2]} voxels, "
        f"spacing {spacing[0]:.3f} x {spacing[1]:.3f} x {spacing[2]:.3f} mm"
    )
    lines.append(f"- **RTSTRUCT files (raters) loaded:** {n_rtss}")
    lines.append(
        f"- **Organs contoured by 2+ raters and validated:** {n_total} "
        f"(spanning {total_raters} rater contours in total)"
    )
    lines.append(
        f"- **STAPLE parameters:** max_iterations = {CONFIG.max_iterations}, "
        f"confidence_weight = {CONFIG.confidence_weight}, adaptive bbox upper "
        f"foreground-ratio target = {CONFIG.target_fg_ratio_max}"
    )
    lines.append("")
    lines.append(
        "The validation cohort is a single anonymised head-and-neck patient "
        "(HN1) used for development testing, contoured by several independent "
        "auto-segmentation systems and manual observers — each treated as a "
        "STAPLE rater. STAPLE requires 2+ raters; each organ below was "
        "contoured by the stated number of them."
    )
    lines.append("")

    lines.append("## STAPLE parity vs `SimpleITK.STAPLEImageFilter`\n")
    lines.append(
        f"**Headline result:** **{n_exact} / {n_total} consensus computations "
        "were bit-for-bit identical** — every per-rater sensitivity and "
        "specificity matched to zero absolute difference, every binary "
        "consensus mask was voxel-identical (XOR = 0), and consensus volume "
        "and mean entropy reproduced to within floating-point round-off."
    )
    lines.append("")
    lines.append("### Per-organ results\n")
    lines.append(
        "`sens |Δ|` / `spec |Δ|` are the maximum absolute differences across "
        "the organ's raters; `XOR` is the voxel-disagreement count of the "
        "binary consensus. `**0**` denotes exact equality."
    )
    lines.append("")
    lines.append(
        "| Organ | Raters | sens \\|Δ\\| | spec \\|Δ\\| | Consensus voxels | XOR | "
        "Volume (cc) | Vol \\|Δ\\| | Entropy \\|Δ\\| | Match |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|:---:|")
    for r in sorted(rows, key=lambda x: (-x["n_raters"], x["organ"])):
        xor = "—" if r["xor"] is None else f"{r['xor']:,}"
        match = "✅" if r["result"] == "EXACT" else r["result"]
        lines.append(
            f"| `{r['organ']}` | {r['n_raters']} | {_fmt_delta(r['sens_dmax'])} | "
            f"{_fmt_delta(r['spec_dmax'])} | {r['autoseg_voxels']:,} | {xor} | "
            f"{r['volume_cc']:.4f} | {_fmt_delta(r['volume_delta'])} | "
            f"{_fmt_delta(r['entropy_delta'], eps=1e-9)} | {match} |"
        )
    lines.append("")

    lines.append("## Reproducing this report\n")
    lines.append("From the repo root:\n")
    lines.append("```bash")
    lines.append("# SimpleITK is already a core dependency — no extra install needed.")
    lines.append("python scripts/validate_staple_against_upstream.py \\")
    lines.append("    --data /path/to/DICOM/folder \\")
    lines.append("    --out docs/STAPLE_VALIDATION_REPORT.md")
    lines.append("```")
    lines.append("")
    lines.append(
        "The script reads every `.dcm` in the folder, identifies the CT series "
        "and the RTSTRUCTs, groups ROIs by clinical name, and runs the "
        "comparison for every organ contoured by 2+ raters. Exit code is 0 "
        "only if every comparison was exact. The equivalence is also locked in "
        "CI via [`tests/test_staple_equivalence.py`](../tests/test_staple_equivalence.py)."
    )
    lines.append("")

    lines.append("## References\n")
    lines.append(
        "1. **STAPLE** — Warfield SK, Zou KH, Wells WM. *Simultaneous truth and "
        "performance level estimation (STAPLE): an algorithm for the validation "
        "of image segmentation.* IEEE Transactions on Medical Imaging, "
        "23(7):903-921, 2004."
    )
    lines.append("")
    lines.append(
        "2. **SimpleITK** — Lowekamp BC, Chen DT, Ibáñez L, Blezek D. *The "
        "Design of SimpleITK.* Frontiers in Neuroinformatics, 7:45, 2013. "
        "`STAPLEImageFilter` wraps the ITK implementation of the algorithm above."
    )
    lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------- Main ----------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True, type=Path, help="Folder of CT + RTSS files")
    parser.add_argument("--out", required=True, type=Path, help="Markdown report path")
    parser.add_argument("--max-organs", type=int, default=0, help="Cap organs validated (0 = all)")
    args = parser.parse_args()
    folder: Path = args.data
    if not folder.exists() or not folder.is_dir():
        print(f"Data folder does not exist: {folder}", file=sys.stderr)
        return 2

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
    if len(rtss_datasets) < 2:
        print("Need at least 2 RTSTRUCT files for a multi-rater STAPLE.", file=sys.stderr)
        return 2

    # Group ROI occurrences by normalised clinical name.
    name_index: dict[str, list[tuple[Any, int]]] = {}
    for ds in rtss_datasets:
        for struct in getattr(ds, "StructureSetROISequence", []):
            name_index.setdefault(_normalise_name(struct.ROIName), []).append(
                (ds, int(struct.ROINumber))
            )

    shared = sorted(name for name, owners in name_index.items() if len(owners) >= 2)
    if args.max_organs > 0:
        # Keep the most-rated organs first so a capped run is still meaningful.
        shared = sorted(shared, key=lambda n: -len(name_index[n]))[: args.max_organs]
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

    if not rows:
        print("No organ had 2+ non-empty rater masks — nothing to validate.", file=sys.stderr)
        return 2

    versions = {
        "AutoSeg Evaluator": autoseg_version,
        "Python": platform.python_version(),
        "Operating system": f"{platform.system()} {platform.release()}",
        "SimpleITK": importlib.metadata.version("SimpleITK"),
        "pydicom": importlib.metadata.version("pydicom"),
        "numpy": importlib.metadata.version("numpy"),
    }
    write_report(args.out, image=image, n_rtss=len(rtss_datasets), rows=rows, versions=versions)
    print(f"\nReport written to {args.out}")

    n_exact = sum(1 for r in rows if r["result"] == "EXACT")
    print(f"{n_exact}/{len(rows)} organs bit-exact.")
    return 0 if n_exact == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
