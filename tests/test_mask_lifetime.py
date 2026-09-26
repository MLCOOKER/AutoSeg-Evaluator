"""Masks leave the worker's cache after their last use, not with their patient.

A mask is a full-CT volume. The worker used to keep every mask of a patient
until the patient was finished: on a head and neck case, every organ against
every contour set at once, which runs to gigabytes. It now counts, before the
run, how many drawers use each mask and drops it after the last. Only memory
may change — never a number, and never how often a shared mask is rasterised.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydicom.uid import generate_uid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autoseg_evaluator.workers.metrics_worker as worker_module  # noqa: E402
from autoseg_evaluator.workers.metrics_worker import MetricsWorker  # noqa: E402
from test_audit_fixes import _scan, _write_ct, _write_rtss  # noqa: E402

CONFIG = {
    "geometric": {"dice": True, "hausdorff95": True, "volume": True},
    "staple": {},
}
SQUARES = {
    "Manual": (6.0, 6.0, 16.0, 16.0),
    "VendorA": (7.0, 6.0, 17.0, 16.0),
    "VendorB": (6.0, 7.0, 16.0, 17.0),
    "VendorC": (5.0, 6.0, 15.0, 17.0),
}


def _cohort(root: Path) -> dict[str, dict[str, str]]:
    """Two patients, one CT each, and a structure set per source."""
    sops: dict[str, dict[str, str]] = {}
    for patient in ("P1", "P2"):
        study, frame = generate_uid(), generate_uid()
        series, ct_sops = _write_ct(
            root / patient / "ct", patient_id=patient, study_uid=study, for_uid=frame, slices=6
        )
        sops[patient] = {
            source: _write_rtss(
                root / patient,
                patient_id=patient,
                study_uid=study,
                for_uid=frame,
                series_uid=series,
                ct_sops=ct_sops,
                z0=0.0,
                manufacturer=source,
                square=square,
            )
            for source, square in SQUARES.items()
        }
    return sops


def _patient(patient: str, sops: dict[str, str], tests: list[str]) -> dict:
    return {
        "patient_id": patient,
        "gt": {
            "rtstruct_sop_uid": sops["Manual"],
            "source_label": "Manual",
            "roi_number": 1,
            "roi_name": "Parotid_L",
        },
        "tests": [
            {
                "rtstruct_sop_uid": sops[source],
                "source_label": source,
                "organ_name": "Parotid_L",
                "roi_number": 1,
            }
            for source in tests
        ],
    }


def _drawers(sops: dict[str, dict[str, str]]) -> list[dict]:
    """Two drawers sharing P1's ground truth; the first also runs STAPLE."""
    return [
        {
            "organ_name": "A_Parotid",
            "truncate": False,
            "gt_comparison": True,
            "staple_consensus": True,
            "patients": [
                _patient("P1", sops["P1"], ["VendorA", "VendorB"]),
                _patient("P2", sops["P2"], ["VendorA"]),
            ],
        },
        {
            "organ_name": "B_Parotid",
            "truncate": True,
            "gt_comparison": True,
            "patients": [_patient("P1", sops["P1"], ["VendorC"])],
        },
    ]


def _run(worker: MetricsWorker) -> list[dict]:
    rows: list[dict] = []
    worker.result.connect(rows.append)
    worker._do_run()
    return rows


def _without_times(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in row.items() if k != "computed_at"} for row in rows]


def test_a_mask_is_dropped_after_its_last_drawer(tmp_path):
    sops = _cohort(tmp_path)
    worker = MetricsWorker(_scan(tmp_path), _drawers(sops), CONFIG)

    cached_before: list[tuple[str, str, set]] = []
    compute = worker._compute_group

    def spy(group, *args, **kwargs):
        sources = {sop: name for name, sop in sops[group["patient_id"]].items()}
        cached = {sources[sop] for (_p, sop, _r) in worker._mask_cache}
        cached_before.append((group["patient_id"], group["organ_name"], cached))
        return compute(group, *args, **kwargs)

    worker._compute_group = spy
    rows = _run(worker)

    assert rows and not any(row.get("error") for row in rows)
    # Groups run patient by patient; P1's shared ground truth waits for its
    # second drawer, and the first drawer's vendor masks are already gone.
    assert cached_before == [
        ("P1", "A_Parotid", set()),
        ("P1", "B_Parotid", {"Manual"}),
        ("P2", "A_Parotid", set()),
    ]
    assert worker._mask_cache == {}
    assert not worker._mask_uses


def test_a_shared_mask_is_still_rasterised_once(tmp_path, monkeypatch):
    sops = _cohort(tmp_path)
    rasterised: list[tuple[str, int]] = []
    real = worker_module.mask_with_reading

    def counting(ct, rtss, roi_number):
        rasterised.append((str(rtss.SOPInstanceUID), int(roi_number)))
        return real(ct, rtss, roi_number)

    monkeypatch.setattr(worker_module, "mask_with_reading", counting)
    _run(MetricsWorker(_scan(tmp_path), _drawers(sops), CONFIG))

    assert rasterised.count((sops["P1"]["Manual"], 1)) == 1
    assert len(rasterised) == len(set(rasterised))


def test_releasing_masks_changes_no_result(tmp_path):
    sops = _cohort(tmp_path)
    released = _run(MetricsWorker(_scan(tmp_path), _drawers(sops), CONFIG))

    kept_worker = MetricsWorker(_scan(tmp_path), _drawers(sops), CONFIG)
    kept_worker._release_group_masks = lambda group: None  # the old behaviour
    kept = _run(kept_worker)

    assert len(released) == len(kept) > 5
    assert _without_times(released) == _without_times(kept)


def test_masks_are_released_when_a_group_fails(tmp_path):
    sops = _cohort(tmp_path)
    worker = MetricsWorker(_scan(tmp_path), _drawers(sops), CONFIG)
    compute = worker._compute_group
    calls = []

    def failing_second(group, *args, **kwargs):
        calls.append(group["organ_name"])
        if len(calls) == 2:
            compute(group, *args, **kwargs)  # its masks are loaded, then it fails
            raise RuntimeError("boom")
        return compute(group, *args, **kwargs)

    worker._compute_group = failing_second
    with pytest.raises(RuntimeError):
        worker._do_run()
    # The failed group's masks, including the shared ground truth's last use,
    # left the cache even though the group raised.
    assert all(key[0] != "P1" for key in worker._mask_cache)
