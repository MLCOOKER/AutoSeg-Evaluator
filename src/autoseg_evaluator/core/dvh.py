"""Dose-volume statistics, integrated over the contours themselves.

A structure's dose-volume histogram is the dose integrated over the region its
contours enclose. The contours are read by the function the masks and the 2D
metrics also use (:func:`~autoseg_evaluator.core.masks.read_structure`), into
one region per CT slice, and each region stands for a slab one slice thick: the
convention of every DVH method compared, and of the analytic benchmarks.

Each slab is divided into sub-cells aligned to the CT voxels. A sub-cell is
weighted by the exact area of the region inside it, found by signed-area
accumulation along each row rather than by clipping, and the dose is
interpolated trilinearly at the centroid of that area. The spacing is the
finest of 0.25, 0.5 and 1 mm that keeps a structure's samples within ten
million: small organs are sampled at 0.25 mm, where it matters, large targets
at up to 1 mm, where it no longer does. A structure too large for that even at
1 mm, such as a body contour, is sampled once per voxel. Samples accumulate a
slice at a time into a histogram of 1 mGy bins, so memory does not grow with
their number.

A structure born as a mask has no contours: the STAPLE consensus, and a Tab 2
consensus used as ground truth. Its voxels are sampled instead, with the same
spacing rule, histogram and statistics.

The statistics describe the part of a structure inside the dose grid. A part
outside it has no calculated dose, so it is left out rather than given one,
and the result says what share of the structure the statistics cover.

Validated against the analytic datasets of Nelms et al. 2015 (Med Phys
42:4435) and against disc phantoms in ``docs/DVH_METHOD_VALIDATION.md``,
produced by ``scripts/validate_dvh_methods.py``. Until v3 the DVH came from
dicompyler-core, which that report also scores.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import shapely
import SimpleITK as sitk
from scipy import ndimage
from shapely.geometry.polygon import orient

from autoseg_evaluator.core.contour_reading import ContourReadingError
from autoseg_evaluator.core.masks import MaskConversionError, read_structure

#: Sub-sample spacings tried, finest first.
SPACINGS_MM = (0.25, 0.5, 1.0)
#: The last resort, past the cap even at 1 mm: one sample per voxel, at its centre.
VOXEL_CENTRES = math.inf
#: The finest spacing is used whose samples stay within this many.
MAX_SAMPLES = 10_000_000
#: Dose histogram bin. D{x} is read to the centre of its bin.
BIN_GY = 0.001
#: A sub-cell covered by less than this share is floating-point residue from an
#: edge lying along a grid line, and is dropped.
SLIVER = 1e-9
#: Points per interpolation call, to bound memory.
CHUNK = 2_000_000


class DVHError(Exception):
    """Raised when DVH computation fails for a specific structure/dose pair."""


@dataclass
class DVHConfig:
    include_dmean: bool = True
    include_dmax: bool = True
    include_dmin: bool = False
    d_at_volumes_pct: list[float] = field(default_factory=list)
    d_at_volumes_cc: list[float] = field(default_factory=list)
    v_at_doses_gy: list[float] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DVHConfig:
        return cls(
            include_dmean=bool(data.get("include_dmean", True)),
            include_dmax=bool(data.get("include_dmax", True)),
            include_dmin=bool(data.get("include_dmin", False)),
            d_at_volumes_pct=[float(x) for x in data.get("d_at_volumes_pct", []) or []],
            d_at_volumes_cc=[float(x) for x in data.get("d_at_volumes_cc", []) or []],
            v_at_doses_gy=[float(x) for x in data.get("v_at_doses_gy", []) or []],
        )

    def any_enabled(self) -> bool:
        return (
            self.include_dmean
            or self.include_dmax
            or self.include_dmin
            or bool(self.d_at_volumes_pct)
            or bool(self.d_at_volumes_cc)
            or bool(self.v_at_doses_gy)
        )

    def output_keys(self) -> list[str]:
        """Names of every output metric this config produces, in display order."""
        keys: list[str] = []
        if self.include_dmin:
            keys.append("dmin_gy")
        if self.include_dmean:
            keys.append("dmean_gy")
        if self.include_dmax:
            keys.append("dmax_gy")
        for v in self.d_at_volumes_pct:
            keys.append(f"d{_fmt_num(v)}_gy")
        for v in self.d_at_volumes_cc:
            keys.append(f"d{_fmt_num(v)}cc_gy")
        for d in self.v_at_doses_gy:
            keys.append(f"v{_fmt_num(d)}gy_cc")
        return keys


def _fmt_num(value: float) -> str:
    """Format a number for inclusion in a metric key — drops trailing ``.0``."""
    if isinstance(value, (int, float)) and float(value).is_integer():
        return str(int(value))
    return str(value)


# ---- The dose ------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class DoseGrid:
    """An RT Dose grid in Gy, sampled by trilinear interpolation.

    Any orientation: points are taken into the grid through its own row,
    column and frame directions, so head-first, feet-first, prone and
    decubitus grids are handled alike.
    """

    values: np.ndarray  # (frames, rows, columns), Gy
    origin: np.ndarray  # ImagePositionPatient: the first frame's first voxel, mm
    row_direction: np.ndarray  # along a row: increasing column index
    column_direction: np.ndarray  # down a column: increasing row index
    normal: np.ndarray  # from frame to frame
    pixel_spacing: tuple[float, float]  # (between rows, between columns), mm
    frame_offsets: np.ndarray  # along the normal from the origin, increasing, mm

    @classmethod
    def from_dataset(cls, ds) -> DoseGrid:
        # As the dose display reads it (core/dose.py): Gy when unstated, cGy
        # converted, anything else (RELATIVE) refused.
        units = str(getattr(ds, "DoseUnits", "") or "GY").strip().upper()
        if units not in ("GY", "CGY"):
            raise DVHError(f"the dose is in {units} units, not Gy")
        try:
            values = ds.pixel_array.astype(np.float64)
        except Exception as exc:  # noqa: BLE001 — any decoding failure is the same answer
            raise DVHError(f"the dose grid could not be read: {exc}") from exc
        values *= float(getattr(ds, "DoseGridScaling", None) or 1.0)
        if units == "CGY":
            values *= 0.01
        if values.ndim == 2:
            values = values[None]
        iop = np.asarray(ds.ImageOrientationPatient, float)
        row_direction, column_direction = iop[:3], iop[3:]
        offsets = np.asarray(getattr(ds, "GridFrameOffsetVector", None) or [0.0], float)
        if offsets.size != values.shape[0]:
            raise DVHError("the dose grid's frame offsets do not match its frames")
        # DICOM allows offsets relative to the first frame (the first is 0) or
        # absolute positions along the normal; both become relative here.
        offsets = offsets - offsets[0]
        order = np.argsort(offsets, kind="stable")
        spacing = ds.PixelSpacing
        return cls(
            values=np.ascontiguousarray(values[order]),
            origin=np.asarray(ds.ImagePositionPatient, float),
            row_direction=row_direction,
            column_direction=column_direction,
            normal=np.cross(row_direction, column_direction),
            pixel_spacing=(float(spacing[0]), float(spacing[1])),
            frame_offsets=np.ascontiguousarray(offsets[order]),
        )

    def sample(self, points: np.ndarray) -> np.ndarray:
        """Dose (Gy) at each ``(x, y, z)`` point in mm; NaN outside the grid."""
        out = np.empty(len(points))
        frames = np.arange(len(self.frame_offsets), dtype=np.float64)
        for start in range(0, len(points), CHUNK):
            d = points[start : start + CHUNK] - self.origin
            column = (d @ self.row_direction) / self.pixel_spacing[1]
            row = (d @ self.column_direction) / self.pixel_spacing[0]
            along = d @ self.normal
            if frames.size > 1:
                frame = np.interp(along, self.frame_offsets, frames, left=-1.0, right=frames.size)
            else:
                frame = np.where(np.abs(along) <= 1e-3, 0.0, -1.0)
            out[start : start + len(d)] = ndimage.map_coordinates(
                self.values, [frame, row, column], order=1, mode="constant", cval=np.nan
            )
        return out


class DoseHistogram:
    """A differential DVH built up sample by sample, in bins of ``BIN_GY``.

    Memory stays at one array of bins however many samples a structure takes.
    Dmin, Dmax and Dmean are kept exactly; D{x} is read to the centre of its
    bin, within half a bin (0.5 mGy) of the sample it stands for. A sample
    outside the dose grid has no dose to count: it is left out of the
    histogram, and its volume kept in ``outside_cc`` so the coverage is known.
    ``total_cc`` is the volume the statistics describe, the part inside.
    """

    def __init__(self) -> None:
        self.volume = np.zeros(1 << 16)
        self.total_cc = 0.0  # inside the dose grid
        self.outside_cc = 0.0
        self.dose_volume = 0.0
        self.low = math.inf
        self.high = -math.inf
        self.samples = 0

    def add(self, dose: np.ndarray, volume_cc: np.ndarray) -> None:
        self.samples += int(dose.size)
        outside = ~np.isfinite(dose)
        if outside.any():
            self.outside_cc += float(volume_cc[outside].sum())
            dose, volume_cc = dose[~outside], volume_cc[~outside]
        if not dose.size:
            return
        index = np.maximum(np.floor(dose / BIN_GY), 0).astype(np.int64)
        top = int(index.max()) + 1
        if top > self.volume.size:
            grown = np.zeros(max(top, 2 * self.volume.size))
            grown[: self.volume.size] = self.volume
            self.volume = grown
        self.volume[:top] += np.bincount(index, weights=volume_cc, minlength=top)
        self.total_cc += float(volume_cc.sum())
        self.dose_volume += float(np.dot(dose, volume_cc))
        self.low = min(self.low, float(dose.min()))
        self.high = max(self.high, float(dose.max()))

    @property
    def coverage_pct(self) -> float:
        """The share of the structure's volume inside the dose grid, in %."""
        whole = self.total_cc + self.outside_cc
        return 100.0 * self.total_cc / whole if whole > 0 else 0.0

    def _bins(self) -> tuple[np.ndarray, np.ndarray]:
        filled = np.nonzero(self.volume)[0]
        return (filled + 0.5) * BIN_GY, self.volume[filled]

    def dose_at(self, volume_cc: float) -> float:
        """The lowest dose the hottest ``volume_cc`` of the structure receives."""
        if volume_cc > self.total_cc * (1 + 1e-12):
            return math.nan
        dose, volume = self._bins()
        hottest_first = np.cumsum(volume[::-1])
        i = int(np.searchsorted(hottest_first, volume_cc - self.total_cc * 1e-12))
        return float(dose[::-1][min(i, dose.size - 1)])

    def volume_at(self, dose_gy) -> np.ndarray:
        """Volume (cc) receiving at least each dose: the cumulative DVH."""
        dose, volume = self._bins()
        at_least = np.cumsum(volume[::-1])[::-1]
        index = np.searchsorted(dose, np.asarray(dose_gy, float), side="left")
        return np.where(index < dose.size, at_least[np.minimum(index, dose.size - 1)], 0.0)

    def statistics(self, config: DVHConfig) -> tuple[dict[str, float], list[str]]:
        """The statistics ``config`` asks for, and notes on any it could not give."""
        out: dict[str, float] = {}
        notes: list[str] = []
        if config.include_dmin:
            out["dmin_gy"] = self.low
        if config.include_dmean:
            out["dmean_gy"] = self.dose_volume / self.total_cc
        if config.include_dmax:
            out["dmax_gy"] = self.high
        for v in config.d_at_volumes_pct:
            out[f"d{_fmt_num(v)}_gy"] = self.dose_at(self.total_cc * float(v) / 100.0)
        for v in config.d_at_volumes_cc:
            out[f"d{_fmt_num(v)}cc_gy"] = self.dose_at(float(v))
            if float(v) > self.total_cc:
                where = " inside the dose grid" if self.outside_cc > 0 else ""
                notes.append(
                    f"D{_fmt_num(v)}cc: the structure is only {self.total_cc:.3g} cc{where}"
                )
        for d in config.v_at_doses_gy:
            out[f"v{_fmt_num(d)}gy_cc"] = float(self.volume_at(float(d)))
        return out, notes


