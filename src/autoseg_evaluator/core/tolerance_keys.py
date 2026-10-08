"""Metric keys that carry the tolerance they were computed at.

Surface Dice at 1 mm and at 5 mm are different measurements. They used to share
the key ``surface_dice``, with the tolerance kept once for the whole results
table and pasted into the column header; an external audit found that two runs
at different tolerances then sat under whichever header was set last, and the
report compared one run's values while naming the other's tolerance.

The tolerance is now part of the key, the way D95 and D98 are different keys:
``surface_dice@3mm``, ``poly_apl_mm@1.5mm``. A run at several tolerances fills
one column per tolerance, a row always says what its numbers were measured at,
and the report treats each tolerance as its own metric. Everything that looks a
metric up by name — labels, units, direction, family — does so through
:func:`base_metric`, so the tolerance never has to be special-cased there.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

#: Separates a metric's name from its tolerance in a key.
TOLERANCE_SEPARATOR = "@"

#: Metrics whose value depends on a tolerance: the 3D Surface Dice and the four
#: 2D added-path-length columns. The 2D distances do not take one.
SURFACE_DICE_METRICS: frozenset[str] = frozenset({"surface_dice"})
POLYGON_TOLERANCE_METRICS: frozenset[str] = frozenset(
    {"poly_apl_mm", "poly_napl", "poly_apl_reverse_mm", "poly_napl_reverse"}
)
TOLERANCE_METRICS: frozenset[str] = SURFACE_DICE_METRICS | POLYGON_TOLERANCE_METRICS

#: Tolerances are kept to 0.01 mm — the precision of the entry field — so 1,
#: 1.0 and 1.00 are one column rather than three.
DECIMALS = 2


def normalise_tolerances(value: object, default: float = 3.0) -> tuple[float, ...]:
    """Sorted, de-duplicated tolerances from a number, a list, or a text list.

    Accepts what settings files have held over time: a single number (every
    version before tolerance lists), a list of numbers, or the comma-separated
    text typed into the Compute tab. An empty value gives ``(default,)``.
    Raises ``ValueError`` for anything that is not a finite, non-negative
    number, naming it.
    """
    if value is None or value == "" or value == []:
        items: list[object] = [default]
    elif isinstance(value, str):
        items = [token for token in value.replace(";", ",").split(",") if token.strip()]
        if not items:
            items = [default]
    elif isinstance(value, Iterable):
        items = list(value)
        if not items:
            items = [default]
    else:
        items = [value]
    found: set[float] = set()
    for item in items:
        text = str(item).strip()
        try:
            number = float(text)
        except ValueError as exc:
            raise ValueError(f"Cannot read '{text}' as a tolerance in mm.") from exc
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"A tolerance must be a non-negative number of mm, not '{text}'.")
        found.add(round(number, DECIMALS))
    return tuple(sorted(found))


def tolerance_token(tolerance_mm: float) -> str:
    """``3`` -> ``"3"``, ``2.5`` -> ``"2.5"``, ``0.75`` -> ``"0.75"``."""
    text = f"{round(float(tolerance_mm), DECIMALS):.{DECIMALS}f}".rstrip("0").rstrip(".")
    return text or "0"


def tolerance_key(metric: str, tolerance_mm: float) -> str:
    """The key for ``metric`` computed at ``tolerance_mm``: ``surface_dice@3mm``."""
    return f"{metric}{TOLERANCE_SEPARATOR}{tolerance_token(tolerance_mm)}mm"


def split_tolerance(key: str) -> tuple[str, float | None]:
    """``surface_dice@3mm`` -> ``("surface_dice", 3.0)``; untouched keys get ``None``."""
    text = str(key)
    base, separator, rest = text.partition(TOLERANCE_SEPARATOR)
    if not separator or not rest.endswith("mm"):
        return text, None
    try:
        return base, float(rest[:-2])
    except ValueError:
        return text, None


def base_metric(key: str) -> str:
    """The metric's name without its tolerance, for every lookup by name."""
    return split_tolerance(key)[0]


def tolerance_of(key: str) -> float | None:
    """The tolerance a key was computed at, or ``None``."""
    return split_tolerance(key)[1]


def with_tolerance_label(label: str, key: str) -> str:
    """Append ``@ 3.00 mm`` to a display label when the key carries a tolerance."""
    tolerance = tolerance_of(key)
    return label if tolerance is None else f"{label} @ {tolerance:.2f} mm"


__all__ = [
    "DECIMALS",
    "POLYGON_TOLERANCE_METRICS",
    "SURFACE_DICE_METRICS",
    "TOLERANCE_METRICS",
    "TOLERANCE_SEPARATOR",
    "base_metric",
    "normalise_tolerances",
    "split_tolerance",
    "tolerance_key",
    "tolerance_of",
    "tolerance_token",
    "with_tolerance_label",
]
