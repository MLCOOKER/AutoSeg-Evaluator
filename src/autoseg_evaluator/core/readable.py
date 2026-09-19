"""Prose names for metrics, for figure titles and axes.

``surface_dice`` is a dictionary key, not a label. Figures get "Surface Dice",
with the unit on the axis and the tolerance in the subtitle, so a title stays a
name rather than a specification.

**Organ names are deliberately not here.** They come from the canonical organ
the Matching tab assigned — the same value the Results table shows in its Organ
column and the CSV exports — so a figure, a table and an export all name a
structure identically. An expansion dictionary living here would have been a
second naming authority, free to drift from the one the user curated.

This module only ever *renders*; nothing here feeds a comparison.
"""

from __future__ import annotations

import re

#: Metric key -> prose, without units or tolerance. Units belong on the axis and
#: the tolerance in the subtitle, so a title stays a name.
METRIC_PROSE: dict[str, str] = {
    "dice": "Dice",
    "surface_dice": "Surface Dice",
    "hausdorff95": "Hausdorff 95%",
    "hausdorff100": "Hausdorff (maximum)",
    "mean_surface_distance": "Mean surface distance",
    "apl_mean": "Mean added path length",
    "apl_total": "Total added path length",
    "volume_gt_cc": "Reference volume",
    "volume_test_cc": "Test volume",
    "volume_diff_cc": "Volume difference",
    "volume_ratio": "Volume ratio",
    "com_offset_mm": "Centre-of-mass offset",
    "com_dx_mm": "Centre-of-mass offset, x",
    "com_dy_mm": "Centre-of-mass offset, y",
    "com_dz_mm": "Centre-of-mass offset, z",
    "precision": "Precision",
    "recall": "Recall",
    "staple_sensitivity": "Sensitivity vs consensus",
    "staple_specificity": "Specificity vs consensus",
    "dmin_gy": "Minimum dose",
    "dmean_gy": "Mean dose",
    "dmax_gy": "Maximum dose",
}

#: Metric key -> unit, for the axis.
METRIC_UNITS: dict[str, str] = {
    "hausdorff95": "mm",
    "hausdorff100": "mm",
    "mean_surface_distance": "mm",
    "apl_mean": "mm",
    "apl_total": "mm",
    "com_offset_mm": "mm",
    "com_dx_mm": "mm",
    "com_dy_mm": "mm",
    "com_dz_mm": "mm",
    "volume_gt_cc": "cc",
    "volume_test_cc": "cc",
    "volume_diff_cc": "cc",
    "dmin_gy": "Gy",
    "dmean_gy": "Gy",
    "dmax_gy": "Gy",
}

#: Metrics whose value is meaningless without the tolerance they were computed
#: at. Surface Dice at 1 mm and at 5 mm are different measurements, and two
#: figures that do not say which cannot be compared.
TOLERANCE_METRICS: frozenset[str] = frozenset({"surface_dice", "apl_mean", "apl_total"})


def readable_metric(metric: str) -> str:
    """``surface_dice`` -> ``Surface Dice``, falling through unknown keys."""
    key = str(metric).strip()
    prose = METRIC_PROSE.get(key.lower())
    if prose:
        return prose
    # Dose metrics are generated, so they are matched by shape: D95_gy, V20gy_cc.
    dose = re.fullmatch(r"([dv])(\d+(?:\.\d+)?)(cc)?_?gy(_cc|_pct)?", key, re.IGNORECASE)
    if dose:
        letter, value, per_cc, suffix = dose.groups()
        name = f"{letter.upper()}{value}{'cc' if per_cc else 'Gy' if letter.lower() == 'v' else ''}"
        return name
    return key.replace("_", " ").strip().capitalize()


def metric_units(metric: str) -> str:
    key = str(metric).strip().lower()
    if key in METRIC_UNITS:
        return METRIC_UNITS[key]
    if key.endswith("_gy"):
        return "Gy"
    if key.endswith("_cc") or "gy_cc" in key:
        return "cc"
    if key.endswith("_pct"):
        return "%"
    return ""


def tolerance_note(
    metric: str, sd_tau_mm: float | None = None, apl_tau_mm: float | None = None
) -> str:
    """``tolerance = 3.00 mm``, or empty where the metric does not take one.

    Surface Dice at 1 mm and at 5 mm are different measurements. A figure that
    omits which was used cannot be compared with another, and the number looks
    authoritative either way — so where the tolerance is required and not
    recorded, that is said rather than left blank.
    """
    key = str(metric).strip().lower()
    if key not in TOLERANCE_METRICS:
        return ""
    tolerance = sd_tau_mm if key == "surface_dice" else apl_tau_mm
    if tolerance is None:
        return "tolerance not recorded"
    return f"tolerance = {float(tolerance):.2f} mm"


__all__ = [
    "METRIC_PROSE",
    "METRIC_UNITS",
    "TOLERANCE_METRICS",
    "metric_units",
    "readable_metric",
    "tolerance_note",
]
