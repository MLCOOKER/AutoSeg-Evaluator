"""AutoSeg's polygon metrics against the analytical shapes of Boukerroui et al.

Boukerroui, Vasquez Osorio, Brunenberg and Gooding (Phys Imaging Radiat Oncol
2023;26:100436) published 150 contour pairs whose metrics are known in closed
form: squares against circles, centred and offset, at three CT resolutions,
and two cuboids. ``validate_polygon_metrics.py`` checks this application
against audited values for the polygons *as stored*. This script asks the
question a reader of a paper asks instead: how close does AutoSeg Evaluator,
as it runs, come to the ideal shapes, and to the authors' own software?

Four references, kept apart because agreement with each means something else:

analytic
    The ideal shapes' closed-form values: the supplement's MATLAB tables for
    the 16 square/circle pairs of each family, and Tables D.8 and D.11 for the
    cuboids. A stored circle is a polygon, so a metric computed exactly on it
    still differs from the ideal circle's by the polygon's own deviation.
audited
    High-precision values for the stored polygons (``golden_metrics.json``,
    from the metric suppliers' review package). The difference from these is
    the computation's own error.
authors
    The authors' published empirical results, from their sampled
    implementation (0.05 mm boundary sampling, buffered-polygon APL).
AutoSeg
    This application: the published DICOM files, read by our grid builder and
    parser, measured by the engine the application selects.

Every value below is recomputed from the files on each run.

Usage::

    python scripts/validate_polygon_analytic.py \\
        --package "<Native_Polygon_Metrics_Gooding_Expert_Review folder>" \\
        --out docs/POLYGON_ANALYTIC_VALIDATION.md [--csv pairs.csv]
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "src"))

from validate_polygon_metrics import _golden, _series_grid  # noqa: E402
from validation_common import git_revision, long_path  # noqa: E402

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
    """The authors' APL code, re-run by the review package: reference → test, 1 mm.

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


# ---- Report -------------------------------------------------------------------


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
    rule = ["---"] + (["---:"] if numeric else ["---"]) * (len(headers) - 1)
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(rule) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


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
    """The stored polygon's own departure from the ideal shape, by the audited values."""
    return [
        record["audited"][key] - record["analytic"][key]
        for record in records
        if where(record["case"]) and key in record["analytic"] and key in record["audited"]
    ]


def _max_abs(values: list[float]) -> float:
    return max((abs(v) for v in values), default=float("nan"))


