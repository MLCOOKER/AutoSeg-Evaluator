# Supplementary 1 - DVH Validation Report

AutoSeg Evaluator 2.6.1, commit 9dc078f · computed 2026-10-02 by `scripts/validate_dvh_methods.py` and `scripts/validate_dvh_mask_vs_polygon.py`. Every table behind this report is in its [full results](Supplementary_1_DVH_Validation_Report_Full_Results.md).

## Aim

A dose-volume histogram (DVH) shows how much of a structure receives each dose, and AutoSeg Evaluator compares contours dosimetrically through statistics read from it, such as the mean dose (Dmean) or the dose received by the hottest 95 % of the structure (D95). AutoSeg Evaluator versions 1 and 2 computed DVHs with dicompyler-core 0.5.6 [2]. Version 3 computes them with its own method, which integrates the dose over the contour polygons directly. This validation asks two questions:

1. **Is the v3 method accurate?** It is scored against DVHs whose true values are known exactly, beside v1-v2's dicompyler-core, the other methods considered, and two commercial systems.
2. **What does a DVH from a binary mask cost?** A STAPLE consensus [3] exists only as a binary mask, with no contours, so its DVH has to come from its voxels. How far is a mask's DVH from the DVH of the same structure's contours? And when contours are compared with a consensus, should each contour's DVH come from a mask too, so that both are taken the same way?

## Method

### How AutoSeg v3 computes a DVH

Each contour is treated as a slab one CT slice thick, centred on its plane. The slab is divided into sub-cells aligned with the CT voxels, at the finest spacing of 0.25, 0.5 or 1 mm that keeps the structure within ten million samples, so organs at risk are sampled at 0.25 mm and only the largest structures more coarsely. Each sub-cell is weighted by the exact area of the contour inside it, and the dose is interpolated trilinearly at the centre of that area. The samples are collected in a dose histogram of 1 mGy bins. Dmin, Dmax and Dmean are kept exactly, and D*x*, the lowest dose received by the hottest *x* of the volume, is read to the centre of its bin. A structure that exists only as a mask is sampled over its voxels by the same spacing rule.

### The methods compared

| Method | How it samples the structure |
| --- | --- |
| v1-v2: dicompyler-core | Tests whether each dose-grid point in each contour plane lies inside the contour; each point stands for a dose voxel's area × the gap between planes |
| dicompyler-core, supersampled | The same, on a grid a quarter of the dose pixel |
| Voxel mask | The binary mask AutoSeg uses for its 3D metrics, with the dose at each CT voxel's centre |
| Voxel mask, sub-sampled | The same voxels, each sub-sampled every 0.25 mm |
| Contour polygons | Sub-cells every 0.25 mm, weighted by the exact area of contour inside them |
| **AutoSeg v3** | Contour polygons, at 0.25, 0.5 or 1 mm by size, as above |

### Data

