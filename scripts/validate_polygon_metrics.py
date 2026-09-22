"""Acceptance for the native polygon metrics, at four depths.

``public`` and ``stress``
    The suppliers' own suites, run unmodified against our vendored copy. 150
    published pairs and 44 geometric edge cases. These say the *kernel* is
    right.

``adapter``
    The same 150 pairs, driven through this application's own call path —
    ``ContourRegions``, engine selection, and the mapping onto result columns.
    A kernel can be perfect while the code calling it swaps the two directions,
    and APL is directional: on the first published pair the two sides are 480.0
    and 265.8 mm, so a swap is caught rather than averaged away.

``dicom``
    The published DICOM archives, from files, through *our* grid builder and
    *our* parser before any metric is computed. This is the only layer where
    the planes are genuinely distinct — the compact fixtures repeat one polygon
    across every plane, which is the case that collapses under multiplicity and
    hides the per-plane cost — and the only one that exercises slice ordering,
    frame checks and contour-to-plane assignment at all.

``dicom`` needs the original archives, which are 314 MB and do not belong in
this repository. The report it writes does.

Usage::

    python scripts/validate_polygon_metrics.py
    python scripts/validate_polygon_metrics.py --suite adapter
    python scripts/validate_polygon_metrics.py --suite dicom \\
        --data "<folder with the three Resolution.zip archives>" \\
        --out docs/POLYGON_VALIDATION_REPORT.md
"""

from __future__ import annotations

import argparse
import gzip
import json
import runpy
import shutil
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR = REPO_ROOT / "src" / "autoseg_evaluator" / "vendor"
SUPPLIER = REPO_ROOT / "third_party" / "native_contour_metrics" / "v0.2"
DATA = SUPPLIER / "data"

sys.path.insert(0, str(REPO_ROOT / "src"))

#: The suppliers' acceptance thresholds, not ours. Loosening one silently would
#: defeat the point of having them.
DISTANCE_TOLERANCE_MM = 0.001000002
APL_TOLERANCE_MM = 1e-8
NAPL_TOLERANCE = 1e-10

#: The published benchmark is stated at these two tolerances.
TOLERANCES_MM = (1.0, 2.0)


# ---- The suppliers' own suites ---------------------------------------------


def _run_supplier(name: str, script: Path, output_dir: Path | None) -> dict[str, Any]:
    print(f"\n=== {name} " + "=" * (68 - len(name)))
    argv = [str(script)]
    if output_dir is not None:
        argv += ["--output-dir", str(output_dir / name)]
    saved, sys.argv = sys.argv, argv
    started = time.perf_counter()
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exit_signal:
        if exit_signal.code:
            return {"suite": name, "passed": False, "detail": f"exit {exit_signal.code}"}
    finally:
        sys.argv = saved
    sizes = {"public": 150, "stress": 44}
    return {
        "suite": name,
        "passed": True,
        "pairs": sizes.get(name),
        "seconds": time.perf_counter() - started,
    }


# ---- Our own call path -----------------------------------------------------


def _golden() -> list[dict[str, Any]]:
    return json.loads((DATA / "golden_metrics.json").read_text(encoding="utf-8"))


def _check_pair(case: dict[str, Any], values: dict[str, float], planes: tuple[int, int, int]):
    """Compare one pair's results against the published golden record.

    Only the symmetric distances and both APL directions are checked, because
    those are what this application puts in a results row. The directional
    distances stay the suppliers' business and are covered by ``public``.
    """
    failures = []
    worst = {"distance_mm": 0.0, "apl_mm": 0.0, "napl": 0.0}

    expected = case["distance"]
    for key, column in (
        ("hd_mm", "poly_hd100_mm"),
        ("hd95_mm", "poly_hd95_mm"),
        ("mean_mm", "poly_mean_distance_mm"),
        ("median_mm", "poly_median_distance_mm"),
    ):
        error = abs(values[column] - expected[key])
        worst["distance_mm"] = max(worst["distance_mm"], error)
        if error > DISTANCE_TOLERANCE_MM:
            failures.append(f"{case['id']} {column}: {error:.3e} mm")

    # ``a`` is the reference and ``b`` the test, in both the golden record and
    # our columns. They differ by hundreds of millimetres, so a swap here is
    # loud rather than subtle.
    reference_apl = case["apl"][f"{TOLERANCES_MM[1]}"]
    for side, apl_column, napl_column in (
        ("a", "poly_apl_mm", "poly_napl"),
        ("b", "poly_apl_reverse_mm", "poly_napl_reverse"),
    ):
        error = abs(values[apl_column] - reference_apl[side]["apl_mm"])
        worst["apl_mm"] = max(worst["apl_mm"], error)
        if error > APL_TOLERANCE_MM:
            failures.append(f"{case['id']} {apl_column}: {error:.3e} mm")
        error = abs(values[napl_column] - reference_apl[side]["napl"])
        worst["napl"] = max(worst["napl"], error)
        if error > NAPL_TOLERANCE:
            failures.append(f"{case['id']} {napl_column}: {error:.3e}")

    joint, excluded_a, excluded_b = planes
    if (joint, excluded_a, excluded_b) != (
        case["joint_planes"],
        case["excluded_reference_planes"],
        case["excluded_test_planes"],
    ):
        failures.append(
            f"{case['id']} plane counts: got {(joint, excluded_a, excluded_b)}, "
            f"expected {(case['joint_planes'], case['excluded_reference_planes'], case['excluded_test_planes'])}"
        )
    return failures, worst


