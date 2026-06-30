"""Tests for the visualisation dialog's optional dose-overlay toggle."""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest
import SimpleITK as sitk

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from autoseg_evaluator.ui.dialogs.visualization import VisualizationWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _u8(arr) -> sitk.Image:
    return sitk.GetImageFromArray(np.asarray(arr, dtype=np.uint8))


def _ct_gt_tests():
    ct = sitk.GetImageFromArray(np.zeros((6, 16, 16), dtype=np.int16))
    gt = np.zeros((6, 16, 16), dtype=np.uint8)
    gt[3, 6:10, 6:10] = 1
    test = np.zeros((6, 16, 16), dtype=np.uint8)
    test[3, 7:11, 7:11] = 1
    return ct, _u8(gt), [("vendor — Eye_L", _u8(test))]


def test_dose_overlay_disabled_when_no_dose(qapp):
    ct, gt, tests = _ct_gt_tests()
    win = VisualizationWindow(ct, gt, "GT — Eye_L", tests, title="t", dose_arr=None)
    assert win._dose_arr is None
    assert win._dose_checkbox.isEnabled() is False
    win.close()


def test_dose_overlay_ignored_when_all_zero(qapp):
    ct, gt, tests = _ct_gt_tests()
    dose = np.zeros((6, 16, 16), dtype=np.float32)  # no positive dose anywhere
    win = VisualizationWindow(ct, gt, "GT — Eye_L", tests, title="t", dose_arr=dose)
    assert win._dose_arr is None
    assert win._dose_checkbox.isEnabled() is False
    win.close()


def test_dose_overlay_enabled_and_toggles(qapp):
    ct, gt, tests = _ct_gt_tests()
    dose = np.zeros((6, 16, 16), dtype=np.float32)
    dose[3, 4:12, 4:12] = 30.0  # 30 Gy block on the GT slice
    win = VisualizationWindow(ct, gt, "GT — Eye_L", tests, title="t", dose_arr=dose)

    assert win._dose_arr is not None
    assert win._dose_checkbox.isEnabled() is True
    assert win._dose_vmax == pytest.approx(30.0)
    assert win._dose_visible is False
    # Colorbar axes exists but is hidden until the overlay is shown.
    assert win._dose_cax is not None
    assert win._dose_cax.get_visible() is False

    # Toggle on → visible, colorbar shown, no exception during redraw.
    win._dose_checkbox.setChecked(True)
    assert win._dose_visible is True
    assert win._dose_cax.get_visible() is True

    # Toggle off → colorbar hidden again.
    win._dose_checkbox.setChecked(False)
    assert win._dose_visible is False
    assert win._dose_cax.get_visible() is False
    win.close()


def test_dose_opacity_slider_changes_alpha(qapp):
    ct, gt, tests = _ct_gt_tests()
    dose = np.zeros((6, 16, 16), dtype=np.float32)
    dose[3, 4:12, 4:12] = 30.0
    win = VisualizationWindow(ct, gt, "GT — Eye_L", tests, title="t", dose_arr=dose)
    win._dose_checkbox.setChecked(True)
    win._dose_opacity.setValue(70)
    assert win._dose_alpha == pytest.approx(0.70, abs=1e-6)
    win.close()
