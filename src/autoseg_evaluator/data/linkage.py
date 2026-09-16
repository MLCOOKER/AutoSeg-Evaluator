"""Explicit-reference linking between RTSTRUCT, image series and RTDOSE.

The metadata layer groups files into :class:`ImagingContext` objects keyed by
``FrameOfReferenceUID``. That grouping is correct for the common case but is
not sufficient on its own to say *which* CT a structure set was drawn on, or
*which* dose belongs to it: a single Frame of Reference can legitimately hold
several imaging studies, structure sets and dose distributions. Re-irradiation
and replan datasets do this routinely, and composites make it worse.

This module resolves those links from the explicit UID references DICOM
already carries, falling back through progressively weaker rules and — the
part that matters — refusing to guess when two candidates are equally good.

Resolution tiers, strongest first:

===================  =====================================================
``explicit``         A referenced UID names the target outright.
``sop-overlap``      The structure set's per-slice ``ContourImageSequence``
                     SOP UIDs are present in the series.
``for+study``        Same Frame of Reference *and* same study.
``for``              Same Frame of Reference (what v2 did, unaided).
``singleton``        Exactly one candidate exists for the whole patient.
``none``             Nothing matched.
===================  =====================================================

When more than one distinct candidate survives at the winning tier the result
is *ambiguous*: ``target`` is ``None`` and every candidate is reported so the
Load Data tab can ask the user. A user's answer is stored in
``MetadataLibrary.link_overrides`` and wins over every tier.

RTPLAN is deliberately never required nor traversed. The dose to structure set
edge is read straight off the dose object's ``ReferencedStructureSetSequence``
(including the copy some writers nest inside ``ReferencedRTPlanSequence``), so
the chain dose -> structure set -> series closes with no plan file present.

The RTSTRUCT to series traversal matches SlicerRT's
``vtkSlicerDicomRtReader::GetReferencedSeriesInstanceUID()`` tag for tag. The
one deliberate difference: SlicerRT takes ``gotoFirstItem()`` at each sequence
level, so a structure set spanning two series resolves silently to the first;
here that is surfaced as an ambiguity instead.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# ---- Tier names (ordered strongest to weakest) ----------------------------

TIER_OVERRIDE = "override"
TIER_EXPLICIT = "explicit"
TIER_SOP_OVERLAP = "sop-overlap"
TIER_FOR_STUDY = "for+study"
TIER_FOR = "for"
TIER_SINGLETON = "singleton"
TIER_NONE = "none"

#: Human-readable explanation per tier, shown in the Load Data tab.
TIER_DESCRIPTIONS = {
    TIER_OVERRIDE: "Chosen by you",
    TIER_EXPLICIT: "Referenced directly by UID in the DICOM file",
    TIER_SOP_OVERLAP: "Matched on per-slice referenced image UIDs",
    TIER_FOR_STUDY: "Same Frame of Reference and study",
    TIER_FOR: "Same Frame of Reference only",
    TIER_SINGLETON: "Only one candidate for this patient",
    TIER_NONE: "No match found",
}

#: Tiers weak enough to be worth telling the user about even when unambiguous.
WEAK_TIERS = frozenset({TIER_FOR, TIER_SINGLETON})

KIND_SERIES = "series"
KIND_DOSE = "dose"
_KIND_RTSS = "rtss"


# ---- Results --------------------------------------------------------------


@dataclass(frozen=True)
class Resolution:
    """Outcome of resolving one link.

    ``target`` is the resolved entry, or ``None`` when nothing matched or when
    the match was ambiguous — check :attr:`is_ambiguous` to tell those apart.
    ``candidates`` always lists everything that tied at the winning tier.
    """

    kind: str
    tier: str
    target: Any | None
    candidates: tuple[Any, ...]

    @property
    def is_ambiguous(self) -> bool:
        return self.target is None and len(self.candidates) > 1

    @property
    def is_resolved(self) -> bool:
        return self.target is not None

    @property
    def is_weak(self) -> bool:
        """True when resolved, but only by a rule that could collide."""
        return self.is_resolved and self.tier in WEAK_TIERS

    @property
    def description(self) -> str:
        return TIER_DESCRIPTIONS.get(self.tier, self.tier)


@dataclass(frozen=True)
class LinkIssue:
    """A link that the user needs to settle before metrics can be computed."""

    severity: str  # "error" — blocks computation; "warning" — informational
    kind: str  # KIND_SERIES | KIND_DOSE
    patient_id: str
    rtstruct_sop_uid: str
    rtstruct_label: str
    message: str
    candidates: tuple[tuple[str, str], ...] = ()
    """``(uid, human_label)`` pairs offered as choices."""


# ---- Override keys --------------------------------------------------------


def override_key(patient_id: str, rtstruct_sop_uid: str, kind: str) -> str:
    """Stable key for a user-chosen link, safe to persist in a session file."""
    return f"{patient_id}|{rtstruct_sop_uid}|{kind}"


# ---- Lookup helpers -------------------------------------------------------


def _rtstruct_entry(library, patient_id: str, rtstruct_sop_uid: str):
    patient = library.patients.get(patient_id)
    if patient is None:
        return None, None
    for ctx in patient.contexts:
        for rtss in ctx.rtstructs:
            if rtss.sop_instance_uid == rtstruct_sop_uid:
                return patient, rtss
    return patient, None


def _all_image_series(patient) -> list:
    return [s for ctx in patient.contexts for s in ctx.image_series if s.files]


def _all_doses(patient) -> list:
    return [d for ctx in patient.contexts for d in ctx.rtdoses]


def _all_rtstructs(patient) -> list:
    return [r for ctx in patient.contexts for r in ctx.rtstructs]


def series_uid_of(series) -> str:
    return series.series_instance_uid


def _overrides(library) -> Mapping[str, str]:
    return getattr(library, "link_overrides", None) or {}


def _apply_override(library, patient_id, rtstruct_sop_uid, kind, candidates, uid_of):
    chosen_uid = _overrides(library).get(override_key(patient_id, rtstruct_sop_uid, kind))
    if not chosen_uid:
        return None
    for cand in candidates:
        if uid_of(cand) == chosen_uid:
            return Resolution(kind=kind, tier=TIER_OVERRIDE, target=cand, candidates=(cand,))
    # The override names something no longer present (the folder changed since
    # the session was saved). Fall through to normal resolution rather than
    # failing outright — a stale answer should not brick a reload.
    return None


def _settle(kind: str, tier: str, candidates: list, uid_of) -> Resolution:
    """Collapse candidates at one tier into a Resolution, deduping by UID."""
    unique: dict[str, Any] = {}
    for cand in candidates:
        unique.setdefault(uid_of(cand), cand)
    items = tuple(unique.values())
    if len(items) == 1:
        return Resolution(kind=kind, tier=tier, target=items[0], candidates=items)
    return Resolution(kind=kind, tier=tier, target=None, candidates=items)


# ---- Resolution -----------------------------------------------------------


def resolve_image_series(library, patient_id: str, rtstruct_sop_uid: str) -> Resolution:
    """Resolve which image series an RTSTRUCT was contoured on."""
    patient, rtss = _rtstruct_entry(library, patient_id, rtstruct_sop_uid)
    if patient is None or rtss is None:
        return Resolution(KIND_SERIES, TIER_NONE, None, ())

    series = _all_image_series(patient)
    if not series:
        return Resolution(KIND_SERIES, TIER_NONE, None, ())

    override = _apply_override(
        library, patient_id, rtstruct_sop_uid, KIND_SERIES, series, series_uid_of
    )
    if override is not None:
        return override

    ref_series = getattr(rtss, "referenced_series_uids", None) or set()
    if ref_series:
        hits = [s for s in series if s.series_instance_uid in ref_series]
        if hits:
            return _settle(KIND_SERIES, TIER_EXPLICIT, hits, series_uid_of)

    ref_images = getattr(rtss, "referenced_image_sop_uids", None) or set()
    if ref_images:
        hits = [s for s in series if (getattr(s, "sop_instance_uids", None) or set()) & ref_images]
        if hits:
            return _settle(KIND_SERIES, TIER_SOP_OVERLAP, hits, series_uid_of)

    for_uid = rtss.frame_of_reference_uid
    for_hits = [s for s in series if for_uid and s.frame_of_reference_uid == for_uid]
    if for_hits:
        study_hits = [
            s
            for s in for_hits
            if rtss.study_instance_uid
            and getattr(s, "study_instance_uid", "") == rtss.study_instance_uid
        ]
        if study_hits:
            return _settle(KIND_SERIES, TIER_FOR_STUDY, study_hits, series_uid_of)
        return _settle(KIND_SERIES, TIER_FOR, for_hits, series_uid_of)

    if len(series) == 1:
        return _settle(KIND_SERIES, TIER_SINGLETON, series, series_uid_of)

    return Resolution(KIND_SERIES, TIER_NONE, None, ())


def resolve_dose(library, patient_id: str, rtstruct_sop_uid: str) -> Resolution:
    """Resolve which RTDOSE belongs with an RTSTRUCT.

    ``DoseSummationType == "PLAN"`` acts as a tie-breaker *within* a tier — it
    reproduces v2's preference for plan-summed dose — but it only settles a tie
    when exactly one candidate is a PLAN dose. Two PLAN doses at the same tier
    stay ambiguous, which is precisely the re-irradiation case that used to
    resolve silently to whichever file happened to be walked first.
    """
    patient, rtss = _rtstruct_entry(library, patient_id, rtstruct_sop_uid)
    if patient is None or rtss is None:
        return Resolution(KIND_DOSE, TIER_NONE, None, ())

    doses = _all_doses(patient)
    if not doses:
        return Resolution(KIND_DOSE, TIER_NONE, None, ())

    def uid_of(dose):
        return dose.sop_instance_uid

    override = _apply_override(library, patient_id, rtstruct_sop_uid, KIND_DOSE, doses, uid_of)
    if override is not None:
        return override

    def settle(tier: str, candidates: list) -> Resolution:
        res = _settle(KIND_DOSE, tier, candidates, uid_of)
        if res.is_resolved or not res.candidates:
            return res
        plan = [d for d in res.candidates if d.dose_summation_type == "PLAN"]
        if len(plan) == 1:
            return Resolution(KIND_DOSE, tier, plan[0], res.candidates)
        return res

    hits = [
        d
        for d in doses
        if rtstruct_sop_uid in (getattr(d, "referenced_structure_set_uids", None) or set())
    ]
    if hits:
        return settle(TIER_EXPLICIT, hits)

    for_uid = rtss.frame_of_reference_uid
    for_hits = [d for d in doses if for_uid and d.frame_of_reference_uid == for_uid]
    if for_hits:
        study_hits = [
            d
            for d in for_hits
            if rtss.study_instance_uid and d.study_instance_uid == rtss.study_instance_uid
        ]
        if study_hits:
            return settle(TIER_FOR_STUDY, study_hits)
        return settle(TIER_FOR, for_hits)

    if len(doses) == 1:
        return settle(TIER_SINGLETON, doses)

    return Resolution(KIND_DOSE, TIER_NONE, None, ())


# ---- Convenience wrappers -------------------------------------------------


def reference_image_folder(library, patient_id: str, rtstruct_sop_uid: str) -> str | None:
    """Folder holding the resolved image series, or ``None`` if unresolved."""
    res = resolve_image_series(library, patient_id, rtstruct_sop_uid)
    if not res.is_resolved or not res.target.files:
        return None
    return os.path.dirname(res.target.files[0])


# ---- Linkage components ---------------------------------------------------


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[Any, Any] = {}

    def add(self, item) -> None:
        self._parent.setdefault(item, item)

    def find(self, item):
        self.add(item)
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:  # path compression
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra

    def groups(self) -> dict[Any, list]:
        out: dict[Any, list] = {}
        for item in self._parent:
            out.setdefault(self.find(item), []).append(item)
        return out


def _stamp_linkage(entry, node, node_label: Mapping[Any, str]) -> None:
    """Write an entry's ``linkage_id``, falling back to its Frame of Reference."""
    label = node_label.get(node, "")
    for_uid = entry.frame_of_reference_uid
    entry.linkage_id = label or (f"for:{for_uid}" if for_uid else "")


