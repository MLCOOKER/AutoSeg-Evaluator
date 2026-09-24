"""Dose-volume statistics against answers known exactly.

In a linear dose the mean over a region is the dose at its centroid, and the
volume above a dose has a closed form for a slab or a square. So exact
coverage and exact centroids — the two things the method claims — can be
checked to rounding error, and the D{x} lookup to within one sub-sample.
The full benchmark (Nelms et al. 2015, disc phantoms, large structures) is
``scripts/validate_dvh_methods.py``; this file pins the parts it relies on.
"""

from __future__ import annotations

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
import shapely  # noqa: E402
import SimpleITK as sitk  # noqa: E402
from pydicom.dataset import Dataset, FileMetaDataset  # noqa: E402
from pydicom.uid import ExplicitVRLittleEndian  # noqa: E402

from autoseg_evaluator.core.dvh import (  # noqa: E402
    BIN_GY,
    MAX_SAMPLES,
    VOXEL_CENTRES,
    DoseGrid,
    DoseHistogram,
    DVHConfig,
    DVHError,
    choose_spacing,
    mask_dvh,
    odd_factor,
    polygon_cells,
    structure_dvh,
)
from autoseg_evaluator.core.masks import mask_with_reading  # noqa: E402
from autoseg_evaluator.workers.metrics_worker import MetricsWorker  # noqa: E402

CONFIG = DVHConfig(
    include_dmin=True,
    include_dmean=True,
    include_dmax=True,
    d_at_volumes_pct=[99.0, 95.0, 50.0, 5.0, 1.0],
    d_at_volumes_cc=[0.03],
    v_at_doses_gy=[50.0],
)
D0 = 50.0  # Gy at the origin


# ---- Builders -----------------------------------------------------------


def _image(spacing=(1.0, 1.0, 2.0), size=(80, 80, 30), origin=(-40.0, -40.0, -30.0)):
    """A CT geometry: the only part of an image a DVH reads."""
    image = sitk.Image(*size, sitk.sitkUInt8)
    image.SetSpacing(spacing)
    image.SetOrigin(origin)
    return image


def _circle(r: float, centre=(0.0, 0.0), n: int = 720) -> np.ndarray:
    theta = 2 * np.pi * np.arange(n) / n
    return np.column_stack([centre[0] + r * np.cos(theta), centre[1] + r * np.sin(theta)])


def _rectangle(x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], float)


def _structure(
    planes: dict[float, list[np.ndarray]],
) -> tuple[Dataset, dict[float, shapely.Geometry]]:
    """An RTSTRUCT whose ROI 1 holds ``planes``, and each plane's region as stored.

    Coordinates are stored as DICOM decimal strings, and the regions returned
    are built from those stored values, so exact answers stay exact.
    """
    item = Dataset()
    item.ReferencedROINumber = 1
    item.ContourSequence = []
    regions: dict[float, shapely.Geometry] = {}
    for z, loops in planes.items():
        region = None
        for loop in loops:
            stored = [f"{v:.6f}" for v in np.column_stack([loop, np.full(len(loop), z)]).ravel()]
            contour = Dataset()
            contour.ContourGeometricType = "CLOSED_PLANAR"
            contour.NumberOfContourPoints = len(loop)
            contour.ContourData = stored
            item.ContourSequence.append(contour)
            polygon = shapely.Polygon(np.asarray(stored, float).reshape(-1, 3)[:, :2])
            region = polygon if region is None else region.symmetric_difference(polygon)
        regions[z] = region
    roi = Dataset()
    roi.ROINumber = 1
    ds = Dataset()
    ds.StructureSetROISequence = [roi]
    ds.ROIContourSequence = [item]
    return ds, regions


def _linear_grid(gradient, spacing: float = 2.0, half: float = 40.0) -> DoseGrid:
    """A dose of ``D0 + gradient · p`` Gy on an axial grid covering ±``half`` mm."""
    axis = -half + spacing * np.arange(int(2 * half / spacing) + 1)
    z, y, x = np.meshgrid(axis, axis, axis, indexing="ij")
    gx, gy, gz = gradient
    return DoseGrid(
        values=D0 + gx * x + gy * y + gz * z,
        origin=np.array([-half, -half, -half]),
        row_direction=np.array([1.0, 0.0, 0.0]),
        column_direction=np.array([0.0, 1.0, 0.0]),
        normal=np.array([0.0, 0.0, 1.0]),
        pixel_spacing=(spacing, spacing),
        frame_offsets=spacing * np.arange(axis.size),
    )


