"""Compare the legacy and continuous mask rasterisers on a real cohort.

Quantifies what changing the rasteriser does to your masks — per-ROI agreement
and per-backend runtime — so the delta can be reported rather than silently
absorbed into the metrics.

The report is PHI-safe by construction: RTSS files are labelled ``RTSS A``,
``RTSS B``, … and only anonymised ROI display names, ROI numbers and numeric
values are emitted. No filenames, SOPInstanceUIDs, patient identifiers, dates
or institution metadata are written.

Usage::

    python scripts/compare_rasterisers.py \\
        --data /path/to/folder/containing/CT/and/RTSS/files \\
        --out docs/RASTERISER_COMPARISON.md
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pydicom
import SimpleITK as sitk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from autoseg_evaluator.core.masks import (  # noqa: E402
    RASTERISER_CONTINUOUS,
    RASTERISER_LEGACY,
    extract_mask_for_roi,
    read_dicom_image,
    read_rtstruct,
)

COMPARABLE = ("IDENTICAL", "DIFFERS")


def find_rtstructs(folder: Path) -> list[Path]:
    """Every RTSTRUCT in ``folder``, identified by DICOM Modality.

    Filename globbing misses vendor exports that don't follow the ``RS*.dcm``
    convention (``limbus_*.dcm``, ``MVision.dcm``, …), which is exactly the
    multi-vendor case this comparison needs.
    """
    found: list[Path] = []
    for path in sorted(folder.glob("*.dcm")):
        try:
            ds = pydicom.dcmread(str(path), stop_before_pixels=True, force=True)
        except Exception:  # noqa: BLE001 — unreadable file is simply not an RTSS
            continue
        if str(getattr(ds, "Modality", "")).upper() == "RTSTRUCT":
            found.append(path)
    return found


def _voxel_volume_cc(image: sitk.Image) -> float:
    sx, sy, sz = image.GetSpacing()
    return float(sx * sy * sz) / 1000.0


def _rasterise_timed(image, ds, roi_number: int, backend: str, repeat: int):
    """Rasterise one ROI, returning ``(bool_array_or_None, best_seconds)``."""
    best = float("inf")
    array = None
    for _ in range(max(1, repeat)):
        start = time.perf_counter()
        mask = extract_mask_for_roi(image, ds, roi_number, backend=backend)
        best = min(best, time.perf_counter() - start)
        array = None if mask is None else sitk.GetArrayFromImage(mask) > 0
    return array, best


def compare_rtss(label: str, ds: Any, image: sitk.Image, repeat: int) -> list[dict[str, Any]]:
    """One row per ROI comparing the two backends."""
    rows: list[dict[str, Any]] = []
    if not hasattr(ds, "StructureSetROISequence"):
        return rows
    voxel_cc = _voxel_volume_cc(image)
    for struct in ds.StructureSetROISequence:
        roi_number = int(struct.ROINumber)
        legacy, t_legacy = _rasterise_timed(image, ds, roi_number, RASTERISER_LEGACY, repeat)
        cont, t_cont = _rasterise_timed(image, ds, roi_number, RASTERISER_CONTINUOUS, repeat)

        row: dict[str, Any] = {
            "rtss": label,
            "roi": str(struct.ROIName),
            "roi_number": roi_number,
            "t_legacy_ms": t_legacy * 1000.0,
            "t_continuous_ms": t_cont * 1000.0,
        }
        if legacy is None and cont is None:
            row["status"] = "BOTH-NONE"
        elif legacy is None:
            row["status"] = "CONTINUOUS-ONLY"
            row["continuous_cc"] = float(cont.sum()) * voxel_cc
        elif cont is None:
            row["status"] = "LEGACY-ONLY"
            row["legacy_cc"] = float(legacy.sum()) * voxel_cc
        else:
            n_legacy, n_cont = int(legacy.sum()), int(cont.sum())
            intersect = int(np.logical_and(legacy, cont).sum())
            disagree = int(np.logical_xor(legacy, cont).sum())
            denom = n_legacy + n_cont
            row["status"] = "IDENTICAL" if disagree == 0 else "DIFFERS"
            row["legacy_cc"] = n_legacy * voxel_cc
            row["continuous_cc"] = n_cont * voxel_cc
            row["delta_cc"] = (n_cont - n_legacy) * voxel_cc
            row["disagreeing_voxels"] = disagree
            row["dice"] = (2.0 * intersect / denom) if denom else float("nan")
        rows.append(row)
    return rows


def _fmt(value: Any, spec: str = ".4g") -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return "—" if np.isnan(value) else format(value, spec)
    return str(value)


def build_report(rows: list[dict[str, Any]], image: sitk.Image, repeat: int) -> str:
    compared = [r for r in rows if r["status"] in COMPARABLE]
    identical = [r for r in compared if r["status"] == "IDENTICAL"]
    dices = [r["dice"] for r in compared]
    deltas = [abs(r["delta_cc"]) for r in compared]
    t_legacy = sum(r["t_legacy_ms"] for r in rows)
    t_cont = sum(r["t_continuous_ms"] for r in rows)

    lines = [
        "# Rasteriser comparison — legacy vs continuous",
        "",
        "Generated by `scripts/compare_rasterisers.py`. Contains no PHI: RTSS files are",
        "labelled A, B, … and only anonymised ROI names and numeric values appear.",
        "",
        f"* CT grid `{image.GetSize()}`, spacing "
        f"`{tuple(round(s, 4) for s in image.GetSpacing())}`",
        f"* ROIs compared: **{len(compared)}** of {len(rows)} "
        f"({len(identical)} voxel-identical, {len(compared) - len(identical)} differing)",
    ]
    if dices:
        lines.append(
            f"* Dice(legacy, continuous): mean **{statistics.fmean(dices):.5f}**, "
            f"min {min(dices):.5f}"
        )
        lines.append(
            f"* |Δ volume|: mean **{statistics.fmean(deltas):.4f} cc**, max {max(deltas):.4f} cc"
        )
    if t_cont > 0:
        lines.append(
            f"* Rasterisation time (best of {repeat}): legacy **{t_legacy:.0f} ms** vs "
            f"continuous **{t_cont:.0f} ms** (**{t_legacy / t_cont:.2f}×**)"
        )
    lines.extend(
        [
            "",
            "Rows where only one backend produced a mask are flagged by status rather than",
            "compared numerically.",
            "",
            "| RTSS | ROI | # | Status | Legacy (cc) | Continuous (cc) | Δ (cc) | Dice "
            "| Legacy (ms) | Continuous (ms) |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for r in rows:
        lines.append(
            f"| {r['rtss']} | {r['roi']} | {r['roi_number']} | {r['status']} "
            f"| {_fmt(r.get('legacy_cc'))} | {_fmt(r.get('continuous_cc'))} "
            f"| {_fmt(r.get('delta_cc'), '+.4g')} | {_fmt(r.get('dice'), '.5f')} "
            f"| {r['t_legacy_ms']:.1f} | {r['t_continuous_ms']:.1f} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True, type=Path, help="Folder with CT + RTSS files")
    parser.add_argument("--out", type=Path, help="Optional markdown report path")
    parser.add_argument(
        "--repeat", type=int, default=1, help="Timing repeats per ROI (best-of); default 1"
    )
    args = parser.parse_args()

    # A cp1252 Windows console must not be able to kill a finished run.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    folder: Path = args.data
    if not folder.is_dir():
        print(f"Data folder does not exist: {folder}", file=sys.stderr)
        return 2

    print(f"Loading CT series from {folder} ...")
    image = read_dicom_image(str(folder))
    print(f"  CT: size {image.GetSize()}, spacing {tuple(round(s, 3) for s in image.GetSpacing())}")

    rtss_paths = find_rtstructs(folder)
    if not rtss_paths:
        print(f"No RTSTRUCT files found in {folder}", file=sys.stderr)
        return 2
    print(f"  found {len(rtss_paths)} RTSTRUCT(s)")

    rows: list[dict[str, Any]] = []
    for idx, path in enumerate(rtss_paths):
        label = f"RTSS {chr(ord('A') + idx)}"
        print(f"  comparing {label} ...")
        rows.extend(compare_rtss(label, read_rtstruct(str(path)), image, args.repeat))

    compared = [r for r in rows if r["status"] in COMPARABLE]
    identical = sum(1 for r in compared if r["status"] == "IDENTICAL")
    print(f"\n{identical} / {len(compared)} ROI masks voxel-identical between backends.")
    if compared:
        print(f"mean Dice {statistics.fmean(r['dice'] for r in compared):.5f}")
        print(f"max |delta volume| {max(abs(r['delta_cc']) for r in compared):.4f} cc")
    t_legacy = sum(r["t_legacy_ms"] for r in rows)
    t_cont = sum(r["t_continuous_ms"] for r in rows)
    if t_cont > 0:
        print(
            f"time: legacy {t_legacy:.0f} ms vs continuous {t_cont:.0f} ms "
            f"({t_legacy / t_cont:.2f}x)"
        )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(build_report(rows, image, args.repeat), encoding="utf-8")
        print(f"Report written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
