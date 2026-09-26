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
    POLYGON_TOLERANCE_METRICS,
    REFERENCE_SAMPLING_MM,
    ROW_KEYS,
    ContourRegions,
    ContoursUnavailableError,
    compare_structures,
    library_available,
    parse_structure,
    select_engine,
)
from autoseg_evaluator.core.tolerance_keys import tolerance_key

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


def test_a_ring_inside_a_ring_is_a_hole(grid):
    """Composed without asking, because the mask path already composes them.

    Such a loop has unambiguous geometry and ambiguous intent, which was the
    original argument for making it an opt-in. That argument ignored the rest of
    the application: both mask rasterisers have read these as holes since v1, so
    every published result already rests on the interpretation. An opt-in could
    not avoid the assumption — it could only put the two streams out of step.
    """
    dataset = _rtss({3: [_square(0, 0, 40), _square(10, 10, 10)]})

    composed = parse_structure(dataset, 1, grid)

    assert composed.nested_planes == 1
    # 40x40 outer, less the 10x10 ring sitting wholly inside it.
    assert composed.planes[3].area == pytest.approx(1600.0 - 100.0)


def test_partially_overlapping_rings_are_still_refused(grid):
    """Containment has one reading; a partial overlap has none.

    This is the one place the two streams genuinely diverge: the mask path
    combines these silently, and here the structure is refused. Refusing beats
    guessing, and it means a structure can carry mask metrics and no polygon
    metrics — none did in the reference cohort.
    """
    dataset = _rtss({3: [_square(0, 0, 20), _square(10, 10, 20)]})

    with pytest.raises(ContoursUnavailableError):
        parse_structure(dataset, 1, grid)


def test_an_explicit_xor_needs_no_opt_in(grid):
    """When the exporter states the parity, there is nothing to interpret."""
    dataset = _rtss({3: [_square(0, 0, 40), _square(10, 10, 10)]}, kind="CLOSEDPLANAR_XOR")

    regions = parse_structure(dataset, 1, grid)
    assert regions.planes[3].area == pytest.approx(1500.0)
    assert regions.nested_planes == 0


# ---- References that point at nothing --------------------------------------
#
# Found on a real tender cohort: one vendor of seven wrote structure sets whose
# references name a CT that is not the one loaded beside them — a frame UID the
# file never declares, and slice UIDs absent from the series. The contours
# themselves sit exactly on the CT's planes. The mask stream never reads these
# references; the parser refused every structure in those sets (0 of 447).

SLICES = {generate_uid(): k for k in range(20)}


@pytest.fixture
def sliced_grid() -> ContourGrid:
    """The same frame, with slice UIDs the contours can reference."""
    return ContourGrid(
        origin=(0.0, 0.0, 0.0),
        basis=np.eye(3),
        spacing=(1.0, 1.0, 2.0),
        size=(512, 512, 20),
        frame_of_reference_uid=FRAME,
        sops=dict(SLICES),
    )


def _declare(dataset, *frames):
    """Give a structure set the top-level frame declarations exporters write."""
    items = []
    for frame in frames:
        item = Dataset()
        item.FrameOfReferenceUID = frame
        items.append(item)
    dataset.ReferencedFrameOfReferenceSequence = items
    return dataset


def _reference_slices(dataset, uid_for_plane):
    """Point each contour at an image, as ``ContourImageSequence`` does."""
    for contour in dataset.ROIContourSequence[0].ContourSequence:
        plane = int(round(float(contour.ContourData[2]) / 2.0))
        ref = Dataset()
        ref.ReferencedSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
        ref.ReferencedSOPInstanceUID = uid_for_plane(plane)
        contour.ContourImageSequence = [ref]
    return dataset


def _uid_of_plane(plane):
    return next(uid for uid, k in SLICES.items() if k == plane)


def test_a_consistent_structure_set_is_passed_through_untouched(sliced_grid):
    dataset = _reference_slices(_declare(_rtss({3: [_square(10, 10, 20)]}), FRAME), _uid_of_plane)

    regions = parse_structure(dataset, 1, sliced_grid)

    assert sorted(regions.planes) == [3]
    assert regions.references_set_aside == ()