def _rtdose(field, iop, offsets, origin, spacing=2.0, shape=(12, 10, 11), units="GY"):
    """An in-memory RTDOSE holding ``field(x, y, z)`` Gy on the grid described."""
    frames, rows, columns = shape
    row_dir, col_dir = np.asarray(iop[:3], float), np.asarray(iop[3:], float)
    normal = np.cross(row_dir, col_dir)
    rel = np.asarray(offsets, float) - offsets[0]
    k, r, c = np.meshgrid(np.arange(frames), np.arange(rows), np.arange(columns), indexing="ij")
    points = (
        np.asarray(origin, float)
        + c[..., None] * spacing * row_dir
        + r[..., None] * spacing * col_dir
        + rel[k][..., None] * normal
    )
    gy = field(points[..., 0], points[..., 1], points[..., 2])
    scale = 1e-5 if units != "CGY" else 1e-3
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.is_little_endian, ds.is_implicit_VR = True, False
    ds.DoseUnits = units
    ds.DoseGridScaling = f"{scale:g}"
    ds.ImagePositionPatient = [f"{v:.6f}" for v in origin]
    ds.ImageOrientationPatient = [f"{v:g}" for v in iop]
    ds.PixelSpacing = [f"{spacing:g}", f"{spacing:g}"]
    ds.GridFrameOffsetVector = [f"{v:.6f}" for v in offsets]
    ds.Rows, ds.Columns, ds.NumberOfFrames = rows, columns, frames
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = ds.BitsStored = 32
    ds.HighBit = 31
    ds.PixelRepresentation = 0
    stored = gy * (100.0 if units == "CGY" else 1.0) / scale
    ds.PixelData = np.round(stored).astype("<u4").tobytes()
    return ds


def _slab_centroid(regions: dict[float, shapely.Geometry]) -> tuple[np.ndarray, float]:
    """Centroid and area-sum of a stack of regions, each a slab of equal thickness."""
    areas = np.array([r.area for r in regions.values()])
    centres = np.array([[r.centroid.x, r.centroid.y, z] for z, r in regions.items()])
    return (areas[:, None] * centres).sum(axis=0) / areas.sum(), float(areas.sum())


# ---- Exact answers ------------------------------------------------------


@pytest.mark.parametrize(
    "gradient", [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (0.6, -0.3, 0.8)]
)
def test_mean_dose_is_the_dose_at_the_centroid(gradient):
    """In a linear dose the mean is exact only if coverage and centroids are."""
    planes = {
        z: [_circle(math.sqrt(49.0 - z * z), centre=(0.37, -0.21))]
        for z in (-6, -4, -2, 0, 2, 4, 6)
    }
    rtss, regions = _structure(planes)
    result = structure_dvh(rtss, 1, _linear_grid(gradient), _image(), CONFIG)

    centroid, area = _slab_centroid(regions)
    assert result.histogram.total_cc == pytest.approx(area * 2.0 / 1000.0, rel=1e-9)
    assert result.metrics["dmean_gy"] == pytest.approx(D0 + np.dot(gradient, centroid), abs=1e-6)


def test_a_hole_is_left_out_exactly():
    """Nested loops are a ring: its volume and mean say the hole was subtracted."""
    rtss, regions = _structure(
        {z: [_circle(8.0), _circle(4.0, centre=(1.3, 0.4))] for z in (-2, 0, 2)}
    )
    result = structure_dvh(rtss, 1, _linear_grid((1.0, 0.0, 0.0)), _image(), CONFIG)

    centroid, area = _slab_centroid(regions)
    assert area == pytest.approx(3 * math.pi * (64.0 - 16.0), rel=1e-3)
    assert result.histogram.total_cc == pytest.approx(area * 2.0 / 1000.0, rel=1e-9)
    assert result.metrics["dmean_gy"] == pytest.approx(D0 + centroid[0], abs=1e-6)


