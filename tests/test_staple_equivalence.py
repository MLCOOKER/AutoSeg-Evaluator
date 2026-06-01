"""Bit-for-bit equivalence of AutoSeg's STAPLE wrapper vs SimpleITK.

AutoSeg's :func:`autoseg_evaluator.core.staple.compute_staple` wraps
``SimpleITK.STAPLEImageFilter`` (Warfield 2004 — the reference STAPLE
implementation) and adds a union-bbox crop, a P >= 0.5 threshold, and a
pad-back. This test builds a synthetic 3-rater fixture (three offset
squares spanning several slices, so STAPLE is non-trivial: per-rater
sensitivity/specificity strictly between 0 and 1, a consensus smaller than
the union) and asserts that AutoSeg's reported per-rater sensitivity /
specificity and binary consensus mask are exactly reproduced by an
independent SimpleITK STAPLE invocation on the same masks.

The independent path here re-implements the crop / threshold / pad-back
from scratch with NumPy + a fresh ``STAPLEImageFilter``; only the single
integer bbox padding AutoSeg selected is reused, so both paths feed the
upstream filter the identical prepared input.
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

from autoseg_evaluator.core.masks import extract_mask_for_roi, read_dicom_image, read_rtstruct
from autoseg_evaluator.core.staple import StapleConfig, compute_staple

CT_ROWS = 32
CT_COLS = 32
CT_SLICES = 14
CT_SPACING_XY = 2.0
CT_SLICE_THICK = 2.0

CONFIG = StapleConfig()


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


# Three "raters" of the same organ: overlapping but offset squares.
_RATERS = [
    ((20.0, 20.0, 12.0), (4, 5, 6, 7)),
    ((23.0, 20.0, 12.0), (4, 5, 6, 7)),
    ((20.0, 23.0, 12.0), (5, 6, 7, 8)),
]


def _write_rtstruct(folder: Path, *, study_uid: str, for_uid: str, ct_sop_uids: list[str]) -> Path:
    sop_uid = generate_uid()
    meta = _new_meta(RTStructureSetStorage, sop_uid)
    ds = FileDataset(str(folder / "rtss.dcm"), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.PatientID = "TEST"
    ds.StudyInstanceUID = study_uid
    ds.SOPInstanceUID = sop_uid
    ds.SOPClassUID = RTStructureSetStorage
    ds.Modality = "RTSTRUCT"
    ds.SeriesInstanceUID = generate_uid()
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
    ds.ROIContourSequence = []
    for idx, ((cx, cy, half), slices) in enumerate(_RATERS, start=1):
        roi = Dataset()
        roi.ROINumber = idx
        roi.ROIName = f"rater{idx}"
        roi.ReferencedFrameOfReferenceUID = for_uid
        roi.ROIGenerationAlgorithm = ""
        ds.StructureSetROISequence.append(roi)

        rc = Dataset()
        rc.ReferencedROINumber = idx
        rc.ROIDisplayColor = [255, 0, 0]
        rc.ContourSequence = [
            _contour_item(_square_xy(cx, cy, half), float(z) * CT_SLICE_THICK, ct_sop_uids[z])
            for z in slices
        ]
        ds.ROIContourSequence.append(rc)

    ds.is_little_endian = True
    ds.is_implicit_VR = False
    path = folder / "rtss.dcm"
    pydicom.dcmwrite(str(path), ds, write_like_original=False)
    return path


@pytest.fixture(scope="module")
def rater_masks(tmp_path_factory):
    folder = tmp_path_factory.mktemp("staple_equiv")
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
    masks = [extract_mask_for_roi(image, ds, roi_number=i) for i in (1, 2, 3)]
    assert all(m is not None for m in masks)
    return masks


def _reference_consensus(masks, padding: int):
    """Independent crop -> SimpleITK STAPLE -> threshold -> pad-back."""
    union = None
    for m in masks:
        a = sitk.GetArrayFromImage(m) > 0
        union = a if union is None else np.logical_or(union, a)
    size = masks[0].GetSize()  # (x, y, z)
    zs, ys, xs = np.where(union)
    x0 = max(0, int(xs.min()) - padding)
    y0 = max(0, int(ys.min()) - padding)
    z0 = max(0, int(zs.min()) - padding)
    x1 = min(size[0] - 1, int(xs.max()) + padding)
    y1 = min(size[1] - 1, int(ys.max()) + padding)
    z1 = min(size[2] - 1, int(zs.max()) + padding)
    cropped = [sitk.Cast(m[x0 : x1 + 1, y0 : y1 + 1, z0 : z1 + 1], sitk.sitkUInt8) for m in masks]

    f = sitk.STAPLEImageFilter()
    f.SetForegroundValue(1)
    f.SetMaximumIterations(int(CONFIG.max_iterations))
    f.SetConfidenceWeight(float(CONFIG.confidence_weight))
    prob = f.Execute(cropped)
    sens = [float(s) for s in f.GetSensitivity()]
    spec = [float(s) for s in f.GetSpecificity()]

    binc = sitk.Cast(sitk.BinaryThreshold(prob, 0.5, 1.0), sitk.sitkUInt8)
    cons = np.zeros((size[2], size[1], size[0]), dtype=np.uint8)
    cons[z0 : z1 + 1, y0 : y1 + 1, x0 : x1 + 1] = sitk.GetArrayFromImage(binc)
    return sens, spec, cons


def test_fixture_is_nontrivial(rater_masks):
    arrs = [sitk.GetArrayFromImage(m).astype(bool) for m in rater_masks]
    for a in arrs:
        assert a.any(), "a rater mask is empty — fixture broken"
    inter = np.logical_and.reduce(arrs)
    union = np.logical_or.reduce(arrs)
    assert 0 < inter.sum() < union.sum(), "raters should partly disagree"


def test_staple_consensus_matches_simpleitk(rater_masks):
    result = compute_staple(rater_masks, CONFIG)
    assert result is not None
    assert result.n_raters == 3

    ref_sens, ref_spec, ref_cons = _reference_consensus(rater_masks, result.bbox_padding_used)

    # Per-rater EM estimates — bit-for-bit.
    assert result.sensitivities == ref_sens, (
        f"sensitivity differs: AutoSeg={result.sensitivities} sitk={ref_sens}"
    )
    assert result.specificities == ref_spec, (
        f"specificity differs: AutoSeg={result.specificities} sitk={ref_spec}"
    )
    # The estimates must be non-trivial (genuine disagreement exercised).
    assert any(0.0 < s < 1.0 for s in result.sensitivities)

    # Binary consensus — voxel-for-voxel.
    a_cons = sitk.GetArrayFromImage(result.consensus_mask).astype(bool)
    assert a_cons.shape == ref_cons.shape
    xor = int(np.sum(a_cons != ref_cons.astype(bool)))
    assert xor == 0, f"consensus mask differs by {xor} voxels"
    assert a_cons.any(), "consensus is empty — fixture/threshold broken"


def test_compute_staple_requires_two_raters(rater_masks):
    assert compute_staple(rater_masks[:1], CONFIG) is None
