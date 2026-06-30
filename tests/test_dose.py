"""Tests for core.dose — RTDOSE loading (Gy scaling) + resampling onto a reference grid."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pydicom
import pytest
import SimpleITK as sitk
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, RTDoseStorage, generate_uid

from autoseg_evaluator.core.dose import (
    dose_array_on_reference,
    read_dose_image,
    resample_dose_to_reference,
)

ROWS, COLS, FRAMES = 16, 16, 8
SPACING = 2.0


def _sitk_image(arr_zyx, spacing=(1.0, 1.0, 1.0), origin=(0.0, 0.0, 0.0)) -> sitk.Image:
    im = sitk.GetImageFromArray(np.asarray(arr_zyx, dtype=np.float32))
    im.SetSpacing(spacing)
    im.SetOrigin(origin)
    return im


def _write_rtdose(folder: Path, *, units: str = "GY", scaling: float = 0.01) -> Path:
    """RTDOSE whose value (in its own DoseUnits) is the column index: 0 .. COLS-1."""
    sop_uid = generate_uid()
    meta = Dataset()
    meta.MediaStorageSOPClassUID = RTDoseStorage
    meta.MediaStorageSOPInstanceUID = sop_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(str(folder / "rtdose.dcm"), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = RTDoseStorage
    ds.SOPInstanceUID = sop_uid
    ds.Modality = "RTDOSE"
    ds.FrameOfReferenceUID = generate_uid()
    ds.ImagePositionPatient = [0.0, 0.0, 0.0]
    ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    ds.PixelSpacing = [SPACING, SPACING]
    ds.Rows = ROWS
    ds.Columns = COLS
    ds.NumberOfFrames = FRAMES
    ds.FrameIncrementPointer = (0x3004, 0x000C)
    ds.GridFrameOffsetVector = [float(i) * SPACING for i in range(FRAMES)]
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.DoseUnits = units
    ds.DoseType = "PHYSICAL"
    ds.DoseSummationType = "PLAN"
    ds.DoseGridScaling = scaling

    col = np.arange(COLS, dtype=np.float64)  # value (in DoseUnits) == column index
    frame = np.tile(col, (ROWS, 1))  # (rows, cols)
    grid = np.repeat(frame[None, :, :], FRAMES, axis=0)  # (frames, rows, cols)
    stored = np.round(grid / scaling).astype("<u2")
    ds.PixelData = stored.tobytes()
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    path = folder / "rtdose.dcm"
    pydicom.dcmwrite(str(path), ds, write_like_original=False)
    return path


# ---- Resampling ----------------------------------------------------------


def test_resample_places_dose_on_reference_grid_with_zero_fill():
    """The dose covers only a sub-box; outside that box the resample is 0."""
    ref = _sitk_image(np.zeros((20, 20, 20)), spacing=(1, 1, 1), origin=(0, 0, 0))
    dose = _sitk_image(np.full((5, 5, 5), 10.0), spacing=(1, 1, 1), origin=(5, 5, 5))
    out = resample_dose_to_reference(dose, ref)
    assert out.GetSize() == ref.GetSize()
    arr = sitk.GetArrayFromImage(out)
    assert arr.shape == (20, 20, 20)
    assert arr[7, 7, 7] == pytest.approx(10.0, abs=1e-4)  # inside the dose box
    assert arr[0, 0, 0] == 0.0  # outside → zero-filled
    assert arr[19, 19, 19] == 0.0


# ---- Reading (Gy scaling + units) ----------------------------------------


def test_read_dose_image_returns_gy_and_aligns_to_reference(tmp_path):
    path = _write_rtdose(tmp_path, units="GY", scaling=0.01)
    arr = sitk.GetArrayFromImage(read_dose_image(str(path)))
    assert arr.shape == (FRAMES, ROWS, COLS)
    # Column gradient in Gy: value == column index.
    assert arr[0, 0, 5] == pytest.approx(5.0, abs=1e-3)
    assert arr[3, 8, 12] == pytest.approx(12.0, abs=1e-3)

    # Onto a reference identical to the dose grid → values preserved.
    ref = _sitk_image(np.zeros((FRAMES, ROWS, COLS)), spacing=(SPACING, SPACING, SPACING))
    on_ref = dose_array_on_reference(str(path), ref)
    assert on_ref.shape == (FRAMES, ROWS, COLS)
    assert on_ref[0, 0, 5] == pytest.approx(5.0, abs=1e-3)


def test_read_dose_image_converts_cgy_to_gy(tmp_path):
    # File units are cGy (value == column index in cGy); Gy = cGy / 100.
    path = _write_rtdose(tmp_path, units="CGY", scaling=1.0)
    arr = sitk.GetArrayFromImage(read_dose_image(str(path)))
    assert arr[0, 0, 10] == pytest.approx(0.10, abs=1e-4)
