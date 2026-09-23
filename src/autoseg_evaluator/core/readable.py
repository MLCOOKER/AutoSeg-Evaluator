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
    "hausdorff95": "3D Hausdorff 95%",
    "hausdorff100": "3D Hausdorff (maximum)",
    "mean_surface_distance": "Mean surface distance",
    "volume_gt_cc": "Reference volume",
    "volume_test_cc": "Test volume",
    "volume_diff_cc": "Volume difference",
    "volume_ratio": "Volume ratio",
    "poly_apl_mm": "2D added path length",
    "poly_napl": "2D normalised added path length",
    "poly_apl_reverse_mm": "2D added path length, reverse",
    "poly_napl_reverse": "2D normalised added path length, reverse",
    "poly_hd100_mm": "2D Hausdorff (maximum)",
    "poly_hd95_mm": "2D Hausdorff 95%",
    "poly_mean_distance_mm": "2D mean contour distance",
    "poly_median_distance_mm": "2D median contour distance",
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
    "com_offset_mm": "mm",
    "com_dx_mm": "mm",
    "com_dy_mm": "mm",
    "com_dz_mm": "mm",
    "volume_gt_cc": "cc",
    "volume_test_cc": "cc",
    "volume_diff_cc": "cc",
    "poly_apl_mm": "mm",
    "poly_apl_reverse_mm": "mm",
    "poly_hd100_mm": "mm",
    "poly_hd95_mm": "mm",
    "poly_mean_distance_mm": "mm",
    "poly_median_distance_mm": "mm",
    "dmin_gy": "Gy",
    "dmean_gy": "Gy",
    "dmax_gy": "Gy",
}

#: Metrics whose value is meaningless without the tolerance they were computed
#: at. Surface Dice at 1 mm and at 5 mm are different measurements, and two
#: figures that do not say which cannot be compared.
TOLERANCE_METRICS: frozenset[str] = frozenset(
    {
        "surface_dice",
        # The 2D added path length takes its own tolerance, set in the 2D group
        # on the Compute tab, not Surface Dice's.
        "poly_apl_mm",
        "poly_napl",
        "poly_apl_reverse_mm",
        "poly_napl_reverse",
    }
)

#: Of those, the ones whose tolerance comes from the polygon stream.
POLYGON_TOLERANCE_METRICS: frozenset[str] = frozenset(
    {"poly_apl_mm", "poly_napl", "poly_apl_reverse_mm", "poly_napl_reverse"}
)


#: Metrics bounded to [0, 1] by construction. Their axis shows the whole range,
#: because a Dice axis cropped to 0.78-0.86 makes a three-point spread look like
#: a chasm, and a reader who does not check the tick labels will read it as one.
BOUNDED_UNIT_METRICS: frozenset[str] = frozenset(
    {
        "dice",
        "surface_dice",
        "precision",
        "recall",
        "sensitivity",
        "specificity",
        "staple_sensitivity",
        "staple_specificity",
        # Boundary length missed as a share of the ground truth's length.
        "poly_napl",
        "poly_napl_reverse",
    }
)

#: Metrics that cannot go below zero. Zero is kept in view because it is the
#: meaningful floor — a perfect contour — and an axis starting at 3 mm hides how
#: far from perfect everything on it is.
NON_NEGATIVE_METRICS: frozenset[str] = frozenset(
    {
        "hausdorff95",
        "hausdorff100",
        "mean_surface_distance",
        "com_offset_mm",
        "volume_gt_cc",
        "volume_test_cc",
        "volume_ratio",
        "poly_apl_mm",
        "poly_apl_reverse_mm",
        "poly_hd100_mm",
        "poly_hd95_mm",
        "poly_mean_distance_mm",
        "poly_median_distance_mm",
    }
)

SCALE_BOUNDED_UNIT = "bounded01"
SCALE_NON_NEGATIVE = "nonnegative"
SCALE_SIGNED = "signed"
SCALE_FREE = "free"


def metric_scale(metric: str) -> str:
    """How a figure's axis should be bounded for this metric.

    Autoscaling to the data is the wrong default here. Ten Dice values between
    0.78 and 0.86 autoscale to an axis where a 0.01 difference spans a third of
    the plot; the same figure on the full 0-1 range shows what it is, which is
    a small difference between four good contours.
    """
    key = str(metric).strip().lower()
    if key in BOUNDED_UNIT_METRICS:
        return SCALE_BOUNDED_UNIT
    # Differences are tested before anything else, because a difference of a
    # non-negative quantity is not itself non-negative: ``d2cc_gy`` cannot go
    # below zero but ``d2cc_gy_diff`` is test minus reference and routinely does.
    if key.endswith("_diff") or "diff" in key or key.startswith("com_d"):
        return SCALE_SIGNED
    if key in NON_NEGATIVE_METRICS:
        return SCALE_NON_NEGATIVE
    if key.endswith("_gy") or "gy_cc" in key or re.match(r"^[dv]\d", key):
        return SCALE_NON_NEGATIVE
    return SCALE_FREE


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
    metric: str,
    sd_tau_mm: float | None = None,
    poly_tau_mm: float | None = None,
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
    tolerance = poly_tau_mm if key in POLYGON_TOLERANCE_METRICS else sd_tau_mm
    if tolerance is None:
        return "tolerance not recorded"
    return f"tolerance = {float(tolerance):.2f} mm"


__all__ = [
    "BOUNDED_UNIT_METRICS",
    "METRIC_PROSE",
    "METRIC_UNITS",
    "NON_NEGATIVE_METRICS",
    "POLYGON_TOLERANCE_METRICS",
    "SCALE_BOUNDED_UNIT",
    "SCALE_FREE",
    "SCALE_NON_NEGATIVE",
    "SCALE_SIGNED",
    "TOLERANCE_METRICS",
    "metric_scale",
    "metric_units",
    "readable_metric",
    "tolerance_note",
]
