"""Deterministic string-matching pipeline for organ-name comparison.

Pipeline (applied to each name before comparison):

1. Lowercase + trim whitespace.
2. Apply user-defined replacement rules (``Find`` → ``Replace``).
3. Convert ``_`` / ``-`` to spaces; collapse repeated whitespace.
4. If the spaceless form matches a known synonym variant, replace the
   whole string with the synonym's canonical form.

Similarity is then computed by combining a normalised Levenshtein
edit-distance with a character-frequency cosine distance — the same
hybrid algorithm used by the original AutoSeg Evaluator (Rusanov et al.
2025). The two distances are averaged and the result is returned as a
similarity in ``[0, 1]`` via ``1 - distance``.

This module is deliberately UI-free and pydicom-free so it can be unit
tested without any GUI or DICOM fixtures.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal, TypeVar

import numpy as np
from scipy import spatial

T = TypeVar("T")

# ``match_method`` values. ``tg263`` means both inputs resolved to the same
# canonical via the bundled TG-263 dictionary — a high-confidence match the UI
# can badge accordingly. ``fuzzy`` covers everything else with a positive score
# (literal string equality, partial Lev+cosine, anything not dictionary-backed).
MatchMethod = Literal["tg263", "fuzzy", "none"]


@dataclass(frozen=True)
class ReplacementRule:
    """A user-defined ``Find`` → ``Replace`` rule.

    Replacement is case-insensitive (we lowercase both sides before
    substitution). ``find`` matches as a substring, so ``Musc_Constrict``
    can be rewritten to ``Pharyngeal_Constrictor`` regardless of any
    surrounding tokens.
    """

    find: str
    replace: str


@dataclass(frozen=True)
class Match:
    """Outcome of a similarity comparison between two organ names.

    ``score`` is the usual ``[0, 1]`` similarity. ``method`` records how
    the match was established — surfaced in the UI as a small badge so
    clinicians can distinguish dictionary-grounded matches from
    string-similarity guesses.
    """

    score: float
    method: MatchMethod

    def __float__(self) -> float:  # convenience for callers that only want the score
        return float(self.score)


def _normalise_only(name: str, rules: Iterable[ReplacementRule] = ()) -> str:
    """Apply lowercase + rules + separator-to-space normalisation. No dict lookup.

    Used by :func:`similarity` to compute the fuzzy fallback on consistent
    lowercase strings WITHOUT substituting any dictionary canonical — which
    avoids the unfair length / case asymmetry that arose when only one side
    resolved via the synonyms dictionary.
    """
    if not name:
        return ""
    s = name.lower().strip()
    for rule in rules:
        if rule.find:
            s = s.replace(rule.find.lower(), rule.replace.lower())
    s = s.replace("_", " ").replace("-", " ")
    return " ".join(s.split())


def canonicalise(
    name: str,
    rules: Iterable[ReplacementRule] = (),
    synonyms_flat: dict[str, str] | None = None,
) -> str:
    """Apply the full normalisation pipeline to ``name``.

    Returns the canonical form (lowercase, spaces between tokens, synonyms
    expanded). Whitespace, underscores and hyphens are treated as token
    separators. An empty/None input becomes the empty string.
    """
    canonical, _resolved = canonicalise_with_meta(name, rules, synonyms_flat)
    return canonical


def canonicalise_with_meta(
    name: str,
    rules: Iterable[ReplacementRule] = (),
    synonyms_flat: dict[str, str] | None = None,
) -> tuple[str, bool]:
    """Return ``(canonical_form, resolved_via_dictionary)``.

    The canonical form is always lowercased so it can be compared directly
    against other lowercased forms without case-sensitivity tripping up
    downstream Lev / cosine calculations.

    The boolean flag is ``True`` iff step 4 of the pipeline produced a hit
    in the synonyms dictionary — i.e. the input mapped to a known TG-263
    (or other) canonical. Used by :func:`similarity` to label matches with
    their provenance.
    """
    s = _normalise_only(name, rules)
    if not s:
        return "", False

    if synonyms_flat:
        spaceless = s.replace(" ", "")
        canonical = synonyms_flat.get(spaceless)
        if canonical:
            # Lowercase the canonical: the JSON preserves TG-263's display
            # casing (``Eye_L``), but the matcher must compare case-insensitively
            # against the lowercased fallback form to avoid spurious mismatches.
            return canonical.lower(), True
    return s, False


def similarity(
    a: str,
    b: str,
    *,
    rules: Iterable[ReplacementRule] = (),
    synonyms_flat: dict[str, str] | None = None,
) -> Match:
    """Return a :class:`Match` for two organ names.

    Behaviour:

    * If **both** inputs resolve to the **same** TG-263 canonical via the
      synonyms dictionary, return ``Match(1.0, "tg263")`` — the
      high-confidence dictionary-grounded path.
    * Otherwise, fall back to the hybrid Levenshtein + cosine algorithm
      from AutoSeg Evaluator v1, computed on the *cleaned-but-not-
      substituted* raw forms (lowercase, separators-to-spaces, rules
      applied, but no canonical swap). This avoids the asymmetry that
      arose when only one side resolved via the dictionary: substituting
      a short canonical for a long verbose name distorts both Lev and
      cosine, causing semantically wrong candidates to win.
    * Empty inputs return ``Match(0.0, "none")``.
    """
    _ca, a_resolved = canonicalise_with_meta(a, rules, synonyms_flat)
    _cb, b_resolved = canonicalise_with_meta(b, rules, synonyms_flat)
    if not _ca or not _cb:
        return Match(0.0, "none")

    # TG-263 short-circuit — both sides resolved to the same canonical.
    if a_resolved and b_resolved and _ca == _cb:
        return Match(1.0, "tg263")

    # Fall back to fuzzy on the cleaned RAW forms (no dictionary substitution).
    ca = _normalise_only(a, rules)
    cb = _normalise_only(b, rules)
    if ca == cb:
        return Match(1.0, "fuzzy")
    if ca.replace(" ", "") == cb.replace(" ", ""):
        return Match(1.0, "fuzzy")
    lev_norm = _normalised_levenshtein(ca, cb)
    cos_dist = _cosine_char_distance(ca, cb)
    distance = 0.5 * lev_norm + 0.5 * cos_dist
    score = max(0.0, min(1.0, 1.0 - distance))
    return Match(score, "fuzzy" if score > 0 else "none")


def _normalised_levenshtein(a: str, b: str) -> float:
    """Levenshtein edit distance divided by the longer string's length."""
    longest = max(len(a), len(b))
    if longest == 0:
        return 0.0
    return _levenshtein_distance(a, b) / longest


