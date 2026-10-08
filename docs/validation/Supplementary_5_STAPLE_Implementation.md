# Supplementary 5 - STAPLE Implementation

AutoSeg Evaluator 3.0.0, commit 85e6c28 · computed 2026-10-08 by `scripts/validate_staple_against_upstream.py`. Every table behind this report is in its [full results](Supplementary_5_STAPLE_Implementation_Full_Results.md).

## Aim

When several observers contour the same organ, AutoSeg Evaluator can combine their contours into a consensus with STAPLE (Simultaneous Truth and Performance Level Estimation) [1]. STAPLE estimates how reliably each observer, or rater, includes the organ's voxels (sensitivity) and leaves out the rest (specificity), and from these the probability that each voxel belongs to the organ. AutoSeg runs SimpleITK's implementation of STAPLE [2] and adds three steps around it:

1. it crops the raters' masks to the box enclosing all of them, with a small margin;
2. it thresholds STAPLE's probability map at 0.5 to give a binary consensus;
3. it pads the consensus back to the full image.

It also stops STAPLE after at most 500 iterations. This validation asks three questions:

1. Is AutoSeg's consensus exactly SimpleITK's STAPLE on the same prepared input, so that the added steps are implemented as described?
2. Does the iteration cap ever stop STAPLE before it converges?
3. What does the crop change, compared with running STAPLE over the whole image, and is it justified?

## Method

**Data.** An anonymised head-and-neck case (Human Research Ethics approval RGS4979): a CT of 512 × 512 × 208 voxels of 1.367 × 1.367 × 2.000 mm, with 7 structure sets from several auto-contouring systems and manual observers. Every organ contoured in at least two structure sets was used: 55 organs, with 2-6 raters each (168 rater contours). Each rater's contours were converted to a binary mask by AutoSeg's rasteriser (Supplementary 2). Only organ names and numbers appear in this report.

**Comparisons.** For each organ:

1. *AutoSeg against an independent reconstruction.* The validation script rebuilt the consensus from SimpleITK alone: the same crop, with the margin AutoSeg chose; a new STAPLE filter with AutoSeg's settings; the 0.5 threshold; and the pad-back. Compared: each rater's sensitivity and specificity, every consensus voxel, and the consensus volume and mean entropy.
2. *AutoSeg against SimpleITK's own defaults.* SimpleITK's STAPLE was run on the same cropped input with no iteration limit, its default, so that it stopped only when its estimates converged. Compared: each rater's sensitivity and specificity, every consensus voxel, and the iterations taken. This was repeated with the cap of 100 iterations that AutoSeg used before version 3.
3. *Cropped against the whole image.* STAPLE was run on SimpleITK's defaults over the whole CT, and over the box widened by 25 voxels. Each consensus was compared with AutoSeg's by Dice and volume, and the time taken recorded.

**Criterion.** The first two comparisons require exact equality, with no tolerance. The third measures the difference.

**Settings.** Iteration cap 500; confidence weight 1, SimpleITK's default; a crop margin of at least 2 voxels, widened one voxel at a time until the organ fills at most 50 % of the box, up to 25 voxels.

**Software.** SimpleITK 2.3.1, NumPy 1.26.4, pydicom 2.4.4.

## Results

| Comparison | Organs | Identical |
| --- | ---: | ---: |
| AutoSeg against the independent reconstruction | 55 | 55 |
| AutoSeg against SimpleITK's own defaults | 55 | 55 |

Identical means every rater's sensitivity and specificity and every consensus voxel were equal, bit for bit; against the reconstruction, the consensus volume and mean entropy were too.

Run to convergence, STAPLE took 2-151 iterations (median 21), within AutoSeg's cap of 500 for every organ. Under the earlier cap of 100, 2 organs stopped before converging: ln_neck_viia_l (102), spinalcord (151), with the iterations each needs in brackets. Stopping early left their consensus unchanged and moved their sensitivities and specificities by at most 8.1 × 10⁻⁶.

Over the whole image, STAPLE gave the same consensus as AutoSeg's crop for 50 of 55 organs. The other 5:

| Organ | Raters | Whole-image volume against cropped | Dice |
| --- | ---: | ---: | ---: |
| parotid_l | 5 | +8.0 % | 0.9616 |
| brainstem | 6 | +7.4 % | 0.9641 |
| parotid_r | 5 | +2.3 % | 0.9888 |
| ln_neck_viia_l | 3 | +0.6 % | 0.9971 |
| larynx | 4 | +0.1 % | 0.9995 |

No organ was left empty either way. A box widened by 25 voxels gave the whole image's consensus, voxel for voxel, for 55 of 55 organs. The margin AutoSeg chose was 2 voxels. Median time per organ: 0.45 s cropped and 7.8 s over the whole image.

## Findings

- **AutoSeg's STAPLE is SimpleITK's STAPLE.** Its consensus matched an independent reconstruction bit for bit on all 55 organs, so the crop, the threshold and the pad-back are implemented as described, and the estimates are SimpleITK's own.
- **STAPLE runs to convergence.** No organ reached the cap of 500 (the most any needed was 151), so every consensus is the one SimpleITK gives with no limit. The earlier cap of 100 stopped 2 organs short of convergence, without changing their consensus; version 3 raised it to 500, the largest the Compute tab allows, so that STAPLE's estimates are always its converged ones.
- **The crop changes the consensus slightly, and is kept deliberately.** STAPLE estimates each rater's specificity, and the prior probability that a voxel belongs to the organ, from every voxel it is given. Over a whole CT, both are dominated by background that every rater agrees on, so they depend on how much air and body the scan happens to include, which has nothing to do with the contours. Estimating only in the organ's neighbourhood, where the raters' contours actually differ, makes the consensus depend on the contours. Other approaches restrict STAPLE's estimation differently, for example by ignoring the voxels on which all raters agree [3]. Here the cost was small: 50 of 55 organs were identical, and the other 5 were larger over the whole image by 0.1-8.0 % in volume (Dice at least 0.96). The crop also makes STAPLE about 17 times faster.
- **The consensus depends on how much background the box holds.** A margin of 25 voxels already gave the whole image's consensus for 55 of 55 organs. AutoSeg's margin stayed at its 2-voxel minimum on every organ, because each already filled less than 50% of its box, so its consensus is STAPLE estimated in the organ's immediate neighbourhood. The specificities AutoSeg reports are therefore relative to that neighbourhood, not to the whole scan.
- **Limits.** One case, with 2-6 raters per organ. The comparisons test the implementation and the effect of its two settings, not how close any consensus is to the true anatomy.

## References

1. Warfield SK, Zou KH, Wells WM. Simultaneous truth and performance level estimation (STAPLE): an algorithm for the validation of image segmentation. IEEE Trans Med Imaging. 2004;23(7):903-921.
2. Lowekamp BC, Chen DT, Ibáñez L, Blezek D. The design of SimpleITK. Front Neuroinform. 2013;7:45.
3. Xu Z, Asman AJ, Landman BA. Generalized statistical label fusion using multiple consensus levels. Proc SPIE Int Soc Opt Eng. 2012;8314:831411. doi:10.1117/12.910918

## Reproduce

```
python scripts/validate_staple_against_upstream.py --data <folder of CT and RTSTRUCT files>
```

Point it at any folder holding a CT and two or more structure sets. The run took 12 minutes, most of it the whole-image comparison, which `--no-crop-comparison` skips; `--render-only` rewrites this report from the last run's results. The exit code is 0 only if every comparison was exact. Regenerate this report rather than editing it.
