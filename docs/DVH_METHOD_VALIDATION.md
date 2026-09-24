# DVH method validation

Generated 2026-09-24 by `scripts/validate_dvh_methods.py` (AutoSeg 2.4.2, commit 7078bc8). Regenerate with `python scripts/validate_dvh_methods.py --nelms <folder> --out docs/DVH_METHOD_VALIDATION.md`.

Roadmap item #7: should structure-set DVHs keep coming from dicompyler-core, or come from the same contour reading as the geometric metrics? Every candidate is scored here against DVHs whose true values are known exactly. This report records the measurements; the decision is recorded separately.

## Methods

| Method | Contours read by | Dose sampled at | Each sample stands for |
| --- | --- | --- | --- |
| dicompyler | dicompyler-core: every loop tested, loops combined by exclusive-or | each dose-grid point in each contour plane; the dose interpolated between dose planes only | one dose voxel's area × the gap between contour planes |
| dicompyler-ss | the same | a grid a quarter of the dose pixel, in each contour plane | that grid's cell × the gap between contour planes |
| mask | the shared reading, half-open fill (the 3D metrics' own mask) | each CT voxel centre, trilinear | one CT voxel |
| mask-ss | the same mask | sub-samples ≤ 0.25 mm apart in every CT voxel, trilinear | an equal share of its voxel |
| polygon | the shared reading's regions, no voxels | sub-cells ≤ 0.25 mm apart, trilinear, each at the centroid of the part covered | the exact area of the region in its sub-cell × its share of the slice |

Every method treats a contour as a slab one slice thick, centred on its plane, and none interpolates between contours: that is the convention in both benchmarks' truth. The sub-sampling spacing is rounded to an odd number of sub-samples per voxel edge, so one always sits on the voxel centre. D*x* is the lowest dose the hottest *x* of the volume receives. The three sampled methods take a slice at a time into a dose histogram of 1 mGy bins, so memory does not grow with the number of samples; Dmin, Dmax and Dmean are kept exactly and D*x* is read to the bin's centre. Polygon finds each sub-cell's covered area and its centroid exactly, without clipping, by the signed-area accumulation fonts are rasterised with, so its extra work grows with a structure's outline rather than its area. The dicompyler rows are production's numbers: each case is checked against `core.dvh.compute_dvh_metrics`.

## Nelms et al. 2015

Nelms B, Stambaugh C, Hunt D, Tonner B, Zhang G, Feygelman V. *Methods, software and datasets to verify DVH calculations against analytical values: twenty years late(r).* Med Phys 2015;42(8):4435-48. doi:10.1118/1.4923175. PMID 26233174.

A sphere, axial and rotated cylinders, and axial and rotated cones, 24 mm across, contoured every 0.2, 1, 2 or 3 mm on a CT of 0.6 mm pixels, in 1 Gy/mm linear dose fields (16 Gy at the centre) along anterior-posterior or superior-inferior. The truth is the solid itself extended half a slice beyond its end contours, so a method is also charged for filling the gap between contour planes: a rotated cylinder is a stack of rectangles, and no slab method can recover its round side. Pinnacle3 v9.8 and PlanIQ v2.1 are the paper's own results on the same data. The datasets are the paper's supplementary material and are not redistributed with AutoSeg.

### Test 1: contours every 0.2 mm, dose grid 0.4-3 mm

Cells: parameters more than 3 % from the analytic value / parameters scored (lowest to highest % difference). Differences are relative to the local analytic value, as published, so the low-dose parameters (Dmin, D99, D95) are amplified. The paper columns are its Table I.

| Parameter | dicompyler | dicompyler-ss | mask | mask-ss | polygon | Pinnacle3 (paper) | PlanIQ (paper) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| V | 6/20 (-20.2 to -0.1) | 0/20 (-0.0 to +2.8) | 0/20 (-0.2 to +0.6) | 0/20 (-0.2 to +0.6) | 0/20 (-0.1 to -0.0) | 0 (-2.0 to +1.9) | 0 (-0.4 to +0.9) |
| Dmin | 16/40 (+0.0 to +75.0) | 25/40 (+2.6 to +65.8) | 36/40 (+2.6 to +28.2) | 20/40 (+2.5 to +28.2) | 4/40 (+2.5 to +3.3) | 20 (-7.5 to +2.6) | 0 (+0.0 to +0.0) |
| Dmax | 18/40 (-10.7 to -0.4) | 5/40 (-1.1 to +4.0) | 0/40 (-1.1 to -0.4) | 0/40 (-1.1 to -0.4) | 0/40 (-0.4 to +0.0) | 0 (-1.1 to +1.1) | 0 (+0.0 to +0.0) |
| Dmean | 5/40 (-9.4 to +0.0) | 11/40 (-0.0 to +9.4) | 0/40 (-0.0 to +0.0) | 0/40 (-0.0 to +0.0) | 0/40 (-0.0 to +0.0) | 0 (-1.9 to +0.0) | 0 (-0.1 to +0.7) |
| D99 | 30/40 (-100.0 to +6.3) | 27/40 (-100.0 to +22.9) | 0/40 (-1.1 to +2.4) | 0/40 (-2.0 to +1.4) | 0/40 (-1.7 to +1.7) | 20 (-1.4 to +7.5) | 3 (-1.8 to +5.2) |
| D95 | 28/40 (-100.0 to +3.7) | 22/40 (-6.8 to +27.6) | 8/40 (-3.7 to +5.9) | 0/40 (-1.2 to +2.0) | 0/40 (-1.2 to +2.0) | 12 (-6.6 to +5.2) | 2 (-0.8 to +3.9) |
| D5 | 14/40 (-20.3 to -0.2) | 6/40 (-3.3 to +4.8) | 0/40 (-1.0 to +1.1) | 0/40 (-0.3 to +0.4) | 0/40 (-0.3 to +0.4) | 0 (-1.8 to +1.0) | 0 (-0.4 to +0.8) |
| D1 | 18/40 (-21.2 to +0.1) | 6/40 (-3.6 to +4.3) | 0/40 (-0.8 to +0.3) | 0/40 (-0.2 to +0.4) | 0/40 (-0.3 to +0.3) | 0 (-0.9 to +2.2) | 0 (-0.4 to +0.4) |
| D0.03cc | 18/40 (-21.2 to +0.0) | 4/40 (-3.7 to +4.7) | 0/40 (-0.8 to +0.2) | 0/40 (-0.4 to +0.4) | 0/40 (-0.4 to +0.2) | 0 (-0.9 to +1.3) | 0 (-0.4 to +0.3) |
| **All** | **153/340** | **106/340** | **44/340** | **20/340** | **4/340** | **52** | **5** |
| **Without Dmin, Dmax** | **119/260** | **76/260** | **8/260** | **0/260** | **0/260** | **32** | **5** |

### Test 2: contours and dose grid both 1, 2 or 3 mm, aligned

Cells: parameters more than 3 % from the analytic value / parameters scored (lowest to highest % difference). Differences are relative to the local analytic value, as published, so the low-dose parameters (Dmin, D99, D95) are amplified. The paper columns are its Table II.

| Parameter | dicompyler | dicompyler-ss | mask | mask-ss | polygon | Pinnacle3 (paper) | PlanIQ (paper) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| V | 5/15 (-22.4 to -0.1) | 1/15 (-3.9 to +2.9) | 1/15 (-6.1 to +0.6) | 1/15 (-6.1 to +0.6) | 1/15 (-4.6 to +0.6) | 0 (-2.8 to +2.1) | 1 (-4.2 to +0.6) |
| Dmin | 24/30 (+0.0 to +180.0) | 30/30 (+14.3 to +180.0) | 30/30 (+7.5 to +180.0) | 17/30 (+2.5 to +124.6) | 13/30 (+2.5 to +4.6) | 30 (-7.5 to +60.0) | 0 (+0.0 to +0.0) |
| Dmax | 26/30 (-15.3 to -1.8) | 18/30 (-15.3 to +4.0) | 13/30 (-15.3 to -1.1) | 9/30 (-10.6 to -0.4) | 0/30 (-0.4 to +0.0) | 10 (-5.1 to +1.1) | 0 (+0.0 to +0.0) |
| Dmean | 5/30 (-9.4 to +0.0) | 11/30 (-0.1 to +9.4) | 0/30 (-0.3 to +0.1) | 0/30 (-0.3 to +0.1) | 0/30 (-0.2 to +0.0) | 0 (-1.9 to +0.0) | 0 (+0.0 to +0.0) |
| D99 | 27/30 (-100.0 to -0.2) | 29/30 (-100.0 to +22.9) | 16/30 (-7.6 to +46.3) | 5/30 (-3.4 to +22.2) | 5/30 (-3.4 to +22.2) | 18 (-4.2 to +44.4) | 11 (-4.2 to +22.3) |
| D95 | 27/30 (-100.0 to -0.1) | 28/30 (-100.0 to +27.5) | 20/30 (-15.6 to +10.5) | 2/30 (-2.1 to +6.9) | 2/30 (-2.1 to +6.9) | 19 (-7.8 to +19.5) | 4 (-2.9 to +7.0) |
| D5 | 23/30 (-20.3 to -0.9) | 18/30 (-14.3 to +5.6) | 3/30 (-3.5 to +5.5) | 0/30 (-1.7 to +0.5) | 0/30 (-1.7 to +0.5) | 2 (-3.6 to +5.3) | 0 (-1.7 to +0.5) |
| D1 | 26/30 (-21.2 to -0.8) | 17/30 (-19.1 to +5.3) | 7/30 (-8.1 to +1.6) | 1/30 (-3.9 to +0.8) | 1/30 (-3.9 to +0.8) | 7 (-8.1 to +2.6) | 1 (-3.9 to +0.8) |
| D0.03cc | 27/30 (-21.2 to -0.4) | 17/30 (-20.5 to +4.7) | 9/30 (-9.6 to +0.5) | 1/30 (-4.6 to +1.1) | 1/30 (-4.6 to +1.1) | 7 (-7.8 to +0.9) | 1 (-4.6 to +0.9) |
| **All** | **190/255** | **169/255** | **99/255** | **36/255** | **23/255** | **93** | **18** |
| **Without Dmin, Dmax** | **140/195** | **121/195** | **56/195** | **10/195** | **10/195** | **53** | **18** |

### Test 2, shifted half a dose voxel off the grid

Cells: parameters more than 3 % from the analytic value / parameters scored (lowest to highest % difference). Differences are relative to the local analytic value, as published, so the low-dose parameters (Dmin, D99, D95) are amplified.

| Parameter | dicompyler | dicompyler-ss | mask | mask-ss | polygon |
| --- | ---: | ---: | ---: | ---: | ---: |
| V | 14/30 (-25.4 to +3.5) | 3/30 (-3.9 to +3.1) | 1/30 (-4.0 to +2.6) | 1/30 (-4.0 to +2.6) | 2/30 (-4.6 to +0.6) |
| Dmin | 27/30 (+0.0 to +300.0) | 30/30 (+14.3 to +180.0) | 30/30 (+7.5 to +180.0) | 15/30 (+2.5 to +124.6) | 13/30 (+2.5 to +4.6) |
| Dmax | 28/30 (-15.3 to -1.8) | 18/30 (-15.3 to +4.0) | 13/30 (-15.3 to -1.1) | 7/30 (-10.6 to -0.4) | 0/30 (-0.4 to +0.0) |
| Dmean | 4/30 (-9.4 to +1.2) | 11/30 (-0.2 to +9.4) | 0/30 (-0.1 to +0.1) | 0/30 (-0.1 to +0.1) | 0/30 (-0.2 to +0.0) |
| D99 | 27/30 (-100.0 to +9.1) | 29/30 (-100.0 to +22.9) | 16/30 (-7.6 to +46.3) | 5/30 (-3.4 to +17.4) | 5/30 (-3.4 to +22.2) |
| D95 | 25/30 (-100.0 to +1.2) | 28/30 (-100.0 to +27.5) | 20/30 (-7.0 to +20.5) | 3/30 (-2.1 to +6.9) | 2/30 (-2.1 to +6.9) |
| D5 | 24/30 (-20.3 to -0.9) | 18/30 (-14.3 to +5.6) | 3/30 (-7.2 to +1.3) | 0/30 (-1.7 to +0.5) | 0/30 (-1.7 to +0.5) |
| D1 | 28/30 (-21.2 to -2.2) | 17/30 (-19.1 to +5.3) | 7/30 (-8.1 to +1.6) | 1/30 (-3.0 to +0.8) | 1/30 (-3.9 to +0.8) |
| D0.03cc | 29/30 (-21.2 to -0.7) | 17/30 (-20.5 to +4.7) | 8/30 (-7.7 to +1.2) | 0/30 (-2.8 to +1.1) | 1/30 (-4.6 to +1.1) |
| **All** | **206/270** | **171/270** | **98/270** | **32/270** | **24/270** |
| **Without Dmin, Dmax** | **151/210** | **123/210** | **55/210** | **10/210** | **11/210** |

### The same at 2 %

| Parameters beyond 2 %, without Dmin, Dmax | dicompyler | dicompyler-ss | mask | mask-ss | polygon |
| --- | ---: | ---: | ---: | ---: | ---: |
| Test 1 | 155/260 | 122/260 | 16/260 | 8/260 | 4/260 |
| Test 2 | 155/195 | 143/195 | 66/195 | 22/195 | 20/195 |
| Test 2, shifted half a dose voxel off the grid | 163/210 | 146/210 | 69/210 | 26/210 | 22/210 |

### Test 3: volume error along the whole curve

The volume error at every 0.1 Gy from 0 to 30 Gy (301 points), as % of the structure's analytic volume, on the Test 2 data at 1 and 3 mm. Each cell averages the five shapes' lowest, highest and mean error and SD, like the paper's Table III: lowest to highest (mean ± SD).

| Data | dicompyler | dicompyler-ss | mask | mask-ss | polygon | Pinnacle3 (paper) | PlanIQ (paper) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 mm, superior-inferior | -5.4 to +1.8 (-1.8 ± 1.7) | -3.5 to +2.9 (-0.1 ± 1.2) | -3.7 to +2.8 (-0.2 ± 1.2) | -0.9 to +0.2 (-0.2 ± 0.3) | -0.8 to +0.0 (-0.3 ± 0.3) | -4.4 to +2.2 (-0.3 ± 1.3) | -0.4 to +0.4 (+0.0 ± 0.2) |
| 1 mm, anterior-posterior | -5.6 to +1.5 (-2.2 ± 1.5) | -0.1 to +4.2 (+1.7 ± 1.2) | -1.7 to +2.2 (+0.1 ± 0.8) | -0.4 to +0.8 (+0.1 ± 0.3) | -0.2 to +0.7 (+0.1 ± 0.3) | -3.5 to +0.8 (-0.8 ± 1.0) | -0.4 to +0.6 (+0.1 ± 0.2) |
| 3 mm, superior-inferior | -14.7 to +6.3 (-4.6 ± 4.7) | -10.5 to +9.4 (-0.2 ± 3.7) | -10.9 to +8.8 (-0.9 ± 3.7) | -2.1 to +0.8 (-0.7 ± 0.7) | -1.9 to +0.6 (-0.6 ± 0.6) | -11.2 to +7.9 (-0.7 ± 3.5) | -1.5 to +0.6 (-0.5 ± 0.5) |
| 3 mm, anterior-posterior | -17.0 to +4.3 (-6.8 ± 4.5) | -1.1 to +12.7 (+4.9 ± 3.8) | -3.6 to +1.4 (-0.9 ± 1.1) | -2.3 to +0.2 (-0.9 ± 0.8) | -2.0 to +0.2 (-0.8 ± 0.6) | -4.1 to +0.8 (-1.2 ± 1.1) | -1.5 to +0.8 (-0.3 ± 0.6) |

## Disc phantoms

576 cases: 3 shapes × 4 radii × 2 grids × 8 random placements × 3 dose directions. The dose is 50 Gy at the centre with a 1 Gy/mm gradient, so an error in Gy is also the boundary shift, in mm, that would cause it.

### Every case

Mean / largest absolute error over all cases. Doses in Gy; volume in %.

| Metric | dicompyler | dicompyler-ss | mask | mask-ss | polygon |
| --- | ---: | ---: | ---: | ---: | ---: |
| V (%) | 7.15 / 69.77 | 1.57 / 20.49 | 1.56 / 16.45 | 1.56 / 16.45 | 0.00 / 0.00 |
| Dmin | 1.40 / 3.80 | 0.79 / 2.00 | 0.98 / 1.72 | 0.18 / 0.60 | 0.09 / 0.15 |
| Dmax | 1.35 / 2.90 | 1.56 / 2.68 | 0.99 / 1.75 | 0.19 / 0.59 | 0.09 / 0.15 |
| Dmean | 0.14 / 1.30 | 0.67 / 1.39 | 0.04 / 0.41 | 0.04 / 0.41 | 0.00 / 0.00 |
| D99 | 32.22 / 48.53 | 22.47 / 48.53 | 0.53 / 1.47 | 0.09 / 0.48 | 0.03 / 0.10 |
| D95 | 22.16 / 48.65 | 11.98 / 48.65 | 0.39 / 1.35 | 0.07 / 0.40 | 0.04 / 0.11 |
| D5 | 3.51 / 52.10 | 2.56 / 51.35 | 0.37 / 1.35 | 0.07 / 0.50 | 0.04 / 0.11 |
| D1 | 4.18 / 52.36 | 2.93 / 51.47 | 0.54 / 1.47 | 0.09 / 0.46 | 0.03 / 0.10 |
| D0.03cc | 4.76 / 50.99 | 2.74 / 49.97 | 0.44 / 1.48 | 0.10 / 0.64 | 0.03 / 0.10 |
| Worst of Dmean-D0.03cc | 32.96 / 52.36 | 22.90 / 51.47 | 0.69 / 1.48 | 0.15 / 0.64 | 0.06 / 0.11 |
| Whole curve, largest ΔV (%) | 17.41 / 106.14 | 15.66 / 53.62 | 9.24 / 53.38 | 2.56 / 19.70 | 0.85 / 3.82 |

### By size

Median / largest of each case's worst error among Dmean, D99, D95, D5, D1 and D0.03cc (Gy), then the volume error (%).

| Size | dicompyler | dicompyler-ss | mask | mask-ss | polygon |
| --- | ---: | ---: | ---: | ---: | ---: |
| R = 2.5 mm, worst dose | 47.99 / 52.36 | 47.64 / 51.47 | 0.82 / 1.47 | 0.16 / 0.64 | 0.06 / 0.10 |
| R = 5 mm, worst dose | 45.56 / 46.50 | 23.21 / 46.50 | 0.52 / 1.41 | 0.10 / 0.43 | 0.06 / 0.10 |
| R = 10 mm, worst dose | 40.55 / 42.52 | 1.72 / 41.90 | 0.46 / 1.40 | 0.09 / 0.39 | 0.07 / 0.10 |
| R = 20 mm, worst dose | 3.30 / 31.91 | 1.86 / 31.91 | 0.46 / 1.48 | 0.09 / 0.48 | 0.08 / 0.11 |
| R = 2.5 mm, volume (%) | 18.51 / 69.77 | 2.52 / 20.49 | 3.71 / 16.45 | 3.71 / 16.45 | 0.00 / 0.00 |
| R = 5 mm, volume (%) | 4.51 / 20.42 | 0.32 / 1.79 | 1.15 / 2.77 | 1.15 / 2.77 | 0.00 / 0.00 |
| R = 10 mm, volume (%) | 1.54 / 8.33 | 0.20 / 0.81 | 0.65 / 2.49 | 0.65 / 2.49 | 0.00 / 0.00 |
| R = 20 mm, volume (%) | 0.34 / 2.52 | 0.05 / 0.48 | 0.12 / 0.57 | 0.12 / 0.57 | 0.00 / 0.00 |

### By dose direction and grid

Median / largest worst dose error (Gy).

| Subset | dicompyler | dicompyler-ss | mask | mask-ss | polygon |
| --- | ---: | ---: | ---: | ---: | ---: |
| in-plane (x) | 41.60 / 52.36 | 2.00 / 48.39 | 0.47 / 0.86 | 0.23 / 0.64 | 0.08 / 0.10 |
| through-plane (z) | 43.43 / 51.47 | 43.12 / 51.47 | 1.08 / 1.48 | 0.09 / 0.50 | 0.09 / 0.11 |
| oblique (45° x-z) | 2.63 / 52.35 | 1.33 / 48.21 | 0.38 / 1.23 | 0.08 / 0.58 | 0.01 / 0.05 |
| CT 0.98 × 0.98 × 2 mm, dose 2 mm | 41.90 / 52.36 | 2.86 / 48.20 | 0.51 / 1.04 | 0.11 / 0.50 | 0.07 / 0.11 |
| CT 0.98 × 0.98 × 3 mm, dose 2.5 mm | 43.01 / 52.36 | 29.42 / 51.47 | 0.57 / 1.48 | 0.10 / 0.64 | 0.06 / 0.10 |
| sphere | 41.34 / 52.34 | 2.02 / 51.47 | 0.51 / 1.47 | 0.10 / 0.50 | 0.07 / 0.11 |
| cylinder | 41.95 / 52.34 | 30.89 / 51.47 | 0.54 / 1.48 | 0.10 / 0.46 | 0.07 / 0.11 |
| ring | 41.90 / 52.36 | 30.40 / 51.47 | 0.59 / 1.47 | 0.11 / 0.64 | 0.07 / 0.11 |

### Time per structure

Median seconds for one structure and one dose, from nothing.

| Size | dicompyler | dicompyler-ss | mask | mask-ss | polygon |
| --- | ---: | ---: | ---: | ---: | ---: |
| R = 2.5 mm | 0.01 | 0.01 | 0.01 | 0.00 | 0.01 |
| R = 5 mm | 0.02 | 0.05 | 0.01 | 0.01 | 0.02 |
| R = 10 mm | 0.10 | 0.27 | 0.01 | 0.07 | 0.08 |
| R = 20 mm | 0.39 | 1.84 | 0.03 | 0.52 | 0.49 |

## Sub-sample spacing: accuracy against cost

The two sub-sampled methods at three spacings, on the 30 Test 2 datasets (Dmin and Dmax set aside). The sample count is per structure, and so is the time: building the samples once plus one dose lookup.

| Method | > 3 % | > 2 % | Worst |%| | Samples | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| mask-ss @ 1 mm | 36/195 | 46/195 | 25.4 | 20,088 | 0.04 |
| mask-ss @ 0.5 mm | 13/195 | 23/195 | 19.4 | 449,280 | 0.09 |
| mask-ss @ 0.25 mm | 10/195 | 22/195 | 22.2 | 808,704 | 0.12 |
| polygon @ 1 mm | 35/195 | 46/195 | 25.4 | 21,572 | 0.03 |
| polygon @ 0.5 mm | 14/195 | 22/195 | 19.4 | 459,900 | 0.07 |
| polygon @ 0.25 mm | 10/195 | 20/195 | 22.2 | 827,820 | 0.10 |

## Large structures: time against accuracy

One sphere or cylinder per size (the cylinders as tall as they are wide) on the tender cohort's grid: CT 0.98 × 0.98 × 2 mm, dose 2 mm. The dose is 100 Gy at the centre rising 0.25 Gy/mm obliquely, gentler than elsewhere so it stays positive across 20 cm.

### Seconds for one structure against one dose

From reading the contours to the statistics, single-threaded, on the machine named under Environment; a run under 5 s is the faster of two. In brackets: dose samples taken, in millions.

| Structure | dicompyler | mask | mask-ss @ 1 mm | mask-ss @ 0.5 mm | mask-ss @ 0.25 mm | polygon @ 1 mm | polygon @ 0.5 mm | polygon @ 0.25 mm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| sphere, R 10 mm (4 cc) | 0.08 | 0.01 (0.0 M) | 0.01 (0.0 M) | 0.02 (0.1 M) | 0.06 (0.5 M) | 0.02 (0.0 M) | 0.03 (0.1 M) | 0.06 (0.5 M) |
| sphere, R 20 mm (33 cc) | 0.39 | 0.03 (0.0 M) | 0.03 (0.1 M) | 0.10 (0.8 M) | 0.45 (3.9 M) | 0.05 (0.1 M) | 0.12 (0.8 M) | 0.38 (4.0 M) |
| sphere, R 40 mm (268 cc) | 1.82 | 0.06 (0.1 M) | 0.09 (0.4 M) | 0.71 (6.3 M) | 3.57 (31.6 M) | 0.12 (0.4 M) | 0.68 (6.4 M) | 3.19 (31.8 M) |
| sphere, R 60 mm (905 cc) | 5.47 | 0.11 (0.5 M) | 0.20 (1.4 M) | 2.42 (21.3 M) | 13.08 (106.6 M) | 0.26 (1.5 M) | 2.26 (21.5 M) | 11.58 (107.1 M) |
| cylinder, R 80 mm (3,177 cc) | 11.56 | 0.25 (1.7 M) | 0.59 (5.0 M) | 9.26 (74.9 M) | 46.57 (374.5 M) | 0.64 (5.1 M) | 8.87 (75.3 M) | 45.32 (375.6 M) |
| cylinder, R 100 mm (6,220 cc) | 20.75 | 0.41 (3.3 M) | 1.16 (9.8 M) | 18.36 (146.6 M) | 91.31 (732.9 M) | 1.16 (9.9 M) | 18.74 (147.2 M) | 94.29 (735.0 M) |

### Accuracy

The worst error among Dmean, D99, D95, D5, D1 and D0.03cc, as the boundary shift that would cause it (Gy ÷ 0.25), then the volume error.

| Structure | dicompyler | mask | mask-ss @ 1 mm | mask-ss @ 0.5 mm | mask-ss @ 0.25 mm | polygon @ 1 mm | polygon @ 0.5 mm | polygon @ 0.25 mm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| sphere, R 10 mm (4 cc) | 1.42 mm / +0.32 % | 0.36 mm / +0.17 % | 0.09 mm / +0.17 % | 0.07 mm / +0.17 % | 0.07 mm / +0.17 % | 0.09 mm / -0.00 % | 0.01 mm / -0.00 % | 0.00 mm / -0.00 % |
| sphere, R 20 mm (33 cc) | 1.23 mm / -0.15 % | 0.20 mm / -0.04 % | 0.07 mm / -0.04 % | 0.02 mm / -0.04 % | 0.03 mm / -0.04 % | 0.08 mm / -0.00 % | 0.01 mm / -0.00 % | 0.01 mm / -0.00 % |
| sphere, R 40 mm (268 cc) | 1.34 mm / -0.00 % | 0.22 mm / +0.00 % | 0.19 mm / +0.00 % | 0.04 mm / +0.00 % | 0.04 mm / +0.00 % | 0.02 mm / -0.00 % | 0.00 mm / -0.00 % | 0.00 mm / -0.00 % |
| sphere, R 60 mm (905 cc) | 1.71 mm / -0.02 % | 0.50 mm / +0.01 % | 0.09 mm / +0.01 % | 0.03 mm / +0.01 % | 0.03 mm / +0.01 % | 0.09 mm / -0.00 % | 0.01 mm / -0.00 % | 0.00 mm / -0.00 % |
| cylinder, R 80 mm (3,177 cc) | 1.82 mm / -0.07 % | 0.21 mm / +0.03 % | 0.02 mm / +0.03 % | 0.03 mm / +0.03 % | 0.05 mm / +0.03 % | 0.01 mm / +0.00 % | 0.02 mm / +0.00 % | 0.01 mm / +0.00 % |
| cylinder, R 100 mm (6,220 cc) | 1.16 mm / +0.01 % | 0.15 mm / -0.03 % | 0.15 mm / -0.03 % | 0.10 mm / -0.03 % | 0.12 mm / -0.03 % | 0.15 mm / +0.00 % | 0.05 mm / +0.00 % | 0.00 mm / +0.00 % |

## dicompyler with its lookup corrected

dicompyler's own histogram, with D*x* read the way the other methods read it (the lowest dose the hottest *x* receives, to the 1 cGy bin). The volume, Dmin, Dmax and Dmean are unchanged. This separates what dicompyler's lookup costs (next section) from what its sampling costs, and is what keeping dicompyler with only the lookup replaced would give.

|  | dicompyler | dicompyler (lookup corrected) | dicompyler-ss | dicompyler-ss (lookup corrected) | mask-ss | polygon |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Nelms Test 1: beyond 3 %, without Dmin, Dmax | 119/260 | 83/260 | 76/260 | 81/260 | 0/260 | 0/260 |
| Nelms Test 2: beyond 3 %, without Dmin, Dmax | 140/195 | 103/195 | 121/195 | 112/195 | 10/195 | 10/195 |
| Nelms Test 2, shifted half a dose voxel off the grid: beyond 3 %, without Dmin, Dmax | 151/210 | 114/210 | 123/210 | 114/210 | 10/210 | 11/210 |
| Disc phantoms: worst of Dmean-D0.03cc, median / largest (Gy) | 41.90 / 52.36 | 1.12 / 3.66 | 29.42 / 51.47 | 1.26 / 2.04 | 0.10 / 0.64 | 0.07 / 0.11 |

## Doses reported as 0 Gy

dicompyler-core's `dose_constraint` answers D*x* with the first dose bin whose cumulative volume is nearest *x*. Every bin from 0 Gy up to Dmin holds 100 % of the volume, so when no later bin is nearer *x* than 100 % is, the answer is the first bin: 0 Gy. For D99 that happens once the coldest 1 cGy bin holds more than 2 % of the volume, for D95 more than 10 %; a structure whose dose falls in a single bin gets 0 Gy for every D*x*, and a D*x*cc larger than the volume dicompyler found gets 0 Gy too. Few dose samples, or whole planes sharing one dose, are enough.

| Benchmark | Method | Cases | Statistics at 0 Gy |
| --- | --- | ---: | --- |
| Disc phantoms | dicompyler | 428/576 | D99 428, D95 271, D0.03cc 31, D5 20, D1 20 |
| Disc phantoms | dicompyler-ss | 289/576 | D99 289, D95 141, D5 10, D1 10, D0.03cc 10 |
| Nelms | dicompyler | 49/100 | D99 49, D95 9 |
| Nelms | dicompyler-ss | 29/100 | D99 29, D95 4 |

## Failures

No method failed on any case.

## Checks on the harness

- Closed-form disc truth against brute-force integration over the written polygons (96-point Gauss-Legendre through each slab, exact polygon clipping), largest difference as a share of the structure's volume: 5.2e-08.
- Trilinear dose sampling here against SimpleITK's resampling, which the consensus DVH uses today, at every voxel of a Nelms sphere: largest difference 7.1e-15 Gy.
- Polygon's sub-cell coverage and centroids, found by accumulation, against clipping every sub-cell with shapely (Nelms and disc outlines, 1-5 sub-cells per voxel edge): 3,123,651 sub-cells, the same cells covered; largest difference in the share covered 4e-13, in the centroid 7e-09 voxel.
- The dicompyler rows equal `compute_dvh_metrics` on every case (checked per case; a mismatch would appear under Failures).

## Environment

AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD, 16 logical cores; Python 3.12.3 on Windows 10; numpy 1.26.4, scipy 1.13.1, shapely 2.0.6, SimpleITK 2.3.1, pydicom 2.4.4, dicompyler-core 0.5.6. Sub-sample spacing 0.25 mm; 8 placements per disc phantom; seed 20260924.

## Appendix: Test 3 by dataset

Lowest to highest volume error (%).

| Dataset | dicompyler | dicompyler-ss | mask | mask-ss | polygon |
| --- | ---: | ---: | ---: | ---: | ---: |
| Cone_10_0, AP | -3.6 to +3.6 | +0.0 to +4.7 | -1.5 to +2.4 | -0.1 to +0.8 | -0.0 to +0.8 |
| Cylinder_10_0, AP | -3.8 to +1.3 | +0.0 to +3.2 | -0.8 to +1.9 | -0.0 to +0.9 | -0.0 to +0.5 |
| RtCone_10_0, AP | -10.8 to +0.0 | +0.0 to +7.2 | -2.5 to +3.6 | -0.2 to +1.2 | -0.0 to +1.2 |
| RtCylinder_10_0, AP | -6.2 to +0.0 | -0.4 to +2.5 | -2.1 to +1.2 | -1.3 to +0.4 | -0.9 to +0.4 |
| Sphere_10_0, AP | -3.5 to +2.5 | -0.2 to +3.5 | -1.4 to +1.9 | -0.2 to +0.6 | -0.2 to +0.6 |
| Cone_10_0, SI | -5.9 to +4.4 | -5.9 to +4.7 | -5.9 to +4.8 | -1.2 to +0.2 | -1.2 to +0.1 |
| Cylinder_10_0, SI | -4.8 to +1.5 | -2.0 to +1.7 | -2.0 to +2.2 | -0.4 to +0.6 | -0.4 to +0.0 |
| RtCone_10_0, SI | -8.1 to +0.3 | -3.5 to +3.7 | -3.9 to +3.2 | -0.8 to +0.2 | -0.8 to +0.1 |
| RtCylinder_10_0, SI | -4.1 to +1.0 | -2.9 to +2.0 | -3.4 to +1.5 | -1.3 to +0.0 | -1.0 to +0.0 |
| Sphere_10_0, SI | -4.1 to +1.9 | -3.2 to +2.4 | -3.2 to +2.5 | -0.7 to +0.1 | -0.7 to +0.0 |
| Cone_30_0, AP | -13.6 to +10.2 | +0.0 to +14.5 | -2.1 to +1.8 | -0.6 to +0.5 | -0.4 to +0.7 |
| Cylinder_30_0, AP | -12.2 to +5.2 | +0.0 to +10.3 | -1.4 to +1.4 | -0.3 to +0.6 | -0.5 to +0.0 |
| RtCone_30_0, AP | -33.0 to +0.0 | +0.0 to +20.8 | -3.8 to +2.1 | -1.7 to +0.0 | -1.6 to +0.0 |
| RtCylinder_30_0, AP | -14.7 to +0.0 | -3.9 to +7.3 | -7.2 to +0.7 | -6.5 to +0.0 | -5.0 to +0.0 |
| Sphere_30_0, AP | -11.7 to +6.2 | -1.8 to +10.4 | -3.5 to +1.2 | -2.4 to +0.1 | -2.2 to +0.1 |
| Cone_30_0, SI | -15.8 to +12.7 | -15.8 to +14.9 | -15.8 to +14.9 | -1.1 to +2.1 | -1.2 to +1.9 |
| Cylinder_30_0, SI | -11.3 to +4.5 | -5.6 to +5.7 | -5.6 to +5.8 | -0.4 to +1.0 | -0.4 to +0.4 |
| RtCone_30_0, SI | -23.9 to +0.9 | -10.8 to +12.9 | -12.0 to +11.1 | -1.2 to +1.0 | -1.3 to +0.9 |
| RtCylinder_30_0, SI | -10.2 to +7.2 | -10.1 to +5.7 | -11.0 to +4.4 | -6.1 to +0.0 | -4.6 to +0.0 |
| Sphere_30_0, SI | -12.0 to +6.6 | -10.3 to +7.9 | -10.3 to +7.9 | -1.9 to +0.0 | -1.7 to +0.0 |
