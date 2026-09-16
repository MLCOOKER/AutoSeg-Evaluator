"""Canonical organ grouping — collapse ROI name spellings into one organ.

Ground-truth contours arrive named however whoever drew them chose to name
them. ``OpticNerve_L``, ``Left optic nerve`` and ``optic nerve lt`` are one
organ; ``OpticNerve_L`` and ``OpticNerve_R`` are two. Downstream statistics
need the first collapsed and the second kept apart, and the difference is not
recoverable from string similarity — measured on this project's own matcher,
left/right pairs score 0.82–0.93 while genuinely different organs score
0.38–0.45. There is no threshold that separates them.

So grouping is done on a **structured key**, and only ``base`` is ever
compared fuzzily::

    OrganKey(base="opticnrv", laterality="L", qualifier="oar")

``laterality`` is extracted, never inferred, which makes a left/right merge
structurally impossible rather than threshold-dependent. Grouping by ``base``
alone pools the two sides when that is what an analysis wants; grouping by the
full key keeps them separate.

**Assignment tiers**, strongest first:

===============  =========================================================
``dictionary``   The name resolves through the TG-263 synonym dictionary.
``stripped``     It resolves once vendor decorations are removed.
``fuzzy``        Clustered with other unresolved names of identical
                 laterality and qualifier. **A proposal only** — never
                 applied without confirmation.
``manual``       Assigned by the user; outranks everything.
``unassigned``   Stands alone as its own group of one.
===============  =========================================================

An unassigned name is not an error. It forms a group by itself, statistics
still compute over it, and the only thing lost is pooling with other spellings
of the same organ. That is what keeps the review step optional rather than a
gate: effort is proportional to how much pooling is wanted.

``base`` is never invented. It is either a TG-263 canonical or a verbatim
normalised string taken from the data.

Decorations vs barriers
-----------------------
A **decoration** never changes which physical structure is meant, so removing
it is safe: ``_Experimental`` (a vendor's model marker), ``_MR`` (the modality
it was drawn on), ``_F`` (a sex-specific template), a trailing parenthetical
note, ``(1)`` copy suffixes, ``_v2`` versions.

A **barrier** changes the entity and is extracted, never stripped: laterality,
PRV, target class, and boolean composites.

The asymmetry matters. Over-splitting leaves two groups a user can merge in
one action; over-merging silently pools distinct structures into a statistic
nobody can tell is wrong. So anything not recognised as a decoration stays in
the base, and the failure mode is always the recoverable one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from autoseg_evaluator.core.matching import (
    ReplacementRule,
    canonicalise_with_meta,
    similarity,
)

# ---- Tiers ----------------------------------------------------------------

TIER_MANUAL = "manual"
TIER_DICTIONARY = "dictionary"
TIER_STRIPPED = "stripped"
TIER_FUZZY = "fuzzy"
TIER_UNASSIGNED = "unassigned"

#: Tiers safe to apply without asking. ``fuzzy`` is deliberately absent.
AUTOMATIC_TIERS = frozenset({TIER_MANUAL, TIER_DICTIONARY, TIER_STRIPPED})

TIER_DESCRIPTIONS = {
    TIER_MANUAL: "Assigned by you",
    TIER_DICTIONARY: "Resolved through the TG-263 dictionary",
    TIER_STRIPPED: "Resolved after removing vendor decorations",
    TIER_FUZZY: "Proposed by name similarity — needs confirmation",
    TIER_UNASSIGNED: "Stands alone",
}

# ---- Laterality -----------------------------------------------------------

LATERALITY_NONE = ""
LATERALITY_L = "L"
LATERALITY_R = "R"

_LEFT_TOKENS = frozenset({"l", "lt", "left"})
_RIGHT_TOKENS = frozenset({"r", "rt", "right"})

# ---- Qualifiers -----------------------------------------------------------

QUALIFIER_OAR = "oar"
QUALIFIER_TARGET = "target"
QUALIFIER_PRV = "prv"
QUALIFIER_NONANATOMIC = "nonanatomic"
QUALIFIER_COMPOSITE = "composite"
QUALIFIER_DOSE = "dose"

QUALIFIER_DESCRIPTIONS = {
    QUALIFIER_OAR: "Organ at risk",
    QUALIFIER_TARGET: "Target volume",
    QUALIFIER_PRV: "Planning risk volume",
    QUALIFIER_NONANATOMIC: "Non-anatomical (external, support, marker, control)",
    QUALIFIER_COMPOSITE: "Composite of other structures",
    QUALIFIER_DOSE: "Dose-level structure",
}

#: ``RTROIInterpretedType`` values that mean "this is a candidate organ".
#: AVOIDANCE is included deliberately: it is a statement of planning intent,
#: not of anatomy — clinical structure sets routinely type Kidney, Liver and
#: Skeleton as AVOIDANCE rather than ORGAN.
OAR_TYPES = frozenset({"ORGAN", "AVOIDANCE", "CAVITY"})

TARGET_TYPES = frozenset({"GTV", "CTV", "PTV", "ITV", "TREATED_VOLUME", "IRRAD_VOLUME"})

NONANATOMIC_TYPES = frozenset(
    {"EXTERNAL", "SUPPORT", "CONTROL", "MARKER", "BOLUS", "FIXATION", "REGISTRATION", "ISOCENTER"}
)

# ---- Default rules (overridable from resources/organ_rules.json) ----------

#: Affixes that never change which structure is meant. Applied in order, and
#: every removal is recorded on the assignment so the review UI can show its
#: working. Anchored to token boundaries so ``Brain_MR`` loses its suffix while
#: ``MR_Brain_Something`` and ``Femur`` are untouched.
DEFAULT_DECORATIONS: tuple[tuple[str, str], ...] = (
    (r"[_\s-]*experimental\b", "vendor model marker"),
    # A trailing parenthetical is usually an annotation — ``(BrachialPlex_proxy)``,
    # ``(1)``, ``(StJude)`` — and safe to drop. Two kinds are not.
    #
    # One naming a target volume marks a derived structure: ``Kidney_L(PTV)``
    # is the kidney cropped to the PTV, not the kidney.
    #
    # One holding a side *is* the laterality: real data carries
    # ``Mammary tissue(Lt)`` and ``Mammary tissue(Rt)``, and stripping those
    # collapses the left and the right breast into one organ — precisely the
    # merge this module exists to make impossible.
    (
        r"\s*\((?![^)]*(?i:gtv|ctv|ptv|itv))(?!\s*(?i:l|r|lt|rt|left|right)\s*\))[^)]*\)\s*$",
        "trailing parenthetical",
    ),
    (r"[_\s-]+MR\b", "contoured on MR"),
    (r"[_\s-]+F\b", "sex-specific template"),
    (r"[_\s-]+v\d+\s*$", "version suffix"),
    (r"[_\s-]+(final|new|old|copy)\s*$", "workflow suffix"),
)

#: Name patterns that classify a structure regardless of its interpreted type.
#: These are *barriers* — facts about the entity that the DICOM type does not
#: record. Checked before the type-derived qualifier.
DEFAULT_PRV_PATTERN = r"\bprv\b|prv$"
#: Lookarounds rather than ``\b``: a digit is a word character, so ``\b``
#: never fires between the ``1`` and the ``PTV`` of ``1PTV_``, which is how
#: numbered targets are routinely named.
DEFAULT_TARGET_PATTERN = r"(?<![a-z])(gtv|ctv|ptv|itv)(?![a-z])"
DEFAULT_DOSE_PATTERN = r"^\s*\d+(\.\d+)?\s*gy\b|\bdose\b\s*\d|\b\d+(\.\d+)?\s*gy\s*\("
#: Structures derived from others, by a boolean operation (``Heart+A_Pulm``,
#: ``Body-PTV``) or by a margin (``BODY -1.0``, ``GTV+10mm``). Either way the
#: result is not comparable with the structure it was built from, so it gets
#: its own qualifier.
#:
#: Detection is heuristic — DICOM has no tag marking a derived structure;
#: ``ROIGenerationAlgorithm`` records how a contour was drawn, not what it was
#: built from, and ``ROIObservationDescription`` carries display properties —
#: so a composite is always *flagged*, never acted on silently. The hyphen rule
#: wants letters either side so a leading minus sign does not catch ordinary
#: anatomy; vertebral ranges like ``L4-L5`` are a known false positive, which
#: is part of why this is a proposal rather than a decision.
DEFAULT_COMPOSITE_PATTERN = r"\+|[A-Za-z0-9]\s*-\s*[A-Za-z]|[A-Za-z]\s*[-+]\s*\d"

DEFAULT_FUZZY_THRESHOLD = 0.88


@dataclass(frozen=True)
class OrganRules:
    """The editable rule set. Load from JSON with :func:`load_rules`."""

    decorations: tuple[tuple[str, str], ...] = DEFAULT_DECORATIONS
    prv_pattern: str = DEFAULT_PRV_PATTERN
    target_pattern: str = DEFAULT_TARGET_PATTERN
    dose_pattern: str = DEFAULT_DOSE_PATTERN
    composite_pattern: str = DEFAULT_COMPOSITE_PATTERN
    fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD


def load_rules(path: Path | str | None) -> OrganRules:
    """Load rules from JSON, falling back to the built-in defaults.

    A malformed or missing file yields the defaults rather than raising —
    these rules are a heuristic aid, and a bad edit should not stop the app
    from starting.
    """
    if path is None:
        return OrganRules()
    p = Path(path)
    if not p.exists():
        return OrganRules()
    try:
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return OrganRules()
    if not isinstance(raw, dict):
        return OrganRules()

    decorations = DEFAULT_DECORATIONS
    entries = raw.get("decorations")
    if isinstance(entries, list):
        collected = []
        for item in entries:
            if isinstance(item, dict) and item.get("pattern"):
                collected.append((str(item["pattern"]), str(item.get("reason", ""))))
            elif isinstance(item, str):
                collected.append((item, ""))
        if collected:
            decorations = tuple(collected)

    def _pattern(key: str, default: str) -> str:
        value = raw.get(key)
        return str(value) if isinstance(value, str) and value else default

    try:
        threshold = float(raw.get("fuzzy_threshold", DEFAULT_FUZZY_THRESHOLD))
    except (TypeError, ValueError):
        threshold = DEFAULT_FUZZY_THRESHOLD

    return OrganRules(
        decorations=decorations,
        prv_pattern=_pattern("prv_pattern", DEFAULT_PRV_PATTERN),
        target_pattern=_pattern("target_pattern", DEFAULT_TARGET_PATTERN),
        dose_pattern=_pattern("dose_pattern", DEFAULT_DOSE_PATTERN),
        composite_pattern=_pattern("composite_pattern", DEFAULT_COMPOSITE_PATTERN),
        fuzzy_threshold=threshold,
    )


# ---- The key --------------------------------------------------------------


@dataclass(frozen=True, order=True)
class OrganKey:
    """What makes two contours "the same organ" for statistical purposes."""

    base: str
    laterality: str = LATERALITY_NONE
    qualifier: str = QUALIFIER_OAR

    @property
    def pooled(self) -> OrganKey:
        """The same organ with laterality dropped — pools left and right."""
        return OrganKey(self.base, LATERALITY_NONE, self.qualifier)

    def label(self) -> str:
        """Human-readable name, e.g. ``Opticnrv (L)``."""
        stem = self.base.replace("_", " ").strip().title() if self.base else "(unnamed)"
        if self.laterality:
            stem = f"{stem} ({self.laterality})"
        if self.qualifier != QUALIFIER_OAR:
            stem = f"{stem} [{self.qualifier}]"
        return stem

    def __str__(self) -> str:  # stable, usable as a column value
        parts = [self.base or "?"]
        if self.laterality:
            parts.append(self.laterality)
        if self.qualifier != QUALIFIER_OAR:
            parts.append(self.qualifier)
        return "|".join(parts)


@dataclass(frozen=True)
class OrganAssignment:
    """One ROI name's place in the grouping, and how it got there."""

    roi_name: str
    key: OrganKey
    tier: str
    removed: tuple[str, ...] = ()
    """Decorations taken off, as ``"text (reason)"`` — shown in the review UI."""
    interpreted_type: str = ""
    type_conflict: bool = False
    """True when this name carried different RTROIInterpretedTypes across files."""

    @property
    def is_automatic(self) -> bool:
        return self.tier in AUTOMATIC_TIERS

    @property
    def needs_review(self) -> bool:
        return self.tier in (TIER_FUZZY, TIER_UNASSIGNED)

    @property
    def description(self) -> str:
        return TIER_DESCRIPTIONS.get(self.tier, self.tier)