def _measure(reference, test, engine) -> tuple[dict[str, float], tuple[int, int, int]]:
    from autoseg_evaluator.core.polygon_metrics import compare_structures

    result = compare_structures(reference, test, tolerance_mm=TOLERANCES_MM[1], engine=engine)
    if not result.available:
        raise RuntimeError(result.status)
    values = result.values
    return values, (
        int(values["poly_planes_joint"]),
        int(values["poly_planes_gt_only"]),
        int(values["poly_planes_test_only"]),
    )


def run_adapter(engine_name: str | None = None) -> dict[str, Any]:
    """The published pairs, through this application's own call path."""
    from shapely.geometry import Polygon

    from autoseg_evaluator.core.polygon_metrics import (
        REFERENCE_ACCEPTANCE_SAMPLING_MM,
        ContourRegions,
        select_engine,
    )

    print("\n=== adapter " + "=" * 60)
    # The published thresholds describe agreement with audited *continuous*
    # values. The reference engine reaches them only at the sampling step its
    # own acceptance was recorded at; judging it at the coarser step it runs at
    # in production measures that step and reports it as a failure.
    engine = select_engine(engine_name, sampling_mm=REFERENCE_ACCEPTANCE_SAMPLING_MM)
    with gzip.open(DATA / "common_plane_fixtures.json.gz", "rt") as stream:
        fixtures = json.load(stream)

    failures: list[str] = []
    worst = {"distance_mm": 0.0, "apl_mm": 0.0, "napl": 0.0}
    started = time.perf_counter()
    cases = _golden()
    for case in cases:
        fixture = next(
            row
            for row in fixtures
            if (row["resolution"], row["family"], row["number"])
            == (case["resolution"], case["family"], case["number"])
        )
        a, b = Polygon(fixture["a"]), Polygon(fixture["b"])
        joint = case["joint_planes"]
        reference_planes = joint + case["excluded_reference_planes"]
        test_planes = joint + case["excluded_test_planes"]
        reference = ContourRegions(
            planes={z: a for z in range(reference_planes)},
            geometric_type="CLOSED_PLANAR",
            vertices=0,
        )
        test = ContourRegions(
            planes={
                **{z: b for z in range(joint)},
                **{reference_planes + z: b for z in range(test_planes - joint)},
            },
            geometric_type="CLOSED_PLANAR",
            vertices=0,
        )
        values, planes = _measure(reference, test, engine)
        case_failures, case_worst = _check_pair(case, values, planes)
        failures += case_failures
        for key in worst:
            worst[key] = max(worst[key], case_worst[key])

    elapsed = time.perf_counter() - started
    print(f"adapter: {len(cases)} pairs through {engine.label}, {len(failures)} failures")
    return {
        "suite": f"adapter ({engine.name})",
        "passed": not failures,
        "engine": engine.label,
        "settings": engine.settings,
        "pairs": len(cases),
        "seconds": elapsed,
        "max_errors": worst,
        "failures": failures[:20],
    }


# ---- From the DICOM up -----------------------------------------------------


def _series_grid(root: Path, series: str, cache: dict[str, Any]):
    from autoseg_evaluator.core.contour_grid import build_grid

    if series not in cache:
        files = sorted(str(p) for p in (root / series / "CT").glob("*.dcm"))
        cache[series] = build_grid(files)
    return cache[series]


