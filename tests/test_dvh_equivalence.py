"""Bit-for-bit equivalence of AutoSeg's DVH statistics vs dicompyler-core.

AutoSeg's :func:`autoseg_evaluator.core.dvh.compute_dvh_metrics` drives
``dicompylercore.dvhcalc`` to compute per-structure dose statistics. This
test builds a synthetic CT + single-ROI RTSTRUCT + analytic gradient
RTDOSE (so the structure spans a real dose range — Dmin < Dmean < Dmax,
partial V20Gy) and asserts that every statistic AutoSeg reports equals the
value obtained by querying dicompyler-core's public API directly.

The dose grid is a pure mathematical function generated from the geometry,
so the test carries no patient data and is fully reproducible.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pydicom
import pytest
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import (
    CTImageStorage,
    ExplicitVRLittleEndian,
    RTDoseStorage,
    RTStructureSetStorage,
    generate_uid,
)

from autoseg_evaluator.core.dvh import DVHConfig, _find_roi_contour, compute_dvh_metrics

pytest.importorskip("dicompylercore")

CT_ROWS = 32
CT_COLS = 32
CT_SLICES = 14
SPACING_XY = 2.0
SLICE_THICK = 2.0
MAX_DOSE_GY = 40.0
DOSE_SCALING = 0.001  # stored uint16 * scaling = Gy

CONFIG = DVHConfig(
    include_dmin=True,
    include_dmean=True,
    include_dmax=True,
    d_at_volumes_pct=[95.0, 50.0, 2.0],
    d_at_volumes_cc=[0.1, 2.0],
    v_at_doses_gy=[20.0, 30.0],
)


def _new_meta(sop_class_uid: str, sop_uid: str) -> Dataset:
    meta = Dataset()
    meta.MediaStorageSOPClassUID = sop_class_uid
    meta.MediaStorageSOPInstanceUID = sop_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    return meta


def _write_ct_series(folder: Path, *, study_uid: str, for_uid: str, series_uid: str) -> list[str]:
    pixel = np.zeros((CT_ROWS, CT_COLS), dtype=np.uint16)
    sop_uids: list[str] = []
    for z_idx in range(CT_SLICES):
        sop_uid = generate_uid()
        sop_uids.append(sop_uid)
        meta = _new_meta(CTImageStorage, sop_uid)
        ds = FileDataset(
            str(folder / f"ct_{z_idx:03d}.dcm"), {}, file_meta=meta, preamble=b"\0" * 128
        )
        ds.PatientID = "TEST"
        ds.PatientName = "Test^Patient"
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = series_uid
        ds.SOPInstanceUID = sop_uid
        ds.SOPClassUID = CTImageStorage
        ds.FrameOfReferenceUID = for_uid
        ds.Modality = "CT"
        ds.SeriesNumber = 1
        ds.InstanceNumber = z_idx + 1
        ds.ImagePositionPatient = [0.0, 0.0, float(z_idx) * SLICE_THICK]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.PixelSpacing = [SPACING_XY, SPACING_XY]
        ds.SliceThickness = SLICE_THICK
        ds.Rows = CT_ROWS
        ds.Columns = CT_COLS
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.PixelRepresentation = 0
        ds.RescaleIntercept = -1024
        ds.RescaleSlope = 1
        ds.PixelData = pixel.tobytes()
        ds.is_little_endian = True
        ds.is_implicit_VR = False
        pydicom.dcmwrite(str(folder / f"ct_{z_idx:03d}.dcm"), ds, write_like_original=False)
    return sop_uids


def _square_xy(cx: float, cy: float, half_w: float) -> list[float]:
    pts = [
        (cx - half_w, cy - half_w),
        (cx + half_w, cy - half_w),
        (cx + half_w, cy + half_w),
        (cx - half_w, cy + half_w),
    ]
    out: list[float] = []
    for x, y in pts:
        out.extend([float(x), float(y)])
    return out


def _write_rtstruct(
    folder: Path,
    *,
    study_uid: str,
    for_uid: str,
    ct_sop_uids: list[str],
    planes: tuple[int, ...] = (4, 5, 6, 7, 8, 9, 10),
    fname: str = "rtss.dcm",
    center: tuple[float, float] = (30.0, 30.0),
    half_w: float = 16.0,
) -> Path:
    """One solid square ROI of half-width ``half_w`` mm at ``center`` over ``planes``."""
    sop_uid = generate_uid()
    meta = _new_meta(RTStructureSetStorage, sop_uid)
    ds = FileDataset(str(folder / fname), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.PatientID = "TEST"
    ds.StudyInstanceUID = study_uid
    ds.SOPInstanceUID = sop_uid
    ds.SOPClassUID = RTStructureSetStorage
    ds.Modality = "RTSTRUCT"
    ds.SeriesInstanceUID = generate_uid()
    ds.StructureSetLabel = "TestSet"

    for_item = Dataset()
    for_item.FrameOfReferenceUID = for_uid
    ds.ReferencedFrameOfReferenceSequence = [for_item]

    roi = Dataset()
    roi.ROINumber = 1
    roi.ROIName = "target"
    roi.ReferencedFrameOfReferenceUID = for_uid
    roi.ROIGenerationAlgorithm = ""
    ds.StructureSetROISequence = [roi]

    rc = Dataset()
    rc.ReferencedROINumber = 1
    rc.ROIDisplayColor = [255, 0, 0]
    rc.ContourSequence = []
    for z in planes:
        item = Dataset()
        item.ContourGeometricType = "CLOSED_PLANAR"
        item.NumberOfContourPoints = 4
        coords2d = _square_xy(center[0], center[1], half_w)
        data: list[float] = []
        for i in range(0, len(coords2d), 2):
            data.extend([coords2d[i], coords2d[i + 1], float(z) * SLICE_THICK])
        item.ContourData = data
        image_ref = Dataset()
        image_ref.ReferencedSOPClassUID = CTImageStorage
        image_ref.ReferencedSOPInstanceUID = ct_sop_uids[z]
        item.ContourImageSequence = [image_ref]
        rc.ContourSequence.append(item)
    ds.ROIContourSequence = [rc]

    obs = Dataset()
    obs.ObservationNumber = 1
    obs.ReferencedROINumber = 1
    obs.RTROIInterpretedType = "ORGAN"
    obs.ROIInterpreter = ""
    ds.RTROIObservationsSequence = [obs]

    ds.is_little_endian = True
    ds.is_implicit_VR = False
    path = folder / fname
    pydicom.dcmwrite(str(path), ds, write_like_original=False)
    return path


def _write_rtdose(folder: Path, *, study_uid: str, for_uid: str) -> Path:
    """Analytic dose: a linear gradient along x (columns), 0 .. MAX_DOSE_GY."""
    sop_uid = generate_uid()
    meta = _new_meta(RTDoseStorage, sop_uid)
    ds = FileDataset(str(folder / "rtdose.dcm"), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.PatientID = "TEST"
    ds.PatientName = "Test^Patient"
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = generate_uid()
    ds.SOPInstanceUID = sop_uid
    ds.SOPClassUID = RTDoseStorage
    ds.Modality = "RTDOSE"
    ds.FrameOfReferenceUID = for_uid
    ds.ImagePositionPatient = [0.0, 0.0, 0.0]
    ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    ds.PixelSpacing = [SPACING_XY, SPACING_XY]
    ds.Rows = CT_ROWS
    ds.Columns = CT_COLS
    ds.NumberOfFrames = CT_SLICES
    ds.FrameIncrementPointer = (0x3004, 0x000C)
    ds.GridFrameOffsetVector = [float(i) * SLICE_THICK for i in range(CT_SLICES)]
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.DoseUnits = "GY"
    ds.DoseType = "PHYSICAL"
    ds.DoseSummationType = "PLAN"
    ds.DoseGridScaling = DOSE_SCALING

    # dose_gy[z, y, x] = MAX_DOSE_GY * x / (cols - 1) — varies across the ROI.
    col_gy = MAX_DOSE_GY * (np.arange(CT_COLS, dtype=np.float64) / (CT_COLS - 1))
    frame = np.tile(col_gy, (CT_ROWS, 1))  # (rows, cols)
    grid_gy = np.repeat(frame[None, :, :], CT_SLICES, axis=0)  # (frames, rows, cols)
    stored = np.round(grid_gy / DOSE_SCALING).astype("<u2")
    ds.PixelData = stored.tobytes()

    ds.is_little_endian = True
    ds.is_implicit_VR = False
    path = folder / "rtdose.dcm"
    pydicom.dcmwrite(str(path), ds, write_like_original=False)
    return path


def _write_rtdose_zgrad(folder: Path, *, study_uid: str, for_uid: str) -> Path:
    """Analytic dose increasing with z (frame index), 0 .. MAX_DOSE_GY.

    Constant within each axial plane, so z-truncating a structure visibly
    changes Dmax/Dmean (unlike the in-plane gradient used elsewhere).
    """
    path = _write_rtdose(folder, study_uid=study_uid, for_uid=for_uid)
    ds = pydicom.dcmread(str(path))
    z_gy = MAX_DOSE_GY * (np.arange(CT_SLICES, dtype=np.float64) / (CT_SLICES - 1))
    grid_gy = np.repeat(z_gy[:, None, None], CT_ROWS, axis=1)
    grid_gy = np.repeat(grid_gy, CT_COLS, axis=2)  # (frames, rows, cols)
    ds.PixelData = np.round(grid_gy / DOSE_SCALING).astype("<u2").tobytes()
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    pydicom.dcmwrite(str(path), ds, write_like_original=False)
    return path


def _reference_dvh(rtss_ds, dose_ds, roi_number: int) -> dict[str, float]:
    """Query dicompyler-core's public API directly (the reference path)."""
    from dicompylercore import dvhcalc

    dvh = dvhcalc.get_dvh(rtss_ds, dose_ds, roi_number, calculate_full_volume=True)

    def stat(expr: str) -> float:
        try:
            s = dvh.statistic(expr)
        except Exception:  # noqa: BLE001
            return math.nan
        return float(getattr(s, "value", s))

    return {
        "dmin_gy": float(dvh.min),
        "dmean_gy": float(dvh.mean),
        "dmax_gy": float(dvh.max),
        "d95_gy": stat("D95"),
        "d50_gy": stat("D50"),
        "d2_gy": stat("D2"),
        "d0.1cc_gy": stat("D0.1cc"),
        "d2cc_gy": stat("D2cc"),
        "v20gy_cc": stat("V20Gy"),
        "v30gy_cc": stat("V30Gy"),
    }


