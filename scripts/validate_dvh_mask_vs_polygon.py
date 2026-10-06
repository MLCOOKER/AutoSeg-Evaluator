"""How much a DVH from a binary mask differs from one from the contours.

AutoSeg Evaluator computes a structure's dose statistics two ways. A structure
stored as an RTSTRUCT is integrated over its contours (``structure_dvh``, the
polygon path). A structure that exists only as a voxel mask -- a drawer's
STAPLE consensus, or a multi-observer consensus used as ground truth -- has no
contours, so its voxels are sampled instead (``mask_dvh``, the mask path). The
two appear side by side in one results table: a consensus ground truth's dose
row next to each vendor's.

This script measures what that costs, on the analytic benchmark of Nelms et al.
2015 (Med Phys 42:4435): spheres, cylinders and cones of 3.6-12 cc, contoured
every 0.2-3 mm, in linear dose fields on 0.4-3 mm dose grids, with the true
DVH known in closed form. Each structure is taken both ways, exactly as the
application takes it: the polygon path from its RTSTRUCT, and the mask path
from the mask the application rasterises from that same RTSTRUCT on the CT grid
-- the mask a unanimous STAPLE consensus returns, which is checked here too.
Both are scored against the truth, and against each other.

``validate_dvh_methods.py`` scores every DVH method that was considered, and is
where the polygon path was chosen. This asks the narrower question the results
table raises: when a consensus's dose row sits beside a contour's, how far
apart can they be for the same structure?

Usage::

    python scripts/validate_dvh_mask_vs_polygon.py --nelms <Nelms dataset folder>

It keeps its results (``validation_common.save_results``) and rewrites
Supplementary 1, which ``supplementary_1_dvh.py`` builds from them and from
``validate_dvh_methods.py``'s. It needs the ``validation`` extra (openpyxl). Every input is synthetic.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pydicom
import SimpleITK as sitk

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "src"))

from validate_dvh_methods import (  # noqa: E402
    CLINICAL,
    CONFIG,
    LABEL,
    METRICS,
    NELMS_CT,
    NELMS_DOSE,
    NELMS_GRADIENT,
    RESULT_TYPES,
    TEST_NAMES,
    Case,
    NelmsRow,
    closed_roi,
    ct_geometry,
    curve_error,
    engine_result,
    failed,
    nelms_counts,
    nelms_structure_path,
    read_nelms_curves,
    read_nelms_rows,
    table,
)
from validation_common import load_results, run_info, save_results  # noqa: E402

from autoseg_evaluator.core.dvh import DoseGrid, mask_dvh, structure_dvh  # noqa: E402
from autoseg_evaluator.core.masks import mask_with_reading  # noqa: E402
from autoseg_evaluator.core.staple import StapleConfig, compute_staple  # noqa: E402

POLYGON = "polygon"
MASK = "mask"
PATHS = (POLYGON, MASK)
PATH_LABEL = {
    POLYGON: "Contours (RTSTRUCT)",
    MASK: "Mask (STAPLE, consensus)",
}
TESTS = ("1", "2", "2s")
#: The dataset's own CT pixels, then the two in-plane pixel sizes of this
#: project's cohort's body CTs (of its 80 patients with a CT): 1.074 mm, the
#: most common (42), and 1.367 mm, the coarsest (28: every breast, most
#: paediatric); the other 10 are brain CTs at 0.468 mm, finer than the
#: dataset's. The slices stay the dataset's.
NATIVE = "native"
PIXELS_MM: tuple[float | None, ...] = (None, 1.074, 1.367)
GRID_NAME = {
    NATIVE: "0.6 mm (the dataset's)",
    "1.07 mm": "1.07 mm (cohort's most common)",
    "1.37 mm": "1.37 mm (cohort's coarsest)",
}
#: Where a coarser grid's first pixel sits, in pixels along the rows and the
#: columns. A mask's error depends on where the structure falls among the
#: voxels, and the Nelms structures share one position, so each coarser grid is
#: laid at four offsets; the native grid is the dataset's own, at one.
PHASES: tuple[tuple[float, float], ...] = ((0.0, 0.0), (0.25, 0.5), (0.5, 0.25), (0.75, 0.75))


def grid_label(pixel_mm: float | None) -> str:
    return NATIVE if pixel_mm is None else f"{pixel_mm:.2f} mm"


def coarser(
    ct: sitk.Image, pixel_mm: float | None, phase: tuple[float, float] = (0.0, 0.0)
) -> sitk.Image:
    """The same CT extent and slices, with in-plane pixels of ``pixel_mm``.

    ``phase`` moves the grid by that fraction of a pixel along its rows and
    columns, so a structure lands at a different place among the voxels.
    """
    if pixel_mm is None:
        return ct
    sx, sy, sz = ct.GetSpacing()
    nx, ny, nz = ct.GetSize()
    image = sitk.Image(
        int(math.ceil(nx * sx / pixel_mm)), int(math.ceil(ny * sy / pixel_mm)), nz, sitk.sitkUInt8
    )
    direction = np.asarray(ct.GetDirection(), float).reshape(3, 3)
    origin = np.asarray(ct.GetOrigin(), float) - pixel_mm * (
        phase[0] * direction[:, 0] + phase[1] * direction[:, 1]
    )
    image.SetOrigin(tuple(float(v) for v in origin))
    image.SetDirection(ct.GetDirection())
    image.SetSpacing((pixel_mm, pixel_mm, sz))
    return image


def _contour_points(root: Path, stem: str) -> list[np.ndarray]:
    ds = pydicom.dcmread(str(nelms_structure_path(root, stem)))
    return [
        np.asarray(c.ContourData, float).reshape(-1, 3)
        for c in ds.ROIContourSequence[0].ContourSequence
    ]


def along_field_rows(root: Path, rows: list[NelmsRow]) -> list[NelmsRow]:
    """The diagonally shifted copies again, in the front-to-back field.

    The dataset pairs each diagonal copy (moved sideways and front-to-back) with
    the head-to-foot field, which neither movement climbs, so its analytic values
    equal its twin's. In the front-to-back field the same copy has moved along
    the gradient, and a linear field makes its truth exact without a table:
    moving a structure by ``t`` in a gradient ``g`` adds ``g · t`` to every dose
    statistic and leaves the volume alone. ``t`` is measured from the two files,
    and checked to be a pure translation; ``g`` from the dose grid itself.
    """
    twins = {(r.structure, r.voxel, r.gradient): r for r in rows}
    gradients: dict[str, np.ndarray] = {}
    out = []
    for row in rows:
        if row.gradient != "SI" or "," not in row.shift:
            continue
        shape, code = row.structure.split("_")[:2]
        twin = twins.get((f"{shape}_{code}_0", row.voxel, "AP"))
        if twin is None:
            continue
        before, after = _contour_points(root, twin.structure), _contour_points(root, row.structure)
        t = np.concatenate(after).mean(axis=0) - np.concatenate(before).mean(axis=0)
        if len(before) != len(after) or any(
            a.shape != b.shape or np.abs(b - a - t).max() > 1e-6
            for a, b in zip(before, after, strict=True)
        ):
            raise ValueError(f"{row.structure} is not a translation of {twin.structure}")
        if row.voxel not in gradients:
            ds = pydicom.dcmread(
                str(
                    root
                    / "DOSE GRIDS"
                    / f"{NELMS_GRADIENT['AP']}_{NELMS_DOSE[row.voxel]}_Aligned.dcm"
                )
            )
            grid = DoseGrid.from_dataset(ds)
            centre = np.concatenate(before).mean(axis=0)
            gradients[row.voxel] = np.array(
                [
                    (
                        grid.sample(np.array([centre + e]))[0]
                        - grid.sample(np.array([centre - e]))[0]
                    )
                    / 2.0
                    for e in np.eye(3)
                ]
            )
        delta = float(gradients[row.voxel] @ t)
        truth = {k: (v if k == "volume_cc" else v + delta) for k, v in twin.truth.items()}
        out.append(NelmsRow(row.structure, row.spacing, row.shift, row.voxel, "AP", truth))
    return out


def run(root: Path) -> tuple[dict[str, list[dict]], dict]:
    """Every Nelms structure and dose, through both paths, on each CT grid.

    Records of the dataset's own rows go in the first dict, by grid; those of
    the extra along-field rows (:func:`along_field_rows`) in ``extra["design"]``,
    because only the design comparison uses them.
    """
    rows = read_nelms_rows(root)
    along = along_field_rows(root, rows)
    design_only = {id(r) for r in along}
    rows = [*rows, *along]
    curves = read_nelms_curves(root)
    test3_dose = np.round(np.arange(0.0, 30.0001, 0.1), 6)
    doses: dict[tuple[str, str], tuple] = {}
    cts: dict[tuple[str, float | None], sitk.Image] = {}
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for row in rows:
        groups[(row.structure, row.spacing)].append(row)

    records: dict[str, list[dict]] = {grid_label(p): [] for p in PIXELS_MM}
    design: dict[str, list[dict]] = {grid_label(p): [] for p in PIXELS_MM}
    staple_checks = []
    for n, ((stem, spacing), group) in enumerate(groups.items(), 1):
        print(f"  {n}/{len(groups)}: {stem}", flush=True)
        rtss = pydicom.dcmread(str(nelms_structure_path(root, stem)))
        roi = closed_roi(rtss)
        layouts = [(p, phase) for p in PIXELS_MM for phase in (PHASES[:1] if p is None else PHASES)]
        for pixel_mm, phase in layouts:
            key = (spacing, pixel_mm, phase)
            if key not in cts:
                native = (spacing, None, PHASES[0])
                if native not in cts:
                    cts[native] = ct_geometry(root / "CT" / NELMS_CT[spacing])
                cts[key] = coarser(cts[native], pixel_mm, phase)
            ct = cts[key]
            mask, _notes = mask_with_reading(ct, rtss, roi)
            if pixel_mm is None:
                # A unanimous consensus: three identical raters. STAPLE should
                # hand back the raters' own mask, so the mask path is exactly
                # what a consensus of agreeing raters gets.
                consensus = compute_staple([mask, mask, mask], StapleConfig())
                same = consensus is not None and np.array_equal(
                    sitk.GetArrayViewFromImage(consensus.consensus_mask),
                    sitk.GetArrayViewFromImage(mask),
                )
                staple_checks.append({"structure": stem, "spacing": spacing, "identical": same})
            for row in group:
                dose_key = (row.voxel, row.gradient)
                if dose_key not in doses:
                    name = f"{NELMS_GRADIENT[row.gradient]}_{NELMS_DOSE[row.voxel]}_Aligned.dcm"
                    ds = pydicom.dcmread(str(root / "DOSE GRIDS" / name))
                    doses[dose_key] = (ds, DoseGrid.from_dataset(ds))
                dose_ds, grid = doses[dose_key]
                case = Case(rtss, roi, dose_ds, grid, ct)
                target = design if id(row) in design_only else records
                for path in PATHS:
                    record = _measure(case, mask, path, row, curves, test3_dose)
                    record["phase"] = phase
                    target[grid_label(pixel_mm)].append(record)
    geometry = {
        spacing: (tuple(round(v, 3) for v in image.GetSpacing()), image.GetSize())
        for (spacing, pixel_mm, _phase), image in cts.items()
        if pixel_mm is None
    }
    return records, {"staple": staple_checks, "ct": geometry, "design": design}


def _measure(case: Case, mask, path: str, row, curves, test3_dose) -> dict:
    started = time.perf_counter()
    try:
        if path == POLYGON:
            dvh = structure_dvh(case.rtss, case.roi, case.dose, case.ct, CONFIG)
        else:
            dvh = mask_dvh(mask, case.dose, CONFIG)
        result = engine_result(dvh)
    except Exception as exc:  # noqa: BLE001 — a failure is a result
        result = failed(f"{type(exc).__name__}: {exc}")
    result.seconds = time.perf_counter() - started
    record = {"benchmark": "nelms", "row": row, "method": path, "result": result}
    curve_key = (row.shape, row.gradient, row.spacing)
    if row.test == "2" and row.spacing in ("1mm", "3mm") and curve_key in curves:
        dose, volume = curves[curve_key]
        truth = np.interp(test3_dose, dose, volume, right=0.0)
        error = curve_error(result, test3_dose, truth, float(volume[0]))
        record["test3"] = (
            float(np.min(error)),
            float(np.max(error)),
            float(np.mean(error)),
            float(np.std(error, ddof=1)),
        )
    return record


# ---- Report -------------------------------------------------------------------


def _pair(records: list[dict]) -> list[tuple]:
    """Each (structure, dose) once per path: (row, polygon result, mask result)."""
    by_key: dict[tuple, dict] = defaultdict(dict)
    for r in records:
        row = r["row"]
        key = (row.structure, row.spacing, row.shift, row.voxel, row.gradient, r.get("phase"))
        by_key[key][r["method"]] = r
    out = []
    for pair in by_key.values():
        if POLYGON in pair and MASK in pair:
            out.append((pair[POLYGON]["row"], pair[POLYGON]["result"], pair[MASK]["result"]))
    return out


def _stats(values: list[float]) -> tuple[float, float, float]:
    a = np.abs(np.asarray([v for v in values if math.isfinite(v)]))
    if not a.size:
        return math.nan, math.nan, math.nan
    return float(np.median(a)), float(np.percentile(a, 95)), float(a.max())


def _f(value: float, digits: int = 2) -> str:
    return "–" if not math.isfinite(value) else f"{value:.{digits}f}"


def accuracy_section(records: list[dict]) -> list[str]:
    lines = [
        "## Against the analytic truth",
        "",
        "Parameters more than 3 % from the analytic value, out of those scored, and",
        "the range of the % differences, as Nelms et al. count them. Volume counts",
        "once per structure and dose grid. Differences are relative to the local",
        "analytic value, so the low-dose parameters (Dmin, D99, D95) are amplified.",
        "",
    ]
    for test in TESTS:
        counts = {p: nelms_counts(records, test, p) for p in PATHS}
        rows = []
        for metric in METRICS:
            row = [LABEL[metric]]
            for p in PATHS:
                n, total, lo, hi = counts[p][metric]
                row.append(f"{n}/{total} ({lo:+.1f} to {hi:+.1f})")
            rows.append(row)
        for label, subset in (
            ("**All**", METRICS),
            ("**Without Dmin, Dmax**", ("volume_cc", *CLINICAL)),
        ):
            row = [label]
            for p in PATHS:
                n = sum(counts[p][k][0] for k in subset)
                total = sum(counts[p][k][1] for k in subset)
                row.append(f"**{n}/{total}**")
            rows.append(row)
        lines += [f"### {TEST_NAMES[test]}", ""]
        lines += table(["Parameter", *(PATH_LABEL[p] for p in PATHS)], rows)
    rows = []
    for test in TESTS:
        row = [TEST_NAMES[test].split(":")[0]]
        for p in PATHS:
            c = nelms_counts(records, test, p, threshold=2.0)
            n = sum(c[k][0] for k in ("volume_cc", *CLINICAL))
            total = sum(c[k][1] for k in ("volume_cc", *CLINICAL))
            row.append(f"{n}/{total}")
        rows.append(row)
    lines += ["At the stricter 2 %, without Dmin and Dmax:", ""]
    lines += table(["", *(PATH_LABEL[p] for p in PATHS)], rows)
    return lines


def difference_section(pairs: list[tuple]) -> list[str]:
    lines = [
        "## Mask against contours, structure by structure",
        "",
        "The same structure and dose through both paths: the mask path's value minus",
        "the polygon path's. This is what separates a consensus's dose row from a",
        "contour's in the results table when the two describe the same shape.",
        "Doses in Gy (the fields rise 1 Gy per mm), volume in cc; the % columns are",
        "relative to the analytic value. Every test and dose grid.",
        "",
    ]
    rows = []
    for metric in METRICS:
        absolute, relative = [], []
        for row, polygon, mask in pairs:
            a, b = mask.metrics.get(metric, math.nan), polygon.metrics.get(metric, math.nan)
            if math.isfinite(a) and math.isfinite(b):
                absolute.append(a - b)
                relative.append((a - b) / row.truth[metric] * 100.0)
        med, p95, worst = _stats(absolute)
        rmed, rp95, rworst = _stats(relative)
        unit = "cc" if metric == "volume_cc" else "Gy"
        rows.append(
            [
                LABEL[metric],
                f"{_f(med, 3)} {unit}",
                f"{_f(p95, 3)} {unit}",
                f"{_f(worst, 3)} {unit}",
                f"{_f(rmed)} %",
                f"{_f(rp95)} %",
                f"{_f(rworst)} %",
                str(len(absolute)),
            ]
        )
    lines += table(
        [
            "Parameter",
            "Median |Δ|",
            "95th pct |Δ|",
            "Largest |Δ|",
            "Median |Δ| %",
            "95th pct |Δ| %",
            "Largest |Δ| %",
            "Pairs",
        ],
        rows,
    )
    # By contour spacing, which sets the mask's slice thickness.
    lines += [
        "By CT slice (and contour) spacing, which sets the mask's voxel height: the",
        "95th percentile of |Δ| as % of the analytic value.",
        "",
    ]
    spacings = sorted({row.spacing for row, *_ in pairs}, key=lambda s: float(s.rstrip("m")))
    rows = []
    for metric in ("volume_cc", *CLINICAL):
        cells = [LABEL[metric]]
        for spacing in spacings:
            rel = [
                (m.metrics[metric] - p.metrics[metric]) / row.truth[metric] * 100.0
                for row, p, m in pairs
                if row.spacing == spacing
                and math.isfinite(m.metrics[metric])
                and math.isfinite(p.metrics[metric])
            ]
            cells.append(f"{_f(_stats(rel)[1])} %")
        rows.append(cells)
    lines += table(["Parameter", *spacings], rows)
    # By structure size.
    sizes = defaultdict(list)
    for row, p, m in pairs:
        sizes[row.structure].append((row, p, m))
    rows = []
    for structure in sorted(sizes, key=lambda s: sizes[s][0][0].truth["volume_cc"]):
        group = sizes[structure]
        vol = group[0][0].truth["volume_cc"]
        vrel = [
            (m.metrics["volume_cc"] - p.metrics["volume_cc"]) / row.truth["volume_cc"] * 100.0
            for row, p, m in group
        ]
        drel = [
            (m.metrics["dmean"] - p.metrics["dmean"]) / row.truth["dmean"] * 100.0
            for row, p, m in group
        ]
        rows.append(
            [structure, f"{vol:.2f}", f"{_f(_stats(vrel)[2])} %", f"{_f(_stats(drel)[2])} %"]
        )
    lines += [
        "By structure, largest |Δ| over its dose grids and contour spacings:",
        "",
    ]
    lines += table(["Structure", "Volume (cc)", "Volume", "Dmean"], rows)
    return lines


def test3_section(records: list[dict]) -> list[str]:
    lines = [
        "## Along the whole curve",
        "",
        "Test 3 of Nelms et al.: the volume error at every 0.1 Gy from 0 to 30 Gy, as",
        "% of the structure's analytic volume, on the Test 2 data at 1 and 3 mm. Each",
        "cell averages the five shapes' lowest and highest error and their mean ± SD.",
        "",
    ]
    rows = []
    for spacing in ("1mm", "3mm"):
        for gradient in ("SI", "AP"):
            cells = [f"{spacing}, {gradient}"]
            for p in PATHS:
                chosen = [
                    r["test3"]
                    for r in records
                    if r["method"] == p
                    and "test3" in r
                    and r["row"].spacing == spacing
                    and r["row"].gradient == gradient
                ]
                if not chosen:
                    cells.append("–")
                    continue
                a = np.array(chosen).mean(axis=0)
                cells.append(f"{a[0]:+.1f} to {a[1]:+.1f} ({a[2]:+.1f} ± {a[3]:.1f})")
            rows.append(cells)
    return lines + table(["Data", *(PATH_LABEL[p] for p in PATHS)], rows)


def _relative(pairs: list[tuple], metric: str) -> list[float]:
    """|mask - polygon| as % of the analytic value, where both are finite."""
    return [
        abs(m.metrics[metric] - p.metrics[metric]) / row.truth[metric] * 100.0
        for row, p, m in pairs
        if math.isfinite(m.metrics[metric]) and math.isfinite(p.metrics[metric])
    ]


def beyond_three_percent(records: list[dict], path: str) -> tuple[int, int]:
    """Volume and clinical-dose parameters beyond 3 % of the truth, over every test and offset.

    Counted per offset, then summed: the volume is counted once per structure
    and dose grid within an offset, and a mask's volume changes with it.
    """
    n = total = 0
    for phase in {r.get("phase") for r in records}:
        chosen = [r for r in records if r.get("phase") == phase]
        for t in TESTS:
            counts = nelms_counts(chosen, t, path)
            n += sum(counts[k][0] for k in ("volume_cc", *CLINICAL))
            total += sum(counts[k][1] for k in ("volume_cc", *CLINICAL))
    return n, total


def grid_section(by_grid: dict[str, list[dict]]) -> list[str]:
    """The same comparison with the mask made on coarser in-plane pixels."""
    lines = [
        "## On a clinical CT grid",
        "",
        "The dataset's CT has 0.6 mm pixels, finer than most planning CTs. The whole",
        "comparison again, with the CT's slices kept and its in-plane pixels made",
        "1.07 mm and 1.37 mm: the most common and the coarsest in-plane pixel of this",
        "project's cohort (42 and 28 of its 80 CT patients). A mask's staircase is",
        "set by its voxels, so the mask path should move; the contour path reads",
        "the CT only for its geometry, so it should not. Each coarser grid is laid",
        f"at {len(PHASES)} sub-pixel offsets, pooled below, because a mask's error depends on",
        "where a structure falls among the voxels and the Nelms structures all sit",
        "at one position; counts are over every offset.",
        "",
    ]
    rows = []
    for label, records in by_grid.items():
        cells = [GRID_NAME.get(label, label)]
        for path in PATHS:
            n, total = beyond_three_percent(records, path)
            cells.append(f"{n}/{total} ({100 * n / total:.1f} %)")
        pairs = _pair(records)
        shift = max(
            abs(m.metrics["dmean"] - p.metrics["dmean"])
            for _row, p, m in pairs
            if math.isfinite(m.metrics["dmean"]) and math.isfinite(p.metrics["dmean"])
        )
        cells.append(f"{shift:.2f}")
        rows.append(cells)
    lines += [
        "Parameters beyond 3 % of the analytic value, volume and clinical doses, every",
        "test and offset; and the largest Dmean difference between the paths in Gy.",
        "The fields rise 1 Gy per mm, so that is also how far, in mm, the mask moved",
        "the structure's dose-weighted centre: a mask can shift a boundary by up to",
        "half a pixel.",
        "",
    ]
    lines += table(
        ["CT pixel", *(PATH_LABEL[p] for p in PATHS), "Largest |ΔDmean| (Gy, = mm)"], rows
    )
    metrics = ("volume_cc", "dmean", "d99", "d95", "d5", "d1", "d0.03cc")
    rows = []
    for label, records in by_grid.items():
        pairs = _pair(records)
        cells = [GRID_NAME.get(label, label)]
        for metric in metrics:
            _med, p95, worst = _stats(_relative(pairs, metric))
            cells.append(f"{_f(p95)} / {_f(worst)} %")
        rows.append(cells)
    lines += [
        "Mask against contours for the same structure: 95th percentile / largest",
        "|Δ|, as % of the analytic value.",
        "",
    ]
    lines += table(["CT pixel", *(LABEL[m] for m in metrics)], rows)
    return lines


#: Ways to take the difference between a test contour's DVH and a reference's:
#: (key, label, the test's path, the reference's path).
DESIGNS = (
    ("contours", "Both from contours", POLYGON, POLYGON),
    ("mixed", "Test from contours, reference from its mask", POLYGON, MASK),
    ("masks", "Both from masks", MASK, MASK),
)
DESIGN_METRICS = ("volume_cc", "dmean", "d99", "d95", "d5", "d1", "d0.03cc")

#: The two sets of shifted pairs, by whether the shift climbs the dose field.
ACROSS = "across"
ALONG = "along"
SHIFT_KINDS = {
    ACROSS: "Shifted across the field: true difference zero",
    ALONG: "Shifted along the field: true difference 0.5-1.5 Gy",
}


def _shift_pairs(records: list[dict], extra: list[dict] = ()) -> dict[str, list[tuple[dict, dict]]]:
    """Each shifted structure with its unshifted twin: same dose, grid and offset.

    The twin stands for the reference and the shifted copy for a test contour
    that differs from it. Both have analytic values, so the true difference is
    known. The dataset's own pairings shift each copy across its field, which
    leaves the dose unchanged, so there the true difference is zero; the extra
    along-field rows (:func:`along_field_rows`) shift it along the field, where
    it is not. The pairs are returned under :data:`ACROSS` and :data:`ALONG`.
    """
    everything = [*records, *extra]
    index = {}
    for r in everything:
        row = r["row"]
        index[(row.structure, row.voxel, row.gradient, r.get("phase"), r["method"])] = r
    out: dict[str, list[tuple[dict, dict]]] = {ACROSS: [], ALONG: []}
    for r in everything:
        row = r["row"]
        if r["method"] != POLYGON or row.shift == "0" or row.test == "1":
            continue
        shape, code = row.structure.split("_")[:2]
        twin = f"{shape}_{code}_0"
        phase = r.get("phase")
        try:
            test = {p: index[(row.structure, row.voxel, row.gradient, phase, p)] for p in PATHS}
            ref = {p: index[(twin, row.voxel, row.gradient, phase, p)] for p in PATHS}
        except KeyError:
            continue
        moved = abs(test[POLYGON]["row"].truth["dmean"] - ref[POLYGON]["row"].truth["dmean"])
        out[ALONG if moved > 1e-9 else ACROSS].append((test, ref))
    return out


def _design_errors(pairs, metric: str, test_path: str, ref_path: str) -> list[float]:
    """Measured minus true difference, as % of the reference's analytic value."""
    errors = []
    for test, ref in pairs:
        truth_test, truth_ref = test[POLYGON]["row"].truth, ref[POLYGON]["row"].truth
        measured = (
            test[test_path]["result"].metrics[metric] - ref[ref_path]["result"].metrics[metric]
        )
        true = truth_test[metric] - truth_ref[metric]
        if math.isfinite(measured) and truth_ref[metric]:
            errors.append((measured - true) / truth_ref[metric] * 100.0)
    return errors