# ---- Decomposition --------------------------------------------------------


def _tokens(name: str) -> list[str]:
    """Split a name into comparable word tokens.

    Punctuation is discarded rather than kept on the token: clinical names
    like ``Level IVb Left: Medial supraclavicular group`` attach a colon to
    the very word that carries the laterality, and a token of ``left:`` would
    not match anything.
    """
    parts = re.split(r"[\s_\-./\\,;:()\[\]]+", name.strip().lower())
    return [t for t in (p.strip(".,;:!?") for p in parts) if t]


def strip_decorations(name: str, rules: OrganRules | None = None) -> tuple[str, tuple[str, ...]]:
    """Remove decorations; return ``(clean_name, removed_descriptions)``.

    Applied repeatedly until stable, so ``Eye_L_Experimental (1)`` loses both.
    """
    rules = rules or OrganRules()
    out = name
    removed: list[str] = []
    for _pass in range(4):  # bounded: a handful of stacked decorations at most
        changed = False
        for pattern, reason in rules.decorations:
            match = re.search(pattern, out, flags=re.IGNORECASE)
            if not match or not match.group(0).strip(" _-"):
                continue
            removed.append(f"{match.group(0).strip()} ({reason})" if reason else match.group(0))
            out = (out[: match.start()] + out[match.end() :]).strip(" _-")
            changed = True
        if not changed:
            break
    return out.strip(" _-"), tuple(removed)