@pytest.fixture(scope="module")
def dvh_fixture(tmp_path_factory):
    folder = tmp_path_factory.mktemp("dvh_equiv")
    study_uid = generate_uid()
    for_uid = generate_uid()
    series_uid = generate_uid()
    ct_sop_uids = _write_ct_series(
        folder, study_uid=study_uid, for_uid=for_uid, series_uid=series_uid
    )
    rtss_path = _write_rtstruct(
        folder, study_uid=study_uid, for_uid=for_uid, ct_sop_uids=ct_sop_uids
    )
    dose_path = _write_rtdose(folder, study_uid=study_uid, for_uid=for_uid)
    rtss_ds = pydicom.dcmread(str(rtss_path))
    dose_ds = pydicom.dcmread(str(dose_path))
    dose_ds.filename = str(dose_path)
    return rtss_ds, dose_ds


def test_dvh_curve_is_nontrivial(dvh_fixture):
    """The gradient dose must give a real spread, else equivalence is vacuous."""
    rtss_ds, dose_ds = dvh_fixture
    ref = _reference_dvh(rtss_ds, dose_ds, 1)
    assert ref["dmax_gy"] > ref["dmean_gy"] > ref["dmin_gy"] > 0.0
    assert ref["v20gy_cc"] > 0.0  # some volume above 20 Gy
    assert ref["d2_gy"] > ref["d95_gy"]  # hot-spot above cold-spot


