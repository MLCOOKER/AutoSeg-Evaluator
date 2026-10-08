"""Tests for the STAPLE consensus module."""

from __future__ import annotations

import math

import numpy as np
import pytest
import SimpleITK as sitk

from autoseg_evaluator.core.staple import StapleConfig, compute_staple

# ---- Fixtures ------------------------------------------------------------


def _cube(shape_xyz, lo_xyz, hi_xyz, spacing=(1.0, 1.0, 1.0)) -> sitk.Image:
    """Build a binary uint8 SimpleITK image with a filled cube."""
    sx, sy, sz = shape_xyz
    arr = np.zeros((sz, sy, sx), dtype=np.uint8)
    arr[lo_xyz[2] : hi_xyz[2], lo_xyz[1] : hi_xyz[1], lo_xyz[0] : hi_xyz[0]] = 1
    img = sitk.GetImageFromArray(arr)
    img.SetSpacing(spacing)
    return img


# ---- Config --------------------------------------------------------------


def test_staple_config_defaults():
    cfg = StapleConfig()
    # An iteration cap high enough to converge (SimpleITK sets none), and an
    # adaptive bbox targeting a <= 0.50 foreground-to-bbox ratio (upper only).
    assert cfg.max_iterations == 500
    assert cfg.confidence_weight == 1.0
    assert cfg.target_fg_ratio_max == 0.50
    assert cfg.bbox_padding_min_voxels == 2
    assert cfg.bbox_padding_max_voxels == 25
    # The unused lower-ratio knob was removed (padding can only lower the ratio).
    assert not hasattr(cfg, "target_fg_ratio_min")


def test_the_compute_tab_offers_the_same_iteration_default():
    """The Reset button and an unset config must give the core's default."""
    from autoseg_evaluator.ui.tabs.compute import _STAPLE_DEFAULTS

    assert _STAPLE_DEFAULTS["max_iterations"] == StapleConfig().max_iterations
    assert StapleConfig.from_dict({}).max_iterations == StapleConfig().max_iterations


def test_staple_config_from_dict_round_trip():
    cfg = StapleConfig.from_dict(
        {
            "max_iterations": 50,
            "confidence_weight": 0.8,
            "target_fg_ratio_max": 0.45,
            "bbox_padding_min_voxels": 3,
            "bbox_padding_max_voxels": 20,
        }
    )
    assert cfg.max_iterations == 50
    assert cfg.confidence_weight == 0.8
    assert cfg.target_fg_ratio_max == 0.45
    assert cfg.bbox_padding_min_voxels == 3
    assert cfg.bbox_padding_max_voxels == 20


def test_staple_config_from_dict_ignores_legacy_min_ratio_key():
    # Older settings.json may still carry target_fg_ratio_min — it must be
    # silently ignored, not raise.
    cfg = StapleConfig.from_dict({"max_iterations": 200, "target_fg_ratio_min": 0.2})
    assert cfg.max_iterations == 200
    assert cfg.confidence_weight == 1.0
    assert cfg.target_fg_ratio_max == 0.50


# ---- Degenerate inputs ---------------------------------------------------


def test_staple_returns_none_for_empty_list():
    assert compute_staple([]) is None


def test_staple_returns_none_for_single_mask():
    m = _cube((20, 20, 20), (5, 5, 5), (15, 15, 15))
    assert compute_staple([m]) is None


def test_staple_returns_none_when_all_masks_empty():
    empty = _cube((20, 20, 20), (0, 0, 0), (0, 0, 0))
    assert compute_staple([empty, empty, empty]) is None


def test_staple_geometry_mismatch_raises():
    a = _cube((20, 20, 20), (5, 5, 5), (15, 15, 15))
    b = _cube((20, 20, 20), (5, 5, 5), (15, 15, 15), spacing=(2.0, 2.0, 2.0))
    with pytest.raises(ValueError, match="STAPLE requires"):
        compute_staple([a, b])


# ---- Consensus correctness -----------------------------------------------


