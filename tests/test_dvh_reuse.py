"""Each row's DVH from its contours, the consensus's from its mask, once per drawer.

Every structure with contours takes its dose statistics from them, whatever it
is compared against; a consensus, which exists only as a mask, is sampled over
its voxels. So against a consensus the one mask error in a difference is the
consensus's own, the same for every source, and it cancels between sources
(Supplementary 1, docs/validation/, has why the test is not taken from its mask too).
Within a drawer the dose and the CT are the ground truth's, so a structure's
DVH is computed once, and a failure keeps its wording.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from pydicom.uid import generate_uid
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autoseg_evaluator.workers.metrics_worker as worker_module  # noqa: E402
from autoseg_evaluator.core.dvh import DoseGrid, DVHError  # noqa: E402
from autoseg_evaluator.workers.metrics_worker import (  # noqa: E402
    DVH_FROM_CONTOURS,
    DVH_FROM_MASK,
    MetricsWorker,
)
from test_audit_fixes import _scan, _write_ct, _write_rtss  # noqa: E402
from test_mask_lifetime import _cohort, _patient, _without_times  # noqa: E402

CONFIG = {
    "geometric": {"dice": True},
    "dvh": {"include_dmean": True, "include_dmax": True, "d_at_volumes_cc": [0.05]},
    "staple": {},
}
FAILURE = "no contour lies within the ground truth's extent"
DVH_KEYS = ("dmean_gy", "dmax_gy", "d0.05cc_gy")


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication(sys.argv)


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


def _with_dose(worker: MetricsWorker) -> MetricsWorker:
    grid = _dose()
    worker._load_dose = lambda patient_id, sop: object()
    worker._load_dose_grid = lambda patient_id, sop: grid
    return worker


def _drawers(sops) -> list[dict]:
    """A manual ground truth, with the drawer's STAPLE consensus on too."""
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
    return _with_dose(MetricsWorker(_scan(tmp_path), _drawers(sops), CONFIG))


def _run(worker) -> list[dict]:
    rows: list[dict] = []
    worker.result.connect(rows.append)
    worker._do_run()
    return rows


class _Forgetful(dict):
    """A cache that keeps nothing: the behaviour before reuse."""

    def __setitem__(self, key, value) -> None:
        pass


def _counting(monkeypatch, name: str) -> Counter:
    """Count calls to the worker's ``structure_dvh`` or ``mask_dvh``."""
    calls: Counter = Counter()
    real = getattr(worker_module, name)

    def counted(*args, **kwargs):
        calls[id(args[0])] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(worker_module, name, counted)
    return calls


def _dose_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if "dmean_gy" in r["metrics"]]


def test_contours_everywhere_but_the_consensus(tmp_path, monkeypatch):
    sops = _cohort(tmp_path)
    from_contours = _counting(monkeypatch, "structure_dvh")
    from_masks = _counting(monkeypatch, "mask_dvh")
    rows = _run(_worker(tmp_path, sops))

    assert rows and not any(row.get("error") for row in rows)
    manual_rows = [r for r in _dose_rows(rows) if r["gt_source_label"] == "Manual"]
    pool_rows = [r for r in _dose_rows(rows) if r["gt_source_label"] != "Manual"]
    # Against the manual ground truth: its own dose row and both vendors'.
    assert len(manual_rows) == 3
    assert {r["metrics"]["dvh_basis"] for r in manual_rows} == {DVH_FROM_CONTOURS}
    # Against the drawer's consensus: the three raters from their contours, and
    # the consensus itself, which has none, from its mask.
    assert len(pool_rows) == 4
    consensus = [r for r in pool_rows if r["comparison_mode"] == "gt_dose"]
    raters = [r for r in pool_rows if r["comparison_mode"] != "gt_dose"]
    assert [r["metrics"]["dvh_basis"] for r in consensus] == [DVH_FROM_MASK]
    assert {r["metrics"]["dvh_basis"] for r in raters} == {DVH_FROM_CONTOURS}
    # Each contour integrated once, shared by its manual and pool rows; one mask.
    assert sorted(from_contours.values()) == [1, 1, 1]
    assert sorted(from_masks.values()) == [1]


def test_reuse_changes_no_row(tmp_path):
    sops = _cohort(tmp_path)
    reused = _run(_worker(tmp_path, sops))
    before = _worker(tmp_path, sops)
    before._dvh_cache = _Forgetful()
    recomputed = _run(before)
    assert len(reused) == len(recomputed) > 4
    assert _without_times(reused) == _without_times(recomputed)


def test_a_kept_failure_keeps_its_wording():
    worker = MetricsWorker(library=None, drawers_state=[], config=CONFIG)
    _with_dose(worker)
    calls = []

    def failing(_dose):
        calls.append(1)
        raise DVHError(FAILURE)

    group = {"patient_id": "P1", "gt_sop": "gt"}
    rows = [{"metrics": {}, "error": ""} for _ in range(2)]
    for row in rows:
        worker._dose_into_row(row, group, failing, contour=("rtss", 1, None))
    assert calls == [1]
    assert {r["error"] for r in rows} == {f"DVH: {FAILURE}"}