def test_dose_at_volume_through_plane_matches_the_slab():
    """Along z, a cylinder's D{x} is known: each contour fills a slab one slice thick."""
    rtss, _ = _structure({z: [_circle(6.0)] for z in (-4, -2, 0, 2, 4)})  # slab from -5 to 5
    result = structure_dvh(rtss, 1, _linear_grid((0.0, 0.0, 1.0)), _image(), CONFIG)

    step = 2.0 / odd_factor(2.0, result.spacing_mm)  # the sub-slab spacing
    for x in (99, 95, 50, 5, 1):
        truth = D0 + 5.0 - 10.0 * x / 100.0
        assert abs(result.metrics[f"d{x}_gy"] - truth) <= step / 2 + BIN_GY
    assert result.metrics["dmax_gy"] == pytest.approx(D0 + 5.0 - step / 2, abs=1e-9)
    assert result.metrics["dmin_gy"] == pytest.approx(D0 - 5.0 + step / 2, abs=1e-9)


def test_dose_at_volume_in_plane_matches_the_square():
    """Along x, a square's volume above a dose falls linearly; D{x} follows exactly."""
    x0, x1 = -4.63, 5.37
    rtss, _ = _structure({z: [_rectangle(x0, -3.1, x1, 6.9)] for z in (-2, 0, 2)})
    result = structure_dvh(rtss, 1, _linear_grid((1.0, 0.0, 0.0)), _image(), CONFIG)

    cell = 1.0 / odd_factor(1.0, result.spacing_mm)
    for x in (99, 95, 50, 5, 1):
        truth = D0 + x1 - (x1 - x0) * x / 100.0
        assert abs(result.metrics[f"d{x}_gy"] - truth) <= cell + BIN_GY
    assert result.metrics["v50gy_cc"] == pytest.approx(
        x1 * 10.0 * 6.0 / 1000.0, abs=cell * 60 / 1000
    )


def test_a_single_contour_fills_one_slice():
    """v2 had to invent a thickness for one plane; a slab is simply one slice."""
    rtss, regions = _structure({0.0: [_circle(5.0)]})
    result = structure_dvh(rtss, 1, _linear_grid((1.0, 0.0, 0.0)), _image(), CONFIG)
    assert result.histogram.total_cc == pytest.approx(regions[0.0].area * 2.0 / 1000.0, rel=1e-9)


def test_accumulated_coverage_equals_clipping():
    """The signed-area accumulation against clipping every sub-cell with shapely."""
    star = [
        (
            (7 if k % 2 else 3.2) * math.cos(math.pi * k / 7) + 0.31,
            (7 if k % 2 else 3.2) * math.sin(math.pi * k / 7) - 0.17,
        )
        for k in range(14)
    ]
    region = shapely.Polygon(star, [_circle(1.5, n=40).tolist()])
    for f in (1, 3, 5):
        x, y, share = polygon_cells(region, f, f)
        h = 1.0 / f
        # Each sample lies in its own sub-cell; sub-cells are aligned to voxel edges.
        x0 = np.floor((x + 0.5) * f) / f - 0.5
        y0 = np.floor((y + 0.5) * f) / f - 0.5
        clipped = shapely.intersection(shapely.box(x0, y0, x0 + h, y0 + h), region)
        assert share == pytest.approx(shapely.area(clipped) / (h * h), abs=1e-12)
        centroid = shapely.get_coordinates(shapely.centroid(clipped))
        assert np.max(np.abs(centroid - np.column_stack([x, y]))) < 1e-9
        assert share.sum() * h * h == pytest.approx(region.area, rel=1e-12)


def test_histogram_reads_dose_at_volume_from_the_hottest_down():
    histogram = DoseHistogram()
    histogram.add(np.array([1.0, 2.0, 3.0, 4.0]), np.full(4, 0.25))
    stats, notes = histogram.statistics(DVHConfig(True, True, True, [50.0, 100.0], [0.25], [2.5]))
    assert stats["dmean_gy"] == pytest.approx(2.5)
    assert stats["dmin_gy"] == 1.0 and stats["dmax_gy"] == 4.0
    assert stats["d50_gy"] == pytest.approx(3.0, abs=BIN_GY)  # the hottest half gets at least 3
    assert stats["d100_gy"] == pytest.approx(1.0, abs=BIN_GY)
    assert stats["d0.25cc_gy"] == pytest.approx(4.0, abs=BIN_GY)
    assert stats["v2.5gy_cc"] == pytest.approx(0.5)
    assert notes == []


