"""Qualitative (Likert) assessment — scale definition and rating-stack builder.

This is the UI-free core of the qualitative-assessment workflow (Tab 4). It
defines the 5-point MD Anderson Likert scale and turns the matched-drawer
snapshot (``MatchContoursTab.session_state()``) into an ordered list of
individual contours to rate — the "stack".

Kept free of Qt / SimpleITK so the stack logic can be unit-tested directly.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LikertPoint:
    """One point on the rating scale."""

    value: int
    label: str
    explanation: str


# The five-point scale as deployed at MD Anderson Cancer Center, ordered high
# (use-as-is) to low (unusable). ``explanation`` is surfaced as the Likert
# button tooltip in the assessment UI.
LIKERT_SCALE: list[LikertPoint] = [
    LikertPoint(
        5,
        "Strongly agree",
        "Use-as-is (i.e. clinically acceptable, and could be used for treatment without change).",
    ),
    LikertPoint(
        4,
        "Agree",
        "Minor edits that are not necessary. Stylistic differences, but not clinically "
        "important. The current contours/plan are acceptable.",
    ),
    LikertPoint(
        3,
        "Neither agree nor disagree",
        "Minor edits that are necessary. Minor edits are those that the reviewer judges can "
        "be made in less time than starting from scratch or are expected to have minimal "
        "effect on treatment outcome.",
    ),
    LikertPoint(
        2,
        "Disagree",
        "Major edits. The necessary edits are required to ensure appropriate treatment, and "
        "sufficiently significant that the user would prefer to start from scratch.",
    ),
    LikertPoint(
        1,
        "Strongly disagree",
        "Unusable. The quality of the automatically generated contour or plan is so poor that "
        "it is unusable.",
    ),
]

# Valid score values, for validation on load/restore.
LIKERT_VALUES: frozenset[int] = frozenset(p.value for p in LIKERT_SCALE)


@dataclass(frozen=True)
class QualitativeItem:
    """A single contour to be rated.

    ``organ_name`` is the template/drawer organ the contour was matched into;
    ``roi_name`` is the contour's own ROI name. ``source_label`` identifies the
    contouring source (hidden from the rater in blinded mode). ``is_gt`` marks
    the designated ground-truth contour (only present when "include GT" is on).
    """

    patient_id: str
    organ_name: str
    source_label: str
    rtstruct_sop_uid: str
    roi_number: int
    roi_name: str
    is_gt: bool

    @property
    def item_id(self) -> str:
        """Stable identity used to key scores + survive session save/restore."""
        return f"{self.patient_id}|{self.organ_name}|{self.rtstruct_sop_uid}|{self.roi_number}"


def build_rating_stack(
    drawers_state: list[dict[str, Any]],
    *,
    include_gt: bool = False,
    randomize: bool = False,
    seed: int | None = None,
) -> list[QualitativeItem]:
    """Flatten the matched drawers into an ordered list of contours to rate.

    The natural order is drawer-major, then patient, then GT (if included)
    followed by each test contour — i.e. the order the drawers appear in Tab 3.
    When ``randomize`` is set the list is shuffled with a seeded RNG so the
    order is reproducible across a session save/restore (store the seed).

    Items whose RTSS SOP UID is blank are skipped (e.g. an unset GT slot).
    """
    items: list[QualitativeItem] = []
    for drawer in drawers_state or []:
        organ_name = str(drawer.get("organ_name", "") or "")
        for patient in drawer.get("patients", []) or []:
            patient_id = str(patient.get("patient_id", "") or "")
            if include_gt:
                gt = patient.get("gt", {}) or {}
                gt_sop = str(gt.get("rtstruct_sop_uid", "") or "")
                if gt_sop:
                    items.append(
                        QualitativeItem(
                            patient_id=patient_id,
                            organ_name=organ_name,
                            source_label=str(gt.get("source_label", "") or ""),
                            rtstruct_sop_uid=gt_sop,
                            roi_number=int(gt.get("roi_number", 0) or 0),
                            roi_name=str(gt.get("roi_name", "") or ""),
                            is_gt=True,
                        )
                    )
            for test in patient.get("tests", []) or []:
                test_sop = str(test.get("rtstruct_sop_uid", "") or "")
                if not test_sop:
                    continue
                items.append(
                    QualitativeItem(
                        patient_id=patient_id,
                        organ_name=organ_name,
                        source_label=str(test.get("source_label", "") or ""),
                        rtstruct_sop_uid=test_sop,
                        roi_number=int(test.get("roi_number", 0) or 0),
                        roi_name=str(test.get("organ_name", "") or ""),
                        is_gt=False,
                    )
                )

    if randomize:
        # Shuffle at the (patient, organ) GROUP level so every source / vendor
        # for a given organ stays consecutive — the rater finishes all contours
        # of one organ before moving on (required for transparent mode, and
        # harmless in blinded). Within a group the natural order (GT first, then
        # tests) is preserved.
        groups: dict[tuple[str, str], list[QualitativeItem]] = {}
        for it in items:
            groups.setdefault((it.patient_id, it.organ_name), []).append(it)
        keys = list(groups.keys())
        random.Random(seed).shuffle(keys)
        items = [it for key in keys for it in groups[key]]
    return items


def items_by_patient(items: list[QualitativeItem]) -> dict[str, list[QualitativeItem]]:
    """Group items by ``patient_id`` (used by transparent mode to show siblings)."""
    grouped: dict[str, list[QualitativeItem]] = {}
    for item in items:
        grouped.setdefault(item.patient_id, []).append(item)
    return grouped
