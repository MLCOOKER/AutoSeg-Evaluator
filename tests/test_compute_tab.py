"""Tests for Tab 3 (ComputeTab) — UI configuration and progress panel."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication

from autoseg_evaluator.ui.tabs.compute import ComputeTab, _parse_number_list  # noqa: E402
from autoseg_evaluator.ui.widgets.progress_panel import ProgressPanel, _format_hms  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---- _parse_number_list ---------------------------------------------------


def test_parse_number_list_empty_returns_empty():
    assert _parse_number_list("") == []
    assert _parse_number_list("   ") == []


def test_parse_number_list_basic():
    assert _parse_number_list("95, 50, 5, 2") == [95.0, 50.0, 5.0, 2.0]


def test_parse_number_list_accepts_semicolons_and_spaces():
    assert _parse_number_list("95; 50, 5") == [95.0, 50.0, 5.0]


def test_parse_number_list_strips_whitespace():
    assert _parse_number_list("  95 , 50  ") == [95.0, 50.0]


def test_parse_number_list_rejects_non_numeric():
    with pytest.raises(ValueError, match="Cannot parse"):
        _parse_number_list("95, foo, 5")


def test_parse_number_list_accepts_decimals():
    assert _parse_number_list("0.5, 1.5, 2.0") == [0.5, 1.5, 2.0]


# ---- ComputeTab -----------------------------------------------------------


def test_compute_tab_defaults_load_from_settings(qapp):
    settings = {
        "compute_geometric": {
            "dice": True,
            "hausdorff100": False,
            "hausdorff95": True,
            "mean_surface_distance": False,
            "surface_dice": True,
            "apl_mean": True,
            "apl_total": False,
        },
        "tolerances": {"surface_dice_tau_mm": 5.0, "apl_tolerance_mm": 2.5},
        "dvh": {
            "include_dmean": False,
            "include_dmax": True,
            "include_dmin": True,
            "d_at_volumes_pct": [99, 50],
            "v_at_doses_gy": [25],
        },
    }
    tab = ComputeTab(settings=settings)
    cfg = tab.config()
    assert cfg["geometric"]["dice"] is True
    assert cfg["geometric"]["hausdorff100"] is False
    assert cfg["tolerances"]["surface_dice_tau_mm"] == pytest.approx(5.0)
    assert cfg["tolerances"]["apl_tolerance_mm"] == pytest.approx(2.5)
    assert cfg["dvh"]["include_dmean"] is False
    assert cfg["dvh"]["include_dmin"] is True
    assert cfg["dvh"]["d_at_volumes_pct"] == [99.0, 50.0]
    assert cfg["dvh"]["v_at_doses_gy"] == [25.0]


def test_compute_tab_config_changed_signal_fires(qapp):
    tab = ComputeTab(settings={})
    received: list[dict] = []
    tab.metricConfigChanged.connect(received.append)
    # Toggle one checkbox
    tab._geom_checks["dice"].setChecked(not tab._geom_checks["dice"].isChecked())
    assert len(received) >= 1


def test_compute_tab_compute_clicked_emits_full_config(qapp):
    tab = ComputeTab(settings={})
    received: list[dict] = []
    tab.computeRequested.connect(received.append)
    tab._on_compute_clicked()
    assert len(received) == 1
    cfg = received[0]
    assert "geometric" in cfg
    assert "tolerances" in cfg
    assert "dvh" in cfg


def test_compute_tab_compute_rejects_invalid_dvh_input(qapp):
    tab = ComputeTab(settings={})
    tab._d_pct_edit.setText("95, abc, 5")
    received: list[dict] = []
    tab.computeRequested.connect(received.append)
    tab._on_compute_clicked()
    # No signal fired, validation label populated instead
    assert received == []
    assert "Cannot parse" in tab._validation_label.text()


def test_compute_tab_progress_panel_starts_hidden(qapp):
    tab = ComputeTab(settings={})
    assert tab._progress.isVisible() is False


def test_compute_tab_set_library_updates_dose_summary(qapp):
    """When a library is loaded the dose-summary label reports the dose-file count."""
    tab = ComputeTab(settings={})
    assert "No data loaded" in tab._dose_summary_label.text()

    # Build a minimal fake library object
    class FakeCtx:
        def __init__(self, n_dose):
            self.rtdoses = list(range(n_dose))

    class FakePatient:
        def __init__(self, n_dose):
            self.contexts = [FakeCtx(n_dose)]

    class FakeLibrary:
        patients = {"HN1": FakePatient(1), "HN2": FakePatient(0), "HN3": FakePatient(2)}

    tab.set_library(FakeLibrary())
    text = tab._dose_summary_label.text()
    assert "3 RTDOSE file(s)" in text
    assert "2/3 patient(s)" in text


# ---- ProgressPanel --------------------------------------------------------


def test_progress_panel_starts_hidden(qapp):
    p = ProgressPanel()
    assert p.isVisible() is False


def test_progress_panel_begin_shows_and_resets(qapp):
    p = ProgressPanel()
    p.begin(total_steps=10)
    assert p.isVisible() is True
    assert p._bar.maximum() == 10
    assert p._bar.value() == 0
    assert p._lbl_errors.text() == "0"


def test_progress_panel_updates_from_state(qapp):
    p = ProgressPanel()
    p.begin(total_steps=10)
    p.update_progress(
        3,
        10,
        {
            "patient": "HN1",
            "drawer": "Prostate",
            "gt": "manual.dcm",
            "test": "limbus.dcm (1 of 3)",
            "metric": "Surface Dice (2 of 7)",
            "drawers_done": 1,
            "drawers_total": 5,
            "errors": 2,
        },
    )
    assert p._bar.value() == 3
    assert p._lbl_patient.text() == "HN1"
    assert p._lbl_drawer.text() == "Prostate"
    assert p._lbl_gt.text() == "manual.dcm"
    assert p._lbl_done.text() == "1 / 5"
    assert p._lbl_errors.text() == "2"


def test_progress_panel_cancel_button_emits_signal(qapp):
    p = ProgressPanel()
    p.begin(total_steps=5)
    received: list[bool] = []
    p.cancelRequested.connect(lambda: received.append(True))
    p._cancel_btn.click()
    assert received == [True]


def test_progress_panel_finish_marks_done(qapp):
    p = ProgressPanel()
    p.begin(total_steps=5)
    p.update_progress(5, 5, {})
    p.finish(errors=3)
    assert p._lbl_errors.text() == "3"
    assert "Done" in p._bar.format()
    # Cancel button morphs into "Close"
    assert p._cancel_btn.text() == "Close"


def test_progress_panel_finish_cancelled(qapp):
    p = ProgressPanel()
    p.begin(total_steps=5)
    p.update_progress(2, 5, {})
    p.finish(cancelled=True)
    assert "Cancelled" in p._bar.format()
    assert p._cancel_btn.text() == "Cancelled"


# ---- _format_hms helper --------------------------------------------------


def test_format_hms_zero():
    assert _format_hms(0) == "0:00:00"


def test_format_hms_seconds_only():
    assert _format_hms(45) == "0:00:45"


def test_format_hms_with_minutes():
    assert _format_hms(125) == "0:02:05"


def test_format_hms_with_hours():
    assert _format_hms(3725) == "1:02:05"
