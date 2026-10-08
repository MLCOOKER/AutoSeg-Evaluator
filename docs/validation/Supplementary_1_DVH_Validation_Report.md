# Supplementary 1 - DVH Validation Report

AutoSeg Evaluator 3.0.0, commit 85e6c28 · computed 2026-10-08 by `scripts/validate_dvh_methods.py` and `scripts/validate_dvh_mask_vs_polygon.py`. Every table behind this report is in its [full results](Supplementary_1_DVH_Validation_Report_Full_Results.md).

## Aim

A dose-volume histogram (DVH) shows how much of a structure receives each dose, and AutoSeg Evaluator compares contours dosimetrically through statistics read from it, such as the mean dose (Dmean) or the dose received by the hottest 95 % of the structure (D95). AutoSeg Evaluator versions 1 and 2 computed DVHs with dicompyler-core 0.5.6 [2]. Version 3 computes them with its own method, which integrates the dose over the contour polygons directly. This validation asks two questions:

1. **Is the v3 method accurate?** It is scored against DVHs whose true values are known exactly, beside v1-v2's dicompyler-core, the other methods considered, and two commercial systems.
2. **What does a consensus's binary mask cost?** A STAPLE consensus [3] exists only as a binary mask, with no contours, so its DVH has to come from its voxels. What error does that add when a test structure set is compared with a consensus? And should the test's DVH be computed from a mask too, so that both are taken the same way?

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

**Contours against a consensus mask.** A test structure set compared with a STAPLE consensus has its DVH difference taken between a contour and a mask. To measure the error this adds, the shifted copies in the Nelms dataset were used: the same shapes moved by 0.5, 1 or 1.5 mm, whose analytic values are known. Each shifted copy played the test contour, and its unshifted twin the reference, converted to the binary mask a STAPLE consensus of identical raters returns (checked below). Both shared the same dose grid, CT grid and sub-pixel offset, so the true dose difference between them is known exactly, and each way of taking the difference can be scored against it. The copies come in two kinds:

- *across the gradient*: copies whose movement does not climb the dose field, so the true difference is 0 Gy;
- *along the gradient*: copies whose movement climbs the 1 Gy/mm field, so the true difference is 0.5-1.5 Gy, known exactly because the field is linear.

