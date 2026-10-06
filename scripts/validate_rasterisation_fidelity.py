"""AutoSeg's rasteriser in the authors' synthetic RTSTRUCT rasterisation benchmark.

The benchmark asks which binary mask best preserves the polygon an RTSTRUCT
actually stores. It wrote 108 CT/RTSTRUCT configurations of 14 shape families;
converted them with six complete converters (dcmrtstruct2nii, Plastimatch,
PlatiPy, DicomRTTool, PyRaDiSe, RT-Utils) and with direct rasterisers; and
scored every plane against the exact fraction of each pixel the polygon covers.
Its primary error is the area a mask gains plus the area it loses, against which
the majority-coverage mask (a pixel is in when at least half of it is) is the
attainable optimum.

This script puts AutoSeg Evaluator's own rasteriser into that benchmark on equal
terms. It rasterises the benchmark's own fixtures with ``mask_with_reading`` --
both backends: ``continuous``, shipped since v3, and ``legacy``, v1 and v2 --
maps each mask onto the benchmark's reference grid with its own
``canonicalize``, scores every plane with its own ``metrics``, against its own
precomputed references, and aggregates with its own rules. It then checks the
aggregation by reproducing the benchmark's published tables from its raw rows.
The benchmark's evidence is read, never written. With ``--clinical`` it also
compares the two backends on a folder of real structure sets.

It writes Supplementary 2, a short report (aim, method, results, findings), and
its full results, and keeps what it computed so ``--render-only`` can rewrite
both without computing again.

Usage::

    python scripts/validate_rasterisation_fidelity.py \\
        --study "<benchmark>/project" [--clinical <CT + RTSTRUCT folder>] \\
        [--work <scratch folder> --reuse]

The benchmark folder is about 5 GB and does not belong in this repository.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from validation_common import (  # noqa: E402
    VALIDATION_DOCS,
    load_results,
    long_path,
    md_table,
    run_info,
    save_results,
    stamp,
    unwrap,
)

BACKENDS = ("continuous", "legacy")
METHOD = {"continuous": "AutoSeg (continuous)", "legacy": "AutoSeg (legacy)"}
SHIPPED = METHOD["continuous"]
BASELINES = ["dcmrtstruct2nii", "DicomRTTool", "Plastimatch", "PlatiPy", "PyRaDiSe", "RT-Utils"]
FOUR = ["dcmrtstruct2nii", "DicomRTTool", "Plastimatch", "PlatiPy"]
BOUND = "majority_bound"
DIRECT = [
    "majority_bound",
    "supersample_32",
    "vtk_zero",
    "skimage_float",
    "vtk_default",
    "vtk_tol1e3",
    "opencv_integer",
    "opencv_shift8",
]
#: Every direct method the study ran (study/run.py ``ALGORITHMS``), for the check.
ALGORITHMS = DIRECT + ["supersample_4", "supersample_8", "supersample_16"]
#: The study's per-ROI metrics, averaged over planes (study/analyze.py).
METRICS = [
    "error_normalized",
    "excess_normalized",
    "error_mm2",
    "minimum_error_mm2",
    "excess_mm2",
    "reference_area_mm2",
    "signed_bias_mm2",
    "geometric_dice",
    "topology_preserved",
    "centre_disagreements",
    "centre_disagreements_unambiguous",
    "ambiguous_centres",
    "centroid_mm",
    "boundary_mean_mm",
    "hd95_mm",
    "boundary_max_mm",
    "agreement_05mm",
    "agreement_1mm",
    "centre_dice",
    "majority_dice",
]
KEYS = ["case_id", "block", "family", "slice", "method", "status", "nonempty_reference"]
EXTRA = ["mask_area_mm2"]
#: The study's reporting tolerance for ties, in normalised error.
TIE = 1e-7


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---- The study's own code, staged where imports can reach it --------------------


def stage_study_code(study: Path, work: Path) -> dict[str, str]:
    """Copy the study's scoring code to a short path and import from there.

    Its own folder is too deep for Python's importer on Windows. The copies are
    byte-identical; their hashes go in the report.
    """
    target = work / "study_src"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(study / "study", target / "study")
    vendor = study / "vendor" / "RTMaskConformanceTest" / "src"
    shutil.copytree(vendor, target / "vendor" / "RTMaskConformanceTest" / "src")
    sys.path.insert(0, str(target / "vendor" / "RTMaskConformanceTest" / "src"))
    sys.path.insert(0, str(target))
    return {p.name: _sha(p) for p in sorted((study / "study").glob("*.py"))}


# ---- AutoSeg on the study's fixtures ---------------------------------------------


def stage_fixture(study: Path, work: Path, case_id: str) -> Path:
    """The files AutoSeg and the scorer read, copied to a path SimpleITK can open."""
    source = study / "artifacts" / "core" / "fixtures" / case_id
    local = work / "fixtures" / case_id
    if not (local / "polygons.json").exists():
        (local / "ct").mkdir(parents=True, exist_ok=True)
        for dcm in (source / "ct").glob("*.dcm"):
            shutil.copy2(dcm, local / "ct" / dcm.name)
        for name in ("rtstruct.dcm", "reference.nii.gz", "polygons.json", "config.json"):
            shutil.copy2(source / name, local / name)
    return local


def rasterise(config: dict, local: Path, families: list[str]) -> dict[str, dict]:
    """Every family through each AutoSeg backend, mapped onto the study's grid."""
    import pydicom
    import SimpleITK as sitk
    from study.converters import canonicalize

    from autoseg_evaluator.core.masks import (
        MaskConversionError,
        mask_with_reading,
        read_dicom_image,
    )

    ct = read_dicom_image(str(local / "ct"))
    rtss = pydicom.dcmread(str(local / "rtstruct.dcm"))
    reference = sitk.ReadImage(str(local / "reference.nii.gz"))
    numbers = {str(s.ROIName): int(s.ROINumber) for s in rtss.StructureSetROISequence}
    tolerance = 0.05 if config["large"] else 1e-4  # as the study allowed every converter
    out = {}
    for backend in BACKENDS:
        statuses: dict[str, dict] = {}
        masks: dict[str, np.ndarray] = {}
        for family in families:
            started = time.perf_counter()
            try:
                mask, notes = mask_with_reading(ct, rtss, numbers[family], backend=backend)
            except MaskConversionError as exc:
                statuses[family] = {"status": "execution_error", "detail": str(exc)}
                continue
            seconds = time.perf_counter() - started
            try:
                canonical, _geometry = canonicalize(mask, reference, tolerance)
            except ValueError as exc:
                statuses[family] = {"status": "geometry_mismatch", "detail": str(exc)}
                continue
            masks[family] = canonical
            statuses[family] = {"status": "success", "seconds": seconds, "notes": list(notes)}
        out[backend] = {"statuses": statuses, "masks": masks}
    return out