def assign_linkage_ids(library) -> None:
    """Stamp ``linkage_id`` on every entry, from the explicit reference graph.

    A linkage is a connected component of the graph whose edges are explicit
    DICOM references (structure set -> series, dose -> structure set). One
    component is one coherent treatment context: a planning image, the
    structure sets drawn on it, and the doses computed against those.

    Entries with no explicit edges fall back to ``for:<FrameOfReferenceUID>``
    so the field is always populated and still groups sensibly on data whose
    writers omit the reference sequences. Frame of Reference is never used to
    *merge* components — doing so would re-create exactly the collision this
    module exists to prevent.
    """
    for patient_id, patient in library.patients.items():
        uf = _UnionFind()
        series = _all_image_series(patient)
        rtstructs = _all_rtstructs(patient)
        doses = _all_doses(patient)

        for entry in series:
            uf.add((KIND_SERIES, entry.series_instance_uid))
        for entry in rtstructs:
            uf.add((_KIND_RTSS, entry.sop_instance_uid))
        for entry in doses:
            uf.add((KIND_DOSE, entry.sop_instance_uid))

        strong = (TIER_OVERRIDE, TIER_EXPLICIT, TIER_SOP_OVERLAP)
        for rtss in rtstructs:
            res = resolve_image_series(library, patient_id, rtss.sop_instance_uid)
            if res.is_resolved and res.tier in strong:
                uf.union(
                    (_KIND_RTSS, rtss.sop_instance_uid),
                    (KIND_SERIES, series_uid_of(res.target)),
                )
        known_rtss = {r.sop_instance_uid for r in rtstructs}
        for dose in doses:
            for struct_uid in getattr(dose, "referenced_structure_set_uids", None) or set():
                if struct_uid in known_rtss:
                    uf.union((KIND_DOSE, dose.sop_instance_uid), (_KIND_RTSS, struct_uid))

        node_label: dict[Any, str] = {}
        for members in uf.groups().values():
            if len(members) > 1:
                digest = hashlib.sha1(
                    "|".join(sorted(f"{kind}:{uid}" for kind, uid in members)).encode("utf-8")
                ).hexdigest()[:8]
                label = f"link:{digest}"
            else:
                label = ""
            for member in members:
                node_label[member] = label

        for entry in series:
            _stamp_linkage(entry, (KIND_SERIES, entry.series_instance_uid), node_label)
        for entry in rtstructs:
            _stamp_linkage(entry, (_KIND_RTSS, entry.sop_instance_uid), node_label)
        for entry in doses:
            _stamp_linkage(entry, (KIND_DOSE, entry.sop_instance_uid), node_label)


