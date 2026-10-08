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
    be captured by it. The shipped dictionary no longer does this — its
    generator leaves a TG-263 name to its own entry, where TG-263 lists both
    ``Elbow_L`` and ``Joint_Elbow_L`` — but a dictionary edited by hand can. A
    name that is canonical in its own right is therefore never anything else's
    synonym, whatever the data says.

    Variants whose laterality contradicts their canonical are re-filed under
    the correct side. The shipped dictionary has none: the TG-263 worksheet
    describes 13 structures as the opposite side to their own names, which
    filed every right-sided spelling of the femoral necks and common iliac
    veins under the left structure and vice versa, and ``build_synonyms.py``
    now corrects those descriptions before generating variants
    (``DESCRIPTION_CORRECTIONS``, listed in the dictionary's
    ``_tg263_corrections``). The re-filing stays for a dictionary edited by
    hand, where the same error is easy to make: taken literally, such a variant
    sends "Left_Femur Neck" to the right femoral neck, and which side wins would
    be decided by dictionary order.

    A contradicting variant with no opposite-side canonical to move to is
    dropped rather than mis-filed: losing a synonym costs a fuzzy match, while
    keeping it costs the wrong side of the body.
    """
    from autoseg_evaluator.core.organ_groups import extract_laterality

    # (normalised stem, side) -> canonical, for finding a variant's real home.
    by_stem_side: dict[tuple[str, str], str] = {}
    for canonical in synonyms:
        stem, side = extract_laterality(canonical)
        by_stem_side[(_normalise_for_lookup(stem), side)] = canonical

    flat: dict[str, str] = {}
    for canonical, variants in synonyms.items():
        c_stem, c_side = extract_laterality(canonical)
        c_stem_norm = _normalise_for_lookup(c_stem)
        for variant in variants:
            v_norm = _normalise_for_lookup(variant)
            if not v_norm:
                continue
            target = canonical
            v_side = extract_laterality(str(variant))[1]
            if c_side and v_side and c_side != v_side:
                sibling = by_stem_side.get((c_stem_norm, v_side))
                if sibling is None:
                    continue
                target = sibling
            flat[v_norm] = target

    # Canonicals last: a canonical name is never another entry's synonym.
    for canonical in synonyms:
        canonical_norm = _normalise_for_lookup(canonical)
        if canonical_norm:
            flat[canonical_norm] = canonical
    return flat


def _normalise_for_lookup(name: str) -> str:
    """Match the matcher's spaceless normalisation: lowercase, strip ``_-`` + whitespace."""
    return (name or "").lower().replace(" ", "").replace("_", "").replace("-", "").strip()
