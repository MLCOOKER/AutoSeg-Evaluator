"""AutoSeg's polygon metrics against the analytical shapes of Boukerroui et al.

Boukerroui, Vasquez Osorio, Brunenberg and Gooding (Phys Imaging Radiat Oncol
2023;26:100436) published 150 contour pairs whose metrics are known in closed
form: squares against circles, centred and offset, at three CT resolutions,
and two cuboids. This script asks how close AutoSeg Evaluator, as it runs,
comes to the ideal shapes, to the contours as stored, and to the authors' own
software, and writes Supplementary 3: a short report (aim, method, results,
findings) and its full results.

Four sets of values, kept apart because agreement with each means something else:

analytic
    The ideal shapes' closed-form values: the supplement's MATLAB tables for
    the 16 square/circle pairs of each family, and Tables D.8 and D.11 for the
    cuboids. A stored circle is a polygon, so a metric computed exactly on it
    still differs from the ideal circle's by the polygon's own deviation.
audited
    High-precision values for the stored polygons (``golden_metrics.json``),
    computed in decimal arithmetic by a separate implementation. The difference
    from these is the computation's own error.
authors
    The authors' published empirical results, from their sampled
    implementation (0.05 mm boundary sampling, buffered-polygon APL).
AutoSeg
    This application: the published DICOM files, read by our grid builder and
    parser, measured by the engine the application selects.

Every value is recomputed from the files on each run, and kept so that
``--render-only`` can rewrite the reports without computing again.

Usage::

    python scripts/validate_polygon_analytic.py \\
        --data "<folder holding the DICOM archives and the authors' repository>" \\
        [--csv pairs.csv]
"""

from __future__ import annotations

import argparse
import csv
import math
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "src"))

from validate_polygon_metrics import _golden, _series_grid  # noqa: E402
from validation_common import (  # noqa: E402
    VALIDATION_DOCS,
    load_results,
    long_path,
    md_table,
    run_info,
    save_results,
    sci,
    stamp,
    unwrap,
)

#: The published benchmark is stated at these two tolerances.
TOLERANCES_MM = (1.0, 2.0)
RESOLUTIONS = ("Coarse", "Typical", "Fine")
#: The authors' result files name the resolutions low, normal and high.
AUTHOR_SUFFIX = {"Coarse": "lr", "Typical": "nr", "Fine": "hr"}
FAMILY_LABEL = {
    "cc_vm": "centred",
    "cc_vm_rotated": "centred, rotated",
    "ldv_vm": "offset",
    "cuboid_sparse": "cuboid, sparse test",
    "cuboid_dense": "cuboid, dense test",
}
SQUARE_CIRCLE = ("cc_vm", "cc_vm_rotated", "ldv_vm")

#: Supplement Table D.8, 2D distances for the cuboids, valid on every grid.
#: The reverse mean is printed as 23.499, which is the *symmetric* mean; the
#: reverse directional mean is 24.085 by the suppliers' high-precision
#: calculation, a separate quadrature and the authors' own empirical results
#: (24.085231). The symmetric values below do not depend on which is read.
D8 = {"hd": 40.665, "hd95_a": 31.109, "hd95_b": 34.223, "median_a": 26.2, "median_b": 26.2}
D8_MEAN_A = 22.914
D8_MEAN_B_CORRECTED = 24.085
D8_MEAN_B_AS_PRINTED = 23.499
#: Supplement Table D.11: reference length and its APL at 1 and 2 mm, per grid.
D11 = {
    "Coarse": {"length": 6400.0, 1.0: 6348.0, 2.0: 6296.0},
    "Typical": {"length": 20000.0, 1.0: 19844.0, 2.0: 19688.0},
    "Fine": {"length": 40000.0, 1.0: 39692.0, 2.0: 39384.0},
}

#: The quantities compared, in report order: (key, label, unit).
DISTANCES = (
    ("hd100", "HD100", "mm"),
    ("hd95", "HD95", "mm"),
    ("mean", "Mean distance", "mm"),
    ("median", "Median distance", "mm"),
)
NAPLS = tuple(
    (f"napl_{side}_{tau:g}", f"NAPL {arrow} @ {tau:g} mm", "")
    for tau in TOLERANCES_MM
    for side, arrow in (("a", "reference → test"), ("b", "test → reference"))
)


# ---- The references ---------------------------------------------------------


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def analytic_tables(upstream: Path) -> dict[str, list[dict[str, float]]]:
    """The supplement's closed-form values, 16 rows per family, in pair order."""
    mlab = upstream / "VitruvianPhantomPy" / "mlab"
    tables = {}
    for family, name in (("cc", "CC"), ("ldv", "LdV")):
        rows = _read_csv(mlab / f"{name}_VitruvianMan_shapePaper_theory_results.csv")
        tables[family] = [{k: float(v) for k, v in row.items()} for row in rows]
    return tables


def analytic_values(case: dict[str, Any], tables) -> dict[str, float]:
    """The ideal shapes' values for one pair, combined as the paper combines them.

    HD95 and median take the larger direction, the mean the average of the two
    (Supplement A). APL is reported per direction, as a fraction of the source
    length, since the planes repeat one shape and NAPL does not depend on how
    many there are.
    """
    family = case["family"]
    if family in SQUARE_CIRCLE:
        row = tables["ldv" if family == "ldv_vm" else "cc"][case["number"] - 1]
        values = {
            "hd100": row["hd"],
            "hd95": max(row["hd95da"], row["hd95db"]),
            "mean": 0.5 * (row["mua"] + row["mub"]),
            "median": max(row["hd50da"], row["hd50db"]),
        }
        for tau in TOLERANCES_MM:
            values[f"napl_a_{tau:g}"] = row[f"nAPLA{tau:g}mm"]
            values[f"napl_b_{tau:g}"] = row[f"nAPLB{tau:g}mm"]
        return values
    table = D11[case["resolution"]]
    values = {
        "hd100": D8["hd"],
        "hd95": max(D8["hd95_a"], D8["hd95_b"]),
        "mean": 0.5 * (D8_MEAN_A + D8_MEAN_B_CORRECTED),
        "median": max(D8["median_a"], D8["median_b"]),
    }
    for tau in TOLERANCES_MM:
        values[f"apl_a_{tau:g}"] = table[tau]
        values[f"napl_a_{tau:g}"] = table[tau] / table["length"]
    return values