# ---- The spacing rule ---------------------------------------------------


def test_the_finest_spacing_within_ten_million_samples_is_chosen():
    spacing = np.array([0.977, 0.977, 2.0])  # 0.25 mm: 5x5x9 per voxel; 0.5: 3x3x5; 1: 1x1x3
    assert choose_spacing(MAX_SAMPLES / 225, spacing) == 0.25
    assert choose_spacing(MAX_SAMPLES / 225 + 1, spacing) == 0.5
    assert choose_spacing(MAX_SAMPLES / 45 + 1, spacing) == 1.0
    assert choose_spacing(MAX_SAMPLES / 3, spacing) == 1.0
    assert choose_spacing(MAX_SAMPLES / 3 + 1, spacing) == VOXEL_CENTRES  # a body contour


def test_small_structures_get_the_finest_spacing_and_large_ones_coarser():
    small, _ = _structure({0.0: [_circle(5.0)]})
    large, _ = _structure({float(z): [_circle(35.0)] for z in range(-24, 26, 2)})
    dose = _linear_grid((1.0, 0.0, 0.0), half=60.0)
    image = _image(size=(90, 90, 40), origin=(-45.0, -45.0, -40.0))
    assert structure_dvh(small, 1, dose, image, CONFIG).spacing_mm == 0.25
    assert structure_dvh(large, 1, dose, image, CONFIG).spacing_mm == 0.5


def test_past_the_cap_even_at_1_mm_a_structure_is_sampled_once_per_voxel(monkeypatch):
    """On a 1.37 mm CT, "1 mm" is still 27 samples a voxel: a 30 L body took 220 M."""
    monkeypatch.setattr("autoseg_evaluator.core.dvh.MAX_SAMPLES", 1_000)
    rtss, regions = _structure({z: [_circle(12.0)] for z in (-2.0, 0.0, 2.0)})
    result = structure_dvh(rtss, 1, _linear_grid((1.0, 0.0, 0.0)), _image(), CONFIG)

    assert result.spacing_mm == VOXEL_CENTRES
    assert result.samples_per_voxel == (1, 1, 1)
    assert result.audit()["subsample_target_mm"] is None
    _, area = _slab_centroid(regions)
    assert result.histogram.total_cc == pytest.approx(area * 2.0 / 1000.0, rel=1e-9)
    # One sample per voxel covered, wholly or in part: the area plus its rim.
    per_slice = area / 3.0  # mm², and a voxel here is 1 mm²
    assert 3 * per_slice <= result.histogram.samples <= 3 * (per_slice + 2 * math.pi * 12.0 + 4)


# ---- The dose grid ------------------------------------------------------


def _field(x, y, z):
    return 60.0 + 0.5 * x - 0.3 * y + 0.7 * z


@pytest.mark.parametrize(
    "iop",
    [
        (1, 0, 0, 0, 1, 0),  # head-first supine
        (-1, 0, 0, 0, 1, 0),  # feet-first supine
        (-1, 0, 0, 0, -1, 0),  # head-first prone
        (0, 1, 0, -1, 0, 0),  # decubitus
    ],
)
@pytest.mark.parametrize("convention", ["relative", "relative-descending", "absolute"])
def test_a_dose_grid_is_read_in_any_orientation(iop, convention):
    """Points are taken into the grid through its own axes, whatever they are."""
    origin = np.array([4.0, -3.0, -6.0])
    steps = 2.5 * np.arange(12)
    offsets = {
        "relative": steps,
        "relative-descending": -steps,
        "absolute": steps + float(np.dot(origin, np.cross(iop[:3], iop[3:]))),
    }[convention]
    grid = DoseGrid.from_dataset(_rtdose(_field, iop, offsets, origin, spacing=2.5))

    # Points well inside the grid: 11 columns along the row direction, 10 rows
    # along the column direction, 12 frames along the normal or against it.
    row, column = np.asarray(iop[:3], float), np.asarray(iop[3:], float)
    frames = np.cross(row, column) * (-1 if convention == "relative-descending" else 1)
    u, v, w = np.random.default_rng(1).uniform(0.5, 8.5, (3, 200))
    points = origin + np.outer(2.5 * u, row) + np.outer(2.5 * v, column) + np.outer(2.5 * w, frames)
    assert np.max(np.abs(grid.sample(points) - _field(*points.T))) < 1e-3