#: Unambiguous laterality words, recognised anywhere in a name. Clinical
#: nomenclature buries them mid-string — ``Level IVb Left: Medial
#: supraclavicular group`` — and a first-or-last-token rule silently misses
#: those, which is how a left structure and a right one end up in one group.
_LATERAL_WORDS = {"left": LATERALITY_L, "right": LATERALITY_R}

#: Abbreviations, accepted only at the start or end of a name. Mid-name they
#: are too ambiguous to trust: ``rt`` reads as "right" in ``Parotid_Rt`` and as
#: "radiotherapy" in ``Bones_RT_Plan``.
_LATERAL_ABBREV = {
    "l": LATERALITY_L,
    "lt": LATERALITY_L,
    "r": LATERALITY_R,
    "rt": LATERALITY_R,
}


def extract_laterality(name: str) -> tuple[str, str]:
    """Split a laterality token off a name.

    Returns ``(name_without_laterality, "L" | "R" | "")``. Whole tokens only,
    so ``LAD_Coronary`` and ``Rectum`` keep their initial letters while
    ``Parotid_L``, ``Left optic nerve``, ``optic nerve lt`` and
    ``Level IVb Left: Medial supraclavicular group`` all give up their side.
    """
    tokens = _tokens(name)
    if len(tokens) < 2:
        return name, LATERALITY_NONE

    for index, token in enumerate(tokens):
        side = _LATERAL_WORDS.get(token)
        if side is None and index in (0, len(tokens) - 1):
            side = _LATERAL_ABBREV.get(token)
        if side is not None:
            rest = tokens[:index] + tokens[index + 1 :]
            return " ".join(rest), side
    return name, LATERALITY_NONE


