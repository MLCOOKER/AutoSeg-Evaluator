"""DICOM I/O and RTSTRUCT → binary-mask conversion.

This is a port of the PlatiPy-derived ``transform_point_set_from_dicom_struct``
embedded in AutoSeg Evaluator v1, plus thin wrappers around SimpleITK and
pydicom for loading CT/MR/PT image series and RTSTRUCT files.

All functions return SimpleITK images that share spacing/origin/orientation
with the reference image, so downstream surface-distance code can operate
on them with the correct physical-units spacing.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

import numpy as np
import pydicom
import SimpleITK as sitk
from skimage.draw import polygon


def read_dicom_image(folder: str) -> sitk.Image:
    """Load a CT/MR/PT series from ``folder`` as a 3D SimpleITK volume."""
    reader = sitk.ImageSeriesReader()
    files = reader.GetGDCMSeriesFileNames(str(folder))
    reader.SetFileNames(files)
    return reader.Execute()


def read_rtstruct(file_path: str) -> pydicom.Dataset:
    """Read an RTSTRUCT DICOM file with pydicom (force=True for permissive parsing)."""
    return pydicom.dcmread(file_path, force=True)


def extract_mask_for_roi(
    dicom_image: sitk.Image,
    rtstruct_ds: pydicom.Dataset,
    roi_number: int,
    spacing_override: Iterable[float] | None = None,
) -> sitk.Image | None:
    """Rasterise a specific ROI from an RTSTRUCT into a binary SimpleITK mask.

    Returns ``None`` if the ROI is missing, has no contours, or only contains
    non-CLOSED_PLANAR contour geometry. The returned image shares spacing /
    origin / direction with ``dicom_image``.
    """
    masks, names_to_roi_number = _rtstruct_to_masks(
        dicom_image, rtstruct_ds, spacing_override=spacing_override, only_roi_number=roi_number
    )
    if not masks:
        return None
    # _rtstruct_to_masks (with only_roi_number filter) returns at most one
    return masks[0]


def rtstruct_to_all_masks(
    dicom_image: sitk.Image,
    rtstruct_ds: pydicom.Dataset,
    spacing_override: Iterable[float] | None = None,
) -> tuple[list[sitk.Image], list[str]]:
    """Rasterise every ROI in the RTSTRUCT to a list of binary masks + names."""
    return _rtstruct_to_masks(dicom_image, rtstruct_ds, spacing_override=spacing_override)


def _rtstruct_to_masks(
    dicom_image: sitk.Image,
    rtstruct_ds: pydicom.Dataset,
    *,
    spacing_override: Iterable[float] | None = None,
    only_roi_number: int | None = None,
) -> tuple[list[sitk.Image], list[str]]:
    """Internal mask rasteriser — the v1 port, with optional ROI-number filter."""
    if spacing_override:
        current = list(dicom_image.GetSpacing())
        new = tuple(
            current[k] if spacing_override[k] == 0 else spacing_override[k] for k in range(3)
        )
        dicom_image.SetSpacing(new)
    if not hasattr(rtstruct_ds, "ROIContourSequence") or not hasattr(
        rtstruct_ds, "StructureSetROISequence"
    ):
        return [], []
    roi_contour_map = {cs.ReferencedROINumber: cs for cs in rtstruct_ds.ROIContourSequence}

    out_masks: list[sitk.Image] = []
    out_names: list[str] = []

    for struct_ds in rtstruct_ds.StructureSetROISequence:
        roi_number = int(struct_ds.ROINumber)
        if only_roi_number is not None and roi_number != only_roi_number:
            continue
        if roi_number not in roi_contour_map:
            continue
        roi_contours = roi_contour_map[roi_number]
        if not hasattr(roi_contours, "ContourSequence"):
            continue
        if len(roi_contours.ContourSequence) == 0:
            continue
        # Only CLOSED_PLANAR contour geometry is supported (matches v1 behaviour).
        if roi_contours.ContourSequence[0].ContourGeometricType != "CLOSED_PLANAR":
            continue

        image_blank = np.zeros(dicom_image.GetSize()[::-1], dtype=np.uint8)
        struct_name = "_".join(str(struct_ds.ROIName).split())
        skip = False
        for sl in range(len(roi_contours.ContourSequence)):
            contour_data = np.array(roi_contours.ContourSequence[sl].ContourData, dtype=np.double)
            pts_physical = contour_data.reshape(contour_data.shape[0] // 3, 3)
            pts_index = np.array(
                [dicom_image.TransformPhysicalPointToIndex(p) for p in pts_physical]
            ).T
            x_arr, y_arr = pts_index[[0, 1]]
            z_index = pts_index[2][0]
            if np.any(pts_index[2] != z_index):
                # Out-of-plane contour — abort this ROI (matches v1)
                skip = True
                break
            if z_index >= dicom_image.GetSize()[2] or z_index < 0:
                continue
            slice_arr = np.zeros(image_blank.shape[-2:], dtype=np.uint8)
            rr, cc = polygon(x_arr, y_arr, shape=slice_arr.shape)
            slice_arr[cc, rr] = 1
            # XOR combines stacked polygons → produces holes for donut shapes (e.g. rectum)
            image_blank[z_index] ^= slice_arr
        if skip:
            continue
        si = sitk.GetImageFromArray((image_blank > 0).astype(np.uint8))
        si.CopyInformation(dicom_image)
        out_masks.append(sitk.Cast(si, sitk.sitkUInt8))
        out_names.append(struct_name)
    return out_masks, out_names


def truncate_to_gt_z_extent(
    test_mask: sitk.Image, gt_mask: sitk.Image
) -> tuple[sitk.Image, dict[str, float]]:
    """Zero out slices in ``test_mask`` that fall outside the GT's craniocaudal extent.

    Useful for structures with high-variability cranio-caudal extent (e.g.
    spinal cord, rectum) where the AI may legitimately contour beyond the GT
    range but those slices shouldn't penalise the comparison.

    Returns ``(truncated_mask, extent_info)`` where ``extent_info`` carries:

    * ``slices_removed`` — count of test-mask Z slices that contained voxels
      but were zeroed because they fell outside the GT extent,
    * ``extent_removed_mm`` — the same count multiplied by the image's Z
      spacing, i.e. the craniocaudal extent (in mm) that was discarded.

    When the GT mask is empty, the test mask is returned unchanged and the
    extent values are both zero.
    """
    gt_arr = sitk.GetArrayFromImage(gt_mask)
    test_arr = sitk.GetArrayFromImage(test_mask).copy()
    z_spacing = float(test_mask.GetSpacing()[2])
    # Identify which Z slices have any GT voxels
    z_with_gt = np.any(gt_arr > 0, axis=(1, 2))
    if not z_with_gt.any():
        return test_mask, {"slices_removed": 0, "extent_removed_mm": 0.0}
    z_min = int(np.argmax(z_with_gt))
    z_max = int(len(z_with_gt) - 1 - np.argmax(z_with_gt[::-1]))
    test_z_present = np.any(test_arr > 0, axis=(1, 2))
    # Only count slices that actually carried test voxels — silent zero-slices
    # outside the GT extent aren't really "lost" data.
    slices_removed = int(np.sum(test_z_present[:z_min])) + int(np.sum(test_z_present[z_max + 1 :]))
    truncated = np.zeros_like(test_arr)
    truncated[z_min : z_max + 1] = test_arr[z_min : z_max + 1]
    si = sitk.GetImageFromArray(truncated.astype(np.uint8))
    si.CopyInformation(test_mask)
    return si, {
        "slices_removed": slices_removed,
        "extent_removed_mm": float(slices_removed) * z_spacing,
    }


def gt_z_extent_mm(gt_mask: sitk.Image) -> tuple[float, float] | None:
    """Physical craniocaudal (z) extent of the GT mask's foreground, in mm.

    Returns ``(z_lo, z_hi)`` covering the slices that contain GT voxels, padded
    by half a slice on each side so a contour plane sitting at a boundary
    slice's centre is included. This is the contour-space equivalent of the
    voxel-space crop :func:`truncate_to_gt_z_extent` performs, used to truncate
    a test structure's RTSS contour planes for DVH so the dose statistics
    describe the same craniocaudal range as the geometric comparison.

    Returns ``None`` when the GT mask is empty.
    """
    arr = sitk.GetArrayViewFromImage(gt_mask)
    z_with_gt = np.any(arr > 0, axis=(1, 2))
    if not z_with_gt.any():
        return None
    z0 = int(np.argmax(z_with_gt))
    z1 = int(len(z_with_gt) - 1 - np.argmax(z_with_gt[::-1]))
    p0 = float(gt_mask.TransformIndexToPhysicalPoint((0, 0, z0))[2])
    p1 = float(gt_mask.TransformIndexToPhysicalPoint((0, 0, z1))[2])
    half = 0.5 * float(gt_mask.GetSpacing()[2])
    lo, hi = (p0, p1) if p0 <= p1 else (p1, p0)
    return lo - half, hi + half


def find_reference_image_folder(library, patient_id: str, rtstruct_sop_uid: str) -> str | None:
    """Locate the folder containing the CT/MR/PT series referenced by an RTSTRUCT.

    Uses ``FrameOfReferenceUID`` from the loaded :class:`MetadataLibrary` —
    falls back to ``None`` if no matching image series is found.
    """
    patient = library.patients.get(patient_id)
    if patient is None:
        return None
    target_for = None
    for ctx in patient.contexts:
        for rtss in ctx.rtstructs:
            if rtss.sop_instance_uid == rtstruct_sop_uid:
                target_for = rtss.frame_of_reference_uid
                break
        if target_for is not None:
            break
    if target_for is None:
        return None
    for ctx in patient.contexts:
        if ctx.frame_of_reference_uid != target_for:
            continue
        for series in ctx.image_series:
            if series.files:
                return os.path.dirname(series.files[0])
    return None
