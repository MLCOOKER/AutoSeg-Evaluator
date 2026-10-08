"""Supplementary 1 - DVH Validation Report, from the two DVH validations' results.

``validate_dvh_methods.py`` scores the DVH method AutoSeg ships, v2's and every
alternative considered against analytic truth (part A).
``validate_dvh_mask_vs_polygon.py`` measures how far a DVH from a binary mask, a
STAPLE consensus's, is from one from the contours, and which way a contour should
be compared with a consensus (part B). Each keeps its results and calls
:func:`render`, which writes the short report and the full results from both.

Usage, to rewrite both files from the last runs' results without computing again::

    python scripts/supplementary_1_dvh.py
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import numpy as np

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "src"))

import validate_dvh_mask_vs_polygon as part_b  # noqa: E402
import validate_dvh_methods as part_a  # noqa: E402
from validate_dvh_mask_vs_polygon import (  # noqa: E402
    ACROSS,
    ALONG,
    MASK,
    NATIVE,
    PHASES,
    POLYGON,
    _design_errors,
    _shift_pairs,
    _stats,
    design_summary,
    like_for_like_worse_along,
)
from validate_dvh_methods import (  # noqa: E402
    CLINICAL,
    DIRECTIONS,
    GRIDS,
    METHODS,
    PUBLISHED,
    PUBLISHED_TEST3,
    RADII,
    SHAPES,
    disc_errors,
    nelms_counts,
    with_corrected_lookup,
)
from validation_common import VALIDATION_DOCS, demote, md_table, stamp, unwrap  # noqa: E402

TITLE = "Supplementary 1 - DVH Validation Report"
REPORT = VALIDATION_DOCS / "Supplementary_1_DVH_Validation_Report.md"
FULL_RESULTS = REPORT.with_name(REPORT.stem + "_Full_Results.md")
SCRIPT_A = Path(part_a.__file__).name
SCRIPT_B = Path(part_b.__file__).name

#: The statistics the counts are over: the volume and the clinical doses.
SUBSET = ("volume_cc", *CLINICAL)
LABEL = {
    "volume_cc": "Volume",
    "dmean": "Dmean",
    "d99": "D99",
    "d95": "D95",
    "d5": "D5",
    "d1": "D1",
    "d0.03cc": "D0.03cc",
}
#: Each method as the report names it.
METHOD_NAME = {
    "dicompyler": "v1-v2: dicompyler-core",
    "dicompyler (lookup corrected)": "dicompyler-core, D*x* read correctly",
    "dicompyler-ss": "dicompyler-core, supersampled",
    "dicompyler-ss (lookup corrected)": "dicompyler-core, supersampled, D*x* read correctly",
    "mask": "Voxel mask",
    "mask-ss": "Voxel mask, sub-sampled",
    "polygon": "Contour polygons",
    "autoseg": "**AutoSeg v3**",
}
TEST_LABEL = {"1": "Test 1", "2": "Test 2", "2s": "Test 2, shifted"}
#: The three ways of taking a test contour's DVH difference from a reference, as
#: Supplementary 1 names them: (key, label, the test's path, the reference's path).
COMPARISONS = (
    ("masks", "Mask vs mask", MASK, MASK),
    ("mixed", "Contour vs mask (AutoSeg)", POLYGON, MASK),
    ("contours", "Contour vs contour", POLYGON, POLYGON),
)


# ---- Numbers ---------------------------------------------------------------------


def _count(records: list[dict], test: str, method: str) -> tuple[int, int]:
    """Statistics beyond 3 % of the truth, and those scored, without Dmin and Dmax."""
    counts = nelms_counts(records, test, method)
    return sum(counts[k][0] for k in SUBSET), sum(counts[k][1] for k in SUBSET)


def _published(test: str, system: str) -> int:
    return sum(PUBLISHED[test][system][k][0] for k in SUBSET)


def _test3_range(records: list[dict], method: str) -> tuple[float, float]:
    """The lowest and highest volume error over Test 3's four data sets.

    Each data set's is the mean of its five shapes', as the paper's Table III gives it.
    """
    lows, highs = [], []
    for spacing in ("1mm", "3mm"):
        for gradient in ("SI", "AP"):
            stats = np.array(
                [
                    r["test3"]
                    for r in records
                    if r["method"] == method
                    and "test3" in r
                    and r["row"].spacing == spacing
                    and r["row"].gradient == gradient
                ]
            )
            lo, hi = stats.mean(axis=0)[:2]
            lows.append(float(lo))
            highs.append(float(hi))
    return min(lows), max(highs)


def _published_test3(system: str) -> tuple[float, float]:
    rows = [values[system] for values in PUBLISHED_TEST3.values()]
    return min(r[0] for r in rows), max(r[1] for r in rows)


def _disc_worst(discs: list[dict], method: str) -> tuple[float, float]:
    """Median and largest of each disc case's worst clinical dose error (Gy)."""
    worst = np.abs([e["clinical"] for e in disc_errors(discs, method)])
    return float(np.nanmedian(worst)), float(np.nanmax(worst))