def classify_qualifier(
    name: str,
    interpreted_type: str = "",
    rules: OrganRules | None = None,
) -> str:
    """Decide what kind of structure this is.

    ``RTROIInterpretedType`` leads where it is populated — it is the only
    non-guessed signal available, and on a real multi-vendor cohort it removes
    roughly 40% of names from organ consideration outright. But it cannot
    express PRV, composite or dose-level structures, so those are detected
    from the name and take precedence over the type-derived answer.

    Nothing here filters anything out. Target and non-anatomical structures are
    classified, not discarded — whether they enter a given analysis is a
    decision for the statistics layer, where it can be changed without
    recomputing.
    """
    rules = rules or OrganRules()
    low = name.strip().lower()
    itype = (interpreted_type or "").strip().upper()

    # Name-level facts the DICOM type cannot record.
    if re.search(rules.dose_pattern, low, flags=re.IGNORECASE):
        return QUALIFIER_DOSE
    if re.search(rules.prv_pattern, low, flags=re.IGNORECASE):
        return QUALIFIER_PRV
    if re.search(rules.composite_pattern, name):
        return QUALIFIER_COMPOSITE

    if itype in TARGET_TYPES:
        return QUALIFIER_TARGET
    if itype in NONANATOMIC_TYPES:
        return QUALIFIER_NONANATOMIC
    if itype in OAR_TYPES:
        return QUALIFIER_OAR

    # Type absent or unrecognised — fall back to the name.
    if re.search(rules.target_pattern, low, flags=re.IGNORECASE):
        return QUALIFIER_TARGET
    return QUALIFIER_OAR


