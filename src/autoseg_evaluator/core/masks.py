"""DICOM I/O and RTSTRUCT → binary-mask conversion.

Two rasteriser backends are available, selectable per call, via
:func:`set_default_rasteriser`, or via the ``AUTOSEG_RASTERISER`` env var:

``continuous`` (default)
    Derived from dcmrtstruct2nii's ``DcmPatientCoords2Mask`` (MIT, see
    ``_rasterise_roi_continuous``). Transforms vertices to **continuous**
    (sub-voxel) index coordinates before filling. The loops on each slice are
    read into regions by :mod:`autoseg_evaluator.core.contour_reading` — the
    same reading the 2D stream measures — and each region is filled with a
    half-open rule, so a voxel centre on an edge belongs to exactly one side.

``legacy``
    The PlatiPy-derived ``transform_point_set_from_dicom_struct`` port from
    AutoSeg Evaluator v1. Transforms each contour vertex with
    ``TransformPhysicalPointToIndex`` — i.e. every vertex is **snapped to the
    voxel grid before rasterising**. Retained for backwards comparison; it
    over-estimates structure volume by roughly ``1.5 / R`` (R = structure
    radius in voxels), from ~3 % for large organs to >50 % for structures one
    to two voxels across, because snapping places the contour boundary exactly
    on the sampling lattice and ties resolve as "inside".

Both share the same public API and both return SimpleITK images that carry the
reference image's spacing/origin/direction, so downstream surface-distance code
operates in correct physical units.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

import numpy as np
import pydicom
import SimpleITK as sitk
from skimage.draw import polygon

from autoseg_evaluator.core.contour_reading import (
    AREA_TOLERANCE_MM2,
    ContourReading,
    ContourReadingError,
    Outline,
    read_outlines,
)

# ---- Rasteriser backend selection ----------------------------------------

RASTERISER_LEGACY = "legacy"
RASTERISER_CONTINUOUS = "continuous"
RASTERISERS = (RASTERISER_LEGACY, RASTERISER_CONTINUOUS)


class MaskConversionError(RuntimeError):
    """One structure could not be turned into a mask; the message says why.

    Never an empty mask instead: downstream code would read that as a real
    zero-volume structure rather than a failed conversion.
    """


# A contour whose vertices span more than this many voxels in the through-plane
# index direction is not planar in *image* space and is not reconstructable by
# single-slice filling. Checked in continuous index space, so a contour on an
# obliquely-oriented image (varying patient-space z, constant index z) passes.
PLANARITY_TOLERANCE_VOXELS = 0.5


def _rasteriser_from_env() -> str:
    name = os.environ.get("AUTOSEG_RASTERISER", RASTERISER_CONTINUOUS).strip().lower()
    return name if name in RASTERISERS else RASTERISER_CONTINUOUS


_default_rasteriser = _rasteriser_from_env()


def set_default_rasteriser(name: str) -> None:
    """Select the backend used when a call doesn't pass ``backend=``."""
    global _default_rasteriser
    if name not in RASTERISERS:
        raise ValueError(f"Unknown rasteriser {name!r}; expected one of {RASTERISERS}.")
    _default_rasteriser = name


def get_default_rasteriser() -> str:
    return _default_rasteriser


def physical_points_to_continuous_index(
    dicom_image: sitk.Image, pts_physical: np.ndarray
) -> np.ndarray:
    """Vectorised ``TransformPhysicalPointToContinuousIndex`` for an ``(N, 3)`` array.

    SimpleITK maps index → physical as ``p = origin + D · (index * spacing)``,
    so the inverse is ``index = D⁻¹ · (p − origin) / spacing``. Doing that as a
    single NumPy matmul replaces N per-vertex SimpleITK calls, which is the
    dominant per-contour cost in both backends.

    Returns continuous ``(x, y, z)`` index coordinates, matching SimpleITK's
    index ordering (not NumPy's ``(z, y, x)``).
    """
    return _apply_index_transform(
        np.asarray(pts_physical, dtype=np.float64), _index_transform(dicom_image)
    )


def _index_transform(dicom_image: sitk.Image):
    """Precompute ``(origin, spacing, inverse-direction)`` for the mapping above.

    Hoisted out of the per-contour loop so an ROI inverts its direction matrix
    once rather than once per contour.
    """
    origin = np.asarray(dicom_image.GetOrigin(), dtype=np.float64)
    spacing = np.asarray(dicom_image.GetSpacing(), dtype=np.float64)
    direction = np.asarray(dicom_image.GetDirection(), dtype=np.float64).reshape(3, 3)
    return origin, spacing, np.linalg.inv(direction)


