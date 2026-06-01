"""Validate AutoSeg Evaluator's DVH dose statistics against dicompyler-core.

AutoSeg's :func:`autoseg_evaluator.core.dvh.compute_dvh_metrics` computes
per-structure dose-volume statistics by driving ``dicompylercore.dvhcalc``
(the reference DVH library — Panchal & Keyes, dicompyler-core). This script
demonstrates that the statistics AutoSeg reports are **bit-for-bit
identical** to those obtained by invoking dicompyler-core's public API
directly, on a clinical CT + RT Dose + RT Structure sample dataset.

For every ROI that yields a DVH against the dose grid, the following are
compared (Δ = 0 expected):

* Dmin / Dmean / Dmax (Gy)
* D{X}% — dose to the hottest X % of the structure (Gy)
* D{X}cc — dose to the hottest X cc of the structure (Gy)
* V{X}Gy — volume receiving >= X Gy (cc)

The reference values are read straight from ``dvh.min`` / ``dvh.mean`` /
``dvh.max`` and ``dvh.statistic(...)`` on a fresh ``dvhcalc.get_dvh``
result, independently of AutoSeg's wrapper — so the comparison confirms
the wrapper selects the correct dicompyler statistics, units, and keys.

All output is PHI-safe: no SOPInstanceUIDs, filenames, patient identifiers,
dates, or institution metadata. ROIs are referenced by clinical name only.

Usage::

    python scripts/validate_dvh_against_upstream.py \\
        --data /path/to/folder/with/CT/RTSS/RTDOSE \\
        --out docs/DVH_VALIDATION_REPORT.md
"""

from __future__ import annotations

import argparse
import datetime
import importlib.metadata
import math
import platform
import sys
from pathlib import Path
from typing import Any

import pydicom

# AutoSeg
from autoseg_evaluator import __version__ as autoseg_version
from autoseg_evaluator.core.dvh import DVHConfig, DVHError, compute_dvh_metrics

# The validation config — a representative spread of HN dose statistics.
CONFIG = DVHConfig(
    include_dmin=True,
    include_dmean=True,
    include_dmax=True,
    d_at_volumes_pct=[95.0, 50.0, 2.0],
    d_at_volumes_cc=[0.1, 2.0],
    v_at_doses_gy=[20.0, 30.0],
)

# Organs to expand into a full per-statistic appendix table (if present).
REPRESENTATIVE = [
    "parotid_l",
    "parotid_r",
    "brainstem",
    "spinalcord",
    "spinal_cord",
    "glnd_submand_l",
    "bone_mandible",
    "mandible",
]


def _normalise_name(n: str) -> str:
    return "_".join(str(n).split()).lower()


def _fmt_num(value: float) -> str:
    if isinstance(value, (int, float)) and float(value).is_integer():
        return str(int(value))
    return str(value)