def test_a_frame_the_structure_set_never_declares_is_read_in_the_one_it_does(grid):
    dataset = _declare(_rtss({3: [_square(10, 10, 20)]}), FRAME)
    dataset.StructureSetROISequence[0].ReferencedFrameOfReferenceUID = generate_uid()

    regions = parse_structure(dataset, 1, grid)

    assert regions.planes[3].area == pytest.approx(400.0)
    assert len(regions.references_set_aside) == 1
    assert "not declared" in regions.references_set_aside[0]


def test_a_frame_the_structure_set_does_declare_is_still_a_mismatch(grid):
    """A structure drawn on another image cannot be placed by its coordinates.

    Its frame is real and named; it is simply not this one. That is the case the
    parser's check exists for, and setting it aside would put an MR contour on
    CT slices.
    """
    other = generate_uid()
    dataset = _declare(_rtss({3: [_square(10, 10, 20)]}), FRAME, other)
    dataset.StructureSetROISequence[0].ReferencedFrameOfReferenceUID = other

    with pytest.raises(ContoursUnavailableError, match="different Frame of Reference"):
        parse_structure(dataset, 1, grid)


def test_an_undeclared_frame_is_refused_when_there_is_no_single_frame_to_mean(grid):
    dataset = _declare(_rtss({3: [_square(10, 10, 20)]}), FRAME, generate_uid())
    dataset.StructureSetROISequence[0].ReferencedFrameOfReferenceUID = generate_uid()

    with pytest.raises(ContoursUnavailableError, match="no\\s+single frame"):
        parse_structure(dataset, 1, grid)


def test_image_references_that_name_no_slice_are_set_aside(sliced_grid):
    dataset = _reference_slices(
        _declare(_rtss({3: [_square(10, 10, 20)], 4: [_square(10, 10, 20)]}), FRAME),
        lambda plane: generate_uid(),
    )

    regions = parse_structure(dataset, 1, sliced_grid)

    assert sorted(regions.planes) == [3, 4]
    assert regions.references_set_aside
    assert regions.references_set_aside[0].startswith("2 contour image references")


def test_setting_references_aside_never_touches_the_cached_dataset(sliced_grid):
    """The dataset is shared with the mask stream and every other pair."""
    stray_frame = generate_uid()
    dataset = _reference_slices(
        _declare(_rtss({3: [_square(10, 10, 20)]}), FRAME), lambda plane: generate_uid()
    )
    dataset.StructureSetROISequence[0].ReferencedFrameOfReferenceUID = stray_frame

    parse_structure(dataset, 1, sliced_grid)

    assert dataset.StructureSetROISequence[0].ReferencedFrameOfReferenceUID == stray_frame
    assert "ContourImageSequence" in dataset.ROIContourSequence[0].ContourSequence[0]


def test_a_reference_that_resolves_to_the_wrong_slice_is_still_refused(sliced_grid):
    """Contradiction, not absence: this is what the parser's check is for."""
    dataset = _reference_slices(
        _declare(_rtss({3: [_square(10, 10, 20)]}), FRAME),
        lambda plane: _uid_of_plane(plane + 1),
    )

    with pytest.raises(ContoursUnavailableError, match="Referenced CT plane mismatch"):
        parse_structure(dataset, 1, sliced_grid)


def test_references_of_which_only_some_resolve_are_ambiguous(sliced_grid):
    dataset = _reference_slices(
        _declare(_rtss({3: [_square(10, 10, 20)], 4: [_square(10, 10, 20)]}), FRAME),
        lambda plane: _uid_of_plane(plane) if plane == 3 else generate_uid(),
    )

    with pytest.raises(ContoursUnavailableError, match="1 of this structure's 2"):
        parse_structure(dataset, 1, sliced_grid)


