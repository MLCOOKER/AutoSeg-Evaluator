"""Voxel-exact equivalence between AutoSeg's mask rasteriser and PlatiPy's.

AutoSeg Evaluator's :func:`extract_mask_for_roi` is a port of PlatiPy's
``transform_point_set_from_dicom_struct``. This test pins the two
implementations as bit-for-bit identical so future refactors of
``core/masks.py`` can't silently drift away from PlatiPy semantics —
in particular the XOR slice-combination that produces correct hole
behaviour for donut-shaped structures.

The fixtures build a tiny self-contained synthetic CT + RTSTRUCT in a
temp directory; the test reads both with SimpleITK + pydicom, then
runs each ROI through both rasterisers and asserts the resulting
numpy arrays are equal.

The test is skipped when ``platipy`` isn't installed — see
``.github/workflows/ci.yml`` for the CI install of platipy alongside
the project dev extras.
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

from autoseg_evaluator.core.masks import (
    extract_mask_for_roi,
    read_dicom_image,
    read_rtstruct,
)

# Skip the whole module if PlatiPy isn't available (e.g. local dev install
# without the optional comparison extras). CI installs platipy explicitly.
platipy_rt = pytest.importorskip("platipy.dicom.io.rtstruct_to_nifti")
from platipy.dicom.io.rtstruct_to_nifti import (  # noqa: E402
    transform_point_set_from_dicom_struct,
)

# CT geometry kept small so the test is fast. 16×16 in-plane, 8 slices,
# 2 mm isotropic, axial orientation, origin at (0,0,0).
CT_ROWS = 16
CT_COLS = 16
CT_SLICES = 8
CT_SPACING_XY = 2.0
CT_SLICE_THICK = 2.0


def _new_meta(sop_class_uid: str, sop_uid: str) -> Dataset:
    meta = Dataset()
    meta.MediaStorageSOPClassUID = sop_class_uid
    meta.MediaStorageSOPInstanceUID = sop_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    return meta


def _write_ct_series(folder: Path, *, study_uid: str, for_uid: str, series_uid: str) -> None:
    """Write CT_SLICES axial slices with a flat intensity pattern."""
    pixel = np.zeros((CT_ROWS, CT_COLS), dtype=np.uint16)
    for z_idx in range(CT_SLICES):
        sop_uid = generate_uid()
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


def _square_contour_xy(cx: float, cy: float, half_w: float) -> list[tuple[float, float]]:
    """Axis-aligned square contour in physical (mm) space."""
    return [
        (cx - half_w, cy - half_w),
        (cx + half_w, cy - half_w),
        (cx + half_w, cy + half_w),
        (cx - half_w, cy + half_w),
    ]


def _contour_seq_item(xy_points: list[tuple[float, float]], z_mm: float, ref_sop: str) -> Dataset:
    """Build one ContourSequence item with CLOSED_PLANAR geometry."""
    item = Dataset()
    item.ContourGeometricType = "CLOSED_PLANAR"
    item.NumberOfContourPoints = len(xy_points)
    item.ContourData = []
    for x, y in xy_points:
        item.ContourData.extend([float(x), float(y), float(z_mm)])
    image_ref = Dataset()
    image_ref.ReferencedSOPClassUID = CTImageStorage
    image_ref.ReferencedSOPInstanceUID = ref_sop
    item.ContourImageSequence = [image_ref]
    return item


def _write_rtstruct(folder: Path, *, study_uid: str, for_uid: str, ct_sop_uids: list[str]) -> Path:
    """Write an RTSTRUCT with three ROIs exercising the three rasterisation paths:

    * ROI 1 'Square' — one axis-aligned square on a single slice (trivial).
    * ROI 2 'Donut' — two concentric squares on the same slice; XOR must
      produce a hole between them.
    * ROI 3 'MultiSlice' — same square repeated on three consecutive slices.
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

    # Referenced FrameOfReference + study/series
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

    # StructureSetROISequence — one entry per ROI
    roi_specs = [("Square", 1), ("Donut", 2), ("MultiSlice", 3)]
    structure_seq = []
    for name, num in roi_specs:
        roi = Dataset()
        roi.ROINumber = num
        roi.ROIName = name
        roi.ReferencedFrameOfReferenceUID = for_uid
        roi.ROIGenerationAlgorithm = ""
        structure_seq.append(roi)
    ds.StructureSetROISequence = structure_seq

    # ROIContourSequence — the actual geometry
    contour_seq_per_roi: list[Dataset] = []

    # ROI 1: Square on the middle slice (index 4 → z = 8.0 mm).
    rc1 = Dataset()
    rc1.ReferencedROINumber = 1
    rc1.ROIDisplayColor = [255, 0, 0]
    rc1.ContourSequence = [
        _contour_seq_item(
            _square_contour_xy(cx=14.0, cy=14.0, half_w=4.0),
            z_mm=8.0,
            ref_sop=ct_sop_uids[4],
        )
    ]
    contour_seq_per_roi.append(rc1)

    # ROI 2: Donut on slice 5 — outer square + concentric inner square.
    # XOR semantics must produce a hole in the middle.
    rc2 = Dataset()
    rc2.ReferencedROINumber = 2
    rc2.ROIDisplayColor = [0, 255, 0]
    rc2.ContourSequence = [
        _contour_seq_item(
            _square_contour_xy(cx=16.0, cy=16.0, half_w=6.0),
            z_mm=10.0,
            ref_sop=ct_sop_uids[5],
        ),
        _contour_seq_item(
            _square_contour_xy(cx=16.0, cy=16.0, half_w=2.0),
            z_mm=10.0,
            ref_sop=ct_sop_uids[5],
        ),
    ]
    contour_seq_per_roi.append(rc2)

    # ROI 3: MultiSlice — same square on three consecutive slices.
    rc3 = Dataset()
    rc3.ReferencedROINumber = 3
    rc3.ROIDisplayColor = [0, 0, 255]
    rc3.ContourSequence = [
        _contour_seq_item(
            _square_contour_xy(cx=8.0, cy=8.0, half_w=3.0),
            z_mm=float(z_idx) * CT_SLICE_THICK,
            ref_sop=ct_sop_uids[z_idx],
        )
        for z_idx in (1, 2, 3)
    ]
    contour_seq_per_roi.append(rc3)

    ds.ROIContourSequence = contour_seq_per_roi

    # RTROIObservationsSequence is technically required by the standard
    # but neither AutoSeg nor PlatiPy reads it during rasterisation;
    # omitting it keeps the fixture small.

    ds.is_little_endian = True
    ds.is_implicit_VR = False
    path = folder / fname
    pydicom.dcmwrite(str(path), ds, write_like_original=False)
    return path


