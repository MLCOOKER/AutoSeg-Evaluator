"""Metric keys that carry their tolerance: ``surface_dice@3mm``."""

from __future__ import annotations

import pytest

from autoseg_evaluator.core.readable import readable_metric, tolerance_note
from autoseg_evaluator.core.tolerance_keys import (
    base_metric,
    normalise_tolerances,
    split_tolerance,
    tolerance_key,
    tolerance_of,
)
from autoseg_evaluator.data.report import metric_direction, metric_family
from autoseg_evaluator.data.results import metric_display_label


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (None, (3.0,)),
        ("", (3.0,)),
        ([], (3.0,)),
        (2.5, (2.5,)),  # a single number, as every settings file before lists held
        ([3, 1, 2.5], (1.0, 2.5, 3.0)),
        ("3, 1; 2.5", (1.0, 2.5, 3.0)),
        ([1, 1.0, 1.001, 0.999], (1.0,)),  # one column, not four
        ([0], (0.0,)),
    ],
)
def test_tolerances_are_read_from_every_shape_settings_have_held(given, expected):
    assert normalise_tolerances(given) == expected


@pytest.mark.parametrize("given", ["two", [1, "x"], [-1], [float("nan")], "inf"])
def test_a_tolerance_that_is_not_a_distance_is_refused(given):
    with pytest.raises(ValueError):
        normalise_tolerances(given)


def test_a_key_carries_its_tolerance_and_gives_it_back():
    assert tolerance_key("surface_dice", 3.0) == "surface_dice@3mm"
    assert tolerance_key("poly_apl_mm", 2.5) == "poly_apl_mm@2.5mm"
    assert tolerance_key("surface_dice", 0.75) == "surface_dice@0.75mm"
    assert split_tolerance("surface_dice@2.5mm") == ("surface_dice", 2.5)
    assert split_tolerance("dice") == ("dice", None)
    assert base_metric("poly_napl_reverse@1mm") == "poly_napl_reverse"
    assert tolerance_of("hausdorff95") is None


def test_every_lookup_by_name_sees_through_the_tolerance():
    """Direction, family, prose and label must not need a tolerance-specific case."""
    key = "surface_dice@2mm"
    assert metric_direction(key) == metric_direction("surface_dice") == 1
    assert metric_direction("poly_apl_mm@1mm") == -1
    assert metric_family(key) == metric_family("surface_dice")
    assert metric_family("poly_napl@1mm") == metric_family("poly_napl")
    assert readable_metric(key) == "Surface Dice"
    assert metric_display_label(key) == "Surface Dice @ 2.00 mm"
    assert metric_display_label("poly_apl_mm@1.5mm") == "2D APL (mm) @ 1.50 mm"


def test_the_tolerance_note_reads_the_key():
    assert tolerance_note("surface_dice@1mm") == "tolerance = 1.00 mm"
    assert tolerance_note("surface_dice") == "tolerance not recorded"
    assert tolerance_note("dice@1mm") == ""