def run_dicom(archives: Path, engine_name: str | None = None) -> dict[str, Any]:
    """The published archives, from DICOM files, through our whole path.

    Every earlier layer starts from polygons somebody else prepared. This one
    starts from the files and builds the frame, orders the slices and assigns
    the contours to planes itself, which is the part no supplier suite can test
    for us.
    """
    import pydicom

    from autoseg_evaluator.core.polygon_metrics import (
        ContourRegions,
        parse_structure,
        select_engine,
    )

    print("\n=== dicom " + "=" * 62)
    engine = select_engine(engine_name)
    cases = _golden()
    by_resolution: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        by_resolution.setdefault(case["resolution"], []).append(case)

    failures: list[str] = []
    worst = {"distance_mm": 0.0, "apl_mm": 0.0, "napl": 0.0}
    checked = 0
    parsed_rois = 0
    started = time.perf_counter()

    for resolution, group in sorted(by_resolution.items()):
        archive = archives / f"{resolution}Resolution.zip"
        if not archive.is_file():
            raise FileNotFoundError(f"Missing published archive: {archive}")
        scratch = Path(tempfile.mkdtemp(prefix=f"polygon-{resolution.lower()}-"))
        try:
            print(f"  {resolution}: extracting {archive.name} ...", flush=True)
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(scratch)
            grids: dict[str, Any] = {}
            for case in group:
                series = case["reference_file"].split("/")[0]
                grid = _series_grid(scratch, series, grids)
                regions = []
                for key in ("reference_file", "test_file"):
                    dataset = pydicom.dcmread(str(scratch / case[key]))
                    roi_number = int(dataset.StructureSetROISequence[0].ROINumber)
                    regions.append(parse_structure(dataset, roi_number, grid))
                    parsed_rois += 1
                reference, test = regions
                assert isinstance(reference, ContourRegions)
                values, planes = _measure(reference, test, engine)
                case_failures, case_worst = _check_pair(case, values, planes)
                failures += case_failures
                for key in worst:
                    worst[key] = max(worst[key], case_worst[key])
                checked += 1
            print(f"  {resolution}: {len(group)} pairs, {len(grids)} grids built", flush=True)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    elapsed = time.perf_counter() - started
    print(f"dicom: {checked} pairs, {parsed_rois} ROIs parsed, {len(failures)} failures")
    return {
        "suite": "dicom",
        "passed": not failures,
        "engine": engine.label,
        "settings": engine.settings,
        "pairs": checked,
        "rois_parsed": parsed_rois,
        "seconds": elapsed,
        "max_errors": worst,
        "failures": failures[:20],
    }


# ---- Reporting -------------------------------------------------------------


