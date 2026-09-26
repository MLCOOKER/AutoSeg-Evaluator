"""Main-window workflows the external audit found wanting.

One computation per results table, confirmed before it is replaced; a
different folder clears the previous cohort's work, after confirming; the
results table travels with the session; the settings and session file behave.
The dialogs are answered through the methods that ask them, so no test blocks.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

import autoseg_evaluator.ui.main_window as main_window_module  # noqa: E402
from autoseg_evaluator.data.session import load_session_file  # noqa: E402
from autoseg_evaluator.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(autouse=True)
def _no_settings_file(monkeypatch):
    """Keep the tests away from the real settings.json."""
    monkeypatch.setattr(main_window_module, "save_settings", lambda _settings: None)


def _row(patient="P1", dice=0.8):
    return {
        "drawer": "Parotid_L",
        "patient_id": patient,
        "comparison_mode": "gt",
        "gt_source_label": "Manual",
        "gt_roi_name": "Parotid_L",
        "test_source_label": "VendorA",
        "test_rtstruct_sop_uid": "rtss-a",
        "test_organ": "Parotid_L",
        "test_roi_number": 1,
        "computed_at": "2026-09-20 10:00:00+08:00",
        "metrics": {"dice": dice},
        "error": "",
    }


def _score(win, score=4):
    win._on_qualitative_scored(
        {
            "patient_id": "P1",
            "drawer": "Parotid_L",
            "source_label": "VendorA",
            "rtstruct_sop_uid": "rtss-a",
            "roi_name": "Parotid_L",
            "roi_number": 1,
            "is_gt": False,
            "rater": "Alice",
            "score": score,
            "blinded": True,
            "scored_at": "2026-09-21 09:00:00+08:00",
        }
    )


# ---- One computation per results table ------------------------------------------


@pytest.mark.parametrize(
    ("answer", "exported", "replaced"),
    [("cancel", False, False), ("replace", False, True), ("export", True, True)],
)
def test_computing_again_replaces_the_table_only_once_confirmed(
    qapp, monkeypatch, answer, exported, replaced
):
    win = MainWindow({})
    win._results.add_row(_row())
    _score(win)
    calls = []
    monkeypatch.setattr(win, "_ask_replace_results", lambda count: calls.append(count) or answer)
    exports = []
    monkeypatch.setattr(
        win._results_tab, "export_with_dialog", lambda: exports.append(True) or True
    )

    assert win._confirm_replace_results() is replaced
    assert calls == [1]
    assert bool(exports) is exported
    assert win._results.computed_row_count() == (0 if replaced else 1)
    # The Likert score is never part of what is replaced.
    assert any("likert_Alice" in r["metrics"] for r in win._results.rows())
    win.close()


def test_an_export_the_user_abandons_keeps_the_table(qapp, monkeypatch):
    win = MainWindow({})
    win._results.add_row(_row())
    monkeypatch.setattr(win, "_ask_replace_results", lambda count: "export")
    monkeypatch.setattr(win._results_tab, "export_with_dialog", lambda: False)
    assert win._confirm_replace_results() is False
    assert win._results.computed_row_count() == 1
    win.close()


def test_an_empty_table_needs_no_confirmation(qapp, monkeypatch):
    win = MainWindow({})
    monkeypatch.setattr(win, "_ask_replace_results", lambda count: pytest.fail("asked"))
    assert win._confirm_replace_results() is True
    win.close()


def test_compute_all_is_disabled_while_a_computation_runs(qapp):
    """A second click used to tear the running thread down mid-computation."""
    win = MainWindow({})
    win._compute_tab.set_running(True)
    assert not win._compute_tab._compute_btn.isEnabled()
    win._on_metrics_finished(0)
    assert win._compute_tab._compute_btn.isEnabled()
    win.close()


# ---- A different folder is a different piece of work ---------------------------


def _with_work(win, tmp_path):
    win._library = SimpleNamespace(root_folder=str(tmp_path / "cohort-a"))
    win._results.add_row(_row())
    _score(win)
    win._organ_assignments = {"Parotid_L": "parotid"}
    win._current_session_path = tmp_path / "cohort-a.session.json"


def test_rescanning_the_same_folder_keeps_everything(qapp, monkeypatch, tmp_path):
    win = MainWindow({})
    _with_work(win, tmp_path)
    monkeypatch.setattr(win, "_ask_discard_work", lambda *a: pytest.fail("asked"))
    assert win._confirm_folder_change(str(tmp_path / "cohort-a")) is True
    assert win._results.computed_row_count() == 1
    assert win._current_session_path is not None
    win.close()


def test_cancelling_a_folder_change_keeps_everything(qapp, monkeypatch, tmp_path):
    win = MainWindow({})
    _with_work(win, tmp_path)
    monkeypatch.setattr(win, "_ask_discard_work", lambda *a: "cancel")
    assert win._confirm_folder_change(str(tmp_path / "cohort-b")) is False
    assert win._results.computed_row_count() == 1
    assert win._organ_assignments
    win.close()


def test_a_confirmed_folder_change_clears_the_previous_cohorts_work(qapp, monkeypatch, tmp_path):
    """External audit: results, scores, organ labels and the session path all
    survived loading another folder, so Save could overwrite the old session."""
    win = MainWindow({})
    _with_work(win, tmp_path)
    monkeypatch.setattr(win, "_ask_discard_work", lambda *a: "discard")
    assert win._confirm_folder_change(str(tmp_path / "cohort-b")) is True
    assert win._results.rows() == []
    assert win._organ_assignments == {}
    assert win._current_session_path is None
    assert not win._qualitative_tab.has_scores()
    win.close()


def test_saving_first_is_offered_and_a_cancelled_save_stops_the_change(qapp, monkeypatch, tmp_path):
    win = MainWindow({})
    _with_work(win, tmp_path)
    monkeypatch.setattr(win, "_ask_discard_work", lambda *a: "save")
    monkeypatch.setattr(win, "_on_save_session", lambda: False)
    assert win._confirm_folder_change(str(tmp_path / "cohort-b")) is False
    assert win._results.computed_row_count() == 1
    win.close()


# ---- The session holds the computed table ---------------------------------------


def test_the_results_table_is_saved_with_the_session(qapp, tmp_path):
    win = MainWindow({})
    win._library = SimpleNamespace(root_folder=str(tmp_path), link_overrides={})
    win._results.add_row(_row(dice=0.81))
    path = tmp_path / "work.session.json"

    assert win._write_session_to(path) is True

    saved = load_session_file(path)
    assert saved["schema_version"] == 7
    assert saved["results"]["rows"][0]["metrics"]["dice"] == 0.81
    assert saved["results"]["rows"][0]["computed_at"] == "2026-09-20 10:00:00+08:00"
    win.close()


def test_restoring_a_session_brings_the_table_back(qapp, monkeypatch, tmp_path):
    win = MainWindow({})
    win._library = SimpleNamespace(root_folder=str(tmp_path), link_overrides={})
    win._results.add_row(_row(dice=0.81))
    path = tmp_path / "work.session.json"
    win._write_session_to(path)
    win.close()

    # No drawers can be rebuilt without a scanned folder, which the restore
    # reports in a dialog; answered here so the test does not block.
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    fresh = MainWindow({})
    fresh._library = SimpleNamespace(root_folder=str(tmp_path), link_overrides={})
    fresh._pending_session_restore = load_session_file(path)
    fresh._on_library_loaded_post_session()
    assert fresh._results.computed_row_count() == 1
    assert fresh._results.rows()[0]["metrics"]["dice"] == 0.81
    fresh.close()


# ---- Smaller findings -------------------------------------------------------


def test_staple_settings_are_kept_across_launches(qapp):
    """External audit: the configuration save handler left STAPLE out."""
    settings: dict = {}
    win = MainWindow(settings)
    config = win._compute_tab.config()
    config["staple"]["max_iterations"] = 77
    win._on_metric_config_changed(config)
    assert settings["staple"]["max_iterations"] == 77
    win.close()


def test_a_session_saved_without_an_extension_gets_one(qapp, monkeypatch, tmp_path):
    """External audit: the fallback passed "session", without a dot, to
    Path.with_suffix, which raises."""
    win = MainWindow({})
    win._library = SimpleNamespace(root_folder=str(tmp_path), link_overrides={})
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: (str(tmp_path / "study"), "")),
    )
    assert win._on_save_session_as() is True
    assert (tmp_path / "study.session.json").is_file()
    win.close()


def test_the_session_folder_path_is_compared_however_it_is_written(tmp_path):
    same = main_window_module._same_folder
    assert same(str(tmp_path), str(tmp_path) + os.sep)
    assert same(str(tmp_path / "a" / ".."), str(tmp_path))
    assert not same(str(tmp_path / "a"), str(tmp_path / "b"))


def test_nothing_else_named_like_the_old_fallback_remains():
    source = Path(main_window_module.__file__).read_text(encoding="utf-8")
    assert "with_suffix(DEFAULT_SUFFIX[1:]" not in source