def like_for_like_worse_along(
    by_grid: dict[str, list[dict]], design: dict[str, list[dict]]
) -> bool:
    """Whether taking the test from its mask loses on every statistic at every coarser grid.

    For the copies shifted along the field, at the 95th percentile: the finding
    the choice of DVH path rests on.
    """
    return all(
        _stats(_design_errors(pairs, metric, MASK, MASK))[1]
        > _stats(_design_errors(pairs, metric, POLYGON, MASK))[1]
        for label, records in by_grid.items()
        if label != NATIVE
        for pairs in (_shift_pairs(records, design.get(label, []))[ALONG],)
        for metric in DESIGN_METRICS
    )


def design_section(by_grid: dict[str, list[dict]], design: dict[str, list[dict]]) -> list[str]:
    """Which way of taking a DVH difference against a mask reference is closest to the truth."""
    lines = [
        "## A test contour against a reference that is a mask",
        "",
        "A consensus reference exists only as a mask. A test contour's DVH can be",
        "set against it from the test's contours (mixed) or from the test's own mask,",
        "rasterised on the same grid (like for like). To see which is closer to the",
        "true difference, each shifted Nelms structure is taken as a test contour and",
        "its unshifted twin as the reference, on the same dose grid, CT grid and",
        "offset, in two sets:",
        "",
        "- **Shifted across the field.** The dataset's own pairings: copies moved",
        "  sideways (0.5-1.5 mm) in the front-to-back field, and diagonally (sideways",
        "  and front-to-back, 0.5-1.5 mm each) in the head-to-foot field. Neither",
        "  movement climbs its field, so the true dose difference is zero, as the",
        "  dataset's analytic values confirm: any difference measured is discretisation.",
        "- **Shifted along the field.** The diagonal copies again, in the front-to-back",
        "  field, which their front-to-back movement climbs. A linear field makes the",
        "  truth exact without the dataset tabulating it: moving a structure by *s* mm",
        "  along a gradient of *g* Gy/mm adds *g·s* to every dose statistic and leaves",
        "  its volume alone, here 0.5, 1 or 1.5 Gy. The movement and the gradient are",
        "  measured from the files, and each copy checked to be an exact translation",
        "  of its twin.",
        "",
        "*Both from contours* is the best case, open only to a reference that has",
        "contours. Error in the difference (measured minus true), as % of the",
        "reference's analytic value: 95th percentile / largest, over every pair and",
        "offset.",
        "",
    ]
    for kind, heading in SHIFT_KINDS.items():
        rows = []
        n_pairs = 0
        for label, records in by_grid.items():
            pairs = _shift_pairs(records, design.get(label, []))[kind]
            n_pairs = max(n_pairs, len(pairs))
            for _key, name, test_path, ref_path in DESIGNS:
                cells = [GRID_NAME.get(label, label), name]
                for metric in DESIGN_METRICS:
                    _med, p95, worst = _stats(_design_errors(pairs, metric, test_path, ref_path))
                    cells.append(f"{_f(p95)} / {_f(worst)} %")
                rows.append(cells)
        lines += [f"### {heading}", "", f"Up to {n_pairs} pairs per CT grid, over its offsets.", ""]
        lines += table(
            ["CT pixel", "Difference taken", *(LABEL[m] for m in DESIGN_METRICS)],
            rows,
            align="ll" + "r" * len(DESIGN_METRICS),
        )
        # The reading, from the numbers above: per grid, where like for like is
        # the smaller error at the 95th percentile and where it is not.
        for label, records in by_grid.items():
            if label == NATIVE:
                continue
            pairs = _shift_pairs(records, design.get(label, []))[kind]
            better, worse = [], []
            for metric in DESIGN_METRICS:
                mixed = _stats(_design_errors(pairs, metric, POLYGON, MASK))[1]
                masks = _stats(_design_errors(pairs, metric, MASK, MASK))[1]
                (better if masks < mixed else worse).append(
                    f"{LABEL[metric]} {_f(mixed)} → {_f(masks)} %"
                )
            lines += [
                f"On {GRID_NAME[label]}, taking the test from its mask lowers the 95th-percentile",
                f"error for {', '.join(better) or 'no statistic'}"
                + (f", and raises it for {', '.join(worse)}." if worse else "."),
                "",
            ]
    along_worse = like_for_like_worse_along(by_grid, design)
    lines += [
        "Taking the test from its mask cancels the mask's error only where the two",
        "masks fall among the voxels alike along the dose gradient. The copies",
        "shifted across the field do: none moves along its field, so its mask's",
        "staircase along the gradient matches its twin's and most of the error",
        "cancels. That is, in effect, the case of a contour identical to the",
        "reference, where all of it cancels. The copies shifted along the field move",
        "0.5-1.5 mm up the gradient, a fraction of a voxel off their twins'",
        "staircase, and then the two masks carry independent errors that add: like",
        "for like is "
        + (
            "worse than taking the test from its contours on every statistic at both of the"
            if along_worse
            else "worse than taking the test from its contours on most statistics at the"
        ),
        "cohort's grids. A contour from another source differs from a consensus by",
        "far more than half a voxel, so it is the second case that applies.",
        "",
        "AutoSeg therefore takes every contour's DVH from its contours, whatever it",
        "is compared against, and only a consensus, which has none, from its mask",
        "(the *DVH from* column says which on every dose row). The consensus's error",
        "is then the same in every source's difference, and cancels when two sources",
        "are compared with each other; the price is that a contour identical to the",
        "consensus differs from it by that error. The V at x Gy columns are not",
        "tested here, because the Nelms truth has no V at x Gy for the shifted",
        "structures.",
        "",
    ]
    return lines