# ---- Assignment -----------------------------------------------------------


def _split_canonical(canonical: str) -> tuple[str, str]:
    """Split laterality off a TG-263 canonical, which uses a strict suffix."""
    base, lat = extract_laterality(canonical)
    return base.replace(" ", "_") if lat else canonical, lat


def _laterality_of(*candidates: str) -> str:
    """First laterality found across several spellings of one name.

    Decoration stripping can remove the very token that carries the side, so
    the raw name is always consulted as well as the cleaned one. Losing a side
    silently is far worse than keeping a decoration.
    """
    for candidate in candidates:
        if not candidate:
            continue
        _stem, lat = extract_laterality(candidate)
        if lat:
            return lat
    return LATERALITY_NONE


def assign(
    roi_name: str,
    interpreted_type: str = "",
    *,
    synonyms_flat: Mapping[str, str] | None = None,
    replacement_rules: Iterable[ReplacementRule] = (),
    rules: OrganRules | None = None,
    manual: Mapping[str, str] | None = None,
) -> OrganAssignment:
    """Place one ROI name, trying each automatic tier in turn.

    ``manual`` maps a raw ROI name to a user-chosen base and outranks every
    automatic tier — that is where the review dialog's answers come back in.
    """
    rules = rules or OrganRules()
    qualifier = classify_qualifier(roi_name, interpreted_type, rules)

    chosen = (manual or {}).get(roi_name)
    if chosen:
        base, lat = extract_laterality(chosen)
        if not lat:
            _stem, lat = extract_laterality(roi_name)
            base = chosen
        return OrganAssignment(
            roi_name=roi_name,
            key=OrganKey(_slug(base), lat, qualifier),
            tier=TIER_MANUAL,
            interpreted_type=interpreted_type,
        )

    # Tier 1 — straight dictionary hit. Laterality is taken from the canonical
    # rather than the raw name: TG-263 spells it consistently, the source does
    # not necessarily.
    canonical, resolved = canonicalise_with_meta(
        roi_name, replacement_rules, dict(synonyms_flat or {})
    )
    if resolved:
        base, lat = _split_canonical(canonical)
        return OrganAssignment(
            roi_name=roi_name,
            key=OrganKey(_slug(base), lat or _laterality_of(roi_name), qualifier),
            tier=TIER_DICTIONARY,
            interpreted_type=interpreted_type,
        )

    # Tier 2 — same lookup once decorations are gone.
    clean, removed = strip_decorations(roi_name, rules)
    if clean and clean != roi_name:
        canonical, resolved = canonicalise_with_meta(
            clean, replacement_rules, dict(synonyms_flat or {})
        )
        if resolved:
            base, lat = _split_canonical(canonical)
            return OrganAssignment(
                roi_name=roi_name,
                key=OrganKey(_slug(base), lat or _laterality_of(roi_name), qualifier),
                tier=TIER_STRIPPED,
                removed=removed,
                interpreted_type=interpreted_type,
            )

    # Unresolved — stands alone, but still decomposed so that laterality and
    # qualifier are correct and a later fuzzy pass can only ever merge it with
    # something sharing both.
    stem, _lat = extract_laterality(clean or roi_name)
    lat = _laterality_of(clean or roi_name, roi_name)
    return OrganAssignment(
        roi_name=roi_name,
        key=OrganKey(_slug(stem), lat, qualifier),
        tier=TIER_UNASSIGNED,
        removed=removed,
        interpreted_type=interpreted_type,
    )