def test_dose_units():
    iop, offsets, origin = (1, 0, 0, 0, 1, 0), 2.0 * np.arange(12), np.zeros(3)
    in_cgy = DoseGrid.from_dataset(_rtdose(_field, iop, offsets, origin, units="CGY"))
    assert in_cgy.sample(np.array([[4.0, 4.0, 4.0]]))[0] == pytest.approx(_field(4, 4, 4), abs=1e-2)
    with pytest.raises(DVHError, match="RELATIVE"):
        DoseGrid.from_dataset(_rtdose(_field, iop, offsets, origin, units="RELATIVE"))


def test_the_part_outside_the_dose_grid_counts_as_zero_and_is_reported():
    rtss, regions = _structure({z: [_circle(6.0)] for z in (-4, -2, 0, 2, 4)})
    dose = _linear_grid((1.0, 0.0, 0.0), half=40.0)
    covering_half = DoseGrid(
        values=dose.values[:21],  # frames up to z = 0 only
        origin=dose.origin,
        row_direction=dose.row_direction,
        column_direction=dose.column_direction,
        normal=dose.normal,
        pixel_spacing=dose.pixel_spacing,
        frame_offsets=dose.frame_offsets[:21],
    )
    result = structure_dvh(rtss, 1, covering_half, _image(), CONFIG)

    _, area = _slab_centroid(regions)
    assert result.histogram.total_cc == pytest.approx(area * 2.0 / 1000.0, rel=1e-9)
    assert result.metrics["dmin_gy"] == 0.0
    assert "outside the dose grid" in result.status
    # Two whole slabs of five, and the upper 4 of the middle slab's 9 sub-slabs.
    assert float(result.status.split(" %")[0]) == pytest.approx(100 * (2 + 4 / 9) / 5, abs=0.05)


# ---- What goes in, and what cannot --------------------------------------


def test_truncation_keeps_only_slices_inside_the_extent():
    rtss, _ = _structure({z: [_circle(6.0)] for z in (-4, -2, 0, 2, 4)})
    dose, image = _linear_grid((0.0, 0.0, 1.0)), _image()
    full = structure_dvh(rtss, 1, dose, image, CONFIG)
    lower = structure_dvh(rtss, 1, dose, image, CONFIG, z_extent_mm=(-5.0, -1.0))
    assert lower.histogram.total_cc == pytest.approx(full.histogram.total_cc * 2 / 5, rel=1e-9)
    assert lower.metrics["dmax_gy"] < D0
    with pytest.raises(DVHError, match="extent"):
        structure_dvh(rtss, 1, dose, image, CONFIG, z_extent_mm=(20.0, 30.0))


def test_a_volume_larger_than_the_structure_has_no_dose_and_says_why():
    rtss, _ = _structure({0.0: [_circle(1.0)]})  # about 0.006 cc
    result = structure_dvh(rtss, 1, _linear_grid((1.0, 0.0, 0.0)), _image(), CONFIG)
    assert math.isnan(result.metrics["d0.03cc_gy"])
    assert "D0.03cc" in result.status


def test_no_contours_is_an_error_not_an_empty_dvh():
    rtss, _ = _structure({})
    with pytest.raises(DVHError, match="no contours"):
        structure_dvh(rtss, 1, _linear_grid((1.0, 0.0, 0.0)), _image(), CONFIG)