def write_report(target: Path, records, engine: str, package: Path, seconds: float) -> None:
    square = [r for r in records if r["case"]["family"] in SQUARE_CIRCLE]
    lines = [
        "# Native polygon metrics against the analytical shapes",
        "",
        "Generated by `scripts/validate_polygon_analytic.py`. Regenerate it rather",
        "than editing it.",
        "",
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · AutoSeg Evaluator, {git_revision()} · engine {engine} · "
        f"{len(records)} pairs from the published DICOM files · {seconds:.0f} s",
        "",
    ]

    # --- Headline, every figure computed below ---
    computation = max(
        _max_abs(_errors(records, key, "audited", lambda c: True)) for key, _l, _u in DISTANCES
    )
    shape_worst, shape_where = max(
        (abs(r["autoseg"][key] - r["analytic"][key]), r["case"]["resolution"])
        for r in square
        for key, _l, _u in DISTANCES
    )
    fine_worst = max(
        _max_abs(
            _errors(
                records,
                key,
                "analytic",
                lambda c: c["resolution"] == "Fine" and c["family"] in SQUARE_CIRCLE,
            )
        )
        for key, _l, _u in DISTANCES
    )
    authors_worst = max(
        _max_abs(_errors(records, key, "authors", lambda c: True)) for key, _l, _u in DISTANCES
    )
    apl_gaps = [abs(r["autoseg"]["apl_a_1"] - r["authors"]["apl_a_1"]) for r in records]
    # Where AutoSeg and the authors differ on APL, how far AutoSeg is from the
    # audited value: the claim below is made only if it holds.
    apl_audited = max(
        (
            abs(r["autoseg"]["apl_a_1"] - r["audited"]["apl_a_1"])
            for r, gap in zip(records, apl_gaps)
            if gap > 1e-3
        ),
        default=0.0,
    )
    apl_side = (
        f"and on every one AutoSeg matches the audited exact value to {_fmt(apl_audited, 2)} mm."
        if apl_audited < 1e-6
        else f"and there AutoSeg is up to {_fmt(apl_audited, 3)} mm from the audited values."
    )
    disagreements = apl_disagreements(records)
    by_method = sum(d["why"] == "their method" for d in disagreements)
    not_reproduced = sum(d["why"] == "not reproduced" for d in disagreements)
    apl_relative = max(
        (abs(d["ours"] - d["published"]) / d["ours"] * 100 for d in disagreements), default=0.0
    )
    sparse = [r for r in records if r["case"]["family"] == "cuboid_sparse"]
    sparse_worst = max(
        abs(r["autoseg"][key] - r["analytic"][key]) for r in sparse for key, _l, _u in DISTANCES
    )
    lines += [
        "## In brief",
        "",
        f"- **The computation is exact to {_fmt(computation, 2)} mm.** No distance of the 150",
        "  pairs differs by more from high-precision values for the polygons as stored.",
        f"- **Against the ideal squares and circles, distances differ by at most {_fmt(shape_worst, 3)} mm**",
        f"  ({shape_where.lower()} grid; {_fmt(fine_worst, 4)} mm on the fine grid). All of it is the",
        "  stored circle's: a circle stored as a polygon is not the circle, and the",
        "  audited values for the stored polygons differ from the ideal shapes by the",
        "  same amounts, to the four decimals tabulated.",
        f"- **Against the sparse cuboid, stored exactly, distances agree to {_fmt(sparse_worst, 2)} mm**, inside",
        "  the three decimals the supplement prints, and APL exactly.",
        f"- **Against the authors' software, distances agree to {_fmt(authors_worst, 3)} mm**, inside its",
        f"  0.05 mm sampling step. APL at 1 mm agrees on {sum(g <= 1e-3 for g in apl_gaps)} of {len(apl_gaps)} pairs;",
        f"  the other {len(disagreements)} differ by at most {_fmt(max(apl_gaps), 3)} mm "
        f"({apl_relative:.3f} % of the pair's APL), {apl_side}",
        f"  On {by_method} of them the authors' code, re-run, gives its published value, so the",
        f"  difference is its method's approximation; on {not_reproduced} it does not reproduce its own",
        "  published value.",
        "",
        "## What is compared",
        "",
        *_table(
            ["Reference", "What it is", "What agreement shows"],
            [
                [
                    "Analytical",
                    "Closed-form values for ideal squares, circles and cuboids (the supplement's tables)",
                    "How far a result is from the ideal shape, including the polygon's own departure from it",
                ],
                [
                    "Audited polygons",
                    "High-precision values for the contours as stored in the RTSTRUCTs",
                    "That the computation itself is right",
                ],
                [
                    "Authors' software",
                    "The paper's empirical results, from the authors' scoring code (VitruvianPhantomPy "
                    "`score_autocontours_lib.py`, adapted from Gooding's Chapter 15 code)",
                    "Agreement with the implementation the paper validated",
                ],
            ],
            numeric=False,
        ),
        "",
        "AutoSeg's values come from the published DICOM archives, read by this",
        "application's grid builder and structure parser, and measured by the engine",
        "it selects, exactly as a computation run does. The 150 pairs are 16 centred",
        "square/circle pairs, 16 rotated, 16 offset and two cuboid pairs, at each of",
        "three resolutions: coarse (1.8 × 2.2 mm, 3 mm planes), typical (0.96 mm, 1 mm)",
        "and fine (0.5 mm, 0.5 mm).",
        "",
    ]

    # --- Against the analytical values, square/circle ---
    lines += [
        "## Against the ideal shapes: squares and circles",
        "",
        "Largest absolute difference over the 16 pairs of each family, AutoSeg",
        "against the analytical value. Beside it, the same for the audited values",
        "of the stored polygons: where the two agree, the whole difference is the",
        "stored polygon's, not the computation's.",
        "",
    ]
    rows = []
    for key, label, unit in DISTANCES + NAPLS:
        for resolution in RESOLUTIONS:

            def where(c, resolution=resolution):
                return c["resolution"] == resolution and c["family"] in SQUARE_CIRCLE

            ours = _max_abs(_errors(records, key, "analytic", where))
            stored = _max_abs(_reference_errors(records, key, where))
            rows.append(
                [
                    label if resolution == "Coarse" else "",
                    resolution,
                    _fmt(ours),
                    _fmt(stored),
                    unit or "fraction",
                ]
            )
    lines += _table(
        ["Quantity", "Grid", "AutoSeg − analytic", "Stored polygon − analytic", "Unit"], rows
    )
    lines += [
        "",
        "Largest *relative* difference in each distance over the square/circle",
        "pairs, as a percentage of the analytical value:",
        "",
    ]
    rows = []
    for key, label, _unit in DISTANCES:
        per = []
        for resolution in RESOLUTIONS:
            worst = 0.0
            for record in square:
                if record["case"]["resolution"] != resolution:
                    continue
                truth = record["analytic"][key]
                if truth:
                    worst = max(worst, abs(record["autoseg"][key] - truth) / truth * 100)
            per.append(f"{worst:.2f} %")
        rows.append([label, *per])
    lines += _table(["Quantity", *RESOLUTIONS], rows)
    lines += [
        "",
        "By family, largest absolute distance difference from the analytical value (mm):",
        "",
    ]
    rows = []
    for family in SQUARE_CIRCLE:
        cells = []
        for key, _label, _unit in DISTANCES:
            cells.append(
                _fmt(
                    _max_abs(
                        _errors(records, key, "analytic", lambda c, f=family: c["family"] == f)
                    )
                )
            )
        rows.append([FAMILY_LABEL[family], *cells])
    lines += _table(["Family", *[label for _k, label, _u in DISTANCES]], rows)
    lines += [
        "",
        "The rotated family does not test rotation invariance: the published files",
        "rotate the square by 45 degrees but the circle's polygon by 45 radians. For a",
        "centred circle this moves only its vertices, so the analytical values of the",
        "centred family still apply.",
        "",
    ]

    # --- Cuboids ---
    cuboids = [r for r in records if r["case"]["family"].startswith("cuboid")]
    lines += [
        "## Against the ideal shapes: the cuboids",
        "",
        "The sparse test cuboid stores each plane's rectangle exactly, so there the",
        "analytical values are the ground truth for the contours as drawn, to the",
        "precision the supplement prints (Tables D.8 and D.11). The dense test cuboid",
        "is sampled at pixel boundaries, which clips its corners; the supplement notes",
        "its analytical values are not attainable.",
        "",
    ]
    rows = []
    for record in sorted(
        cuboids, key=lambda r: (RESOLUTIONS.index(r["case"]["resolution"]), r["case"]["family"])
    ):
        case = record["case"]
        cells = [case["resolution"], FAMILY_LABEL[case["family"]]]
        for key in ("hd100", "hd95", "mean", "median", "apl_a_1", "apl_a_2"):
            cells.append(
                f"{_fmt(record['autoseg'][key], 3)} ({_fmt(record['autoseg'][key] - record['analytic'][key], 3)})"
            )
        rows.append(cells)
    lines += _table(
        ["Grid", "Pair", "HD100", "HD95", "Mean", "Median", "APL @ 1 mm", "APL @ 2 mm"], rows
    )
    lines += [
        "",
        "Each cell: AutoSeg's value in mm, and in brackets its difference from the",
        "analytical value. Analytical: HD100 40.665, HD95 34.223 (the larger",
        "direction), median 26.2, symmetric mean 23.4995 mm; APL from Table D.11.",
        "",
        f"Table D.8 prints the reverse mean as {D8_MEAN_B_AS_PRINTED}, which is the symmetric",
        f"mean; the reverse directional mean is {D8_MEAN_B_CORRECTED} mm (the suppliers'",
        "high-precision calculation, a separate quadrature, and the authors' own",
        "empirical result of 24.085231). The symmetric mean compared here does not",
        "depend on which is read: taking the printed value as a direction would",
        f"give {0.5 * (D8_MEAN_A + D8_MEAN_B_AS_PRINTED):.4f} mm instead.",
        "",
    ]

    # --- Against the authors' software ---
    lines += [
        "## Against the authors' software",
        "",
        "The paper's *empirical* results come from the authors' own scoring code:",
        "`score_autocontours_lib.py` in their VitruvianPhantomPy repository (Boukerroui",
        "and Gooding, Mirada Medical), adapted in 2022 from Gooding's",
        "`score_autocontours.py` for Chapter 15 of *Auto-segmentation for Radiation",
        "Oncology*. The repository publishes those results as `results/*_empirical.csv`,",
        "and they are compared here as published; nothing of theirs was re-run for this",
        "report. For distances the code takes a point every 0.05 mm along each contour,",
        "from its start, measures each point's distance to the other contour, and reads",
        "HD95 and the median as NumPy percentiles of those points and the mean as their",
        "average. Differences of a few hundredths of a millimetre are that sampling,",
        "not an error on either side.",
        "",
        "Largest absolute difference from their published results, over all 150 pairs:",
        "",
    ]
    rows = []
    for key, label, _unit in DISTANCES:
        diffs = _errors(records, key, "authors", lambda c: True)
        rows.append(
            [label, _fmt(_max_abs(diffs)), _fmt(sorted(abs(d) for d in diffs)[len(diffs) // 2])]
        )
    lines += _table(["Quantity", "Largest difference (mm)", "Median difference (mm)"], rows)
    apl = []
    for record in records:
        ours, theirs = record["autoseg"]["apl_a_1"], record["authors"]["apl_a_1"]
        apl.append((abs(ours - theirs), record, ours, theirs))
    agreeing = sum(1 for d, *_ in apl if d <= 1e-3)
    lines += [
        "",
        f"**APL at 1 mm** (reference → test, summed over planes): {agreeing} of {len(apl)} pairs",
        "agree with the authors' value to within 0.001 mm. The others:",
        "",
    ]
    why_label = {
        "their method": "their method",
        "not reproduced": "not reproduced by their code",
        "no re-run": "–",
    }
    rows = [
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
    ]
    lines += _table(
        [
            "Pair",
            "AutoSeg (mm)",
            "Published (mm)",
            "Their code, re-run (mm)",
            "AutoSeg − published (mm)",
            "Exact − AutoSeg (mm)",
            "Difference due to",
        ],
        rows,
    )
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
        "",
        "*Exact* is the audited high-precision value for the stored polygons; AutoSeg",
        f"is within {_fmt(apl_audited, 2)} mm of it on every one of these pairs. *Their code, re-run* is the",
        "metric suppliers' review package re-running the authors' APL function",
        "(`historical_validation/overlap_apl`, with three mechanical edits for",
        "Shapely 2, on current libraries rather than the 2022 environment).",
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
        f"- **{by_method} of these pairs are their method's approximation.** Re-running their code",
        "  gives exactly their published value, and that value differs from the exact",
        f"  one, by at most {_fmt(method_worst, 3)} mm.",
        f"- **{not_reproduced} are published values their own code no longer reproduces.** On "
        f"{'all' if len(closer) == not_reproduced else len(closer)} of them",
        "  the re-run lands closer to the exact value than the published figure. The",
        "  review package did not isolate why; different library versions and the",
        "  random retry are both possible.",
        "",
        "In a degenerate case the difference is large. An axis-aligned square",
        "[-5, 5]² against [-3, 3]² at τ = 2 mm has 16 mm of reference boundary",
        "farther than τ from the test, by the paper's definition; the authors' code",
        "returns 40 mm, because the reference's straight edges lie exactly on the",
        "widened outline and count as unmatched. None of the published pairs is",
        "degenerate like this, and on them the differences are at most",
        f"{apl_relative:.3f} % of a pair's APL, which is summed over every plane.",
        "",
    ]
    medians = [abs(r["authors"]["median"] - r["authors"]["median_chapter15"]) for r in records]
    lines += [
        "The authors' published Chapter 15 code combines the two directional medians",
        "by averaging them; the paper, the audited values and AutoSeg take the",
        f"larger. On these pairs the two conventions differ by up to {max(medians):.3f} mm, so",
        "the comparison above composes the authors' directional medians the paper's",
        "way.",
        "",
    ]

    # --- Computation ---
    lines += [
        "## The computation itself",
        "",
        "Largest absolute difference from the audited high-precision values for the",
        "stored polygons, over all 150 pairs and both directions of APL:",
        "",
    ]
    rows = []
    for key, label, _unit in DISTANCES:
        rows.append(
            [label, _fmt(_max_abs(_errors(records, key, "audited", lambda c: True)), 2), "mm"]
        )
    for tau in TOLERANCES_MM:
        for side, arrow in (("a", "reference → test"), ("b", "test → reference")):
            rows.append(
                [
                    f"APL {arrow} @ {tau:g} mm",
                    _fmt(
                        _max_abs(
                            _errors(records, f"apl_{side}_{tau:g}", "audited", lambda c: True)
                        ),
                        2,
                    ),
                    "mm",
                ]
            )
            rows.append(
                [
                    f"NAPL {arrow} @ {tau:g} mm",
                    _fmt(
                        _max_abs(
                            _errors(records, f"napl_{side}_{tau:g}", "audited", lambda c: True)
                        ),
                        2,
                    ),
                    "",
                ]
            )
    lines += _table(["Quantity", "Largest difference", "Unit"], rows)
    lines += [
        "",
        "`validate_polygon_metrics.py` holds these to the suppliers' thresholds on every release.",
        "",
    ]

    # --- Conventions ---
    lines += [
        "## Conventions",
        "",
        *_table(
            ["Quantity", "How AutoSeg reports it"],
            [
                [
                    "Distance",
                    "From each point of one boundary to the nearest point of the other's boundary on the same plane",
                ],
                [
                    "Planes",
                    "Distances over planes both structures have; APL over every plane of its source",
                ],
                ["HD100", "The larger of the two directional maxima"],
                [
                    "HD95, median",
                    "The larger of the two directional percentiles (lower generalised inverse)",
                ],
                ["Mean", "The average of the two directional arc-length means"],
                [
                    "APL, NAPL",
                    "Both directions, as separate columns; closed tolerance (a point exactly at τ is matched)",
                ],
            ],
            numeric=False,
        ),
        "",
        "These are the paper's definitions (Supplement A). Points are weighted by",
        "arc length, not by vertex, so adding vertices along an edge changes nothing.",
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
        "`cc4106a`) and Supplement Tables D.8 and D.11. Audited values:",
        "`third_party/native_contour_metrics/v0.2/data/golden_metrics.json`.",
        "",
        "Reproduce with:",
        "",
        "```",
        "python scripts/validate_polygon_analytic.py --package <review package folder> \\",
        "    --out docs/POLYGON_ANALYTIC_VALIDATION.md",
        "```",
        "",
        f"The review package read for this run: `{package.name}`.",
        "",
    ]
    target.write_text("\n".join(lines), encoding="utf-8")


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
        "--package",
        type=Path,
        required=True,
        help="The Native_Polygon_Metrics_Gooding_Expert_Review folder.",
    )
    parser.add_argument("--out", type=Path, help="Write the markdown report here.")
    parser.add_argument("--csv", type=Path, help="Write every pair's values here.")
    parser.add_argument("--engine", help="Force 'fast' or 'reference'.")
    args = parser.parse_args(argv)

    reference = long_path(args.package) / "reference_v0_1"
    upstream = reference / "upstream"
    started = time.perf_counter()
    records, engine = measure_all(reference / "data" / "original", args.engine)
    analytic = analytic_tables(upstream)
    authors = author_tables(upstream)
    reruns = author_reruns(long_path(args.package))
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
    if args.out:
        write_report(args.out, records, engine, args.package, elapsed)
        print(f"wrote {args.out}")
    if args.csv:
        write_csv(args.csv, records)
        print(f"wrote {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