def _slug(text: str) -> str:
    return re.sub(r"[\s_\-.]+", "_", (text or "").strip().lower()).strip("_")


# ---- Fuzzy proposals ------------------------------------------------------


#: A token is an *index* if it numbers or enumerates a structure rather than
#: naming it: ``1`` in ``Rib Left 1``, ``IVb`` in ``Level IVb``, ``L4`` in
#: ``LN_L4_R``. Roman numerals are matched loosely and a few ordinary words
#: (``mid``, ``lid``) will be caught by that — which only ever prevents a
#: merge, and an unwanted split is the recoverable failure.
_INDEX_TOKEN = re.compile(r"^(?:\d+[a-z]?|[a-z]?\d+|[ivxlcdm]{1,4}[ab]?)$", re.IGNORECASE)


#: Anatomical position and direction words. Like laterality, these sit a few
#: characters apart but mean opposite things — ``Aorte_Thx_Asc`` against
#: ``Aorte_Thx_Desc`` scores high on any string metric while naming different
#: segments of the aorta. Each entry maps a spelling to the axis it belongs to,
#: so ``asc``/``ascending`` count as the same modifier and ``asc``/``desc`` do
#: not.
_POSITIONAL_TOKENS = {
    "sup": "sup",
    "superior": "sup",
    "upper": "sup",
    "cranial": "sup",
    "inf": "inf",
    "inferior": "inf",
    "lower": "inf",
    "caudal": "inf",
    "ant": "ant",
    "anterior": "ant",
    "post": "post",
    "posterior": "post",
    "med": "med",
    "medial": "med",
    "middle": "mid",
    "mid": "mid",
    "lateral": "lateral",
    "prox": "prox",
    "proximal": "prox",
    "dist": "dist",
    "distal": "dist",
    "asc": "asc",
    "ascending": "asc",
    "desc": "desc",
    "dsc": "desc",
    "descending": "desc",
    "int": "int",
    "internal": "int",
    "ext": "ext",
    "external": "ext",
    "contra": "contra",
    "contralateral": "contra",
    "ipsi": "ipsi",
    "ipsilateral": "ipsi",
}


