"""Tests for the two mask rasteriser backends (legacy vs continuous).

Scope is conformance, not independent geometric validation:

* the vectorised coordinate transform reproduces SimpleITK's own per-point call,
* the continuous backend reproduces dcmrtstruct2nii's engine on supported input,
* the deliberate deviations from upstream (bounds / geometry types) hold.
"""

from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk
from pydicom.dataset import Dataset

from autoseg_evaluator.core import masks as m

SIZE = (64, 64, 8)  # (x, y, z)


def _image(*, oblique: bool = False) -> sitk.Image:
    img = sitk.Image(*SIZE, sitk.sitkUInt8)
    img.SetSpacing((0.9765, 0.9765, 2.5))
    img.SetOrigin((-31.7, -28.4, -12.5))
    if oblique:
        theta = np.deg2rad(13.0)
        c, s = float(np.cos(theta)), float(np.sin(theta))
        img.SetDirection((c, -s, 0.0, s, c, 0.0, 0.0, 0.0, 1.0))
    return img


def _contour_data(img: sitk.Image, verts_xy, z_index: float) -> list[float]:
    """Flat DICOM ContourData for vertices given in *index* space.

    Built via the image transform so the contour genuinely lies in the image
    plane even when the image is obliquely oriented.
    """
    out: list[float] = []
    for x, y in verts_xy:
        out.extend(
            img.TransformContinuousIndexToPhysicalPoint((float(x), float(y), float(z_index)))
        )
    return out


def _roi(contour_datas, gtype: str = "CLOSED_PLANAR") -> Dataset:
    seq = []
    for data in contour_datas:
        c = Dataset()
        c.ContourGeometricType = gtype
        c.ContourData = list(data)
        seq.append(c)
    roi = Dataset()
    roi.ContourSequence = seq
    return roi


def _square(cx=32.0, cy=32.0, half=9.3):
    return [
        (cx - half, cy - half),
        (cx + half, cy - half),
        (cx + half, cy + half),
        (cx - half, cy + half),
    ]


# ---- Vectorised transform -------------------------------------------------


@pytest.mark.parametrize("oblique", [False, True])
def test_vectorised_transform_matches_simpleitk(oblique):
    img = _image(oblique=oblique)
    pts = np.random.default_rng(0).uniform(-60, 60, size=(400, 3))
    reference = np.array([img.TransformPhysicalPointToContinuousIndex(tuple(p)) for p in pts])
    assert np.allclose(m.physical_points_to_continuous_index(img, pts), reference, atol=1e-9)


# ---- Conformance with the upstream engine ---------------------------------


def test_continuous_backend_matches_dcmrtstruct2nii_engine():
    upstream = pytest.importorskip(
        "dcmrtstruct2nii.adapters.convert.rtstructcontour2mask"
    ).DcmPatientCoords2Mask()
    img = _image()
    # Two stacked squares on one slice (outer + inner) exercise the XOR hole
    # path, plus a square on a second slice.
    datas = [
        _contour_data(img, _square(), 3),
        _contour_data(img, _square(half=4.1), 3),
        _contour_data(img, _square(cx=20.0, cy=40.0, half=6.7), 5),
    ]

    contour_dicts = []
    for data in datas:
        pts = np.asarray(data, dtype=float).reshape(-1, 3)
        contour_dicts.append(
            {
                "type": "CLOSED_PLANAR",
                "name": "roi",
                "points": {
                    "x": pts[:, 0].tolist(),
                    "y": pts[:, 1].tolist(),
                    "z": pts[:, 2].tolist(),
                },
            }
        )

    theirs = sitk.GetArrayFromImage(upstream.convert(contour_dicts, img, 0, 1)).astype(bool)
    ours = m._rasterise_roi_continuous(img, _roi(datas))
    assert ours is not None
    assert np.array_equal(ours, theirs)
    assert ours.any()  # the fixture actually drew something


# ---- Deliberate deviations from upstream ----------------------------------


def test_unsupported_geometry_returns_none_not_empty_mask():
    """An open contour must surface as a failed conversion, not volume 0."""
    img = _image()
    roi = _roi([_contour_data(img, _square(), 3)], gtype="OPEN_PLANAR")
    assert m._rasterise_roi_continuous(img, roi) is None


def test_a_declared_xor_structure_now_gets_a_mask():
    """The shared reading accepts it, so the 3D stream does too.

    It used to get no mask at all while the 2D stream read it — the same
    structure present in one stream and absent from the other.
    """
    img = _image()
    roi = _roi(
        [_contour_data(img, _square(), 3), _contour_data(img, _square(half=4.1), 3)],
        gtype="CLOSEDPLANAR_XOR",
    )
    volume = m._rasterise_roi_continuous(img, roi)
    assert volume is not None
    assert volume[3].any()
    assert not volume[3, 32, 32], "the inner loop is a hole"


