# Supplementary 3 - 2D Polygon Metric Validation Report: full results

AutoSeg Evaluator 2.6.1, commit 9dc078f with uncommitted source changes · computed 2026-10-02 by `scripts/validate_polygon_analytic.py` · engine fast 0.2.0.dev2 · 150 pairs from the published DICOM files · 110 s. The summary is [Supplementary 3](Supplementary_3_2D_Polygon_Metric_Validation_Report.md). Regenerate this file rather than editing it.

## What is compared

| Reference | What it is | What agreement shows |
| --- | --- | --- |
| Analytical | Closed-form values for ideal squares, circles and cuboids (the supplement's tables) | How far a result is from the ideal shape, including the polygon's own departure from it |
| High-precision | Values for the contours as stored in the RTSTRUCTs, computed in high-precision decimal arithmetic | That the computation itself is right |
| Authors' software | The paper's empirical results, from the authors' scoring code (VitruvianPhantomPy `score_autocontours_lib.py`, adapted from Gooding's Chapter 15 code) | Agreement with the implementation the paper validated |

AutoSeg's values come from the published DICOM archives, read by this application's grid builder and structure parser, and measured by the engine it selects, exactly as a computation run does. The 150 pairs are 16 centred square/circle pairs, 16 rotated, 16 offset and two cuboid pairs, at each of three resolutions: coarse (1.8 × 2.2 mm, 3 mm planes), typical (0.96 mm, 1 mm) and fine (0.5 mm, 0.5 mm).

## Against the ideal shapes: squares and circles

Largest absolute difference over the 16 pairs of each family, AutoSeg against the analytical value. Beside it, the same for the high-precision values of the stored polygons: where the two agree, the whole difference is the stored polygon's, not the computation's.

| Quantity | Grid | AutoSeg − analytic | Stored polygon − analytic | Unit |
| --- | ---: | ---: | ---: | ---: |
| HD100 | Coarse | 0.0584 | 0.0584 | mm |
|  | Typical | 0.0154 | 0.0154 | mm |
|  | Fine | 0.0084 | 0.0084 | mm |
| HD95 | Coarse | 0.0306 | 0.0306 | mm |
|  | Typical | 0.0079 | 0.0079 | mm |
|  | Fine | 0.0039 | 0.0039 | mm |
| Mean distance | Coarse | 0.0355 | 0.0355 | mm |
|  | Typical | 0.0080 | 0.0080 | mm |
|  | Fine | 0.0027 | 0.0027 | mm |
| Median distance | Coarse | 0.0370 | 0.0370 | mm |
|  | Typical | 0.0149 | 0.0149 | mm |
|  | Fine | 0.0048 | 0.0048 | mm |
| NAPL reference → test @ 1 mm | Coarse | 0.0098 | 0.0098 | fraction |
|  | Typical | 0.0029 | 0.0029 | fraction |
|  | Fine | 9.4e-04 | 9.4e-04 | fraction |
| NAPL test → reference @ 1 mm | Coarse | 0.0127 | 0.0127 | fraction |
|  | Typical | 0.0048 | 0.0048 | fraction |
|  | Fine | 0.0013 | 0.0013 | fraction |
| NAPL reference → test @ 2 mm | Coarse | 0.0124 | 0.0124 | fraction |
|  | Typical | 0.0016 | 0.0016 | fraction |
|  | Fine | 5.7e-04 | 5.7e-04 | fraction |
| NAPL test → reference @ 2 mm | Coarse | 0.0129 | 0.0129 | fraction |
|  | Typical | 0.0028 | 0.0028 | fraction |
|  | Fine | 8.3e-04 | 8.3e-04 | fraction |

Largest *relative* difference in each distance over the square/circle pairs, as a percentage of the analytical value:

| Quantity | Coarse | Typical | Fine |
| --- | ---: | ---: | ---: |
| HD100 | 1.61 % | 0.40 % | 0.16 % |
| HD95 | 1.05 % | 0.20 % | 0.07 % |
| Mean distance | 2.14 % | 0.52 % | 0.16 % |
| Median distance | 3.14 % | 1.07 % | 0.33 % |