# ---- Issue collection for the Load Data gate ------------------------------


def _series_label(series) -> str:
    count = len(series.files)
    folder = os.path.basename(os.path.dirname(series.files[0])) if series.files else ""
    plural = "s" if count != 1 else ""
    suffix = f" (folder '{folder}')" if folder else ""
    return f"{series.modality} series — {count} slice{plural}{suffix}"


def _dose_label(dose) -> str:
    kind = dose.dose_summation_type or "unspecified"
    return f"{dose.filename} — {kind} summation"


def collect_link_issues(library, *, include_dose: bool = True) -> list[LinkIssue]:
    """Every unresolved or ambiguous link in the library, for the Tab 1 gate.

    ``include_dose`` mirrors whether any DVH metric is switched on: with dose
    metrics disabled an unresolvable dose link is not worth blocking on.
    Synthetic STAPLE-consensus structure sets are skipped for the series check
    — they have no DICOM file of their own and inherit their geometry from the
    constituents that built them.
    """
    issues: list[LinkIssue] = []
    for patient_id, patient in sorted(library.patients.items()):
        for rtss in _all_rtstructs(patient):
            label = rtss.source_label or rtss.filename
            if not rtss.is_synthetic_consensus:
                res = resolve_image_series(library, patient_id, rtss.sop_instance_uid)
                if res.is_ambiguous:
                    issues.append(
                        LinkIssue(
                            severity="error",
                            kind=KIND_SERIES,
                            patient_id=patient_id,
                            rtstruct_sop_uid=rtss.sop_instance_uid,
                            rtstruct_label=label,
                            message=(
                                f"'{label}' could refer to {len(res.candidates)} image series "
                                f"({res.description.lower()}). Choose which one it was "
                                f"contoured on."
                            ),
                            candidates=tuple(
                                (series_uid_of(s), _series_label(s)) for s in res.candidates
                            ),
                        )
                    )
                elif not res.is_resolved:
                    issues.append(
                        LinkIssue(
                            severity="error",
                            kind=KIND_SERIES,
                            patient_id=patient_id,
                            rtstruct_sop_uid=rtss.sop_instance_uid,
                            rtstruct_label=label,
                            message=(
                                f"No image series found for '{label}'. Its reference CT/MR may "
                                f"not be in the loaded folder."
                            ),
                        )
                    )

            if not include_dose:
                continue
            dres = resolve_dose(library, patient_id, rtss.sop_instance_uid)
            if dres.is_ambiguous:
                issues.append(
                    LinkIssue(
                        severity="error",
                        kind=KIND_DOSE,
                        patient_id=patient_id,
                        rtstruct_sop_uid=rtss.sop_instance_uid,
                        rtstruct_label=label,
                        message=(
                            f"'{label}' could pair with {len(dres.candidates)} dose "
                            f"distributions ({dres.description.lower()}). Choose which dose "
                            f"applies."
                        ),
                        candidates=tuple(
                            (d.sop_instance_uid, _dose_label(d)) for d in dres.candidates
                        ),
                    )
                )
    return issues


__all__ = [
    "KIND_DOSE",
    "KIND_SERIES",
    "LinkIssue",
    "Resolution",
    "TIER_DESCRIPTIONS",
    "TIER_EXPLICIT",
    "TIER_FOR",
    "TIER_FOR_STUDY",
    "TIER_NONE",
    "TIER_OVERRIDE",
    "TIER_SINGLETON",
    "TIER_SOP_OVERLAP",
    "WEAK_TIERS",
    "assign_linkage_ids",
    "collect_link_issues",
    "override_key",
    "reference_image_folder",
    "resolve_dose",
    "resolve_image_series",
    "series_uid_of",
]
