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
    """
    flat: dict[str, str] = {}
    for canonical, variants in synonyms.items():
        canonical_norm = _normalise_for_lookup(canonical)
        canonical_display = canonical  # what we return to the matcher
        if canonical_norm:
            flat[canonical_norm] = canonical_display
        for variant in variants:
            v_norm = _normalise_for_lookup(variant)
            if v_norm:
                flat[v_norm] = canonical_display
    return flat


def _normalise_for_lookup(name: str) -> str:
    """Match the matcher's spaceless normalisation: lowercase, strip ``_-`` + whitespace."""
    return (name or "").lower().replace(" ", "").replace("_", "").replace("-", "").strip()
