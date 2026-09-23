"""The Matching tab's visualise popup: the shared viewer, plus legend and dose."""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest
import SimpleITK as sitk

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from autoseg_evaluator.ui.dialogs.visualization import VisualizationWindow  # noqa: E402
from autoseg_evaluator.ui.widgets._palette import GT_COLOR  # noqa: E402
from autoseg_evaluator.ui.widgets.multiplanar_viewer import (  # noqa: E402
    CORONAL,
    MultiPlanarViewer,
)


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


def _block_dose():
    dose = np.zeros((6, 16, 16), dtype=np.float32)
    dose[3, 4:12, 4:12] = 30.0  # 30 Gy block on the GT slice
    return dose


# ---- The viewer it hosts ---------------------------------------------------


def test_it_uses_the_qualitative_tabs_viewer(qapp):
    """Level/window, zoom and the three planes come with it."""
    ct, gt, tests = _ct_gt_tests()
    win = VisualizationWindow(ct, gt, "GT — Eye_L", tests, title="t")

    assert isinstance(win._viewer, MultiPlanarViewer)
    assert win._viewer._level_slider.maximum() >= win._viewer._level_slider.minimum()
    win._viewer.set_plane(CORONAL)
    assert win._viewer._plane == CORONAL
    win.close()


def test_it_opens_on_the_ground_truth_and_draws_it_last(qapp):
    ct, gt, tests = _ct_gt_tests()
    win = VisualizationWindow(ct, gt, "GT — Eye_L", tests, title="t")

    assert win._viewer._slice == 3, "the ground truth's middle slice"
    assert win._overlays[-1].color == GT_COLOR, "drawn over every test contour"
    assert not any(o.active for o in win._overlays), "no test contour is dimmed"
    win.close()


def test_the_legend_turns_each_source_off_and_on(qapp):
    ct, gt, tests = _ct_gt_tests()
    win = VisualizationWindow(ct, gt, "GT — Eye_L", tests, title="t")

    ground_truth_row = win._legend_rows[0]
    ground_truth_row.checkbox.setChecked(False)
    assert win._viewer._overlays[win._gt_index].visible is False
    ground_truth_row.checkbox.setChecked(True)
    assert win._viewer._overlays[win._gt_index].visible is True
    win.close()


# ---- Dose -----------------------------------------------------------------


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
    win = VisualizationWindow(ct, gt, "GT — Eye_L", tests, title="t", dose_arr=_block_dose())

    assert win._dose_arr is not None
    assert win._dose_checkbox.isEnabled() is True
    assert win._dose_vmax == pytest.approx(30.0)
    assert win._dose_visible is False
    # The scale is hidden until the wash is shown.
    assert win._dose_scale.isHidden()

    win._dose_checkbox.setChecked(True)
    assert win._dose_visible is True
    assert win._viewer._dose_visible is True
    assert not win._dose_scale.isHidden()

    win._dose_checkbox.setChecked(False)
    assert win._dose_visible is False
    assert win._dose_scale.isHidden()
    win.close()


def test_dose_opacity_slider_changes_alpha(qapp):
    ct, gt, tests = _ct_gt_tests()
    win = VisualizationWindow(ct, gt, "GT — Eye_L", tests, title="t", dose_arr=_block_dose())
    win._dose_checkbox.setChecked(True)
    win._dose_opacity.setValue(70)
    assert win._dose_alpha == pytest.approx(0.70, abs=1e-6)
    assert win._viewer._dose_opacity == pytest.approx(0.70, abs=1e-6)
    win.close()
