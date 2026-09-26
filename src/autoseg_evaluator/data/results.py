"""In-memory store of computed metric rows.

**One computation per results table.** The metrics are chosen once and computed
once; computing again replaces the table, after the user confirms. Rows are never
updated or merged, so every row in a table was produced by the same run with the
same settings, and a tolerance can never be attributed to another run's values.

Likert scores are kept separately, keyed by the contour they were given to, and
overlaid onto that contour's row when the table is read. They are not part of a
computation: replacing the metric rows leaves them untouched, and scoring can
continue over several sessions against a table computed once and saved with the
session.

The :class:`ResultsManager` is owned by :class:`MainWindow` and read by the
Results tab for display and CSV export.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

from autoseg_evaluator.core.tolerance_keys import (
    POLYGON_TOLERANCE_METRICS,
    SURFACE_DICE_METRICS,
    TOLERANCE_METRICS,
    base_metric,
    split_tolerance,
    tolerance_of,
)
from autoseg_evaluator.utils.timestamps import now_local

#: Metric-column prefix for when a rater gave their score: ``scored_at_<rater>``.
SCORED_AT_PREFIX = "scored_at_"

#: Version of the results payload a session carries.
RESULTS_SESSION_VERSION = 1

# Order of the fixed metadata columns shown before the dynamic metric columns.
META_COLUMNS: list[tuple[str, str]] = [
    ("drawer", "Drawer"),
    # Canonical organ grouping. ``drawer`` is whatever the ground-truth ROI
    # happened to be called, so two patients whose manual contours were named
    # differently sit in different drawers; these columns say which organ those
    # drawers are actually of, which is what a cohort statistic groups on.
    # Laterality and qualifier are broken out as their own columns because the
    # downstream analysis pivots on them.
    ("canonical_organ", "Organ"),
    ("organ_laterality", "Side"),
    ("organ_qualifier", "Structure type"),
    ("patient_id", "Patient"),
    ("comparison_mode", "Mode"),
    ("was_designated_gt", "Designated GT"),
    ("gt_source_label", "GT source"),
    ("gt_rtstruct_filename", "GT RTSS"),
    ("gt_roi_name", "GT ROI"),
    ("test_source_label", "Test source"),
    ("test_rtstruct_filename", "Test RTSS"),
    ("test_organ", "Test ROI"),
    ("truncated", "Truncated"),
    ("truncated_slices", "Truncated slices"),
    ("truncated_extent_mm", "Truncated extent (mm)"),
    ("similarity", "Name similarity"),
    ("organ_tier", "Organ match"),
    # When this row was produced. Likert scores carry their own time, in a
    # Scored at column beside each rater's score, because scoring can happen
    # sessions after the computation.
    ("computed_at", "Computed at"),
    ("error", "Error"),
]


# Canonical metric column order. Every column listed here is ALWAYS shown in
# the results table (and CSV export) — even when no row populated it — so the
# table layout stays stable across runs and copy-pasting into Excel always
# lines up. Dynamic metrics (e.g. user-defined ``D95_gy`` / ``V20gy_cc``)
# are appended after this list, sorted by their numeric suffix.
CANONICAL_METRIC_COLUMNS: list[str] = [
    # Volumetric overlap
    "dice",
    "precision",
    "recall",
    "surface_dice",
    # Surface distances
    "hausdorff100",
    "hausdorff95",
    "mean_surface_distance",
    # 2D contour metrics, measured on the RTSTRUCT polygons rather than on a
    # rasterised mask. Kept together and after the mask block, because the two
    # streams answer the same question by different means and a reader has to be
    # able to see which is which at a glance.
    "poly_apl_mm",
    "poly_napl",
    "poly_apl_reverse_mm",
    "poly_napl_reverse",
    "poly_hd100_mm",
    "poly_hd95_mm",
    "poly_mean_distance_mm",
    "poly_median_distance_mm",
    "poly_planes_joint",
    "poly_planes_gt_only",
    "poly_planes_test_only",
    "poly_status",
    # Volume + centre-of-mass
    "volume_gt_cc",
    "volume_test_cc",
    "volume_diff_cc",
    "volume_ratio",
    "com_offset_mm",
    "com_dx_mm",
    "com_dy_mm",
    "com_dz_mm",
    # STAPLE per-rater
    "staple_sensitivity",
    "staple_specificity",
    # STAPLE consensus summary
    "consensus_volume_cc",
    "rater_disagreement_cc",
    "rater_volume_range_cc",
    "uncertain_band_cc",
    "mean_entropy",
    "n_raters",
    "staple_iterations",
    "staple_converged",
    "staple_bbox_padding",
    "staple_bbox_fg_ratio",
    # DVH (static built-ins) — kept at the very right so all dose-related
    # columns cluster together and dynamic D{X}/V{X} columns naturally
    # follow without being separated from Dmin/Dmean/Dmax by other metrics.
    # Coverage is the share of the structure inside the dose grid, which is
    # what every dose statistic in the row describes; the status says when a
    # D{X}cc is larger than that part, and is blank otherwise.
    "dose_coverage_pct",
    "dvh_status",
    "dmin_gy",
    "dmean_gy",
    "dmax_gy",
]


# Human-readable headers (with physical units) shown in the results table and
# CSV. Keys map to ``CANONICAL_METRIC_COLUMNS``; the underlying metric dict keys
# stay machine-friendly so the rest of the codebase doesn't need to change.
_METRIC_LABELS: dict[str, str] = {
    # Volumetric overlap
    "dice": "Dice",
    "precision": "Precision",
    "recall": "Recall",
    "surface_dice": "Surface Dice",
    # Surface distances
    "hausdorff100": "3D Hausdorff 100% (mm)",
    "hausdorff95": "3D Hausdorff 95% (mm)",
    "mean_surface_distance": "Mean Surface Distance (mm)",
    # 2D contour metrics. Every one says "2D" because the mask stream reports
    # metrics with the same names, and in a table there is no group heading
    # between them.
    "poly_apl_mm": "2D APL (mm)",
    "poly_napl": "2D NAPL",
    "poly_apl_reverse_mm": "2D APL reverse (mm)",
    "poly_napl_reverse": "2D NAPL reverse",
    "poly_hd100_mm": "2D Hausdorff 100% (mm)",
    "poly_hd95_mm": "2D Hausdorff 95% (mm)",
    "poly_mean_distance_mm": "2D Mean Contour Distance (mm)",
    "poly_median_distance_mm": "2D Median Contour Distance (mm)",
    "poly_planes_joint": "2D planes compared",
    "poly_planes_gt_only": "2D planes GT only",
    "poly_planes_test_only": "2D planes test only",
    "poly_status": "2D status",
    # Volume + centre-of-mass
    "volume_gt_cc": "Volume GT (cc)",
    "volume_test_cc": "Volume Test (cc)",
    "volume_diff_cc": "Volume diff (cc)",
    "volume_ratio": "Volume ratio",
    "com_offset_mm": "COM offset (mm)",
    "com_dx_mm": "COM Δx (mm)",
    "com_dy_mm": "COM Δy (mm)",
    "com_dz_mm": "COM Δz (mm)",
    # DVH (static)
    "dose_coverage_pct": "Dose grid coverage (%)",
    "dvh_status": "Dose status",
    "dmin_gy": "Dmin (Gy)",
    "dmean_gy": "Dmean (Gy)",
    "dmax_gy": "Dmax (Gy)",
    # STAPLE per-rater
    "staple_sensitivity": "STAPLE Sensitivity",
    "staple_specificity": "STAPLE Specificity",
    # STAPLE consensus summary
    "consensus_volume_cc": "Consensus Volume (cc)",
    "rater_disagreement_cc": "Rater Disagreement (cc)",
    "rater_volume_range_cc": "Rater Volume Range (cc)",
    "uncertain_band_cc": "Uncertain Band (cc)",
    "mean_entropy": "Mean Entropy",
    "n_raters": "N raters",
    "staple_iterations": "STAPLE iterations",
    "staple_converged": "STAPLE converged",
    "staple_bbox_padding": "STAPLE bbox padding (vox)",
    "staple_bbox_fg_ratio": "STAPLE bbox FG ratio",
}


def metric_display_label(
    key: str,
    *,
    sd_tau_mm: float | None = None,
    poly_tau_mm: float | None = None,
) -> str:
    """Return the user-facing column header for a metric key (with units).

    Handles the canonical static labels (above), plus dynamic DVH metrics
    matching ``d{X}_gy`` → ``D{X} (Gy)``, ``d{X}cc_gy`` → ``D{X}cc (Gy)``,
    and ``v{X}gy_cc`` → ``V{X}Gy (cc)``. Anything else falls through as-is
    so non-standard keys are still visible.

    A key carrying its tolerance — ``surface_dice@3mm`` — is labelled with it:
    ``Surface Dice @ 3.00 mm``. The tolerance comes from the key, so every
    column says what its own numbers were measured at. ``sd_tau_mm`` and
    ``poly_tau_mm`` decorate a bare ``surface_dice`` or 2D APL key the same way,
    for the Build Consensus table, which measures at one tolerance of its own.

    DVH difference columns (``{base}_diff``, e.g. ``d2cc_gy_diff``) reuse the
    base metric's label suffixed with ``Δ vs GT`` (the value is test − GT).
    """
    if key.endswith("_diff"):
        base = metric_display_label(key[:-5], sd_tau_mm=sd_tau_mm, poly_tau_mm=poly_tau_mm)
        return f"{base} Δ vs GT"
    base, tolerance = split_tolerance(key)
    if tolerance is not None and base in TOLERANCE_METRICS:
        return f"{_METRIC_LABELS[base]} @ {tolerance:.2f} mm"
    if key == "surface_dice" and sd_tau_mm is not None:
        return f"Surface Dice @ {sd_tau_mm:.2f} mm"
    # The 2D stream keeps its own tolerance, separate from Surface Dice's.
    if key in POLYGON_TOLERANCE_METRICS and poly_tau_mm is not None:
        return f"{_METRIC_LABELS[key]} @ {poly_tau_mm:.2f} mm"
    if key in _METRIC_LABELS:
        return _METRIC_LABELS[key]
    # Qualitative (Likert) columns: a score per rater, when each was given, and
    # the assessed / blinded flags.
    if key.startswith(SCORED_AT_PREFIX):
        return f"Scored at — {key[len(SCORED_AT_PREFIX) :]}"
    if key.startswith("likert_"):
        return f"Likert — {key[len('likert_') :]}"
    if key == "qualitative_assessed":
        return "Qualitative"
    if key == "qualitative_blinded":
        return "Blinded"
    # ``d{X}cc_gy`` must be tested before the bare ``d{X}_gy`` form since
    # both end with ``_gy``; the cc variant carries an explicit unit
    # suffix between the number and ``_gy``.
    if key.startswith("d") and key.endswith("cc_gy"):
        try:
            return f"D{_fmt_dvh_num(key[1:-5])}cc (Gy)"
        except ValueError:
            pass
    if key.startswith("d") and key.endswith("_gy"):
        try:
            return f"D{int(key[1:-3])} (Gy)"
        except ValueError:
            pass
    if key.startswith("v") and key.endswith("gy_cc"):
        try:
            return f"V{int(key[1:-5])}Gy (cc)"
        except ValueError:
            pass
    return key


def _fmt_dvh_num(token: str) -> str:
    """Format a DVH numeric token for header display (drop trailing ``.0``)."""
    val = float(token)
    if val.is_integer():
        return str(int(val))
    return token


def _dynamic_metric_sort_key(name: str) -> tuple:
    """Sort key for metric columns not in the canonical list.

    Groups user-defined DVH metrics by family and numeric suffix so a list
    like ``[v40gy_cc, d95_gy, d2cc_gy, d2_gy, v20gy_cc, custom_x]`` becomes
    ``[d2_gy, d95_gy, d2cc_gy, v20gy_cc, v40gy_cc, custom_x]``.
    """
    # DVH difference columns sort by the SAME family logic as their base
    # metric, but all land after the absolute columns (offset +10) so the
    # table reads "every absolute, then every Δ-vs-GT".
    base = name[:-5] if name.endswith("_diff") else name
    diff_offset = 10 if name.endswith("_diff") else 0
    # D{X}cc_gy → dose to hottest X cc — sort ascending by X (D0.1cc, D1cc,
    # D2cc are the typical order for OAR hotspot constraints). Tested first
    # because the bare ``d{X}_gy`` form also matches the ``_gy`` suffix.
    if base.startswith("d") and base.endswith("cc_gy"):
        try:
            return (1 + diff_offset, float(base[1:-5]), name)
        except ValueError:
            pass
    # D{X}_gy → dose to hottest X% — sort by X descending so D95 comes before D2
    if base.startswith("d") and base.endswith("_gy"):
        try:
            return (0 + diff_offset, -int(base[1:-3]), name)
        except ValueError:
            pass
    # V{X}gy_cc → volume receiving ≥ X Gy — sort by X ascending
    if base.startswith("v") and base.endswith("gy_cc"):
        try:
            return (2 + diff_offset, int(base[1:-5]), name)
        except ValueError:
            pass
    return (3 + diff_offset, 0, name)


def _safe_rater(rater: str) -> str:
    """Sanitise a rater name for use as a metric-column key suffix."""
    return (str(rater).strip() or "Rater").replace("|", "/")


def _first_dvh_index(columns: list[str]) -> int:
    """Index of the first dose (DVH) column in ``columns`` (``len`` if none)."""
    for i, c in enumerate(columns):
        if c in ("dmin_gy", "dmean_gy", "dmax_gy"):
            return i
    return len(columns)


class ResultsManager:
    """The rows of one computation, and the Likert scores given to its contours."""

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []
        # Qualitative (Likert) scores, stored against the contour and overlaid
        # onto its metric row at read time so each contour stays a single row.
        # Keyed by (patient_id, drawer, source_label, structure set UID,
        # roi_number): an external audit found two structure sets from one
        # source sharing an ROI number, and the second score landing on the
        # first file's row, when the structure set was not part of the key.
        self._qualitative: dict[tuple[str, str, str, str, int], dict[str, Any]] = {}
        # ``{roi_name: OrganAssignment}``. Applied at read time rather than
        # baked into rows when they are computed, so changing an organ
        # assignment in the review dialog re-labels existing results instead of
        # requiring the whole cohort to be recomputed.
        self._organ_index: dict[str, Any] = {}

    def set_organ_index(self, index: Any | None) -> None:
        """Supply canonical organ assignments, keyed by raw ROI name.

        Accepts either an :class:`~autoseg_evaluator.data.organ_index.OrganIndex`
        or a plain mapping. Passing ``None`` clears the tagging, which simply
        leaves the organ columns blank — results remain valid, they just are
        not grouped.
        """
        if index is None:
            self._organ_index = {}
            return
        mapping = getattr(index, "assignments", index)
        self._organ_index = dict(mapping or {})

    def organ_index(self) -> dict[str, Any]:
        return dict(self._organ_index)

    def _overlay_organ(self, row: dict[str, Any]) -> None:
        """Label a row with the organ its ground-truth contour belongs to."""
        if not self._organ_index:
            return
        found = self._organ_index.get(str(row.get("gt_roi_name") or ""))
        if found is None:
            return
        row["canonical_organ"] = found.key.label()
        row["organ_laterality"] = found.key.laterality
        row["organ_qualifier"] = found.key.qualifier
        row["organ_tier"] = found.tier

    def add_row(self, row: dict[str, Any]) -> None:
        self._rows.append(dict(row))

    def add_rows(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            self.add_row(r)

    def computed_row_count(self) -> int:
        """Rows produced by a computation — not counting score-only rows."""
        return len(self._rows)

    def tolerances_in_use(self) -> dict[str, list[float]]:
        """Every tolerance the table's columns were computed at, by stream.

        Read from the column keys themselves, which carry their tolerance, so
        this can never disagree with the numbers it describes.
        """
        surface: set[float] = set()
        polygon: set[float] = set()
        for row in self._rows:
            for key in row.get("metrics") or {}:
                base, tolerance = split_tolerance(key)
                if tolerance is None:
                    continue
                if base in SURFACE_DICE_METRICS:
                    surface.add(tolerance)
                elif base in POLYGON_TOLERANCE_METRICS:
                    polygon.add(tolerance)
        return {"surface_dice_mm": sorted(surface), "polygon_apl_mm": sorted(polygon)}

    # ---- Qualitative (Likert) scores -------------------------------------

    def upsert_qualitative_score(
        self,
        *,
        patient_id: str,
        drawer: str,
        source_label: str,
        roi_name: str,
        roi_number: int,
        is_gt: bool,
        rater: str,
        score: int,
        blinded: bool,
        rtstruct_sop_uid: str = "",
        scored_at: str | None = None,
    ) -> None:
        """Record one rater's Likert score for one contour.

        The score is stored against the contour (patient, drawer, source,
        structure set, roi) and overlaid onto that contour's metric row at read
        time, so the Likert score sits on the **same row** as the geometric /
        dose metrics. A synthetic row is emitted only when no metric row exists
        for the contour (e.g. the qualitative pass ran before Compute). A single
        ``qualitative_blinded`` flag records whether the run was blinded (True)
        or transparent (False); re-rating overwrites in place.

        ``scored_at`` is when the score was given — kept with the session, since
        scoring can span several — and defaults to now.

        ``rtstruct_sop_uid`` is part of the contour's identity; see the key.
        """
        key = (
            str(patient_id),
            str(drawer),
            str(source_label),
            str(rtstruct_sop_uid or ""),
            int(roi_number),
        )
        entry = self._qualitative.get(key)
        if entry is None:
            entry = {
                "patient_id": patient_id,
                "drawer": drawer,
                "source_label": source_label,
                "rtstruct_sop_uid": str(rtstruct_sop_uid or ""),
                "roi_name": roi_name,
                "roi_number": int(roi_number),
                "is_gt": bool(is_gt),
                "scores": {},
                "scored_at": {},
                "blinded": None,
            }
            self._qualitative[key] = entry
        name = _safe_rater(rater)
        entry["scores"][name] = int(score)
        # ``None`` means "now"; an empty string is a score restored from a
        # session saved before times were kept, and stays blank.
        entry["scored_at"][name] = now_local() if scored_at is None else str(scored_at)
        entry["blinded"] = bool(blinded)

    def _qualitative_metric_keys(self) -> set[str]:
        if not self._qualitative:
            return set()
        keys: set[str] = {"qualitative_assessed"}
        for entry in self._qualitative.values():
            for rater in entry["scores"]:
                keys.add(f"likert_{rater}")
                keys.add(f"{SCORED_AT_PREFIX}{rater}")
            if entry.get("blinded") is not None:
                keys.add("qualitative_blinded")
        return keys

    @staticmethod
    def _row_contour_key(row: dict[str, Any]) -> tuple[str, str, str, str, int] | None:
        """Contour identity for a *primary* test-comparison row, else None.

        Excludes GT-only rows (gt_dose, the designated-GT STAPLE rater, STAPLE
        Details) so a Likert score overlays onto the test-vs-reference row.
        """
        mode = str(row.get("comparison_mode", ""))
        if mode in ("gt_dose", "STAPLE Details") or row.get("was_designated_gt"):
            return None
        try:
            roi = int(row.get("test_roi_number", -1))
        except (TypeError, ValueError):
            return None
        return (
            str(row.get("patient_id", "")),
            str(row.get("drawer", "")),
            str(row.get("test_source_label", "")),
            str(row.get("test_rtstruct_sop_uid", "") or ""),
            roi,
        )

    @staticmethod
    def _overlay_qualitative(row: dict[str, Any], entry: dict[str, Any]) -> None:
        for rater, score in entry["scores"].items():
            row["metrics"][f"likert_{rater}"] = int(score)
            row["metrics"][f"{SCORED_AT_PREFIX}{rater}"] = str(
                (entry.get("scored_at") or {}).get(rater, "")
            )
        if entry.get("blinded") is not None:
            row["metrics"]["qualitative_blinded"] = bool(entry["blinded"])

    def _synthetic_qualitative_row(self, entry: dict[str, Any]) -> dict[str, Any]:
        row: dict[str, Any] = {
            "drawer": entry["drawer"],
            "patient_id": entry["patient_id"],
            "comparison_mode": "Qualitative",
            "was_designated_gt": bool(entry.get("is_gt")),
            "gt_source_label": "",
            "gt_rtstruct_filename": "",
            "gt_roi_name": "",
            "test_source_label": entry["source_label"],
            "test_rtstruct_filename": "",
            "test_rtstruct_sop_uid": entry.get("rtstruct_sop_uid", ""),
            "test_organ": entry["roi_name"],
            "test_roi_number": int(entry["roi_number"]),
            "truncated": False,
            "similarity": "",
            "computed_at": "",
            "error": "",
            "metrics": {"qualitative_assessed": True},
        }
        self._overlay_qualitative(row, entry)
        return row

    # ---- Clearing ----------------------------------------------------------

    def clear_computed(self) -> None:
        """Discard the computed rows and keep every Likert score.

        What replacing a computation does. The scores belong to the contours,
        not to a run: they were given in the Qualitative tab, often over several
        sessions, and the next computation's rows pick them up again.
        """
        self._rows.clear()

    def clear(self) -> None:
        """Discard the rows and the scores — for loading a different cohort."""
        self._rows.clear()
        self._qualitative.clear()
        # The organ index belongs to the loaded cohort, not to a batch of
        # results, so it deliberately survives a clear.

    # ---- Reading -------------------------------------------------------------

    def rows(self) -> list[dict[str, Any]]:
        """Snapshot of all rows, with qualitative scores overlaid.

        Each metric row gets the Likert score(s) for its contour merged into
        its ``metrics`` (first matching row per contour wins, so a score is not
        repeated across e.g. a GT and a STAPLE comparison of the same contour).
        Qualitative entries with no matching metric row are emitted as their own
        ``Qualitative`` rows so nothing is lost.
        """
        has_qual = bool(self._qualitative)
        consumed: set[tuple[str, str, str, str, int]] = set()
        out: list[dict[str, Any]] = []
        for row in self._rows:
            r = dict(row)
            r["metrics"] = dict(row.get("metrics") or {})
            self._overlay_organ(r)
            key = self._row_contour_key(row)
            if key is not None and has_qual:
                assessed = key in self._qualitative
                # ``Qualitative`` flag on every ratable contour row; the score +
                # ``Blinded`` flag overlay only the first matching row.
                r["metrics"]["qualitative_assessed"] = assessed
                if assessed and key not in consumed:
                    self._overlay_qualitative(r, self._qualitative[key])
                    consumed.add(key)
            out.append(r)
        for key, entry in self._qualitative.items():
            if key not in consumed:
                synthetic = self._synthetic_qualitative_row(entry)
                self._overlay_organ(synthetic)
                out.append(synthetic)
        return out

    def __len__(self) -> int:
        # Count the rows the user actually sees (metric rows + any
        # qualitative-only rows) so the "N rows" / export / clear states stay
        # correct even when only qualitative scores exist.
        return len(self.rows())

    def metric_columns(self) -> list[str]:
        """Return the metric column headers in a stable canonical order.

        Every column in :data:`CANONICAL_METRIC_COLUMNS` is always included
        (even when no row populated it) so the table layout is identical
        across runs and Excel paste stays aligned. A tolerance-dependent metric
        computed at one or more tolerances takes its canonical place once per
        tolerance, ascending — ``surface_dice@1mm``, ``surface_dice@2mm`` — and
        keeps an empty placeholder when it was not computed. Dynamic DVH points
        (``d95_gy`` / ``v20gy_cc``) are appended after the canonical block. The
        qualitative columns — each rater's score and when it was given, then the
        assessed and blinded flags — are inserted immediately before the first
        dose (DVH) column.
        """
        seen: set[str] = set()
        for row in self._rows:
            seen.update((row.get("metrics") or {}).keys())
        seen.update(self._qualitative_metric_keys())

        canonical: list[str] = []
        placed: set[str] = set()
        for column in CANONICAL_METRIC_COLUMNS:
            if column in TOLERANCE_METRICS:
                variants = sorted(
                    (k for k in seen if base_metric(k) == column and tolerance_of(k) is not None),
                    key=lambda k: tolerance_of(k) or 0.0,
                )
                if variants:
                    canonical.extend(variants)
                    placed.update(variants)
                    continue
            canonical.append(column)
            placed.add(column)
        dynamic = seen - placed
        raters = sorted(k[len("likert_") :] for k in dynamic if k.startswith("likert_"))
        qual_dynamic: list[str] = []
        for rater in raters:
            qual_dynamic.append(f"likert_{rater}")
            if f"{SCORED_AT_PREFIX}{rater}" in dynamic:
                qual_dynamic.append(f"{SCORED_AT_PREFIX}{rater}")
        qual_dynamic += [k for k in ("qualitative_assessed", "qualitative_blinded") if k in dynamic]
        stray_scored = sorted(
            k for k in dynamic if k.startswith(SCORED_AT_PREFIX) and k not in qual_dynamic
        )
        qual_dynamic += stray_scored
        other_dynamic = sorted(dynamic - set(qual_dynamic), key=_dynamic_metric_sort_key)
        dvh_at = _first_dvh_index(canonical)
        return canonical[:dvh_at] + qual_dynamic + canonical[dvh_at:] + other_dynamic

    def export_csv(self, path: Path | str) -> int:
        """Write every stored row to a CSV at ``path``. Returns the row count.

        Columns: all metadata columns from :data:`META_COLUMNS`, followed by
        every metric key that appears in any row (qualitative scores overlaid).
        Missing metrics for a particular row are written as empty cells.
        """
        rows = self.rows()
        metrics = self.metric_columns()
        meta_keys = [k for k, _label in META_COLUMNS]
        meta_labels = [label for _k, label in META_COLUMNS]
        headers = meta_labels + [metric_display_label(k) for k in metrics]

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for row in rows:
                meta = [_format_cell(row.get(k, "")) for k in meta_keys]
                m = row.get("metrics") or {}
                metric_cells = [_format_cell(m.get(k, "")) for k in metrics]
                writer.writerow(meta + metric_cells)
        return len(rows)

    # ---- Session ---------------------------------------------------------------

    def session_state(self) -> dict[str, Any]:
        """The computed rows, for saving with the session.

        Saved so that qualitative scoring can continue over several sessions
        against a table computed once. The Likert scores are not included: the
        Qualitative tab saves them with each rater's progress, and re-sends them
        when the session is restored.
        """
        return {
            "version": RESULTS_SESSION_VERSION,
            "rows": [_json_safe(row) for row in self._rows],
        }

    def apply_session_state(self, data: dict[str, Any] | None) -> int:
        """Replace the computed rows with a saved table. Returns the row count.

        The Likert scores are left alone; the Qualitative tab restores them.
        """
        self._rows.clear()
        for row in (data or {}).get("rows", []) or []:
            if isinstance(row, dict):
                restored = dict(row)
                restored["metrics"] = dict(restored.get("metrics") or {})
                self._rows.append(restored)
        return len(self._rows)


def _json_safe(value: Any) -> Any:
    """A copy of a row that JSON can hold and give back unchanged.

    NumPy scalars become Python numbers, arrays and tuples become lists, sets
    become sorted lists. Non-finite floats are kept: an infinite Hausdorff
    distance is a result, and Python's JSON reads it back as written.
    """
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_safe(v) for v in value), key=str)
    item = getattr(value, "item", None)
    if callable(item) and not isinstance(value, (str, bytes)):
        try:
            return _json_safe(item())
        except (TypeError, ValueError):
            pass
    tolist = getattr(value, "tolist", None)
    if callable(tolist) and not isinstance(value, (str, bytes)):
        return _json_safe(tolist())
    return value


def _format_cell(value: Any) -> str:
    """Format a cell value for CSV output (no trailing-zero issues, no NaN noise)."""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        # Round to 6 significant figures for stable CSV output
        return f"{value:.6g}"
    return str(value)