def _levenshtein_distance(a: str, b: str) -> int:
    """Classic iterative Levenshtein distance (case-sensitive on already-lowered inputs)."""
    if len(a) < len(b):
        return _levenshtein_distance(b, a)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, c1 in enumerate(a):
        cur = [i + 1]
        for j, c2 in enumerate(b):
            ins = prev[j + 1] + 1
            dele = cur[j] + 1
            sub = prev[j] + (c1 != c2)
            cur.append(min(ins, dele, sub))
        prev = cur
    return prev[-1]


def _cosine_char_distance(a: str, b: str) -> float:
    """Cosine distance between character-frequency vectors of ``a`` and ``b``."""
    c1 = Counter(a)
    c2 = Counter(b)
    all_chars = set(c1) | set(c2)
    v1 = np.array([c1[ch] for ch in all_chars], dtype=float)
    v2 = np.array([c2[ch] for ch in all_chars], dtype=float)
    if v1.sum() == 0 or v2.sum() == 0:
        return 1.0
    return float(spatial.distance.cosine(v1, v2))


def best_match(
    target: str,
    candidates: list[T],
    *,
    key: Callable[[T], str],
    rules: Iterable[ReplacementRule] = (),
    synonyms_flat: dict[str, str] | None = None,
) -> tuple[T | None, Match]:
    """Return ``(candidate, Match)`` for the closest match to ``target``.

    On ties the first candidate wins. When the candidate list is empty the
    return is ``(None, Match(0.0, "none"))``. When every candidate scored 0
    we fall through to the first candidate so callers always get something
    they can label as a "best (poor) match" rather than silently dropping
    the file — the accompanying :class:`Match` keeps ``score=0`` and
    ``method="none"`` so the UI can show that honestly.
    """
    best: T | None = None
    best_match_obj = Match(0.0, "none")
    for candidate in candidates:
        m = similarity(target, key(candidate), rules=rules, synonyms_flat=synonyms_flat)
        if m.score > best_match_obj.score:
            best_match_obj = m
            best = candidate
    if best is None and candidates:
        return candidates[0], Match(0.0, "none")
    return best, best_match_obj
