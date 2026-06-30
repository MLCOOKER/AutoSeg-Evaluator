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