def test_a_mask_and_its_contours_agree_when_the_contours_follow_voxel_edges():
    """Voxel-aligned edges make the mask exact, so both sample the same points."""
    rtss, _ = _structure({z: [_rectangle(-5.5, -3.5, 4.5, 6.5)] for z in (-2.0, 0.0, 2.0)})
    image = _image()
    mask, _notes = mask_with_reading(image, rtss, 1)
    dose = _linear_grid((0.6, -0.3, 0.8))
    from_contours = structure_dvh(rtss, 1, dose, image, CONFIG)
    from_mask = mask_dvh(mask, dose, CONFIG)
    assert from_mask.histogram.total_cc == pytest.approx(
        from_contours.histogram.total_cc, rel=1e-12
    )
    for key, value in from_contours.metrics.items():
        assert from_mask.metrics[key] == pytest.approx(value, abs=BIN_GY + 1e-9), key
    assert from_mask.metrics["dmean_gy"] == pytest.approx(
        from_contours.metrics["dmean_gy"], abs=1e-9
    )


# ---- The worker ---------------------------------------------------------


def _worker(audit: bool = False) -> MetricsWorker:
    config = {"dvh": {"include_dmean": True, "include_dmax": True, "include_dmin": False}}
    if audit:
        config["audit"] = {"sidecar": True}
    return MetricsWorker(None, [], config)


def _dose_dataset(units="GY"):
    return _rtdose(
        lambda x, y, z: D0 + x,
        (1, 0, 0, 0, 1, 0),
        2.0 * np.arange(31),
        (-30.0, -30.0, -30.0),
        shape=(31, 31, 31),
        units=units,
    )


def test_the_worker_puts_statistics_status_and_audit_in_the_row(monkeypatch):
    worker = _worker(audit=True)
    monkeypatch.setattr(worker, "_load_dose", lambda *_: _dose_dataset())
    rtss, _ = _structure({z: [_circle(4.0, centre=(0.4, 0.0))] for z in (-2, 0, 2)})
    row = {"metrics": {}, "error": ""}
    group = {"patient_id": "P01", "gt_sop": "gt.1"}

    worker._dose_into_row(
        row, group, lambda d: structure_dvh(rtss, 1, d, _image(), worker._dvh_config)
    )

    assert set(row["metrics"]) == {"dmean_gy", "dmax_gy"}
    assert row["metrics"]["dmean_gy"] == pytest.approx(D0 + 0.4, abs=1e-6)
    assert row["error"] == ""
    assert row["audit"]["dvh"]["source"] == "contours"
    assert row["audit"]["dvh"]["subsample_target_mm"] == 0.25
    assert row["audit"]["dvh"]["samples_per_voxel"] == [5, 5, 9]
    assert row["audit"]["dvh"]["subsample_spacing_mm"] == [0.2, 0.2, 0.2222]


def test_an_unusable_dose_fails_the_dose_columns_only_and_is_remembered(monkeypatch):
    worker = _worker()
    relative = _dose_dataset("RELATIVE")
    monkeypatch.setattr(worker, "_load_dose", lambda *_: relative)  # cached, like the real one
    group = {"patient_id": "P01", "gt_sop": "gt.1"}
    for _ in range(2):
        row = {"metrics": {"dice": 0.9}, "error": ""}
        worker._dose_into_row(row, group, lambda d: pytest.fail("no DVH without a usable dose"))
        assert row["error"].startswith("DVH: the dose is in RELATIVE units")
        assert row["metrics"] == {"dice": 0.9}
    assert len(worker._dose_grid_cache) == 1


def test_a_patient_without_a_dose_gets_no_dose_columns(monkeypatch):
    worker = _worker()
    monkeypatch.setattr(worker, "_load_dose", lambda *_: None)
    row = {"metrics": {}, "error": ""}
    worker._dose_into_row(row, {"patient_id": "P01", "gt_sop": "gt.1"}, lambda d: pytest.fail())
    assert row == {"metrics": {}, "error": ""}


def test_the_dose_grids_are_released_with_the_rest_of_the_patient():
    worker = _worker()
    worker._dose_grid_cache[("P01", "dose.1")] = object()
    worker._dose_grid_cache[("P02", "dose.9")] = object()
    worker._evict_patient_caches("P01")
    assert list(worker._dose_grid_cache) == [("P02", "dose.9")]
