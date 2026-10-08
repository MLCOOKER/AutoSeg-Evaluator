# Supplementary 1 - DVH Validation Report: full results

AutoSeg Evaluator 3.0.0, commit 85e6c28 · computed 2026-10-08 by `scripts/validate_dvh_methods.py` and `scripts/validate_dvh_mask_vs_polygon.py`. The summary is [Supplementary 1](Supplementary_1_DVH_Validation_Report.md). Regenerate this file rather than editing it.

## Part A: the DVH method against analytic truth

From `scripts/validate_dvh_methods.py`, a run of 36 minutes.

Until v3.0.0 AutoSeg took structure-set DVHs from dicompyler-core; since v3.0.0 it integrates the dose over the contours themselves (**autoseg** below). That method, v2's, and every alternative considered are scored here against DVHs whose true values are known exactly.

### Methods

| Method | Contours read by | Dose sampled at | Each sample stands for |
| --- | --- | --- | --- |
| dicompyler (v2) | dicompyler-core: every loop tested, loops combined by exclusive-or | each dose-grid point in each contour plane; the dose interpolated between dose planes only | one dose voxel's area × the gap between contour planes |
| dicompyler-ss | the same | a grid a quarter of the dose pixel, in each contour plane | that grid's cell × the gap between contour planes |
| mask | the shared reading, half-open fill (the 3D metrics' own mask) | each CT voxel centre, trilinear | one CT voxel |
| mask-ss | the same mask | sub-samples ≤ 0.25 mm apart in every CT voxel, trilinear | an equal share of its voxel |
| polygon | the shared reading's regions, no voxels | sub-cells ≤ 0.25 mm apart, trilinear, each at the centroid of the part covered | the exact area of the region in its sub-cell × its share of the slice |
| **autoseg** (v3) | as polygon | as polygon, at the finest of 0.25, 0.5 and 1 mm that keeps the structure within 10 million samples | as polygon |

Every method treats a contour as a slab one slice thick, centred on its plane, and none interpolates between contours: that is the convention in both benchmarks' truth. The sub-sampling spacing is rounded to an odd number of sub-samples per voxel edge, so one always sits on the voxel centre. D*x* is the lowest dose the hottest *x* of the volume receives. The three sampled methods take a slice at a time into a dose histogram of 1 mGy bins, so memory does not grow with the number of samples; Dmin, Dmax and Dmean are kept exactly and D*x* is read to the bin's centre. Polygon finds each sub-cell's covered area and its centroid exactly, without clipping, by the signed-area accumulation fonts are rasterised with, so its extra work grows with a structure's outline rather than its area. Every method except the dicompyler rows is the application's own `core.dvh`, with the spacing forced where the method names one. The dicompyler rows reproduce v2's calls, frozen in this script; every case was checked equal to v2's `compute_dvh_metrics` before v3 replaced it (commit `acced87`).

### Nelms et al. 2015

Nelms B, Stambaugh C, Hunt D, Tonner B, Zhang G, Feygelman V. *Methods, software and datasets to verify DVH calculations against analytical values: twenty years late(r).* Med Phys 2015;42(8):4435-48. doi:10.1118/1.4923175. PMID 26233174.

A sphere, axial and rotated cylinders, and axial and rotated cones, 24 mm across, contoured every 0.2, 1, 2 or 3 mm on a CT of 0.6 mm pixels, in 1 Gy/mm linear dose fields (16 Gy at the centre) along anterior-posterior or superior-inferior. The truth is the solid itself extended half a slice beyond its end contours, so a method is also charged for filling the gap between contour planes: a rotated cylinder is a stack of rectangles, and no slab method can recover its round side. Pinnacle3 v9.8 and PlanIQ v2.1 are the paper's own results on the same data. The datasets are the paper's supplementary material and are not redistributed with AutoSeg.

#### Test 1: contours every 0.2 mm, dose grid 0.4-3 mm

Cells: parameters more than 3 % from the analytic value / parameters scored (lowest to highest % difference). Differences are relative to the local analytic value, as published, so the low-dose parameters (Dmin, D99, D95) are amplified. The paper columns are its Table I.

| Parameter | dicompyler | dicompyler-ss | mask | mask-ss | polygon | autoseg | Pinnacle3 (paper) | PlanIQ (paper) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| V | 6/20 (-20.2 to -0.1) | 0/20 (-0.0 to +2.8) | 0/20 (-0.2 to +0.6) | 0/20 (-0.2 to +0.6) | 0/20 (-0.1 to -0.0) | 0/20 (-0.1 to -0.0) | 0 (-2.0 to +1.9) | 0 (-0.4 to +0.9) |
| Dmin | 16/40 (+0.0 to +75.0) | 25/40 (+2.6 to +65.8) | 36/40 (+2.6 to +28.2) | 20/40 (+2.5 to +28.2) | 4/40 (+2.5 to +3.3) | 4/40 (+2.5 to +3.3) | 20 (-7.5 to +2.6) | 0 (+0.0 to +0.0) |
| Dmax | 18/40 (-10.7 to -0.4) | 5/40 (-1.1 to +4.0) | 0/40 (-1.1 to -0.4) | 0/40 (-1.1 to -0.4) | 0/40 (-0.4 to +0.0) | 0/40 (-0.4 to +0.0) | 0 (-1.1 to +1.1) | 0 (+0.0 to +0.0) |
| Dmean | 5/40 (-9.4 to +0.0) | 11/40 (-0.0 to +9.4) | 0/40 (-0.0 to +0.0) | 0/40 (-0.0 to +0.0) | 0/40 (-0.0 to +0.0) | 0/40 (-0.0 to +0.0) | 0 (-1.9 to +0.0) | 0 (-0.1 to +0.7) |
| D99 | 30/40 (-100.0 to +6.3) | 27/40 (-100.0 to +22.9) | 0/40 (-1.1 to +2.4) | 0/40 (-2.0 to +1.4) | 0/40 (-1.7 to +1.7) | 0/40 (-1.7 to +1.7) | 20 (-1.4 to +7.5) | 3 (-1.8 to +5.2) |
| D95 | 28/40 (-100.0 to +3.7) | 22/40 (-6.8 to +27.6) | 8/40 (-3.7 to +5.9) | 0/40 (-1.2 to +2.0) | 0/40 (-1.2 to +2.0) | 0/40 (-1.2 to +2.0) | 12 (-6.6 to +5.2) | 2 (-0.8 to +3.9) |
| D5 | 14/40 (-20.3 to -0.2) | 6/40 (-3.3 to +4.8) | 0/40 (-1.0 to +1.1) | 0/40 (-0.3 to +0.4) | 0/40 (-0.3 to +0.4) | 0/40 (-0.3 to +0.4) | 0 (-1.8 to +1.0) | 0 (-0.4 to +0.8) |
| D1 | 18/40 (-21.2 to +0.1) | 6/40 (-3.6 to +4.3) | 0/40 (-0.8 to +0.3) | 0/40 (-0.2 to +0.4) | 0/40 (-0.3 to +0.3) | 0/40 (-0.3 to +0.3) | 0 (-0.9 to +2.2) | 0 (-0.4 to +0.4) |
| D0.03cc | 18/40 (-21.2 to +0.0) | 4/40 (-3.7 to +4.7) | 0/40 (-0.8 to +0.2) | 0/40 (-0.4 to +0.4) | 0/40 (-0.4 to +0.2) | 0/40 (-0.4 to +0.2) | 0 (-0.9 to +1.3) | 0 (-0.4 to +0.3) |
| **All** | **153/340** | **106/340** | **44/340** | **20/340** | **4/340** | **4/340** | **52** | **5** |
| **Without Dmin, Dmax** | **119/260** | **76/260** | **8/260** | **0/260** | **0/260** | **0/260** | **32** | **5** |

#### Test 2: contours and dose grid both 1, 2 or 3 mm, aligned

Cells: parameters more than 3 % from the analytic value / parameters scored (lowest to highest % difference). Differences are relative to the local analytic value, as published, so the low-dose parameters (Dmin, D99, D95) are amplified. The paper columns are its Table II.

| Parameter | dicompyler | dicompyler-ss | mask | mask-ss | polygon | autoseg | Pinnacle3 (paper) | PlanIQ (paper) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| V | 5/15 (-22.4 to -0.1) | 1/15 (-3.9 to +2.9) | 1/15 (-6.1 to +0.6) | 1/15 (-6.1 to +0.6) | 1/15 (-4.6 to +0.6) | 1/15 (-4.6 to +0.6) | 0 (-2.8 to +2.1) | 1 (-4.2 to +0.6) |
| Dmin | 24/30 (+0.0 to +180.0) | 30/30 (+14.3 to +180.0) | 30/30 (+7.5 to +180.0) | 17/30 (+2.5 to +124.6) | 13/30 (+2.5 to +4.6) | 13/30 (+2.5 to +4.6) | 30 (-7.5 to +60.0) | 0 (+0.0 to +0.0) |
| Dmax | 26/30 (-15.3 to -1.8) | 18/30 (-15.3 to +4.0) | 13/30 (-15.3 to -1.1) | 9/30 (-10.6 to -0.4) | 0/30 (-0.4 to +0.0) | 0/30 (-0.4 to +0.0) | 10 (-5.1 to +1.1) | 0 (+0.0 to +0.0) |
| Dmean | 5/30 (-9.4 to +0.0) | 11/30 (-0.1 to +9.4) | 0/30 (-0.3 to +0.1) | 0/30 (-0.3 to +0.1) | 0/30 (-0.2 to +0.0) | 0/30 (-0.2 to +0.0) | 0 (-1.9 to +0.0) | 0 (+0.0 to +0.0) |
| D99 | 27/30 (-100.0 to -0.2) | 29/30 (-100.0 to +22.9) | 16/30 (-7.6 to +46.3) | 5/30 (-3.4 to +22.2) | 5/30 (-3.4 to +22.2) | 5/30 (-3.4 to +22.2) | 18 (-4.2 to +44.4) | 11 (-4.2 to +22.3) |
| D95 | 27/30 (-100.0 to -0.1) | 28/30 (-100.0 to +27.5) | 20/30 (-15.6 to +10.5) | 2/30 (-2.1 to +6.9) | 2/30 (-2.1 to +6.9) | 2/30 (-2.1 to +6.9) | 19 (-7.8 to +19.5) | 4 (-2.9 to +7.0) |
| D5 | 23/30 (-20.3 to -0.9) | 18/30 (-14.3 to +5.6) | 3/30 (-3.5 to +5.5) | 0/30 (-1.7 to +0.5) | 0/30 (-1.7 to +0.5) | 0/30 (-1.7 to +0.5) | 2 (-3.6 to +5.3) | 0 (-1.7 to +0.5) |
| D1 | 26/30 (-21.2 to -0.8) | 17/30 (-19.1 to +5.3) | 7/30 (-8.1 to +1.6) | 1/30 (-3.9 to +0.8) | 1/30 (-3.9 to +0.8) | 1/30 (-3.9 to +0.8) | 7 (-8.1 to +2.6) | 1 (-3.9 to +0.8) |
| D0.03cc | 27/30 (-21.2 to -0.4) | 17/30 (-20.5 to +4.7) | 9/30 (-9.6 to +0.5) | 1/30 (-4.6 to +1.1) | 1/30 (-4.6 to +1.1) | 1/30 (-4.6 to +1.1) | 7 (-7.8 to +0.9) | 1 (-4.6 to +0.9) |
| **All** | **190/255** | **169/255** | **99/255** | **36/255** | **23/255** | **23/255** | **93** | **18** |
| **Without Dmin, Dmax** | **140/195** | **121/195** | **56/195** | **10/195** | **10/195** | **10/195** | **53** | **18** |

#### Test 2, shifted half a dose voxel off the grid

Cells: parameters more than 3 % from the analytic value / parameters scored (lowest to highest % difference). Differences are relative to the local analytic value, as published, so the low-dose parameters (Dmin, D99, D95) are amplified.

| Parameter | dicompyler | dicompyler-ss | mask | mask-ss | polygon | autoseg |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| V | 14/30 (-25.4 to +3.5) | 3/30 (-3.9 to +3.1) | 1/30 (-4.0 to +2.6) | 1/30 (-4.0 to +2.6) | 2/30 (-4.6 to +0.6) | 2/30 (-4.6 to +0.6) |
| Dmin | 27/30 (+0.0 to +300.0) | 30/30 (+14.3 to +180.0) | 30/30 (+7.5 to +180.0) | 15/30 (+2.5 to +124.6) | 13/30 (+2.5 to +4.6) | 13/30 (+2.5 to +4.6) |
| Dmax | 28/30 (-15.3 to -1.8) | 18/30 (-15.3 to +4.0) | 13/30 (-15.3 to -1.1) | 7/30 (-10.6 to -0.4) | 0/30 (-0.4 to +0.0) | 0/30 (-0.4 to +0.0) |
| Dmean | 4/30 (-9.4 to +1.2) | 11/30 (-0.2 to +9.4) | 0/30 (-0.1 to +0.1) | 0/30 (-0.1 to +0.1) | 0/30 (-0.2 to +0.0) | 0/30 (-0.2 to +0.0) |
| D99 | 27/30 (-100.0 to +9.1) | 29/30 (-100.0 to +22.9) | 16/30 (-7.6 to +46.3) | 5/30 (-3.4 to +17.4) | 5/30 (-3.4 to +22.2) | 5/30 (-3.4 to +22.2) |
| D95 | 25/30 (-100.0 to +1.2) | 28/30 (-100.0 to +27.5) | 20/30 (-7.0 to +20.5) | 3/30 (-2.1 to +6.9) | 2/30 (-2.1 to +6.9) | 2/30 (-2.1 to +6.9) |
| D5 | 24/30 (-20.3 to -0.9) | 18/30 (-14.3 to +5.6) | 3/30 (-7.2 to +1.3) | 0/30 (-1.7 to +0.5) | 0/30 (-1.7 to +0.5) | 0/30 (-1.7 to +0.5) |
| D1 | 28/30 (-21.2 to -2.2) | 17/30 (-19.1 to +5.3) | 7/30 (-8.1 to +1.6) | 1/30 (-3.0 to +0.8) | 1/30 (-3.9 to +0.8) | 1/30 (-3.9 to +0.8) |
| D0.03cc | 29/30 (-21.2 to -0.7) | 17/30 (-20.5 to +4.7) | 8/30 (-7.7 to +1.2) | 0/30 (-2.8 to +1.1) | 1/30 (-4.6 to +1.1) | 1/30 (-4.6 to +1.1) |
| **All** | **206/270** | **171/270** | **98/270** | **32/270** | **24/270** | **24/270** |
| **Without Dmin, Dmax** | **151/210** | **123/210** | **55/210** | **10/210** | **11/210** | **11/210** |

#### The same at 2 %

| Parameters beyond 2 %, without Dmin, Dmax | dicompyler | dicompyler-ss | mask | mask-ss | polygon | autoseg |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Test 1 | 155/260 | 122/260 | 16/260 | 8/260 | 4/260 | 4/260 |
| Test 2 | 155/195 | 143/195 | 66/195 | 22/195 | 20/195 | 20/195 |
| Test 2, shifted half a dose voxel off the grid | 163/210 | 146/210 | 69/210 | 26/210 | 22/210 | 22/210 |

#### Test 3: volume error along the whole curve

The volume error at every 0.1 Gy from 0 to 30 Gy (301 points), as % of the structure's analytic volume, on the Test 2 data at 1 and 3 mm. Each cell averages the five shapes' lowest, highest and mean error and SD, like the paper's Table III: lowest to highest (mean ± SD).

| Data | dicompyler | dicompyler-ss | mask | mask-ss | polygon | autoseg | Pinnacle3 (paper) | PlanIQ (paper) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 mm, superior-inferior | -5.4 to +1.8 (-1.8 ± 1.7) | -3.5 to +2.9 (-0.1 ± 1.2) | -3.7 to +2.8 (-0.2 ± 1.2) | -0.9 to +0.2 (-0.2 ± 0.3) | -0.8 to +0.0 (-0.3 ± 0.3) | -0.8 to +0.0 (-0.3 ± 0.3) | -4.4 to +2.2 (-0.3 ± 1.3) | -0.4 to +0.4 (+0.0 ± 0.2) |
| 1 mm, anterior-posterior | -5.6 to +1.5 (-2.2 ± 1.5) | -0.1 to +4.2 (+1.7 ± 1.2) | -1.7 to +2.2 (+0.1 ± 0.8) | -0.4 to +0.8 (+0.1 ± 0.3) | -0.2 to +0.7 (+0.1 ± 0.3) | -0.2 to +0.7 (+0.1 ± 0.3) | -3.5 to +0.8 (-0.8 ± 1.0) | -0.4 to +0.6 (+0.1 ± 0.2) |
| 3 mm, superior-inferior | -14.7 to +6.3 (-4.6 ± 4.7) | -10.5 to +9.4 (-0.2 ± 3.7) | -10.9 to +8.8 (-0.9 ± 3.7) | -2.1 to +0.8 (-0.7 ± 0.7) | -1.9 to +0.6 (-0.6 ± 0.6) | -1.9 to +0.6 (-0.6 ± 0.6) | -11.2 to +7.9 (-0.7 ± 3.5) | -1.5 to +0.6 (-0.5 ± 0.5) |
| 3 mm, anterior-posterior | -17.0 to +4.3 (-6.8 ± 4.5) | -1.1 to +12.7 (+4.9 ± 3.8) | -3.6 to +1.4 (-0.9 ± 1.1) | -2.3 to +0.2 (-0.9 ± 0.8) | -2.0 to +0.2 (-0.8 ± 0.6) | -2.0 to +0.2 (-0.8 ± 0.6) | -4.1 to +0.8 (-1.2 ± 1.1) | -1.5 to +0.8 (-0.3 ± 0.6) |

### Disc phantoms

576 cases: 3 shapes × 4 radii × 2 grids × 8 random placements × 3 dose directions. The dose is 50 Gy at the centre with a 1 Gy/mm gradient, so an error in Gy is also the boundary shift, in mm, that would cause it.

#### Every case

Mean / largest absolute error over all cases. Doses in Gy; volume in %.

| Metric | dicompyler | dicompyler-ss | mask | mask-ss | polygon | autoseg |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| V (%) | 7.15 / 69.77 | 1.57 / 20.49 | 1.56 / 16.45 | 1.56 / 16.45 | 0.00 / 0.00 | 0.00 / 0.00 |
| Dmin | 1.40 / 3.80 | 0.79 / 2.00 | 0.98 / 1.72 | 0.18 / 0.60 | 0.09 / 0.15 | 0.09 / 0.15 |
| Dmax | 1.35 / 2.90 | 1.56 / 2.68 | 0.99 / 1.75 | 0.19 / 0.59 | 0.09 / 0.15 | 0.09 / 0.15 |
| Dmean | 0.14 / 1.30 | 0.67 / 1.39 | 0.04 / 0.41 | 0.04 / 0.41 | 0.00 / 0.00 | 0.00 / 0.00 |
| D99 | 32.22 / 48.53 | 22.47 / 48.53 | 0.53 / 1.47 | 0.09 / 0.48 | 0.03 / 0.10 | 0.03 / 0.10 |
| D95 | 22.16 / 48.65 | 11.98 / 48.65 | 0.39 / 1.35 | 0.07 / 0.40 | 0.04 / 0.11 | 0.04 / 0.11 |
| D5 | 3.51 / 52.10 | 2.56 / 51.35 | 0.37 / 1.35 | 0.07 / 0.50 | 0.04 / 0.11 | 0.04 / 0.11 |
| D1 | 4.18 / 52.36 | 2.93 / 51.47 | 0.54 / 1.47 | 0.09 / 0.46 | 0.03 / 0.10 | 0.03 / 0.10 |
| D0.03cc | 4.76 / 50.99 | 2.74 / 49.97 | 0.44 / 1.48 | 0.10 / 0.64 | 0.03 / 0.10 | 0.03 / 0.10 |
| Worst of Dmean-D0.03cc | 32.96 / 52.36 | 22.90 / 51.47 | 0.69 / 1.48 | 0.15 / 0.64 | 0.06 / 0.11 | 0.06 / 0.11 |
| Whole curve, largest ΔV (%) | 17.41 / 106.14 | 15.66 / 53.62 | 9.24 / 53.38 | 2.56 / 19.70 | 0.85 / 3.82 | 0.85 / 3.82 |

#### By size

Median / largest of each case's worst error among Dmean, D99, D95, D5, D1 and D0.03cc (Gy), then the volume error (%).

| Size | dicompyler | dicompyler-ss | mask | mask-ss | polygon | autoseg |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| R = 2.5 mm, worst dose | 47.99 / 52.36 | 47.64 / 51.47 | 0.82 / 1.47 | 0.16 / 0.64 | 0.06 / 0.10 | 0.06 / 0.10 |
| R = 5 mm, worst dose | 45.56 / 46.50 | 23.21 / 46.50 | 0.52 / 1.41 | 0.10 / 0.43 | 0.06 / 0.10 | 0.06 / 0.10 |
| R = 10 mm, worst dose | 40.55 / 42.52 | 1.72 / 41.90 | 0.46 / 1.40 | 0.09 / 0.39 | 0.07 / 0.10 | 0.07 / 0.10 |
| R = 20 mm, worst dose | 3.30 / 31.91 | 1.86 / 31.91 | 0.46 / 1.48 | 0.09 / 0.48 | 0.08 / 0.11 | 0.08 / 0.11 |
| R = 2.5 mm, volume (%) | 18.51 / 69.77 | 2.52 / 20.49 | 3.71 / 16.45 | 3.71 / 16.45 | 0.00 / 0.00 | 0.00 / 0.00 |
| R = 5 mm, volume (%) | 4.51 / 20.42 | 0.32 / 1.79 | 1.15 / 2.77 | 1.15 / 2.77 | 0.00 / 0.00 | 0.00 / 0.00 |
| R = 10 mm, volume (%) | 1.54 / 8.33 | 0.20 / 0.81 | 0.65 / 2.49 | 0.65 / 2.49 | 0.00 / 0.00 | 0.00 / 0.00 |
| R = 20 mm, volume (%) | 0.34 / 2.52 | 0.05 / 0.48 | 0.12 / 0.57 | 0.12 / 0.57 | 0.00 / 0.00 | 0.00 / 0.00 |

#### By dose direction and grid

Median / largest worst dose error (Gy).

| Subset | dicompyler | dicompyler-ss | mask | mask-ss | polygon | autoseg |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| in-plane (x) | 41.60 / 52.36 | 2.00 / 48.39 | 0.47 / 0.86 | 0.23 / 0.64 | 0.08 / 0.10 | 0.08 / 0.10 |
| through-plane (z) | 43.43 / 51.47 | 43.12 / 51.47 | 1.08 / 1.48 | 0.09 / 0.50 | 0.09 / 0.11 | 0.09 / 0.11 |
| oblique (45° x-z) | 2.63 / 52.35 | 1.33 / 48.21 | 0.38 / 1.23 | 0.08 / 0.58 | 0.01 / 0.05 | 0.01 / 0.05 |
| CT 0.98 × 0.98 × 2 mm, dose 2 mm | 41.90 / 52.36 | 2.86 / 48.20 | 0.51 / 1.04 | 0.11 / 0.50 | 0.07 / 0.11 | 0.07 / 0.11 |
| CT 0.98 × 0.98 × 3 mm, dose 2.5 mm | 43.01 / 52.36 | 29.42 / 51.47 | 0.57 / 1.48 | 0.10 / 0.64 | 0.06 / 0.10 | 0.06 / 0.10 |
| sphere | 41.34 / 52.34 | 2.02 / 51.47 | 0.51 / 1.47 | 0.10 / 0.50 | 0.07 / 0.11 | 0.07 / 0.11 |
| cylinder | 41.95 / 52.34 | 30.89 / 51.47 | 0.54 / 1.48 | 0.10 / 0.46 | 0.07 / 0.11 | 0.07 / 0.11 |
| ring | 41.90 / 52.36 | 30.40 / 51.47 | 0.59 / 1.47 | 0.11 / 0.64 | 0.07 / 0.11 | 0.07 / 0.11 |

#### Time per structure

Median seconds for one structure and one dose, from nothing.

| Size | dicompyler | dicompyler-ss | mask | mask-ss | polygon | autoseg |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| R = 2.5 mm | 0.01 | 0.01 | 0.01 | 0.01 | 0.01 | 0.01 |
| R = 5 mm | 0.01 | 0.05 | 0.01 | 0.02 | 0.02 | 0.02 |
| R = 10 mm | 0.06 | 0.28 | 0.01 | 0.09 | 0.10 | 0.10 |
| R = 20 mm | 0.23 | 1.84 | 0.03 | 0.65 | 0.56 | 0.57 |

### Sub-sample spacing: accuracy against cost

The two sub-sampled methods at three spacings, on the 30 Test 2 datasets (Dmin and Dmax set aside). The sample count is per structure, and so is the time: building the samples once plus one dose lookup.

| Method | > 3 % | > 2 % | Worst |%| | Samples | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| mask-ss @ 1 mm | 36/195 | 46/195 | 25.4 | 20,088 | 0.04 |
| mask-ss @ 0.5 mm | 13/195 | 23/195 | 19.4 | 449,280 | 0.11 |
| mask-ss @ 0.25 mm | 10/195 | 22/195 | 22.2 | 808,704 | 0.14 |
| polygon @ 1 mm | 35/195 | 46/195 | 25.4 | 21,572 | 0.02 |
| polygon @ 0.5 mm | 14/195 | 22/195 | 19.4 | 459,900 | 0.07 |
| polygon @ 0.25 mm | 10/195 | 20/195 | 22.2 | 827,820 | 0.12 |

### Large structures: time against accuracy

One sphere or cylinder per size (the cylinders as tall as they are wide) on the tender cohort's grid: CT 0.98 × 0.98 × 2 mm, dose 2 mm. The dose is 100 Gy at the centre rising 0.25 Gy/mm obliquely, gentler than elsewhere so it stays positive across 20 cm.

#### Seconds for one structure against one dose

From reading the contours to the statistics, single-threaded, on the machine named under Environment; a run under 5 s is the faster of two. In brackets: dose samples taken, in millions.

| Structure | dicompyler | autoseg | mask | mask-ss @ 1 mm | mask-ss @ 0.5 mm | mask-ss @ 0.25 mm | polygon @ 1 mm | polygon @ 0.5 mm | polygon @ 0.25 mm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| sphere, R 10 mm (4 cc) | 0.04 | 0.09 (0.5 M) | 0.02 (0.0 M) | 0.02 (0.0 M) | 0.03 (0.1 M) | 0.07 (0.5 M) | 0.02 (0.0 M) | 0.03 (0.1 M) | 0.09 (0.5 M) |
| sphere, R 20 mm (33 cc) | 0.17 | 0.44 (4.0 M) | 0.03 (0.0 M) | 0.04 (0.1 M) | 0.12 (0.8 M) | 0.57 (3.9 M) | 0.05 (0.1 M) | 0.15 (0.8 M) | 0.45 (4.0 M) |
| sphere, R 40 mm (268 cc) | 0.91 | 0.78 (6.4 M) | 0.07 (0.1 M) | 0.10 (0.4 M) | 0.90 (6.3 M) | 4.33 (31.6 M) | 0.15 (0.4 M) | 0.78 (6.4 M) | 3.78 (31.8 M) |
| sphere, R 60 mm (905 cc) | 2.46 | 0.30 (1.5 M) | 0.14 (0.5 M) | 0.22 (1.4 M) | 2.90 (21.3 M) | 14.79 (106.6 M) | 0.30 (1.5 M) | 2.62 (21.5 M) | 14.33 (107.1 M) |
| cylinder, R 80 mm (3,177 cc) | 6.11 | 0.71 (5.1 M) | 0.28 (1.7 M) | 0.69 (5.0 M) | 10.36 (74.9 M) | 51.35 (374.5 M) | 0.71 (5.1 M) | 10.80 (75.3 M) | 52.17 (375.6 M) |
| cylinder, R 100 mm (6,220 cc) | 10.85 | 1.23 (9.9 M) | 0.46 (3.3 M) | 1.28 (9.8 M) | 20.34 (146.6 M) | 100.91 (732.9 M) | 1.24 (9.9 M) | 21.22 (147.2 M) | 104.44 (735.0 M) |

#### Accuracy

The worst error among Dmean, D99, D95, D5, D1 and D0.03cc, as the boundary shift that would cause it (Gy ÷ 0.25), then the volume error.

| Structure | dicompyler | autoseg | mask | mask-ss @ 1 mm | mask-ss @ 0.5 mm | mask-ss @ 0.25 mm | polygon @ 1 mm | polygon @ 0.5 mm | polygon @ 0.25 mm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| sphere, R 10 mm (4 cc) | 1.42 mm / +0.32 % | 0.00 mm / -0.00 % | 0.36 mm / +0.17 % | 0.09 mm / +0.17 % | 0.07 mm / +0.17 % | 0.07 mm / +0.17 % | 0.09 mm / -0.00 % | 0.01 mm / -0.00 % | 0.00 mm / -0.00 % |
| sphere, R 20 mm (33 cc) | 1.23 mm / -0.15 % | 0.01 mm / -0.00 % | 0.20 mm / -0.04 % | 0.07 mm / -0.04 % | 0.02 mm / -0.04 % | 0.03 mm / -0.04 % | 0.08 mm / -0.00 % | 0.01 mm / -0.00 % | 0.01 mm / -0.00 % |
| sphere, R 40 mm (268 cc) | 1.34 mm / -0.00 % | 0.00 mm / -0.00 % | 0.22 mm / +0.00 % | 0.19 mm / +0.00 % | 0.04 mm / +0.00 % | 0.04 mm / +0.00 % | 0.02 mm / -0.00 % | 0.00 mm / -0.00 % | 0.00 mm / -0.00 % |
| sphere, R 60 mm (905 cc) | 1.71 mm / -0.02 % | 0.09 mm / -0.00 % | 0.50 mm / +0.01 % | 0.09 mm / +0.01 % | 0.03 mm / +0.01 % | 0.03 mm / +0.01 % | 0.09 mm / -0.00 % | 0.01 mm / -0.00 % | 0.00 mm / -0.00 % |
| cylinder, R 80 mm (3,177 cc) | 1.82 mm / -0.07 % | 0.01 mm / +0.00 % | 0.21 mm / +0.03 % | 0.02 mm / +0.03 % | 0.03 mm / +0.03 % | 0.05 mm / +0.03 % | 0.01 mm / +0.00 % | 0.02 mm / +0.00 % | 0.01 mm / +0.00 % |
| cylinder, R 100 mm (6,220 cc) | 1.16 mm / +0.01 % | 0.15 mm / +0.00 % | 0.15 mm / -0.03 % | 0.15 mm / -0.03 % | 0.10 mm / -0.03 % | 0.12 mm / -0.03 % | 0.15 mm / +0.00 % | 0.05 mm / +0.00 % | 0.00 mm / +0.00 % |

### dicompyler with its lookup corrected

dicompyler's own histogram, with D*x* read the way the other methods read it (the lowest dose the hottest *x* receives, to the 1 cGy bin). The volume, Dmin, Dmax and Dmean are unchanged. This separates what dicompyler's lookup costs (next section) from what its sampling costs, and is what keeping dicompyler with only the lookup replaced would give.

|  | dicompyler | dicompyler (lookup corrected) | dicompyler-ss | dicompyler-ss (lookup corrected) | mask-ss | polygon | autoseg |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Nelms Test 1: beyond 3 %, without Dmin, Dmax | 119/260 | 83/260 | 76/260 | 81/260 | 0/260 | 0/260 | 0/260 |
| Nelms Test 2: beyond 3 %, without Dmin, Dmax | 140/195 | 103/195 | 121/195 | 112/195 | 10/195 | 10/195 | 10/195 |
| Nelms Test 2, shifted half a dose voxel off the grid: beyond 3 %, without Dmin, Dmax | 151/210 | 114/210 | 123/210 | 114/210 | 10/210 | 11/210 | 11/210 |
| Disc phantoms: worst of Dmean-D0.03cc, median / largest (Gy) | 41.90 / 52.36 | 1.12 / 3.66 | 29.42 / 51.47 | 1.26 / 2.04 | 0.10 / 0.64 | 0.07 / 0.11 | 0.07 / 0.11 |

### Doses reported as 0 Gy

dicompyler-core's `dose_constraint` answers D*x* with the first dose bin whose cumulative volume is nearest *x*. Every bin from 0 Gy up to Dmin holds 100 % of the volume, so when no later bin is nearer *x* than 100 % is, the answer is the first bin: 0 Gy. For D99 that happens once the coldest 1 cGy bin holds more than 2 % of the volume, for D95 more than 10 %; a structure whose dose falls in a single bin gets 0 Gy for every D*x*, and a D*x*cc larger than the volume dicompyler found gets 0 Gy too. Few dose samples, or whole planes sharing one dose, are enough.

| Benchmark | Method | Cases | Statistics at 0 Gy |
| --- | --- | ---: | --- |
| Disc phantoms | dicompyler | 428/576 | D99 428, D95 271, D0.03cc 31, D5 20, D1 20 |
| Disc phantoms | dicompyler-ss | 289/576 | D99 289, D95 141, D5 10, D1 10, D0.03cc 10 |
| Nelms | dicompyler | 49/100 | D99 49, D95 9 |
| Nelms | dicompyler-ss | 29/100 | D99 29, D95 4 |

### Failures

No method failed on any case.

### Checks on the harness

- Closed-form disc truth against brute-force integration over the written polygons (96-point Gauss-Legendre through each slab, exact polygon clipping), largest difference as a share of the structure's volume: 5.2e-08.
- The application's trilinear dose sampling against SimpleITK's resampling, which v2's consensus DVH used, at every voxel of a Nelms sphere: largest difference 7.1e-15 Gy.
- Polygon's sub-cell coverage and centroids, found by accumulation, against clipping every sub-cell with shapely (Nelms and disc outlines, 1-5 sub-cells per voxel edge): 3,123,651 sub-cells, the same cells covered; largest difference in the share covered 4e-13, in the centroid 7e-09 voxel.
- The dicompyler rows are v2's calls, frozen in this script. Every case was checked equal to v2's `compute_dvh_metrics` before v3 replaced it (commit `acced87`), and the frozen copy reproduces that run exactly on all 100 Nelms rows.
- Timings are single-threaded and comparable only within one run: regenerate the report on an otherwise idle machine before quoting them.

### Environment

AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD, 16 logical cores; Python 3.12.3 on Windows 10; numpy 1.26.4, scipy 1.13.1, shapely 2.0.6, SimpleITK 2.3.1, pydicom 2.4.4, dicompyler-core 0.5.6. Sub-sample spacing 0.25 mm; 8 placements per disc phantom; seed 20260924.

### Test 3 by dataset

Lowest to highest volume error (%).

| Dataset | dicompyler | dicompyler-ss | mask | mask-ss | polygon | autoseg |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Cone_10_0, AP | -3.6 to +3.6 | +0.0 to +4.7 | -1.5 to +2.4 | -0.1 to +0.8 | -0.0 to +0.8 | -0.0 to +0.8 |
| Cylinder_10_0, AP | -3.8 to +1.3 | +0.0 to +3.2 | -0.8 to +1.9 | -0.0 to +0.9 | -0.0 to +0.5 | -0.0 to +0.5 |
| RtCone_10_0, AP | -10.8 to +0.0 | +0.0 to +7.2 | -2.5 to +3.6 | -0.2 to +1.2 | -0.0 to +1.2 | -0.0 to +1.2 |
| RtCylinder_10_0, AP | -6.2 to +0.0 | -0.4 to +2.5 | -2.1 to +1.2 | -1.3 to +0.4 | -0.9 to +0.4 | -0.9 to +0.4 |
| Sphere_10_0, AP | -3.5 to +2.5 | -0.2 to +3.5 | -1.4 to +1.9 | -0.2 to +0.6 | -0.2 to +0.6 | -0.2 to +0.6 |
| Cone_10_0, SI | -5.9 to +4.4 | -5.9 to +4.7 | -5.9 to +4.8 | -1.2 to +0.2 | -1.2 to +0.1 | -1.2 to +0.1 |
| Cylinder_10_0, SI | -4.8 to +1.5 | -2.0 to +1.7 | -2.0 to +2.2 | -0.4 to +0.6 | -0.4 to +0.0 | -0.4 to +0.0 |
| RtCone_10_0, SI | -8.1 to +0.3 | -3.5 to +3.7 | -3.9 to +3.2 | -0.8 to +0.2 | -0.8 to +0.1 | -0.8 to +0.1 |
| RtCylinder_10_0, SI | -4.1 to +1.0 | -2.9 to +2.0 | -3.4 to +1.5 | -1.3 to +0.0 | -1.0 to +0.0 | -1.0 to +0.0 |
| Sphere_10_0, SI | -4.1 to +1.9 | -3.2 to +2.4 | -3.2 to +2.5 | -0.7 to +0.1 | -0.7 to +0.0 | -0.7 to +0.0 |
| Cone_30_0, AP | -13.6 to +10.2 | +0.0 to +14.5 | -2.1 to +1.8 | -0.6 to +0.5 | -0.4 to +0.7 | -0.4 to +0.7 |
| Cylinder_30_0, AP | -12.2 to +5.2 | +0.0 to +10.3 | -1.4 to +1.4 | -0.3 to +0.6 | -0.5 to +0.0 | -0.5 to +0.0 |
| RtCone_30_0, AP | -33.0 to +0.0 | +0.0 to +20.8 | -3.8 to +2.1 | -1.7 to +0.0 | -1.6 to +0.0 | -1.6 to +0.0 |
| RtCylinder_30_0, AP | -14.7 to +0.0 | -3.9 to +7.3 | -7.2 to +0.7 | -6.5 to +0.0 | -5.0 to +0.0 | -5.0 to +0.0 |
| Sphere_30_0, AP | -11.7 to +6.2 | -1.8 to +10.4 | -3.5 to +1.2 | -2.4 to +0.1 | -2.2 to +0.1 | -2.2 to +0.1 |
| Cone_30_0, SI | -15.8 to +12.7 | -15.8 to +14.9 | -15.8 to +14.9 | -1.1 to +2.1 | -1.2 to +1.9 | -1.2 to +1.9 |
| Cylinder_30_0, SI | -11.3 to +4.5 | -5.6 to +5.7 | -5.6 to +5.8 | -0.4 to +1.0 | -0.4 to +0.4 | -0.4 to +0.4 |
| RtCone_30_0, SI | -23.9 to +0.9 | -10.8 to +12.9 | -12.0 to +11.1 | -1.2 to +1.0 | -1.3 to +0.9 | -1.3 to +0.9 |
| RtCylinder_30_0, SI | -10.2 to +7.2 | -10.1 to +5.7 | -11.0 to +4.4 | -6.1 to +0.0 | -4.6 to +0.0 | -4.6 to +0.0 |
| Sphere_30_0, SI | -12.0 to +6.6 | -10.3 to +7.9 | -10.3 to +7.9 | -1.9 to +0.0 | -1.7 to +0.0 | -1.7 to +0.0 |

## Part B: DVH from a mask against DVH from the contours

From `scripts/validate_dvh_mask_vs_polygon.py`, a run of 5 minutes.

100 structure/dose cases from Nelms et al. 2015, each through both paths.

### The two paths

|  | Contours (RTSTRUCT) | Mask (STAPLE, consensus) |
| --- | --- | --- |
| Used for | Every structure stored as contours | A drawer's STAPLE consensus; a multi-observer consensus used as ground truth |
| Region | The contours' own polygons, each standing for a slab one slice thick | The voxels of the mask rasterised from those contours on the CT grid |
| Samples | Sub-cells weighted by the exact area of polygon inside them | Sub-samples of each whole voxel, equally weighted |
| Spacing | The finest of 0.25, 0.5, 1 mm within 10 M samples | The same rule |
| Dose | Trilinear, at each sample | Trilinear, at each sample |

The dose sampling and the histogram are shared; only the region differs. What separates the two paths is therefore the mask's voxel staircase: it includes or leaves out whole voxels where the contour cuts through them.

CT grids the masks were made on (voxel size in mm; columns × rows × slices):

| Contour spacing | CT voxel (mm) | CT size |
| --- | ---: | ---: |
| 0.2mm | 0.6 × 0.6 × 0.2 | 512 × 512 × 300 |
| 1mm | 0.6 × 0.6 × 1 | 512 × 512 × 60 |
| 2mm | 0.6 × 0.6 × 2 | 512 × 512 × 30 |
| 3mm | 0.6 × 0.6 × 3 | 512 × 512 × 20 |

### Against the analytic truth

Parameters more than 3 % from the analytic value, out of those scored, and the range of the % differences, as Nelms et al. count them. Volume counts once per structure and dose grid. Differences are relative to the local analytic value, so the low-dose parameters (Dmin, D99, D95) are amplified.

#### Test 1: contours every 0.2 mm, dose grid 0.4-3 mm

| Parameter | Contours (RTSTRUCT) | Mask (STAPLE, consensus) |
| --- | ---: | ---: |
| V | 0/20 (-0.1 to -0.0) | 0/20 (-0.2 to +0.6) |
| Dmin | 4/40 (+2.5 to +3.3) | 20/40 (+2.5 to +28.2) |
| Dmax | 0/40 (-0.4 to +0.0) | 0/40 (-1.1 to -0.4) |
| Dmean | 0/40 (-0.0 to +0.0) | 0/40 (-0.0 to +0.0) |
| D99 | 0/40 (-1.7 to +1.7) | 0/40 (-2.0 to +1.4) |
| D95 | 0/40 (-1.2 to +2.0) | 0/40 (-1.2 to +2.0) |
| D5 | 0/40 (-0.3 to +0.4) | 0/40 (-0.3 to +0.4) |
| D1 | 0/40 (-0.3 to +0.3) | 0/40 (-0.2 to +0.4) |
| D0.03cc | 0/40 (-0.4 to +0.2) | 0/40 (-0.4 to +0.4) |
| **All** | **4/340** | **20/340** |
| **Without Dmin, Dmax** | **0/260** | **0/260** |

#### Test 2: contours and dose grid both 1, 2 or 3 mm, aligned

| Parameter | Contours (RTSTRUCT) | Mask (STAPLE, consensus) |
| --- | ---: | ---: |
| V | 1/15 (-4.6 to +0.6) | 1/15 (-6.1 to +0.6) |
| Dmin | 13/30 (+2.5 to +4.6) | 17/30 (+2.5 to +124.6) |
| Dmax | 0/30 (-0.4 to +0.0) | 9/30 (-10.6 to -0.4) |
| Dmean | 0/30 (-0.2 to +0.0) | 0/30 (-0.3 to +0.1) |
| D99 | 5/30 (-3.4 to +22.2) | 5/30 (-3.4 to +22.2) |
| D95 | 2/30 (-2.1 to +6.9) | 2/30 (-2.1 to +6.9) |
| D5 | 0/30 (-1.7 to +0.5) | 0/30 (-1.7 to +0.5) |
| D1 | 1/30 (-3.9 to +0.8) | 1/30 (-3.9 to +0.8) |
| D0.03cc | 1/30 (-4.6 to +1.1) | 1/30 (-4.6 to +1.1) |
| **All** | **23/255** | **36/255** |
| **Without Dmin, Dmax** | **10/195** | **10/195** |

#### Test 2, shifted half a dose voxel off the grid

| Parameter | Contours (RTSTRUCT) | Mask (STAPLE, consensus) |
| --- | ---: | ---: |
| V | 2/30 (-4.6 to +0.6) | 1/30 (-4.0 to +2.6) |
| Dmin | 13/30 (+2.5 to +4.6) | 15/30 (+2.5 to +124.6) |
| Dmax | 0/30 (-0.4 to +0.0) | 7/30 (-10.6 to -0.4) |
| Dmean | 0/30 (-0.2 to +0.0) | 0/30 (-0.1 to +0.1) |
| D99 | 5/30 (-3.4 to +22.2) | 5/30 (-3.4 to +17.4) |
| D95 | 2/30 (-2.1 to +6.9) | 3/30 (-2.1 to +6.9) |
| D5 | 0/30 (-1.7 to +0.5) | 0/30 (-1.7 to +0.5) |
| D1 | 1/30 (-3.9 to +0.8) | 1/30 (-3.0 to +0.8) |
| D0.03cc | 1/30 (-4.6 to +1.1) | 0/30 (-2.8 to +1.1) |
| **All** | **24/270** | **32/270** |
| **Without Dmin, Dmax** | **11/210** | **10/210** |

At the stricter 2 %, without Dmin and Dmax:

|  | Contours (RTSTRUCT) | Mask (STAPLE, consensus) |
| --- | ---: | ---: |
| Test 1 | 4/260 | 8/260 |
| Test 2 | 20/195 | 22/195 |
| Test 2, shifted half a dose voxel off the grid | 22/210 | 26/210 |

### Mask against contours, structure by structure

The same structure and dose through both paths: the mask path's value minus the polygon path's. This is what separates a consensus's dose row from a contour's in the results table when the two describe the same shape. Doses in Gy (the fields rise 1 Gy per mm), volume in cc; the % columns are relative to the analytic value. Every test and dose grid.

| Parameter | Median |Δ| | 95th pct |Δ| | Largest |Δ| | Median |Δ| % | 95th pct |Δ| % | Largest |Δ| % | Pairs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| V | 0.012 cc | 0.098 cc | 0.186 cc | 0.16 % | 1.47 % | 3.76 % | 100 |
| Dmin | 0.000 Gy | 2.050 Gy | 3.000 Gy | 0.01 % | 69.33 % | 120.00 % | 100 |
| Dmax | 0.000 Gy | 2.000 Gy | 3.000 Gy | 0.00 % | 6.90 % | 10.17 % | 100 |
| Dmean | 0.000 Gy | 0.010 Gy | 0.028 Gy | 0.00 % | 0.04 % | 0.12 % | 100 |
| D99 | 0.000 Gy | 0.200 Gy | 0.231 Gy | 0.00 % | 3.70 % | 4.83 % | 100 |
| D95 | 0.000 Gy | 0.000 Gy | 0.231 Gy | 0.00 % | 0.00 % | 2.78 % | 100 |
| D5 | 0.000 Gy | 0.000 Gy | 0.231 Gy | 0.00 % | 0.00 % | 0.97 % | 100 |
| D1 | 0.000 Gy | 0.200 Gy | 0.231 Gy | 0.00 % | 0.75 % | 0.85 % | 100 |
| D0.03cc | 0.000 Gy | 0.051 Gy | 0.923 Gy | 0.00 % | 0.20 % | 3.34 % | 100 |

By CT slice (and contour) spacing, which sets the mask's voxel height: the 95th percentile of |Δ| as % of the analytic value.

| Parameter | 0.2mm | 1mm | 2mm | 3mm |
| --- | ---: | ---: | ---: | ---: |
| V | 0.59 % | 0.62 % | 0.69 % | 1.82 % |
| Dmean | 0.04 % | 0.02 % | 0.03 % | 0.12 % |
| D99 | 3.70 % | 3.70 % | 3.70 % | 0.44 % |
| D95 | 0.00 % | 1.60 % | 0.00 % | 0.14 % |
| D5 | 0.00 % | 0.04 % | 0.00 % | 0.05 % |
| D1 | 0.75 % | 0.75 % | 0.75 % | 0.07 % |
| D0.03cc | 0.20 % | 0.59 % | 0.00 % | 0.17 % |

By structure, largest |Δ| over its dose grids and contour spacings:

| Structure | Volume (cc) | Volume | Dmean |
| --- | ---: | ---: | ---: |
| RtCone_02_0 | 3.62 | 0.13 % | 0.04 % |
| RtCone_10_0 | 3.62 | 0.09 % | 0.10 % |
| RtCone_10_X05 | 3.62 | 0.01 % | 0.00 % |
| RtCone_10_X05Z05 | 3.62 | 1.18 % | 0.00 % |
| RtCone_20_0 | 3.62 | 0.29 % | 0.03 % |
| RtCone_20_X10 | 3.62 | 0.07 % | 0.00 % |
| RtCone_20_X10Z10 | 3.62 | 2.66 % | 0.00 % |
| RtCone_30_0 | 3.62 | 0.12 % | 0.11 % |
| RtCone_30_X15 | 3.62 | 0.12 % | 0.05 % |
| RtCone_30_X15Z15 | 3.62 | 3.76 % | 0.00 % |
| Cone_02_0 | 3.66 | 0.16 % | 0.02 % |
| Cone_10_0 | 3.85 | 0.04 % | 0.01 % |
| Cone_10_X05 | 3.85 | 0.25 % | 0.00 % |
| Cone_10_X05Z05 | 3.85 | 0.06 % | 0.00 % |
| Cone_20_0 | 4.07 | 0.15 % | 0.04 % |
| Cone_20_X10 | 4.07 | 0.08 % | 0.00 % |
| Cone_20_X10Z10 | 4.07 | 0.11 % | 0.03 % |
| Cone_30_0 | 4.30 | 0.13 % | 0.11 % |
| Cone_30_X15 | 4.30 | 0.81 % | 0.01 % |
| Cone_30_X15Z15 | 4.30 | 0.58 % | 0.12 % |
| Sphere_02_0 | 7.24 | 0.16 % | 0.00 % |
| Sphere_10_0 | 7.24 | 0.09 % | 0.00 % |
| Sphere_10_X05 | 7.24 | 0.07 % | 0.00 % |
| Sphere_10_X05Z05 | 7.24 | 0.02 % | 0.00 % |
| Sphere_20_0 | 7.24 | 0.01 % | 0.00 % |
| Sphere_20_X10 | 7.24 | 0.03 % | 0.00 % |
| Sphere_20_X10Z10 | 7.24 | 0.05 % | 0.00 % |
| Sphere_30_0 | 7.24 | 0.20 % | 0.00 % |
| Sphere_30_X15 | 7.24 | 0.20 % | 0.00 % |
| Sphere_30_X15Z15 | 7.24 | 0.38 % | 0.00 % |
| RtCylinder_02_0 | 10.86 | 0.14 % | 0.00 % |
| RtCylinder_10_0 | 10.86 | 0.43 % | 0.00 % |
| RtCylinder_10_X05 | 10.86 | 0.16 % | 0.00 % |
| RtCylinder_10_X05Z05 | 10.86 | 0.16 % | 0.00 % |
| RtCylinder_20_0 | 10.86 | 0.14 % | 0.00 % |
| RtCylinder_20_X10 | 10.86 | 0.14 % | 0.00 % |
| RtCylinder_20_X10Z10 | 10.86 | 0.14 % | 0.00 % |
| RtCylinder_30_0 | 10.86 | 1.47 % | 0.00 % |
| RtCylinder_30_X15 | 10.86 | 1.71 % | 0.00 % |
| RtCylinder_30_X15Z15 | 10.86 | 1.71 % | 0.00 % |
| Cylinder_02_0 | 10.95 | 0.59 % | 0.00 % |
| Cylinder_10_0 | 11.31 | 0.59 % | 0.00 % |
| Cylinder_10_X05 | 11.31 | 0.27 % | 0.00 % |
| Cylinder_10_X05Z05 | 11.31 | 0.11 % | 0.00 % |
| Cylinder_20_0 | 11.76 | 0.59 % | 0.00 % |
| Cylinder_20_X10 | 11.76 | 0.05 % | 0.00 % |
| Cylinder_20_X10Z10 | 11.76 | 0.21 % | 0.00 % |
| Cylinder_30_0 | 12.21 | 0.59 % | 0.00 % |
| Cylinder_30_X15 | 12.21 | 0.36 % | 0.00 % |
| Cylinder_30_X15Z15 | 12.21 | 0.76 % | 0.00 % |

### Along the whole curve

Test 3 of Nelms et al.: the volume error at every 0.1 Gy from 0 to 30 Gy, as % of the structure's analytic volume, on the Test 2 data at 1 and 3 mm. Each cell averages the five shapes' lowest and highest error and their mean ± SD.

| Data | Contours (RTSTRUCT) | Mask (STAPLE, consensus) |
| --- | ---: | ---: |
| 1mm, SI | -0.8 to +0.0 (-0.3 ± 0.3) | -0.9 to +0.2 (-0.2 ± 0.3) |
| 1mm, AP | -0.2 to +0.7 (+0.1 ± 0.3) | -0.4 to +0.8 (+0.1 ± 0.3) |
| 3mm, SI | -1.9 to +0.6 (-0.6 ± 0.6) | -2.1 to +0.8 (-0.7 ± 0.7) |
| 3mm, AP | -2.0 to +0.2 (-0.8 ± 0.6) | -2.3 to +0.2 (-0.9 ± 0.8) |

### On a clinical CT grid

The dataset's CT has 0.6 mm pixels, finer than most planning CTs. The whole comparison again, with the CT's slices kept and its in-plane pixels made 1.07 mm and 1.37 mm: the most common and the coarsest in-plane pixel of this project's cohort (42 and 28 of its 80 CT patients). A mask's staircase is set by its voxels, so the mask path should move; the contour path reads the CT only for its geometry, so it should not. Each coarser grid is laid at 4 sub-pixel offsets, pooled below, because a mask's error depends on where a structure falls among the voxels and the Nelms structures all sit at one position; counts are over every offset.

Parameters beyond 3 % of the analytic value, volume and clinical doses, every test and offset; and the largest Dmean difference between the paths in Gy. The fields rise 1 Gy per mm, so that is also how far, in mm, the mask moved the structure's dose-weighted centre: a mask can shift a boundary by up to half a pixel.

| CT pixel | Contours (RTSTRUCT) | Mask (STAPLE, consensus) | Largest |ΔDmean| (Gy, = mm) |
| --- | ---: | ---: | ---: |
| 0.6 mm (the dataset's) | 21/665 (3.2 %) | 20/665 (3.0 %) | 0.03 |
| 1.07 mm (cohort's most common) | 88/2660 (3.3 %) | 237/2660 (8.9 %) | 0.41 |
| 1.37 mm (cohort's coarsest) | 88/2660 (3.3 %) | 261/2660 (9.8 %) | 0.47 |

Mask against contours for the same structure: 95th percentile / largest
|Δ|, as % of the analytic value.

| CT pixel | V | Dmean | D99 | D95 | D5 | D1 | D0.03cc |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.6 mm (the dataset's) | 1.47 / 3.76 % | 0.04 / 0.12 % | 3.70 / 4.83 % | 0.00 / 2.78 % | 0.00 / 0.97 % | 0.75 / 0.85 % | 0.20 / 3.34 % |
| 1.07 mm (cohort's most common) | 4.36 / 6.63 % | 1.01 / 1.88 % | 5.32 / 10.14 % | 4.35 / 8.28 % | 1.56 / 2.33 % | 1.55 / 1.69 % | 1.56 / 1.69 % |
| 1.37 mm (cohort's coarsest) | 5.42 / 9.41 % | 2.10 / 2.17 % | 8.20 / 13.82 % | 3.75 / 11.28 % | 1.45 / 2.19 % | 1.52 / 2.21 % | 2.12 / 2.21 % |

### A test contour against a reference that is a mask

A consensus reference exists only as a mask. A test contour's DVH can be set against it from the test's contours (mixed) or from the test's own mask, rasterised on the same grid (like for like). To see which is closer to the true difference, each shifted Nelms structure is taken as a test contour and its unshifted twin as the reference, on the same dose grid, CT grid and offset, in two sets:

- **Shifted across the field.** The dataset's own pairings: copies moved sideways (0.5-1.5 mm) in the front-to-back field, and diagonally (sideways and front-to-back, 0.5-1.5 mm each) in the head-to-foot field. Neither movement climbs its field, so the true dose difference is zero, as the dataset's analytic values confirm: any difference measured is discretisation.
- **Shifted along the field.** The diagonal copies again, in the front-to-back field, which their front-to-back movement climbs. A linear field makes the truth exact without the dataset tabulating it: moving a structure by *s* mm along a gradient of *g* Gy/mm adds *g·s* to every dose statistic and leaves its volume alone, here 0.5, 1 or 1.5 Gy. The movement and the gradient are measured from the files, and each copy checked to be an exact translation of its twin.

*Both from contours* is the best case, open only to a reference that has contours. Error in the difference (measured minus true), as % of the reference's analytic value: 95th percentile / largest, over every pair and offset.

#### Shifted across the field: true difference zero

Up to 120 pairs per CT grid, over its offsets.

| CT pixel | Difference taken | V | Dmean | D99 | D95 | D5 | D1 | D0.03cc |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.6 mm (the dataset's) | Both from contours | 0.00 / 0.00 % | 0.00 / 0.00 % | 0.09 / 0.17 % | 0.00 / 0.00 % | 0.00 / 0.00 % | 0.02 / 0.03 % | 0.00 / 0.01 % |
| 0.6 mm (the dataset's) | Test from contours, reference from its mask | 1.07 / 1.47 % | 0.11 / 0.11 % | 3.00 / 3.70 % | 0.86 / 2.41 % | 0.00 / 0.84 % | 0.43 / 0.75 % | 0.00 / 0.59 % |
| 0.6 mm (the dataset's) | Both from masks | 3.18 / 3.88 % | 0.07 / 0.16 % | 1.19 / 4.83 % | 2.03 / 2.78 % | 0.46 / 0.97 % | 0.00 / 0.85 % | 0.00 / 3.34 % |
| 1.07 mm (cohort's most common) | Both from contours | 0.00 / 0.00 % | 0.00 / 0.00 % | 0.00 / 0.07 % | 0.00 / 0.02 % | 0.00 / 0.01 % | 0.00 / 0.01 % | 0.00 / 0.02 % |
| 1.07 mm (cohort's most common) | Test from contours, reference from its mask | 3.92 / 5.96 % | 1.01 / 1.88 % | 5.32 / 10.14 % | 4.35 / 8.28 % | 1.55 / 2.33 % | 1.55 / 1.69 % | 1.49 / 1.69 % |
| 1.07 mm (cohort's most common) | Both from masks | 5.49 / 11.79 % | 0.28 / 0.67 % | 3.96 / 6.44 % | 3.35 / 5.57 % | 0.90 / 1.95 % | 0.81 / 1.55 % | 0.82 / 2.33 % |
| 1.37 mm (cohort's coarsest) | Both from contours | 0.00 / 0.00 % | 0.00 / 0.00 % | 0.00 / 0.09 % | 0.00 / 0.27 % | 0.00 / 0.05 % | 0.00 / 0.03 % | 0.00 / 0.03 % |
| 1.37 mm (cohort's coarsest) | Test from contours, reference from its mask | 5.03 / 7.93 % | 2.03 / 2.17 % | 8.20 / 13.82 % | 3.75 / 11.28 % | 1.45 / 2.19 % | 1.52 / 2.21 % | 2.12 / 2.21 % |
| 1.37 mm (cohort's coarsest) | Both from masks | 5.58 / 11.51 % | 0.25 / 0.79 % | 4.08 / 6.44 % | 2.80 / 3.63 % | 0.84 / 0.97 % | 0.76 / 1.55 % | 0.86 / 1.54 % |

On 1.07 mm (cohort's most common), taking the test from its mask lowers the 95th-percentile error for Dmean 1.01 → 0.28 %, D99 5.32 → 3.96 %, D95 4.35 → 3.35 %, D5 1.55 → 0.90 %, D1 1.55 → 0.81 %, D0.03cc 1.49 → 0.82 %, and raises it for V 3.92 → 5.49 %.

On 1.37 mm (cohort's coarsest), taking the test from its mask lowers the 95th-percentile error for Dmean 2.03 → 0.25 %, D99 8.20 → 4.08 %, D95 3.75 → 2.80 %, D5 1.45 → 0.84 %, D1 1.52 → 0.76 %, D0.03cc 2.12 → 0.86 %, and raises it for V 5.03 → 5.58 %.

#### Shifted along the field: true difference 0.5-1.5 Gy

Up to 60 pairs per CT grid, over its offsets.

| CT pixel | Difference taken | V | Dmean | D99 | D95 | D5 | D1 | D0.03cc |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.6 mm (the dataset's) | Both from contours | 0.00 / 0.00 % | 0.00 / 0.00 % | 2.36 / 2.36 % | 1.92 / 1.92 % | 0.42 / 0.42 % | 0.38 / 0.39 % | 0.37 / 0.38 % |
| 0.6 mm (the dataset's) | Test from contours, reference from its mask | 0.85 / 1.47 % | 0.11 / 0.11 % | 2.76 / 3.70 % | 1.92 / 1.92 % | 0.42 / 0.42 % | 0.50 / 0.75 % | 0.38 / 0.38 % |
| 0.6 mm (the dataset's) | Both from masks | 3.39 / 3.88 % | 1.44 / 1.87 % | 5.42 / 7.08 % | 4.42 / 5.77 % | 1.10 / 1.12 % | 1.08 / 1.08 % | 1.07 / 1.07 % |
| 1.07 mm (cohort's most common) | Both from contours | 0.00 / 0.00 % | 0.00 / 0.00 % | 3.05 / 3.42 % | 2.65 / 2.79 % | 0.58 / 0.60 % | 0.54 / 0.56 % | 0.52 / 0.54 % |
| 1.07 mm (cohort's most common) | Test from contours, reference from its mask | 3.92 / 5.96 % | 1.25 / 1.88 % | 8.55 / 11.79 % | 8.20 / 11.05 % | 1.60 / 2.13 % | 1.69 / 2.04 % | 1.81 / 2.04 % |
| 1.07 mm (cohort's most common) | Both from masks | 6.33 / 11.79 % | 2.67 / 3.39 % | 13.54 / 15.28 % | 11.05 / 12.46 % | 2.16 / 3.73 % | 2.32 / 3.60 % | 2.32 / 3.58 % |
| 1.37 mm (cohort's coarsest) | Both from contours | 0.00 / 0.00 % | 0.00 / 0.00 % | 2.90 / 3.14 % | 2.32 / 3.31 % | 0.54 / 0.72 % | 0.50 / 0.65 % | 0.46 / 0.65 % |
| 1.37 mm (cohort's coarsest) | Test from contours, reference from its mask | 5.03 / 7.93 % | 2.10 / 2.17 % | 12.40 / 15.85 % | 6.45 / 12.46 % | 1.90 / 2.28 % | 2.20 / 2.58 % | 2.49 / 2.57 % |
| 1.37 mm (cohort's coarsest) | Both from masks | 6.15 / 11.51 % | 2.96 / 3.44 % | 20.45 / 29.10 % | 12.92 / 19.98 % | 2.53 / 3.62 % | 3.11 / 3.60 % | 3.13 / 3.61 % |

On 1.07 mm (cohort's most common), taking the test from its mask lowers the 95th-percentile error for no statistic, and raises it for V 3.92 → 6.33 %, Dmean 1.25 → 2.67 %, D99 8.55 → 13.54 %, D95 8.20 → 11.05 %, D5 1.60 → 2.16 %, D1 1.69 → 2.32 %, D0.03cc 1.81 → 2.32 %.

On 1.37 mm (cohort's coarsest), taking the test from its mask lowers the 95th-percentile error for no statistic, and raises it for V 5.03 → 6.15 %, Dmean 2.10 → 2.96 %, D99 12.40 → 20.45 %, D95 6.45 → 12.92 %, D5 1.90 → 2.53 %, D1 2.20 → 3.11 %, D0.03cc 2.49 → 3.13 %.

Taking the test from its mask cancels the mask's error only where the two masks fall among the voxels alike along the dose gradient. The copies shifted across the field do: none moves along its field, so its mask's staircase along the gradient matches its twin's and most of the error cancels. That is, in effect, the case of a contour identical to the reference, where all of it cancels. The copies shifted along the field move 0.5-1.5 mm up the gradient, a fraction of a voxel off their twins' staircase, and then the two masks carry independent errors that add: like for like is worse than taking the test from its contours on every statistic at both of the cohort's grids. A contour from another source differs from a consensus by far more than half a voxel, so it is the second case that applies.

AutoSeg therefore takes every contour's DVH from its contours, whatever it is compared against, and only a consensus, which has none, from its mask (the *DVH from* column says which on every dose row). The consensus's error is then the same in every source's difference, and cancels when two sources are compared with each other; the price is that a contour identical to the consensus differs from it by that error. The V at x Gy columns are not tested here, because the Nelms truth has no V at x Gy for the shifted structures.

### STAPLE check

Each structure's mask was given to STAPLE as three identical raters, with the application's default settings. The consensus equalled the mask voxel for voxel on all 50. A consensus of raters who disagree has no contours to compare with, which is why this benchmark uses agreeing ones: the question here is the cost of the mask, not of the disagreement.

### What this does not cover

The Nelms structures are 3.6-12 cc. A mask's relative error grows as a structure shrinks against its voxels, so for smaller structures -- a cochlea, the optic chiasm -- expect larger differences than these, and for large organs smaller. Part A measures both paths on disc phantoms of 2.5-20 mm radius, where the truth is exact for the contours as drawn. The design comparison uses rigid shifts of 0.5-1.5 mm; a test contour whose shape differs from the reference's is not tested, though a difference in shape makes the two masks' errors less alike still. Consensus masks of raters who disagree are a different question, answered by the STAPLE validation (Supplementary 5), not this.

## Reproduce

```
python scripts/validate_dvh_methods.py --nelms <Nelms et al. dataset folder>
python scripts/validate_dvh_mask_vs_polygon.py --nelms <Nelms et al. dataset folder>
```
