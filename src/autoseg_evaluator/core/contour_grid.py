"""The image geometry the polygon metrics are measured in.

The native polygon metrics never rasterise anything, but they still need the
CT. Contours are stored as patient-coordinate millimetres, and comparing two
structure sets means projecting both into one orthonormal frame and agreeing
which contours lie on the same plane. That frame and that plane numbering come
from the image series, which is all this module builds.

**It refuses rather than approximates.** A grid that is slightly wrong does not
produce slightly wrong metrics — it produces contours assigned to the wrong
plane, which silently changes which pairs are compared at all. Every condition
that would make the frame ambiguous raises :class:`GridUnavailableError` with a
reason a user can act on, and the caller reports that instead of a number.

On tags: this reads six of them, and they are geometry plus the two UIDs that
say which image a contour belongs to. It is deliberately not routed through
:mod:`autoseg_evaluator.core.acquisition`, whose allowlist exists to keep
identifiers out of a *published document*. These UIDs never reach one; they are
internal plumbing, and the parser needs them to check that a contour references
the slice it claims to lie on. Nothing else is parsed — ``specific_tags`` keeps
the rest of the header from being read at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pydicom

#: The vendored parser rejects a contour whose plane sits further than this from
#: the slice it was assigned to. Our grid has to be good enough that a correct
#: contour clears it.
PLANE_ALIGNMENT_BUDGET_MM = 1e-3

#: How far any slice may sit from the regular grid fitted through the series.
#: An order of magnitude inside the budget above, because the two errors add:
#: the contour's own planarity is checked against the same allowance.
GRID_REGULARITY_TOLERANCE_MM = 1e-4

#: How far two slice normals may diverge before the series is not one stack.
#: Cosine distance, so this is about 0.08 degrees.
ORIENTATION_TOLERANCE = 1e-6

#: Read exactly these and parse nothing else.
_GEOMETRY_TAGS = [
    "ImagePositionPatient",
    "ImageOrientationPatient",
    "PixelSpacing",
    "Rows",
    "Columns",
    "FrameOfReferenceUID",
    "SOPInstanceUID",
]


class GridUnavailableError(RuntimeError):
    """The image series cannot define an unambiguous contour frame.

    Carries the reason in its message: it is shown to the user, and every reason
    names what was measured rather than only what failed.
    """


@dataclass(frozen=True)
class ContourGrid:
    """One image series as a frame for planar contour comparison."""

    #: Patient coordinates of the first slice, in millimetres.
    origin: tuple[float, float, float]
    #: Columns are image x, image y and the slice normal. Orthonormal.
    basis: np.ndarray
    #: Column spacing, row spacing, slice spacing, in millimetres.
    spacing: tuple[float, float, float]
    #: Columns, rows, slices.
    size: tuple[int, int, int]
    frame_of_reference_uid: str
    #: SOPInstanceUID to zero-based slice index.
    sops: dict[str, int]

    @property
    def slices(self) -> int:
        return self.size[2]

    def as_parser_grid(self) -> dict[str, Any]:
        """The plain-dict shape the vendored parser expects.

        Kept as a conversion rather than as this class's own layout so the
        vendored contract stays visible at the boundary where it applies,
        instead of shaping everything upstream of it.
        """
        return {
            "origin": list(self.origin),
            "basis": self.basis,
            "spacing": list(self.spacing),
            "size": list(self.size),
            "frame": self.frame_of_reference_uid,
            "sops": dict(self.sops),
        }


def _read_geometry(path: str):
    try:
        return pydicom.dcmread(path, stop_before_pixels=True, specific_tags=_GEOMETRY_TAGS)
    except Exception as exc:  # noqa: BLE001 — a bad slice is a reason, not a crash
        raise GridUnavailableError(f"Could not read image header {path}: {exc}") from exc


def _require(dataset, name: str, path: str):
    value = getattr(dataset, name, None)
    if value is None:
        raise GridUnavailableError(f"Image slice is missing {name}: {path}")
    return value


def build_grid(files: Sequence[str]) -> ContourGrid:
    """Build the contour frame for one image series.

    ``files`` is the series' slices in any order; they are sorted here by
    position along their own normal, which is the only ordering that means
    anything geometrically. Instance numbers are not trusted for this — they are
    routinely reversed, duplicated or absent.
    """
    if len(files) < 2:
        raise GridUnavailableError(
            f"An image series needs at least two slices to define a slice spacing; "
            f"this one has {len(files)}."
        )

    slices = [(path, _read_geometry(path)) for path in files]

    first_path, first = slices[0]
    orientation = np.asarray(_require(first, "ImageOrientationPatient", first_path), dtype=float)
    if orientation.shape != (6,):
        raise GridUnavailableError(f"ImageOrientationPatient is not six values: {first_path}")
    axes = orientation.reshape(2, 3)
    normal = np.cross(axes[0], axes[1])
    norm = float(np.linalg.norm(normal))
    if not np.isfinite(norm) or norm < 0.5:
        raise GridUnavailableError(
            f"ImageOrientationPatient does not describe perpendicular axes: {first_path}"
        )
    normal = normal / norm

    frame = str(_require(first, "FrameOfReferenceUID", first_path))
    columns, rows = int(first.Columns), int(first.Rows)
    pixel_spacing = np.asarray(_require(first, "PixelSpacing", first_path), dtype=float)

    # Every slice must agree, or this is not one stack and the frame is a guess.
    positions = []
    for path, dataset in slices:
        if str(_require(dataset, "FrameOfReferenceUID", path)) != frame:
            raise GridUnavailableError(
                "Image series spans more than one Frame of Reference, so contours "
                f"cannot be placed in a single frame: {path}"
            )
        other = np.asarray(_require(dataset, "ImageOrientationPatient", path), dtype=float)
        if float(np.max(np.abs(other - orientation))) > ORIENTATION_TOLERANCE:
            raise GridUnavailableError(
                f"Image slices are not all in the same plane orientation: {path}"
            )
        if int(dataset.Columns) != columns or int(dataset.Rows) != rows:
            raise GridUnavailableError(f"Image slices are not all the same size: {path}")
        position = np.asarray(_require(dataset, "ImagePositionPatient", path), dtype=float)
        positions.append((float(np.dot(position, normal)), path, dataset, position))

    positions.sort(key=lambda row: row[0])
    levels = np.array([row[0] for row in positions], dtype=float)

    # Fit through the endpoints rather than averaging the gaps: on a regular
    # stack both agree, and on an irregular one this bounds the *accumulated*
    # offset, which is what actually misplaces a contour near the far end.
    span = float(levels[-1] - levels[0])
    count = len(levels) - 1
    if abs(span) < GRID_REGULARITY_TOLERANCE_MM:
        raise GridUnavailableError(
            "Image slices all sit at the same position along the slice normal, "
            "so there is no slice spacing to derive."
        )
    dz = span / count
    drift = float(np.max(np.abs(levels - (levels[0] + dz * np.arange(len(levels))))))
    if drift > GRID_REGULARITY_TOLERANCE_MM:
        raise GridUnavailableError(
            f"Image slices are not evenly spaced: they deviate from a regular grid "
            f"by up to {drift:.4g} mm, against a tolerance of "
            f"{GRID_REGULARITY_TOLERANCE_MM:g} mm. Planar contour metrics assign "
            "contours to slices by position, so an uneven stack would place them "
            "on the wrong plane. The metrics themselves do not require a constant "
            "slice thickness; this adapter does."
        )

    sops: dict[str, int] = {}
    for index, (_level, path, dataset, _position) in enumerate(positions):
        uid = str(_require(dataset, "SOPInstanceUID", path))
        if uid in sops:
            raise GridUnavailableError(
                f"Two image slices share one SOPInstanceUID, so a contour "
                f"referencing it is ambiguous: {path}"
            )
        sops[uid] = index

    origin = positions[0][3]
    return ContourGrid(
        origin=(float(origin[0]), float(origin[1]), float(origin[2])),
        # Columns, matching the parser's ``(xyz - origin) @ basis`` projection.
        basis=np.stack([axes[0], axes[1], normal], axis=1),
        spacing=(float(pixel_spacing[1]), float(pixel_spacing[0]), abs(dz)),
        size=(columns, rows, len(positions)),
        frame_of_reference_uid=frame,
        sops=sops,
    )


__all__ = [
    "GRID_REGULARITY_TOLERANCE_MM",
    "PLANE_ALIGNMENT_BUDGET_MM",
    "ContourGrid",
    "GridUnavailableError",
    "build_grid",
]