# ---- Where to sample -----------------------------------------------------


def odd_factor(spacing_mm: float, target_mm: float) -> int:
    """Sub-samples per voxel edge: the fewest no further apart than ``target_mm``, odd.

    Odd, so one sub-sample always sits on the voxel centre.
    """
    k = max(1, math.ceil(spacing_mm / target_mm - 1e-9))
    return k if k % 2 else k + 1


def _factors(spacing: np.ndarray, target_mm: float) -> tuple[int, int, int]:
    fx, fy, fz = (odd_factor(float(s), target_mm) for s in spacing)
    return fx, fy, fz


def choose_spacing(voxels: float, spacing: np.ndarray) -> float:
    """The finest of ``SPACINGS_MM`` keeping ``voxels`` worth of samples within the cap.

    ``voxels`` is the structure's size in CT voxels, so the sample count is
    known before any is taken. Past the cap even at 1 mm (a body contour, whose
    1 mm still means several samples per voxel on a coarse CT) the structure is
    sampled once per voxel, at :data:`VOXEL_CENTRES`: at that size the voxel is
    far below anything a dose statistic can resolve.
    """
    for target in SPACINGS_MM:
        fx, fy, fz = _factors(spacing, target)
        if voxels * fx * fy * fz <= MAX_SAMPLES:
            return target
    return VOXEL_CENTRES


