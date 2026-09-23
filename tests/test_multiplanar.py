"""Tests for the multiplanar viewer — pure reslice helpers + a construction smoke test."""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest
import SimpleITK as sitk

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from autoseg_evaluator.ui.widgets.multiplanar_viewer import (  # noqa: E402
    AXIAL,
    CORONAL,
    SAGITTAL,
    MultiPlanarViewer,
    Overlay,
    _mask_to_path,
    n_slices,
    plane_pixel_size,
    reslice,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---- Pure helpers --------------------------------------------------------


def test_n_slices_per_plane():
    vol = np.zeros((4, 5, 6))  # (z, y, x)
    assert n_slices(vol, AXIAL) == 4
    assert n_slices(vol, CORONAL) == 5
    assert n_slices(vol, SAGITTAL) == 6


def test_reslice_shapes():
    vol = np.zeros((4, 5, 6))
    assert reslice(vol, AXIAL, 0).shape == (5, 6)
    assert reslice(vol, CORONAL, 0).shape == (4, 6)
    assert reslice(vol, SAGITTAL, 0).shape == (4, 5)


def test_reslice_clamps_index():
    vol = np.arange(4 * 5 * 6).reshape(4, 5, 6)
    # Out-of-range index clamps to the last slice rather than erroring.
    assert np.array_equal(reslice(vol, AXIAL, 999), vol[3])


def test_coronal_and_sagittal_flipped_superior_on_top():
    # vol[z, y, x] = z, so superior (high z) must render at the TOP row.
    z = 4
    vol = np.broadcast_to(np.arange(z).reshape(z, 1, 1), (z, 5, 6)).astype(float)
    cor = reslice(vol, CORONAL, 2)  # (z, x), flipped
    assert cor[0, 0] == z - 1  # top row = most superior
    assert cor[-1, 0] == 0  # bottom row = most inferior
    sag = reslice(vol, SAGITTAL, 3)  # (z, y), flipped
    assert sag[0, 0] == z - 1
    # Axial is unchanged.
    assert reslice(vol, AXIAL, 1)[0, 0] == 1


def test_plane_pixel_size_uses_correct_spacings():
    spacing = (1.0, 2.0, 3.0)  # (sx, sy, sz)
    assert plane_pixel_size(spacing, AXIAL) == (1.0, 2.0)  # (x, y)
    assert plane_pixel_size(spacing, CORONAL) == (1.0, 3.0)  # (x, z)
    assert plane_pixel_size(spacing, SAGITTAL) == (2.0, 3.0)  # (y, z)


def test_mask_to_path_nonempty_for_a_square():
    mask = np.zeros((20, 20), dtype=bool)
    mask[5:15, 5:15] = True
    path = _mask_to_path(mask)
    assert not path.isEmpty()


def test_mask_to_path_empty_for_blank():
    assert _mask_to_path(np.zeros((20, 20), dtype=bool)).isEmpty()


# ---- Viewer smoke test ---------------------------------------------------


def _ct_and_overlay():
    ct = sitk.GetImageFromArray(
        (np.random.default_rng(0).random((8, 16, 16)) * 1000).astype(np.int16)
    )
    ct.SetSpacing((1.0, 1.0, 3.0))
    mask = np.zeros((8, 16, 16), dtype=bool)
    mask[3:6, 5:11, 5:11] = True
    return ct, [Overlay(label="VendorA", color="#E41A1C", mask=mask, active=True)]


def test_viewer_loads_cycles_and_toggles(qapp):
    ct, overlays = _ct_and_overlay()
    v = MultiPlanarViewer()
    v.set_data(ct, overlays)
    # Slider spans the axial slices.
    assert v._slice_slider.maximum() == 7
    # Cycle through planes without error.
    v.cycle_plane(1)
    assert v._plane == CORONAL
    v.cycle_plane(1)
    assert v._plane == SAGITTAL
    v.set_plane(AXIAL)
    assert v._plane == AXIAL
    # Visibility + active toggles re-render without error.
    v.set_overlay_visible(0, False)
    v.set_active_overlay(None)
    v.set_active_overlay(0)
    v.deleteLater()


def test_set_plane_keeps_the_plane_buttons_in_step(qapp):
    ct, overlays = _ct_and_overlay()
    v = MultiPlanarViewer()
    v.set_data(ct, overlays)
    v.set_plane(SAGITTAL)
    checked = [b.text() for b in v._plane_group.buttons() if b.isChecked()]
    assert checked == ["Sagittal (S)"]
    v.deleteLater()


def test_a_focus_overlay_picks_the_opening_slice_without_highlighting(qapp):
    ct, overlays = _ct_and_overlay()
    overlays[0].active = False
    v = MultiPlanarViewer()
    v.set_data(ct, overlays, focus=0)
    assert v._slice == 4  # middle of z 3..5
    assert not overlays[0].active
    v.deleteLater()


# ---- Fitting the image to the view ----------------------------------------


def _image_share_of_view(v) -> float:
    """How much of the viewport the image spans along its tighter axis."""
    shown = v._view.mapFromScene(v._pixmap_item.sceneBoundingRect()).boundingRect()
    port = v._view.viewport().rect()
    return max(shown.width() / port.width(), shown.height() / port.height())


def test_data_loaded_before_the_viewer_is_shown_still_opens_fitted(qapp):
    """The Matching popup loads its CT while being built, before it is on screen.

    Fitting then used the view's placeholder size, and the image opened far
    too small until Reset view was pressed.
    """
    ct, overlays = _ct_and_overlay()
    v = MultiPlanarViewer()
    v.set_data(ct, overlays)
    v.resize(700, 700)
    v.show()
    qapp.processEvents()

    assert _image_share_of_view(v) == pytest.approx(1.0, abs=0.05)
    v.close()


def test_a_zoom_the_user_made_survives_a_resize(qapp):
    ct, overlays = _ct_and_overlay()
    v = MultiPlanarViewer()
    v.set_data(ct, overlays)
    v.resize(600, 600)
    v.show()
    qapp.processEvents()

    v._view.scale(2.0, 2.0)
    v._view.zoomed.emit()
    zoom = v._view.transform().m11()
    v.resize(900, 900)
    qapp.processEvents()

    assert v._view.transform().m11() == pytest.approx(zoom)
    v.close()


# ---- Dose wash ------------------------------------------------------------


def test_a_dose_that_does_not_fit_the_ct_is_refused(qapp):
    ct, overlays = _ct_and_overlay()
    v = MultiPlanarViewer()
    v.set_data(ct, overlays)
    assert v.set_dose(np.ones((2, 2, 2), dtype=np.float32)) is False
    assert v.set_dose(np.zeros((8, 16, 16), dtype=np.float32)) is False
    assert v.set_dose(None) is False
    assert v.dose_max == 0.0
    v.deleteLater()


def test_the_dose_wash_sits_between_the_ct_and_the_contours(qapp):
    from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsPixmapItem

    ct, overlays = _ct_and_overlay()
    dose = np.zeros((8, 16, 16), dtype=np.float32)
    dose[3:6, 2:14, 2:14] = 50.0
    v = MultiPlanarViewer()
    v.set_data(ct, overlays)
    assert v.set_dose(dose) is True
    assert v.dose_max == pytest.approx(50.0)

    v.set_dose_visible(True)
    children = v._pixmap_item.childItems()
    washes = [c for c in children if isinstance(c, QGraphicsPixmapItem)]
    lines = [c for c in children if isinstance(c, QGraphicsPathItem)]
    assert len(washes) == 1 and lines
    assert all(line.zValue() > washes[0].zValue() for line in lines)

    v.set_dose_visible(False)
    assert not [c for c in v._pixmap_item.childItems() if isinstance(c, QGraphicsPixmapItem)]
    v.deleteLater()


def test_low_dose_is_not_washed_in():
    """Below 5% of the maximum the wash is transparent, so air is not tinted."""
    from autoseg_evaluator.ui.widgets.multiplanar_viewer import _dose_wash

    dose = np.array([[0.0, 1.0, 40.0]], dtype=np.float32)  # 1 Gy is 2.5% of 40
    image = _dose_wash(dose, 40.0, 0.5)
    alphas = [image.pixelColor(x, 0).alpha() for x in range(3)]
    assert alphas[0] == 0 and alphas[1] == 0
    assert alphas[2] == pytest.approx(128, abs=1)