def positional_signature(name: str) -> tuple[str, ...]:
    """The position/direction modifiers in a name, sorted.

    A second barrier for the fuzzy tier, on the same reasoning as laterality:
    string similarity cannot distinguish ``Lung Lobe Left Lower`` from ``Lung
    Lobe Left Upper``, or ``LN External Iliac`` from ``LN Internal Iliac``, so
    the difference is taken out of the similarity score's hands.
    """
    return tuple(sorted({_POSITIONAL_TOKENS[t] for t in _tokens(name) if t in _POSITIONAL_TOKENS}))


def index_signature(name: str) -> tuple[str, ...]:
    """The enumerating tokens in a name, sorted.

    Two names may only be proposed as one organ if these agree. Without it,
    character similarity happily merges all twelve of ``Rib Left 1`` through
    ``Rib Left 12`` into a single structure, and ``LN_Neck_IX_L`` with
    ``LN_Neck_XA_L`` — names that differ by exactly the thing that
    distinguishes them.
    """
    # Laterality tokens are excluded: "l" is also the Roman numeral 50, so
    # without this ``Parotid_L`` would carry an index of ("l",) while
    # ``Left Parotid`` carried none, and the two would never be proposed as
    # one organ.
    return tuple(
        sorted(
            t
            for t in _tokens(name)
            if _INDEX_TOKEN.match(t) and t not in _LATERAL_ABBREV and t not in _LATERAL_WORDS
        )
    )


#: Tokens this short are codes rather than words, so a one-character
#: difference between them changes the structure instead of misspelling it.
_SHORT_CODE_LENGTH = 3


def differs_by_a_short_code(a: str, b: str) -> bool:
    """True when two names differ in exactly one token and that token is a code.

    ``UJ_Front_L`` and ``LJ_Front_L`` are the upper and the lower jaw;
    ``A_Aorta`` and ``V_Aorta`` are an artery and a vein. Each pair differs by
    a single character inside a two-letter token, which any string metric reads
    as near-identical. The same single-character difference inside a long word
    — ``Artefact`` against ``Artifact``, ``Humeral_Head`` against
    ``Humerus_Head`` — really is a spelling variant, which is what the fuzzy
    tier exists to catch. Length is what separates the two cases.
    """
    ta, tb = _tokens(a), _tokens(b)
    if len(ta) != len(tb):
        return False
    differing = [(x, y) for x, y in zip(ta, tb, strict=False) if x != y]
    if len(differing) != 1:
        return False
    x, y = differing[0]
    return min(len(x), len(y)) <= _SHORT_CODE_LENGTH


@dataclass
class FuzzyProposal:
    """A suggested merge of unresolved names. Never applied automatically."""

    key: OrganKey
    members: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.members)