def _index_to_world(image: sitk.Image, index: np.ndarray) -> np.ndarray:
    origin = np.asarray(image.GetOrigin(), float)
    spacing = np.asarray(image.GetSpacing(), float)
    direction = np.asarray(image.GetDirection(), float).reshape(3, 3)
    return origin + (index * spacing) @ direction.T


def _oriented_rings(region) -> list[np.ndarray]:
    """Every ring of a region, outer boundaries anticlockwise and holes clockwise."""
    rings = []
    for part in getattr(region, "geoms", [region]):
        part = orient(part, sign=1.0)
        rings.append(np.asarray(part.exterior.coords))
        rings.extend(np.asarray(hole.coords) for hole in part.interiors)
    return rings


def _edge_pieces(rings: list[np.ndarray]) -> tuple[np.ndarray, ...]:
    """The rings' edges cut at every unit grid line: ``(xa, ya, xb, yb)`` per piece.

    Each piece then lies within one cell ``[i, i+1] x [j, j+1]``.
    """
    x0 = np.concatenate([r[:-1, 0] for r in rings])
    y0 = np.concatenate([r[:-1, 1] for r in rings])
    x1 = np.concatenate([r[1:, 0] for r in rings])
    y1 = np.concatenate([r[1:, 1] for r in rings])
    n = x0.size

    def cuts(a0: np.ndarray, a1: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        lo = np.floor(np.minimum(a0, a1)) + 1  # grid lines strictly inside the edge
        hi = np.ceil(np.maximum(a0, a1)) - 1
        count = np.maximum(hi - lo + 1, 0).astype(np.int64)
        edge = np.repeat(np.arange(n), count)
        line = lo[edge] + (np.arange(int(count.sum())) - np.repeat(np.cumsum(count) - count, count))
        return edge, (line - a0[edge]) / (a1[edge] - a0[edge])

    ex, tx = cuts(x0, x1)
    ey, ty = cuts(y0, y1)
    edge = np.concatenate([np.arange(n), np.arange(n), ex, ey])
    t = np.concatenate([np.zeros(n), np.ones(n), tx, ty])
    order = np.lexsort((t, edge))
    edge, t = edge[order], t[order]
    same = edge[1:] == edge[:-1]
    e, ta, tb = edge[1:][same], t[:-1][same], t[1:][same]
    dx, dy = x1[e] - x0[e], y1[e] - y0[e]
    return x0[e] + ta * dx, y0[e] + ta * dy, x0[e] + tb * dx, y0[e] + tb * dy


def polygon_cells(region, fx: int, fy: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The sub-cells a region covers: where to sample each (voxel units), and its share.

    ``region`` is in continuous (column, row) index units. Sub-cells are
    ``1/fx`` by ``1/fy`` of a voxel, aligned to the voxel edges. Each is
    weighted by the exact area of the region inside it and sampled at the
    centroid of that area, found without clipping by the signed-area
    accumulation fonts are rasterised with. The outline is cut at every grid
    line; a piece adds the signed area between itself and its cell's right
    side, and its full height to every cell to its right in the same row, so a
    running sum along each row gives every cell its covered area. The same sums
    of first moments give the centroids. Outer boundaries run anticlockwise and
    holes clockwise, so a hole subtracts. The work grows with the outline's
    length, not the region's area.
    """
    minx, miny, maxx, maxy = region.bounds
    ix0, iy0 = math.floor((minx + 0.5) * fx), math.floor((miny + 0.5) * fy)
    nx = math.ceil((maxx + 0.5) * fx) - ix0
    ny = math.ceil((maxy + 0.5) * fy) - iy0
    scale, shift = np.array([fx, fy], float), np.array([ix0, iy0], float)
    unit = shapely.transform(region, lambda c: (c + 0.5) * scale - shift)  # cell i: [i, i+1]
    xa, ya, xb, yb = _edge_pieces(_oriented_rings(unit))
    i = np.clip(np.floor(0.5 * (xa + xb)).astype(np.int64), 0, nx - 1)
    j = np.clip(np.floor(0.5 * (ya + yb)).astype(np.int64), 0, ny - 1)
    # Each piece relative to its own cell's corner, so every term is of order one
    # and a sliver's centroid does not drown in cancellation.
    xa, xb, ya, yb = xa - i, xb - i, ya - j, yb - j
    dy = yb - ya
    band_y = 0.5 * (yb * yb - ya * ya)  # first moment in y of the piece's horizontal band
    # Within the piece's own cell: the part between the piece and the cell's right side.
    own_area = dy * (1.0 - 0.5 * (xa + xb))
    own_x = 0.5 * dy * (1.0 - (xa * xa + xa * xb + xb * xb) / 3.0)
    own_y = band_y - dy * (2 * ya * xa + ya * xb + yb * xa + 2 * yb * xb) / 6.0
    grids = np.zeros((5, ny, nx))
    for k, values in enumerate((own_area, own_x, own_y, dy, band_y)):
        np.add.at(grids[k], (j, i), values)
    own_area, own_x, own_y, height, height_y = grids
    # Every cell to the right in the row gets the piece's whole band.
    height = np.cumsum(height, axis=1) - height
    height_y = np.cumsum(height_y, axis=1) - height_y
    area = -(own_area + height)
    moment_x = -(own_x + 0.5 * height)
    moment_y = -(own_y + height_y)
    covered = area > SLIVER
    share = area[covered]
    row, column = np.nonzero(covered)
    x = (column + moment_x[covered] / share + ix0) / fx - 0.5
    y = (row + moment_y[covered] / share + iy0) / fy - 0.5
    return x, y, np.minimum(share, 1.0)


# ---- The DVH -------------------------------------------------------------


@dataclass(eq=False)
class DVHResult:
    """A structure's dose statistics, with the histogram they were read from."""

    metrics: dict[str, float]
    histogram: DoseHistogram
    source: str  # "contours" or "mask"
    spacing_mm: float  # the target chosen; VOXEL_CENTRES for one sample per voxel
    samples_per_voxel: tuple[int, int, int]  # along x, y, z
    voxel_mm: tuple[float, float, float]
    status: str  # "" unless something about the result needs saying

    def audit(self) -> dict[str, Any]:
        """What the sidecar records about how this DVH was taken."""
        return {
            "source": self.source,
            "subsample_target_mm": None if math.isinf(self.spacing_mm) else self.spacing_mm,
            "samples_per_voxel": list(self.samples_per_voxel),
            "subsample_spacing_mm": [
                round(v / k, 4) for v, k in zip(self.voxel_mm, self.samples_per_voxel)
            ],
            "samples": self.histogram.samples,
            "volume_in_dose_grid_cc": self.histogram.total_cc,
            "volume_outside_dose_grid_cc": self.histogram.outside_cc,
            "dose_grid_coverage_pct": self.coverage_pct,
            "bin_gy": BIN_GY,
        }

    @property
    def coverage_pct(self) -> float:
        """The share of the structure the statistics describe: inside the dose grid."""
        return self.histogram.coverage_pct


def _result(
    histogram: DoseHistogram,
    config: DVHConfig,
    source: str,
    spacing_mm: float,
    spacing: np.ndarray,
) -> DVHResult:
    if histogram.total_cc <= 0:
        if histogram.outside_cc > 0:
            raise DVHError("the structure lies wholly outside the dose grid")
        raise DVHError("the structure has no volume on the image")
    metrics, notes = histogram.statistics(config)
    return DVHResult(
        metrics,
        histogram,
        source,
        spacing_mm,
        _factors(spacing, spacing_mm),
        tuple(float(v) for v in spacing),
        "; ".join(notes),
    )


def _find_roi_contour(rtstruct_ds, roi_number: int):
    """Return the ROIContourSequence item for ``roi_number`` (or ``None``)."""
    for rc in getattr(rtstruct_ds, "ROIContourSequence", []) or []:
        if int(getattr(rc, "ReferencedROINumber", -1)) == int(roi_number):
            return rc
    return None


def structure_dvh(
    rtstruct_ds,
    roi_number: int,
    dose: DoseGrid,
    reference: sitk.Image,
    config: DVHConfig,
    *,
    z_extent_mm: tuple[float, float] | None = None,
    spacing_mm: float | None = None,
) -> DVHResult:
    """One structure's dose statistics, integrated over its contours.

    ``reference`` supplies the CT geometry the contours are read on — the CT
    itself or any mask made on it; its voxels are never read.
    ``z_extent_mm`` keeps only the slices whose centre lies within
    ``(z_lo, z_hi)``, the ground truth's extent, so a truncated comparison's
    dose describes the same range as its geometry. ``spacing_mm`` overrides the
    spacing rule; it is for validation, not for production.
    """
    item = _find_roi_contour(rtstruct_ds, roi_number)
    if item is None or not getattr(item, "ContourSequence", None):
        raise DVHError("no contours are stored for this structure")
    try:
        reading = read_structure(reference, item)
    except (MaskConversionError, ContourReadingError) as exc:
        raise DVHError(f"the contours could not be read: {exc}") from exc
    regions = {z: r for z, r in reading.regions.items() if not r.is_empty}
    if z_extent_mm is not None:
        lo, hi = z_extent_mm
        regions = {
            z: r
            for z, r in regions.items()
            if lo
            <= reference.TransformContinuousIndexToPhysicalPoint((0.0, 0.0, float(z)))[2]
            <= hi
        }
        if not regions:
            raise DVHError("no contour lies within the ground truth's extent")
    if not regions:
        raise DVHError("the structure has no area on the image")
    spacing = np.asarray(reference.GetSpacing(), float)
    if spacing_mm is None:
        spacing_mm = choose_spacing(sum(r.area for r in regions.values()), spacing)
    fx, fy, fz = _factors(spacing, spacing_mm)
    z_offsets = (np.arange(fz) + 0.5) / fz - 0.5
    cell_cc = float(np.prod(spacing)) / (fx * fy * fz) / 1000.0
    histogram = DoseHistogram()
    for z_index, region in regions.items():
        x, y, share = polygon_cells(region, fx, fy)
        for start in range(0, x.size, CHUNK):
            xs, ys = x[start : start + CHUNK], y[start : start + CHUNK]
            weight = share[start : start + CHUNK] * cell_cc
            for dz in z_offsets:
                index = np.stack([xs, ys, np.full(xs.size, z_index + dz)], axis=1)
                histogram.add(dose.sample(_index_to_world(reference, index)), weight)
    return _result(histogram, config, "contours", spacing_mm, spacing)


def mask_dvh(
    mask: sitk.Image,
    dose: DoseGrid,
    config: DVHConfig,
    *,
    spacing_mm: float | None = None,
) -> DVHResult:
    """Dose statistics of a structure that exists only as a mask.

    Each voxel is split into sub-samples by the same spacing rule the contours
    use, every one standing for an equal share of its voxel.
    """
    voxels = sitk.GetArrayViewFromImage(mask) > 0
    count = int(voxels.sum())
    if not count:
        raise DVHError("the mask is empty")
    spacing = np.asarray(mask.GetSpacing(), float)
    if spacing_mm is None:
        spacing_mm = choose_spacing(count, spacing)
    factors = _factors(spacing, spacing_mm)
    axes = [(np.arange(k) + 0.5) / k - 0.5 for k in factors]
    gx, gy, gz = np.meshgrid(*axes, indexing="ij")
    offsets = np.stack([gx.ravel(), gy.ravel(), gz.ravel()], axis=1)
    weight = float(np.prod(spacing)) / 1000.0 / len(offsets)
    per_chunk = max(1, CHUNK // len(offsets))
    histogram = DoseHistogram()
    for z in np.nonzero(voxels.any(axis=(1, 2)))[0]:
        y, x = np.nonzero(voxels[z])
        centres = np.stack([x, y, np.full(x.size, z)], axis=1).astype(np.float64)
        for start in range(0, len(centres), per_chunk):
            index = (centres[start : start + per_chunk, None, :] + offsets[None]).reshape(-1, 3)
            dose_gy = dose.sample(_index_to_world(mask, index))
            histogram.add(dose_gy, np.full(len(index), weight))
    return _result(histogram, config, "mask", spacing_mm, spacing)
