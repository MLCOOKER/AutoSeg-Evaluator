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