By family, largest absolute distance difference from the analytical value (mm):

| Family | HD100 | HD95 | Mean distance | Median distance |
| --- | ---: | ---: | ---: | ---: |
| centred | 0.0584 | 0.0306 | 0.0355 | 0.0370 |
| centred, rotated | 0.0461 | 0.0276 | 0.0355 | 0.0350 |
| offset | 0.0372 | 0.0269 | 0.0264 | 0.0369 |

The rotated family does not test rotation invariance: the published files rotate the square by 45 degrees but the circle's polygon by 45 radians. For a centred circle this moves only its vertices, so the analytical values of the centred family still apply.

## Against the ideal shapes: the cuboids

The sparse test cuboid stores each plane's rectangle exactly, so there the analytical values are the ground truth for the contours as drawn, to the precision the supplement prints (Tables D.8 and D.11). The dense test cuboid is sampled at pixel boundaries, which clips its corners; the supplement notes its analytical values are not attainable.

| Grid | Pair | HD100 | HD95 | Mean | Median | APL @ 1 mm | APL @ 2 mm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Coarse | cuboid, dense test | 40.132 (-0.533) | 34.121 (-0.102) | 23.499 (-8.9e-04) | 26.200 (2.0e-12) | 6348.000 (0) | 6296.000 (0) |
| Coarse | cuboid, sparse test | 40.665 (9.6e-05) | 34.223 (-1.4e-04) | 23.499 (-1.3e-04) | 26.200 (6.3e-11) | 6348.000 (0) | 6296.000 (0) |
| Typical | cuboid, dense test | 40.434 (-0.231) | 34.174 (-0.049) | 23.500 (8.9e-04) | 26.200 (1.2e-10) | 19844.000 (-3.6e-12) | 19688.000 (-3.6e-12) |
| Typical | cuboid, sparse test | 40.665 (9.6e-05) | 34.223 (-1.4e-04) | 23.499 (-1.3e-04) | 26.200 (6.3e-11) | 19844.000 (-3.6e-12) | 19688.000 (-3.6e-12) |
| Fine | cuboid, dense test | 40.589 (-0.076) | 34.210 (-0.013) | 23.507 (7.1e-03) | 26.200 (7.9e-11) | 39692.000 (0) | 39384.000 (0) |
| Fine | cuboid, sparse test | 40.665 (9.6e-05) | 34.223 (-1.4e-04) | 23.499 (-1.3e-04) | 26.200 (6.3e-11) | 39692.000 (0) | 39384.000 (0) |

Each cell: AutoSeg's value in mm, and in brackets its difference from the analytical value. Analytical: HD100 40.665, HD95 34.223 (the larger direction), median 26.2, symmetric mean 23.4995 mm; APL from Table D.11.

