"""RT Dose loading and resampling onto a reference image grid.

Reads an RTDOSE file as a SimpleITK image scaled to **Gy**, and resamples it
onto a reference image's grid (e.g. the planning CT) so the dose array aligns
voxel-for-voxel with the CT for slice-by-slice overlay in the visualiser.

This is intentionally UI-free (no Qt, no matplotlib) so it can be unit-tested
against synthetic RTDOSE fixtures without a display.
"""

from __future__ import annotations

import numpy as np
import pydicom
import SimpleITK as sitk


def read_dose_image(file_path: str) -> sitk.Image:
    """Read an RTDOSE file as a float32 SimpleITK image in Gy.

    SimpleITK reads the raw stored pixel values; RTDOSE encodes the physical
    dose via the ``DoseGridScaling`` (3004,000E) factor and ``DoseUnits``
    (GY / CGY) tags — neither of which SimpleITK applies — so they are baked
    in here to return an image in absolute Gy.
    """
    reader = sitk.ImageFileReader()
    reader.SetFileName(str(file_path))
    img = sitk.Cast(reader.Execute(), sitk.sitkFloat32)

    ds = pydicom.dcmread(str(file_path), stop_before_pixels=True, force=True)
    scaling = float(getattr(ds, "DoseGridScaling", 1.0) or 1.0)
    if scaling != 1.0:
        img = img * scaling
    units = str(getattr(ds, "DoseUnits", "GY") or "GY").upper()
    if units == "CGY":
        img = img * 0.01
    return img


def resample_dose_to_reference(dose_img: sitk.Image, reference: sitk.Image) -> sitk.Image:
    """Resample ``dose_img`` onto ``reference``'s grid (trilinear, 0 outside).

    The result shares ``reference``'s size / spacing / origin / direction, so
    its array indexes identically to the reference's. Voxels that fall outside
    the dose grid take a default value of 0.
    """
    return sitk.Resample(
        dose_img,
        reference,
        sitk.Transform(),
        sitk.sitkLinear,
        0.0,
        sitk.sitkFloat32,
    )


def dose_array_on_reference(file_path: str, reference: sitk.Image) -> np.ndarray:
    """Return a ``(z, y, x)`` Gy array of the dose resampled onto ``reference``.

    Voxels outside the dose grid are 0. The array shares its shape with
    ``sitk.GetArrayFromImage(reference)`` so ``dose[z]`` aligns with the
    reference's slice ``z`` for direct overlay.
    """
    dose_img = read_dose_image(file_path)
    resampled = resample_dose_to_reference(dose_img, reference)
    return sitk.GetArrayFromImage(resampled).astype(np.float32)


def dose_grid_on_reference(grid, reference: sitk.Image) -> np.ndarray:
    """``(z, y, x)`` Gy array of a :class:`~autoseg_evaluator.core.dvh.DoseGrid` on ``reference``.

    The dose the DVH integrates, read the way the DVH reads it — the grid's own
    orientation, frame offsets in either convention, units — and interpolated
    trilinearly, so the viewer shows the dose the numbers came from. Voxels
    outside the dose grid are 0.

    Evenly spaced frames, which is nearly every dose, become one SimpleITK image
    and are resampled in a single call. Unevenly spaced frames cannot, and are
    sampled at every voxel centre of ``reference`` instead.
    """
    offsets = np.asarray(grid.frame_offsets, dtype=float)
    steps = np.diff(offsets)
    if steps.size and steps[0] > 0 and np.allclose(steps, steps[0], rtol=0.0, atol=1e-3):
        image = sitk.GetImageFromArray(np.asarray(grid.values, dtype=np.float32))
        image.SetSpacing((grid.pixel_spacing[1], grid.pixel_spacing[0], float(steps[0])))
        image.SetOrigin(tuple(float(v) for v in grid.origin + grid.normal * offsets[0]))
        axes = np.column_stack([grid.row_direction, grid.column_direction, grid.normal])
        image.SetDirection(tuple(float(v) for v in axes.ravel()))
        resampled = resample_dose_to_reference(image, reference)
        return sitk.GetArrayFromImage(resampled).astype(np.float32)

    nx, ny, nz = reference.GetSize()
    spacing = np.asarray(reference.GetSpacing(), dtype=float)
    direction = np.asarray(reference.GetDirection(), dtype=float).reshape(3, 3)
    origin = np.asarray(reference.GetOrigin(), dtype=float)
    columns, rows = np.meshgrid(np.arange(nx), np.arange(ny))
    out = np.zeros((nz, ny, nx), dtype=np.float32)
    for k in range(nz):
        index = np.stack([columns.ravel(), rows.ravel(), np.full(columns.size, k)], axis=1)
        points = origin + (index * spacing) @ direction.T
        values = grid.sample(points)
        out[k] = np.nan_to_num(values, nan=0.0).reshape(ny, nx)
    return out
