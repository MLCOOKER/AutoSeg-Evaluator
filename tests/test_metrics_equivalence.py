"""Bit-for-bit equivalence of AutoSeg metric implementations vs upstream.

* Volumetric Dice, Hausdorff 100% / 95%, Surface Dice @ 3 mm, mean
  surface distance — compared against
  ``google-deepmind/surface-distance`` (the upstream of AutoSeg's
  embedded port).
* Total APL, Mean APL @ 3 mm — compared against
  ``platipy.imaging.label.comparison`` (the original of AutoSeg's
  ``apl_total`` / ``apl_mean`` ports).

The test builds a tiny synthetic CT + a single RTSTRUCT carrying two
ROIs ("GT" and "Test"; offset squares spanning multiple slices) so the
seven metric outputs are non-trivial — Dice strictly between 0 and 1,
Hausdorff > 0, APL > 0 — and asserts that every AutoSeg / upstream
comparison produces exactly zero absolute difference.

Skipped when upstream packages aren't installed. CI explicitly installs
``platipy>=0.7`` and ``surface-distance`` so this test runs there.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pydicom
import pytest
import SimpleITK as sitk
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import (
    CTImageStorage,
    ExplicitVRLittleEndian,
    RTStructureSetStorage,
    generate_uid,
)

from autoseg_evaluator.core import surface_distance as autoseg_sd
from autoseg_evaluator.core.masks import extract_mask_for_roi, read_dicom_image, read_rtstruct
from autoseg_evaluator.core.metrics import apl_mean as autoseg_apl_mean
from autoseg_evaluator.core.metrics import apl_total as autoseg_apl_total

# Skip the whole module if either upstream isn't installed.
deepmind = pytest.importorskip("surface_distance")
platipy_cmp = pytest.importorskip("platipy.imaging.label.comparison")

CT_ROWS = 24
CT_COLS = 24
CT_SLICES = 12
CT_SPACING_XY = 2.0
CT_SLICE_THICK = 2.0

# Surface Dice / APL tolerance — must match what AutoSeg uses in tests.
TAU_MM = 3.0


def _new_meta(sop_class_uid: str, sop_uid: str) -> Dataset:
    meta = Dataset()
    meta.MediaStorageSOPClassUID = sop_class_uid
    meta.MediaStorageSOPInstanceUID = sop_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    return meta


def _write_ct_series(folder: Path, *, study_uid: str, for_uid: str, series_uid: str) -> list[str]:
    """Write a flat-intensity CT series; return the SOP UIDs in z order."""
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
        ds.Manufacturer = "TestVendor"
        ds.StudyDate = "20260101"
        ds.SeriesNumber = 1
        ds.InstanceNumber = z_idx + 1
        ds.ImagePositionPatient = [0.0, 0.0, float(z_idx) * CT_SLICE_THICK]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.PixelSpacing = [CT_SPACING_XY, CT_SPACING_XY]
        ds.SliceThickness = CT_SLICE_THICK
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


def _square_xy(cx: float, cy: float, half_w: float) -> list[tuple[float, float]]:
    return [
        (cx - half_w, cy - half_w),
        (cx + half_w, cy - half_w),
        (cx + half_w, cy + half_w),
        (cx - half_w, cy + half_w),
    ]


def _contour_item(xy: list[tuple[float, float]], z_mm: float, ref_sop: str) -> Dataset:
    item = Dataset()
    item.ContourGeometricType = "CLOSED_PLANAR"
    item.NumberOfContourPoints = len(xy)
    item.ContourData = []
    for x, y in xy:
        item.ContourData.extend([float(x), float(y), float(z_mm)])
    image_ref = Dataset()
    image_ref.ReferencedSOPClassUID = CTImageStorage
    image_ref.ReferencedSOPInstanceUID = ref_sop
    item.ContourImageSequence = [image_ref]
    return item


def _write_rtstruct(folder: Path, *, study_uid: str, for_uid: str, ct_sop_uids: list[str]) -> Path:
    """RTSTRUCT with two overlapping multi-slice ROIs (GT vs Test).

    GT  : 12 mm half-width square at (cx, cy)=(20, 20), slices 4-7 (z = 8-14 mm).
    Test: 12 mm half-width square at (cx, cy)=(24, 24), slices 5-8 (z = 10-16 mm).

    The 4-mm XY offset and 2-mm Z offset guarantee non-trivial outputs:
        * Dice strictly between 0 and 1
        * HD100 / HD95 > 0
        * APL total / mean > 0
    """
    sop_uid = generate_uid()
    meta = _new_meta(RTStructureSetStorage, sop_uid)
    fname = "rtss.dcm"
    ds = FileDataset(str(folder / fname), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.PatientID = "TEST"
    ds.StudyInstanceUID = study_uid
    ds.SOPInstanceUID = sop_uid
    ds.SOPClassUID = RTStructureSetStorage
    ds.Modality = "RTSTRUCT"
    ds.SeriesInstanceUID = generate_uid()
    ds.Manufacturer = "TestVendor"
    ds.StructureSetLabel = "TestSet"

    for_item = Dataset()
    for_item.FrameOfReferenceUID = for_uid
    rt_study = Dataset()
    rt_study.ReferencedSOPClassUID = "1.2.840.10008.3.1.2.3.1"
    rt_study.ReferencedSOPInstanceUID = study_uid
    rt_series = Dataset()
    rt_series.SeriesInstanceUID = generate_uid()
    rt_series.ContourImageSequence = []
    for sop in ct_sop_uids:
        img_ref = Dataset()
        img_ref.ReferencedSOPClassUID = CTImageStorage
        img_ref.ReferencedSOPInstanceUID = sop
        rt_series.ContourImageSequence.append(img_ref)
    rt_study.RTReferencedSeriesSequence = [rt_series]
    for_item.RTReferencedStudySequence = [rt_study]
    ds.ReferencedFrameOfReferenceSequence = [for_item]

    ds.StructureSetROISequence = []
    for roi_num, roi_name in ((1, "GT"), (2, "Test")):
        roi = Dataset()
        roi.ROINumber = roi_num
        roi.ROIName = roi_name
        roi.ReferencedFrameOfReferenceUID = for_uid
        roi.ROIGenerationAlgorithm = ""
        ds.StructureSetROISequence.append(roi)

    # GT — square at (20, 20), slices 4..7
    gt_rc = Dataset()
    gt_rc.ReferencedROINumber = 1
    gt_rc.ROIDisplayColor = [255, 0, 0]
    gt_rc.ContourSequence = [
        _contour_item(_square_xy(20.0, 20.0, 12.0), float(z) * CT_SLICE_THICK, ct_sop_uids[z])
        for z in (4, 5, 6, 7)
    ]
    # Test — square at (24, 24), slices 5..8
    test_rc = Dataset()
    test_rc.ReferencedROINumber = 2
    test_rc.ROIDisplayColor = [0, 0, 255]
    test_rc.ContourSequence = [
        _contour_item(_square_xy(24.0, 24.0, 12.0), float(z) * CT_SLICE_THICK, ct_sop_uids[z])
        for z in (5, 6, 7, 8)
    ]
    ds.ROIContourSequence = [gt_rc, test_rc]

    ds.is_little_endian = True
    ds.is_implicit_VR = False
    path = folder / fname
    pydicom.dcmwrite(str(path), ds, write_like_original=False)
    return path


@pytest.fixture(scope="module")
def gt_and_test_masks(tmp_path_factory):
    """Rasterise the GT and Test ROIs from the synthetic fixture into masks."""
    folder = tmp_path_factory.mktemp("metrics_equiv")
    study_uid = generate_uid()
    for_uid = generate_uid()
    series_uid = generate_uid()
    ct_sop_uids = _write_ct_series(
        folder, study_uid=study_uid, for_uid=for_uid, series_uid=series_uid
    )
    rtss_path = _write_rtstruct(
        folder, study_uid=study_uid, for_uid=for_uid, ct_sop_uids=ct_sop_uids
    )
    image = read_dicom_image(str(folder))
    ds = read_rtstruct(str(rtss_path))
    gt_mask = extract_mask_for_roi(image, ds, roi_number=1)
    test_mask = extract_mask_for_roi(image, ds, roi_number=2)
    assert gt_mask is not None and test_mask is not None
    return gt_mask, test_mask


# ---- Sanity preconditions on the synthetic fixture ----------------------


def test_fixture_produces_partial_overlap(gt_and_test_masks):
    """If the synthetic GT and Test masks ever stop overlapping, the
    metric comparisons below become degenerate. Guard against that."""
    gt, test = gt_and_test_masks
    a = sitk.GetArrayFromImage(gt).astype(bool)
    b = sitk.GetArrayFromImage(test).astype(bool)
    assert a.any(), "GT mask is empty — fixture broken"
    assert b.any(), "Test mask is empty — fixture broken"
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    assert 0 < inter < union, "Fixture should produce partial overlap (not disjoint, not identical)"


# ---- google-deepmind surface-distance equivalence -----------------------


def _autoseg_sd_pack(gt_mask: sitk.Image, test_mask: sitk.Image):
    gt_arr = sitk.GetArrayFromImage(gt_mask).astype(bool)
    test_arr = sitk.GetArrayFromImage(test_mask).astype(bool)
    return autoseg_sd.compute_surface_distances(gt_arr, test_arr, gt_mask.GetSpacing())


def _deepmind_sd_pack(gt_mask: sitk.Image, test_mask: sitk.Image):
    gt_arr = sitk.GetArrayFromImage(gt_mask).astype(bool)
    test_arr = sitk.GetArrayFromImage(test_mask).astype(bool)
    return deepmind.compute_surface_distances(gt_arr, test_arr, gt_mask.GetSpacing())


def test_dice_equivalence(gt_and_test_masks):
    gt, test = gt_and_test_masks
    gt_arr = sitk.GetArrayFromImage(gt).astype(bool)
    test_arr = sitk.GetArrayFromImage(test).astype(bool)
    a = float(autoseg_sd.compute_dice_coefficient(gt_arr, test_arr))
    b = float(deepmind.compute_dice_coefficient(gt_arr, test_arr))
    assert 0.0 < a < 1.0, "Dice should be non-trivial"
    assert a == b, f"Dice differs: AutoSeg={a} deepmind={b}"


def test_hausdorff_100_equivalence(gt_and_test_masks):
    gt, test = gt_and_test_masks
    a = float(autoseg_sd.compute_robust_hausdorff(_autoseg_sd_pack(gt, test), 100.0))
    b = float(deepmind.compute_robust_hausdorff(_deepmind_sd_pack(gt, test), 100.0))
    assert a > 0.0, "HD100 should be > 0 on offset masks"
    assert a == b, f"HD100 differs: AutoSeg={a} deepmind={b}"


def test_hausdorff_95_equivalence(gt_and_test_masks):
    gt, test = gt_and_test_masks
    a = float(autoseg_sd.compute_robust_hausdorff(_autoseg_sd_pack(gt, test), 95.0))
    b = float(deepmind.compute_robust_hausdorff(_deepmind_sd_pack(gt, test), 95.0))
    assert a > 0.0
    assert a == b, f"HD95 differs: AutoSeg={a} deepmind={b}"


def test_surface_dice_equivalence(gt_and_test_masks):
    gt, test = gt_and_test_masks
    a = float(autoseg_sd.compute_surface_dice_at_tolerance(_autoseg_sd_pack(gt, test), TAU_MM))
    b = float(deepmind.compute_surface_dice_at_tolerance(_deepmind_sd_pack(gt, test), TAU_MM))
    assert 0.0 < a < 1.0
    assert a == b, f"Surface Dice @ {TAU_MM} mm differs: AutoSeg={a} deepmind={b}"


def test_mean_surface_distance_equivalence(gt_and_test_masks):
    gt, test = gt_and_test_masks
    a_a, a_b = autoseg_sd.compute_average_surface_distance(_autoseg_sd_pack(gt, test))
    d_a, d_b = deepmind.compute_average_surface_distance(_deepmind_sd_pack(gt, test))
    msd_autoseg = (a_a + a_b) / 2.0
    msd_deepmind = (d_a + d_b) / 2.0
    assert msd_autoseg > 0.0
    assert msd_autoseg == msd_deepmind, (
        f"Mean Surface Distance differs: AutoSeg={msd_autoseg} deepmind={msd_deepmind}"
    )


# ---- PlatiPy APL equivalence -------------------------------------------


def test_apl_total_equivalence(gt_and_test_masks):
    gt, test = gt_and_test_masks
    a = float(autoseg_apl_total(gt, test, distance_threshold_mm=TAU_MM))
    b = float(platipy_cmp.compute_metric_total_apl(gt, test, distance_threshold_mm=TAU_MM))
    assert a > 0.0, "APL total should be > 0 on offset masks"
    assert a == b, f"APL total differs: AutoSeg={a} platipy={b}"


def test_apl_mean_equivalence(gt_and_test_masks):
    gt, test = gt_and_test_masks
    a = float(autoseg_apl_mean(gt, test, distance_threshold_mm=TAU_MM))
    b = float(platipy_cmp.compute_metric_mean_apl(gt, test, distance_threshold_mm=TAU_MM))
    assert a > 0.0
    assert a == b, f"APL mean differs: AutoSeg={a} platipy={b}"