Table D.8 prints the reverse mean as 23.499, which is the symmetric mean; the reverse directional mean is 24.085 mm (the high-precision values, a separate quadrature, and the authors' own empirical result of 24.085231). The symmetric mean compared here does not depend on which is read: taking the printed value as a direction would give 23.2065 mm instead.

## Against the authors' software

The paper's *empirical* results come from the authors' own scoring code: `score_autocontours_lib.py` in their VitruvianPhantomPy repository (Boukerroui and Gooding, Mirada Medical), adapted in 2022 from Gooding's `score_autocontours.py` for Chapter 15 of *Auto-segmentation for Radiation Oncology*. The repository publishes those results as `results/*_empirical.csv`, and they are compared here as published. For distances the code takes a point every 0.05 mm along each contour, from its start, measures each point's distance to the other contour, and reads HD95 and the median as NumPy percentiles of those points and the mean as their average. Differences of a few hundredths of a millimetre are that sampling, not an error on either side.

Largest absolute difference from their published results, over all 150 pairs:

| Quantity | Largest difference (mm) | Median difference (mm) |
| --- | ---: | ---: |
| HD100 | 0.0030 | 1.7e-07 |
| HD95 | 0.0133 | 5.5e-04 |
| Mean distance | 8.9e-04 | 2.7e-04 |
| Median distance | 0.0051 | 4.4e-04 |

**APL at 1 mm** (reference → test, summed over planes): 133 of 150 pairs agree with the authors' value to within 0.001 mm. The others:

| Pair | AutoSeg (mm) | Published (mm) | Their code, re-run (mm) | AutoSeg − published (mm) | Exact − AutoSeg (mm) | Difference due to |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Typical-cc_vm_rotated-2 | 868.5266 | 868.5996 | 868.5508 | -0.0731 | 1.1e-13 | not reproduced by their code |
| Fine-cc_vm-16 | 201162.8628 | 201162.8097 | 201162.8097 | 0.0531 | 0 | their method |
| Fine-cc_vm_rotated-3 | 1681.9359 | 1681.9796 | 1681.9796 | -0.0437 | -6.8e-13 | their method |
| Coarse-cc_vm-3 | 248.5668 | 248.6000 | 248.5744 | -0.0333 | -8.5e-14 | not reproduced by their code |
| Coarse-ldv_vm-2 | 318.0793 | 318.1066 | 318.0840 | -0.0274 | 0 | not reproduced by their code |
| Coarse-cc_vm_rotated-2 | 263.5743 | 263.5896 | 263.5772 | -0.0153 | 0 | not reproduced by their code |
| Coarse-cc_vm_rotated-3 | 248.0194 | 248.0286 | 248.0221 | -0.0092 | 8.5e-14 | not reproduced by their code |
| Coarse-cc_vm_rotated-7 | 1746.2475 | 1746.2564 | 1746.2564 | -0.0089 | 2.3e-13 | their method |
| Coarse-cc_vm-10 | 6457.3819 | 6457.3894 | 6457.3894 | -0.0075 | 0 | their method |
| Fine-ldv_vm-2 | 2122.5248 | 2122.5317 | 2122.5317 | -0.0068 | 4.5e-13 | their method |
| Typical-cc_vm_rotated-11 | 22824.1953 | 22824.1887 | 22824.1887 | 0.0066 | -3.6e-12 | their method |
| Coarse-ldv_vm-12 | 6455.1695 | 6455.1753 | 6455.1753 | -0.0059 | 9.1e-13 | their method |
| Typical-cc_vm_rotated-15 | 96867.3109 | 96867.3159 | 96867.3159 | -0.0050 | 1.5e-11 | their method |
| Typical-ldv_vm-5 | 5320.8885 | 5320.8920 | 5320.8920 | -0.0035 | -9.1e-13 | their method |
| Coarse-ldv_vm-6 | 1771.8469 | 1771.8502 | 1771.8502 | -0.0033 | 2.3e-13 | their method |
| Fine-ldv_vm-3 | 1469.5424 | 1469.5451 | 1469.5451 | -0.0027 | 2.3e-13 | their method |
| Coarse-ldv_vm-10 | 7304.3196 | 7304.3175 | 7304.3175 | 0.0021 | 9.1e-13 | their method |

*Exact* is the high-precision value for the stored polygons; AutoSeg is within 1.5e-11 mm of it on every one of these pairs. *Their code, re-run* is the authors' APL function re-run by the present authors (with three mechanical edits for Shapely 2, on current libraries rather than the 2022 environment).

Their APL is an approximation of the paper's definition. It widens the test contour by τ and shrinks it by τ, cuts the reference contour where it crosses those two outlines, and counts a piece as added if its midpoint lies outside the widened outline or inside the shrunk one. The widened outline's rounded corners are polygons of 32 segments per quarter circle rather than arcs; each piece is judged by its midpoint alone; a point exactly τ away counts as unmatched, where the paper (A.13) counts it as matched; and when a cut fails on parallel edges the outline is nudged by a random amount of up to 0.0001 mm and the cut retried. AutoSeg computes the paper's definition on the stored contours exactly.

- **12 of these pairs are their method's approximation.** Re-running their code gives exactly their published value, and that value differs from the exact one, by at most 0.053 mm.
- **5 are published values their own code no longer reproduces.** On all of them the re-run lands closer to the exact value than the published figure. Different library versions and the random retry are both possible causes.

In a degenerate case the difference is large. An axis-aligned square [-5, 5]² against [-3, 3]² at τ = 2 mm has 16 mm of reference boundary farther than τ from the test, by the paper's definition; the authors' code returns 40 mm, because the reference's straight edges lie exactly on the widened outline and count as unmatched. None of the published pairs is degenerate like this, and on them the differences are at most 0.013 % of a pair's APL, which is summed over every plane.

The authors' published Chapter 15 code combines the two directional medians by averaging them; the paper, the high-precision values and AutoSeg take the larger. On these pairs the two conventions differ by up to 2.570 mm, so the comparison above composes the authors' directional medians the paper's way.

## The computation itself

Largest absolute difference from the high-precision values for the stored polygons, over all 150 pairs and both directions of APL:

| Quantity | Largest difference | Unit |
| --- | ---: | ---: |
| HD100 | 6.4e-14 | mm |
| HD95 | 4.9e-10 | mm |
| Mean distance | 2.1e-13 | mm |
| Median distance | 4.8e-10 | mm |
| APL reference → test @ 1 mm | 5.8e-11 | mm |
| NAPL reference → test @ 1 mm | 5.6e-16 |  |
| APL test → reference @ 1 mm | 2.9e-10 | mm |
| NAPL test → reference @ 1 mm | 1.7e-15 |  |
| APL reference → test @ 2 mm | 5.8e-11 | mm |
| NAPL reference → test @ 2 mm | 5.6e-16 |  |
| APL test → reference @ 2 mm | 2.6e-10 | mm |
| NAPL test → reference @ 2 mm | 1.7e-15 |  |

`scripts/validate_polygon_metrics.py` checks both engines against the same values, and against 44 geometric edge cases, on every release.

## Conventions

| Metric | Measured in each direction as | Reported as | Planes used |
| --- | --- | --- | --- |
| Hausdorff 100 % | The largest distance from one boundary to the other | The larger of the two directions | Planes both structures have |
| Hausdorff 95 % | The 95th percentile of distance, weighted by boundary length | The larger of the two directions | Planes both structures have |
| Median distance | The 50th percentile of distance, weighted by boundary length | The larger of the two directions | Planes both structures have |
| Mean distance | The average distance, weighted by boundary length | The average of the two directions | Planes both structures have |
| APL | The length of one boundary farther than τ from the other; a point exactly τ away counts as matched | Reference → test, in mm; test → reference as a separate column | Every plane of the boundary measured; planes the other structure lacks count in full |
| NAPL | APL as a fraction of that boundary's length | Total APL ÷ total boundary length over all planes; each direction separately | As APL |

These are the paper's definitions (Supplement A). Points are weighted by arc length, not by vertex, so adding vertices along an edge changes nothing. Percentiles are the lower generalised inverse of the length-weighted distribution.

## What this does not cover

The shapes are convex, single-loop and on regular grids. Holes, nested and XOR contours, missing planes and uneven stacks are covered by the unit tests (`tests/test_contour_grid.py`, `tests/test_polygon_metrics.py`) against cases taken from real structure sets, not by this benchmark. These are planar measurements, not 3D surface distances.

## Sources

Boukerroui D, Vasquez Osorio E, Brunenberg E, Gooding MJ. Analytic calculations and synthetic shapes for validation of quantitative contour comparison software. *Phys Imaging Radiat Oncol* 2023;26:100436. doi:10.1016/j.phro.2023.100436. Dataset: doi:10.17632/9xjyrftzth.1 (CC BY 4.0).

Analytical values: `VitruvianPhantomPy/mlab/*_theory_results.csv` (commit `cc4106a`, https://github.com/Vitruvian-phantom-for-RadOnc/VitruvianPhantomPy) and Supplement Tables D.8 and D.11. High-precision values: `third_party/native_contour_metrics/v0.2/data/golden_metrics.json`.