def test_dvh_metrics_match_dicompyler(dvh_fixture):
    rtss_ds, dose_ds = dvh_fixture
    autoseg = compute_dvh_metrics(rtss_ds, dose_ds, 1, CONFIG)
    reference = _reference_dvh(rtss_ds, dose_ds, 1)

    assert set(autoseg) == set(reference), "output keys differ from the reference set"
    for key in CONFIG.output_keys():
        a = float(autoseg[key])
        b = float(reference[key])
        if math.isnan(a) and math.isnan(b):
            continue
        assert a == b, f"{key} differs: AutoSeg={a} dicompyler={b}"


@pytest.fixture(scope="module")
def single_slice_fixture(tmp_path_factory):
    """A ROI contoured on exactly one slice — dicompyler's blind spot."""
    folder = tmp_path_factory.mktemp("dvh_single")
    study_uid = generate_uid()
    for_uid = generate_uid()
    series_uid = generate_uid()
    ct_sop_uids = _write_ct_series(
        folder, study_uid=study_uid, for_uid=for_uid, series_uid=series_uid
    )
    rtss_path = _write_rtstruct(
        folder, study_uid=study_uid, for_uid=for_uid, ct_sop_uids=ct_sop_uids, planes=(6,)
    )
    dose_path = _write_rtdose(folder, study_uid=study_uid, for_uid=for_uid)
    rtss_ds = pydicom.dcmread(str(rtss_path))
    dose_ds = pydicom.dcmread(str(dose_path))
    dose_ds.filename = str(dose_path)
    return rtss_ds, dose_ds


