"""The adapter between the application and the vendored polygon kernels.

The kernels have their own acceptance suites, run in ``tests/vendor`` and
against the published synthetic study. What is checked here is everything
between them and a results row: which engine gets chosen, how contour topology
is resolved, and — most of all — that a metric which cannot be computed is
reported as such rather than filled in.

The two engines are independent implementations of the same definitions, one
sampling and one analytic, so running both over the same input and comparing
them is a real check rather than a tautology.
"""

from __future__ import annotations

import numpy as np
import pytest
from pydicom.dataset import Dataset
from pydicom.uid import generate_uid
from shapely.geometry import Polygon, box

from autoseg_evaluator.core.contour_grid import ContourGrid
from autoseg_evaluator.core.polygon_metrics import (
    ENGINE_FAST,
    ENGINE_REFERENCE,
    ENGINE_VARIABLE,
    REFERENCE_SAMPLING_MM,
    ROW_KEYS,
    ContourRegions,
    ContoursUnavailableError,
    compare_structures,
    library_available,
    parse_structure,
    select_engine,
)

FRAME = generate_uid()


@pytest.fixture
def grid() -> ContourGrid:
    """An axis-aligned frame, so millimetres and plane indices read plainly."""
    return ContourGrid(
        origin=(0.0, 0.0, 0.0),
        basis=np.eye(3),
        spacing=(1.0, 1.0, 2.0),
        size=(512, 512, 20),
        frame_of_reference_uid=FRAME,
        sops={},
    )


def _rtss(rings_by_plane: dict[int, list[list[tuple[float, float]]]], *, kind="CLOSED_PLANAR"):
    """A structure set holding one ROI, built from explicit rings per plane."""
    ds = Dataset()
    roi = Dataset()
    roi.ROINumber = 1
    roi.ROIName = "Test"
    roi.ReferencedFrameOfReferenceUID = FRAME
    ds.StructureSetROISequence = [roi]

    contours = []
    for plane, rings in sorted(rings_by_plane.items()):
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


def _square(x0, y0, side):
    return [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)]


def _regions(planes, geometry):
    return ContourRegions(planes=dict(planes), geometric_type=geometry, vertices=0)


# ---- Choosing an engine ----------------------------------------------------


def test_the_compiled_engine_is_the_default_where_a_library_exists(monkeypatch):
    monkeypatch.delenv(ENGINE_VARIABLE, raising=False)
    engine = select_engine()

    expected = ENGINE_FAST if library_available() else ENGINE_REFERENCE
    assert engine.name == expected
    assert engine.version


def test_either_engine_can_be_demanded_explicitly(monkeypatch):
    monkeypatch.delenv(ENGINE_VARIABLE, raising=False)
    assert select_engine(ENGINE_REFERENCE).name == ENGINE_REFERENCE
    monkeypatch.setenv(ENGINE_VARIABLE, ENGINE_REFERENCE)
    assert select_engine().name == ENGINE_REFERENCE


def test_an_unrecognised_engine_name_is_refused_rather_than_ignored(monkeypatch):
    """Silently falling back would hide a typo in a deployment's environment."""
    monkeypatch.setenv(ENGINE_VARIABLE, "quick")
    with pytest.raises(ValueError, match="must be"):
        select_engine()


def test_each_engine_records_the_settings_its_numbers_were_measured_under():
    """``error_mm`` means different things in the two kernels.

    In the compiled engine it is a quantile guard that changes nothing; in the
    sampling engine it is the step, and it costs three orders of magnitude. The
    settings have to travel with the number or the number cannot be reproduced.
    """
    reference = select_engine(ENGINE_REFERENCE).settings
    assert reference["sampling_mm"] == REFERENCE_SAMPLING_MM
    if library_available():
        assert "quantile_guard_mm" in select_engine(ENGINE_FAST).settings


# ---- Reading contours ------------------------------------------------------