def _apply_index_transform(pts_physical: np.ndarray, transform) -> np.ndarray:
    origin, spacing, inv_direction = transform
    return ((pts_physical - origin) @ inv_direction.T) / spacing


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
    *,
    backend: str | None = None,
) -> sitk.Image | None:
    """Rasterise a specific ROI from an RTSTRUCT into a binary SimpleITK mask.

    Returns ``None`` if the ROI is missing, has no contours, carries an
    unsupported contour geometric type, or is not planar in image space. The
    returned image shares spacing / origin / direction with ``dicom_image``.

    ``backend`` selects the rasteriser (see the module docstring); ``None``
    uses :func:`get_default_rasteriser`.
    """
    masks, names_to_roi_number = _rtstruct_to_masks(
        dicom_image,
        rtstruct_ds,
        spacing_override=spacing_override,
        only_roi_number=roi_number,
        backend=backend,
    )
    if not masks:
        return None
    # _rtstruct_to_masks (with only_roi_number filter) returns at most one
    return masks[0]


def rtstruct_to_all_masks(
    dicom_image: sitk.Image,
    rtstruct_ds: pydicom.Dataset,
    spacing_override: Iterable[float] | None = None,
    *,
    backend: str | None = None,
) -> tuple[list[sitk.Image], list[str]]:
    """Rasterise every ROI in the RTSTRUCT to a list of binary masks + names."""
    return _rtstruct_to_masks(
        dicom_image, rtstruct_ds, spacing_override=spacing_override, backend=backend
    )


def _rtstruct_to_masks(
    dicom_image: sitk.Image,
    rtstruct_ds: pydicom.Dataset,
    *,
    spacing_override: Iterable[float] | None = None,
    only_roi_number: int | None = None,
    backend: str | None = None,
) -> tuple[list[sitk.Image], list[str]]:
    """Internal mask rasteriser — dispatches per ROI to the selected backend."""
    rasterise = (
        _rasterise_roi_continuous
        if (backend or _default_rasteriser) == RASTERISER_CONTINUOUS
        else _rasterise_roi_legacy
    )
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
        volume = rasterise(dicom_image, roi_contours)
        if volume is None:
            continue

        struct_name = "_".join(str(struct_ds.ROIName).split())
        si = sitk.GetImageFromArray(volume.astype(np.uint8))
        # Restore the reference geometry. Upstream's engine drops it at this
        # point and repairs it in its facade, so lifting the engine alone would
        # silently yield unit-spacing masks at origin 0.
        si.CopyInformation(dicom_image)
        out_masks.append(sitk.Cast(si, sitk.sitkUInt8))
        out_names.append(struct_name)
    return out_masks, out_names