The difference was taken three ways: the test from its contours and the reference from its mask (*contour vs mask*, AutoSeg's choice); both from masks (*mask vs mask*); and both from contours (*contour vs contour*, the lower limit, which a consensus reference does not allow). Masks were made on the Nelms CT (0.6 mm pixels) and again with its pixels enlarged to 1.07 mm and 1.37 mm, keeping the slices: the most common and the coarsest in-plane pixel sizes among the planning CTs of the authors' clinical cohort. A mask's error depends on where a structure falls among the voxels, so each coarser grid was laid at 4 sub-pixel offsets.

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
| 0.5 mm | 14/195 | 19.4 | 459,900 | 0.07 |
| 0.25 mm | 10/195 | 22.2 | 827,820 | 0.12 |

For large structures, the time for one structure against one dose, with AutoSeg's rule and with 0.25 mm throughout, and AutoSeg's worst dose error expressed as the boundary shift that would cause it:

| Structure | AutoSeg v3: spacing | AutoSeg v3: seconds | 0.25 mm throughout: seconds | AutoSeg v3: worst error (mm) |
| --- | ---: | ---: | ---: | ---: |
| sphere, 4 cc | 0.25 mm | 0.09 | 0.09 | 0.003 |
| sphere, 33 cc | 0.25 mm | 0.44 | 0.45 | 0.008 |
| sphere, 268 cc | 0.5 mm | 0.78 | 3.78 | 0.005 |
| sphere, 905 cc | 1 mm | 0.30 | 14.33 | 0.091 |
| cylinder, 3,177 cc | 1 mm | 0.71 | 52.17 | 0.006 |
| cylinder, 6,220 cc | 1 mm | 1.23 | 104.44 | 0.155 |

### A test contour against a consensus mask

Error in the DVH difference between a test structure and a reference shifted 0.5-1.5 mm along the dose gradient (true difference 0.5-1.5 Gy), with each DVH computed from its binary mask or its polygon contours, on 1.37 mm pixels (60 pairs over 4 offsets): 95th percentile / largest error (measured minus true difference), as % of the reference's analytic value.

| Statistic | Mask vs mask | Contour vs mask (AutoSeg) | Contour vs contour |
| --- | ---: | ---: | ---: |
| Volume | 6.15 / 11.51 | 5.03 / 7.93 | 0.00 / 0.00 |
| Dmean | 2.96 / 3.44 | 2.10 / 2.17 | 0.00 / 0.00 |
| D99 | 20.45 / 29.10 | 12.40 / 15.85 | 2.90 / 3.14 |
| D95 | 12.92 / 19.98 | 6.45 / 12.46 | 2.32 / 3.31 |
| D5 | 2.53 / 3.62 | 1.90 / 2.28 | 0.54 / 0.72 |
| D1 | 3.11 / 3.60 | 2.20 / 2.58 | 0.50 / 0.65 |
| D0.03cc | 3.13 / 3.61 | 2.49 / 2.57 | 0.46 / 0.65 |

On every grid and every statistic, mask vs mask had the largest error, and contour vs mask fell between it and contour vs contour. The same table for the 0.6 mm (Nelms CT) and 1.07 mm grids is in the full results. For copies shifted across the gradient, which in effect coincide with the reference, the order reverses: over all statistics on 1.37 mm pixels, the 95th-percentile error was 2.80 % for mask vs mask against 3.75 % for contour vs mask.

Given three identical masks, STAPLE returned the mask itself for all 50 structures, so a consensus of agreeing raters is exactly the mask scored here.

## Findings

- **The v3 DVH method is accurate.** It had fewer statistics beyond 3 % than either commercial system in both tests (0 and 10, against 5 and 18 for PlanIQ and 32 and 53 for Pinnacle³). Its volume error along the whole DVH (-2.0 to +0.7 %) spans 2.7 percentage points, against 2.3 for PlanIQ (-1.5 to +0.8 %) and 19.1 for Pinnacle³ (-11.2 to +7.9 %).
- **Why dicompyler-core was replaced.** As used in v1-v2 it had 119 and 140 statistics beyond 3 % in Tests 1 and 2, and reported D99 as 0 Gy in 49 of 100 cases. Reading D*x* correctly from its histogram still leaves 83 and 103, because it also samples the dose only once per dose voxel in each contour plane, so correcting it would not have been enough.
- **Why contour polygons rather than a mask.** On the Nelms data the sub-sampled mask had 0, 10 and 10 statistics beyond 3 % in the three tests, against 0, 10 and 11 for AutoSeg v3; on the disc phantoms its worst error reached 0.64 Gy against 0.11 Gy. A mask includes or leaves out whole voxels where a contour cuts through them, which matters most for small structures; the polygons have no such staircase.
- **Why the 0.25, 0.5 or 1 mm rule.** 0.25 mm sub-cells were the most accurate tested on Test 2 (10 statistics beyond 3 %, against 14 at 0.5 mm, 35 at 1 mm), but their number grows with a structure's volume. At 0.25 mm throughout, the largest structure (6,220 cc) took 104 s; the rule sampled it at 1 mm in 1.2 s, with a worst dose error equal to a 0.15 mm boundary shift. Every Nelms and disc structure fell within the cap, so the rule sampled each at 0.25 mm and its results there are those of the finest method.
- **A DVH difference against a consensus carries the consensus's mask error.** When a test structure set is compared with a STAPLE consensus, the error expected on 1.37 mm pixels is up to 2.1 % for Dmean and 12.4 % for D99 (95th percentile; 1.3 % and 8.5 % on 1.07 mm pixels). It comes from representing the consensus on the CT voxel grid: a mask includes or excludes whole voxels where a contour passes through them, which can shift its boundary by up to half a voxel.
- **So every contour takes its DVH from its contours, and only a consensus from its mask.** Computing the test's DVH from a mask as well (mask vs mask) does not cancel the consensus's error but adds a second, independent one: for copies shifted along the gradient, as any contour that genuinely differs from the consensus is, it had the largest error on every grid and statistic. It helped only for copies shifted across the gradient, which in effect coincide with the reference. Because the consensus's error is the same for every source compared with it, it cancels when sources are compared with each other.
- **Limits.** These values apply to the Nelms structures (3.6-12.2 cc) in a 1 Gy/mm dose gradient. Smaller structures, such as a cochlea or the optic chiasm, and steeper gradients will show larger errors, and larger structures or shallower gradients smaller ones. The shifts tested are rigid; a test contour differing in shape makes the two masks' errors less alike still. A consensus of raters who disagree is validated in Supplementary 5.

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