def test_out_of_range_slice_is_skipped_not_wrapped():
    """A contour below the volume must not paint the last slice (upstream bug)."""
    img = _image()
    roi = _roi([_contour_data(img, _square(), -1)])
    volume = m._rasterise_roi_continuous(img, roi)
    assert volume is not None
    assert not volume.any(), "negative slice index wrapped around to the end"


def test_non_planar_contour_aborts_roi():
    img = _image()
    # Vertices deliberately spread across slices in index space.
    data: list[float] = []
    for (x, y), z in zip(_square(), [3.0, 3.0, 5.0, 3.0]):
        data.extend(img.TransformContinuousIndexToPhysicalPoint((float(x), float(y), z)))
    assert m._rasterise_roi_continuous(img, _roi([data])) is None


def test_geometry_is_preserved_through_the_public_api():
    """The upstream engine drops spacing/origin/direction; ours must not."""
    img = _image(oblique=True)
    rtss = Dataset()
    struct = Dataset()
    struct.ROINumber = 1
    struct.ROIName = "Test ROI"
    rtss.StructureSetROISequence = [struct]
    roi = _roi([_contour_data(img, _square(), 3)])
    roi.ReferencedROINumber = 1
    rtss.ROIContourSequence = [roi]

    mask = m.extract_mask_for_roi(img, rtss, 1, backend=m.RASTERISER_CONTINUOUS)
    assert mask is not None
    assert mask.GetSpacing() == pytest.approx(img.GetSpacing())
    assert mask.GetOrigin() == pytest.approx(img.GetOrigin())
    assert mask.GetDirection() == pytest.approx(img.GetDirection())
    assert sitk.GetArrayFromImage(mask).sum() > 0


# ---- Filling the shared reading --------------------------------------------


def _unit_image() -> sitk.Image:
    """1 mm voxels at the origin, so index and millimetre coordinates coincide."""
    return sitk.Image(24, 16, 3, sitk.sitkUInt8)


def _flat(verts_xy, z=1.0):
    return [v for x, y in verts_xy for v in (float(x), float(y), z)]


def _box(x0, y0, side):
    return [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)]


def test_away_from_edge_ties_the_fill_is_the_previous_fill():
    """Where no voxel centre sits exactly on an edge, nothing may change.

    Random star-shaped outlines at arbitrary sub-voxel positions, filled by the
    new scanline and by scikit-image's point-in-polygon, which the previous
    continuous backend used. They must agree voxel for voxel.
    """
    from skimage.draw import polygon

    rng = np.random.default_rng(7)
    img = _image()
    for _ in range(25):
        n = int(rng.integers(5, 40))
        angles = np.sort(rng.uniform(0, 2 * np.pi, n))
        radius = rng.uniform(3, 20, n)
        cx, cy = rng.uniform(22, 42, 2)
        verts = np.c_[cx + radius * np.cos(angles), cy + radius * np.sin(angles)]
        ours = m._rasterise_roi_continuous(img, _roi([_contour_data(img, verts, 3)]))
        previous = np.zeros(SIZE[1::-1], dtype=bool)
        rr, cc = polygon(verts[:, 1], verts[:, 0], shape=previous.shape)
        previous[rr, cc] = True
        assert np.array_equal(ours[3], previous)


def test_a_centre_on_an_edge_belongs_to_one_side():
    """A 5 x 5 mm square with its edges on voxel centres is 25 voxels, not 36."""
    img = _unit_image()
    volume = m._rasterise_roi_continuous(img, _roi([_flat(_box(1.0, 1.0, 5))]))
    assert int(volume.sum()) == 25


def test_loops_sharing_an_edge_leave_no_seam():
    """Merged by the reading, filled once: no background column between them."""
    img = _unit_image()
    volume = m._rasterise_roi_continuous(
        img, _roi([_flat(_box(1.0, 1.0, 5)), _flat(_box(6.0, 1.0, 5))])
    )
    assert int(volume.sum()) == 50
    assert volume[1, 1:6, 1:11].all()


def test_partially_overlapping_loops_get_no_mask_and_say_why():
    img = _unit_image()
    rtss = Dataset()
    struct = Dataset()
    struct.ROINumber = 1
    rtss.StructureSetROISequence = [struct]
    roi = _roi([_flat(_box(0.5, 0.5, 8)), _flat(_box(5.5, 3.5, 8))])
    roi.ReferencedROINumber = 1
    rtss.ROIContourSequence = [roi]

    with pytest.raises(m.MaskConversionError, match="partially overlap"):
        m.mask_with_reading(img, rtss, 1)
    assert m.extract_mask_for_roi(img, rtss, 1) is None