def write_report(target: Path, results: list[dict[str, Any]]) -> None:
    from autoseg_evaluator.core.polygon_metrics import (
        REFERENCE_ACCEPTANCE_SAMPLING_MM,
        REFERENCE_SAMPLING_MM,
    )

    produced = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    passed = all(row.get("passed") for row in results)
    lines = [
        "# Native polygon metrics — validation report",
        "",
        "Generated by `scripts/validate_polygon_metrics.py`. Regenerate it rather",
        "than editing it.",
        "",
        f"**{'PASSED' if passed else 'FAILED'}** · {produced}",
        "",
        "## What was checked",
        "",
        "| Suite | Result | Pairs | Seconds | What it proves |",
        "|---|---|---:|---:|---|",
    ]
    meaning = {
        "public": "the vendored kernel reproduces the published values",
        "stress": "geometric edge cases behave, including refusals that must stay refusals",
        "adapter": "this application's call path preserves them, directions included",
        "dicom": "our grid builder and parser reach them from the DICOM files themselves",
    }
    for row in results:
        name = row["suite"].split(" ")[0]
        lines.append(
            f"| `{row['suite']}` | {'pass' if row.get('passed') else 'FAIL'} | "
            f"{row.get('pairs', '—')} | {row.get('seconds', 0):.1f} | {meaning.get(name, '')} |"
        )

    lines += ["", "## Largest disagreement with the published values", ""]
    for row in results:
        if "max_errors" not in row:
            continue
        errors = row["max_errors"]
        lines += [
            f"### `{row['suite']}` — {row.get('engine', '')}",
            "",
            "| Quantity | Largest error | Threshold |",
            "|---|---:|---:|",
            f"| Symmetric distances | {errors['distance_mm']:.3e} mm | "
            f"{DISTANCE_TOLERANCE_MM:g} mm |",
            f"| APL, both directions | {errors['apl_mm']:.3e} mm | {APL_TOLERANCE_MM:g} mm |",
            f"| NAPL, both directions | {errors['napl']:.3e} | {NAPL_TOLERANCE:g} |",
            "",
            "Measured under "
            + ", ".join(f"`{k}={v}`" for k, v in sorted(row.get("settings", {}).items())),
            "",
        ]

    failures = [line for row in results for line in row.get("failures", [])]
    if failures:
        lines += ["## Failures", "", *(f"- {line}" for line in failures), ""]

    lines += [
        "## Settings",
        "",
        f"- Tolerances: {', '.join(f'{value:g} mm' for value in TOLERANCES_MM)}",
        "- Missing-plane policy: `exclude`, the convention the published study uses",
        "",
        "Per-suite settings are stated with each result above, because they differ.",
        "The reference engine is validated at the sampling step its own published",
        f"acceptance was recorded at ({REFERENCE_ACCEPTANCE_SAMPLING_MM:g} mm), not the",
        f"coarser step it runs at in the application ({REFERENCE_SAMPLING_MM:g} mm). At",
        "the coarser step it disagrees with the audited continuous values by up to",
        "7e-3 mm — inside its own stated interval, outside the suppliers' threshold.",
        "Judging it there would measure the sampling step and call it a defect.",
        "",
        "The thresholds above are the suppliers', not ours.",
        "",
        "## What this does not cover",
        "",
        "These are synthetic shapes with regular slice spacing and clean topology.",
        "They say nothing about vendor structure sets with nested rings, irregular",
        "stacks or empty ROIs; those are covered by `tests/test_contour_grid.py`",
        "and `tests/test_polygon_metrics.py`, against cases taken from real data.",
        "",
    ]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {target}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--suite",
        choices=["public", "stress", "adapter", "dicom", "all"],
        default="all",
        help="Which layer to run. 'all' excludes 'dicom' unless --data is given.",
    )
    parser.add_argument("--data", type=Path, help="Folder holding the published .zip archives.")
    parser.add_argument("--out", type=Path, help="Write a markdown report here.")
    parser.add_argument("--engine", help="Force 'fast' or 'reference'.")
    parser.add_argument(
        "--differential",
        action="store_true",
        help="Also run the published pairs through the second engine (slow).",
    )
    parser.add_argument(
        "--output-dir", type=Path, help="Where supplier suites write their records."
    )
    args = parser.parse_args(argv)

    if not (VENDOR / "native_contour_metrics_fast").is_dir():
        print(f"No vendored package at {VENDOR}", file=sys.stderr)
        return 2
    # Ahead of anything else, so the supplier scripts resolve their own
    # top-level name to the vendored copy rather than an installed release.
    sys.path.insert(0, str(VENDOR))

    if args.suite == "all":
        from autoseg_evaluator.core.polygon_metrics import (
            ENGINE_FAST,
            ENGINE_REFERENCE,
            library_available,
        )

        chosen = ["public", "stress", "adapter"]
        # The second engine is opt-in: it is nine minutes of sampling against
        # thirty seconds, which is worth paying for a release record and not on
        # every push. CI runs everything else.
        if args.differential and library_available() and not args.engine:
            chosen[chosen.index("adapter")] = f"adapter:{ENGINE_FAST}"
            chosen.append(f"adapter:{ENGINE_REFERENCE}")
        if args.data:
            chosen.append("dicom")
    else:
        chosen = [args.suite]
    if "dicom" in chosen and not args.data:
        parser.error("--suite dicom needs --data pointing at the published archives")

    results: list[dict[str, Any]] = []
    for name in chosen:
        if name in ("public", "stress"):
            results.append(
                _run_supplier(name, SUPPLIER / "scripts" / f"validate_{name}.py", args.output_dir)
            )
        elif name.startswith("adapter"):
            _, _, forced = name.partition(":")
            results.append(run_adapter(forced or args.engine))
        else:
            results.append(run_dicom(args.data, args.engine))

    print("\n" + "=" * 72)
    for row in results:
        print(f"  {row['suite']:10s} {'passed' if row.get('passed') else 'FAILED'}")
    if args.out:
        write_report(args.out, results)
    return 0 if all(row.get("passed") for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