def _rasterise_roi_legacy(
    dicom_image: sitk.Image, roi_contours: pydicom.Dataset
) -> np.ndarray | None:
    """v1 / PlatiPy-derived backend: vertices snapped to integer voxel indices.

    Returns a ``(z, y, x)`` boolean volume, or ``None`` when the ROI should be
    dropped (unsupported geometry, or a contour that isn't planar in image
    space). Behaviour is byte-for-byte the historical one — it still backs
    ``tests/test_platipy_equivalence.py``.
    """
    if str(getattr(roi_contours.ContourSequence[0], "ContourGeometricType", "")) != "CLOSED_PLANAR":
        return None

    size_z = dicom_image.GetSize()[2]
    image_blank = np.zeros(dicom_image.GetSize()[::-1], dtype=np.uint8)
    for sl in range(len(roi_contours.ContourSequence)):
        contour_data = np.array(roi_contours.ContourSequence[sl].ContourData, dtype=np.double)
        pts_physical = contour_data.reshape(contour_data.shape[0] // 3, 3)
        pts_index = np.array([dicom_image.TransformPhysicalPointToIndex(p) for p in pts_physical]).T
        x_arr, y_arr = pts_index[[0, 1]]
        z_index = pts_index[2][0]
        if np.any(pts_index[2] != z_index):
            return None  # out-of-plane contour — abort this ROI (matches v1)
        if z_index >= size_z or z_index < 0:
            continue
        slice_arr = np.zeros(image_blank.shape[-2:], dtype=np.uint8)
        rr, cc = polygon(x_arr, y_arr, shape=slice_arr.shape)
        slice_arr[cc, rr] = 1
        # XOR combines stacked polygons → produces holes for donut shapes (e.g. rectum)
        image_blank[z_index] ^= slice_arr
    return image_blank > 0


def _rasterise_roi_continuous(
    dicom_image: sitk.Image, roi_contours: pydicom.Dataset
) -> np.ndarray | None:
    """Continuous-coordinate backend: the volume, or ``None`` if unconvertible.

    See :func:`_fill_structure`; this is its quiet form, for callers that only
    need to know whether a mask exists.
    """
    try:
        volume, _reading = _fill_structure(dicom_image, roi_contours)
    except (ContourReadingError, MaskConversionError):
        return None
    return volume


def _fill_structure(
    dicom_image: sitk.Image, roi_contours: pydicom.Dataset
) -> tuple[np.ndarray, ContourReading]:
    """Read one structure's loops into regions and fill them onto the image.

    Adapted from ``DcmPatientCoords2Mask.convert`` in dcmrtstruct2nii v5 (MIT,
    Copyright (c) 2022 Thomas Phil) — see ``NOTICE`` for the full licence
    text. The idea taken from upstream is transforming vertices to
    **continuous** index coordinates before filling, instead of snapping them
    to the voxel grid first.

    Where this departs from upstream, deliberately:

    * **Loops are read, not toggled.** Upstream fills every loop and combines
      them by exclusive-or, which turns a partial overlap into a hole and
      leaves a one-voxel seam where two loops share an edge. Here the loops on
      each slice are read into regions by
      :func:`~autoseg_evaluator.core.contour_reading.read_outlines` — the same
      function the 2D stream measures — and a structure it refuses gets no mask.
    * **Half-open filling.** A voxel is inside when its centre is inside the
      region; a centre exactly on an edge belongs to one side only (the edge's
      lower and left side). Upstream counts it on both sides, which over-fills
      outlines that pass through voxel centres. Away from such ties the two
      fill exactly the same voxels.
    * **Bounds.** Upstream computes ``z = round(...)`` and indexes straight into
      the array, so a contour just below the volume gives ``z = -1`` and NumPy
      silently paints the **last** slice. Out-of-range slices are skipped here.
    * **Planarity.** Upstream takes the first vertex's slice and flattens the
      contour onto it. A contour spanning more than
      ``PLANARITY_TOLERANCE_VOXELS`` in index space refuses the structure here.
    * **Geometry metadata** is restored by the caller.

    The loops are read in voxel units, so the coordinates filled are exactly
    the coordinates read, with no rescale in between that could move an edge
    off a voxel centre. The area tolerance is converted to match.
    """
    size_x, size_y, size_z = dicom_image.GetSize()
    spacing = dicom_image.GetSpacing()
    transform = _index_transform(dicom_image)  # invert the direction matrix once

    outlines: list[Outline] = []
    for contour in roi_contours.ContourSequence:
        data = np.asarray(getattr(contour, "ContourData", None) or [], dtype=np.float64)
        if data.size % 3 or not np.isfinite(data).all():
            raise MaskConversionError(
                "a contour's coordinates are not complete x, y, z triples of finite numbers"
            )
        if not data.size:
            continue
        idx = _apply_index_transform(data.reshape(-1, 3), transform)
        z_continuous = idx[:, 2]
        if float(z_continuous.max() - z_continuous.min()) > PLANARITY_TOLERANCE_VOXELS:
            raise MaskConversionError(
                "a contour spans more than half a slice through-plane, so it does not lie "
                "in one image plane"
            )
        z = int(round(float(z_continuous[0])))
        if z < 0 or z >= size_z:
            continue
        outlines.append(Outline(z, idx[:, :2], str(getattr(contour, "ContourGeometricType", ""))))

    reading = read_outlines(outlines, area_tolerance=AREA_TOLERANCE_MM2 / (spacing[0] * spacing[1]))
    volume = np.zeros((size_z, size_y, size_x), dtype=bool)
    for z, region in reading.regions.items():
        volume[z] = _fill_region(region, size_y, size_x)
    return volume, reading


def _fill_region(region, rows: int, columns: int) -> np.ndarray:
    """Voxels whose centre lies in ``region``, which is in (column, row) units.

    A scanline fill, even-odd over every ring of the region: its outer
    boundaries and its holes. Half-open in both directions — an edge counts for
    a row when ``low <= row < high``, and a centre is inside a span when
    ``start <= column < end`` — so a centre exactly on an edge is claimed by one
    side, two regions sharing an edge claim each centre once between them, and
    a shape's voxel count tracks its area rather than its perimeter.
    """
    rings = []
    for part in getattr(region, "geoms", [region]):
        rings.append(np.asarray(part.exterior.coords))
        rings.extend(np.asarray(hole.coords) for hole in part.interiors)
    x0 = np.concatenate([r[:-1, 0] for r in rings])
    y0 = np.concatenate([r[:-1, 1] for r in rings])
    x1 = np.concatenate([r[1:, 0] for r in rings])
    y1 = np.concatenate([r[1:, 1] for r in rings])
    sloped = y0 != y1  # a horizontal edge crosses no row under the half-open rule
    x0, y0, x1, y1 = x0[sloped], y0[sloped], x1[sloped], y1[sloped]

    first = np.maximum(np.ceil(np.minimum(y0, y1)), 0).astype(np.int64)
    stop = np.minimum(np.ceil(np.maximum(y0, y1)), rows).astype(np.int64)
    counts = np.maximum(stop - first, 0)
    filled = np.zeros((rows, columns), dtype=bool)
    total = int(counts.sum())
    if not total:
        return filled
    edge = np.repeat(np.arange(len(counts)), counts)
    row = first[edge] + (np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts))
    x = x0[edge] + (row - y0[edge]) * (x1[edge] - x0[edge]) / (y1[edge] - y0[edge])

    # Every closed ring crosses a row an even number of times under this rule,
    # so once sorted by row and position the crossings pair off in order.
    order = np.lexsort((x, row))
    row, x = row[order], x[order]
    span_row = row[0::2]
    start = np.clip(np.ceil(x[0::2]), 0, columns).astype(np.int64)
    end = np.clip(np.ceil(x[1::2]), 0, columns).astype(np.int64)
    marks = np.zeros((rows, columns + 1), dtype=np.int32)
    np.add.at(marks, (span_row, start), 1)
    np.add.at(marks, (span_row, end), -1)
    filled[:] = np.cumsum(marks, axis=1)[:, :columns] > 0
    return filled