def score(config: dict, study: Path, local: Path, results: dict) -> list[dict]:
    """Every plane of every family, scored exactly as the study scored converters."""
    from study.geometry import compose
    from study.metrics import metrics

    records = json.loads((local / "polygons.json").read_text())
    references = study / "artifacts" / "core" / "references" / config["id"]
    rows = []
    for family, item in records.items():
        ref = np.load(references / f"{family}.npz")
        for z, rings in item["planes"].items():
            p = compose(rings, item["mode"])
            f, centre, ambiguity = ref["f_" + z], ref["centre_" + z], ref["ambiguity_" + z]
            for backend in BACKENDS:
                status = results[backend]["statuses"][family]
                row = {
                    "case_id": config["id"],
                    "block": config["block"],
                    "family": family,
                    "slice": int(z),
                    "method": METHOD[backend],
                    "experiment": "converter",
                    "status": status["status"],
                    "nonempty_reference": bool(rings),
                }
                if status["status"] == "success":
                    mask = results[backend]["masks"][family][int(z)]
                    row.update(metrics(mask, p, f, centre, ambiguity, config["spacing"]))
                else:
                    row["detail"] = status.get("detail", "")
                rows.append(row)
    return rows


def run_autoseg(
    study: Path, work: Path, manifest: dict, reuse: bool = False
) -> tuple[pd.DataFrame, dict]:
    raw = work / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    frames = []
    timings: list[dict] = []
    notes: dict[str, set] = {b: set() for b in BACKENDS}
    configurations = manifest["configurations"]
    for n, config in enumerate(configurations, 1):
        destination = raw / f"{config['id']}.jsonl"
        statuses_file = raw / f"{config['id']}.status.json"
        if reuse and destination.exists() and statuses_file.exists():
            # A finished configuration from an earlier run in this work folder.
            frames.append(
                pd.read_json(io.StringIO(destination.read_text(encoding="utf-8")), lines=True)
            )
            statuses = json.loads(statuses_file.read_text(encoding="utf-8"))
        else:
            local = stage_fixture(study, work, config["id"])
            results = rasterise(config, local, manifest["families"])
            for backend in BACKENDS:
                np.savez_compressed(
                    work / f"masks_{backend}_{config['id']}.npz", **results[backend]["masks"]
                )
            rows = score(config, study, local, results)
            destination.write_text(
                "\n".join(json.dumps(row, allow_nan=False) for row in rows) + "\n",
                encoding="utf-8",
            )
            statuses = {b: results[b]["statuses"] for b in BACKENDS}
            statuses_file.write_text(json.dumps(statuses), encoding="utf-8")
            frames.append(pd.DataFrame(rows))
        for backend in BACKENDS:
            for status in statuses[backend].values():
                if status["status"] == "success":
                    timings.append({"backend": backend, "seconds": status["seconds"]})
                    notes[backend].update(status.get("notes", []))
        print(f"  AutoSeg {n}/{len(configurations)} {config['id']}", flush=True)
    return pd.concat(frames, ignore_index=True), {"timings": timings, "notes": notes}


# ---- The study's own aggregation ------------------------------------------------------


def load_study_rows(study: Path) -> pd.DataFrame:
    keep = KEYS + METRICS + EXTRA
    frames = []
    for path in sorted((study / "artifacts" / "core" / "raw").glob("*.jsonl")):
        frame = pd.read_json(io.StringIO(path.read_text(encoding="utf-8")), lines=True)
        frames.append(frame[[c for c in keep if c in frame.columns]])
    return pd.concat(frames, ignore_index=True)


def roi_table(df: pd.DataFrame) -> pd.DataFrame:
    """Planes averaged within each ROI/configuration, as study/analyze.py does."""
    good = df[(df.status == "success") & df.nonempty_reference.astype(bool)].copy()
    good["topology_preserved"] = good["topology_preserved"].astype(float)
    return (
        good.groupby(["case_id", "block", "family", "method"], observed=True)[METRICS]
        .mean()
        .reset_index()
    )