def test_plain_dicompyler_drops_single_slice_roi(single_slice_fixture):
    """Confirms the underlying limitation: without an explicit thickness,
    dicompyler gives a single-slice structure zero volume (no DVH)."""
    from dicompylercore import dvhcalc

    rtss_ds, dose_ds = single_slice_fixture
    dvh = dvhcalc.get_dvh(rtss_ds, dose_ds, 1, calculate_full_volume=True)
    assert dvh is None or getattr(dvh, "volume", 0) == 0


def test_autoseg_computes_single_slice_dvh(single_slice_fixture):
    """AutoSeg's thickness fallback recovers a DVH for the single-slice ROI."""
    rtss_ds, dose_ds = single_slice_fixture
    out = compute_dvh_metrics(rtss_ds, dose_ds, 1, CONFIG)
    assert out, "single-slice ROI should now yield DVH metrics"
    assert out["dmax_gy"] >= out["dmean_gy"] >= out["dmin_gy"] > 0.0
    # Volume-based stats are populated too (not blank/NaN).
    assert not math.isnan(out["d2cc_gy"])


# --------------------- z-extent (truncation) DVH ---------------------------


@pytest.fixture(scope="module")
def zgrad_fixture(tmp_path_factory):
    """CT + 7-slice ROI (planes 4..10) + a dose that increases with z."""
    folder = tmp_path_factory.mktemp("dvh_zgrad")
    study_uid = generate_uid()
    for_uid = generate_uid()
    series_uid = generate_uid()
    ct_sop_uids = _write_ct_series(
        folder, study_uid=study_uid, for_uid=for_uid, series_uid=series_uid
    )
    rtss_path = _write_rtstruct(
        folder, study_uid=study_uid, for_uid=for_uid, ct_sop_uids=ct_sop_uids
    )
    dose_path = _write_rtdose_zgrad(folder, study_uid=study_uid, for_uid=for_uid)
    rtss_ds = pydicom.dcmread(str(rtss_path))
    dose_ds = pydicom.dcmread(str(dose_path))
    dose_ds.filename = str(dose_path)
    return rtss_ds, dose_ds


def test_z_extent_none_is_identical_to_omitted(zgrad_fixture):
    """Passing z_extent_mm=None must not change the result — preserves the
    bit-for-bit dicompyler equivalence for the (default) untruncated path."""
    rtss_ds, dose_ds = zgrad_fixture
    omitted = compute_dvh_metrics(rtss_ds, dose_ds, 1, CONFIG)
    explicit_none = compute_dvh_metrics(rtss_ds, dose_ds, 1, CONFIG, z_extent_mm=None)
    assert omitted == explicit_none