def test_staple_identifies_outlier_rater():
    """Three near-identical raters plus one obvious outlier — the outlier
    should get a markedly lower sensitivity."""
    a = _cube((40, 40, 40), (10, 10, 10), (25, 25, 25))
    b = _cube((40, 40, 40), (11, 10, 10), (25, 25, 25))
    c = _cube((40, 40, 40), (10, 11, 10), (26, 25, 25))
    outlier = _cube((40, 40, 40), (15, 15, 15), (30, 30, 30))
    result = compute_staple([a, b, c, outlier])
    assert result is not None
    assert result.n_raters == 4
    # The first three raters should all score high; the outlier markedly lower
    high_three = result.sensitivities[:3]
    assert all(s > 0.8 for s in high_three), high_three
    assert result.sensitivities[3] < 0.5


def test_staple_consensus_volume_matches_majority():
    """When 3 of 4 raters agree exactly on a 10×10×10 cube, the consensus
    volume should equal that cube's volume."""
    a = _cube((40, 40, 40), (10, 10, 10), (20, 20, 20))
    b = _cube((40, 40, 40), (10, 10, 10), (20, 20, 20))
    c = _cube((40, 40, 40), (10, 10, 10), (20, 20, 20))
    d = _cube((40, 40, 40), (15, 15, 15), (25, 25, 25))  # disagrees
    result = compute_staple([a, b, c, d])
    assert result is not None
    # 10×10×10 voxels at 1mm spacing = 1000 mm³ = 1.0 cc
    assert math.isclose(result.consensus_volume_cc, 1.0, abs_tol=0.01)


def test_staple_identical_masks_give_zero_uncertainty():
    """When all raters agree exactly the uncertain band should be empty and
    entropy ≈ 0."""
    m = _cube((40, 40, 40), (10, 10, 10), (20, 20, 20))
    result = compute_staple([m, m, m])
    assert result is not None
    assert result.uncertain_band_cc == 0.0
    assert result.mean_entropy < 1e-3


def test_staple_disagreement_shows_in_rater_disagreement_metric():
    """STAPLE's EM aggressively collapses posteriors to 0/1 once it
    converges, so the (0.2, 0.8) consensus band is rarely populated. The
    pre-STAPLE rater_disagreement_cc metric is the honest single-number
    uncertainty signal — it counts every voxel where raters split."""
    # Two raters end at x=20, two end at x=24 — 4 disputed slabs of voxels.
    a = _cube((40, 40, 40), (10, 10, 10), (20, 20, 20))
    b = _cube((40, 40, 40), (10, 10, 10), (20, 20, 20))
    c = _cube((40, 40, 40), (10, 10, 10), (24, 20, 20))
    d = _cube((40, 40, 40), (10, 10, 10), (24, 20, 20))
    result = compute_staple([a, b, c, d])
    assert result is not None
    # 4 × 10 × 10 = 400 disputed voxels at 1 mm³ each = 0.4 cc
    assert math.isclose(result.rater_disagreement_cc, 0.4, abs_tol=0.01)
    # Volume range: rater a/b have 1000 voxels, c/d have 1400 → range = 400 vox = 0.4 cc
    assert math.isclose(result.rater_volume_range_cc, 0.4, abs_tol=0.01)


def test_staple_unanimous_raters_have_zero_disagreement():
    m = _cube((40, 40, 40), (10, 10, 10), (20, 20, 20))
    result = compute_staple([m, m, m])
    assert result is not None
    assert result.rater_disagreement_cc == 0.0
    assert result.rater_volume_range_cc == 0.0


def test_staple_result_geometry_matches_inputs():
    """The consensus mask + probability map should sit on the same grid as the inputs."""
    a = _cube((40, 40, 40), (10, 10, 10), (20, 20, 20))
    b = _cube((40, 40, 40), (10, 10, 10), (20, 20, 20))
    result = compute_staple([a, b])
    assert result is not None
    assert result.consensus_mask.GetSize() == a.GetSize()
    assert result.consensus_mask.GetSpacing() == a.GetSpacing()
    assert result.probability_map.GetSize() == a.GetSize()


def test_staple_skips_empty_masks_in_input():
    """An empty mask in the input list should be filtered out rather than crashing STAPLE."""
    a = _cube((40, 40, 40), (10, 10, 10), (20, 20, 20))
    b = _cube((40, 40, 40), (10, 10, 10), (20, 20, 20))
    empty = _cube((40, 40, 40), (0, 0, 0), (0, 0, 0))
    result = compute_staple([a, b, empty])
    assert result is not None
    assert result.n_raters == 2  # empty mask filtered out


# ---- Convergence flag ----------------------------------------------------


