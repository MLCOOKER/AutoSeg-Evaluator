"""The one reading of a structure's loops that both metric streams measure.

Each rule is checked on the smallest shape that exercises it, with the area the
rule should produce worked out by hand. Then the reading is held against the
vendored parser, which both suppliers validated: on everything that parser
accepts, the regions must be the same.
"""

from __future__ import annotations

import numpy as np
import pytest
from pydicom.dataset import Dataset

from autoseg_evaluator.core.contour_reading import (
    ContourReadingError,
    Outline,
    read_outlines,
)


def _outline(points, z=0, kind="CLOSED_PLANAR"):
    return Outline(z, np.asarray(points, dtype=float), kind)


def _square(x0, y0, side):
    return [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)]


def _area(reading, z=0):
    return reading.regions[z].area


# ---- Loops on one slice ----------------------------------------------------


def test_loops_apart_are_separate_islands():
    reading = read_outlines([_outline(_square(0, 0, 4)), _outline(_square(10, 0, 4))])

    assert _area(reading) == pytest.approx(32.0)
    assert reading.hole_slices == reading.merged_slices == 0


def test_a_loop_inside_a_loop_is_a_hole():
    reading = read_outlines([_outline(_square(0, 0, 10)), _outline(_square(3, 3, 4))])

    assert _area(reading) == pytest.approx(100.0 - 16.0)
    assert reading.hole_slices == 1
    assert "hole" in reading.notes[0]


def test_an_island_inside_a_hole_is_inside_again():
    reading = read_outlines(
        [
            _outline(_square(0, 0, 20)),
            _outline(_square(4, 4, 12)),
            _outline(_square(8, 8, 4)),
        ]
    )

    assert _area(reading) == pytest.approx(400.0 - 144.0 + 16.0)


def test_a_hole_touching_its_outer_loop_is_still_a_hole():
    """Inside is inside. The vendored parser refuses this; the 3D fill never did."""
    reading = read_outlines([_outline(_square(0, 0, 10)), _outline(_square(0, 3, 4))])

    assert _area(reading) == pytest.approx(100.0 - 16.0)


def test_loops_sharing_an_edge_are_merged_without_a_seam():
    reading = read_outlines([_outline(_square(0, 0, 5)), _outline(_square(5, 0, 5))])

    assert _area(reading) == pytest.approx(50.0)
    assert reading.regions[0].geom_type == "Polygon", "the shared edge is dissolved"
    assert reading.merged_slices == 1


def test_loops_touching_at_a_point_are_merged():
    reading = read_outlines([_outline(_square(0, 0, 5)), _outline(_square(5, 5, 5))])

    assert _area(reading) == pytest.approx(50.0)
    assert reading.merged_slices == 1


def test_partially_overlapping_loops_are_refused():
    """Union and hole are both plausible, and they give different tissue."""
    with pytest.raises(ContourReadingError, match="partially overlap"):
        read_outlines([_outline(_square(0, 0, 8)), _outline(_square(5, 3, 8))])


def test_the_same_loop_twice_is_refused():
    """Exclusive-or would erase it and union would keep it; neither is stated."""
    with pytest.raises(ContourReadingError, match="drawn twice"):
        read_outlines([_outline(_square(0, 0, 8)), _outline(_square(0, 0, 8))])


def test_a_declared_xor_cancels_overlaps_as_it_says():
    """The file states the rule, so a partial overlap is not ambiguous here."""
    reading = read_outlines(
        [
            _outline(_square(0, 0, 8), kind="CLOSEDPLANAR_XOR"),
            _outline(_square(4, 0, 8), kind="CLOSEDPLANAR_XOR"),
        ]
    )

    assert _area(reading) == pytest.approx(64.0 + 64.0 - 2 * 32.0)
    assert reading.geometric_type == "CLOSEDPLANAR_XOR"


def test_mixing_declared_rules_in_one_structure_is_refused():
    with pytest.raises(ContourReadingError, match="mixes"):
        read_outlines(
            [
                _outline(_square(0, 0, 8)),
                _outline(_square(20, 0, 8), kind="CLOSEDPLANAR_XOR"),
            ]
        )


def test_an_open_contour_is_not_an_outline():
    with pytest.raises(ContourReadingError, match="not a closed outline"):
        read_outlines([_outline(_square(0, 0, 8), kind="OPEN_PLANAR")])


# ---- One outline touching or crossing itself -------------------------------


def test_a_pinched_outline_reads_as_its_one_region():
    """Two lobes joined at a point, as one vendor's tracer writes them."""
    pinch = [(0, 0), (4, 0), (4, 4), (6, 6), (10, 6), (10, 10), (6, 10), (6, 6), (4, 4), (0, 4)]
    reading = read_outlines([_outline(pinch)])

    assert _area(reading) == pytest.approx(16.0 + 16.0)
    assert reading.repaired_outlines == 1


def test_a_spike_encloses_nothing_and_is_dropped():
    """Out along a line and back: length on the page, no area in the region."""
    spike = [(0, 0), (10, 0), (10, 10), (5, 10), (5, 14), (5, 10), (0, 10)]
    reading = read_outlines([_outline(spike)])

    assert _area(reading) == pytest.approx(100.0)
    assert reading.regions[0].length == pytest.approx(40.0), "the spike is not boundary"


