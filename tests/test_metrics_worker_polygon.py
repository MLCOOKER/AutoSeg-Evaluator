"""The worker's polygon stream: caching, availability and failure reporting.

The metric mathematics is validated elsewhere — the suppliers' suites, the
published archives, and `tests/test_polygon_metrics.py`. What matters here is
the behaviour around it: that a structure is read once however many sources it
is compared against, that a failure lands in its own column instead of voiding
the mask metrics beside it, and that a comparison with no contours to measure
says so.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from shapely.geometry import box  # noqa: E402

from autoseg_evaluator.core.polygon_metrics import (  # noqa: E402
    STATUS_NO_CONTOURS,
    ContourRegions,
    PolygonConfig,
    PolygonMetrics,
)
from autoseg_evaluator.workers.metrics_worker import MetricsWorker  # noqa: E402

ENABLED = {"metrics": {"apl": True, "hd95": True, "mean": True}, "tolerance_mm": 3.0}


def _worker(**polygon) -> MetricsWorker:
    return MetricsWorker(None, [], {"polygon": polygon or ENABLED})


def _group(**overrides):
    group = {
        "patient_id": "P01",
        "gt_sop": "gt.1",
        "gt_roi_number": 1,
        "organ_name": "Parotid",
        "tests": [],
    }
    group.update(overrides)
    return group


def _record(sop="test.1", roi=2):
    return {
        "meta": {"rtstruct_sop_uid": sop, "roi_number": roi, "organ_name": "Parotid"},
        "rtss": SimpleNamespace(),
    }


def test_the_stream_is_off_until_a_metric_is_selected():
    """Nothing is read, resolved or measured while nothing is asked for."""
    worker = MetricsWorker(None, [], {})

    assert not worker._polygon_config.any_enabled()
    assert worker._polygon_config.columns() == ()
    assert worker._polygon_engine is None


def test_a_consensus_ground_truth_has_no_contours_to_compare(monkeypatch):
    """Availability belongs to the pair, not to either structure.

    A consensus is born as a binary mask. No amount of parsing the test side
    makes the comparison defined, so this must not even reach for an engine.
    """
    worker = _worker()
    monkeypatch.setattr(
        worker, "_polygon_engine_for_run", lambda: pytest.fail("must not select an engine")
    )

    values, status = worker._polygon_metrics(_group(_gt_synthetic=True), _record())

    assert values == {}
    assert status == STATUS_NO_CONTOURS


def test_a_series_that_cannot_yield_a_frame_is_reported_once_and_remembered():
    """The reason is cached: a series that failed will fail again identically."""
    worker = _worker()
    calls = []

    def resolve(_library, _patient, _sop):
        calls.append(1)
        return SimpleNamespace(target=None)

    import autoseg_evaluator.workers.metrics_worker as module

    original = module.resolve_image_series
    module.resolve_image_series = resolve
    try:
        first = worker._polygon_grid("P01", "gt.1")
        second = worker._polygon_grid("P01", "gt.1")
    finally:
        module.resolve_image_series = original

    assert isinstance(first, str) and "unavailable" in first
    assert first is second
    assert len(calls) == 1, "the resolution was repeated after it had already failed"


def test_a_structure_is_prepared_once_however_many_sources_it_meets(monkeypatch):
    """One ground truth against five vendors is one parse, not five.

    Preparation walks every contour and builds the edge arrays the engine
    indexes; repeating it per pair would multiply the dominant setup cost by the
    number of sources, which is exactly the shape of a real cohort.
    """
    worker = _worker()
    regions = ContourRegions(
        planes={0: box(0, 0, 10, 10)}, geometric_type="CLOSED_PLANAR", vertices=4
    )
    parses = []

    monkeypatch.setattr(
        "autoseg_evaluator.workers.metrics_worker.parse_structure",
        lambda *args, **kwargs: (parses.append(1), regions)[1],
    )
    prepared = object()
    monkeypatch.setattr(
        worker, "_polygon_engine_for_run", lambda: SimpleNamespace(prepare=lambda _r: prepared)
    )

    for _ in range(5):
        assert worker._polygon_structure("P01", "gt.1", 1, SimpleNamespace(), object()) is prepared
    assert len(parses) == 1


def test_a_structure_with_no_contours_is_undefined_rather_than_prepared(monkeypatch):
    worker = _worker()
    monkeypatch.setattr(
        "autoseg_evaluator.workers.metrics_worker.parse_structure",
        lambda *args, **kwargs: ContourRegions(
            planes={}, geometric_type="CLOSED_PLANAR", vertices=0
        ),
    )
    monkeypatch.setattr(
        worker,
        "_polygon_engine_for_run",
        lambda: SimpleNamespace(prepare=lambda _r: pytest.fail("nothing to prepare")),
    )

    outcome = worker._polygon_structure("P01", "gt.1", 1, SimpleNamespace(), object())

    assert isinstance(outcome, str)
    assert "no contours" in outcome


def test_an_engine_that_cannot_be_chosen_fails_every_row_the_same_way(monkeypatch):
    """A missing library is a run-long condition, not a per-pair surprise.

    Reporting it on each row is right — the rows are what a user reads — but
    re-deciding it per pair would probe the filesystem for every comparison.
    """
    worker = _worker()
    attempts = []

    def refuse():
        attempts.append(1)
        raise RuntimeError("no compiled library for this platform")

    monkeypatch.setattr("autoseg_evaluator.workers.metrics_worker.select_engine", refuse)

    assert worker._polygon_engine_for_run() is None
    assert worker._polygon_engine_for_run() is None
    assert len(attempts) == 1

    values, status = worker._polygon_metrics(_group(), _record())
    assert values == {}
    assert "no compiled library" in status


def test_the_caches_are_released_with_the_rest_of_the_patient():
    """Prepared structures hold edge arrays, so they follow the CT out.

    Peak memory has to stay bounded by one patient; a cache keyed by patient
    that nothing clears grows with the cohort instead.
    """
    worker = _worker()
    worker._grid_cache[("P01", "gt.1")] = object()
    worker._grid_cache[("P02", "gt.9")] = object()
    worker._polygon_cache[("P01", "gt.1", 1)] = object()
    worker._polygon_cache[("P02", "gt.9", 1)] = object()

    worker._evict_patient_caches("P01")

    assert [k for k in worker._grid_cache] == [("P02", "gt.9")]
    assert [k for k in worker._polygon_cache] == [("P02", "gt.9", 1)]


def test_references_set_aside_are_recorded_against_the_side_they_came_from(monkeypatch):
    """A ground truth read on its coordinates is worth finding later.

    Only the side that needed it carries the note: a test structure set that
    was internally consistent says nothing, rather than inheriting a caveat.
    """
    worker = MetricsWorker(None, [], {"polygon": ENABLED, "audit": {"sidecar": True}})
    note = "12 contour image references name no slice in this image series"

    def parse(_dataset, roi_number, _grid):
        return ContourRegions(
            planes={0: box(0, 0, 10, 10)},
            geometric_type="CLOSED_PLANAR",
            vertices=4,
            references_set_aside=(note,) if roi_number == 1 else (),
        )

    monkeypatch.setattr("autoseg_evaluator.workers.metrics_worker.parse_structure", parse)
    monkeypatch.setattr(worker, "_polygon_grid", lambda *_a: object())
    monkeypatch.setattr(worker, "_load_rtstruct", lambda *_a: SimpleNamespace())
    engine = SimpleNamespace(
        settings={"engine": "fast"},
        prepare=lambda regions: regions,
        compare=lambda *_a, **_k: PolygonMetrics(values={"poly_hd95_mm": 1.0}, detail={"x": 1}),
    )
    monkeypatch.setattr(worker, "_polygon_engine_for_run", lambda: engine)

    worker._polygon_metrics(_group(), _record())

    audit = worker._pending_polygon_audit
    assert audit["references_set_aside"] == {"ground_truth": [note]}

    worker._evict_patient_caches("P01")
    assert not worker._structure_notes


@pytest.mark.parametrize("median_selected", [True, False])
def test_an_undetermined_metric_is_explained_only_when_it_was_asked_for(
    monkeypatch, median_selected
):
    """The row keeps every determined value; the status explains the blank.

    A blank the user did not ask for is not a finding, so it says nothing.
    """
    metrics = {"hd95": True, "median": median_selected}
    worker = MetricsWorker(None, [], {"polygon": {"metrics": metrics, "tolerance_mm": 3.0}})
    reason = "undefined: 2D median contour distance could be anything from 0 to 2 mm"
    regions = ContourRegions(planes={0: box(0, 0, 10, 10)}, geometric_type="x", vertices=4)
    monkeypatch.setattr(
        "autoseg_evaluator.workers.metrics_worker.parse_structure", lambda *a: regions
    )
    monkeypatch.setattr(worker, "_polygon_grid", lambda *_a: object())
    monkeypatch.setattr(worker, "_load_rtstruct", lambda *_a: SimpleNamespace())
    engine = SimpleNamespace(
        settings={},
        prepare=lambda r: r,
        compare=lambda *_a, **_k: PolygonMetrics(
            values={"poly_hd95_mm": 2.4, "poly_planes_joint": 4},
            undefined={"poly_median_distance_mm": reason},
        ),
    )
    monkeypatch.setattr(worker, "_polygon_engine_for_run", lambda: engine)

    values, status = worker._polygon_metrics(_group(), _record())

    assert values["poly_hd95_mm"] == 2.4
    assert "poly_median_distance_mm" not in values
    assert status == (reason if median_selected else "")


def test_only_the_selected_columns_reach_the_row():
    """All six are computed regardless; selection decides what is shown.

    They share one distance distribution, so a narrower selection buys no time —
    it buys a narrower table, which is the honest thing to say about it.
    """
    config = PolygonConfig.from_dict({"metrics": {"hd95": True}})
    full = {
        "poly_apl_mm": 1.0,
        "poly_napl": 0.1,
        "poly_hd95_mm": 2.0,
        "poly_mean_distance_mm": 3.0,
        "poly_planes_joint": 10,
        "poly_planes_gt_only": 1,
        "poly_planes_test_only": 0,
    }

    kept = config.select(full)

    assert set(kept) == {
        "poly_hd95_mm",
        "poly_planes_joint",
        "poly_planes_gt_only",
        "poly_planes_test_only",
    }
