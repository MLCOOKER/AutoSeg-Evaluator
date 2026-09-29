"""AutoSeg's rasteriser in the synthetic RTSTRUCT rasterisation study.

An independent synthetic study (the "RTSTRUCT rasterization review package")
asked which binary mask best preserves the polygon an RTSTRUCT actually
stores. It wrote 108 CT/RTSTRUCT configurations of 14 shape families; converted
them with six complete converters (dcmrtstruct2nii, Plastimatch, PlatiPy,
DicomRTTool, PyRaDiSe, RT-Utils) and with direct rasterisers; and scored every
plane against the exact fraction of each pixel the polygon covers. Its primary
error is the area a mask gains plus the area it loses, against which the
majority-coverage mask (a pixel is in when at least half of it is) is the
attainable optimum.

This script puts AutoSeg Evaluator's own rasteriser into that study on equal
terms. It rasterises the study's own fixtures with ``mask_with_reading`` --
both backends: ``continuous``, shipped since v3, and ``legacy``, v1 and v2 --
maps each mask onto the study's reference grid with the study's own
``canonicalize``, scores every plane with the study's own ``metrics``, against
the study's own precomputed references, and aggregates with the study's own
rules. It then checks the aggregation by reproducing the study's published
tables from its raw rows. The study's evidence is read, never written.

Usage::

    python scripts/validate_rasterisation_fidelity.py \\
        --study "<RTSTRUCT_Rasterization_Expert_Review>/project" \\
        --out docs/RASTERISATION_FIDELITY.md [--work <scratch folder>]

The study folder is about 5 GB and does not belong in this repository.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from validation_common import git_revision, long_path  # noqa: E402

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


# ---- Report -------------------------------------------------------------------------------


def _pct(value: float) -> str:
    return (
        "–"
        if value is None or (isinstance(value, float) and math.isnan(value))
        else f"{100 * value:.2f} %"
    )


def _num(value: float, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    text = f"{value:.{digits}f}"
    return text[1:] if text.startswith("-") and float(text) == 0 else text  # no "-0.00"


def _table(headers: list[str], rows: list[list[str]], numeric: bool = True) -> list[str]:
    rule = ["---"] + (["---:"] if numeric else ["---"]) * (len(headers) - 1)
    return [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(rule) + "|",
        *["| " + " | ".join(r) + " |" for r in rows],
    ]


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


def write_report(target: Path, context: dict) -> None:
    rois: pd.DataFrame = context["rois"]
    everything: pd.DataFrame = context["rows"]
    manifest = context["manifest"]
    shipped_rows = everything[everything.method == SHIPPED]
    capability = (
        everything.drop_duplicates(["case_id", "family", "method"])
        .groupby(["method", "status"])
        .size()
        .unstack(fill_value=0)
    )

    methods7 = BASELINES + [SHIPPED, METHOD["legacy"], BOUND]
    seven = common(rois, BASELINES + [SHIPPED])
    seven = rois[
        rois.method.isin(methods7)
        & rois.set_index(["case_id", "family"]).index.isin(
            seven.set_index(["case_id", "family"]).index.unique()
        )
    ]
    seven_summary, _ = weighted_summary(seven)
    seven_no_tiny, _ = weighted_summary(seven[seven.family != "tiny"])

    non_square = rois[rois.block != "square_companion"]
    five_ids = common(non_square, FOUR + [SHIPPED]).set_index(["case_id", "family"]).index.unique()
    five = non_square[
        non_square.method.isin(FOUR + [SHIPPED, METHOD["legacy"], BOUND])
        & non_square.set_index(["case_id", "family"]).index.isin(five_ids)
    ]
    five_summary, _ = weighted_summary(five)
    five_no_tiny, _ = weighted_summary(five[five.family != "tiny"])

    domain_ids = rois[rois.method == SHIPPED].set_index(["case_id", "family"]).index.unique()
    domain = rois[
        rois.method.isin(DIRECT + [SHIPPED, METHOD["legacy"]])
        & rois.set_index(["case_id", "family"]).index.isin(domain_ids)
    ]
    domain_summary, domain_family = weighted_summary(domain)
    domain_no_tiny, _ = weighted_summary(domain[domain.family != "tiny"])

    ok_shipped = int(capability.loc[SHIPPED].get("success", 0))
    attempts = int(capability.loc[SHIPPED].sum())
    s = seven_summary
    best_converter = s.loc[[m for m in BASELINES if m in s.index]].normalized_error.idxmin()
    order = s.loc[[m for m in BASELINES + [SHIPPED] if m in s.index]].normalized_error.sort_values()
    rank = list(order.index).index(SHIPPED) + 1
    rank_text = "the lowest" if rank == 1 else f"number {rank} from the lowest"
    paired = common(rois, [SHIPPED, best_converter]).pivot_table(
        index=["case_id", "family"], columns="method", values="error_normalized"
    )
    gap = paired[SHIPPED] - paired[best_converter]
    paired_best = (
        f"AutoSeg lower on {int((gap < -TIE).sum())}, tied on {int((abs(gap) <= TIE).sum())}, "
        f"higher on {int((gap > TIE).sum())} of {len(gap)}"
    )
    legacy_is_platipy = (
        abs(s.loc[METHOD["legacy"], "normalized_error"] - s.loc["PlatiPy", "normalized_error"])
        < 1e-12
    )
    shipped_bias = float(
        domain[(domain.method == SHIPPED) & (domain.family != "tiny")].signed_bias_mm2.mean()
    )
    moved = context["frame_changes"]
    shipped_moved = int(moved[moved.method == SHIPPED].changed_unambiguous.sum())

    lines = [
        "# The rasteriser against the polygon it rasterises",
        "",
        "Generated by `scripts/validate_rasterisation_fidelity.py`. Regenerate it",
        "rather than editing it.",
        "",
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · AutoSeg Evaluator, "
        f"{context['version']} · {len(manifest['configurations'])} configurations, "
        f"{len(manifest['families'])} shape families",
        "",
        "## In brief",
        "",
        f"- **AutoSeg's shipped rasteriser converted {ok_shipped} of {attempts} ROI/configurations,** "
        "every shape family included.",
        f"- **On the {int(s.loc[SHIPPED, 'roi_cases'])} cases all six converters and AutoSeg share, AutoSeg's area "
        f"error is {_pct(s.loc[SHIPPED, 'normalized_error'])}**, {rank_text} of the seven; "
        f"{best_converter}, the study's best converter, has {_pct(s.loc[best_converter, 'normalized_error'])}, "
        f"and the attainable optimum {_pct(s.loc[BOUND, 'normalized_error'])}. Case by case against "
        f"{best_converter}, over every case both converted: {paired_best}. Without the tiny-region family: "
        f"{_pct(seven_no_tiny.loc[SHIPPED, 'normalized_error'])} against "
        f"{_pct(seven_no_tiny.loc[best_converter, 'normalized_error'])} and "
        f"{_pct(seven_no_tiny.loc[BOUND, 'normalized_error'])}.",
        f"- **v1 and v2's legacy rasteriser, on the same cases: "
        f"{_pct(s.loc[METHOD['legacy'], 'normalized_error'])}** "
        f"({_pct(seven_no_tiny.loc[METHOD['legacy'], 'normalized_error'])} without tiny regions)"
        + (
            ", identical to PlatiPy's, the code it was ported from. That agreement is also a check "
            "on this harness."
            if legacy_is_platipy
            else "."
        ),
        f"- **The shipped rasteriser is unbiased and frame-invariant:** its mean area bias per plane is "
        f"{_num(shipped_bias)} mm², and neither an oblique frame nor coordinates 10⁵ mm away "
        f"changed {'a single voxel' if shipped_moved == 0 else f'more than {shipped_moved} voxels'} "
        "outside the ambiguity band.",
        "- **No binary mask is the polygon.** Even the optimum mask misses or adds",
        f"  {_pct(domain_no_tiny.loc[BOUND, 'normalized_error'])} of a structure's area on average over every",
        "  configuration (tiny regions excluded); that is the price of a voxel grid, and",
        "  the floor under every mask-based metric.",
        "",
        "## The study",
        "",
        "The question it asked: which binary mask best preserves the continuous planar",
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
        "families: the study's primary aggregation. *Beyond the optimum* is the same",
        "for E minus the optimum's E. Tiny regions (0.36 mm squares) can exceed 100 %,",
        "so every table is also given without them.",
        "",
        "The 14 families: sphere and ellipsoid cross-sections, square and rectangle,",
        "triangle, concave L, annulus, nested shells, disconnected components, a",
        "narrow bridge, a tiny region, a keyhole, an irregular star and overlapping",
        "XOR contours. The configurations sweep sub-pixel phase, rotation,",
        "anisotropic spacing, size, vertex density, feature width, near-boundary",
        "offsets, oblique frames and large coordinates.",
        "",
        "AutoSeg's masks were made by `mask_with_reading`, as a computation run makes",
        "them, from the study's own CT and RTSTRUCT files; mapped onto the study's",
        "reference grid by its own `canonicalize`; and scored by its own `metrics`",
        "against its own stored references. The aggregation is the study's, and",
        "reproduces its published tables from its raw rows:",
        "",
        *_table(
            ["Published table", "Methods", "Largest difference in area error"],
            [
                [c["table"], str(c["methods"]), f"{c['largest_difference']:.1e}"]
                for c in context["checks"]
            ],
        ),
        "",
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
    failures = shipped_rows[shipped_rows.status != "success"].drop_duplicates(["case_id", "family"])
    lines += [""]
    if len(failures):
        lines += ["AutoSeg's failures:", ""]
        for (family, detail), g in failures.groupby(["family", "detail"]):
            lines.append(f"- {family}: {detail} ({len(g)} configurations)")
        lines.append("")
    lines += [
        "The study marked dcmrtstruct2nii, PlatiPy and Plastimatch *unsupported* for",
        "the three families stored as CLOSEDPLANAR_XOR (annulus, nested, overlap);",
        "PyRaDiSe and RT-Utils produced grids the study could only match on its",
        "square-grid companions. AutoSeg reads XOR contours through the same shared",
        "reading as its 2D metrics.",
        "",
    ]

    # Seven-way
    lines += [
        "## All six converters and AutoSeg, on the cases they share",
        "",
        f"{int(s.loc[SHIPPED, 'roi_cases'])} ROI/configurations every one of them converted — the study's",
        "six-way set, square-grid companions only — with the optimum on the same cases.",
        "",
        *_table(SUMMARY_HEADERS, _summary_rows(seven_summary, methods7, _label)),
        "",
        "Without the tiny-region family:",
        "",
        *_table(SUMMARY_HEADERS, _summary_rows(seven_no_tiny, methods7, _label)),
        "",
    ]

    # Five-way non-square
    lines += [
        "## The four converters with valid non-square grids, and AutoSeg",
        "",
        "The study's second common set: every configuration but the square-grid",
        "companions, on the ROI/configurations dcmrtstruct2nii, DicomRTTool,",
        "Plastimatch, PlatiPy and AutoSeg all converted.",
        "",
        *_table(
            SUMMARY_HEADERS,
            _summary_rows(five_summary, FOUR + [SHIPPED, METHOD["legacy"], BOUND], _label),
        ),
        "",
        "Without the tiny-region family:",
        "",
        *_table(
            SUMMARY_HEADERS,
            _summary_rows(five_no_tiny, FOUR + [SHIPPED, METHOD["legacy"], BOUND], _label),
        ),
        "",
    ]

    # Direct algorithms
    lines += [
        "## Every configuration: AutoSeg beside the direct rasterisers",
        "",
        "The study also ran planar rasterisers directly on the read-back polygons, on",
        f"all {len(manifest['configurations'])} configurations and {len(manifest['families'])} families. AutoSeg is",
        "compared here on every ROI/configuration it converted.",
        "",
        *_table(
            SUMMARY_HEADERS,
            _summary_rows(domain_summary, DIRECT + [SHIPPED, METHOD["legacy"]], _label),
        ),
        "",
        "Without the tiny-region family:",
        "",
        *_table(
            SUMMARY_HEADERS,
            _summary_rows(domain_no_tiny, DIRECT + [SHIPPED, METHOD["legacy"]], _label),
        ),
        "",
        "`skimage_float` is scikit-image's polygon fill on continuous vertices, the",
        "rule dcmrtstruct2nii and AutoSeg's continuous backend descend from; `opencv_*`",
        "rounds vertices to integers first, as DicomRTTool, PyRaDiSe and RT-Utils do.",
        "",
    ]

    # By family
    fam = domain_family["normalized_error"].unstack("method")
    columns = [BOUND, "skimage_float", SHIPPED, METHOD["legacy"]]
    rows = [
        [family, *[_pct(fam.loc[family, m]) if m in fam.columns else "–" for m in columns]]
        for family in manifest["families"]
        if family in fam.index
    ]
    lines += [
        "## By shape family",
        "",
        "Area error, averaged over the configurations AutoSeg converted:",
        "",
        *_table(
            ["Family", "Optimum", "skimage_float", "AutoSeg (continuous)", "AutoSeg (legacy)"], rows
        ),
        "",
        "The tiny region is a 0.36 mm square, smaller than any pixel here. The",
        "optimum drops it (100 % lost); a rule that includes a pixel whose centre",
        "lies inside keeps a whole pixel whenever the square covers a centre, which",
        "is several times its area. Every centre-inclusion rasteriser, AutoSeg's",
        "among them, shares this, and it dominates any average that includes it.",
        "",
    ]

    # What a mask loses
    lines += [
        "## What a binary mask loses",
        "",
        "Every configuration AutoSeg converted, tiny regions excluded. *Bias* is the",
        "mask's area minus the polygon's, per plane: positive over-fills. Boundary",
        "distances are between the mask's outline and the polygon's, sampled every",
        "0.1 mm along both; *within 0.5 mm* is the share of boundary that close.",
        "",
    ]
    no_tiny = domain[domain.family != "tiny"]
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
        "",
        "Pixel sizes range from 0.5 to 1.3 mm across the configurations, so these",
        "distances are fractions of a pixel. They are the resolution floor under",
        "every mask-based metric AutoSeg reports: Dice, the 3D surface distances and",
        "the mask DVH all start from a mask like these. The 2D stream measures the",
        "polygons themselves and is not subject to it.",
        "",
    ]

    # Robustness
    changes: pd.DataFrame = context["frame_changes"]
    absent = everything[
        (everything.status == "success") & ~everything.nonempty_reference.astype(bool)
    ]
    rows = []
    for method in [SHIPPED, METHOD["legacy"]]:
        g = changes[changes.method == method]
        a = absent[absent.method == method]
        rows.append(
            [
                method,
                str(int(g[g.block == "oblique"].changed_unambiguous.sum())),
                str(int(g[g.block == "large"].changed_unambiguous.sum())),
                _num(float(a.error_mm2.sum()), 1),
            ]
        )
    lines += [
        "## Robustness",
        "",
        "The oblique configurations rotate the whole CT and contour frame together;",
        "the large ones move it 10⁵ mm away. Neither changes the geometry, so a mask",
        "should not change either, except where a pixel centre sits on the boundary",
        "(the study's ambiguity band). *Absent planes* are CT planes a structure does",
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
        "",
        "For comparison the study found no unambiguous changes for Plastimatch and",
        "dcmrtstruct2nii under oblique frames, 4 for Plastimatch under large",
        "coordinates, and 11,072 mm² of spurious area from DicomRTTool.",
        "",
    ]

    # Paired
    rows = []
    for other in BASELINES + [BOUND, "skimage_float", METHOD["legacy"]]:
        paired = common(rois, [SHIPPED, other])
        wide = paired.pivot_table(
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
        f"converted. A tie is within {TIE:g} in area error, the study's reporting tolerance.",
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
        "",
    ]

    # Limits & sources
    t = pd.DataFrame(context["timings"])
    lines += [
        "## What this does not cover",
        "",
        "The fixtures are synthetic, planar and on regular grids, with deliberately",
        "empty planes between extrusions; no surface is reconstructed between planes,",
        "which is how both the study and AutoSeg treat a contour stack. Clinical",
        "structure sets, non-uniform slice spacing and treatment-planning-system",
        "comparisons are outside the study, as its own report states. AutoSeg's",
        "timings are not comparable with the study's converter timings, which include",
        "each converter's file output; for the record, the median AutoSeg rasterisation",
        "of one ROI took "
        + ", ".join(
            f"{1000 * t[t.backend == b].seconds.median():.1f} ms ({b})"
            for b in BACKENDS
            if len(t[t.backend == b])
        )
        + ".",
        "",
        "## Sources",
        "",
        "Study: *External review: synthetic RTSTRUCT rasterization comparison*,",
        "review package assembled 26 September 2026 (study software cutoff",
        "8 September 2026). Its raw per-plane rows, fixtures and references were read",
        "unmodified. The study code used for scoring, by SHA-256:",
        "",
        *[f"- `study/{name}` `{digest[:16]}…`" for name, digest in context["code_hashes"].items()],
        "",
        "Reproduce with:",
        "",
        "```",
        "python scripts/validate_rasterisation_fidelity.py --study <package>/project \\",
        "    --out docs/RASTERISATION_FIDELITY.md",
        "```",
        "",
    ]
    target.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--study", type=Path, required=True, help="The review package's project folder."
    )
    parser.add_argument("--out", type=Path, help="Write the markdown report here.")
    parser.add_argument("--work", type=Path, help="Scratch folder (default: a new temporary one).")
    parser.add_argument(
        "--limit", type=int, help="Only the first N configurations: a trial run, not a report."
    )
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="Reuse configurations already finished in --work instead of redoing them.",
    )
    args = parser.parse_args(argv)

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
    print("Reading the study's raw rows ...", flush=True)
    study_rows = load_study_rows(study)
    columns = [*study_rows.columns, "detail"]
    rows = pd.concat([study_rows, autoseg_rows.reindex(columns=columns)], ignore_index=True)
    rois = roi_table(rows)
    checks = check_against_published(roi_table(study_rows), study)
    for check in checks:
        print(
            f"  reproduces {check['table']}: largest difference {check['largest_difference']:.1e}"
        )
    context = {
        "rows": rows,
        "rois": rois,
        "manifest": manifest,
        "checks": checks,
        "frame_changes": frame_changes(manifest, study, work),
        "timings": extra["timings"],
        "code_hashes": code_hashes,
        "version": git_revision(),
        "seconds": time.perf_counter() - started,
    }
    if args.out:
        write_report(args.out, context)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