**Nelms et al. analytic datasets [1].** Downloaded from the supplementary material of Nelms et al. (2015). Five shapes, each 24 mm across: a sphere, and a cylinder and a cone in two orientations, with the long axis running head to foot (so each slice is a circle) or front to back (so the slices are rectangles, or hyperbolic sections for the cone). The shapes were contoured every 0.2, 1, 2 and 3 mm on CTs with 0.6 mm pixels, and placed in linear dose fields rising 1 Gy/mm (16 Gy at the structure's centre) from front to back or from head to foot, on dose grids of 0.4 × 0.2 × 0.4 mm and of 1, 2 and 3 mm cubes. Copies shifted half a dose voxel off the grid are included. The true value of every statistic is known in closed form, with each shape extended half a slice beyond its end contours, so a method is also charged for how it fills the gap between contour planes. Volumes are 3.6 to 12.2 cc. The 100 structure and dose combinations make up the paper's three tests:

- *Test 1:* contours every 0.2 mm, dose grids of 0.4-3 mm.
- *Test 2:* contour spacing and dose grid both 1, 2 or 3 mm; and the same with the shifted copies.
- *Test 3:* the volume error along the whole DVH, every 0.1 Gy from 0 to 30 Gy, on the Test 2 data at 1 and 3 mm.

The paper's own results for two commercial systems, Pinnacle³ (v9.8) and PlanIQ (v2.1), on the same data are quoted beside AutoSeg's.

**Disc phantoms, built by the authors.** 576 cases: stacks of circular contours forming a sphere, a cylinder or a ring with a hole, of radius 2.5, 5, 10 and 20 mm, each placed at 8 random sub-voxel positions on two clinical CT grids (CT 0.98 × 0.98 × 2 mm, dose 2 mm and CT 0.98 × 0.98 × 3 mm, dose 2.5 mm), in linear dose fields rising 1 Gy/mm in 3 directions (within the slice plane, perpendicular to the slices, and at 45° between them). Each contour stands for a slab whose DVH has a closed form, so the truth is exact for the contours as drawn. With a gradient of 1 Gy/mm, an error in Gy is also the shift of the structure's boundary, in mm, that would cause it. The smallest discs are comparable to a cochlea.

**Large structures.** One sphere or cylinder per size, from 4 to 6,220 cc, each timed on one machine, to weigh accuracy against computing time.

**Mask against contours.** Every Nelms structure and dose was taken both ways: from its contours, and from the binary mask AutoSeg makes from those contours. That mask is what a STAPLE consensus of identical raters returns, which was checked. Masks were made on the Nelms CT and again with its pixels enlarged to 1.07 mm and 1.37 mm, keeping the slices. These are the most common and the coarsest in-plane pixel sizes among the planning CTs of the authors' clinical cohort. A mask's error depends on where a structure falls among the voxels, so each coarser grid was laid at 4 sub-pixel offsets.

To decide how a contour should be compared with a consensus, each shifted Nelms copy was treated as a test contour and its unshifted twin as the reference, on the same dose grid, CT grid and offset. The copies come in two kinds:

- copies shifted across the dose gradient, whose true dose difference from the twin is zero; and
- copies shifted 0.5-1.5 mm along it, whose true difference is 0.5-1.5 Gy, known exactly because the field is linear.

The DVH difference between test and reference was taken three ways: the test from its contours and the reference from its mask (AutoSeg's choice); both from masks ("like for like"); and both from contours, the best case, which only a reference with contours allows. Each was scored against the true difference.

**Criterion.** As in Nelms et al., a statistic fails when it is more than 3 % from its analytic value. The statistics scored are the volume, Dmean, D99, D95, D5, D1 and D0.03cc. Dmin and Dmax are set aside, as the paper set them aside, being the least clinically relevant.

## Results

### The DVH method against analytic truth

Statistics more than 3 % from the analytic value in Tests 1 and 2, and the range of the volume error along the whole DVH in Test 3:

|  | AutoSeg v3 | AutoSeg v1-v2 (dicompyler-core) | Pinnacle³ [1] | PlanIQ [1] |
| --- | ---: | ---: | ---: | ---: |
| Test 1 | 0/260 | 119/260 | 32/260 | 5/260 |
| Test 2 | 10/195 | 140/195 | 53/195 | 18/195 |
| Test 2, shifted | 11/210 | 151/210 | not reported | not reported |
| Test 3: volume error (%) | -2.0 to +0.7 | -17.0 to +6.3 | -11.2 to +7.9 | -1.5 to +0.8 |

AutoSeg v1-v2 reported D99 as 0 Gy in 49 of the 100 Nelms cases, where the true D99 is well above it: dicompyler-core reads D*x* from the first dose bin whenever no later bin is nearer *x*, which happens once the coldest bin holds a few percent of the volume.

Every method considered, on the same data. The disc phantom column is each case's worst error among Dmean, D99, D95, D5, D1 and D0.03cc, in Gy (equivalently mm): median / largest over the 576 cases.

| Method | Test 1 | Test 2 | Test 2, shifted | Disc phantoms (Gy) |
| --- | ---: | ---: | ---: | ---: |
| v1-v2: dicompyler-core | 119/260 | 140/195 | 151/210 | 41.90 / 52.36 |
| dicompyler-core, D*x* read correctly | 83/260 | 103/195 | 114/210 | 1.12 / 3.66 |
| dicompyler-core, supersampled | 76/260 | 121/195 | 123/210 | 29.42 / 51.47 |
| Voxel mask | 8/260 | 56/195 | 55/210 | 0.54 / 1.48 |
| Voxel mask, sub-sampled | 0/260 | 10/195 | 10/210 | 0.10 / 0.64 |
| Contour polygons | 0/260 | 10/195 | 11/210 | 0.07 / 0.11 |
| **AutoSeg v3** | 0/260 | 10/195 | 11/210 | 0.07 / 0.11 |

How finely to sample. The contour-polygon method at three sub-cell spacings on Test 2, with the samples and time for one structure against one dose (medians):

| Sub-cell spacing | Test 2 beyond 3 % | Worst error (%) | Samples | Seconds |
| --- | ---: | ---: | ---: | ---: |
| 1 mm | 35/195 | 25.4 | 21,572 | 0.02 |
| 0.5 mm | 14/195 | 19.4 | 459,900 | 0.08 |
| 0.25 mm | 10/195 | 22.2 | 827,820 | 0.12 |

For large structures, the time for one structure against one dose, with AutoSeg's rule and with 0.25 mm throughout, and AutoSeg's worst dose error expressed as the boundary shift that would cause it:

| Structure | AutoSeg v3: spacing | AutoSeg v3: seconds | 0.25 mm throughout: seconds | AutoSeg v3: worst error (mm) |
| --- | ---: | ---: | ---: | ---: |
| sphere, 4 cc | 0.25 mm | 0.09 | 0.09 | 0.003 |
| sphere, 33 cc | 0.25 mm | 0.45 | 0.45 | 0.008 |
| sphere, 268 cc | 0.5 mm | 0.80 | 3.93 | 0.005 |
| sphere, 905 cc | 1 mm | 0.30 | 14.42 | 0.091 |
| cylinder, 3,177 cc | 1 mm | 0.72 | 52.06 | 0.006 |
| cylinder, 6,220 cc | 1 mm | 1.25 | 102.33 | 0.155 |

### DVH from a mask against DVH from the contours

For the same structure, the difference between the DVH from its mask and the DVH from its contours, as % of the analytic value: 95th percentile / largest, over the 100 structure and dose combinations and, on the coarser grids, their 4 offsets.

| CT pixel | Volume | Dmean | D99 | D95 | D5 | D1 | D0.03cc |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.6 mm (Nelms CT) | 1.47 / 3.76 | 0.04 / 0.12 | 3.70 / 4.83 | 0.00 / 2.78 | 0.00 / 0.97 | 0.75 / 0.85 | 0.20 / 3.34 |
| 1.07 mm | 4.36 / 6.63 | 1.01 / 1.88 | 5.32 / 10.14 | 4.35 / 8.28 | 1.56 / 2.33 | 1.55 / 1.69 | 1.56 / 1.69 |
| 1.37 mm | 5.42 / 9.41 | 2.10 / 2.17 | 8.20 / 13.82 | 3.75 / 11.28 | 1.45 / 2.19 | 1.52 / 2.21 | 2.12 / 2.21 |

Statistics more than 3 % from the analytic value, from each path, over every test and offset:

| CT pixel | From the contours | From the mask |
| --- | ---: | ---: |
| 0.6 mm (Nelms CT) | 21/665 (3.2 %) | 20/665 (3.0 %) |
| 1.07 mm | 88/2660 (3.3 %) | 237/2660 (8.9 %) |
| 1.37 mm | 88/2660 (3.3 %) | 261/2660 (9.8 %) |

A test contour against a consensus reference: the error in the measured DVH difference (measured minus true), as % of the reference's analytic value, 95th percentile over the volume and clinical doses. On the 1.37 mm grid, 120 pairs were shifted across the gradient and 60 along it, over the offsets.

| CT pixel | Test contour shifted | Test from contours, reference from mask (AutoSeg) | Both from masks (like for like) | Both from contours (best case) |
| --- | --- | ---: | ---: | ---: |
| 1.07 mm | Across the gradient (true difference 0) | 3.70 % | 2.58 % | 0.00 % |
| 1.07 mm | Along the gradient (true difference 0.5-1.5 Gy) | 4.58 % | 6.71 % | 1.78 % |
| 1.37 mm | Across the gradient (true difference 0) | 3.75 % | 2.80 % | 0.00 % |
| 1.37 mm | Along the gradient (true difference 0.5-1.5 Gy) | 5.43 % | 6.41 % | 2.03 % |

Given three identical masks, STAPLE returned the mask itself for all 50 structures, so a consensus of agreeing raters is exactly the mask scored here.

## Findings

- **The v3 DVH method is accurate.** It had fewer statistics beyond 3 % than either commercial system in both tests (0 and 10, against 5 and 18 for PlanIQ and 32 and 53 for Pinnacle³). Its volume error along the whole DVH (-2.0 to +0.7 %) spans 2.7 percentage points, against 2.3 for PlanIQ (-1.5 to +0.8 %) and 19.1 for Pinnacle³ (-11.2 to +7.9 %).
- **Why dicompyler-core was replaced.** As used in v1-v2 it had 119 and 140 statistics beyond 3 % in Tests 1 and 2, and reported D99 as 0 Gy in 49 of 100 cases. Reading D*x* correctly from its histogram still leaves 83 and 103, because it also samples the dose only once per dose voxel in each contour plane, so correcting it would not have been enough.
- **Why contour polygons rather than a mask.** On the Nelms data the sub-sampled mask had 0, 10 and 10 statistics beyond 3 % in the three tests, against 0, 10 and 11 for AutoSeg v3; on the disc phantoms its worst error reached 0.64 Gy against 0.11 Gy. A mask includes or leaves out whole voxels where a contour cuts through them, which matters most for small structures; the polygons have no such staircase.
- **Why the 0.25, 0.5 or 1 mm rule.** 0.25 mm sub-cells were the most accurate tested on Test 2 (10 statistics beyond 3 %, against 14 at 0.5 mm, 35 at 1 mm), but their number grows with a structure's volume. At 0.25 mm throughout, the largest structure (6,220 cc) took 102 s; the rule sampled it at 1 mm in 1.3 s, with a worst dose error equal to a 0.15 mm boundary shift. Every Nelms and disc structure fell within the cap, so the rule sampled each at 0.25 mm and its results there are those of the finest method.
- **A consensus's DVH carries its mask's error.** For the same structure, the mask's Dmean differed from the contours' by at most 0.12 % on the Nelms CT and 2.17 % on 1.37 mm pixels, where the volume differed by up to 9.4 % and D99 by up to 13.8 %. The share of statistics beyond 3 % rose with the pixel size for the mask (3.0 % → 8.9 % → 9.8 %) but not for the contours (3.2 % → 3.3 % → 3.3 %).
- **So every contour takes its DVH from its contours, and only a consensus from its mask.** Taking a test contour from its own mask as well (like for like) reduced the error only for copies shifted across the gradient, which in effect coincide with the reference. For copies shifted along the gradient, as a contour that genuinely differs from the consensus is, it was worse on every statistic at both clinical grids. With every contour taken from its contours, the consensus's mask error is the same in every source's DVH difference, and cancels when sources are compared with each other. The price is that a contour identical to the consensus differs from it by up to 2.17 % in Dmean on 1.37 mm pixels.
- **Limits.** The Nelms structures are 3.6-12.2 cc. A mask's relative error grows as a structure shrinks against its voxels, so for smaller structures, such as a cochlea or the optic chiasm, a consensus's DVH will differ from contours by more than measured here. The shifts tested are rigid; a test contour differing in shape makes the two masks' errors less alike still. A consensus of raters who disagree is validated in Supplementary 5.

## References

1. Nelms B, Stambaugh C, Hunt D, Tonner B, Zhang G, Feygelman V. Methods, software and datasets to verify DVH calculations against analytical values: twenty years late(r). Med Phys. 2015;42(8):4435-4448.
2. dicompyler-core. GitHub. https://github.com/dicompyler/dicompyler-core
3. Warfield SK, Zou KH, Wells WM. Simultaneous truth and performance level estimation (STAPLE): an algorithm for the validation of image segmentation. IEEE Trans Med Imaging. 2004;23(7):903-921.

## Reproduce

```
python scripts/validate_dvh_methods.py --nelms <Nelms et al. dataset folder>
python scripts/validate_dvh_mask_vs_polygon.py --nelms <Nelms et al. dataset folder>
```

The Nelms et al. datasets are the paper's supplementary material and are not redistributed with AutoSeg. Each script rewrites this report once both have run. The first takes about 36 minutes and its full results quote timings, so it should run on an otherwise idle machine. `python scripts/supplementary_1_dvh.py` rewrites the report from the last runs' results. Regenerate this report rather than editing it.