def test_one_contour_on_two_bases_is_two_results():
    """A contour's DVH from its contours must never answer a request for its mask."""
    worker = _with_dose(MetricsWorker(library=None, drawers_state=[], config=CONFIG))
    group = {"patient_id": "P1", "gt_sop": "gt"}

    class _Result:
        def __init__(self, dmean):
            self.metrics = {"dmean_gy": dmean}
            self.coverage_pct = 100.0
            self.status = ""

    a, b = {"metrics": {}, "error": ""}, {"metrics": {}, "error": ""}
    worker._dose_into_row(a, group, lambda _d: _Result(1.0), contour=("s", 1, None))
    worker._dose_into_row(
        b, group, lambda _d: _Result(2.0), contour=("s", 1, None), basis=DVH_FROM_MASK
    )
    assert (a["metrics"]["dmean_gy"], a["metrics"]["dvh_basis"]) == (1.0, DVH_FROM_CONTOURS)
    assert (b["metrics"]["dmean_gy"], b["metrics"]["dvh_basis"]) == (2.0, DVH_FROM_MASK)


def test_the_cache_is_emptied_after_each_drawer(tmp_path):
    sops = _cohort(tmp_path)
    worker = _worker(tmp_path, sops)
    _run(worker)
    assert worker._dvh_cache == {}


# ---- A Tab 2 consensus as ground truth -------------------------------------------------


def _consensus_cohort(root: Path):
    """One patient: two identical manual contours, and a vendor that differs."""
    study, frame = generate_uid(), generate_uid()
    series, ct_sops = _write_ct(
        root / "P1" / "ct", patient_id="P1", study_uid=study, for_uid=frame, slices=6
    )
    squares = {
        "Manual": (6.0, 6.0, 16.0, 16.0),
        "ManualCopy": (6.0, 6.0, 16.0, 16.0),
        "VendorA": (7.0, 6.0, 17.0, 16.0),
    }
    sops = {
        source: _write_rtss(
            root / "P1",
            patient_id="P1",
            study_uid=study,
            for_uid=frame,
            series_uid=series,
            ct_sops=ct_sops,
            z0=0.0,
            manufacturer=source,
            square=square,
        )
        for source, square in squares.items()
    }
    return sops


def test_against_a_consensus_truth_the_mask_error_is_common_to_every_source(qapp, tmp_path):
    """Tests take their dose from their contours; only the consensus from its mask.

    So the consensus's own mask error is in every test's difference alike, and
    a difference between two sources is exactly the difference of their contour
    DVHs. The price: a contour identical to the consensus differs from it by
    that mask error rather than by nothing.
    """
    from autoseg_evaluator.core.dvh import structure_dvh
    from autoseg_evaluator.ui.tabs.build_consensus import BuildConsensusTab

    sops = _consensus_cohort(tmp_path)
    library = _scan(tmp_path)
    builder = BuildConsensusTab(settings={})
    builder.set_library(library)
    # The two manual contours agree, so their consensus is their own mask.
    entry = builder._build_synthetic_entry(
        "P1",
        {"Parotid_L": [(sops["Manual"], 1, "Parotid_L"), (sops["ManualCopy"], 1, "Parotid_L")]},
    )
    assert library.register_synthetic_consensus("P1", entry.frame_of_reference_uid, entry)
    drawers = [
        {
            "organ_name": "Parotid_L",
            "truncate": False,
            "gt_comparison": True,
            "patients": [
                {
                    "patient_id": "P1",
                    "gt": {
                        "rtstruct_sop_uid": entry.sop_instance_uid,
                        "source_label": entry.source_label,
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
                        for source in ("ManualCopy", "VendorA")
                    ],
                }
            ],
        }
    ]
    worker = _with_dose(MetricsWorker(library, drawers, CONFIG))
    rows = _run(worker)
    by_source = {r["test_source_label"]: r for r in _dose_rows(rows)}

    consensus = by_source.pop(entry.source_label)["metrics"]
    assert consensus["dvh_basis"] == DVH_FROM_MASK
    assert {r["metrics"]["dvh_basis"] for r in by_source.values()} == {DVH_FROM_CONTOURS}

    # Each test's value is its contour DVH, and its difference is taken from
    # the one consensus value, so between sources the consensus cancels.
    ct = worker_module.read_image_series(
        worker_module.find_reference_image_series(library, "P1", sops["ManualCopy"])
    )
    for source, row in by_source.items():
        rtss = worker_module.read_rtstruct(worker._rtstruct_file_path("P1", sops[source]))
        expected = structure_dvh(rtss, 1, _dose(), ct, worker._dvh_config).metrics
        for key in DVH_KEYS:
            assert row["metrics"][key] == pytest.approx(expected[key])
            assert row["metrics"][f"{key}_diff"] == pytest.approx(expected[key] - consensus[key])
    a, b = by_source["ManualCopy"]["metrics"], by_source["VendorA"]["metrics"]
    assert a["dmean_gy_diff"] - b["dmean_gy_diff"] == pytest.approx(a["dmean_gy"] - b["dmean_gy"])
    # The identical contour differs from the consensus by the mask's own error.
    assert abs(by_source["ManualCopy"]["metrics"]["dmean_gy_diff"]) > 0.01
    builder.deleteLater()
