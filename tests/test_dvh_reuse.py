"""A contour's dose statistics are integrated once per drawer.

With both the ground-truth comparison and the drawer's STAPLE consensus on, each
contour's DVH was integrated twice with identical inputs: once for its row
against the ground truth and once for its row against the consensus. Within a
drawer the dose and the CT are the ground truth's, so the structure set, ROI
and truncation extent are all the result depends on. What must not change is
any number on any row, or any failure's wording.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autoseg_evaluator.workers.metrics_worker as worker_module  # noqa: E402
from autoseg_evaluator.core.dvh import DoseGrid, DVHError  # noqa: E402
from autoseg_evaluator.workers.metrics_worker import MetricsWorker  # noqa: E402
from test_audit_fixes import _scan  # noqa: E402
from test_mask_lifetime import _cohort, _patient, _without_times  # noqa: E402

CONFIG = {
    "geometric": {"dice": True},
    "dvh": {"include_dmean": True, "include_dmax": True, "d_at_volumes_cc": [0.05]},
    "staple": {},
}
FAILURE = "no contour lies within the ground truth's extent"


def _dose() -> DoseGrid:
    """A dose rising across the CT the test structure sets are drawn on."""
    frames, rows, cols = 16, 30, 30
    z, y, x = np.mgrid[:frames, :rows, :cols].astype(float)
    return DoseGrid(
        values=40.0 + 0.5 * x + 0.3 * y + 0.2 * z,
        origin=np.array([-2.0, -2.0, -2.0]),
        row_direction=np.array([1.0, 0.0, 0.0]),
        column_direction=np.array([0.0, 1.0, 0.0]),
        normal=np.array([0.0, 0.0, 1.0]),
        pixel_spacing=(1.0, 1.0),
        frame_offsets=np.arange(frames, dtype=float),
    )


def _drawers(sops) -> list[dict]:
    return [
        {
            "organ_name": "Parotid_L",
            "truncate": True,
            "gt_comparison": True,
            "staple_consensus": True,
            "patients": [_patient("P1", sops["P1"], ["VendorA", "VendorB"])],
        }
    ]


def _worker(tmp_path, sops) -> MetricsWorker:
    worker = MetricsWorker(_scan(tmp_path), _drawers(sops), CONFIG)
    grid = _dose()
    worker._load_dose = lambda patient_id, sop: object()
    worker._load_dose_grid = lambda patient_id, sop: grid
    return worker


def _run(worker) -> list[dict]:
    rows: list[dict] = []
    worker.result.connect(rows.append)
    worker._do_run()
    return rows


class _Forgetful(dict):
    """A cache that keeps nothing: the behaviour before reuse."""

    def __setitem__(self, key, value) -> None:
        pass


def _counting(monkeypatch, fail_for: str | None = None) -> Counter:
    calls: Counter = Counter()
    real = worker_module.structure_dvh

    def counted(rtss, roi_number, dose, reference, config, *, z_extent_mm=None, **kwargs):
        calls[(str(rtss.SOPInstanceUID), int(roi_number), z_extent_mm)] += 1
        if fail_for is not None and str(rtss.SOPInstanceUID) == fail_for:
            raise DVHError(FAILURE)
        return real(rtss, roi_number, dose, reference, config, z_extent_mm=z_extent_mm, **kwargs)

    monkeypatch.setattr(worker_module, "structure_dvh", counted)
    return calls


def _dose_rows(rows: list[dict], source: str) -> list[dict]:
    return [r for r in rows if r.get("test_source_label") == source and "dmean_gy" in r["metrics"]]


def test_each_contour_is_integrated_once(tmp_path, monkeypatch):
    sops = _cohort(tmp_path)
    calls = _counting(monkeypatch)
    rows = _run(_worker(tmp_path, sops))

    assert rows and not any(row.get("error") for row in rows)
    # The ground truth untruncated, each vendor over the ground truth's extent.
    assert sorted(calls.values()) == [1, 1, 1]
    # Both of a vendor's rows carry its statistics, from the one integration.
    for source in ("VendorA", "VendorB"):
        against_gt, against_consensus = _dose_rows(rows, source)
        assert against_gt["metrics"]["dmean_gy"] == against_consensus["metrics"]["dmean_gy"]


def test_reuse_changes_no_row(tmp_path):
    sops = _cohort(tmp_path)
    reused = _run(_worker(tmp_path, sops))
    before = _worker(tmp_path, sops)
    before._dvh_cache = _Forgetful()
    recomputed = _run(before)
    assert len(reused) == len(recomputed) > 4
    assert _without_times(reused) == _without_times(recomputed)


def test_a_failure_is_reused_with_its_wording(tmp_path, monkeypatch):
    sops = _cohort(tmp_path)
    failing = sops["P1"]["VendorB"]
    calls = _counting(monkeypatch, fail_for=failing)
    rows = _run(_worker(tmp_path, sops))

    failed = [r for r in rows if r.get("test_source_label") == "VendorB" and r.get("error")]
    assert len(failed) == 2
    assert {r["error"] for r in failed} == {f"DVH: {FAILURE}"}
    assert sum(n for (sop, _roi, _z), n in calls.items() if sop == failing) == 1


def test_the_cache_is_emptied_after_each_drawer(tmp_path):
    sops = _cohort(tmp_path)
    worker = _worker(tmp_path, sops)
    _run(worker)
    assert worker._dvh_cache == {}
