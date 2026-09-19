"""Prose names for figures.

Tables can carry ``OpticNrv_L`` because a reader has the column header, the
tooltip and the rest of the row to orient them. A figure has none of that: an
axis label is read alone, often out of context, and frequently in a slide or a
manuscript where the abbreviation has never been defined.

So figures get prose — "Left optic nerve", "Spinal cord", "Surface Dice" — and
tables keep the canonical labels, which stay the thing the software matches on.
This module only ever *renders*; nothing here feeds a comparison.

The expansion table is deliberately incomplete. An unknown stem falls through
title-cased and readable, which is the right failure: inventing an expansion for
a name nobody recognised would be worse than showing it as written.
"""

from __future__ import annotations

import re

#: Canonical stem (lowercased, spaces collapsed) -> prose. Laterality is handled
#: separately, so these are all sideless.
ORGAN_PROSE: dict[str, str] = {
    # Head and neck
    "brainstem": "brainstem",
    "brain": "brain",
    "cochlea": "cochlea",
    "opticnrv": "optic nerve",
    "optic nrv": "optic nerve",
    "opticnerve": "optic nerve",
    "opticchiasm": "optic chiasm",
    "chiasm": "optic chiasm",
    "parotid": "parotid",
    "glnd submand": "submandibular gland",
    "glndsubmand": "submandibular gland",
    "submang": "submandibular gland",
    "glnd lacrimal": "lacrimal gland",
    "glnd thyroid": "thyroid gland",
    "thyroid": "thyroid",
    "lens": "lens",
    "eye": "eye",
    "retina": "retina",
    "lips": "lips",
    "cavity oral": "oral cavity",
    "oralcavity": "oral cavity",
    "musc constrict": "constrictor muscle",
    "larynx": "larynx",
    "esophagus": "oesophagus",
    "oesophagus": "oesophagus",
    "trachea": "trachea",
    "mandible": "mandible",
    "pituitary": "pituitary",
    "spinalcord": "spinal cord",
    "spinal cord": "spinal cord",
    "cord": "spinal cord",
    "brachialplex": "brachial plexus",
    "lobe temporal": "temporal lobe",
    # Thorax
    "lung": "lung",
    "lungs": "lungs",
    "heart": "heart",
    "breast": "breast",
    "a lad": "left anterior descending artery",
    "chestwall": "chest wall",
    # Abdomen and pelvis
    "liver": "liver",
    "kidney": "kidney",
    "kidneys": "kidneys",
    "stomach": "stomach",
    "spleen": "spleen",
    "pancreas": "pancreas",
    "duodenum": "duodenum",
    "bowel small": "small bowel",
    "bowel large": "large bowel",
    "bowel": "bowel",
    "colon sigmoid": "sigmoid colon",
    "rectum": "rectum",
    "anorectum": "anorectum",
    "bladder": "bladder",
    "prostate": "prostate",
    "seminalves": "seminal vesicles",
    "uterus": "uterus",
    "cervix uteri": "cervix",
    "ovary": "ovary",
    "femur": "femur",
    "femur head": "femoral head",
    "femurhead": "femoral head",
    "bone pelvic": "pelvic bone",
    "canal anal": "anal canal",
    "penilebulb": "penile bulb",
    "urethra": "urethra",
    "sacrum": "sacrum",
    "skin": "skin",
    "body": "body",
    "external": "external",
}

_SIDE_PROSE = {"L": "Left", "R": "Right", "LT": "Left", "RT": "Right"}

#: Matches the trailing ``(L)`` / ``(R)`` an organ key's label carries.
_SIDE = re.compile(r"\s*\(([^)]+)\)\s*$")

#: Matches a trailing ``[qualifier]`` such as ``[target]``.
_QUALIFIER = re.compile(r"\s*\[([^\]]+)\]\s*$")


def readable_organ(label: str) -> str:
    """``Opticnrv (L)`` -> ``Left optic nerve``.

    Falls through with the stem intact when it is not recognised, since an
    invented expansion would read as authoritative and be wrong.
    """
    text = str(label).strip()
    if not text:
        return ""

    qualifier = ""
    match = _QUALIFIER.search(text)
    if match:
        qualifier = match.group(1).strip()
        text = text[: match.start()].strip()

    side = ""
    match = _SIDE.search(text)
    if match:
        side = _SIDE_PROSE.get(match.group(1).strip().upper(), match.group(1).strip())
        text = text[: match.start()].strip()

    stem = re.sub(r"[_\s]+", " ", text).strip().lower()
    prose = ORGAN_PROSE.get(stem) or ORGAN_PROSE.get(stem.replace(" ", ""))
    if prose is None:
        prose = text.strip()
        name = prose if prose[:1].isupper() else prose.capitalize()
    else:
        name = prose

    if side:
        name = f"{side} {name}"
    else:
        name = name[:1].upper() + name[1:]
    if qualifier and qualifier.lower() != "oar":
        name = f"{name} ({qualifier})"
    return name


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
    "ORGAN_PROSE",
    "TOLERANCE_METRICS",
    "metric_units",
    "readable_metric",
    "readable_organ",
    "tolerance_note",
]