def test_z_extent_truncation_lowers_low_z_dose(zgrad_fixture):
    """Restricting a z-increasing dose structure to its lower slices drops the
    dose statistics — i.e. the DVH respects the contour-plane truncation."""
    rtss_ds, dose_ds = zgrad_fixture
    full = compute_dvh_metrics(rtss_ds, dose_ds, 1, CONFIG)
    # ROI spans z = 8..20 mm (planes 4..10). Keep only the lower planes (4,5,6).
    trunc = compute_dvh_metrics(rtss_ds, dose_ds, 1, CONFIG, z_extent_mm=(7.0, 13.0))
    assert trunc["dmax_gy"] < full["dmax_gy"]
    assert trunc["dmean_gy"] < full["dmean_gy"]
    assert trunc["dmax_gy"] > 0.0


def test_z_extent_truncation_restores_dataset(zgrad_fixture):
    """The temporary contour-plane filtering is undone after the call."""
    rtss_ds, dose_ds = zgrad_fixture
    rc = _find_roi_contour(rtss_ds, 1)
    n_before = len(rc.ContourSequence)
    compute_dvh_metrics(rtss_ds, dose_ds, 1, CONFIG, z_extent_mm=(7.0, 13.0))
    assert len(rc.ContourSequence) == n_before


# ---------- sub-dose-grid (tiny) structure recovery via supersampling ----------


@pytest.fixture(scope="module")
def tiny_structure_fixture(tmp_path_factory):
    """A 0.8 mm structure centred between the 2 mm dose-grid sample points — it
    contains no dose voxel centre, so plain dicompyler rasterises it to nothing."""
    folder = tmp_path_factory.mktemp("dvh_tiny")
    study_uid = generate_uid()
    for_uid = generate_uid()
    series_uid = generate_uid()
    ct_sop_uids = _write_ct_series(
        folder, study_uid=study_uid, for_uid=for_uid, series_uid=series_uid
    )
    rtss_path = _write_rtstruct(
        folder,
        study_uid=study_uid,
        for_uid=for_uid,
        ct_sop_uids=ct_sop_uids,
        planes=(5, 6, 7),
        center=(3.0, 3.0),
        half_w=0.4,
    )
    dose_path = _write_rtdose(folder, study_uid=study_uid, for_uid=for_uid)
    rtss_ds = pydicom.dcmread(str(rtss_path))
    dose_ds = pydicom.dcmread(str(dose_path))
    dose_ds.filename = str(dose_path)
    return rtss_ds, dose_ds


def test_plain_dicompyler_drops_subgrid_structure(tiny_structure_fixture):
    """Confirms the cause: a structure smaller than the dose grid spacing
    rasterises to zero volume on the native grid (no DVH)."""
    from dicompylercore import dvhcalc

    rtss_ds, dose_ds = tiny_structure_fixture
    dvh = dvhcalc.get_dvh(rtss_ds, dose_ds, 1, calculate_full_volume=True)
    assert getattr(dvh, "volume", 0) == 0


def test_supersampling_recovers_subgrid_structure(tiny_structure_fixture):
    """compute_dvh_metrics retries on a supersampled grid so the tiny structure
    still yields dose statistics instead of being silently dropped."""
    rtss_ds, dose_ds = tiny_structure_fixture
    out = compute_dvh_metrics(rtss_ds, dose_ds, 1, CONFIG)
    assert out, "sub-grid structure should be recovered, not dropped"
    # Recovered with positive, finite dose (all ~equal for this sub-voxel block).
    # (Don't assert mean >= min: dicompyler's cumulative-integral mean can dip a
    # hair below the histogram min for a degenerate sub-voxel structure.)
    assert out["dmax_gy"] >= out["dmin_gy"] > 0.0
    assert out["dmean_gy"] > 0.0