def _zero_d99(nelms: list[dict]) -> tuple[int, int]:
    """Nelms cases where v1-v2 reported D99 as 0 Gy though the truth is above it."""
    chosen = [r for r in nelms if r["method"] == "dicompyler"]
    zero = sum(
        1 for r in chosen if r["result"].metrics["d99"] == 0.0 and r["row"].truth["d99"] > 0.5
    )
    return zero, len(chosen)


def _rule_is_fine_everywhere(nelms: list[dict], discs: list[dict]) -> bool:
    """Whether AutoSeg's rule sampled every Nelms and disc structure as finely as 0.25 mm."""

    def key(r: dict) -> tuple:
        if r["benchmark"] == "nelms":
            row = r["row"]
            return ("nelms", row.structure, row.voxel, row.gradient)
        return ("discs", r["grid"], r["shape"], r["radius"], r["direction"], r["placement"])

    samples: dict[tuple, dict[str, int]] = {}
    for r in [*nelms, *discs]:
        if r["method"] in ("polygon", "autoseg"):
            samples.setdefault(key(r), {})[r["method"]] = r["result"].samples
    return all(s.get("polygon") == s.get("autoseg") for s in samples.values())


def _sweep(nelms: list[dict], spacing: float, main_mm: float) -> dict:
    """The polygon method at one sub-cell spacing, on Test 2."""
    name = "polygon" if spacing == main_mm else f"polygon @ {spacing:g} mm"
    chosen = [r for r in nelms if r["method"] == name and r["row"].test == "2"]
    counts = nelms_counts(nelms, "2", name)
    return {
        "beyond": sum(counts[k][0] for k in SUBSET),
        "total": sum(counts[k][1] for k in SUBSET),
        "worst": max(max(abs(counts[k][2]), abs(counts[k][3])) for k in SUBSET),
        "samples": float(np.median([r["result"].samples for r in chosen])),
        "seconds": float(np.median([r["result"].seconds for r in chosen])),
    }


def _large(large: list[dict]) -> list[dict]:
    """Per large structure: what AutoSeg's rule chose, its time and error, and 0.25 mm's time."""
    found = {(r["shape"], r["radius"], r["method"]): r for r in large}
    out = []
    for shape, radius in dict.fromkeys((r["shape"], r["radius"]) for r in large):
        ours = found[(shape, radius, "autoseg")]
        fine = found[(shape, radius, "polygon @ 0.25 mm")]
        chosen = next(
            (
                f"{s:g} mm"
                for s in (0.25, 0.5, 1.0)
                if found[(shape, radius, f"polygon @ {s:g} mm")]["result"].samples
                == ours["result"].samples
            ),
            "voxel centres",
        )
        truth = ours["truth"]
        worst = max(abs(ours["result"].metrics[k] - truth[k]) for k in CLINICAL)
        out.append(
            {
                "label": f"{shape}, {truth['volume_cc']:,.0f} cc",
                "volume": truth["volume_cc"],
                "chosen": chosen,
                "seconds": ours["result"].seconds,
                "samples": ours["result"].samples,
                "fine_seconds": fine["result"].seconds,
                "fine_samples": fine["result"].samples,
                "worst_mm": worst / part_a.LARGE_SLOPE,
            }
        )
    return out


def _grid_name(label: str) -> str:
    return "0.6 mm (Nelms CT)" if label == NATIVE else label


def _f(value: float, digits: int = 2) -> str:
    return "–" if not math.isfinite(value) else f"{value:.{digits}f}"


def _pct(value: float, digits: int = 1) -> str:
    return f"{value:+.{digits}f}"


def _stamp_line(a: dict, b: dict) -> str:
    if a["run"]["version"] == b["run"]["version"] and a["run"]["revision"] == b["run"]["revision"]:
        dates = sorted({a["run"]["date"], b["run"]["date"]})
        return (
            f"AutoSeg Evaluator {a['run']['version']}, {a['run']['revision']} · computed "
            f"{' and '.join(dates)} by `scripts/{SCRIPT_A}` and `scripts/{SCRIPT_B}`"
        )
    return f"Part A: {stamp(SCRIPT_A, a['run'])}. Part B: {stamp(SCRIPT_B, b['run'])}"