def test_staple_converged_flag_set_when_elapsed_below_cap():
    """Identical inputs converge in 1 iteration; the convergence flag should be True."""
    m = _cube((40, 40, 40), (10, 10, 10), (20, 20, 20))
    result = compute_staple([m, m, m], StapleConfig(max_iterations=100))
    assert result is not None
    assert result.converged is True
    assert result.elapsed_iterations < 100


# ---- Bounding-box crop ----------------------------------------------------


def test_staple_bbox_crop_keeps_small_structures_visible():
    """A small structure in a large image keeps a non-empty consensus, and the
    adaptive sizer pads its box into the target foreground ratio.

    A 3×3×3 cube in a 60×60×60 image. The crop is a choice of estimation
    domain (the structure's neighbourhood, not the field of view); whole-image
    STAPLE does not empty small organs on the HN1 sample either, so this
    checks the sizer, not a rescue.
    """
    a = _cube((60, 60, 60), (28, 28, 28), (31, 31, 31))
    b = _cube((60, 60, 60), (28, 28, 28), (31, 31, 31))
    c = _cube((60, 60, 60), (28, 28, 28), (31, 31, 31))
    result = compute_staple([a, b, c])  # adaptive padding by default
    assert result is not None
    # The consensus should be non-empty
    assert result.consensus_volume_cc > 0.0
    # The adaptive sizer should have selected enough padding to bring the
    # foreground ratio into the target band — well below the 0.50 ceiling.
    assert result.bbox_fg_ratio <= 0.50
    # And it should have used SOME padding (not zero) since the cube is small.
    assert result.bbox_padding_used >= 2


# ---- Summaries read from the cropped images -------------------------------------


def _blob(shape_zyx, centre, radii, spacing=(0.98, 0.98, 3.0)) -> sitk.Image:
    zz, yy, xx = np.mgrid[: shape_zyx[0], : shape_zyx[1], : shape_zyx[2]]
    inside = sum(((g - c) / r) ** 2 for g, c, r in zip((zz, yy, xx), centre, radii)) <= 1
    img = sitk.GetImageFromArray(inside.astype(np.uint8))
    img.SetSpacing(spacing)
    return img


@pytest.mark.parametrize(
    "centres",
    [
        # Well inside the image.
        [(15, 40, 40), (16, 42, 38), (14, 39, 43), (15, 44, 41)],
        # Against the image edge, so the padded box is clipped by the image.
        [(3, 5, 60), (2, 7, 62), (4, 4, 58)],
    ],
)
def test_staple_summaries_equal_those_of_the_whole_volume(centres):
    """Cropping changes no summary: outside the box every image is zero."""
    from autoseg_evaluator.core.staple import _rater_disagreement, _uncertainty_metrics, _volume_cc

    masks = [
        _blob((30, 80, 70), c, (6 + i, 12 - i, 10 + (i % 2) * 3)) for i, c in enumerate(centres)
    ]
    result = compute_staple(masks)
    assert result is not None
    assert result.mean_entropy > 0  # the raters disagree, so there is something to measure

    full_map = result.probability_map
    assert (result.uncertain_band_cc, result.mean_entropy) == _uncertainty_metrics(full_map)
    assert result.consensus_volume_cc == _volume_cc(result.consensus_mask)
    assert (result.rater_disagreement_cc, result.rater_volume_range_cc) == _rater_disagreement(
        masks
    )


def test_the_whole_probability_map_is_the_cropped_one_in_place():
    masks = [
        _blob((30, 80, 70), (15, 40, 40), (6, 12, 10)),
        _blob((30, 80, 70), (16, 41, 42), (7, 11, 11)),
    ]
    result = compute_staple(masks)
    full = sitk.GetArrayFromImage(result.probability_map)
    x0, y0, z0, x1, y1, z1 = result.probability_bbox
    inside = full[z0 : z1 + 1, y0 : y1 + 1, x0 : x1 + 1]
    assert full.dtype == np.float32
    assert np.array_equal(inside, sitk.GetArrayFromImage(result.probability_cropped))
    outside = full.copy()
    outside[z0 : z1 + 1, y0 : y1 + 1, x0 : x1 + 1] = 0
    assert np.count_nonzero(outside) == 0
    assert result.probability_map.GetOrigin() == masks[0].GetOrigin()