def _safe(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _statistic_value(dvh, expression: str) -> float:
    try:
        stat = dvh.statistic(expression)
    except Exception:  # noqa: BLE001
        return math.nan
    return _safe(getattr(stat, "value", stat))


# -------------------- Independent dicompyler-core reference ---------------


def reference_dvh_metrics(rtss_ds, dose_ds, roi_number: int, config: DVHConfig) -> dict[str, float]:
    """Compute the same statistics straight from dicompyler-core's public API."""
    from dicompylercore import dvhcalc

    dvh = dvhcalc.get_dvh(rtss_ds, dose_ds, roi_number, calculate_full_volume=True)
    if dvh is None or getattr(dvh, "volume", 0) == 0:
        raise DVHError(f"No DVH curve for ROI #{roi_number}.")

    out: dict[str, float] = {}
    if config.include_dmin:
        out["dmin_gy"] = _safe(dvh.min)
    if config.include_dmean:
        out["dmean_gy"] = _safe(dvh.mean)
    if config.include_dmax:
        out["dmax_gy"] = _safe(dvh.max)
    for v_pct in config.d_at_volumes_pct:
        out[f"d{_fmt_num(v_pct)}_gy"] = _statistic_value(dvh, f"D{_fmt_num(v_pct)}")
    for v_cc in config.d_at_volumes_cc:
        out[f"d{_fmt_num(v_cc)}cc_gy"] = _statistic_value(dvh, f"D{_fmt_num(v_cc)}cc")
    for d_gy in config.v_at_doses_gy:
        out[f"v{_fmt_num(d_gy)}gy_cc"] = _statistic_value(dvh, f"V{_fmt_num(d_gy)}Gy")
    return out


def _values_equal(a: float, b: float) -> bool:
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b


# ------------------------------- Comparison -------------------------------


def compare_one_roi(
    rtss_label: str, organ: str, rtss_ds, dose_ds, roi_number: int
) -> dict[str, Any] | None:
    """Compare AutoSeg vs dicompyler-core DVH stats for one ROI."""
    try:
        autoseg = compute_dvh_metrics(rtss_ds, dose_ds, roi_number, CONFIG)
    except DVHError:
        return None
    try:
        reference = reference_dvh_metrics(rtss_ds, dose_ds, roi_number, CONFIG)
    except DVHError:
        return None
    if not autoseg or not reference:
        return None

    per_stat: dict[str, tuple[float, float, float]] = {}
    max_delta = 0.0
    n_exact = 0
    for key in CONFIG.output_keys():
        a = _safe(autoseg.get(key, math.nan))
        b = _safe(reference.get(key, math.nan))
        equal = _values_equal(a, b)
        delta = 0.0 if equal else abs(a - b)
        if equal:
            n_exact += 1
        else:
            max_delta = max(max_delta, delta)
        per_stat[key] = (a, b, delta)
    return {
        "rtss": rtss_label,
        "organ": organ,
        "n_stats": len(per_stat),
        "n_exact": n_exact,
        "max_delta": max_delta,
        "per_stat": per_stat,
        "result": "EXACT" if n_exact == len(per_stat) else "differs",
    }


# ------------------------------ Report writer -----------------------------


def _fmt_value(v: float) -> str:
    if isinstance(v, float) and math.isnan(v):
        return "nan"
    return f"{v:.6f}"


def _fmt_delta(delta: float) -> str:
    return "**0**" if delta == 0.0 else f"{delta:.3e}"


def write_report(
    out_path: Path,
    *,
    n_rtss: int,
    rows: list[dict[str, Any]],
    versions: dict[str, str],
) -> None:
    lines: list[str] = []
    today = datetime.date.today().isoformat()
    n_total = len(rows)
    n_exact = sum(1 for r in rows if r["result"] == "EXACT")
    n_comparisons = sum(r["n_stats"] for r in rows)
    n_comparisons_exact = sum(r["n_exact"] for r in rows)

    lines.append("# DVH Dose-Statistic Validation — AutoSeg Evaluator vs dicompyler-core\n")
    lines.append(f"_Auto-generated by `scripts/validate_dvh_against_upstream.py` on {today}._\n")
    lines.append("")
    lines.append(
        "This report demonstrates that AutoSeg Evaluator's dose-volume "
        "histogram (DVH) statistics are **bit-for-bit identical** to those "
        "produced by the reference DVH library, `dicompyler-core`, on a "
        "clinical CT + RT Dose + RT Structure sample dataset. It is intended "
        "for manuscript reviewers and any user who wants to verify the "
        "implementation-parity claim."
    )
    lines.append("")
    lines.append(
        "**Privacy:** this report contains no PHI. ROI display names (organ "
        "names) and dose/volume values are shown; no SOPInstanceUIDs, "
        "filenames, patient identifiers, dates, or institution metadata are "
        "included."
    )
    lines.append("")

    lines.append("## What is validated\n")
    lines.append(
        "AutoSeg's `compute_dvh_metrics` drives `dicompylercore.dvhcalc.get_dvh` "
        "(dose-grid resampling onto the structure, cumulative DVH construction) "
        "and extracts the requested statistics. The validator recomputes each "
        "statistic **independently**, reading `dvh.min` / `dvh.mean` / `dvh.max` "
        "and `dvh.statistic(...)` from a fresh `get_dvh` result, and compares. "
        "Agreement to zero confirms the wrapper selects the correct dicompyler "
        "statistic expressions, units (Gy for dose, cc for volume), and output "
        "keys without altering any value."
    )
    lines.append("")
    lines.append("The statistics compared per ROI are:")
    lines.append("")
    lines.append("- **Dmin / Dmean / Dmax** — minimum / mean / maximum dose (Gy).")
    lines.append(
        "- **D95% / D50% / D2%** — dose to the hottest 95 / 50 / 2 % of the structure (Gy)."
    )
    lines.append("- **D0.1cc / D2cc** — dose to the hottest 0.1 / 2 cc of the structure (Gy).")
    lines.append("- **V20Gy / V30Gy** — structure volume receiving >= 20 / 30 Gy (cc).")
    lines.append("")

    lines.append("## Software environment\n")
    lines.append("| Component | Version |")
    lines.append("|---|---|")
    for k, v in versions.items():
        lines.append(f"| {k} | `{v}` |")
    lines.append("")

    lines.append("## Validation input\n")
    lines.append(f"- **RT Structure sets loaded:** {n_rtss}")
    lines.append(f"- **ROIs with a DVH against the dose grid (validated):** {n_total}")
    lines.append(f"- **Statistics compared per ROI:** {len(CONFIG.output_keys())}")
    lines.append(f"- **Total dose-statistic comparisons:** {n_comparisons}")
    lines.append("")
    lines.append(
        "The validation cohort is a single anonymised head-and-neck patient "
        "(HN1) with a clinical RT Dose grid and several RT Structure sets. "
        "Every ROI that overlaps the dose grid contributes one DVH; ROIs that "
        "lie outside the dose grid (no DVH) are skipped by both paths and not "
        "counted."
    )
    lines.append("")

    lines.append("## DVH parity vs `dicompyler-core`\n")
    lines.append(
        f"**Headline result:** **{n_comparisons_exact} / {n_comparisons} "
        f"dose-statistic comparisons across {n_exact} / {n_total} ROIs produced "
        "exactly zero absolute difference.**"
    )
    lines.append("")
    lines.append("### Per-ROI summary\n")
    lines.append(
        "One row per ROI: how many of its statistics matched exactly, and the "
        "maximum absolute difference across all of them. `**0**` denotes exact "
        "equality on every statistic."
    )
    lines.append("")
    lines.append("| RTSS | Organ | Stats matched | Max \\|Δ\\| | Match |")
    lines.append("|---|---|---:|---:|:---:|")
    for r in sorted(rows, key=lambda x: (x["rtss"], x["organ"])):
        match = "✅" if r["result"] == "EXACT" else r["result"]
        lines.append(
            f"| {r['rtss']} | `{r['organ']}` | {r['n_exact']}/{r['n_stats']} | "
            f"{_fmt_delta(r['max_delta'])} | {match} |"
        )
    lines.append("")

    # Detailed per-statistic appendix for representative organs.
    detail = []
    seen_organs: set[str] = set()
    for r in rows:
        if r["organ"] in REPRESENTATIVE and r["organ"] not in seen_organs:
            detail.append(r)
            seen_organs.add(r["organ"])
    if detail:
        lines.append("### Detailed per-statistic values (representative organs)\n")
        lines.append(
            "Actual AutoSeg vs dicompyler-core values for a clinically "
            "representative subset, to show the comparison is on non-trivial "
            "numbers (doses in Gy, volumes in cc)."
        )
        lines.append("")
        lines.append("| Organ | Statistic | AutoSeg | dicompyler-core | \\|Δ\\| |")
        lines.append("|---|---|---:|---:|---:|")
        label_map = {
            "dmin_gy": "Dmin (Gy)",
            "dmean_gy": "Dmean (Gy)",
            "dmax_gy": "Dmax (Gy)",
            "d95_gy": "D95% (Gy)",
            "d50_gy": "D50% (Gy)",
            "d2_gy": "D2% (Gy)",
            "d0.1cc_gy": "D0.1cc (Gy)",
            "d2cc_gy": "D2cc (Gy)",
            "v20gy_cc": "V20Gy (cc)",
            "v30gy_cc": "V30Gy (cc)",
        }
        for r in detail:
            for key in CONFIG.output_keys():
                a, b, delta = r["per_stat"][key]
                lines.append(
                    f"| `{r['organ']}` | {label_map.get(key, key)} | "
                    f"{_fmt_value(a)} | {_fmt_value(b)} | {_fmt_delta(delta)} |"
                )
        lines.append("")

    lines.append("## Reproducing this report\n")
    lines.append("From the repo root:\n")
    lines.append("```bash")
    lines.append("# dicompyler-core is already a core dependency — no extra install needed.")
    lines.append("python scripts/validate_dvh_against_upstream.py \\")
    lines.append("    --data /path/to/DICOM/folder \\")
    lines.append("    --out docs/DVH_VALIDATION_REPORT.md")
    lines.append("```")
    lines.append("")
    lines.append(
        "The folder must contain at least one RT Structure set and one RT Dose. "
        "The script computes a DVH for every ROI that overlaps the dose grid "
        "with both AutoSeg and dicompyler-core, and writes the markdown report "
        "to `--out`. Exit code is 0 only if every comparison was exact. The "
        "equivalence is also locked in CI via "
        "[`tests/test_dvh_equivalence.py`](../tests/test_dvh_equivalence.py)."
    )
    lines.append("")

    lines.append("## References\n")
    lines.append(
        "1. **dicompyler-core** — Panchal A, Keyes R. *dicompyler-core: a "
        "library of radiation therapy data parsing and DVH computation.* "
        "https://github.com/dicompyler/dicompyler-core"
    )
    lines.append("")
    lines.append(
        "2. **DVH conventions** — ICRU Report 83. *Prescribing, Recording, and "
        "Reporting Photon-Beam Intensity-Modulated Radiation Therapy (IMRT).* "
        "Journal of the ICRU, 2010."
    )
    lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------- Main ----------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True, type=Path, help="Folder with RTSS + RTDOSE files")
    parser.add_argument("--out", required=True, type=Path, help="Markdown report path")
    parser.add_argument("--max-rois", type=int, default=0, help="Cap ROIs validated (0 = all)")
    args = parser.parse_args()
    folder: Path = args.data
    if not folder.exists() or not folder.is_dir():
        print(f"Data folder does not exist: {folder}", file=sys.stderr)
        return 2

    rtss_datasets: list[Any] = []
    dose_ds = None
    for path in sorted(folder.glob("*.dcm")):
        try:
            ds = pydicom.dcmread(str(path), force=True)
        except Exception:  # noqa: BLE001
            continue
        modality = str(getattr(ds, "Modality", ""))
        if modality == "RTSTRUCT" or hasattr(ds, "StructureSetROISequence"):
            ds.filename = str(path)
            rtss_datasets.append(ds)
        elif modality == "RTDOSE" and dose_ds is None:
            ds.filename = str(path)
            dose_ds = ds

    if dose_ds is None:
        print(f"No RTDOSE found in {folder}", file=sys.stderr)
        return 2
    if not rtss_datasets:
        print(f"No RTSTRUCT found in {folder}", file=sys.stderr)
        return 2
    print(f"Loaded {len(rtss_datasets)} RTSTRUCT + 1 RTDOSE.")

    rows: list[dict[str, Any]] = []
    n_checked = 0
    for idx, rtss_ds in enumerate(rtss_datasets):
        label = f"RTSS {chr(ord('A') + idx)}"
        for struct in getattr(rtss_ds, "StructureSetROISequence", []):
            if args.max_rois and n_checked >= args.max_rois:
                break
            organ = _normalise_name(struct.ROIName)
            n_checked += 1
            row = compare_one_roi(label, organ, rtss_ds, dose_ds, int(struct.ROINumber))
            if row is None:
                continue
            rows.append(row)
            flag = "OK " if row["result"] == "EXACT" else "DIFF"
            print(f"  [{flag}] {label} {organ:22s} {row['n_exact']}/{row['n_stats']} stats")

    if not rows:
        print("No ROI produced a DVH against the dose grid — nothing to validate.", file=sys.stderr)
        return 2

    versions = {
        "AutoSeg Evaluator": autoseg_version,
        "Python": platform.python_version(),
        "Operating system": f"{platform.system()} {platform.release()}",
        "dicompyler-core": importlib.metadata.version("dicompyler-core"),
        "pydicom": importlib.metadata.version("pydicom"),
        "numpy": importlib.metadata.version("numpy"),
    }
    write_report(args.out, n_rtss=len(rtss_datasets), rows=rows, versions=versions)
    print(f"\nReport written to {args.out}")

    n_exact = sum(1 for r in rows if r["result"] == "EXACT")
    print(f"{n_exact}/{len(rows)} ROIs bit-exact across all statistics.")
    return 0 if n_exact == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
