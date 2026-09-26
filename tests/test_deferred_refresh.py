"""Results and Report refresh in batches, and only when they can be seen.

Both tabs used to rebuild in full after every result row and every Likert
score. Each rebuild reads the whole table, so a computation's rebuild work grew
with the square of its rows — about half an hour of the window's time over
1,000 rows — and every Likert click paid seconds for tabs nobody was looking
at. What matters here is that batching changes when the tables are drawn and
never what they show.
"""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import autoseg_evaluator.ui.main_window as main_window_module  # noqa: E402
from autoseg_evaluator.data.results import ResultsManager  # noqa: E402
from autoseg_evaluator.ui.deferred_refresh import DeferredRefresh  # noqa: E402
from autoseg_evaluator.ui.main_window import MainWindow  # noqa: E402
from autoseg_evaluator.ui.tabs.report import ReportTab  # noqa: E402
from autoseg_evaluator.ui.tabs.results import ResultsTab  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(autouse=True)
def _no_settings_file(monkeypatch):
    monkeypatch.setattr(main_window_module, "save_settings", lambda _settings: None)


def _row(i: int, *, metrics: dict | None = None, error: str = "") -> dict:
    return {
        "drawer": f"Organ{i % 4}",
        "canonical_organ": f"Organ{i % 4}",
        "patient_id": f"P{i // 4:02d}",
        "comparison_mode": "gt",
        "gt_source_label": "Manual",
        "gt_roi_name": f"Organ{i % 4}",
        "test_source_label": "VendorA",
        "test_rtstruct_sop_uid": f"rtss-{i // 4}",
        "test_organ": f"Organ{i % 4}",
        "test_roi_number": i % 4 + 1,
        "computed_at": "2026-09-26 10:00:00+08:00",
        "metrics": dict(metrics if metrics is not None else {"dice": 0.5 + i / 1000}),
        "error": error,
    }


def _grid(tab: ResultsTab) -> list[list[str]]:
    """Every cell's text, row by row, as the user sees it."""
    table = tab._table
    header = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    cells = [
        [table.item(r, c).text() if table.item(r, c) else "" for c in range(table.columnCount())]
        for r in range(table.rowCount())
    ]
    return [header, *cells]


def _rebuilt(manager: ResultsManager) -> list[list[str]]:
    fresh = ResultsTab()
    fresh.set_results_manager(manager)
    grid = _grid(fresh)
    fresh.deleteLater()
    return grid


# ---- The mechanism ------------------------------------------------------------


class _View(QWidget):
    """A widget wired as the tabs are: its showEvent reports to the helper."""

    def __init__(self, refresh, **kwargs) -> None:
        super().__init__()
        self.deferred = DeferredRefresh(self, refresh, **kwargs)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.deferred.shown()


def test_a_hidden_widget_refreshes_once_when_shown(qapp):
    calls: list[int] = []
    view = _View(lambda: calls.append(1), min_interval_ms=5)
    for _ in range(50):
        view.deferred.request()
    QTest.qWait(30)
    assert calls == []
    assert view.deferred.pending

    view.show()
    assert calls == [1]
    assert not view.deferred.pending
    view.close()


def test_requests_to_a_visible_widget_are_gathered_into_one_refresh(qapp):
    calls: list[int] = []
    view = _View(lambda: calls.append(1), min_interval_ms=10)
    view.show()
    for _ in range(50):
        view.deferred.request()
    assert calls == []  # never inside the request itself
    QTest.qWait(60)
    assert calls == [1]
    view.close()


def test_a_refresh_made_otherwise_settles_what_was_owed(qapp):
    calls: list[int] = []
    view = _View(lambda: calls.append(1), min_interval_ms=5)
    view.deferred.request()
    view.deferred.settled()
    view.show()
    QTest.qWait(30)
    assert calls == []
    view.close()


def test_a_request_to_a_widget_hidden_meanwhile_waits_for_it(qapp):
    calls: list[int] = []
    view = _View(lambda: calls.append(1), min_interval_ms=10)
    view.show()
    view.deferred.request()
    view.hide()
    QTest.qWait(40)
    assert calls == []
    view.show()
    assert calls == [1]
    view.close()


def test_a_slow_refresh_waits_longer_before_the_next(qapp):
    """However large the table, refreshing never takes the window over."""
    view = _View(lambda: QTest.qWait(40), min_interval_ms=5)
    view.show()
    view.deferred.request()
    QTest.qWait(80)
    assert view.deferred._interval_ms >= 3 * 40
    view.close()


# ---- Results tab ------------------------------------------------------------------


def test_results_tab_hidden_catches_up_when_shown(qapp):
    manager = ResultsManager()
    tab = ResultsTab()
    tab.set_results_manager(manager)
    for i in range(20):
        manager.add_row(_row(i))
        tab.request_refresh()
    QTest.qWait(20)
    assert tab._table.rowCount() == 0

    tab.show()
    assert tab._table.rowCount() == 20
    assert _grid(tab) == _rebuilt(manager)
    tab.close()


def test_new_rows_are_appended_to_what_is_shown(qapp):
    """Rows already drawn stay as they are; only the new ones are made."""
    manager = ResultsManager()
    manager.add_rows([_row(i) for i in range(5)])
    tab = ResultsTab()
    tab.set_results_manager(manager)
    tab.show()
    first = tab._table.item(0, 0)

    for i in range(5, 12):
        manager.add_row(_row(i))
    tab._catch_up()

    assert tab._table.item(0, 0) is first  # not rebuilt
    assert tab._table.rowCount() == 12
    assert _grid(tab) == _rebuilt(manager)
    assert tab._row_count_label.text() == "12 rows"
    tab.close()