@pytest.fixture(scope="module")
def synthetic_dataset(tmp_path_factory):
    """Build a tiny self-contained CT + RTSTRUCT in a module-scoped tmp dir."""
    folder = tmp_path_factory.mktemp("platipy_equiv")
    study_uid = generate_uid()
    for_uid = generate_uid()
    series_uid = generate_uid()
    _write_ct_series(folder, study_uid=study_uid, for_uid=for_uid, series_uid=series_uid)
    # Collect the CT SOP UIDs so the RTSTRUCT can reference them in order.
    ct_files = sorted(folder.glob("ct_*.dcm"))
    ct_sop_uids = [str(pydicom.dcmread(str(f)).SOPInstanceUID) for f in ct_files]
    rtss_path = _write_rtstruct(
        folder, study_uid=study_uid, for_uid=for_uid, ct_sop_uids=ct_sop_uids
    )
    return folder, rtss_path


# ---- The actual equivalence test -----------------------------------------


@pytest.mark.parametrize("roi_number, roi_name", [(1, "Square"), (2, "Donut"), (3, "MultiSlice")])
def test_autoseg_rasteriser_matches_platipy(synthetic_dataset, roi_number, roi_name):
    """For each of the three ROIs, AutoSeg and PlatiPy must produce
    voxel-identical binary masks."""
    folder, rtss_path = synthetic_dataset
    image = read_dicom_image(str(folder))
    ds = read_rtstruct(str(rtss_path))

    autoseg_mask = extract_mask_for_roi(image, ds, roi_number)
    assert autoseg_mask is not None, f"AutoSeg failed to rasterise ROI {roi_number}"

    platipy_masks, platipy_names = transform_point_set_from_dicom_struct(image, ds)
    platipy_map = {"_".join(str(n).split()): m for n, m in zip(platipy_names, platipy_masks)}
    platipy_mask = platipy_map.get(roi_name)
    assert platipy_mask is not None, f"PlatiPy didn't produce a mask for ROI '{roi_name}'"

    a = sitk.GetArrayFromImage(autoseg_mask).astype(bool)
    b = sitk.GetArrayFromImage(platipy_mask).astype(bool)

    assert a.shape == b.shape, f"Shape mismatch {a.shape} vs {b.shape}"
    diff = int(np.sum(a != b))
    assert diff == 0, (
        f"ROI '{roi_name}' produced {diff} differing voxels between AutoSeg and PlatiPy "
        f"(AutoSeg total={a.sum()}, PlatiPy total={b.sum()})"
    )


def test_donut_xor_actually_produces_hole(synthetic_dataset):
    """Sanity check that the Donut ROI is genuinely hollow (not solid).

    Without XOR semantics in slice combination, a polygon-fill of the
    outer square followed by polygon-fill of the inner square would
    leave a solid square — this test ensures the XOR step is doing
    its job and that the empirical equivalence with PlatiPy isn't a
    false positive caused by both implementations silently filling
    the hole."""
    folder, rtss_path = synthetic_dataset
    image = read_dicom_image(str(folder))
    ds = read_rtstruct(str(rtss_path))
    mask = extract_mask_for_roi(image, ds, roi_number=2)
    assert mask is not None
    arr = sitk.GetArrayFromImage(mask).astype(bool)
    # The slice containing the donut is z_index=5 (z = 10mm, slice thickness 2mm).
    slice_arr = arr[5]
    # Centre voxel of the donut: at (16,16) physical → index (8,8) for 2mm spacing.
    # That voxel sits inside the inner square and should be OFF.
    assert not slice_arr[8, 8], "Donut centre should be a hole, not filled"
    # An edge voxel between inner and outer should be ON.
    assert slice_arr[8, 11] or slice_arr[8, 12], (
        "Annulus between inner and outer squares should contain ON voxels"
    )
