"""Loader for the organ-name synonyms dictionary.

The on-disk format (``synonyms.json``) is ``{canonical: [variants…]}``.
We "flatten" this into a lookup ``{normalised_variant: canonical}`` for
use by :mod:`autoseg_evaluator.core.matching`.

Both the canonical key and each variant are normalised the same way as
the matching pipeline (lowercase + strip ``_-`` and whitespace) so the
lookup is robust to formatting differences.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

_log = logging.getLogger(__name__)


def load_synonyms(path: Path | str) -> dict[str, list[str]]:
    """Load the raw ``{canonical: [variants…]}`` mapping from disk.

    Returns an empty dict on a missing or invalid file. The synonym file
    is treated as a best-effort hint to the matcher; corruption should
    never crash the app.
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("Failed to load synonyms from %s: %s", p, exc)
        return {}

    out: dict[str, list[str]] = {}
    for key, value in (raw or {}).items():
        if not isinstance(key, str):
            continue
        if key.startswith("_"):
            # Keys like ``_comment`` are documentation, not data
            continue
        if not isinstance(value, list):
            continue
        out[key] = [str(v) for v in value if isinstance(v, str)]
    return out


def flatten_synonyms(synonyms: dict[str, list[str]]) -> dict[str, str]:
    """Return ``{normalised_variant: canonical_form}`` for matcher lookup.

    Each canonical name is itself added as a variant of itself so a
    spelling that already matches the canonical key resolves cleanly.

    Canonical names are written **after** every variant list, because a
    canonical name may appear inside some *other* entry's variants and must not
    be captured by it. The shipped dictionary does this eleven times, and two
    of them invert laterality: ``Femur_Neck_L`` lists ``Femur Neck_R`` as a
    variant and vice versa, so a single pass leaves ``Femur_Neck_R`` resolving
    to the left femoral neck. Since laterality is read from the canonical, that
    would file right-sided contours under the left organ.

    A name that is canonical in its own right is therefore never anything
    else's synonym, whatever the data says.
    """
    flat: dict[str, str] = {}
    for canonical, variants in synonyms.items():
        for variant in variants:
            v_norm = _normalise_for_lookup(variant)
            if v_norm:
                flat[v_norm] = canonical
    for canonical in synonyms:
        canonical_norm = _normalise_for_lookup(canonical)
        if canonical_norm:
            flat[canonical_norm] = canonical
    return flat


def _normalise_for_lookup(name: str) -> str:
    """Match the matcher's spaceless normalisation: lowercase, strip ``_-`` + whitespace."""
    return (name or "").lower().replace(" ", "").replace("_", "").replace("-", "").strip()
