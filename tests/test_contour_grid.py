"""The image frame the polygon metrics are measured in.

What is checked here is mostly what the grid *refuses*. A wrong grid does not
make metrics slightly wrong — it assigns contours to the wrong slice, which
changes which contours are compared at all and produces a confident number for
a comparison that never happened. So every condition that makes the frame
ambiguous has to fail loudly, and the reason has to say what was measured.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pydicom
import pytest
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

from autoseg_evaluator.core.contour_grid import (
    GRID_REGULARITY_TOLERANCE_MM,
    ContourGrid,
    GridUnavailableError,
    build_grid,
)

ORIGIN = (-250.0, -250.0, -100.0)
PIXEL_SPACING = (1.171875, 1.171875)
SLICE_MM = 2.5


def _slice(
    folder: Path,
    index: int,
    *,
    for_uid: str,
    orientation=(1, 0, 0, 0, 1, 0),
    z: float | None = None,
    rows: int = 512,
    columns: int = 512,
    sop_uid: str | None = None,
    name: str | None = None,
) -> str:
    """One CT slice header on disk. Only the tags the grid reads are set."""
    sop = sop_uid or generate_uid()
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = CTImageStorage
    meta.MediaStorageSOPInstanceUID = sop
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = generate_uid()

    ds = FileDataset(str(folder / "x.dcm"), Dataset(), file_meta=meta, preamble=b"\0" * 128)
    ds.SOPInstanceUID = sop
    ds.Modality = "CT"
    ds.FrameOfReferenceUID = for_uid
    ds.ImageOrientationPatient = list(orientation)
    ds.ImagePositionPatient = [
        ORIGIN[0],
        ORIGIN[1],
        ORIGIN[2] + (index * SLICE_MM if z is None else z),
    ]
    ds.PixelSpacing = [PIXEL_SPACING[1], PIXEL_SPACING[0]]
    ds.Rows = rows
    ds.Columns = columns
    ds.is_little_endian = True
    ds.is_implicit_VR = False

    path = folder / (name or f"slice_{index:03d}.dcm")
    pydicom.dcmwrite(str(path), ds)
    return str(path)


def _series(folder: Path, count: int = 6, **kwargs) -> list[str]:
    for_uid = kwargs.pop("for_uid", generate_uid())
    return [_slice(folder, i, for_uid=for_uid, **kwargs) for i in range(count)]


def test_it_builds_the_frame_from_a_well_formed_series(tmp_path):
    grid = build_grid(_series(tmp_path))

    assert isinstance(grid, ContourGrid)
    assert grid.origin == pytest.approx(ORIGIN)
    assert grid.spacing == pytest.approx((PIXEL_SPACING[0], PIXEL_SPACING[1], SLICE_MM))
    assert grid.size == (512, 512, 6)
    assert grid.slices == 6
    assert len(grid.sops) == 6


def test_the_basis_is_orthonormal_and_projects_the_way_the_parser_expects(tmp_path):
    """``(xyz - origin) @ basis`` has to land slice *k* at ``k * spacing_z``.

    The vendored parser derives a contour's plane from exactly that product, so
    a basis assembled the other way round would put every contour on slice zero
    without erroring anywhere.
    """
    grid = build_grid(_series(tmp_path))

    assert np.allclose(grid.basis.T @ grid.basis, np.eye(3), atol=1e-12)
    for k in range(6):
        point = np.array([ORIGIN[0], ORIGIN[1], ORIGIN[2] + k * SLICE_MM])
        local = (point - np.asarray(grid.origin)) @ grid.basis
        assert local[2] == pytest.approx(k * grid.spacing[2], abs=1e-9)


def test_slices_are_ordered_by_position_not_by_filename(tmp_path):
    """Instance numbers and filenames are routinely reversed or absent.

    Only the projection onto the slice normal means anything, so the file order
    handed in must not survive into the plane numbering.
    """
    for_uid = generate_uid()
    files = [
        _slice(tmp_path, 2, for_uid=for_uid, name="c_top.dcm"),
        _slice(tmp_path, 0, for_uid=for_uid, name="a_bottom.dcm"),
        _slice(tmp_path, 1, for_uid=for_uid, name="b_middle.dcm"),
    ]
    grid = build_grid(files)

    assert grid.origin[2] == pytest.approx(ORIGIN[2])
    bottom = pydicom.dcmread(files[1], stop_before_pixels=True)
    top = pydicom.dcmread(files[0], stop_before_pixels=True)
    assert grid.sops[str(bottom.SOPInstanceUID)] == 0
    assert grid.sops[str(top.SOPInstanceUID)] == 2


def test_a_single_slice_has_no_spacing_to_derive(tmp_path):
    with pytest.raises(GridUnavailableError, match="at least two slices"):
        build_grid(_series(tmp_path, count=1))


def test_two_frames_of_reference_cannot_share_one_frame(tmp_path):
    files = _series(tmp_path, count=3)
    files.append(_slice(tmp_path, 3, for_uid=generate_uid(), name="other_frame.dcm"))

    with pytest.raises(GridUnavailableError, match="more than one Frame of Reference"):
        build_grid(files)


def test_mixed_slice_orientations_are_not_one_stack(tmp_path):
    for_uid = generate_uid()
    files = [_slice(tmp_path, i, for_uid=for_uid) for i in range(3)]
    files.append(
        _slice(tmp_path, 3, for_uid=for_uid, orientation=(1, 0, 0, 0, 0, -1), name="tilted.dcm")
    )

    with pytest.raises(GridUnavailableError, match="same plane orientation"):
        build_grid(files)


def test_mixed_slice_sizes_are_not_one_stack(tmp_path):
    for_uid = generate_uid()
    files = [_slice(tmp_path, i, for_uid=for_uid) for i in range(3)]
    files.append(_slice(tmp_path, 3, for_uid=for_uid, rows=256, columns=256, name="small.dcm"))

    with pytest.raises(GridUnavailableError, match="same size"):
        build_grid(files)


def test_an_uneven_stack_is_refused_and_the_reason_quantifies_it(tmp_path):
    """A gap this adapter cannot place a contour in, stated as a measurement.

    The metrics themselves do not need a constant slice thickness — this is an
    adapter limit, and the message says so, because the alternative is a user
    concluding the method cannot handle their data.
    """
    for_uid = generate_uid()
    files = [_slice(tmp_path, i, for_uid=for_uid) for i in range(4)]
    files.append(_slice(tmp_path, 4, for_uid=for_uid, z=4 * SLICE_MM + 0.9, name="gap.dcm"))

    with pytest.raises(GridUnavailableError) as raised:
        build_grid(files)
    message = str(raised.value)
    assert "not evenly spaced" in message
    assert "mm" in message
    assert "do not require a constant" in message


def test_sub_tolerance_jitter_is_accepted(tmp_path):
    """Positions are decimal strings, so exact equality is not the bar.

    The bar is whether a contour still lands on its own slice, and the tolerance
    is set an order of magnitude inside the parser's own planarity allowance.
    """
    for_uid = generate_uid()
    jitter = GRID_REGULARITY_TOLERANCE_MM / 10
    files = [
        _slice(tmp_path, i, for_uid=for_uid, z=i * SLICE_MM + (jitter if i % 2 else -jitter))
        for i in range(5)
    ]

    grid = build_grid(files)
    assert grid.size[2] == 5


def test_two_slices_sharing_a_sop_uid_make_a_contour_reference_ambiguous(tmp_path):
    for_uid = generate_uid()
    shared = generate_uid()
    files = [
        _slice(tmp_path, 0, for_uid=for_uid, sop_uid=shared, name="one.dcm"),
        _slice(tmp_path, 1, for_uid=for_uid, sop_uid=shared, name="two.dcm"),
        _slice(tmp_path, 2, for_uid=for_uid, name="three.dcm"),
    ]

    with pytest.raises(GridUnavailableError, match="share one SOPInstanceUID"):
        build_grid(files)


def test_slices_stacked_at_one_position_have_no_spacing(tmp_path):
    for_uid = generate_uid()
    files = [_slice(tmp_path, i, for_uid=for_uid, z=0.0, name=f"flat_{i}.dcm") for i in range(3)]

    with pytest.raises(GridUnavailableError, match="same position"):
        build_grid(files)


def test_a_missing_geometry_tag_is_a_reason_not_a_crash(tmp_path):
    files = _series(tmp_path, count=3)
    ds = pydicom.dcmread(files[1])
    del ds.ImagePositionPatient
    pydicom.dcmwrite(files[1], ds)

    with pytest.raises(GridUnavailableError, match="missing ImagePositionPatient"):
        build_grid(files)


def test_the_parser_dict_carries_everything_the_vendored_parser_reads(tmp_path):
    grid = build_grid(_series(tmp_path))
    parser_grid = grid.as_parser_grid()

    assert set(parser_grid) == {"origin", "basis", "spacing", "size", "frame", "sops"}
    assert parser_grid["size"] == [512, 512, 6]
    # Mutating the conversion must not reach back into the grid it came from.
    parser_grid["sops"].clear()
    assert len(grid.sops) == 6