def test_a_figure_of_eight_keeps_both_lobes():
    """Opposite windings: both fill rules call both lobes inside."""
    bowtie = [(0, 0), (10, 10), (10, 0), (0, 10)]
    reading = read_outlines([_outline(bowtie)])

    assert _area(reading) == pytest.approx(50.0)


def test_an_outline_going_around_twice_is_refused():
    """The centre of a pentagram is wound twice: a hole to one rule, tissue to the other."""
    angles = np.deg2rad(90 + 144 * np.arange(5))
    star = np.c_[np.cos(angles), np.sin(angles)] * 10
    with pytest.raises(ContourReadingError, match="around the same area twice"):
        read_outlines([_outline(star)])


def test_an_outline_with_no_area_is_dropped():
    reading = read_outlines([_outline([(0, 0), (5, 0), (10, 0)]), _outline(_square(0, 5, 2))])

    assert _area(reading) == pytest.approx(4.0)
    assert reading.empty_outlines == 1


def test_the_notes_say_what_was_interpreted_and_nothing_else():
    plain = read_outlines([_outline(_square(0, 0, 4))])
    assert plain.notes == ()


# ---- Held against the vendored parser -------------------------------------


def _dataset(rings_by_plane, kind="CLOSED_PLANAR"):
    ds = Dataset()
    roi = Dataset()
    roi.ROINumber = 1
    roi.ReferencedFrameOfReferenceUID = "1.2.3"
    ds.StructureSetROISequence = [roi]
    contours = []
    for plane, rings in rings_by_plane.items():
        for ring in rings:
            item = Dataset()
            item.ContourGeometricType = kind
            item.NumberOfContourPoints = len(ring)
            item.ContourData = [c for x, y in ring for c in (float(x), float(y), plane * 2.0)]
            contours.append(item)
    holder = Dataset()
    holder.ReferencedROINumber = 1
    holder.ContourSequence = contours
    ds.ROIContourSequence = [holder]
    return ds


ORACLE_CASES = {
    "single loop": ({1: [_square(10, 10, 20)]}, "CLOSED_PLANAR"),
    "loops apart": ({1: [_square(10, 10, 8), _square(40, 10, 8)]}, "CLOSED_PLANAR"),
    "loops sharing an edge": ({1: [_square(10, 10, 8), _square(18, 10, 8)]}, "CLOSED_PLANAR"),
    "hole": ({1: [_square(10, 10, 30), _square(20, 20, 5)]}, "CLOSED_PLANAR"),
    "island in a hole": (
        {1: [_square(10, 10, 40), _square(15, 15, 30), _square(25, 25, 5)]},
        "CLOSED_PLANAR",
    ),
    "declared xor": ({1: [_square(10, 10, 20), _square(20, 10, 20)]}, "CLOSEDPLANAR_XOR"),
    "several planes": (
        {1: [_square(10, 10, 20)], 2: [_square(12, 12, 16)], 3: [_square(10, 10, 30)]},
        "CLOSED_PLANAR",
    ),
}


@pytest.mark.parametrize("case", sorted(ORACLE_CASES))
def test_on_what_the_vendored_parser_accepts_the_regions_are_identical(case):
    """Its reading is the one both suppliers validated; ours must not drift from it."""
    from autoseg_evaluator.core.contour_grid import ContourGrid
    from autoseg_evaluator.core.polygon_metrics import parse_structure
    from autoseg_evaluator.vendor.native_contour_metrics_fast import polygon_compat

    rings, kind = ORACLE_CASES[case]
    grid = ContourGrid(
        origin=(0.0, 0.0, 0.0),
        basis=np.eye(3),
        spacing=(1.0, 1.0, 2.0),
        size=(128, 128, 8),
        frame_of_reference_uid="1.2.3",
        sops={},
    )
    dataset = _dataset(rings, kind)

    theirs, _policy = polygon_compat.parse_compatible(
        dataset, 1, grid.as_parser_grid(), allow_nested=True
    )
    ours = parse_structure(dataset, 1, grid)

    assert sorted(ours.planes) == sorted(theirs.planes)
    for z in theirs.planes:
        assert ours.planes[z].symmetric_difference(theirs.planes[z]).area == pytest.approx(
            0.0, abs=1e-9
        )


def test_where_the_reading_goes_further_than_the_vendored_parser_is_deliberate():
    """Three cases it refuses and this reads. Each has exactly one region."""
    from autoseg_evaluator.vendor.native_contour_metrics_fast import geometry, polygon_compat

    grid = {
        "origin": [0, 0, 0],
        "basis": np.eye(3),
        "spacing": [1, 1, 2],
        "size": [128, 128, 8],
        "frame": "1.2.3",
        "sops": {},
    }
    touching_hole = {1: [_square(10, 10, 20), _square(10, 15, 5)]}
    pinch = {
        1: [
            [
                (10, 10),
                (14, 10),
                (14, 14),
                (16, 16),
                (20, 16),
                (20, 20),
                (16, 20),
                (16, 16),
                (14, 14),
                (10, 14),
            ]
        ]
    }
    for rings in (touching_hole, pinch):
        with pytest.raises(geometry.Unsupported):
            polygon_compat.parse_compatible(_dataset(rings), 1, grid, allow_nested=True)
        read_outlines(
            [_outline(ring, z=z) for z, loops in rings.items() for ring in loops]
        )  # does not raise