def design_summary(
    by_grid: dict[str, list[dict]], design: dict[str, list[dict]], label: str, kind: str
) -> dict[str, float]:
    """95th percentile of |error| over volume and clinical doses, per design."""
    pairs = _shift_pairs(by_grid[label], design.get(label, []))[kind]
    return {
        key: float(
            np.percentile(
                [abs(e) for m in DESIGN_METRICS for e in _design_errors(pairs, m, t, r)], 95
            )
        )
        for key, _design, t, r in DESIGNS
    }


#: The name this script's results are cached under (``validation_common``).
RESULTS_NAME = "dvh_mask_vs_polygon"


def load() -> dict:
    """This script's last results, as :func:`main` kept them."""
    return load_results(RESULTS_NAME, RESULT_TYPES)


def full_results_part(data: dict) -> list[str]:
    """Every table this script computes, for Supplementary 1's full results."""
    by_grid, extra = data["by_grid"], data["extra"]
    records = by_grid[NATIVE]
    design = extra.get("design", {})
    pairs = _pair(records)
    failures = [r for r in records if r["result"].error]
    staple_ok = all(c["identical"] for c in extra["staple"])

    lines = [
        f"{len(pairs)} structure/dose cases from Nelms et al. 2015, each through both paths.",
        "",
        "## The two paths",
        "",
        *table(
            ["", PATH_LABEL[POLYGON], PATH_LABEL[MASK]],
            [
                [
                    "Used for",
                    "Every structure stored as contours",
                    "A drawer's STAPLE consensus; a multi-observer consensus used as ground truth",
                ],
                [
                    "Region",
                    "The contours' own polygons, each standing for a slab one slice thick",
                    "The voxels of the mask rasterised from those contours on the CT grid",
                ],
                [
                    "Samples",
                    "Sub-cells weighted by the exact area of polygon inside them",
                    "Sub-samples of each whole voxel, equally weighted",
                ],
                ["Spacing", "The finest of 0.25, 0.5, 1 mm within 10 M samples", "The same rule"],
                ["Dose", "Trilinear, at each sample", "Trilinear, at each sample"],
            ],
            align="lll",
        ),
        "The dose sampling and the histogram are shared; only the region differs.",
        "What separates the two paths is therefore the mask's voxel staircase: it",
        "includes or leaves out whole voxels where the contour cuts through them.",
        "",
        "CT grids the masks were made on (voxel size in mm; columns × rows × slices):",
        "",
        *table(
            ["Contour spacing", "CT voxel (mm)", "CT size"],
            [
                [s, " × ".join(f"{v:g}" for v in g[0]), " × ".join(str(v) for v in g[1])]
                for s, g in sorted(extra["ct"].items())
            ],
        ),
    ]
    lines += accuracy_section(records)
    lines += difference_section(pairs)
    lines += test3_section(records)
    lines += grid_section(by_grid)
    lines += design_section(by_grid, design)
    lines += [
        "## STAPLE check",
        "",
        "Each structure's mask was given to STAPLE as three identical raters, with",
        "the application's default settings. "
        + (
            f"The consensus equalled the mask voxel for voxel on all {len(extra['staple'])}."
            if staple_ok
            else "Structures where the consensus differed: "
            + ", ".join(
                f"{c['structure']} ({c['spacing']})" for c in extra["staple"] if not c["identical"]
            )
            + "."
        ),
        "A consensus of raters who disagree has no contours to compare with, which is",
        "why this benchmark uses agreeing ones: the question here is the cost of the",
        "mask, not of the disagreement.",
        "",
    ]
    if failures:
        lines += ["## Failures", ""]
        lines += [f"- {r['method']}, {r['row'].structure}: {r['result'].error}" for r in failures]
        lines += [""]
    lines += [
        "## What this does not cover",
        "",
        "The Nelms structures are 3.6-12 cc. A mask's relative error grows as a",
        "structure shrinks against its voxels, so for smaller structures -- a",
        "cochlea, the optic chiasm -- expect larger differences than these, and for",
        "large organs smaller. Part A measures both paths on disc phantoms of",
        "2.5-20 mm radius, where the truth is exact for the contours as drawn. The",
        "design comparison uses rigid shifts of 0.5-1.5 mm; a test contour whose",
        "shape differs from the reference's is not tested, though a difference in",
        "shape makes the two masks' errors less alike still. Consensus masks of",
        "raters who disagree are a different question, answered by the STAPLE",
        "validation (Supplementary 5), not this.",
        "",
    ]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--nelms", type=Path, required=True, help="Folder of the Nelms et al. 2015 dataset."
    )
    args = parser.parse_args(argv)

    started = time.perf_counter()
    info = run_info()
    by_grid, extra = run(args.nelms)
    data = {
        "run": info,
        "seconds": time.perf_counter() - started,
        "by_grid": by_grid,
        "extra": extra,
    }
    print(f"kept the results in {save_results(RESULTS_NAME, data)}")
    from supplementary_1_dvh import render  # here: it imports this module

    try:
        for path in render():
            print(f"wrote {path}")
    except FileNotFoundError as missing:
        print(f"Supplementary 1 not written yet: {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