def test_the_3d_mask_is_the_fill_of_the_region_the_2d_stream_reads():
    """The point of the shared reading, checked end to end.

    One structure with a hole, two touching loops and a pinched outline, read by
    the 2D stream from its DICOM, and filled by the 3D stream from the same
    DICOM. Filling the 2D stream's regions must give the 3D mask exactly.
    """
    from autoseg_evaluator.core.contour_grid import ContourGrid
    from autoseg_evaluator.core.polygon_metrics import parse_structure

    img = sitk.Image(64, 64, 4, sitk.sitkUInt8)
    img.SetSpacing((0.9765, 0.9765, 2.5))
    img.SetOrigin((-31.7, -28.4, -12.5))
    pinch = [
        (10.3, 10.3),
        (18.3, 10.3),
        (18.3, 18.3),
        (24.3, 24.3),
        (30.3, 24.3),
        (30.3, 30.3),
        (24.3, 30.3),
        (24.3, 24.3),
        (18.3, 18.3),
        (10.3, 18.3),
    ]
    per_slice = {
        1: [_square(cx=30.3, cy=30.3, half=12.1), _square(cx=30.3, cy=30.3, half=4.2)],
        2: [_box(10.3, 10.3, 8.0), _box(18.3, 10.3, 8.0)],
        3: [pinch],
    }
    rtss = Dataset()
    struct = Dataset()
    struct.ROINumber = 1
    struct.ReferencedFrameOfReferenceUID = "1.2.3"
    rtss.StructureSetROISequence = [struct]
    roi = _roi([_contour_data(img, loop, z) for z, loops in per_slice.items() for loop in loops])
    for contour in roi.ContourSequence:
        contour.NumberOfContourPoints = len(contour.ContourData) // 3
    roi.ReferencedROINumber = 1
    rtss.ROIContourSequence = [roi]

    grid = ContourGrid(
        origin=tuple(img.GetOrigin()),
        basis=np.eye(3),
        spacing=tuple(img.GetSpacing()),
        size=tuple(img.GetSize()),
        frame_of_reference_uid="1.2.3",
        sops={},
    )
    regions = parse_structure(rtss, 1, grid)
    mask, notes = m.mask_with_reading(img, rtss, 1)
    volume = sitk.GetArrayFromImage(mask).astype(bool)

    from shapely import affinity

    for z in range(img.GetSize()[2]):
        expected = np.zeros(volume.shape[1:], dtype=bool)
        if z in regions.planes:
            in_voxels = affinity.scale(
                regions.planes[z], 1 / img.GetSpacing()[0], 1 / img.GetSpacing()[1], origin=(0, 0)
            )
            expected = m._fill_region(in_voxels, *volume.shape[1:])
        assert np.array_equal(volume[z], expected), f"slice {z}"
    # And both streams say the same about what they had to interpret.
    assert set(notes) == set(regions.reading_notes)
    assert len(notes) == 3  # a hole, a merge, a pinched outline


# ---- Backend selection ----------------------------------------------------


def test_backend_selection_round_trip():
    original = m.get_default_rasteriser()
    try:
        m.set_default_rasteriser(m.RASTERISER_CONTINUOUS)
        assert m.get_default_rasteriser() == m.RASTERISER_CONTINUOUS
        m.set_default_rasteriser(m.RASTERISER_LEGACY)
        assert m.get_default_rasteriser() == m.RASTERISER_LEGACY
    finally:
        m.set_default_rasteriser(original)


def test_unknown_backend_rejected():
    with pytest.raises(ValueError, match="Unknown rasteriser"):
        m.set_default_rasteriser("nope")


def test_default_backend_is_continuous():
    """The sub-voxel backend is the shipped default; legacy is opt-in."""
    assert m.get_default_rasteriser() == m.RASTERISER_CONTINUOUS


def test_backends_broadly_agree_on_an_axis_aligned_square():
    """Sanity check that the backends differ only at the sub-voxel boundary."""
    img = _image()
    roi = _roi([_contour_data(img, _square(), 3)])
    legacy = m._rasterise_roi_legacy(img, roi)
    continuous = m._rasterise_roi_continuous(img, roi)
    assert legacy is not None and continuous is not None
    overlap = np.logical_and(legacy, continuous).sum()
    dice = 2 * overlap / (legacy.sum() + continuous.sum())
    assert dice > 0.9