# ---- The report ------------------------------------------------------------------


def write_report(target: Path, a: dict, b: dict) -> None:
    nelms, discs, large = a["nelms"], a["discs"], a["large"]
    main_mm = a["settings"]["subsample_mm"]
    corrected = with_corrected_lookup(nelms)
    corrected_discs = with_corrected_lookup(discs)
    by_grid, design = b["by_grid"], b["extra"]["design"]
    coarse_labels = [g for g in by_grid if g != NATIVE]
    coarsest = coarse_labels[-1]
    dicompyler = re.search(r"dicompyler-core (\S+)", a["environment"])
    dicompyler_version = f" {dicompyler.group(1)}" if dicompyler else ""

    counts = {
        m: {t: _count(nelms + corrected, t, m) for t in TEST_LABEL}
        for m in (*METHODS, "dicompyler (lookup corrected)")
    }
    ranges = {m: _test3_range(nelms, m) for m in ("autoseg", "dicompyler")}
    paper = {s: {t: _published(t, s) for t in ("1", "2")} for s in ("Pinnacle3", "PlanIQ")}
    paper_ranges = {s: _published_test3(s) for s in ("Pinnacle3", "PlanIQ")}
    discs_all = discs + corrected_discs
    disc_worst = {m: _disc_worst(discs_all, m) for m in METHOD_NAME if disc_errors(discs_all, m)}
    zero, zero_of = _zero_d99(nelms)
    n_cases = len([r for r in nelms if r["method"] == "autoseg"])
    n_discs = len(discs) // len(METHODS)
    volumes = [r["row"].truth["volume_cc"] for r in nelms if r["method"] == "autoseg"]
    sweep = {s: _sweep(nelms, s, main_mm) for s in (1.0, 0.5, main_mm)}
    big = _large(large)
    rule_fine = _rule_is_fine_everywhere(nelms, discs)
    # A test contour shifted along the gradient against its twin as the reference:
    # per grid, comparison and statistic, the (median, 95th percentile, largest)
    # error in the DVH difference, as % of the reference's analytic value.
    along = {g: _shift_pairs(by_grid[g], design.get(g, []))[ALONG] for g in by_grid}
    along_error = {
        g: {
            key: {m: _stats(_design_errors(along[g], m, test, ref)) for m in SUBSET}
            for key, _label, test, ref in COMPARISONS
        }
        for g in by_grid
    }
    masks_largest = all(
        along_error[g]["masks"][m][1]
        > max(along_error[g]["mixed"][m][1], along_error[g]["contours"][m][1])
        for g in by_grid
        for m in SUBSET
    )
    mixed_between = all(
        along_error[g]["contours"][m][1] <= along_error[g]["mixed"][m][1]
        for g in by_grid
        for m in SUBSET
    )
    across = design_summary(by_grid, design, coarsest, ACROSS)
    along_worse = like_for_like_worse_along(by_grid, design)
    staple = b["extra"]["staple"]
    staple_same = sum(c["identical"] for c in staple)

    v3 = {t: counts["autoseg"][t] for t in TEST_LABEL}
    beats_both = all(v3[t][0] < min(paper[s][t] for s in paper) for t in ("1", "2"))
    lines = [
        f"# {TITLE}",
        "",
        f"{_stamp_line(a, b)}. Every table behind this report is in its "
        f"[full results]({FULL_RESULTS.name}).",
        "",
        "## Aim",
        "",
        "A dose-volume histogram (DVH) shows how much of a structure receives each dose, and",
        "AutoSeg Evaluator compares contours dosimetrically through statistics read from it,",
        "such as the mean dose (Dmean) or the dose received by the hottest 95 % of the",
        "structure (D95). AutoSeg Evaluator versions 1 and 2 computed DVHs with",
        f"dicompyler-core{dicompyler_version} [2]. Version 3 computes them with its own method,",
        "which integrates the dose over the contour polygons directly. This validation asks",
        "two questions:",
        "",
        "1. **Is the v3 method accurate?** It is scored against DVHs whose true values are",
        "   known exactly, beside v1-v2's dicompyler-core, the other methods considered, and",
        "   two commercial systems.",
        "2. **What does a consensus's binary mask cost?** A STAPLE consensus [3] exists only as",
        "   a binary mask, with no contours, so its DVH has to come from its voxels. What error",
        "   does that add when a test structure set is compared with a consensus? And should",
        "   the test's DVH be computed from a mask too, so that both are taken the same way?",
        "",
        "## Method",
        "",
        "### How AutoSeg v3 computes a DVH",
        "",
        "Each contour is treated as a slab one CT slice thick, centred on its plane. The slab",
        "is divided into sub-cells aligned with the CT voxels, at the finest spacing of 0.25,",
        "0.5 or 1 mm that keeps the structure within ten million samples, so organs at risk",
        "are sampled at 0.25 mm and only the largest structures more coarsely. Each sub-cell",
        "is weighted by the exact area of the contour inside it, and the dose is interpolated",
        "trilinearly at the centre of that area. The samples are collected in a dose",
        "histogram of 1 mGy bins. Dmin, Dmax and Dmean are kept exactly, and D*x*, the lowest",
        "dose received by the hottest *x* of the volume, is read to the centre of its bin. A",
        "structure that exists only as a mask is sampled over its voxels by the same spacing",
        "rule.",
        "",
        "### The methods compared",
        "",
        *md_table(
            ["Method", "How it samples the structure"],
            [
                [
                    METHOD_NAME["dicompyler"],
                    "Tests whether each dose-grid point in each contour plane lies inside the"
                    " contour; each point stands for a dose voxel's area × the gap between"
                    " planes",
                ],
                [
                    METHOD_NAME["dicompyler-ss"],
                    "The same, on a grid a quarter of the dose pixel",
                ],
                [
                    METHOD_NAME["mask"],
                    "The binary mask AutoSeg uses for its 3D metrics, with the dose at each"
                    " CT voxel's centre",
                ],
                [
                    METHOD_NAME["mask-ss"],
                    f"The same voxels, each sub-sampled every {main_mm:g} mm",
                ],
                [
                    METHOD_NAME["polygon"],
                    f"Sub-cells every {main_mm:g} mm, weighted by the exact area of contour"
                    " inside them",
                ],
                [
                    METHOD_NAME["autoseg"],
                    "Contour polygons, at 0.25, 0.5 or 1 mm by size, as above",
                ],
            ],
            "ll",
        ),
        "### Data",
        "",
        "**Nelms et al. analytic datasets [1].** Downloaded from the supplementary material of",
        "Nelms et al. (2015). Five shapes, each 24 mm across: a sphere, and a cylinder and a",
        "cone in two orientations, with the long axis running head to foot (so each slice is",
        "a circle) or front to back (so the slices are rectangles, or hyperbolic sections for",
        "the cone). The shapes were contoured every 0.2, 1, 2 and 3 mm on CTs with 0.6 mm",
        "pixels, and placed in linear dose fields rising 1 Gy/mm (16 Gy at the structure's",
        "centre) from front to back or from head to foot, on dose grids of 0.4 × 0.2 × 0.4",
        "mm and of 1, 2 and 3 mm cubes. Copies shifted half a dose voxel off the grid are",
        "included. The true value of every statistic is known in closed form, with each",
        "shape extended half a slice beyond its end contours, so a method is also charged",
        f"for how it fills the gap between contour planes. Volumes are {min(volumes):.1f} to",
        f"{max(volumes):.1f} cc. The {n_cases} structure and dose combinations make up the",
        "paper's three tests:",
        "",
        "- *Test 1:* contours every 0.2 mm, dose grids of 0.4-3 mm.",
        "- *Test 2:* contour spacing and dose grid both 1, 2 or 3 mm; and the same with the",
        "  shifted copies.",
        "- *Test 3:* the volume error along the whole DVH, every 0.1 Gy from 0 to 30 Gy, on",
        "  the Test 2 data at 1 and 3 mm.",
        "",
        "The paper's own results for two commercial systems, Pinnacle³ (v9.8) and PlanIQ",
        "(v2.1), on the same data are quoted beside AutoSeg's.",
        "",
        f"**Disc phantoms, built by the authors.** {n_discs} cases: stacks of circular contours",
        f"forming a {', a '.join(SHAPES[:-1])} or a {SHAPES[-1]} with a hole, of radius "
        + ", ".join(f"{r:g}" for r in RADII[:-1])
        + f" and {RADII[-1]:g} mm, each placed at {a['settings']['placements']} random",
        f"sub-voxel positions on two clinical CT grids ({' and '.join(GRIDS)}), in linear dose",
        f"fields rising 1 Gy/mm in {len(DIRECTIONS)} directions (within the slice plane,",
        "perpendicular to the slices, and at 45° between them). Each contour stands for a slab",
        "whose DVH has a closed",
        "form, so the truth is exact for the contours as drawn. With a gradient of 1 Gy/mm,",
        "an error in Gy is also the shift of the structure's boundary, in mm, that would",
        "cause it. The smallest discs are comparable to a cochlea.",
        "",
        f"**Large structures.** One sphere or cylinder per size, from {min(x['volume'] for x in big):,.0f}",
        f"to {max(x['volume'] for x in big):,.0f} cc, each timed on one machine, to weigh",
        "accuracy against computing time.",
        "",
        "**Contours against a consensus mask.** A test structure set compared with a STAPLE",
        "consensus has its DVH difference taken between a contour and a mask. To measure the",
        "error this adds, the shifted copies in the Nelms dataset were used: the same shapes",
        "moved by 0.5, 1 or 1.5 mm, whose analytic values are known. Each shifted copy played",
        "the test contour, and its unshifted twin the reference, converted to the binary mask a",
        "STAPLE consensus of identical raters returns (checked below). Both shared the same",
        "dose grid, CT grid and sub-pixel offset, so the true dose difference between them is",
        "known exactly, and each way of taking the difference can be scored against it. The",
        "copies come in two kinds:",
        "",
        "- *across the gradient*: copies whose movement does not climb the dose field, so the",
        "  true difference is 0 Gy;",
        "- *along the gradient*: copies whose movement climbs the 1 Gy/mm field, so the true",
        "  difference is 0.5-1.5 Gy, known exactly because the field is linear.",
        "",
        "The difference was taken three ways: the test from its contours and the reference from",
        "its mask (*contour vs mask*, AutoSeg's choice); both from masks (*mask vs mask*); and",
        "both from contours (*contour vs contour*, the lower limit, which a consensus reference",
        "does not allow). Masks were made on the Nelms CT (0.6 mm pixels) and again with its",
        "pixels enlarged to "
        + " and ".join(coarse_labels)
        + ", keeping the slices: the most common and the coarsest in-plane pixel sizes among the",
        "planning CTs of the authors' clinical cohort. A mask's error depends on where a",
        "structure falls among the voxels, so each coarser grid was laid at",
        f"{len(PHASES)} sub-pixel offsets.",
        "",
        "**Criterion.** As in Nelms et al., a statistic fails when it is more than 3 % from",
        "its analytic value. The statistics scored are the volume, Dmean, D99, D95, D5, D1 and",
        "D0.03cc. Dmin and Dmax are set aside, as the paper set them aside, being the least",
        "clinically relevant.",
        "",
        "## Results",
        "",
        "### The DVH method against analytic truth",
        "",
        "Statistics more than 3 % from the analytic value in Tests 1 and 2, and the range of",
        "the volume error along the whole DVH in Test 3:",
        "",
        *md_table(
            ["", "AutoSeg v3", "AutoSeg v1-v2 (dicompyler-core)", "Pinnacle³ [1]", "PlanIQ [1]"],
            [
                *(
                    [
                        TEST_LABEL[t],
                        f"{v3[t][0]}/{v3[t][1]}",
                        f"{counts['dicompyler'][t][0]}/{counts['dicompyler'][t][1]}",
                        *(
                            f"{paper[s][t]}/{v3[t][1]}" if t in paper[s] else "not reported"
                            for s in ("Pinnacle3", "PlanIQ")
                        ),
                    ]
                    for t in TEST_LABEL
                ),
                [
                    "Test 3: volume error (%)",
                    *(
                        f"{_pct(lo)} to {_pct(hi)}"
                        for lo, hi in (
                            ranges["autoseg"],
                            ranges["dicompyler"],
                            paper_ranges["Pinnacle3"],
                            paper_ranges["PlanIQ"],
                        )
                    ),
                ],
            ],
            "lrrrr",
        ),
        f"AutoSeg v1-v2 reported D99 as 0 Gy in {zero} of the {zero_of} Nelms cases, where the",
        "true D99 is well above it: dicompyler-core reads D*x* from the first dose bin whenever",
        "no later bin is nearer *x*, which happens once the coldest bin holds a few percent of",
        "the volume.",
        "",
        "Every method considered, on the same data. The disc phantom column is each case's",
        "worst error among Dmean, D99, D95, D5, D1 and D0.03cc, in Gy (equivalently mm):",
        f"median / largest over the {n_discs} cases.",
        "",
        *md_table(
            ["Method", "Test 1", "Test 2", "Test 2, shifted", "Disc phantoms (Gy)"],
            [
                [
                    METHOD_NAME[m],
                    *(f"{counts[m][t][0]}/{counts[m][t][1]}" for t in TEST_LABEL),
                    f"{_f(disc_worst[m][0])} / {_f(disc_worst[m][1])}",
                ]
                for m in (
                    "dicompyler",
                    "dicompyler (lookup corrected)",
                    "dicompyler-ss",
                    "mask",
                    "mask-ss",
                    "polygon",
                    "autoseg",
                )
            ],
            "lrrrr",
        ),
        "How finely to sample. The contour-polygon method at three sub-cell spacings on",
        "Test 2, with the samples and time for one structure against one dose (medians):",
        "",
        *md_table(
            ["Sub-cell spacing", "Test 2 beyond 3 %", "Worst error (%)", "Samples", "Seconds"],
            [
                [
                    f"{s:g} mm",
                    f"{v['beyond']}/{v['total']}",
                    _f(v["worst"], 1),
                    f"{v['samples']:,.0f}",
                    _f(v["seconds"]),
                ]
                for s, v in sorted(sweep.items(), reverse=True)
            ],
            "lrrrr",
        ),
        "For large structures, the time for one structure against one dose, with AutoSeg's",
        "rule and with 0.25 mm throughout, and AutoSeg's worst dose error expressed as the",
        "boundary shift that would cause it:",
        "",
        *md_table(
            [
                "Structure",
                "AutoSeg v3: spacing",
                "AutoSeg v3: seconds",
                "0.25 mm throughout: seconds",
                "AutoSeg v3: worst error (mm)",
            ],
            [
                [
                    x["label"],
                    x["chosen"],
                    _f(x["seconds"]),
                    _f(x["fine_seconds"]),
                    _f(x["worst_mm"], 3),
                ]
                for x in big
            ],
            "lrrrr",
        ),
        "### A test contour against a consensus mask",
        "",
        "Error in the DVH difference between a test structure and a reference shifted",
        "0.5-1.5 mm along the dose gradient (true difference 0.5-1.5 Gy), with each DVH",
        f"computed from its binary mask or its polygon contours, on {coarsest} pixels",
        f"({len(along[coarsest])} pairs over {len(PHASES)} offsets): 95th percentile / largest",
        "error (measured minus true difference), as % of the reference's analytic value.",
        "",
        *md_table(
            ["Statistic", *(label for _k, label, _t, _r in COMPARISONS)],
            [
                [
                    LABEL[m],
                    *(
                        f"{_f(along_error[coarsest][key][m][1])} /"
                        f" {_f(along_error[coarsest][key][m][2])}"
                        for key, _l, _t, _r in COMPARISONS
                    ),
                ]
                for m in SUBSET
            ],
            "lrrr",
        ),
        (
            "On every grid and every statistic, mask vs mask had the largest error"
            if masks_largest
            else "Mask vs mask did not have the largest error on every grid and statistic"
        )
        + (
            ", and contour vs mask fell between it and contour vs contour."
            if mixed_between
            else "."
        )
        + " The same table for the "
        + " and ".join(_grid_name(g) for g in by_grid if g != coarsest)
        + " grids is in the full results. For copies shifted across the gradient, which in"
        " effect coincide with the reference, the order reverses: over all statistics on"
        f" {coarsest} pixels, the 95th-percentile error was {across['masks']:.2f} % for mask vs"
        f" mask against {across['mixed']:.2f} % for contour vs mask.",
        "",
        "Given three identical masks, STAPLE returned the mask itself for "
        + (
            f"all {len(staple)} structures"
            if staple_same == len(staple)
            else f"{staple_same} of {len(staple)} structures"
        )
        + ", so a consensus of agreeing raters is exactly the mask scored here.",
        "",
        "## Findings",
        "",
    ]

    # The method's accuracy, beside the commercial systems.
    if beats_both:
        verdict = (
            f"It had fewer statistics beyond 3 % than either commercial system in both tests"
            f" ({v3['1'][0]} and {v3['2'][0]}, against {paper['PlanIQ']['1']} and"
            f" {paper['PlanIQ']['2']} for PlanIQ and {paper['Pinnacle3']['1']} and"
            f" {paper['Pinnacle3']['2']} for Pinnacle³)"
        )
    else:
        verdict = (
            f"It had {v3['1'][0]} and {v3['2'][0]} statistics beyond 3 % in Tests 1 and 2,"
            f" against {paper['PlanIQ']['1']} and {paper['PlanIQ']['2']} for PlanIQ and"
            f" {paper['Pinnacle3']['1']} and {paper['Pinnacle3']['2']} for Pinnacle³"
        )
    lo, hi = ranges["autoseg"]
    plo, phi = paper_ranges["PlanIQ"]
    nlo, nhi = paper_ranges["Pinnacle3"]
    lines += [
        f"- **The v3 DVH method is accurate.** {verdict}. Its volume error along the whole",
        f"  DVH ({_pct(lo)} to {_pct(hi)} %) spans {hi - lo:.1f} percentage points, against"
        f" {phi - plo:.1f} for PlanIQ ({_pct(plo)} to {_pct(phi)} %) and {nhi - nlo:.1f} for"
        f" Pinnacle³ ({_pct(nlo)} to {_pct(nhi)} %).",
    ]

    # Why dicompyler-core was replaced rather than repaired.
    dc, fixed = counts["dicompyler"], counts["dicompyler (lookup corrected)"]
    lines += [
        "- **Why dicompyler-core was replaced.** As used in v1-v2 it had",
        f"  {dc['1'][0]} and {dc['2'][0]} statistics beyond 3 % in Tests 1 and 2, and reported",
        f"  D99 as 0 Gy in {zero} of {zero_of} cases.",
    ]
    if fixed["1"][0] > v3["1"][0] and fixed["2"][0] > v3["2"][0]:
        lines += [
            f"  Reading D*x* correctly from its histogram still leaves {fixed['1'][0]} and"
            f" {fixed['2'][0]}, because it also samples the dose only once per dose voxel in",
            "  each contour plane, so correcting it would not have been enough.",
        ]

    # Why contour polygons rather than a mask.
    ms, pg = counts["mask-ss"], counts["autoseg"]
    mask_worse_small = disc_worst["mask-ss"][1] > disc_worst["autoseg"][1]
    lines += [
        "- **Why contour polygons rather than a mask.** On the Nelms data the sub-sampled",
        f"  mask had {ms['1'][0]}, {ms['2'][0]} and {ms['2s'][0]} statistics beyond 3 % in the",
        f"  three tests, against {pg['1'][0]}, {pg['2'][0]} and {pg['2s'][0]} for AutoSeg v3; on",
        f"  the disc phantoms its worst error reached {_f(disc_worst['mask-ss'][1])} Gy against"
        f" {_f(disc_worst['autoseg'][1])} Gy.",
    ]
    if mask_worse_small:
        lines += [
            "  A mask includes or leaves out whole voxels where a contour cuts through them,",
            "  which matters most for small structures; the polygons have no such staircase.",
        ]

    # Why the spacing rule.
    largest = max(big, key=lambda x: x["volume"])
    finest_best = sweep[main_mm]["beyond"] <= min(v["beyond"] for v in sweep.values())
    by_spacing = ", ".join(
        f"{v['beyond']} at {s:g} mm" for s, v in sorted(sweep.items()) if s != main_mm
    )
    lines += [
        f"- **Why the 0.25, 0.5 or 1 mm rule.** {main_mm:g} mm sub-cells were"
        + (" the most accurate tested" if finest_best else " tested")
        + f" on Test 2 ({sweep[main_mm]['beyond']} statistics beyond 3 %, against {by_spacing}),",
        "  but their number grows with a structure's volume. At 0.25 mm throughout, the",
        f"  largest structure ({largest['label'].split(', ')[1]}) took"
        f" {_f(largest['fine_seconds'], 0)} s; the rule sampled it at {largest['chosen']} in",
        f"  {_f(largest['seconds'], 1)} s, with a worst dose error equal to a"
        f" {_f(largest['worst_mm'])} mm boundary shift.",
    ]
    if rule_fine:
        lines += [
            "  Every Nelms and disc structure fell within the cap, so the rule sampled each at",
            "  0.25 mm and its results there are those of the finest method.",
        ]

    # A consensus DVH, and the design choice.
    other = next(g for g in coarse_labels if g != coarsest)
    lines += [
        "- **A DVH difference against a consensus carries the consensus's mask error.** When",
        "  a test structure set is compared with a STAPLE consensus, the error expected on"
        f" {coarsest} pixels is up to {_f(along_error[coarsest]['mixed']['dmean'][1], 1)} % for"
        f" Dmean and {_f(along_error[coarsest]['mixed']['d99'][1], 1)} % for D99 (95th"
        f" percentile; {_f(along_error[other]['mixed']['dmean'][1], 1)} % and"
        f" {_f(along_error[other]['mixed']['d99'][1], 1)} % on {other} pixels). It comes from",
        "  representing the consensus on the CT voxel grid: a mask includes or excludes whole",
        "  voxels where a contour passes through them, which can shift its boundary by up to",
        "  half a voxel.",
    ]
    if masks_largest and along_worse:
        lines += [
            "- **So every contour takes its DVH from its contours, and only a consensus from",
            "  its mask.** Computing the test's DVH from a mask as well (mask vs mask) does not",
            "  cancel the consensus's error but adds a second, independent one: for copies",
            "  shifted along the gradient, as any contour that genuinely differs from the",
            "  consensus is, it had the largest error on every grid and statistic. It helped only",
            "  for copies shifted across the gradient, which in effect coincide with the",
            "  reference. Because the consensus's error is the same for every source compared",
            "  with it, it cancels when sources are compared with each other.",
        ]
    else:
        lines += [
            "- **Mask vs mask was not worse on every grid and statistic for contours shifted",
            "  along the gradient,** so the choice to take every contour's DVH from its contours",
            "  needs the full results' design tables to be re-read.",
        ]
    lines += [
        f"- **Limits.** These values apply to the Nelms structures ({min(volumes):.1f}-"
        f"{max(volumes):.1f} cc) in a 1 Gy/mm dose gradient. Smaller structures, such as a",
        "  cochlea or the optic chiasm, and steeper gradients will show larger errors, and",
        "  larger structures or shallower gradients smaller ones. The shifts tested are rigid;",
        "  a test contour differing in shape makes the two masks' errors less alike still. A",
        "  consensus of raters who disagree is validated in Supplementary 5.",
        "",
        "## References",
        "",
        "1. Nelms B, Stambaugh C, Hunt D, Tonner B, Zhang G, Feygelman V. Methods, software and",
        "   datasets to verify DVH calculations against analytical values: twenty years late(r).",
        "   Med Phys. 2015;42(8):4435-4448.",
        "2. dicompyler-core. GitHub. https://github.com/dicompyler/dicompyler-core",
        "3. Warfield SK, Zou KH, Wells WM. Simultaneous truth and performance level estimation",
        "   (STAPLE): an algorithm for the validation of image segmentation. IEEE Trans Med",
        "   Imaging. 2004;23(7):903-921.",
        "",
        "## Reproduce",
        "",
        "```",
        f"python scripts/{SCRIPT_A} --nelms <Nelms et al. dataset folder>",
        f"python scripts/{SCRIPT_B} --nelms <Nelms et al. dataset folder>",
        "```",
        "",
        "The Nelms et al. datasets are the paper's supplementary material and are not",
        "redistributed with AutoSeg. Each script rewrites this report once both have run.",
        f"The first takes about {a['seconds'] / 60:.0f} minutes and its full results quote",
        "timings, so it should run on an otherwise idle machine. `python",
        "scripts/supplementary_1_dvh.py` rewrites the report from the last runs' results.",
        "Regenerate this report rather than editing it.",
        "",
    ]
    target.write_text(unwrap(lines), encoding="utf-8")


