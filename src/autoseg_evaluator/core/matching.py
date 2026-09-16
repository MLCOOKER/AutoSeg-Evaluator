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


#: ``match_method`` values meaning "these name different structures". The score
#: is 0 and the UI shows the reason, rather than a high similarity quietly
#: carrying a wrong pairing into metric computation.
MISMATCH_LATERALITY = "mismatch-laterality"
MISMATCH_INDEX = "mismatch-index"
MISMATCH_POSITION = "mismatch-position"
MISMATCH_CANONICAL = "mismatch-canonical"

MISMATCH_REASONS = {
    MISMATCH_LATERALITY: "opposite sides of the body",
    MISMATCH_INDEX: "different numbered structures",
    MISMATCH_POSITION: "opposite anatomical positions",
    MISMATCH_CANONICAL: "two different structures named in TG-263",
}


def structural_mismatch(a: str, b: str) -> str:
    """Name the axis on which two ROI names describe different structures.

    Returns one of the ``MISMATCH_*`` values, or ``""`` when nothing rules the
    pair out. This exists because string similarity cannot make the call:
    ``Lens_R`` against ``Lens_L`` scores 0.85, comfortably above the default
    0.6 matching threshold, while ``Parotid_L`` against ``Submandibular_L`` —
    genuinely different organs — scores 0.38. Ranking by similarity alone will
    therefore pair a right lens with a left one whenever the right one is the
    only candidate a vendor produced, and nothing downstream will notice.

    Only *conflicting* values block a pair. A name that specifies a side and
    one that does not (``Parotid_L`` against ``Parotid``) is left alone, since
    that is an omission rather than a contradiction.
    """
    # Imported inside the function: organ_groups depends on this module for
    # canonicalisation, so a module-level import here would be a cycle.
    from autoseg_evaluator.core.organ_groups import (
        extract_laterality,
        index_signature,
        positional_signature,
    )

    lat_a = extract_laterality(a)[1]
    lat_b = extract_laterality(b)[1]
    if lat_a and lat_b and lat_a != lat_b:
        return MISMATCH_LATERALITY

    idx_a, idx_b = index_signature(a), index_signature(b)
    if idx_a and idx_b and idx_a != idx_b:
        return MISMATCH_INDEX

    pos_a, pos_b = positional_signature(a), positional_signature(b)
    if pos_a and pos_b and pos_a != pos_b:
        return MISMATCH_POSITION

    return ""


def similarity(
    a: str,
    b: str,
    *,
    rules: Iterable[ReplacementRule] = (),
    synonyms_flat: dict[str, str] | None = None,
) -> Match:
    """Return a :class:`Match` for two organ names.

    Behaviour:

    * If the two names conflict on side, numbering or anatomical position,
      return ``Match(0.0, "mismatch-…")``. See :func:`structural_mismatch` —
      similarity scores these pairs high, so the check has to come first.
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

    # Checked before the dictionary short-circuit: two names can resolve to
    # the same canonical stem and still be opposite sides of the body.
    conflict = structural_mismatch(a, b)
    if conflict:
        return Match(0.0, conflict)

    # TG-263 short-circuit — both sides resolved to the same canonical.
    if a_resolved and b_resolved and _ca == _cb:
        return Match(1.0, "tg263")

    # …and its mirror image. When the dictionary recognises *both* names and
    # gives them different canonicals, the standard itself has said these are
    # two structures, and no amount of string resemblance should overrule that.
    # ``Lens_L`` against ``Lung_L`` scores 0.67 on characters — enough to pass
    # the default 0.6 threshold and become the chosen match when the real lens
    # is missing from a vendor's output.
    #
    # The risk is a dictionary that splits two genuinely equivalent names, in
    # which case a correct pair is refused. That costs a red badge on a good
    # match, which is visible and dismissible; the alternative costs a wrong
    # organ in a published metric, which is neither.
    if a_resolved and b_resolved and _ca != _cb:
        return Match(0.0, MISMATCH_CANONICAL)

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
    the file — the accompanying :class:`Match` keeps ``score=0`` so the UI can
    show that honestly.

    That fall-through keeps the first candidate's own :class:`Match` rather
    than a blank one, because its ``method`` may carry *why* nothing matched.
    When the only contour a vendor produced is the opposite side of the body,
    "no match" and "that is the other side" are very different things to show
    a user, and the second is the one worth saying.
    """
    best: T | None = None
    best_match_obj = Match(0.0, "none")
    for candidate in candidates:
        m = similarity(target, key(candidate), rules=rules, synonyms_flat=synonyms_flat)
        if m.score > best_match_obj.score:
            best_match_obj = m
            best = candidate
    if best is None and candidates:
        first = candidates[0]
        return first, similarity(target, key(first), rules=rules, synonyms_flat=synonyms_flat)
    return best, best_match_obj