def test_it_reads_a_plain_structure_into_planar_regions(grid):
    dataset = _rtss({3: [_square(10, 10, 20)], 4: [_square(10, 10, 20)]})

    regions = parse_structure(dataset, 1, grid)

    assert sorted(regions.planes) == [3, 4]
    assert regions.planes[3].area == pytest.approx(400.0)
    assert regions.nested_planes == 0
    assert not regions.empty


def test_a_structure_with_no_contour_sequence_is_a_reason_not_a_crash(grid):
    dataset = _rtss({})
    dataset.ROIContourSequence[0].ContourSequence = []

    with pytest.raises(ContoursUnavailableError, match="contours not readable"):
        parse_structure(dataset, 1, grid)


def test_nested_rings_are_refused_until_the_interpretation_is_asked_for(grid):
    """A ring inside a ring is unambiguous geometry and ambiguous intent.

    DICOM says "hole" with a keyhole contour or an explicit XOR. A nested plain
    loop says only that one ring contains another, so composing it is a claim
    about the exporter, and claims are opted into.
    """
    dataset = _rtss({3: [_square(0, 0, 40), _square(10, 10, 10)]})

    with pytest.raises(ContoursUnavailableError, match="Ambiguous overlapping"):
        parse_structure(dataset, 1, grid)

    composed = parse_structure(dataset, 1, grid, allow_nested=True)
    assert composed.nested_planes == 1
    # 40x40 outer less the 10x10 ring that sits wholly inside it.
    assert composed.planes[3].area == pytest.approx(1600.0 - 100.0)


def test_partially_overlapping_rings_stay_refused_even_when_nesting_is_allowed(grid):
    """The opt-in covers containment only; a partial overlap has no reading."""
    dataset = _rtss({3: [_square(0, 0, 20), _square(10, 10, 20)]})

    with pytest.raises(ContoursUnavailableError):
        parse_structure(dataset, 1, grid, allow_nested=True)


def test_an_explicit_xor_needs_no_opt_in(grid):
    """When the exporter states the parity, there is nothing to interpret."""
    dataset = _rtss({3: [_square(0, 0, 40), _square(10, 10, 10)]}, kind="CLOSEDPLANAR_XOR")

    regions = parse_structure(dataset, 1, grid)
    assert regions.planes[3].area == pytest.approx(1500.0)
    assert regions.nested_planes == 0


# ---- Measuring -------------------------------------------------------------


@pytest.mark.parametrize("engine_name", [ENGINE_FAST, ENGINE_REFERENCE])
def test_a_comparison_fills_every_column_it_promises(engine_name):
    if engine_name == ENGINE_FAST and not library_available():
        pytest.skip("no compiled library for this platform")
    reference = _regions({0: box(0, 0, 10, 10), 1: box(0, 0, 10, 10)}, "CLOSED_PLANAR")
    test = _regions({0: box(2, 2, 8, 8), 1: box(2, 2, 8, 8)}, "CLOSED_PLANAR")

    result = compare_structures(
        reference, test, tolerance_mm=2.0, engine=select_engine(engine_name)
    )

    assert result.available and not result.status
    assert set(ROW_KEYS) <= set(result.values)
    assert result.engine.startswith(engine_name)
    assert result.detail, "the full engine output is kept for the audit record"


@pytest.mark.parametrize("engine_name", [ENGINE_FAST, ENGINE_REFERENCE])
def test_a_known_offset_square_gives_the_distance_geometry_says_it_should(engine_name):
    """A 10 mm square inside a 10 mm square, inset by 2 mm on every side.

    The furthest a reference corner sits from the inner square is the corner
    diagonal, 2*sqrt(2); every inner point is exactly 2 mm from the outer.
    """
    if engine_name == ENGINE_FAST and not library_available():
        pytest.skip("no compiled library for this platform")
    reference = _regions({0: box(0, 0, 10, 10)}, "CLOSED_PLANAR")
    test = _regions({0: box(2, 2, 8, 8)}, "CLOSED_PLANAR")

    values = compare_structures(
        reference, test, tolerance_mm=2.0, engine=select_engine(engine_name)
    ).values

    assert values["poly_hd100_mm"] == pytest.approx(2 * np.sqrt(2), abs=1e-6)
    assert values["poly_planes_joint"] == 1