def mask_with_reading(
    dicom_image: sitk.Image,
    rtstruct_ds: pydicom.Dataset,
    roi_number: int,
    *,
    backend: str | None = None,
) -> tuple[sitk.Image, tuple[str, ...]]:
    """One ROI's mask, and what reading its loops had to interpret.

    Like :func:`extract_mask_for_roi`, but a structure that cannot be converted
    raises :class:`MaskConversionError` saying why, instead of returning
    ``None``: the reason is what a results row should show.
    """
    number = int(roi_number)
    listed = [
        s
        for s in getattr(rtstruct_ds, "StructureSetROISequence", None) or []
        if int(s.ROINumber) == number
    ]
    if not listed:
        raise MaskConversionError(f"ROI number {number} is not in this structure set")
    items = [
        c
        for c in getattr(rtstruct_ds, "ROIContourSequence", None) or []
        if int(c.ReferencedROINumber) == number
    ]
    if not items or not getattr(items[0], "ContourSequence", None):
        raise MaskConversionError("no contours are stored for this structure")

    if (backend or _default_rasteriser) == RASTERISER_CONTINUOUS:
        try:
            volume, reading = _fill_structure(dicom_image, items[0])
        except ContourReadingError as exc:
            raise MaskConversionError(str(exc)) from exc
        notes = reading.notes
    else:
        volume = _rasterise_roi_legacy(dicom_image, items[0])
        if volume is None:
            raise MaskConversionError(
                "the legacy rasteriser converts only planar CLOSED_PLANAR structures"
            )
        notes = ("legacy rasteriser: loops combined by exclusive-or, not by the shared reading",)

    image = sitk.GetImageFromArray(volume.astype(np.uint8))
    image.CopyInformation(dicom_image)
    return sitk.Cast(image, sitk.sitkUInt8), notes


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


def default_rasteriser_name() -> str:
    """Which rasteriser backend is active for this process.

    Read through a function rather than imported as a value: the backend is
    reassigned by ``set_default_rasteriser`` and by the environment, so a caller
    holding the name from import time would report the wrong one.
    """
    return str(_default_rasteriser)


def find_reference_image_folder(library, patient_id: str, rtstruct_sop_uid: str) -> str | None:
    """Locate the folder containing the CT/MR/PT series referenced by an RTSTRUCT.

    Delegates to :mod:`autoseg_evaluator.data.linkage`, which resolves the link
    from the structure set's explicit DICOM references and only falls back to
    FrameOfReferenceUID when those are absent. Returns ``None`` when nothing
    matched *or* when several series matched equally well — an ambiguous link
    is never silently resolved to the first candidate, which is what this
    function used to do. The Load Data tab surfaces those cases so the user
    can settle them before any computation starts.
    """
    from autoseg_evaluator.data.linkage import reference_image_folder

    return reference_image_folder(library, patient_id, rtstruct_sop_uid)