def author_tables(upstream: Path) -> dict[tuple[str, str], dict[int, dict[str, float]]]:
    """The authors' empirical results, keyed by (resolution, family) then pair."""
    results = upstream / "VitruvianPhantomPy" / "results"
    tables: dict[tuple[str, str], dict[int, dict[str, float]]] = {}
    for resolution, suffix in AUTHOR_SUFFIX.items():
        for family in SQUARE_CIRCLE:
            rows = _read_csv(results / f"score_list_{family}_{suffix}_empirical.csv")
            tables[(resolution, family)] = {
                int(row["Test ID"]): {k: float(v) for k, v in row.items() if _numeric(v)}
                for row in rows
            }
    cuboids = _read_csv(results / "score_list_cuboids_empirical.csv")
    for row in cuboids:
        number = int(row["Test ID"])  # 1..6: coarse, typical, fine; sparse then dense
        resolution = RESOLUTIONS[(number - 1) // 2]
        family = "cuboid_sparse" if number % 2 else "cuboid_dense"
        tables.setdefault((resolution, family), {})[number] = {
            k: float(v) for k, v in row.items() if _numeric(v)
        }
    return tables


def _numeric(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def author_values(case: dict[str, Any], tables) -> dict[str, float]:
    """The authors' directional results, combined as the paper combines them.

    Their Chapter 15 code averages the two directional medians; the paper takes
    the larger, and so does this application, so the larger is used here.
    Their APL is the reference contour's, at 1 mm, summed over planes.
    """
    row = tables[(case["resolution"], case["family"])][case["number"]]
    return {
        "hd100": row["HD"],
        "hd95": max(row["HD95_ref2test"], row["HD95_test2ref"]),
        "mean": 0.5 * (row["AD_ref2test"] + row["AD_test2ref"]),
        "median": max(row["MD_ref2test"], row["MD_test2ref"]),
        "median_chapter15": 0.5 * (row["MD_ref2test"] + row["MD_test2ref"]),
        "apl_a_1": row["APL"],
    }


def author_reruns(package: Path) -> dict[tuple[str, str, int], float]:
    """The authors' APL code, re-run by the present authors: reference → test, 1 mm.

    From ``historical_validation/overlap_apl/comparison.csv``: their published
    function with three mechanical Shapely 2 edits, run on current libraries
    rather than their 2022 environment. Where it gives their published value, a
    difference from the exact value belongs to their method; where it does not,
    the published value is one their own code no longer reproduces.
    """
    path = package / "historical_validation" / "overlap_apl" / "comparison.csv"
    if not path.is_file():
        return {}
    return {
        (row["resolution"], row["family"], int(row["number"])): float(row["apl_a_chapter_mm"])
        for row in _read_csv(path)
        if float(row["tolerance_mm"]) == 1.0
    }


#: The authors' results are printed to six decimals; a re-run within this of a
#: published value reproduces it.
REPRODUCED_MM = 1e-6


def apl_disagreements(records) -> list[dict[str, Any]]:
    """The pairs whose 1 mm APL differs from the authors' by more than 0.001 mm."""
    out = []
    for record in records:
        ours = record["autoseg"]["apl_a_1"]
        published = record["authors"]["apl_a_1"]
        if abs(ours - published) <= 1e-3:
            continue
        rerun = record["authors"].get("apl_a_1_rerun")
        if rerun is None:
            why = "no re-run"
        elif abs(rerun - published) <= REPRODUCED_MM:
            why = "their method"
        else:
            why = "not reproduced"
        out.append(
            {
                "record": record,
                "ours": ours,
                "published": published,
                "rerun": rerun,
                "exact": record["audited"]["apl_a_1"],
                "why": why,
            }
        )
    return sorted(out, key=lambda d: -abs(d["ours"] - d["published"]))


def audited_values(case: dict[str, Any]) -> dict[str, float]:
    distance = case["distance"]
    values = {
        "hd100": distance["hd_mm"],
        "hd95": distance["hd95_mm"],
        "mean": distance["mean_mm"],
        "median": distance["median_mm"],
    }
    for tau in TOLERANCES_MM:
        for side in ("a", "b"):
            record = case["apl"][f"{tau}"][side]
            values[f"apl_{side}_{tau:g}"] = record["apl_mm"]
            values[f"napl_{side}_{tau:g}"] = record["napl"]
    return values


# ---- This application --------------------------------------------------------


def autoseg_values(values: dict[str, float]) -> dict[str, float]:
    """Our result columns, renamed to the keys the references use."""
    from autoseg_evaluator.core.tolerance_keys import tolerance_key

    out = {
        "hd100": values["poly_hd100_mm"],
        "hd95": values["poly_hd95_mm"],
        "mean": values["poly_mean_distance_mm"],
        "median": values["poly_median_distance_mm"],
        "joint_planes": values["poly_planes_joint"],
    }
    for tau in TOLERANCES_MM:
        out[f"apl_a_{tau:g}"] = values[tolerance_key("poly_apl_mm", tau)]
        out[f"apl_b_{tau:g}"] = values[tolerance_key("poly_apl_reverse_mm", tau)]
        out[f"napl_a_{tau:g}"] = values[tolerance_key("poly_napl", tau)]
        out[f"napl_b_{tau:g}"] = values[tolerance_key("poly_napl_reverse", tau)]
    return out


def measure_all(archives: Path, engine_name: str | None) -> tuple[list[dict[str, Any]], str]:
    """Every published pair, from its DICOM files, as the application measures it."""
    import pydicom

    from autoseg_evaluator.core.polygon_metrics import (
        compare_structures,
        parse_structure,
        select_engine,
    )

    engine = select_engine(engine_name)
    cases = _golden()
    records = []
    for resolution in RESOLUTIONS:
        group = [case for case in cases if case["resolution"] == resolution]
        archive = archives / f"{resolution}Resolution.zip"
        if not archive.is_file():
            raise FileNotFoundError(f"Missing published archive: {archive}")
        scratch = Path(tempfile.mkdtemp(prefix=f"polygon-analytic-{resolution.lower()}-"))
        try:
            print(f"  {resolution}: extracting {archive.name} ...", flush=True)
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(scratch)
            grids: dict[str, Any] = {}
            for case in group:
                grid = _series_grid(scratch, case["reference_file"].split("/")[0], grids)
                regions = []
                for key in ("reference_file", "test_file"):
                    dataset = pydicom.dcmread(str(scratch / case[key]))
                    roi = int(dataset.StructureSetROISequence[0].ROINumber)
                    regions.append(parse_structure(dataset, roi, grid))
                started = time.perf_counter()
                result = compare_structures(*regions, tolerance_mm=TOLERANCES_MM, engine=engine)
                seconds = time.perf_counter() - started
                if not result.available:
                    raise RuntimeError(f"{case['id']}: {result.status}")
                records.append(
                    {"case": case, "autoseg": autoseg_values(result.values), "seconds": seconds}
                )
            print(f"  {resolution}: {len(group)} pairs measured", flush=True)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    return records, engine.label


# ---- Reports ------------------------------------------------------------------

SCRIPT = Path(__file__).name
TITLE = "Supplementary 3 - 2D Polygon Metric Validation Report"
REPORT = VALIDATION_DOCS / "Supplementary_3_2D_Polygon_Metric_Validation_Report.md"
FULL_RESULTS = REPORT.with_name(REPORT.stem + "_Full_Results.md")
#: The name this script's results are cached under (``validation_common``).
RESULTS_NAME = "polygon_analytic"
REPOSITORY = "https://github.com/Vitruvian-phantom-for-RadOnc/VitruvianPhantomPy"
GRIDS = (
    "fine (0.5 mm pixels, 0.5 mm planes), typical (0.96 mm, 1 mm) and coarse (1.8 × 2.2 mm, 3 mm)"
)


def _fmt(value: float, digits: int = 4) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    if value == 0:
        return "0"
    if abs(value) < 10 ** -(digits - 1):
        return f"{value:.1e}"
    return f"{value:.{digits}f}"


def _table(headers: list[str], rows: list[list[str]], *, numeric: bool = True) -> list[str]:
    """A markdown table; ``numeric`` right-aligns every column after the first."""
    return md_table(headers, rows, "l" + ("r" if numeric else "l") * (len(headers) - 1))


def _errors(records, key: str, against: str, where) -> list[float]:
    out = []
    for record in records:
        if not where(record["case"]):
            continue
        ours, theirs = record["autoseg"].get(key), record[against].get(key)
        if ours is not None and theirs is not None:
            out.append(ours - theirs)
    return out


def _reference_errors(records, key: str, where) -> list[float]:
    """The stored polygon's own departure from the ideal shape, by the high-precision values."""
    return [
        record["audited"][key] - record["analytic"][key]
        for record in records
        if where(record["case"]) and key in record["analytic"] and key in record["audited"]
    ]


def _max_abs(values: list[float]) -> float:
    return max((abs(v) for v in values), default=float("nan"))


def _square_circle_at(resolution: str):
    def where(case: dict) -> bool:
        return case["resolution"] == resolution and case["family"] in SQUARE_CIRCLE

    return where


def _everywhere(_case: dict) -> bool:
    return True


#: Every APL and NAPL the high-precision values give, both directions, both tolerances.
OVERLAPS = tuple(
    (f"{kind}_{side}_{tau:g}", f"{kind.upper()} {arrow} @ {tau:g} mm", unit)
    for tau in TOLERANCES_MM
    for side, arrow in (("a", "reference → test"), ("b", "test → reference"))
    for kind, unit in (("apl", "mm"), ("napl", ""))
)


def headline(records) -> dict[str, Any]:
    """The numbers both reports state, each computed from the records."""
    square = [r for r in records if r["case"]["family"] in SQUARE_CIRCLE]
    sparse = [r for r in records if r["case"]["family"] == "cuboid_sparse"]
    apl_gaps = [abs(r["autoseg"]["apl_a_1"] - r["authors"]["apl_a_1"]) for r in records]
    disagreements = apl_disagreements(records)
    distances = [key for key, _l, _u in DISTANCES]
    # Whether AutoSeg's departure from the ideal shapes is the stored polygon's own, to
    # the four decimals the reports print, for every quantity and grid.
    same_as_stored = all(
        _fmt(_max_abs(_errors(records, key, "analytic", _square_circle_at(res))))
        == _fmt(_max_abs(_reference_errors(records, key, _square_circle_at(res))))
        for key, _l, _u in DISTANCES + NAPLS
        for res in RESOLUTIONS
    )
    return {
        "computation_distance": max(
            _max_abs(_errors(records, k, "audited", _everywhere)) for k in distances
        ),
        "computation_apl": max(
            _max_abs(_errors(records, k, "audited", _everywhere))
            for k, _l, unit in OVERLAPS
            if unit == "mm"
        ),
        "computation_napl": max(
            _max_abs(_errors(records, k, "audited", _everywhere))
            for k, _l, unit in OVERLAPS
            if unit == ""
        ),
        "shape": {
            res: max(
                _max_abs(_errors(records, k, "analytic", _square_circle_at(res))) for k in distances
            )
            for res in RESOLUTIONS
        },
        "same_as_stored": same_as_stored,
        "sparse_distance": max(
            abs(r["autoseg"][k] - r["analytic"][k]) for r in sparse for k in distances
        ),
        "sparse_apl": max(
            abs(r["autoseg"][f"apl_a_{tau:g}"] - r["analytic"][f"apl_a_{tau:g}"])
            for r in sparse
            for tau in TOLERANCES_MM
        ),
        "authors_distance": max(
            _max_abs(_errors(records, k, "authors", _everywhere)) for k in distances
        ),
        "apl_agree": sum(g <= 1e-3 for g in apl_gaps),
        "apl_pairs": len(apl_gaps),
        "apl_worst": max(apl_gaps),
        "apl_relative": max(
            (abs(d["ours"] - d["published"]) / d["ours"] * 100 for d in disagreements),
            default=0.0,
        ),
        "apl_exact": max(
            (abs(d["ours"] - d["exact"]) for d in disagreements),
            default=0.0,
        ),
        "by_method": sum(d["why"] == "their method" for d in disagreements),
        "not_reproduced": sum(d["why"] == "not reproduced" for d in disagreements),
        "disagreements": disagreements,
        "medians": max(
            abs(r["authors"]["median"] - r["authors"]["median_chapter15"]) for r in records
        ),
        "relative": {
            key: {
                res: max(
                    (
                        abs(r["autoseg"][key] - r["analytic"][key]) / r["analytic"][key] * 100
                        for r in square
                        if r["case"]["resolution"] == res and r["analytic"][key]
                    ),
                    default=0.0,
                )
                for res in RESOLUTIONS
            }
            for key in distances
        },
    }


#: How each metric's two directions are combined: (metric, per direction, reported as, planes).
CONVENTIONS = [
    [
        "Hausdorff 100 %",
        "The largest distance from one boundary to the other",
        "The larger of the two directions",
        "Planes both structures have",
    ],
    [
        "Hausdorff 95 %",
        "The 95th percentile of distance, weighted by boundary length",
        "The larger of the two directions",
        "Planes both structures have",
    ],
    [
        "Median distance",
        "The 50th percentile of distance, weighted by boundary length",
        "The larger of the two directions",
        "Planes both structures have",
    ],
    [
        "Mean distance",
        "The average distance, weighted by boundary length",
        "The average of the two directions",
        "Planes both structures have",
    ],
    [
        "APL",
        "The length of one boundary farther than τ from the other; a point exactly τ away"
        " counts as matched",
        "Reference → test, in mm; test → reference as a separate column",
        "Every plane of the boundary measured; planes the other structure lacks count in full",
    ],
    [
        "NAPL",
        "APL as a fraction of that boundary's length",
        "Total APL ÷ total boundary length over all planes; each direction separately",
        "As APL",
    ],
]


def write_report(target: Path, data: dict) -> None:
    """Supplementary 3: what was validated, how, what was found, and what it decided."""
    records = data["records"]
    h = headline(records)
    n = len(records)
    per_grid = n // len(RESOLUTIONS)
    n_square = sum(1 for r in records if r["case"]["family"] in SQUARE_CIRCLE) // len(RESOLUTIONS)
    sparse = sorted(
        (r for r in records if r["case"]["family"] == "cuboid_sparse"),
        key=lambda r: RESOLUTIONS.index(r["case"]["resolution"]),
    )
    lines = [
        f"# {TITLE}",
        "",
        f"{stamp(SCRIPT, data['run'])}. Every table behind this report is in its "
        f"[full results]({FULL_RESULTS.name}).",
        "",
        "## Aim",
        "",
        "AutoSeg Evaluator v3 computes a second set of metrics directly on the contours stored",
        "in the RTSTRUCT, plane by plane, without converting them to a binary mask: the",
        "Hausdorff distance (100 % and 95 %), the mean and median contour distance, and the",
        "added path length (APL) with its normalised form (NAPL). The authors implemented them",
        "from the mathematical definitions of Boukerroui et al. [1] (Supplement A), as a",
        "compiled engine with a pure-Python engine of the same definitions as its fallback.",
        "This validation asks three questions:",
        "",
        "1. Does AutoSeg compute these metrics exactly as defined, on the contours as stored?",
        "2. How close are its results to the exact values for the ideal shapes, and to the",
        "   results of Boukerroui et al.'s own software, on the same data?",
        "3. Does it combine the two directions of each metric as the paper does?",
        "",
        "## Method",
        "",
        "**Data.** The synthetic shapes Boukerroui et al. published with the paper [2], as",
        f"DICOM RTSTRUCT files. {per_grid}"
        f" pairs of a reference and a test shape: {n_square} pairs of a square and a circle",
        "(16 centred, 16 rotated by 45° and 16 offset, varying in size and in the ratio of",
        f"circle to square) and {per_grid - n_square} pairs of cuboids. Each was",
        f"provided on three CT grids, {GRIDS}: {n} pairs in all. AutoSeg read every file with",
        "its own DICOM reader and measured it with the engine it selects, exactly as in a",
        "computation run.",
        "",
        "**References.** Three, because agreement with each shows something different:",
        "",
        *_table(
            ["Reference", "What it is", "What agreement shows"],
            [
                [
                    "Exact values for the ideal shapes",
                    "The closed-form values published with the dataset [3], and Supplement"
                    " Tables D.8 and D.11 for the cuboids",
                    "How close AutoSeg comes to the ideal shape, including the error of storing"
                    " a circle as a polygon",
                ],
                [
                    "High-precision values for the stored contours",
                    "The metrics of the contours exactly as stored in the files, computed by the"
                    " authors with a separate implementation in high-precision decimal arithmetic",
                    "That the computation itself is right",
                ],
                [
                    "Boukerroui et al.'s software",
                    "The paper's published empirical results, from the authors' own scoring"
                    " code [3]",
                    "Agreement with the implementation the paper validated",
                ],
            ],
            numeric=False,
        ),
        "**Quantities.** HD100, HD95, mean and median distance; APL and NAPL at τ = 1 and 2 mm,",
        "in both directions.",
        "",
        "**What AutoSeg reports.** Each metric is measured in two directions, from the",
        "reference boundary to the test and from the test to the reference, and combined as",
        "follows:",
        "",
        *_table(
            ["Metric", "Measured in each direction as", "Reported as", "Planes used"],
            CONVENTIONS,
            numeric=False,
        ),
        "## Results",
        "",
        "**The computation.** Largest difference from the high-precision values for the stored",
        f"contours, over all {n} pairs: {sci(h['computation_distance'])} mm for any distance,",
        f"{sci(h['computation_apl'])} mm for any APL and {sci(h['computation_napl'])}"
        " for any NAPL.",
        "",
        f"**The ideal squares and circles.** Largest difference from the exact values, over the"
        f" {n_square} square and circle pairs on each grid:",
        "",
        *_table(
            [
                "Grid",
                "HD100 (mm)",
                "HD95 (mm)",
                "Mean (mm)",
                "Median (mm)",
                *(
                    label.replace("reference → test", "ref→test").replace(
                        "test → reference", "test→ref"
                    )
                    for _k, label, _u in NAPLS
                ),
            ],
            [
                [
                    res,
                    *(
                        _fmt(_max_abs(_errors(records, key, "analytic", _square_circle_at(res))))
                        for key, _l, _u in DISTANCES + NAPLS
                    ),
                ]
                for res in RESOLUTIONS
            ],
        ),
    ]
    if h["same_as_stored"]:
        lines += [
            "For every quantity on every grid, the high-precision values for the stored contours",
            "differ from the exact values by the same amount, to the four decimals shown.",
            "",
        ]
    lines += [
        "**The cuboids.** The sparse test cuboid is stored exactly, plane by plane. AutoSeg's",
        "values, with their difference from the exact values in brackets (mm):",
        "",
        *_table(
            ["Grid", "HD100", "HD95", "Mean", "Median", "APL @ 1 mm", "APL @ 2 mm"],
            [
                [
                    r["case"]["resolution"],
                    *(
                        f"{_fmt(r['autoseg'][key], 3)} ({_fmt(r['autoseg'][key] - r['analytic'][key], 2)})"
                        for key in ("hd100", "hd95", "mean", "median", "apl_a_1", "apl_a_2")
                    ),
                ]
                for r in sparse
            ],
        ),
        "**Boukerroui et al.'s software.** Their published distances differ from AutoSeg's by at",
        f"most {_fmt(h['authors_distance'], 3)} mm over all {n} pairs. Their APL at 1 mm agrees"
        f" with AutoSeg's to within 0.001 mm on {h['apl_agree']} of {h['apl_pairs']} pairs; the"
        f" other {len(h['disagreements'])} differ by at most {_fmt(h['apl_worst'], 3)} mm, or"
        f" {h['apl_relative']:.3f} % of the pair's APL.",
        "",
        "## Findings",
        "",
        "- **AutoSeg computes the 2D metrics exactly as defined.** On the contours as stored, it"
        f" agrees with the high-precision values to within {sci(h['computation_distance'])}"
        " mm.",
    ]
    fine = h["shape"]["Fine"]
    coarse = h["shape"]["Coarse"]
    if h["same_as_stored"]:
        lines += [
            "- **Its differences from the ideal shapes belong to the shapes as stored.** They reach"
            f" {_fmt(coarse, 4)} mm on the coarse grid and {_fmt(fine, 4)} mm on the fine one, and",
            "  each equals the difference between the stored contour and the ideal shape. A",
            "  circle stored in an RTSTRUCT is a polygon: its corners lie on the circle and its",
            "  straight edges cut slightly inside it, so its metrics differ from the ideal",
            "  circle's by an amount that shrinks with the pixel size. A box can be stored",
            f"  exactly, and on the sparse cuboid AutoSeg's distances are within"
            f" {sci(h['sparse_distance'])} mm of the exact values, inside the precision the",
            "  supplement prints" + (", and its APL is exact." if h["sparse_apl"] < 1e-6 else "."),
        ]
    lines += [
        "- **It agrees with Boukerroui et al.'s own software within that software's sampling.**"
        " Their code measures distances from points taken every 0.05 mm along each contour, so"
        " differences of hundredths of a millimetre are expected. Their APL is an approximation"
        " of the paper's definition: on"
        f" {h['by_method']} of the {len(h['disagreements'])} pairs where it differs, re-running"
        " their code gives their published value, which differs from the exact one; on"
        f" {h['not_reproduced']}, their code no longer reproduces its published value. On every"
        f" one, AutoSeg matches the exact value to {sci(h['apl_exact'])} mm.",
        "- **It combines the two directions as the paper does.** The larger direction for the",
        "  Hausdorff distances and the median, the average for the mean, and each APL direction",
        "  on its own; these are also the conventions of AutoSeg's 3D metrics (Supplementary 4).",
        "  Boukerroui et al.'s published scoring code averages the two directional medians",
        f"  instead of taking the larger; on these pairs the two differ by up to {h['medians']:.2f}"
        " mm, and AutoSeg follows the paper.",
        "- **Two details of the published data.** Supplement Table D.8 prints the cuboid's",
        f"  reverse mean as {D8_MEAN_B_AS_PRINTED} mm, which is the symmetric mean; the reverse",
        f"  directional mean is {D8_MEAN_B_CORRECTED} mm, as the authors' own empirical result"
        " confirms. And the",
        "  rotated family rotates the circle's polygon by 45 radians rather than 45 degrees,",
        "  which for a centred circle moves only its vertices, so the family does not test",
        "  rotation invariance.",
        "- **Why metrics on the contours.** They measure the contours themselves, so they carry",
        "  none of the error of converting a contour to a binary mask (Supplementary 2), which",
        "  matters most for small structures.",
        "- **Limits.** The shapes are convex, single contours on regular grids; holes, nested and",
        "  overlapping contours, missing planes and uneven slice spacing are covered by unit",
        "  tests on cases taken from real structure sets. The high-precision values come from",
        "  the same authors as AutoSeg's engines, so they verify the arithmetic; agreement with",
        "  the exact values for the ideal shapes, and with Boukerroui et al.'s software, verifies",
        "  the definitions.",
        "",
        "## References",
        "",
        "1. Boukerroui D, Vasquez Osorio E, Brunenberg E, Gooding MJ. Analytic calculations and",
        "   synthetic shapes for validation of quantitative contour comparison software. Phys",
        "   Imaging Radiat Oncol. 2023;26:100436. doi:10.1016/j.phro.2023.100436",
        "2. The synthetic shapes of [1]. Mendeley Data. doi:10.17632/9xjyrftzth.1 (CC BY 4.0)",
        f"3. VitruvianPhantomPy: the code and results of [1]. GitHub. {REPOSITORY}",
        "",
        "## Reproduce",
        "",
        "```",
        f"python scripts/{SCRIPT} --data <folder holding the dataset and the authors' repository>",
        "```",
        "",
        f"The run took {data['seconds']:.0f} s; `--render-only` rewrites this report from the last",
        "run's results. Regenerate this report rather than editing it.",
        "",
    ]
    target.write_text(unwrap(lines), encoding="utf-8")


def write_full_results(target: Path, data: dict) -> None:
    """Every table behind Supplementary 3."""
    records = data["records"]
    h = headline(records)
    lines = [
        f"# {TITLE}: full results",
        "",
        f"{stamp(SCRIPT, data['run'])} · engine {data['engine']} · {len(records)} pairs from"
        f" the published DICOM files · {data['seconds']:.0f} s. The summary is"
        f" [Supplementary 3]({REPORT.name}). Regenerate this file rather than editing it.",
        "",
        "## What is compared",
        "",
        *_table(
            ["Reference", "What it is", "What agreement shows"],
            [
                [
                    "Analytical",
                    "Closed-form values for ideal squares, circles and cuboids (the supplement's"
                    " tables)",
                    "How far a result is from the ideal shape, including the polygon's own"
                    " departure from it",
                ],
                [
                    "High-precision",
                    "Values for the contours as stored in the RTSTRUCTs, computed in"
                    " high-precision decimal arithmetic",
                    "That the computation itself is right",
                ],
                [
                    "Authors' software",
                    "The paper's empirical results, from the authors' scoring code"
                    " (VitruvianPhantomPy `score_autocontours_lib.py`, adapted from Gooding's"
                    " Chapter 15 code)",
                    "Agreement with the implementation the paper validated",
                ],
            ],
            numeric=False,
        ),
        "AutoSeg's values come from the published DICOM archives, read by this",
        "application's grid builder and structure parser, and measured by the engine",
        "it selects, exactly as a computation run does. The 150 pairs are 16 centred",
        "square/circle pairs, 16 rotated, 16 offset and two cuboid pairs, at each of",
        "three resolutions: coarse (1.8 × 2.2 mm, 3 mm planes), typical (0.96 mm, 1 mm)",
        "and fine (0.5 mm, 0.5 mm).",
        "",
        "## Against the ideal shapes: squares and circles",
        "",
        "Largest absolute difference over the 16 pairs of each family, AutoSeg",
        "against the analytical value. Beside it, the same for the high-precision values",
        "of the stored polygons: where the two agree, the whole difference is the",
        "stored polygon's, not the computation's.",
        "",
    ]
    rows = []
    for key, label, unit in DISTANCES + NAPLS:
        for resolution in RESOLUTIONS:
            where = _square_circle_at(resolution)
            rows.append(
                [
                    label if resolution == "Coarse" else "",
                    resolution,
                    _fmt(_max_abs(_errors(records, key, "analytic", where))),
                    _fmt(_max_abs(_reference_errors(records, key, where))),
                    unit or "fraction",
                ]
            )
    lines += _table(
        ["Quantity", "Grid", "AutoSeg − analytic", "Stored polygon − analytic", "Unit"], rows
    )
    lines += [
        "Largest *relative* difference in each distance over the square/circle",
        "pairs, as a percentage of the analytical value:",
        "",
        *_table(
            ["Quantity", *RESOLUTIONS],
            [
                [label, *(f"{h['relative'][key][res]:.2f} %" for res in RESOLUTIONS)]
                for key, label, _u in DISTANCES
            ],
        ),
        "By family, largest absolute distance difference from the analytical value (mm):",
        "",
        *_table(
            ["Family", *[label for _k, label, _u in DISTANCES]],
            [
                [
                    FAMILY_LABEL[family],
                    *(
                        _fmt(
                            _max_abs(
                                _errors(
                                    records, key, "analytic", lambda c, f=family: c["family"] == f
                                )
                            )
                        )
                        for key, _l, _u in DISTANCES
                    ),
                ]
                for family in SQUARE_CIRCLE
            ],
        ),
        "The rotated family does not test rotation invariance: the published files",
        "rotate the square by 45 degrees but the circle's polygon by 45 radians. For a",
        "centred circle this moves only its vertices, so the analytical values of the",
        "centred family still apply.",
        "",
    ]

    cuboids = sorted(
        (r for r in records if r["case"]["family"].startswith("cuboid")),
        key=lambda r: (RESOLUTIONS.index(r["case"]["resolution"]), r["case"]["family"]),
    )
    lines += [
        "## Against the ideal shapes: the cuboids",
        "",
        "The sparse test cuboid stores each plane's rectangle exactly, so there the",
        "analytical values are the ground truth for the contours as drawn, to the",
        "precision the supplement prints (Tables D.8 and D.11). The dense test cuboid",
        "is sampled at pixel boundaries, which clips its corners; the supplement notes",
        "its analytical values are not attainable.",
        "",
        *_table(
            ["Grid", "Pair", "HD100", "HD95", "Mean", "Median", "APL @ 1 mm", "APL @ 2 mm"],
            [
                [
                    r["case"]["resolution"],
                    FAMILY_LABEL[r["case"]["family"]],
                    *(
                        f"{_fmt(r['autoseg'][key], 3)} ({_fmt(r['autoseg'][key] - r['analytic'][key], 3)})"
                        for key in ("hd100", "hd95", "mean", "median", "apl_a_1", "apl_a_2")
                    ),
                ]
                for r in cuboids
            ],
        ),
        "Each cell: AutoSeg's value in mm, and in brackets its difference from the",
        "analytical value. Analytical: HD100 40.665, HD95 34.223 (the larger",
        "direction), median 26.2, symmetric mean 23.4995 mm; APL from Table D.11.",
        "",
        f"Table D.8 prints the reverse mean as {D8_MEAN_B_AS_PRINTED}, which is the symmetric",
        f"mean; the reverse directional mean is {D8_MEAN_B_CORRECTED} mm (the high-precision",
        "values, a separate quadrature, and the authors' own empirical result of 24.085231).",
        "The symmetric mean compared here does not depend on which is read: taking the",
        f"printed value as a direction would give {0.5 * (D8_MEAN_A + D8_MEAN_B_AS_PRINTED):.4f}"
        " mm instead.",
        "",
        "## Against the authors' software",
        "",
        "The paper's *empirical* results come from the authors' own scoring code:",
        "`score_autocontours_lib.py` in their VitruvianPhantomPy repository (Boukerroui",
        "and Gooding, Mirada Medical), adapted in 2022 from Gooding's",
        "`score_autocontours.py` for Chapter 15 of *Auto-segmentation for Radiation",
        "Oncology*. The repository publishes those results as `results/*_empirical.csv`,",
        "and they are compared here as published. For distances the code takes a point",
        "every 0.05 mm along each contour, from its start, measures each point's distance",
        "to the other contour, and reads HD95 and the median as NumPy percentiles of those",
        "points and the mean as their average. Differences of a few hundredths of a",
        "millimetre are that sampling, not an error on either side.",
        "",
        "Largest absolute difference from their published results, over all 150 pairs:",
        "",
    ]
    rows = []
    for key, label, _unit in DISTANCES:
        diffs = _errors(records, key, "authors", _everywhere)
        rows.append(
            [label, _fmt(_max_abs(diffs)), _fmt(sorted(abs(d) for d in diffs)[len(diffs) // 2])]
        )
    lines += _table(["Quantity", "Largest difference (mm)", "Median difference (mm)"], rows)
    why_label = {
        "their method": "their method",
        "not reproduced": "not reproduced by their code",
        "no re-run": "–",
    }
    disagreements = h["disagreements"]
    lines += [
        f"**APL at 1 mm** (reference → test, summed over planes): {h['apl_agree']} of"
        f" {h['apl_pairs']} pairs agree with the authors' value to within 0.001 mm. The others:",
        "",
        *_table(
            [
                "Pair",
                "AutoSeg (mm)",
                "Published (mm)",
                "Their code, re-run (mm)",
                "AutoSeg − published (mm)",
                "Exact − AutoSeg (mm)",
                "Difference due to",
            ],
            [
                [
                    d["record"]["case"]["id"],
                    _fmt(d["ours"], 4),
                    _fmt(d["published"], 4),
                    _fmt(d["rerun"], 4) if d["rerun"] is not None else "–",
                    _fmt(d["ours"] - d["published"], 4),
                    _fmt(d["exact"] - d["ours"], 2),
                    why_label[d["why"]],
                ]
                for d in disagreements
            ],
        ),
    ]
    closer = [
        d
        for d in disagreements
        if d["why"] == "not reproduced"
        and abs(d["rerun"] - d["exact"]) < abs(d["published"] - d["exact"])
    ]
    method_worst = max(
        (abs(d["published"] - d["exact"]) for d in disagreements if d["why"] == "their method"),
        default=0.0,
    )
    lines += [
        "*Exact* is the high-precision value for the stored polygons; AutoSeg is within",
        f"{_fmt(h['apl_exact'], 2)} mm of it on every one of these pairs. *Their code, re-run* is",
        "the authors' APL function re-run by the present authors (with three mechanical",
        "edits for Shapely 2, on current libraries rather than the 2022 environment).",
        "",
        "Their APL is an approximation of the paper's definition. It widens the test",
        "contour by τ and shrinks it by τ, cuts the reference contour where it crosses",
        "those two outlines, and counts a piece as added if its midpoint lies outside",
        "the widened outline or inside the shrunk one. The widened outline's rounded",
        "corners are polygons of 32 segments per quarter circle rather than arcs; each",
        "piece is judged by its midpoint alone; a point exactly τ away counts as",
        "unmatched, where the paper (A.13) counts it as matched; and when a cut fails",
        "on parallel edges the outline is nudged by a random amount of up to",
        "0.0001 mm and the cut retried. AutoSeg computes the paper's definition on",
        "the stored contours exactly.",
        "",
        f"- **{h['by_method']} of these pairs are their method's approximation.** Re-running their"
        " code gives exactly their published value, and that value differs from the exact one,"
        f" by at most {_fmt(method_worst, 3)} mm.",
        f"- **{h['not_reproduced']} are published values their own code no longer reproduces.**"
        f" On {'all' if len(closer) == h['not_reproduced'] else len(closer)} of them the re-run"
        " lands closer to the exact value than the published figure. Different library versions"
        " and the random retry are both possible causes.",
        "",
        "In a degenerate case the difference is large. An axis-aligned square",
        "[-5, 5]² against [-3, 3]² at τ = 2 mm has 16 mm of reference boundary",
        "farther than τ from the test, by the paper's definition; the authors' code",
        "returns 40 mm, because the reference's straight edges lie exactly on the",
        "widened outline and count as unmatched. None of the published pairs is",
        "degenerate like this, and on them the differences are at most",
        f"{h['apl_relative']:.3f} % of a pair's APL, which is summed over every plane.",
        "",
        "The authors' published Chapter 15 code combines the two directional medians",
        "by averaging them; the paper, the high-precision values and AutoSeg take the",
        f"larger. On these pairs the two conventions differ by up to {h['medians']:.3f} mm, so",
        "the comparison above composes the authors' directional medians the paper's",
        "way.",
        "",
        "## The computation itself",
        "",
        "Largest absolute difference from the high-precision values for the stored",
        "polygons, over all 150 pairs and both directions of APL:",
        "",
        *_table(
            ["Quantity", "Largest difference", "Unit"],
            [
                [label, _fmt(_max_abs(_errors(records, key, "audited", _everywhere)), 2), unit]
                for key, label, unit in DISTANCES + OVERLAPS
            ],
        ),
        "`scripts/validate_polygon_metrics.py` checks both engines against the same values,",
        "and against 44 geometric edge cases, on every release.",
        "",
        "## Conventions",
        "",
        *_table(
            ["Metric", "Measured in each direction as", "Reported as", "Planes used"],
            CONVENTIONS,
            numeric=False,
        ),
        "These are the paper's definitions (Supplement A). Points are weighted by",
        "arc length, not by vertex, so adding vertices along an edge changes nothing.",
        "Percentiles are the lower generalised inverse of the length-weighted",
        "distribution.",
        "",
        "## What this does not cover",
        "",
        "The shapes are convex, single-loop and on regular grids. Holes, nested and",
        "XOR contours, missing planes and uneven stacks are covered by the unit tests",
        "(`tests/test_contour_grid.py`, `tests/test_polygon_metrics.py`) against cases",
        "taken from real structure sets, not by this benchmark. These are planar",
        "measurements, not 3D surface distances.",
        "",
        "## Sources",
        "",
        "Boukerroui D, Vasquez Osorio E, Brunenberg E, Gooding MJ. Analytic",
        "calculations and synthetic shapes for validation of quantitative contour",
        "comparison software. *Phys Imaging Radiat Oncol* 2023;26:100436.",
        "doi:10.1016/j.phro.2023.100436. Dataset: doi:10.17632/9xjyrftzth.1 (CC BY 4.0).",
        "",
        "Analytical values: `VitruvianPhantomPy/mlab/*_theory_results.csv` (commit",
        f"`cc4106a`, {REPOSITORY}) and Supplement Tables D.8 and D.11. High-precision",
        "values: `third_party/native_contour_metrics/v0.2/data/golden_metrics.json`.",
        "",
    ]
    target.write_text(unwrap(lines), encoding="utf-8")


def render(data: dict) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    write_report(REPORT, data)
    write_full_results(FULL_RESULTS, data)
    print(f"wrote {REPORT} and {FULL_RESULTS.name}")


def write_csv(target: Path, records) -> None:
    keys = [k for k, _l, _u in DISTANCES] + [
        f"{kind}_{side}_{tau:g}"
        for tau in TOLERANCES_MM
        for side in ("a", "b")
        for kind in ("apl", "napl")
    ]
    with target.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            ["pair", "resolution", "family", "number"]
            + [
                f"{source}_{key}"
                for source in ("autoseg", "analytic", "audited", "authors")
                for key in keys
            ]
        )
        for record in records:
            case = record["case"]
            row = [case["id"], case["resolution"], case["family"], case["number"]]
            for source in ("autoseg", "analytic", "audited", "authors"):
                row += [record[source].get(key, "") for key in keys]
            writer.writerow(row)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data",
        "--package",
        type=Path,
        help="The folder holding the published DICOM archives and the authors' repository.",
    )
    parser.add_argument("--csv", type=Path, help="Write every pair's values here.")
    parser.add_argument("--engine", help="Force 'fast' or 'reference'.")
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="Rewrite the reports from the last run's results, without computing.",
    )
    args = parser.parse_args(argv)
    if args.render_only:
        render(load_results(RESULTS_NAME))
        return 0
    if args.data is None:
        parser.error("--data is required unless --render-only")

    info = run_info()
    reference = long_path(args.data) / "reference_v0_1"
    upstream = reference / "upstream"
    started = time.perf_counter()
    records, engine = measure_all(reference / "data" / "original", args.engine)
    analytic = analytic_tables(upstream)
    authors = author_tables(upstream)
    reruns = author_reruns(long_path(args.data))
    for record in records:
        case = record["case"]
        record["analytic"] = analytic_values(case, analytic)
        record["authors"] = author_values(case, authors)
        record["authors"]["apl_a_1_rerun"] = reruns.get(
            (case["resolution"], case["family"], case["number"])
        )
        record["audited"] = audited_values(case)
    elapsed = time.perf_counter() - started
    print(f"{len(records)} pairs in {elapsed:.0f} s through {engine}")
    data = {"run": info, "seconds": elapsed, "engine": engine, "records": records}
    print(f"kept the results in {save_results(RESULTS_NAME, data)}")
    render(data)
    if args.csv:
        write_csv(args.csv, records)
        print(f"wrote {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
