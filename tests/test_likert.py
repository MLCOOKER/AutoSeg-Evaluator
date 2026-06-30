"""Tests for core.likert — scale definition + rating-stack construction."""

from __future__ import annotations

from autoseg_evaluator.core.likert import (
    LIKERT_SCALE,
    LIKERT_VALUES,
    build_rating_stack,
    items_by_patient,
)


def _drawers():
    """Two drawers, two patients, each with a GT + two test contours."""
    return [
        {
            "organ_name": "Brainstem",
            "patients": [
                {
                    "patient_id": "P1",
                    "gt": {
                        "rtstruct_sop_uid": "gt1",
                        "source_label": "Manual",
                        "roi_number": 1,
                        "roi_name": "Brainstem",
                    },
                    "tests": [
                        {
                            "rtstruct_sop_uid": "a1",
                            "source_label": "VendorA",
                            "organ_name": "Brain stem",
                            "roi_number": 5,
                        },
                        {
                            "rtstruct_sop_uid": "b1",
                            "source_label": "VendorB",
                            "organ_name": "Brainstem",
                            "roi_number": 9,
                        },
                    ],
                }
            ],
        },
        {
            "organ_name": "Parotid_L",
            "patients": [
                {
                    "patient_id": "P2",
                    "gt": {
                        "rtstruct_sop_uid": "gt2",
                        "source_label": "Manual",
                        "roi_number": 2,
                        "roi_name": "Parotid L",
                    },
                    "tests": [
                        {
                            "rtstruct_sop_uid": "a2",
                            "source_label": "VendorA",
                            "organ_name": "Parotid_L",
                            "roi_number": 7,
                        },
                    ],
                }
            ],
        },
    ]


def test_scale_is_five_points_1_to_5():
    assert len(LIKERT_SCALE) == 5
    assert {p.value for p in LIKERT_SCALE} == {1, 2, 3, 4, 5}
    assert set(LIKERT_VALUES) == {1, 2, 3, 4, 5}
    # Highest first (use-as-is), each point carries an explanation tooltip.
    assert LIKERT_SCALE[0].value == 5
    assert all(p.explanation for p in LIKERT_SCALE)


def test_stack_excludes_gt_by_default_and_keeps_drawer_order():
    stack = build_rating_stack(_drawers())
    # 2 tests (Brainstem) + 1 test (Parotid) = 3, no GT
    assert [i.source_label for i in stack] == ["VendorA", "VendorB", "VendorA"]
    assert all(i.is_gt is False for i in stack)
    assert [i.patient_id for i in stack] == ["P1", "P1", "P2"]


def test_stack_includes_gt_when_requested():
    stack = build_rating_stack(_drawers(), include_gt=True)
    # GT then tests per patient: gt1, a1, b1, gt2, a2
    assert [i.rtstruct_sop_uid for i in stack] == ["gt1", "a1", "b1", "gt2", "a2"]
    assert stack[0].is_gt is True
    assert stack[0].roi_name == "Brainstem"


def test_randomize_is_deterministic_for_a_seed():
    a = build_rating_stack(_drawers(), include_gt=True, randomize=True, seed=42)
    b = build_rating_stack(_drawers(), include_gt=True, randomize=True, seed=42)
    c = build_rating_stack(_drawers(), include_gt=True, randomize=True, seed=7)
    ids_a = [i.item_id for i in a]
    assert ids_a == [i.item_id for i in b]  # same seed → same order
    # different seed almost certainly reorders; the set of items is identical
    assert set(ids_a) == {i.item_id for i in c}


def test_randomize_keeps_organ_groups_consecutive():
    # Group-aware shuffle: every (patient, organ) block stays contiguous, so a
    # rater finishes all sources for one organ before moving on.
    stack = build_rating_stack(_drawers(), include_gt=True, randomize=True, seed=3)
    keys = [(it.patient_id, it.organ_name) for it in stack]
    blocks = []
    for k in keys:
        if not blocks or blocks[-1] != k:
            blocks.append(k)
    assert len(blocks) == len(set(blocks))  # no group split or revisited


def test_item_id_is_stable_and_unique():
    stack = build_rating_stack(_drawers(), include_gt=True)
    ids = [i.item_id for i in stack]
    assert len(ids) == len(set(ids))
    assert stack[1].item_id == "P1|Brainstem|a1|5"


def test_items_by_patient_groups():
    grouped = items_by_patient(build_rating_stack(_drawers(), include_gt=True))
    assert set(grouped) == {"P1", "P2"}
    assert len(grouped["P1"]) == 3  # gt + 2 tests
    assert len(grouped["P2"]) == 2  # gt + 1 test


def test_blank_sop_items_are_skipped():
    drawers = [
        {
            "organ_name": "X",
            "patients": [
                {
                    "patient_id": "P1",
                    "gt": {"rtstruct_sop_uid": "", "roi_number": 0},
                    "tests": [{"rtstruct_sop_uid": "", "source_label": "V", "roi_number": 0}],
                }
            ],
        }
    ]
    assert build_rating_stack(drawers, include_gt=True) == []