def test_without_references_a_contour_off_the_slice_planes_is_still_refused(sliced_grid):
    """Coordinates carry the placement once references are set aside.

    So the geometric check has to hold: a contour half a slice off the lattice
    is not on this CT, whatever its references said.
    """
    dataset = _reference_slices(
        _declare(_rtss({3: [_square(10, 10, 20)]}), FRAME), lambda plane: generate_uid()
    )
    contour = dataset.ROIContourSequence[0].ContourSequence[0]
    contour.ContourData = [
        value + 1.0 if index % 3 == 2 else value for index, value in enumerate(contour.ContourData)
    ]

    with pytest.raises(ContoursUnavailableError, match="Off-grid"):
        parse_structure(dataset, 1, sliced_grid)


def test_nested_rings_compose_through_the_view_as_well(grid):
    """The nested-ring path copies the dataset; here it copies the view."""
    dataset = _declare(_rtss({3: [_square(0, 0, 40), _square(10, 10, 10)]}), FRAME)
    dataset.StructureSetROISequence[0].ReferencedFrameOfReferenceUID = generate_uid()

    regions = parse_structure(dataset, 1, grid)

    assert regions.nested_planes == 1
    assert regions.planes[3].area == pytest.approx(1500.0)
    assert regions.references_set_aside


def test_an_roi_number_listed_twice_is_named_as_the_reason(grid):
    dataset = _rtss({3: [_square(10, 10, 20)]})
    duplicate = Dataset()
    duplicate.ROINumber = 1
    duplicate.ReferencedFrameOfReferenceUID = FRAME
    dataset.StructureSetROISequence.append(duplicate)

    with pytest.raises(ContoursUnavailableError, match="listed 2 times"):
        parse_structure(dataset, 1, grid)


def test_what_was_set_aside_is_described_without_a_single_uid(sliced_grid):
    """These notes go into the audit sidecar, which leaves the building."""
    stray_frame = generate_uid()
    stray_slice = generate_uid()
    dataset = _reference_slices(
        _declare(_rtss({3: [_square(10, 10, 20)]}), FRAME), lambda plane: stray_slice
    )
    dataset.StructureSetROISequence[0].ReferencedFrameOfReferenceUID = stray_frame

    notes = " ".join(parse_structure(dataset, 1, sliced_grid).references_set_aside)

    for uid in (FRAME, stray_frame, stray_slice, *SLICES):
        assert uid not in notes


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
    # The APL columns carry the tolerance they were measured at.
    promised = {
        tolerance_key(key, 2.0) if key in POLYGON_TOLERANCE_METRICS else key for key in ROW_KEYS
    }
    assert promised <= set(result.values)
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
    assert fast.values["poly_apl_mm@2mm"] == pytest.approx(slow.values["poly_apl_mm@2mm"], abs=1e-9)


@pytest.mark.parametrize("engine_name", [ENGINE_FAST, ENGINE_REFERENCE])
def test_several_tolerances_in_one_call_match_one_call_each(engine_name):
    """Every tolerance comes out of one engine call, keyed by its own value.

    The same numbers as asking for each tolerance separately, so computing
    several in one run changes nothing but the number of columns.
    """
    if engine_name == ENGINE_FAST and not library_available():
        pytest.skip("no compiled library for this platform")
    engine = select_engine(engine_name)
    reference = _regions({0: box(0, 0, 10, 10), 1: box(0, 0, 10, 10)}, "CLOSED_PLANAR")
    test = _regions({0: box(1, 1, 8, 8), 1: box(2, 2, 9, 9)}, "CLOSED_PLANAR")

    together = compare_structures(reference, test, tolerance_mm=[3.0, 0.5, 1.5], engine=engine)
    for tau in (0.5, 1.5, 3.0):
        alone = compare_structures(reference, test, tolerance_mm=tau, engine=engine)
        for key in POLYGON_TOLERANCE_METRICS:
            keyed = tolerance_key(key, tau)
            assert together.values[keyed] == pytest.approx(alone.values[keyed], abs=1e-9)
    # Different tolerances really are different measurements here.
    assert together.values["poly_apl_mm@0.5mm"] > together.values["poly_apl_mm@3mm"]


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