def test_error_rows_appended_are_counted_and_tinted(qapp):
    manager = ResultsManager()
    manager.add_row(_row(0))
    tab = ResultsTab()
    tab.set_results_manager(manager)
    manager.add_row(_row(1, error="DVH: no dose"))
    tab._catch_up()
    assert "1 with errors" in tab._row_count_label.text()
    clean, failed = tab._table.item(0, 0), tab._table.item(1, 0)
    assert clean.background().color() != failed.background().color()


def test_appended_rows_sort_into_place(qapp):
    manager = ResultsManager()
    manager.add_rows([_row(i, metrics={"dice": d}) for i, d in enumerate((0.2, 0.6))])
    tab = ResultsTab()
    tab.set_results_manager(manager)
    headers = _grid(tab)[0]
    dice = next(c for c, h in enumerate(headers) if h.startswith("Dice"))
    tab._table.sortItems(dice)

    manager.add_row(_row(2, metrics={"dice": 0.4}))
    tab._catch_up()
    shown = [tab._table.item(r, dice).text() for r in range(tab._table.rowCount())]
    assert shown == ["0.2", "0.4", "0.6"]


@pytest.mark.parametrize(
    "change",
    ["new column", "likert score", "organ index", "cleared", "restored"],
)
def test_changes_to_shown_rows_rebuild_the_table(qapp, change):
    """Anything but new computed rows can change what is already drawn."""
    manager = ResultsManager()
    manager.add_rows([_row(i) for i in range(4)])
    tab = ResultsTab()
    tab.set_results_manager(manager)
    first = tab._table.item(0, 0)

    if change == "new column":
        manager.add_row(_row(4, metrics={"dice": 0.7, "dmean_gy": 12.0, "d95_gy": 11.0}))
    elif change == "likert score":
        manager.upsert_qualitative_score(
            patient_id="P00",
            drawer="Organ0",
            source_label="VendorA",
            rtstruct_sop_uid="rtss-0",
            roi_name="Organ0",
            roi_number=1,
            is_gt=False,
            rater="Alice",
            score=4,
            blinded=True,
        )
    elif change == "organ index":
        manager.set_organ_index(None)
    elif change == "cleared":
        manager.clear_computed()
    else:
        manager.apply_session_state({"rows": [_row(9)]})
    tab._catch_up()

    assert tab._table.rowCount() == 0 or tab._table.item(0, 0) is not first
    assert _grid(tab) == _rebuilt(manager)


def test_rows_computed_while_scores_exist_rebuild_correctly(qapp):
    """A new row can take over a score-only row; appending would duplicate it."""
    manager = ResultsManager()
    manager.upsert_qualitative_score(
        patient_id="P00",
        drawer="Organ0",
        source_label="VendorA",
        rtstruct_sop_uid="rtss-0",
        roi_name="Organ0",
        roi_number=1,
        is_gt=False,
        rater="Alice",
        score=4,
        blinded=True,
    )
    tab = ResultsTab()
    tab.set_results_manager(manager)
    assert tab._table.rowCount() == 1  # the score, on a row of its own

    manager.add_row(_row(0))  # the computed row for that contour
    tab._catch_up()
    assert tab._table.rowCount() == 1
    assert _grid(tab) == _rebuilt(manager)


# ---- Report tab ----------------------------------------------------------------------


def test_report_tab_hidden_is_rebuilt_only_when_shown(qapp):
    manager = ResultsManager()
    tab = ReportTab()
    tab.set_results_manager(manager)
    tab.refresh()
    for i in range(12):
        manager.add_row(_row(i))
        tab.request_refresh()
    QTest.qWait(20)
    assert tab._all.organs() == []

    tab.show()
    assert tab._all.organs()
    tab.close()


# ---- The window ------------------------------------------------------------------------


def test_results_arriving_behind_another_tab_are_drawn_on_opening_it(qapp):
    win = MainWindow({})
    win.show()
    win._tabs.setCurrentIndex(4)  # Compute
    for i in range(40):
        win._on_metric_result(_row(i))
    QTest.qWait(20)
    assert win._results_tab._table.rowCount() == 0
    assert win._report_tab._all.organs() == []

    win._tabs.setCurrentIndex(5)  # Results
    assert win._results_tab._table.rowCount() == 40
    win._tabs.setCurrentIndex(6)  # Report
    assert win._report_tab._all.organs()
    win.close()


def test_scoring_does_not_rebuild_the_hidden_tabs(qapp):
    win = MainWindow({})
    win.show()
    win._results.add_rows([_row(i) for i in range(8)])
    win._results_tab.refresh()
    win._tabs.setCurrentIndex(3)  # Qualitative
    first = win._results_tab._table.item(0, 0)
    for score in (1, 2, 3, 4, 5):
        win._on_qualitative_scored(
            {
                "patient_id": "P00",
                "drawer": "Organ0",
                "source_label": "VendorA",
                "rtstruct_sop_uid": "rtss-0",
                "roi_name": "Organ0",
                "roi_number": 1,
                "is_gt": False,
                "rater": "Alice",
                "score": score,
                "blinded": True,
            }
        )
    QTest.qWait(20)
    assert win._results_tab._table.item(0, 0) is first

    win._tabs.setCurrentIndex(5)
    assert _grid(win._results_tab) == _rebuilt(win._results)
    headers = _grid(win._results_tab)[0]
    assert any("Alice" in h for h in headers)
    win.close()