def write_full_results(target: Path, a: dict, b: dict) -> None:
    lines = [
        f"# {TITLE}: full results",
        "",
        f"{_stamp_line(a, b)}. The summary is [Supplementary 1]({REPORT.name}). Regenerate",
        "this file rather than editing it.",
        "",
        "## Part A: the DVH method against analytic truth",
        "",
        f"From `scripts/{SCRIPT_A}`, a run of {a['seconds'] / 60:.0f} minutes.",
        "",
        *demote(part_a.full_results_part(a)),
        "## Part B: DVH from a mask against DVH from the contours",
        "",
        f"From `scripts/{SCRIPT_B}`, a run of {b['seconds'] / 60:.0f} minutes.",
        "",
        *demote(part_b.full_results_part(b)),
        "## Reproduce",
        "",
        "```",
        f"python scripts/{SCRIPT_A} --nelms <Nelms et al. dataset folder>",
        f"python scripts/{SCRIPT_B} --nelms <Nelms et al. dataset folder>",
        "```",
        "",
    ]
    target.write_text(unwrap(lines), encoding="utf-8")


def render() -> list[Path]:
    """Write Supplementary 1 and its full results from both runs' kept results.

    Raises :class:`FileNotFoundError` naming what is missing if either has not run.
    """
    try:
        a = part_a.load()
    except FileNotFoundError as missing:
        raise FileNotFoundError(f"{missing}; run scripts/{SCRIPT_A}") from None
    try:
        b = part_b.load()
    except FileNotFoundError as missing:
        raise FileNotFoundError(f"{missing}; run scripts/{SCRIPT_B}") from None
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    write_report(REPORT, a, b)
    write_full_results(FULL_RESULTS, a, b)
    return [REPORT, FULL_RESULTS]


def main() -> int:
    try:
        written = render()
    except FileNotFoundError as missing:
        print(missing)
        return 1
    for path in written:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