# ---- Quantiles the contours do not determine -------------------------------
#
# Half the ground truth coincides with the test and half sits 2 mm from it, so
# any median from 0 to 2 mm meets the definition. Which one a program reports is
# decided by whether a running sum of lengths rounds just under or just over one
# half. The engine refuses; the question is how much it should refuse.

_SAME = box(0, 0, 10, 10)


def _half_coincident(test_far):
    """Planes 0-1 identical; planes 2-3 carry ``test_far`` against a 10 mm box."""
    reference = _regions({z: _SAME for z in range(4)}, "CLOSED_PLANAR")
    test = _regions({0: _SAME, 1: _SAME, 2: test_far, 3: test_far}, "CLOSED_PLANAR")
    return reference, test


def _fast_only():
    if not library_available():
        pytest.skip("no compiled library for this platform")
    return select_engine(ENGINE_FAST)


def test_an_undetermined_median_blanks_the_median_and_nothing_else():
    """HD100, the mean and APL do not depend on where a quantile falls."""
    engine = _fast_only()
    # Inset by 2 mm: the ground truth's median could be 0 or 2, and the test's
    # own median is 0, so the reported (larger) one is undetermined too.
    reference, test = _half_coincident(box(2, 2, 8, 8))

    result = compare_structures(reference, test, tolerance_mm=1.0, engine=engine)

    assert result.available, "one undetermined quantile must not void the comparison"
    assert "poly_median_distance_mm" not in result.values
    assert set(result.undefined) == {"poly_median_distance_mm"}
    assert "from 0 to 2 mm" in result.undefined["poly_median_distance_mm"]
    for kept in ("poly_hd100_mm", "poly_hd95_mm", "poly_mean_distance_mm", "poly_apl_mm@1mm"):
        assert kept in result.values
    assert result.values["poly_hd100_mm"] == pytest.approx(2 * np.sqrt(2), abs=1e-9)


def test_a_median_the_other_direction_settles_is_reported():
    """The table shows the larger direction, and here it is the same either way.

    Drawn 2 mm wider: the ground truth's median is anywhere in 0-2 mm, but the
    test's is exactly 2 mm, so the larger of the two is 2 mm whichever end the
    first takes. The engine used to refuse this; nothing about it is a guess.
    """
    engine = _fast_only()
    reference, test = _half_coincident(box(-2, -2, 12, 12))

    result = compare_structures(reference, test, tolerance_mm=1.0, engine=engine)

    assert result.undefined == {}
    assert result.values["poly_median_distance_mm"] == pytest.approx(2.0, abs=1e-6)
    # The first direction really was undetermined; only the symmetry settles it.
    assert result.detail["a_quantile_mass_gap_median_mm"] > 1.9


def test_switching_off_the_engines_refusal_changes_no_number():
    """Its ``error_mm`` only decides when to raise, so lifting it is free."""
    engine = _fast_only()
    from autoseg_evaluator.vendor import native_contour_metrics_fast as fast

    reference = fast.prepare(fast.ROI({0: box(0, 0, 10, 10), 1: box(1, 0, 11, 9)}, {}, 0, "x"))
    test = fast.prepare(fast.ROI({0: box(2, 1, 9, 8), 1: box(0, 0, 10, 10)}, {}, 0, "x"))
    kwargs = {"taus": [1.0], "missing_plane_policy": "exclude"}

    strict = fast.compare(reference, test, error_mm=0.001, **kwargs)
    lifted = engine._compare(reference, test, (1.0,))

    for key, value in strict.items():
        assert lifted[key] == value, key


def test_the_fallback_engine_still_refuses_the_whole_pair_and_says_undefined():
    """It cannot report a quantile's gap without raising.

    So it keeps the old behaviour, but under the same word as the compiled
    engine: an undetermined quantile is undefined, not unavailable.
    """
    reference, test = _half_coincident(box(2, 2, 8, 8))

    result = compare_structures(
        reference, test, tolerance_mm=1.0, engine=select_engine(ENGINE_REFERENCE)
    )

    assert not result.available
    assert result.status.startswith("undefined:")


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
    assert values["poly_apl_mm@2mm"] > 40.0