def test_the_two_engines_agree_within_the_sampling_engines_own_interval():
    """An analytic envelope and a sampled distribution, over one input.

    They share no code path for the distance distribution, so agreement is
    evidence. The tolerance is the sampling engine's discretisation bound, which
    is the most either can claim about the other.
    """
    if not library_available():
        pytest.skip("no compiled library for this platform")
    rng = np.random.default_rng(11)
    planes = {}
    other = {}
    for z in range(8):
        angles = np.linspace(0, 2 * np.pi, 48, endpoint=False)
        radius = 20 + rng.normal(0, 0.4, angles.size)
        planes[z] = Polygon(np.c_[radius * np.cos(angles), radius * np.sin(angles)])
        radius = 19 + rng.normal(0, 0.4, angles.size)
        other[z] = Polygon(np.c_[1.0 + radius * np.cos(angles), 0.5 + radius * np.sin(angles)])

    reference = _regions(planes, "CLOSED_PLANAR")
    test = _regions(other, "CLOSED_PLANAR")
    fast = compare_structures(reference, test, tolerance_mm=2.0, engine=select_engine(ENGINE_FAST))
    slow = compare_structures(
        reference, test, tolerance_mm=2.0, engine=select_engine(ENGINE_REFERENCE)
    )

    for key in ("poly_hd95_mm", "poly_mean_distance_mm", "poly_median_distance_mm"):
        assert fast.values[key] == pytest.approx(slow.values[key], abs=REFERENCE_SAMPLING_MM)
    # APL is exact in both: no sampling is involved on either side.
    assert fast.values["poly_apl_mm"] == pytest.approx(slow.values["poly_apl_mm"], abs=1e-9)


def test_a_structure_with_no_contours_is_undefined_not_zero():
    """The distinction the whole module exists to preserve.

    A zero here would read as perfect agreement in a cohort summary, and would
    be indistinguishable from one.
    """
    empty = _regions({}, "CLOSED_PLANAR")
    present = _regions({0: box(0, 0, 10, 10)}, "CLOSED_PLANAR")

    result = compare_structures(empty, present, tolerance_mm=2.0)

    assert not result.available
    assert "undefined" in result.status
    assert result.values == {}


def test_structures_sharing_no_plane_are_undefined_not_zero():
    """Two structures can both exist and still have nothing to compare.

    Distance metrics are defined on the planes both reach; when that set is
    empty there is no distance, and reporting one would invent an agreement.
    """
    reference = _regions({0: box(0, 0, 10, 10)}, "CLOSED_PLANAR")
    test = _regions({40: box(0, 0, 10, 10)}, "CLOSED_PLANAR")

    result = compare_structures(reference, test, tolerance_mm=2.0)

    assert not result.available
    assert "no common planes" in result.status.lower()
    assert result.values == {}


def test_planes_reached_by_only_one_structure_are_counted_not_hidden():
    """Excluding them is the paper's convention; concealing them is not.

    A model that declines the hard slices would otherwise be rewarded for
    declining them, which is the same failure the coverage table exists to stop.
    """
    reference = _regions({0: box(0, 0, 10, 10), 1: box(0, 0, 10, 10)}, "CLOSED_PLANAR")
    test = _regions({0: box(2, 2, 8, 8)}, "CLOSED_PLANAR")

    values = compare_structures(reference, test, tolerance_mm=2.0).values

    assert values["poly_planes_joint"] == 1
    assert values["poly_planes_gt_only"] == 1
    assert values["poly_planes_test_only"] == 0
    # APL counts the reference-only plane's full length as path to be drawn.
    assert values["poly_apl_mm"] > 40.0
