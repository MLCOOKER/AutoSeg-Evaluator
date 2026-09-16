"""Build the canonical organ assignment for every ROI name in a library.

Bridges :mod:`autoseg_evaluator.core.organ_groups` — which knows how to place
one name — to a loaded :class:`~autoseg_evaluator.data.metadata.MetadataLibrary`,
which holds thousands of them across every producer.

Two things happen here that cannot happen one name at a time:

*Type reconciliation.* ``RTROIInterpretedType`` is a property of an ROI in a
particular file, not of a name. Real cohorts disagree with themselves — nodal
levels come back ORGAN from one vendor and CTV from another for an identical
name — so the counts are pooled per name, the dominant value wins, and the
disagreement is recorded rather than quietly resolved to whichever file was
read first.

*Frequency.* How often a name occurs decides which spelling seeds a fuzzy
proposal and the order the review UI puts things in. On a real cohort the
hundred most common unresolved names cover about two thirds of all unresolved
contours, so ranking by frequency is the difference between a review that can
be finished and one that cannot.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from autoseg_evaluator.core.matching import ReplacementRule
from autoseg_evaluator.core.organ_groups import (
    QUALIFIER_OAR,
    TIER_UNASSIGNED,
    FuzzyProposal,
    OrganAssignment,
    OrganKey,
    OrganRules,
    assign,
    dominant_type,
    group_by_key,
    propose_fuzzy_groups,
)


@dataclass
class OrganIndex:
    """Every ROI name in a library, placed into a canonical organ group."""

    assignments: dict[str, OrganAssignment] = field(default_factory=dict)
    frequencies: Counter[str] = field(default_factory=Counter)
    ground_truth_names: set[str] = field(default_factory=set)
    """Names seen on a structure set acting as ground truth for some drawer."""

    def get(self, roi_name: str) -> OrganAssignment | None:
        return self.assignments.get(roi_name)

    def key_for(self, roi_name: str) -> OrganKey | None:
        found = self.assignments.get(roi_name)
        return found.key if found is not None else None

    def groups(self, *, pool_laterality: bool = False) -> dict[OrganKey, list[OrganAssignment]]:
        return group_by_key(self.assignments.values(), pool_laterality=pool_laterality)

    def needing_review(self, *, oar_only: bool = True) -> list[OrganAssignment]:
        """Unassigned names, most frequent first — the review worklist.

        ``oar_only`` drops targets, support structures and dose levels, which
        are classified but are rarely what a per-organ analysis pools.
        """
        out = [
            a
            for a in self.assignments.values()
            if a.tier == TIER_UNASSIGNED and (not oar_only or a.key.qualifier == QUALIFIER_OAR)
        ]
        out.sort(key=lambda a: (-self.frequencies.get(a.roi_name, 0), a.roi_name))
        return out

    def proposals(
        self,
        *,
        synonyms_flat: Mapping[str, str] | None = None,
        replacement_rules: Iterable[ReplacementRule] = (),
        rules: OrganRules | None = None,
        oar_only: bool = True,
    ) -> list[FuzzyProposal]:
        return propose_fuzzy_groups(
            self.needing_review(oar_only=oar_only),
            synonyms_flat=synonyms_flat,
            replacement_rules=replacement_rules,
            rules=rules,
            frequencies=self.frequencies,
        )

    def summary(self) -> dict[str, Any]:
        """Counts for the Load Data / review headline."""
        tiers = Counter(a.tier for a in self.assignments.values())
        qualifiers = Counter(a.key.qualifier for a in self.assignments.values())
        return {
            "names": len(self.assignments),
            "groups": len(self.groups()),
            "tiers": dict(tiers),
            "qualifiers": dict(qualifiers),
            "conflicts": sum(1 for a in self.assignments.values() if a.type_conflict),
        }


def collect_roi_names(library) -> tuple[dict[str, Counter], Counter[str]]:
    """Gather ``{roi_name: Counter(interpreted_type)}`` and occurrence counts.

    Walks every structure set of every patient, including synthetic STAPLE
    consensus entries — a consensus organ still belongs in a group.
    """
    types: dict[str, Counter] = defaultdict(Counter)
    freq: Counter[str] = Counter()
    for patient in getattr(library, "patients", {}).values():
        for ctx in patient.contexts:
            for rtss in ctx.rtstructs:
                for organ in rtss.organs:
                    name = organ.roi_name
                    if not name:
                        continue
                    types[name][organ.interpreted_type or "(blank)"] += 1
                    freq[name] += 1
    return dict(types), freq


def build_organ_index(
    library,
    *,
    synonyms_flat: Mapping[str, str] | None = None,
    replacement_rules: Iterable[ReplacementRule] = (),
    rules: OrganRules | None = None,
    manual: Mapping[str, str] | None = None,
) -> OrganIndex:
    """Place every ROI name in ``library`` into a canonical organ group.

    ``manual`` carries the user's confirmed answers (raw ROI name to chosen
    base) and outranks every automatic tier, so re-running this after a review
    keeps those decisions.
    """
    types, freq = collect_roi_names(library)
    assignments: dict[str, OrganAssignment] = {}
    for name, counter in types.items():
        interpreted, conflicted = dominant_type(counter)
        placed = assign(
            name,
            interpreted,
            synonyms_flat=synonyms_flat,
            replacement_rules=replacement_rules,
            rules=rules,
            manual=manual,
        )
        if conflicted:
            placed = OrganAssignment(
                roi_name=placed.roi_name,
                key=placed.key,
                tier=placed.tier,
                removed=placed.removed,
                interpreted_type=placed.interpreted_type,
                type_conflict=True,
            )
        assignments[name] = placed

    return OrganIndex(assignments=assignments, frequencies=freq)


def drawer_consensus(
    index: OrganIndex, gt_roi_names: Iterable[str]
) -> tuple[OrganKey | None, bool]:
    """The organ a drawer represents, from the GT names it actually contains.

    Returns ``(key, disagreed)``. A drawer normally holds one organ spelled
    several ways across patients, and those spellings resolve to one key. When
    they do not, that is worth surfacing: a drawer holding ``Parotid_L`` for
    one patient and ``Parotid_R`` for another is a matching error the app has
    no other way to detect.
    """
    keys = Counter()
    for name in gt_roi_names:
        found = index.assignments.get(name)
        if found is not None:
            keys[found.key] += 1
    if not keys:
        return None, False
    ordered = keys.most_common()
    return ordered[0][0], len(ordered) > 1


__all__ = [
    "OrganIndex",
    "build_organ_index",
    "collect_roi_names",
    "drawer_consensus",
]