def weighted_summary(rois: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """study/analyze.py ``weighted_summary``: configurations within a family, then
    families weighted equally."""
    family = rois.groupby(["method", "family"], observed=True).agg(
        normalized_error=("error_normalized", "mean"),
        normalized_excess=("excess_normalized", "mean"),
        geometric_dice=("geometric_dice", "mean"),
        topology_preservation=("topology_preserved", "mean"),
    )
    overall = family.groupby("method").mean()
    for method, g in rois.groupby("method"):
        overall.loc[method, "roi_cases"] = len(g)
        overall.loc[method, "median_normalized_error"] = g.error_normalized.median()
        overall.loc[method, "mean_error_mm2"] = g.error_mm2.mean()
        overall.loc[method, "boundary_mean_mm"] = g.boundary_mean_mm.mean()
        overall.loc[method, "hd95_mm"] = g.hd95_mm.mean()
        overall.loc[method, "agreement_05mm"] = g.agreement_05mm.mean()
    return overall.sort_values("normalized_error"), family


def common(rois: pd.DataFrame, methods: list[str]) -> pd.DataFrame:
    """study/analyze.py ``common``: ROI/configurations every named method succeeded on."""
    sub = rois[rois.method.isin(methods)]
    counts = sub.groupby(["case_id", "family"]).method.nunique()
    ids = set(counts[counts == len(methods)].index)
    return sub.loc[np.array([(c, f) in ids for c, f in zip(sub.case_id, sub.family)], dtype=bool)]


def check_against_published(rois: pd.DataFrame, study: Path) -> list[dict]:
    """Our aggregation of the study's raw rows, against the tables it published."""
    report = study / "artifacts" / "core" / "report"
    checks = []
    for name, subset in (
        ("six_way_common", common(rois, BASELINES)),
        ("four_way_nonsquare_common", common(rois[rois.block != "square_companion"], FOUR)),
        ("algorithm_summary", rois[rois.method.isin(ALGORITHMS)]),
    ):
        ours, _ = weighted_summary(subset)
        published = pd.read_csv(io.StringIO((report / f"{name}.csv").read_text())).set_index(
            "method"
        )
        worst = max(
            abs(ours.loc[m, "normalized_error"] - published.loc[m, "normalized_error"])
            for m in published.index
        )
        checks.append({"table": name, "methods": len(published), "largest_difference": worst})
    return checks


# ---- Frame invariance and absent planes -------------------------------------------------


def frame_changes(manifest: dict, study: Path, work: Path) -> pd.DataFrame:
    """Voxels a rigid change of frame moves, as the study counts them for converters."""
    phases = [c for c in manifest["configurations"] if c["block"] == "phase"]
    rows = []
    for config in manifest["configurations"]:
        if config["block"] not in ("oblique", "large"):
            continue
        base = next(b for b in phases if b["phase"] == config["phase"])
        for backend in BACKENDS:
            a = np.load(work / f"masks_{backend}_{base['id']}.npz")
            b = np.load(work / f"masks_{backend}_{config['id']}.npz")
            for family in manifest["families"]:
                if family not in a.files or family not in b.files:
                    continue
                ra = np.load(
                    study / "artifacts" / "core" / "references" / base["id"] / f"{family}.npz"
                )
                rb = np.load(
                    study / "artifacts" / "core" / "references" / config["id"] / f"{family}.npz"
                )
                ambiguous = np.stack(
                    [ra[f"ambiguity_{z}"] | rb[f"ambiguity_{z}"] for z in range(9)]
                )
                changed = a[family] != b[family]
                rows.append(
                    {
                        "method": METHOD[backend],
                        "block": config["block"],
                        "family": family,
                        "changed": int(changed.sum()),
                        "changed_unambiguous": int((changed & ~ambiguous).sum()),
                    }
                )
    return pd.DataFrame(rows)


# ---- The benchmark's software versions ------------------------------------------------

#: The converters as the benchmark's lock file names them.
LOCKED = {
    "dcmrtstruct2nii": "dcmrtstruct2nii",
    "DicomRTTool": "dicomrttool",
    "PlatiPy": "platipy",
    "PyRaDiSe": "pyradise",
    "RT-Utils": "rt-utils",
}


def converter_versions(study: Path) -> dict[str, str]:
    """Each converter's version, from the benchmark's lock file and software audit."""
    versions: dict[str, str] = {}
    lock = (study / "requirements.lock").read_text(encoding="utf-8")
    for name, package in LOCKED.items():
        found = re.search(rf"^{re.escape(package)}==([^\s\\]+)", lock, re.MULTILINE | re.IGNORECASE)
        if found:
            versions[name] = found.group(1)
    audit = (study / "SOFTWARE_AUDIT.md").read_text(encoding="utf-8")
    found = re.search(r"Plastimatch (\d+(?:\.\d+)+)", audit)
    if found:
        versions["Plastimatch"] = found.group(1)
    return versions


# ---- A clinical structure set: v1-v2's rasteriser against v3's ---------------------------


def clinical_comparison(folder: Path) -> dict:
    """Every structure in a folder of CT + RTSTRUCTs through both backends.

    The structure sets are labelled A, B, ... and only organ names and numbers
    are kept: no file names, UIDs, dates or patient details.
    """
    from compare_rasterisers import COMPARABLE, compare_rtss, find_rtstructs

    from autoseg_evaluator.core.masks import read_dicom_image, read_rtstruct

    image = read_dicom_image(str(folder))
    rows = []
    for idx, path in enumerate(find_rtstructs(folder)):
        label = f"RTSS {chr(ord('A') + idx)}"
        print(f"  clinical: {label}", flush=True)
        rows.extend(compare_rtss(label, read_rtstruct(str(path)), image, repeat=1))
    return {
        "ct": {"size": list(image.GetSize()), "spacing": list(image.GetSpacing())},
        "structure_sets": len({r["rtss"] for r in rows}),
        "rows": [r for r in rows if r["status"] in COMPARABLE],
        "not_compared": len([r for r in rows if r["status"] not in COMPARABLE]),
    }


#: Size bands for the clinical comparison, by v1-v2's volume (cc).
SIZE_BANDS = ((0.0, 1.0), (1.0, 10.0), (10.0, 100.0), (100.0, math.inf))


def _band(lo: float, hi: float) -> str:
    if lo == 0:
        return f"Under {hi:g} cc"
    if math.isinf(hi):
        return f"Over {lo:g} cc"
    return f"{lo:g}-{hi:g} cc"


def clinical_bands(clinical: dict) -> list[dict]:
    out = []
    for lo, hi in SIZE_BANDS:
        rows = [r for r in clinical["rows"] if lo <= r["legacy_cc"] < hi and r["legacy_cc"] > 0]
        if not rows:
            continue
        change = [100 * r["delta_cc"] / r["legacy_cc"] for r in rows]
        out.append(
            {
                "label": _band(lo, hi),
                "n": len(rows),
                "change": float(np.median(change)),
                "dice": float(np.median([r["dice"] for r in rows])),
            }
        )
    return out


# ---- Report -------------------------------------------------------------------------------

SCRIPT = Path(__file__).name
TITLE = "Supplementary 2 - Binary Mask Rasteriser Validation Report"
REPORT = VALIDATION_DOCS / "Supplementary_2_Binary_Mask_Rasteriser_Validation_Report.md"
FULL_RESULTS = REPORT.with_name(REPORT.stem + "_Full_Results.md")
#: The name this script's results are cached under (``validation_common``).
RESULTS_NAME = "rasterisation"
#: How the reports name each method.
NAME = {
    SHIPPED: "AutoSeg v3",
    METHOD["legacy"]: "AutoSeg v1-v2",
    BOUND: "Best possible binary mask",
}


def _pct(value: float) -> str:
    return (
        "–"
        if value is None or (isinstance(value, float) and math.isnan(value))
        else f"{100 * value:.2f} %"
    )


def _signed(value: float) -> str:
    """A signed number to two decimals, with no sign on a value that rounds to zero."""
    if math.isnan(value):
        return "–"
    return "0.00" if abs(value) < 0.005 else f"{value:+.2f}"


def _num(value: float, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    text = f"{value:.{digits}f}"
    return text[1:] if text.startswith("-") and float(text) == 0 else text  # no "-0.00"


def _table(headers: list[str], rows: list[list[str]], numeric: bool = True) -> list[str]:
    return md_table(headers, rows, "l" + ("r" if numeric else "l") * (len(headers) - 1))


def _summary_rows(summary: pd.DataFrame, methods: list[str], label=lambda m: m) -> list[list[str]]:
    rows = []
    for method in summary.index:
        if method not in methods:
            continue
        r = summary.loc[method]
        name = label(method)
        if method in METHOD.values():
            name = f"**{name}**"
        rows.append(
            [
                name,
                _pct(r.normalized_error),
                _pct(r.normalized_excess),
                _num(r.mean_error_mm2),
                _num(r.geometric_dice, 4),
                f"{int(r.roi_cases)}",
            ]
        )
    return rows


SUMMARY_HEADERS = [
    "Method",
    "Area error",
    "Beyond the optimum",
    "Mean error per plane (mm²)",
    "Geometric Dice",
    "ROI cases",
]


def _label(method: str) -> str:
    return {
        "majority_bound": "majority-area mask (the attainable optimum)",
        "supersample_32": "32 × 32 supersampling",
    }.get(method, method)


def _restrict(rois: pd.DataFrame, methods: list[str], ids) -> pd.DataFrame:
    return rois[rois.method.isin(methods) & rois.set_index(["case_id", "family"]).index.isin(ids)]


def analyse(context: dict) -> dict:
    """The comparisons both reports draw on, from the per-ROI table."""
    rois: pd.DataFrame = context["rois"]
    methods7 = BASELINES + [SHIPPED, METHOD["legacy"], BOUND]
    seven_ids = common(rois, BASELINES + [SHIPPED]).set_index(["case_id", "family"]).index.unique()
    seven = _restrict(rois, methods7, seven_ids)
    seven_summary, _ = weighted_summary(seven)
    seven_no_tiny, _ = weighted_summary(seven[seven.family != "tiny"])

    non_square = rois[rois.block != "square_companion"]
    five_ids = common(non_square, FOUR + [SHIPPED]).set_index(["case_id", "family"]).index.unique()
    five = _restrict(non_square, FOUR + [SHIPPED, METHOD["legacy"], BOUND], five_ids)
    five_summary, _ = weighted_summary(five)
    five_no_tiny, _ = weighted_summary(five[five.family != "tiny"])

    domain_ids = rois[rois.method == SHIPPED].set_index(["case_id", "family"]).index.unique()
    domain = _restrict(rois, DIRECT + [SHIPPED, METHOD["legacy"]], domain_ids)
    domain_summary, domain_family = weighted_summary(domain)
    domain_no_tiny, _ = weighted_summary(domain[domain.family != "tiny"])

    s = seven_summary
    best_converter = s.loc[[m for m in BASELINES if m in s.index]].normalized_error.idxmin()
    order = s.loc[[m for m in BASELINES + [SHIPPED] if m in s.index]].normalized_error.sort_values()
    paired = common(rois, [SHIPPED, best_converter]).pivot_table(
        index=["case_id", "family"], columns="method", values="error_normalized"
    )
    gap = paired[SHIPPED] - paired[best_converter]
    no_tiny = domain[domain.family != "tiny"]
    moved = context["frame_changes"]
    return {
        "seven": seven_summary,
        "seven_no_tiny": seven_no_tiny,
        "five": five_summary,
        "five_no_tiny": five_no_tiny,
        "domain": domain_summary,
        "domain_family": domain_family,
        "domain_no_tiny": domain_no_tiny,
        "no_tiny_rows": no_tiny,
        "methods7": methods7,
        "best_converter": best_converter,
        "rank": list(order.index).index(SHIPPED) + 1,
        "paired": {
            "lower": int((gap < -TIE).sum()),
            "tied": int((abs(gap) <= TIE).sum()),
            "higher": int((gap > TIE).sum()),
            "n": len(gap),
        },
        "legacy_is_platipy": abs(
            s.loc[METHOD["legacy"], "normalized_error"] - s.loc["PlatiPy", "normalized_error"]
        )
        < 1e-12,
        "bias": {
            m: float(no_tiny[no_tiny.method == m].signed_bias_mm2.mean())
            for m in (SHIPPED, METHOD["legacy"], BOUND)
            if (no_tiny.method == m).any()
        },
        "moved": {
            block: int(
                moved[(moved.method == SHIPPED) & (moved.block == block)].changed_unambiguous.sum()
            )
            for block in ("oblique", "large")
        },
    }


def write_report(target: Path, context: dict, a: dict) -> None:
    """Supplementary 2: what was validated, how, what was found, and what it decided."""
    manifest = context["manifest"]
    capability: pd.DataFrame = context["capability"]
    versions = context["versions"]
    s, s_nt = a["seven"], a["seven_no_tiny"]
    best = a["best_converter"]
    attempts = int(capability.loc[SHIPPED].sum())
    converted = {
        m: int(capability.loc[m].get("success", 0))
        for m in [SHIPPED, METHOD["legacy"], *BASELINES]
        if m in capability.index
    }
    n_cases = int(s.loc[SHIPPED, "roi_cases"])
    clinical = context.get("clinical")

    def name(method: str) -> str:
        return NAME.get(method, method + (f" {versions[method]}" if method in versions else ""))

    accuracy_rows = [
        [
            ("**" + name(m) + "**") if m == SHIPPED else name(m),
            _pct(s.loc[m, "normalized_error"]),
            _pct(s_nt.loc[m, "normalized_error"]),
        ]
        for m in s.sort_values("normalized_error").index
        if m in a["methods7"]
    ]
    lines = [
        f"# {TITLE}",
        "",
        f"{stamp(SCRIPT, context['run'])}. Every table behind this report is in its "
        f"[full results]({FULL_RESULTS.name}).",
        "",
        "## Aim",
        "",
        "AutoSeg Evaluator's 3D metrics (Supplementary 4) and the DVH of a STAPLE consensus",
        "(Supplementary 1) are computed on binary masks, so they can be no more accurate than",
        "the conversion of each contour into a mask, its rasterisation. AutoSeg v1 and v2",
        "rasterised as PlatiPy does, snapping every contour vertex to the nearest voxel before",
        "filling. Version 3 replaced this with a rasteriser adapted from dcmrtstruct2nii [1],",
        "which keeps every vertex where it is. This validation asks three questions:",
        "",
        "1. How closely do v3's masks match the contours they come from, compared with six",
        "   widely used converters, with v1-v2's rasteriser, and with the best any binary mask",
        "   can do?",
        "2. Does it convert every kind of contour: holes, nested and overlapping contours, tiny",
        "   regions, rotated image frames and coordinates far from the origin?",
        "3. What does the change from v1-v2's rasteriser do to masks of real structures?",
        "",
        "## Method",
        "",
        "### How AutoSeg v3 rasterises",
        "",
        "Each contour vertex is converted to continuous voxel coordinates, so a vertex at",
        "voxel position 203.78 stays at 203.78 instead of being rounded to 204. Each contour",
        "is assigned to its nearest CT slice, and each region is filled with a scanline",
        "algorithm that includes a voxel when its centre lies inside the region. A voxel",
        "centre lying exactly on a contour edge is assigned to one side only, where",
        "dcmrtstruct2nii counts it inside on both; away from such ties the two fill identical",
        "voxels. Contours stored as CLOSEDPLANAR_XOR, which encode holes and overlaps, are read",
        "through the same contour reader as AutoSeg's 2D metrics (Supplementary 3).",
        "",
        "### Data: a synthetic benchmark built by the authors",
        "",
        f"{len(manifest['families'])} shape families: sphere and ellipsoid cross-sections, a",
        "square and a rectangle, a triangle, a concave L, an annulus, nested shells,",
        "disconnected components, a narrow bridge, a tiny 0.36 mm square, a keyhole, an",
        "irregular star, and overlapping contours stored as CLOSEDPLANAR_XOR. Each was written",
        f"as a CT and RTSTRUCT in {len(manifest['configurations'])} configurations that vary",
        "the sub-pixel position, rotation, anisotropic pixel spacing, size, vertex density,",
        "feature width, distance from pixel boundaries, an oblique image frame, and",
        f"coordinates 10⁵ mm from the origin: {attempts:,} structure and configuration cases",
        "in all. Each was converted by AutoSeg v3, by AutoSeg v1-v2's rasteriser, and by six",
        "converters: "
        + ", ".join(name(m) for m in BASELINES)
        + ". The best possible binary mask was also computed for every case (below).",
        "",
        "### Measuring a mask's error",
        "",
        "The reference on each plane is the polygon as stored in, and read back from, the",
        "RTSTRUCT. Each pixel stands for its whole footprint, a fraction *f* of which lies",
        "inside the polygon. A mask's error is the area it wrongly includes plus the area it",
        "wrongly leaves out, as a percentage of the polygon's area: the *area error*. The",
        "mask that includes exactly the pixels at least half covered (*f* ≥ 0.5) has the",
        "smallest possible error of any binary mask, so it is the best possible binary mask,",
        "not a competitor. Errors are averaged over a structure's planes, then over the",
        "configurations within a shape family, then equally over families. The tiny 0.36 mm",
        "square, smaller than any pixel here, can have errors above 100 % and dominates any",
        "average that includes it, so results are also given without it.",
        "",
    ]
    if clinical:
        (nx, ny, nz), (px, py, pz) = clinical["ct"]["size"], clinical["ct"]["spacing"]
        lines += [
            "### Clinical comparison",
            "",
            "An anonymised head-and-neck case (Human Research Ethics approval RGS4979): a CT of",
            f"{nx} × {ny} × {nz} voxels of {px:.3f} × {py:.3f} × {pz:.3f} mm, with",
            f"{clinical['structure_sets']} structure sets. Every structure was rasterised by",
            f"both v1-v2's rasteriser and v3's ({len(clinical['rows'])} structures), and the",
            "two masks compared by volume and Dice. Only organ names and numbers appear in this",
            "report.",
            "",
        ]
    lines += [
        "## Results",
        "",
        f"**Contours converted**, of the {attempts:,} cases:",
        "",
        *md_table(
            ["Method", "Converted"],
            [[name(m), f"{n:,}"] for m, n in sorted(converted.items(), key=lambda mn: -mn[1])],
            "lr",
        ),
        "dcmrtstruct2nii, Plastimatch and PlatiPy do not support CLOSEDPLANAR_XOR contours, and",
        "neither does v1-v2's rasteriser. PyRaDiSe and RT-Utils produced masks on a grid the",
        "benchmark could match only where the pixels are square.",
        "",
        f"**Accuracy**, on the {n_cases} cases all six converters and AutoSeg v3 converted:",
        "",
        *md_table(["Method", "Area error", "Without the tiny square"], accuracy_rows, "lrr"),
        f"Case by case against {name(best)}, the most accurate converter, over the"
        f" {a['paired']['n']:,} cases both converted, AutoSeg v3 was more accurate on"
        f" {a['paired']['lower']:,}, tied on {a['paired']['tied']:,} and less accurate on"
        f" {a['paired']['higher']:,}.",
        "",
        "**Robustness.** Rotating the whole CT and contours to an oblique frame, or moving them",
        "10⁵ mm from the origin, "
        + (
            "changed none of AutoSeg v3's voxels"
            if not any(a["moved"].values())
            else f"changed {a['moved']['oblique']:,} and {a['moved']['large']:,} of AutoSeg v3's"
            " voxels respectively"
        )
        + ", apart from pixels whose centre lies exactly on a contour edge.",
        "",
    ]
    if clinical:
        bands = clinical_bands(clinical)
        rows = clinical["rows"]
        smaller = sum(1 for r in rows if r["delta_cc"] < 0)
        dice = [r["dice"] for r in rows]
        lines += [
            f"**Clinical comparison.** v3's mask was smaller than v1-v2's for {smaller} of"
            f" {len(rows)} structures, and the two agreed with a median Dice of"
            f" {np.median(dice):.3f} (lowest {min(dice):.3f}). By structure size:",
            "",
            *md_table(
                [
                    "Volume (v1-v2 mask)",
                    "Structures",
                    "Median volume change, v1-v2 to v3",
                    "Median Dice, v1-v2 against v3",
                ],
                [
                    [b["label"], f"{b['n']}", f"{b['change']:+.1f} %", f"{b['dice']:.3f}"]
                    for b in bands
                ],
                "lrrr",
            ),
        ]

    lines += ["## Findings", ""]
    shipped, plast = s.loc[SHIPPED, "normalized_error"], s.loc[best, "normalized_error"]
    optimum, optimum_nt = s.loc[BOUND, "normalized_error"], s_nt.loc[BOUND, "normalized_error"]
    lines += [
        "- **AutoSeg v3's masks are as close to the contours as the best converter's.** Its"
        f" area error was {_pct(shipped)}"
        + (", the lowest of the seven methods," if a["rank"] == 1 else "")
        + f" against {_pct(plast)} for {name(best)}, the most accurate converter, and"
        f" {_pct(optimum)} for the best possible binary mask"
        f" ({_pct(s_nt.loc[SHIPPED, 'normalized_error'])},"
        f" {_pct(s_nt.loc[best, 'normalized_error'])} and {_pct(optimum_nt)} without the tiny"
        " square).",
    ]
    if converted[SHIPPED] == attempts:
        lines += [
            f"- **It converts every kind of contour.** It converted all {attempts:,} cases,"
            " including the overlapping and nested contours three of the six converters cannot"
            " read"
            + (
                ", and no voxel changed under a rotated frame or distant coordinates."
                if not any(a["moved"].values())
                else "."
            ),
        ]
    if "dcmrtstruct2nii" in s.index:
        dcm = s.loc["dcmrtstruct2nii", "normalized_error"]
        plastimatch = s.loc["Plastimatch", "normalized_error"]
        level = abs(dcm - plastimatch) < 0.005  # within half a percentage point
        lines += [
            "- **Why dcmrtstruct2nii rather than Plastimatch.** "
            + ("The two were equally accurate" if level else "Their area errors were")
            + f" ({_pct(dcm)} and {_pct(plastimatch)}), but dcmrtstruct2nii is a Python library,",
            "  so it could be built into AutoSeg directly.",
        ]
        if shipped < dcm:
            lines[-1] += (
                " Assigning a voxel centre on an edge to one side only brought AutoSeg's version"
                f" from {_pct(dcm)} to {_pct(shipped)}."
            )
    legacy = s.loc[METHOD["legacy"], "normalized_error"]
    legacy_nt = s_nt.loc[METHOD["legacy"], "normalized_error"]
    lines += [
        f"- **v1-v2's rasteriser was less accurate:** {_pct(legacy)} area error,"
        f" {legacy / shipped:.1f} times v3's ({_pct(legacy_nt)} without the tiny square,"
        f" {legacy_nt / s_nt.loc[SHIPPED, 'normalized_error']:.1f} times)"
        + (", the same as PlatiPy's, which it followed." if a["legacy_is_platipy"] else ".")
        + " Snapping vertices to the voxel grid over-fills a structure: its masks were"
        f" {_signed(a['bias'].get(METHOD['legacy'], math.nan))} mm² per plane too large on"
        f" average, against {_signed(a['bias'].get(SHIPPED, math.nan))} mm² for v3.",
    ]
    if clinical:
        bands = clinical_bands(clinical)
        lines += [
            "- **On real structures the change matters most for small organs.** The median"
            f" volume change from v1-v2's mask to v3's was {bands[0]['change']:+.1f} % for"
            f" structures {bands[0]['label'].lower()} and {bands[-1]['change']:+.1f} % for those"
            f" {bands[-1]['label'].lower()}.",
        ]
        if abs(bands[0]["change"]) > 10:
            lines[-1] += (
                " Mask-based metrics of small organs computed by v1-v2 are therefore not directly"
                " comparable with v3's."
            )
    lines += [
        "- **Limits.** No binary mask reproduces a contour exactly: even the best possible mask",
        f"  missed or added {_pct(optimum_nt)} of a structure's area on these shapes, without the"
        " tiny square. That",
        "  error shrinks as structures grow relative to the pixel, and the benchmark's shapes",
        "  are small, so the percentages here are larger than for most organs; the ranking of",
        "  the methods is what carries over. AutoSeg's 2D metrics (Supplementary 3) work on the",
        "  contours themselves and avoid this error altogether.",
        "",
        "## References",
        "",
        "1. dcmrtstruct2nii: convert DICOM RT-Struct contours to NIfTI masks. GitHub.",
        "   https://github.com/Sikerdebaard/dcmrtstruct2nii",
        "",
        "## Reproduce",
        "",
        "```",
        f"python scripts/{SCRIPT} --study <benchmark>/project --clinical <folder of CT and"
        " RTSTRUCT files>",
        "```",
        "",
        "The benchmark is about 5 GB and is not stored with AutoSeg; `--clinical` is optional.",
        f"This run took {context['seconds'] / 60:.0f} minutes"
        + (", reusing the masks of an earlier run" if context.get("reused") else "")
        + "; `--work <folder> --reuse` keeps finished configurations between runs, and",
        "`--render-only` rewrites this report from the last run's results. Regenerate this",
        "report rather than editing it.",
        "",
    ]
    target.write_text(unwrap(lines), encoding="utf-8")


def write_full_results(target: Path, context: dict, a: dict) -> None:
    """Every table behind Supplementary 2."""
    manifest = context["manifest"]
    capability: pd.DataFrame = context["capability"]
    attempts = int(capability.loc[SHIPPED].sum())
    lines = [
        f"# {TITLE}: full results",
        "",
        f"{stamp(SCRIPT, context['run'])} · {len(manifest['configurations'])} configurations,"
        f" {len(manifest['families'])} shape families. The summary is"
        f" [Supplementary 2]({REPORT.name}). Regenerate this file rather than editing it.",
        "",
        "## The benchmark",
        "",
        "The question it asks: which binary mask best preserves the continuous planar",
        "region encoded by the polygon actually stored in, and read back from, an",
        "RTSTRUCT? Each pixel stands for its whole footprint; its reference fraction",
        "*f* is the share of that footprint inside the polygon. A mask's error on a",
        "plane is the area it wrongly includes plus the area it wrongly leaves out,",
        "",
        "E = pixel area × Σ [ b(1 − f) + (1 − b) f ],",
        "",
        "and the mask that includes exactly the pixels with f ≥ 0.5 minimises it. That",
        "majority-area mask is the attainable optimum, not a competitor. The *area",
        "error* below is E divided by the polygon's area, averaged over a structure's",
        "planes, then over configurations within a shape family, then equally over",
        "families: the benchmark's primary aggregation. *Beyond the optimum* is the same",
        "for E minus the optimum's E. Tiny regions (0.36 mm squares) can exceed 100 %,",
        "so every table is also given without them.",
        "",
        "The 14 families: sphere and ellipsoid cross-sections, square and rectangle,",
        "triangle, concave L, annulus, nested shells, disconnected components, a",
        "narrow bridge, a tiny region, a keyhole, an irregular star and overlapping",
        "XOR contours. The configurations sweep sub-pixel phase, rotation,",
        "anisotropic spacing, size, vertex density, feature width, near-boundary",
        "offsets, oblique frames and large coordinates. Converter versions: "
        + ", ".join(f"{k} {v}" for k, v in sorted(context["versions"].items()))
        + ".",
        "",
        "AutoSeg's masks were made by `mask_with_reading`, as a computation run makes",
        "them, from the benchmark's own CT and RTSTRUCT files; mapped onto its",
        "reference grid by its own `canonicalize`; and scored by its own `metrics`",
        "against its own stored references. The aggregation is the benchmark's, and",
        "reproduces its published tables from its raw rows:",
        "",
        *_table(
            ["Published table", "Methods", "Largest difference in area error"],
            [
                [c["table"], str(c["methods"]), f"{c['largest_difference']:.1e}"]
                for c in context["checks"]
            ],
        ),
    ]

    # Capability
    lines += [
        "## What each method could convert",
        "",
        f"Of {attempts} ROI/configuration attempts per method:",
        "",
    ]
    statuses = list(capability.columns)
    rows = []
    for method in BASELINES + [SHIPPED, METHOD["legacy"]]:
        if method not in capability.index:
            continue
        rows.append(
            [f"**{method}**" if method in METHOD.values() else method]
            + [str(int(capability.loc[method, st])) for st in statuses]
        )
    lines += _table(["Method", *[st.replace("_", " ") for st in statuses]], rows)
    if context["shipped_failures"]:
        lines += ["AutoSeg's failures:", ""]
        lines += [f"- {f}: {d} ({n} configurations)" for f, d, n in context["shipped_failures"]]
        lines += [""]
    lines += [
        "The benchmark marked dcmrtstruct2nii, PlatiPy and Plastimatch *unsupported* for",
        "the three families stored as CLOSEDPLANAR_XOR (annulus, nested, overlap);",
        "PyRaDiSe and RT-Utils produced grids the benchmark could only match on its",
        "square-grid companions. AutoSeg reads XOR contours through the same shared",
        "reading as its 2D metrics.",
        "",
    ]

    s = a["seven"]
    lines += [
        "## All six converters and AutoSeg, on the cases they share",
        "",
        f"{int(s.loc[SHIPPED, 'roi_cases'])} ROI/configurations every one of them converted (the",
        "six-way set, square-grid companions only), with the optimum on the same cases.",
        "",
        *_table(SUMMARY_HEADERS, _summary_rows(s, a["methods7"], _label)),
        "Without the tiny-region family:",
        "",
        *_table(SUMMARY_HEADERS, _summary_rows(a["seven_no_tiny"], a["methods7"], _label)),
        "## The four converters with valid non-square grids, and AutoSeg",
        "",
        "The second common set: every configuration but the square-grid companions, on the",
        "ROI/configurations dcmrtstruct2nii, DicomRTTool, Plastimatch, PlatiPy and AutoSeg",
        "all converted.",
        "",
        *_table(
            SUMMARY_HEADERS,
            _summary_rows(a["five"], FOUR + [SHIPPED, METHOD["legacy"], BOUND], _label),
        ),
        "Without the tiny-region family:",
        "",
        *_table(
            SUMMARY_HEADERS,
            _summary_rows(a["five_no_tiny"], FOUR + [SHIPPED, METHOD["legacy"], BOUND], _label),
        ),
        "## Every configuration: AutoSeg beside the direct rasterisers",
        "",
        "The benchmark also ran planar rasterisers directly on the read-back polygons, on",
        f"all {len(manifest['configurations'])} configurations and {len(manifest['families'])}"
        " families. AutoSeg is compared here on every ROI/configuration it converted.",
        "",
        *_table(
            SUMMARY_HEADERS,
            _summary_rows(a["domain"], DIRECT + [SHIPPED, METHOD["legacy"]], _label),
        ),
        "Without the tiny-region family:",
        "",
        *_table(
            SUMMARY_HEADERS,
            _summary_rows(a["domain_no_tiny"], DIRECT + [SHIPPED, METHOD["legacy"]], _label),
        ),
        "`skimage_float` is scikit-image's polygon fill on continuous vertices, the",
        "rule dcmrtstruct2nii and AutoSeg's continuous backend descend from; `opencv_*`",
        "rounds vertices to integers first, as DicomRTTool, PyRaDiSe and RT-Utils do.",
        "",
    ]

    fam = a["domain_family"]["normalized_error"].unstack("method")
    columns = [BOUND, "skimage_float", SHIPPED, METHOD["legacy"]]
    lines += [
        "## By shape family",
        "",
        "Area error, averaged over the configurations AutoSeg converted:",
        "",
        *_table(
            ["Family", "Optimum", "skimage_float", "AutoSeg (continuous)", "AutoSeg (legacy)"],
            [
                [family, *[_pct(fam.loc[family, m]) if m in fam.columns else "–" for m in columns]]
                for family in manifest["families"]
                if family in fam.index
            ],
        ),
        "The tiny region is a 0.36 mm square, smaller than any pixel here. The",
        "optimum drops it (100 % lost); a rule that includes a pixel whose centre",
        "lies inside keeps a whole pixel whenever the square covers a centre, which",
        "is several times its area. Every centre-inclusion rasteriser, AutoSeg's",
        "among them, shares this, and it dominates any average that includes it.",
        "",
        "## What a binary mask loses",
        "",
        "Every configuration AutoSeg converted, tiny regions excluded. *Bias* is the",
        "mask's area minus the polygon's, per plane: positive over-fills. Boundary",
        "distances are between the mask's outline and the polygon's, sampled every",
        "0.1 mm along both; *within 0.5 mm* is the share of boundary that close.",
        "",
    ]
    no_tiny = a["no_tiny_rows"]
    rows = []
    for method in [BOUND, "skimage_float", SHIPPED, METHOD["legacy"]]:
        g = no_tiny[no_tiny.method == method]
        if not len(g):
            continue
        rows.append(
            [
                _label(method),
                _num(g.error_mm2.mean()),
                _num(g.signed_bias_mm2.mean()),
                _num(g.boundary_mean_mm.mean(), 3),
                _num(g.hd95_mm.mean(), 3),
                _pct(g.agreement_05mm.mean()),
                _pct(g.topology_preserved.mean()),
            ]
        )
    lines += _table(
        [
            "Method",
            "Area error per plane (mm²)",
            "Bias per plane (mm²)",
            "Mean boundary distance (mm)",
            "Boundary HD95 (mm)",
            "Boundary within 0.5 mm",
            "Topology kept",
        ],
        rows,
    )
    lines += [
        "Pixel sizes range from 0.5 to 1.3 mm across the configurations, so these",
        "distances are fractions of a pixel. They are the resolution floor under",
        "every mask-based metric AutoSeg reports: Dice, the 3D surface distances and",
        "the mask DVH all start from a mask like these. The 2D stream measures the",
        "polygons themselves and is not subject to it.",
        "",
    ]

    changes: pd.DataFrame = context["frame_changes"]
    rows = []
    for method in [SHIPPED, METHOD["legacy"]]:
        g = changes[changes.method == method]
        rows.append(
            [
                method,
                str(int(g[g.block == "oblique"].changed_unambiguous.sum())),
                str(int(g[g.block == "large"].changed_unambiguous.sum())),
                _num(float(context["absent_area"].get(method, 0.0)), 1),
            ]
        )
    lines += [
        "## Robustness",
        "",
        "The oblique configurations rotate the whole CT and contour frame together;",
        "the large ones move it 10⁵ mm away. Neither changes the geometry, so a mask",
        "should not change either, except where a pixel centre sits on the boundary",
        "(the benchmark's ambiguity band). *Absent planes* are CT planes a structure does",
        "not reach, where any area is spurious.",
        "",
        *_table(
            [
                "Method",
                "Voxels changed by an oblique frame",
                "Voxels changed by large coordinates",
                "Area on absent planes (mm²)",
            ],
            rows,
        ),
        "For comparison the benchmark found no unambiguous changes for Plastimatch and",
        "dcmrtstruct2nii under oblique frames, 4 for Plastimatch under large",
        "coordinates, and 11,072 mm² of spurious area from DicomRTTool.",
        "",
    ]

    rois = context["rois"]
    rows = []
    for other in BASELINES + [BOUND, "skimage_float", METHOD["legacy"]]:
        wide = common(rois, [SHIPPED, other]).pivot_table(
            index=["case_id", "family"], columns="method", values="error_normalized"
        )
        if not len(wide):
            continue
        diff = wide[SHIPPED] - wide[other]
        rows.append(
            [
                _label(other),
                str(len(wide)),
                str(int((diff < -TIE).sum())),
                str(int((abs(diff) <= TIE).sum())),
                str(int((diff > TIE).sum())),
                _pct(float(diff.groupby("family").mean().mean())),
            ]
        )
    lines += [
        "## Case by case",
        "",
        "AutoSeg (continuous) against each method on the ROI/configurations both",
        f"converted. A tie is within {TIE:g} in area error, the benchmark's reporting",
        "tolerance.",
        "",
        *_table(
            [
                "Against",
                "Shared cases",
                "AutoSeg better",
                "Tie",
                "AutoSeg worse",
                "Mean difference",
            ],
            rows,
        ),
    ]

    clinical = context.get("clinical")
    if clinical:
        (nx, ny, nz), (px, py, pz) = clinical["ct"]["size"], clinical["ct"]["spacing"]
        lines += [
            "## Clinical comparison: v1-v2's rasteriser against v3's",
            "",
            f"An anonymised head-and-neck case: CT {nx} × {ny} × {nz}, voxels {px:.3f} ×"
            f" {py:.3f} × {pz:.3f} mm; {clinical['structure_sets']} structure sets, labelled"
            f" A, B, ...; {len(clinical['rows'])} structures compared"
            + (
                f", {clinical['not_compared']} not (empty in one backend or both)."
                if clinical["not_compared"]
                else "."
            ),
            "Only organ names and numbers appear. *Δ* is v3's volume minus v1-v2's.",
            "",
            *md_table(
                ["Volume (v1-v2 mask)", "Structures", "Median volume change", "Median Dice"],
                [
                    [b["label"], f"{b['n']}", f"{b['change']:+.1f} %", f"{b['dice']:.3f}"]
                    for b in clinical_bands(clinical)
                ],
                "lrrr",
            ),
            *md_table(
                ["Structure set", "Structure", "v1-v2 (cc)", "v3 (cc)", "Δ (cc)", "Δ (%)", "Dice"],
                [
                    [
                        r["rtss"],
                        r["roi"],
                        f"{r['legacy_cc']:.4g}",
                        f"{r['continuous_cc']:.4g}",
                        f"{r['delta_cc']:+.4g}",
                        f"{100 * r['delta_cc'] / r['legacy_cc']:+.1f}" if r["legacy_cc"] else "–",
                        f"{r['dice']:.4f}",
                    ]
                    for r in clinical["rows"]
                ],
                "llrrrrr",
            ),
        ]

    t = pd.DataFrame(context["timings"])
    lines += [
        "## What this does not cover",
        "",
        "The fixtures are synthetic, planar and on regular grids, with deliberately",
        "empty planes between extrusions; no surface is reconstructed between planes,",
        "which is how both the benchmark and AutoSeg treat a contour stack. Non-uniform",
        "slice spacing and treatment-planning-system comparisons are outside the",
        "benchmark. AutoSeg's timings are not comparable with the converter timings,",
        "which include each converter's file output; for the record, the median AutoSeg",
        "rasterisation of one ROI took "
        + ", ".join(
            f"{1000 * t[t.backend == b].seconds.median():.1f} ms ({b})"
            for b in BACKENDS
            if len(t[t.backend == b])
        )
        + ".",
        "",
        "## Sources",
        "",
        "The benchmark's raw per-plane rows, fixtures and references were read unmodified.",
        "Its scoring code, by SHA-256:",
        "",
        *[f"- `study/{name}` `{digest[:16]}…`" for name, digest in context["code_hashes"].items()],
        "",
    ]
    target.write_text(unwrap(lines), encoding="utf-8")


def render(context: dict) -> None:
    analysis = analyse(context)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    write_report(REPORT, context, analysis)
    write_full_results(FULL_RESULTS, context, analysis)
    print(f"wrote {REPORT} and {FULL_RESULTS.name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--study", type=Path, help="The benchmark's project folder.")
    parser.add_argument(
        "--clinical", type=Path, help="A folder of CT + RTSTRUCTs to compare both backends on."
    )
    parser.add_argument("--work", type=Path, help="Scratch folder (default: a new temporary one).")
    parser.add_argument(
        "--limit", type=int, help="Only the first N configurations: a trial run, not a report."
    )
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="Reuse configurations already finished in --work instead of redoing them.",
    )
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="Rewrite the reports from the last run's results, without computing.",
    )
    args = parser.parse_args(argv)
    if args.render_only:
        render(load_results(RESULTS_NAME))
        return 0
    if args.study is None:
        parser.error("--study is required unless --render-only")

    info = run_info()
    study = long_path(args.study)
    work = (args.work or Path(tempfile.mkdtemp(prefix="rasterisation-"))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    print(f"work folder: {work}")
    started = time.perf_counter()
    code_hashes = stage_study_code(study, work)
    manifest = json.loads((study / "artifacts" / "core" / "manifest.json").read_text())
    if args.limit:
        manifest["configurations"] = manifest["configurations"][: args.limit]

    print("Rasterising and scoring with AutoSeg ...", flush=True)
    autoseg_rows, extra = run_autoseg(study, work, manifest, reuse=args.reuse)
    if args.limit:
        print(autoseg_rows.groupby(["method", "status"]).size())
        print(
            roi_table(autoseg_rows)
            .groupby("method")[["error_normalized", "excess_normalized"]]
            .mean()
        )
        return 0
    print("Reading the benchmark's raw rows ...", flush=True)
    study_rows = load_study_rows(study)
    columns = [*study_rows.columns, "detail"]
    rows = pd.concat([study_rows, autoseg_rows.reindex(columns=columns)], ignore_index=True)
    checks = check_against_published(roi_table(study_rows), study)
    for check in checks:
        print(
            f"  reproduces {check['table']}: largest difference {check['largest_difference']:.1e}"
        )
    capability = (
        rows.drop_duplicates(["case_id", "family", "method"])
        .groupby(["method", "status"])
        .size()
        .unstack(fill_value=0)
    )
    capability.columns = [str(c) for c in capability.columns]
    shipped = rows[(rows.method == SHIPPED) & (rows.status != "success")].drop_duplicates(
        ["case_id", "family"]
    )
    absent = rows[(rows.status == "success") & ~rows.nonempty_reference.astype(bool)]
    clinical = None
    if args.clinical:
        print("Comparing both backends on the clinical structure sets ...", flush=True)
        clinical = clinical_comparison(args.clinical)
    context = {
        "run": info,
        "rois": roi_table(rows),
        "capability": capability,
        "shipped_failures": [
            [family, detail, len(g)]
            for (family, detail), g in shipped.groupby(["family", "detail"])
        ],
        "absent_area": {
            m: float(absent[absent.method == m].error_mm2.sum()) for m in METHOD.values()
        },
        "manifest": manifest,
        "checks": checks,
        "frame_changes": frame_changes(manifest, study, work),
        "timings": extra["timings"],
        "code_hashes": code_hashes,
        "versions": converter_versions(study),
        "clinical": clinical,
        "seconds": time.perf_counter() - started,
        "reused": bool(args.reuse),
    }
    print(f"kept the results in {save_results(RESULTS_NAME, context)}")
    render(context)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
