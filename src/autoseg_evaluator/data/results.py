"""In-memory store of computed metric rows.

Results accumulate across multiple compute runs so the user can iterate
(add a vendor, recompute, view both runs) without losing previous work.
The :class:`ResultsManager` is owned by :class:`MainWindow` and read by
the Results tab (step 9) for table display + CSV export.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

# Order of the fixed metadata columns shown before the dynamic metric columns.
META_COLUMNS: list[tuple[str, str]] = [
    ("drawer", "Drawer"),
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
    "surface_dice",
    # Surface distances
    "hausdorff100",
    "hausdorff95",
    "mean_surface_distance",
    # Added Path Length
    "apl_mean",
    "apl_total",
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
    "surface_dice": "Surface Dice",
    # Surface distances
    "hausdorff100": "Hausdorff 100% (mm)",
    "hausdorff95": "Hausdorff 95% (mm)",
    "mean_surface_distance": "Mean Surface Distance (mm)",
    # Added Path Length
    "apl_mean": "Mean APL (mm)",
    "apl_total": "Total APL (mm)",
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
    apl_tau_mm: float | None = None,
) -> str:
    """Return the user-facing column header for a metric key (with units).

    Handles the canonical static labels (above), plus dynamic DVH metrics
    matching ``d{X}_gy`` → ``D{X} (Gy)``, ``d{X}cc_gy`` → ``D{X}cc (Gy)``,
    and ``v{X}gy_cc`` → ``V{X}Gy (cc)``. Anything else falls through as-is
    so non-standard keys are still visible.

    When ``sd_tau_mm`` / ``apl_tau_mm`` are supplied, the Surface Dice and
    APL headers are decorated with the tolerance value so two CSVs
    computed at different tolerances can't be silently mixed up (e.g.
    ``Surface Dice @ 3.00 mm`` vs ``Surface Dice @ 5.00 mm``).

    DVH difference columns (``{base}_diff``, e.g. ``d2cc_gy_diff``) reuse the
    base metric's label suffixed with ``Δ vs GT`` (the value is test − GT).
    """
    if key.endswith("_diff"):
        base = metric_display_label(key[:-5], sd_tau_mm=sd_tau_mm, apl_tau_mm=apl_tau_mm)
        return f"{base} Δ vs GT"
    if key == "surface_dice" and sd_tau_mm is not None:
        return f"Surface Dice @ {sd_tau_mm:.2f} mm"
    if key == "apl_mean" and apl_tau_mm is not None:
        return f"Mean APL @ {apl_tau_mm:.2f} mm"
    if key == "apl_total" and apl_tau_mm is not None:
        return f"Total APL @ {apl_tau_mm:.2f} mm"
    if key in _METRIC_LABELS:
        return _METRIC_LABELS[key]
    # Qualitative (Likert) columns: a score per rater + assessed / blinded flags.
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
    """Accumulating store of per-row metric results."""

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []
        # Qualitative (Likert) scores, stored against the contour and overlaid
        # onto its metric row at read time so each contour stays a single row.
        # Keyed by (patient_id, drawer, source_label, roi_number).
        self._qualitative: dict[tuple[str, str, str, int], dict[str, Any]] = {}
        # Tolerance values that produced this batch of results — surfaced
        # in the Surface Dice / APL column headers so two CSVs computed
        # at different tolerances can't be silently merged in Excel.
        self._sd_tau_mm: float | None = None
        self._apl_tau_mm: float | None = None

    def set_tolerances(self, sd_tau_mm: float | None, apl_tau_mm: float | None) -> None:
        """Record the τ values active for the *next* batch of rows added.

        Called by MainWindow when a Compute run starts. The values are
        forwarded into :func:`metric_display_label` whenever headers are
        rendered (UI + CSV export), so the heading reads e.g.
        ``Surface Dice @ 3.00 mm`` instead of ambiguously ``Surface Dice``.
        """
        self._sd_tau_mm = sd_tau_mm
        self._apl_tau_mm = apl_tau_mm

    def tolerances(self) -> tuple[float | None, float | None]:
        return (self._sd_tau_mm, self._apl_tau_mm)

    def add_row(self, row: dict[str, Any]) -> None:
        self._rows.append(dict(row))

    def add_rows(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            self.add_row(r)

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
    ) -> None:
        """Record one rater's Likert score for one contour.

        The score is stored against the contour (patient, drawer, source, roi)
        and overlaid onto that contour's metric row at read time, so the Likert
        score sits on the **same row** as the geometric / dose metrics. A
        synthetic row is emitted only when no metric row exists for the contour
        (e.g. the qualitative pass ran before Compute). A single
        ``qualitative_blinded`` flag records whether the run was blinded (True)
        or transparent (False); re-rating overwrites in place.
        """
        key = (str(patient_id), str(drawer), str(source_label), int(roi_number))
        entry = self._qualitative.get(key)
        if entry is None:
            entry = {
                "patient_id": patient_id,
                "drawer": drawer,
                "source_label": source_label,
                "roi_name": roi_name,
                "roi_number": int(roi_number),
                "is_gt": bool(is_gt),
                "scores": {},
                "blinded": None,
            }
            self._qualitative[key] = entry
        entry["scores"][_safe_rater(rater)] = int(score)
        entry["blinded"] = bool(blinded)

    def _qualitative_metric_keys(self) -> set[str]:
        if not self._qualitative:
            return set()
        keys: set[str] = {"qualitative_assessed"}
        for entry in self._qualitative.values():
            for rater in entry["scores"]:
                keys.add(f"likert_{rater}")
            if entry.get("blinded") is not None:
                keys.add("qualitative_blinded")
        return keys

    @staticmethod
    def _row_contour_key(row: dict[str, Any]) -> tuple[str, str, str, int] | None:
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
            roi,
        )

    @staticmethod
    def _overlay_qualitative(row: dict[str, Any], entry: dict[str, Any]) -> None:
        for rater, score in entry["scores"].items():
            row["metrics"][f"likert_{rater}"] = int(score)
        if entry.get("blinded") is not None:
            row["metrics"]["qualitative_blinded"] = bool(entry["blinded"])

    def _synthetic_qualitative_row(self, entry: dict[str, Any]) -> dict[str, Any]:
        metrics: dict[str, Any] = {f"likert_{r}": int(s) for r, s in entry["scores"].items()}
        metrics["qualitative_assessed"] = True
        if entry.get("blinded") is not None:
            metrics["qualitative_blinded"] = bool(entry["blinded"])
        return {
            "drawer": entry["drawer"],
            "patient_id": entry["patient_id"],
            "comparison_mode": "Qualitative",
            "was_designated_gt": bool(entry.get("is_gt")),
            "gt_source_label": "",
            "gt_rtstruct_filename": "",
            "gt_roi_name": "",
            "test_source_label": entry["source_label"],
            "test_rtstruct_filename": "",
            "test_organ": entry["roi_name"],
            "test_roi_number": int(entry["roi_number"]),
            "truncated": False,
            "similarity": "",
            "error": "",
            "metrics": metrics,
        }

    def clear(self) -> None:
        self._rows.clear()
        self._qualitative.clear()
        # Tolerances are batch-scoped — drop them when the batch is cleared.
        self._sd_tau_mm = None
        self._apl_tau_mm = None

    def rows(self) -> list[dict[str, Any]]:
        """Snapshot of all rows, with qualitative scores overlaid.

        Each metric row gets the Likert score(s) for its contour merged into
        its ``metrics`` (first matching row per contour wins, so a score is not
        repeated across e.g. a GT and a STAPLE comparison of the same contour).
        Qualitative entries with no matching metric row are emitted as their own
        ``Qualitative`` rows so nothing is lost.
        """
        has_qual = bool(self._qualitative)
        consumed: set[tuple[str, str, str, int]] = set()
        out: list[dict[str, Any]] = []
        for row in self._rows:
            r = dict(row)
            r["metrics"] = dict(row.get("metrics") or {})
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
                out.append(self._synthetic_qualitative_row(entry))
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
        across runs and Excel paste stays aligned. Dynamic DVH points
        (``d95_gy`` / ``v20gy_cc``) are appended after the canonical block.
        The qualitative columns (``likert_<rater>`` then ``qualitative_blinded``)
        are inserted immediately before the first dose (DVH) column.
        """
        seen: set[str] = set()
        for row in self._rows:
            seen.update((row.get("metrics") or {}).keys())
        seen.update(self._qualitative_metric_keys())

        canonical = list(CANONICAL_METRIC_COLUMNS)
        dynamic = seen - set(canonical)
        qual_dynamic = sorted(
            k
            for k in dynamic
            if k.startswith("likert_") or k in ("qualitative_assessed", "qualitative_blinded")
        )
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
        headers = meta_labels + [
            metric_display_label(k, sd_tau_mm=self._sd_tau_mm, apl_tau_mm=self._apl_tau_mm)
            for k in metrics
        ]

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


def _format_cell(value: Any) -> str:
    """Format a cell value for CSV output (no trailing-zero issues, no NaN noise)."""
    import math

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
