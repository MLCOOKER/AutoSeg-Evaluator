# Supplementary 3 - 2D Polygon Metric Validation Report

AutoSeg Evaluator 3.0.0, commit 85e6c28 · computed 2026-10-08 by `scripts/validate_polygon_analytic.py`. Every table behind this report is in its [full results](Supplementary_3_2D_Polygon_Metric_Validation_Report_Full_Results.md).

## Aim

AutoSeg Evaluator v3 computes a second set of metrics directly on the contours stored in the RTSTRUCT, plane by plane, without converting them to a binary mask: the Hausdorff distance (100 % and 95 %), the mean and median contour distance, and the added path length (APL) with its normalised form (NAPL). The authors implemented them from the mathematical definitions of Boukerroui et al. [1] (Supplement A), as a compiled engine with a pure-Python engine of the same definitions as its fallback. This validation asks three questions:

1. Does AutoSeg compute these metrics exactly as defined, on the contours as stored?
2. How close are its results to the exact values for the ideal shapes, and to the results of Boukerroui et al.'s own software, on the same data?
3. Does it combine the two directions of each metric as the paper does?

## Method

**Data.** The synthetic shapes Boukerroui et al. published with the paper [2], as DICOM RTSTRUCT files. 50 pairs of a reference and a test shape: 48 pairs of a square and a circle (16 centred, 16 rotated by 45° and 16 offset, varying in size and in the ratio of circle to square) and 2 pairs of cuboids. Each was provided on three CT grids, fine (0.5 mm pixels, 0.5 mm planes), typical (0.96 mm, 1 mm) and coarse (1.8 × 2.2 mm, 3 mm): 150 pairs in all. AutoSeg read every file with its own DICOM reader and measured it with the engine it selects, exactly as in a computation run.

**References.** Three, because agreement with each shows something different:

| Reference | What it is | What agreement shows |
| --- | --- | --- |
| Exact values for the ideal shapes | The closed-form values published with the dataset [3], and Supplement Tables D.8 and D.11 for the cuboids | How close AutoSeg comes to the ideal shape, including the error of storing a circle as a polygon |
| High-precision values for the stored contours | The metrics of the contours exactly as stored in the files, computed by the authors with a separate implementation in high-precision decimal arithmetic | That the computation itself is right |
| Boukerroui et al.'s software | The paper's published empirical results, from the authors' own scoring code [3] | Agreement with the implementation the paper validated |

**Quantities.** HD100, HD95, mean and median distance; APL and NAPL at τ = 1 and 2 mm, in both directions.

**What AutoSeg reports.** Each metric is measured in two directions, from the reference boundary to the test and from the test to the reference, and combined as follows:

| Metric | Measured in each direction as | Reported as | Planes used |
| --- | --- | --- | --- |
| Hausdorff 100 % | The largest distance from one boundary to the other | The larger of the two directions | Planes both structures have |
| Hausdorff 95 % | The 95th percentile of distance, weighted by boundary length | The larger of the two directions | Planes both structures have |
| Median distance | The 50th percentile of distance, weighted by boundary length | The larger of the two directions | Planes both structures have |
| Mean distance | The average distance, weighted by boundary length | The average of the two directions | Planes both structures have |
| APL | The length of one boundary farther than τ from the other; a point exactly τ away counts as matched | Reference → test, in mm; test → reference as a separate column | Every plane of the boundary measured; planes the other structure lacks count in full |
| NAPL | APL as a fraction of that boundary's length | Total APL ÷ total boundary length over all planes; each direction separately | As APL |

## Results

**The computation.** Largest difference from the high-precision values for the stored contours, over all 150 pairs: 4.9 × 10⁻¹⁰ mm for any distance, 2.9 × 10⁻¹⁰ mm for any APL and 1.7 × 10⁻¹⁵ for any NAPL.

**The ideal squares and circles.** Largest difference from the exact values, over the 48 square and circle pairs on each grid:

| Grid | HD100 (mm) | HD95 (mm) | Mean (mm) | Median (mm) | NAPL ref→test @ 1 mm | NAPL test→ref @ 1 mm | NAPL ref→test @ 2 mm | NAPL test→ref @ 2 mm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Coarse | 0.0584 | 0.0306 | 0.0355 | 0.0370 | 0.0098 | 0.0127 | 0.0124 | 0.0129 |
| Typical | 0.0154 | 0.0079 | 0.0080 | 0.0149 | 0.0029 | 0.0048 | 0.0016 | 0.0028 |
| Fine | 0.0084 | 0.0039 | 0.0027 | 0.0048 | 9.4e-04 | 0.0013 | 5.7e-04 | 8.3e-04 |