def propose_fuzzy_groups(
    assignments: Sequence[OrganAssignment],
    *,
    synonyms_flat: Mapping[str, str] | None = None,
    replacement_rules: Iterable[ReplacementRule] = (),
    rules: OrganRules | None = None,
    frequencies: Mapping[str, int] | None = None,
) -> list[FuzzyProposal]:
    """Cluster unassigned names, strictly within one laterality and qualifier.

    Comparison happens only between names that already agree on both barrier
    axes, so no threshold — however loose — can put a left organ and a right
    organ in the same proposal. Seeds are taken in descending frequency so the
    surviving label is the spelling that actually dominates the cohort.

    Returns proposals, not decisions. Nothing here mutates an assignment.
    """
    rules = rules or OrganRules()
    freq = frequencies or {}
    syn = dict(synonyms_flat or {})

    # Bucketing on the barrier axes *plus* the index signature means the
    # similarity score is only ever consulted between names that already agree
    # on side, kind and enumeration. No threshold, however loose, can cross
    # any of them.
    buckets: dict[tuple[str, str, tuple[tuple[str, ...], ...]], list[OrganAssignment]] = {}
    for item in assignments:
        if item.tier != TIER_UNASSIGNED:
            continue
        signature = (
            index_signature(item.roi_name),
            positional_signature(item.roi_name),
        )
        buckets.setdefault((item.key.laterality, item.key.qualifier, signature), []).append(item)

    proposals: list[FuzzyProposal] = []
    for (lat, qual, _signature), members in sorted(buckets.items()):
        ordered = sorted(members, key=lambda a: (-freq.get(a.roi_name, 0), a.roi_name))
        seeds: list[tuple[OrganAssignment, FuzzyProposal]] = []
        for item in ordered:
            placed = False
            for seed, proposal in seeds:
                score = similarity(
                    item.roi_name,
                    seed.roi_name,
                    rules=replacement_rules,
                    synonyms_flat=syn,
                ).score
                if score >= rules.fuzzy_threshold and not differs_by_a_short_code(
                    item.roi_name, seed.roi_name
                ):
                    proposal.members.append(item.roi_name)
                    proposal.scores[item.roi_name] = float(score)
                    placed = True
                    break
            if not placed:
                proposal = FuzzyProposal(
                    key=OrganKey(item.key.base, lat, qual),
                    members=[item.roi_name],
                    scores={item.roi_name: 1.0},
                )
                seeds.append((item, proposal))
                proposals.append(proposal)

    return [p for p in proposals if p.size > 1]


# ---- Grouping -------------------------------------------------------------


def group_by_key(
    assignments: Iterable[OrganAssignment], *, pool_laterality: bool = False
) -> dict[OrganKey, list[OrganAssignment]]:
    """Collect assignments into their organ groups.

    ``pool_laterality`` merges left and right into one group — the analysis
    that asks "how did the parotids do" rather than "how did each side do".
    Available precisely because laterality is a field on the key rather than
    part of a matched string.
    """
    out: dict[OrganKey, list[OrganAssignment]] = {}
    for item in assignments:
        key = item.key.pooled if pool_laterality else item.key
        out.setdefault(key, []).append(item)
    return out


def dominant_type(types: Mapping[str, int]) -> tuple[str, bool]:
    """Reduce per-name ``RTROIInterpretedType`` counts to one value.

    Returns ``(type, conflicted)``. Real cohorts disagree with themselves —
    nodal levels get typed ORGAN by one vendor and CTV by another for the same
    name — so the conflict is reported rather than hidden, and the review UI
    can surface it instead of silently taking whichever file was read first.
    """
    real = {t: n for t, n in types.items() if t and t != "(blank)"}
    if not real:
        return "", False
    ordered = sorted(real.items(), key=lambda kv: (-kv[1], kv[0]))
    return ordered[0][0], len(real) > 1


__all__ = [
    "AUTOMATIC_TIERS",
    "DEFAULT_DECORATIONS",
    "LATERALITY_L",
    "LATERALITY_NONE",
    "LATERALITY_R",
    "NONANATOMIC_TYPES",
    "OAR_TYPES",
    "QUALIFIER_COMPOSITE",
    "QUALIFIER_DESCRIPTIONS",
    "QUALIFIER_DOSE",
    "QUALIFIER_NONANATOMIC",
    "QUALIFIER_OAR",
    "QUALIFIER_PRV",
    "QUALIFIER_TARGET",
    "TARGET_TYPES",
    "TIER_DESCRIPTIONS",
    "TIER_DICTIONARY",
    "TIER_FUZZY",
    "TIER_MANUAL",
    "TIER_STRIPPED",
    "TIER_UNASSIGNED",
    "FuzzyProposal",
    "OrganAssignment",
    "OrganKey",
    "OrganRules",
    "assign",
    "classify_qualifier",
    "differs_by_a_short_code",
    "dominant_type",
    "extract_laterality",
    "group_by_key",
    "index_signature",
    "positional_signature",
    "load_rules",
    "propose_fuzzy_groups",
    "strip_decorations",
]