For every quantity on every grid, the high-precision values for the stored contours differ from the exact values by the same amount, to the four decimals shown.

**The cuboids.** The sparse test cuboid is stored exactly, plane by plane. AutoSeg's values, with their difference from the exact values in brackets (mm):

| Grid | HD100 | HD95 | Mean | Median | APL @ 1 mm | APL @ 2 mm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Coarse | 40.665 (9.6e-05) | 34.223 (-1.4e-04) | 23.499 (-1.3e-04) | 26.200 (6.3e-11) | 6348.000 (0) | 6296.000 (0) |
| Typical | 40.665 (9.6e-05) | 34.223 (-1.4e-04) | 23.499 (-1.3e-04) | 26.200 (6.3e-11) | 19844.000 (-3.6e-12) | 19688.000 (-3.6e-12) |
| Fine | 40.665 (9.6e-05) | 34.223 (-1.4e-04) | 23.499 (-1.3e-04) | 26.200 (6.3e-11) | 39692.000 (0) | 39384.000 (0) |

**Boukerroui et al.'s software.** Their published distances differ from AutoSeg's by at most 0.013 mm over all 150 pairs. Their APL at 1 mm agrees with AutoSeg's to within 0.001 mm on 133 of 150 pairs; the other 17 differ by at most 0.073 mm, or 0.013 % of the pair's APL.

## Findings

- **AutoSeg computes the 2D metrics exactly as defined.** On the contours as stored, it agrees with the high-precision values to within 4.9 × 10⁻¹⁰ mm.
- **Its differences from the ideal shapes belong to the shapes as stored.** They reach 0.0584 mm on the coarse grid and 0.0084 mm on the fine one, and each equals the difference between the stored contour and the ideal shape. A circle stored in an RTSTRUCT is a polygon: its corners lie on the circle and its straight edges cut slightly inside it, so its metrics differ from the ideal circle's by an amount that shrinks with the pixel size. A box can be stored exactly, and on the sparse cuboid AutoSeg's distances are within 1.4 × 10⁻⁴ mm of the exact values, inside the precision the supplement prints, and its APL is exact.
- **It agrees with Boukerroui et al.'s own software within that software's sampling.** Their code measures distances from points taken every 0.05 mm along each contour, so differences of hundredths of a millimetre are expected. Their APL is an approximation of the paper's definition: on 12 of the 17 pairs where it differs, re-running their code gives their published value, which differs from the exact one; on 5, their code no longer reproduces its published value. On every one, AutoSeg matches the exact value to 1.5 × 10⁻¹¹ mm.
- **It combines the two directions as the paper does.** The larger direction for the Hausdorff distances and the median, the average for the mean, and each APL direction on its own; these are also the conventions of AutoSeg's 3D metrics (Supplementary 4). Boukerroui et al.'s published scoring code averages the two directional medians instead of taking the larger; on these pairs the two differ by up to 2.57 mm, and AutoSeg follows the paper.
- **Two details of the published data.** Supplement Table D.8 prints the cuboid's reverse mean as 23.499 mm, which is the symmetric mean; the reverse directional mean is 24.085 mm, as the authors' own empirical result confirms. And the rotated family rotates the circle's polygon by 45 radians rather than 45 degrees, which for a centred circle moves only its vertices, so the family does not test rotation invariance.
- **Why metrics on the contours.** They measure the contours themselves, so they carry none of the error of converting a contour to a binary mask (Supplementary 2), which matters most for small structures.
- **Limits.** The shapes are convex, single contours on regular grids; holes, nested and overlapping contours, missing planes and uneven slice spacing are covered by unit tests on cases taken from real structure sets. The high-precision values come from the same authors as AutoSeg's engines, so they verify the arithmetic; agreement with the exact values for the ideal shapes, and with Boukerroui et al.'s software, verifies the definitions.

## References

1. Boukerroui D, Vasquez Osorio E, Brunenberg E, Gooding MJ. Analytic calculations and synthetic shapes for validation of quantitative contour comparison software. Phys Imaging Radiat Oncol. 2023;26:100436. doi:10.1016/j.phro.2023.100436
2. The synthetic shapes of [1]. Mendeley Data. doi:10.17632/9xjyrftzth.1 (CC BY 4.0)
3. VitruvianPhantomPy: the code and results of [1]. GitHub. https://github.com/Vitruvian-phantom-for-RadOnc/VitruvianPhantomPy

## Reproduce

```
python scripts/validate_polygon_analytic.py --data <folder holding the dataset and the authors' repository>
```

The run took 109 s; `--render-only` rewrites this report from the last run's results. Regenerate this report rather than editing it.
