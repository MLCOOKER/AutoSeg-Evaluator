# Supplementary 2 - Binary Mask Rasteriser Validation Report

AutoSeg Evaluator 3.0.0, commit 85e6c28 · computed 2026-10-08 by `scripts/validate_rasterisation_fidelity.py`. Every table behind this report is in its [full results](Supplementary_2_Binary_Mask_Rasteriser_Validation_Report_Full_Results.md).

## Aim

AutoSeg Evaluator's 3D metrics (Supplementary 4) and the DVH of a STAPLE consensus (Supplementary 1) are computed on binary masks, so they can be no more accurate than the conversion of each contour into a mask, its rasterisation. AutoSeg v1 and v2 rasterised as PlatiPy does, snapping every contour vertex to the nearest voxel before filling. Version 3 replaced this with a rasteriser adapted from dcmrtstruct2nii [1], which keeps every vertex where it is. This validation asks three questions:

1. How closely do v3's masks match the contours they come from, compared with six widely used converters, with v1-v2's rasteriser, and with the best any binary mask can do?
2. Does it convert every kind of contour: holes, nested and overlapping contours, tiny regions, rotated image frames and coordinates far from the origin?
3. What does the change from v1-v2's rasteriser do to masks of real structures?

## Method

### How AutoSeg v3 rasterises

Each contour vertex is converted to continuous voxel coordinates, so a vertex at voxel position 203.78 stays at 203.78 instead of being rounded to 204. Each contour is assigned to its nearest CT slice, and each region is filled with a scanline algorithm that includes a voxel when its centre lies inside the region. A voxel centre lying exactly on a contour edge is assigned to one side only, where dcmrtstruct2nii counts it inside on both; away from such ties the two fill identical voxels. Contours stored as CLOSEDPLANAR_XOR, which encode holes and overlaps, are read through the same contour reader as AutoSeg's 2D metrics (Supplementary 3).

### Data: a synthetic benchmark built by the authors

14 shape families: sphere and ellipsoid cross-sections, a square and a rectangle, a triangle, a concave L, an annulus, nested shells, disconnected components, a narrow bridge, a tiny 0.36 mm square, a keyhole, an irregular star, and overlapping contours stored as CLOSEDPLANAR_XOR. Each was written as a CT and RTSTRUCT in 108 configurations that vary the sub-pixel position, rotation, anisotropic pixel spacing, size, vertex density, feature width, distance from pixel boundaries, an oblique image frame, and coordinates 10⁵ mm from the origin: 1,512 structure and configuration cases in all. Each was converted by AutoSeg v3, by AutoSeg v1-v2's rasteriser, and by six converters: dcmrtstruct2nii 5, DicomRTTool 6.1.0, Plastimatch 1.9.4, PlatiPy 0.7.2, PyRaDiSe 0.2.3, RT-Utils 1.2.7. The best possible binary mask was also computed for every case (below).

### Measuring a mask's error

The reference on each plane is the polygon as stored in, and read back from, the RTSTRUCT. Each pixel stands for its whole footprint, a fraction *f* of which lies inside the polygon. A mask's error is the area it wrongly includes plus the area it wrongly leaves out, as a percentage of the polygon's area: the *area error*. The mask that includes exactly the pixels at least half covered (*f* ≥ 0.5) has the smallest possible error of any binary mask, so it is the best possible binary mask, not a competitor. Errors are averaged over a structure's planes, then over the configurations within a shape family, then equally over families. The tiny 0.36 mm square, smaller than any pixel here, can have errors above 100 % and dominates any average that includes it, so results are also given without it.

### Clinical comparison

An anonymised head-and-neck case (Human Research Ethics approval RGS4979): a CT of 512 × 512 × 208 voxels of 1.367 × 1.367 × 2.000 mm, with 7 structure sets. Every structure was rasterised by both v1-v2's rasteriser and v3's (357 structures), and the two masks compared by volume and Dice. Only organ names and numbers appear in this report.

## Results

**Contours converted**, of the 1,512 cases:

| Method | Converted |
| --- | ---: |
| AutoSeg v3 | 1,512 |
| DicomRTTool 6.1.0 | 1,512 |
| AutoSeg v1-v2 | 1,188 |
| dcmrtstruct2nii 5 | 1,188 |
| Plastimatch 1.9.4 | 1,188 |
| PlatiPy 0.7.2 | 1,188 |
| PyRaDiSe 0.2.3 | 224 |
| RT-Utils 1.2.7 | 224 |

dcmrtstruct2nii, Plastimatch and PlatiPy do not support CLOSEDPLANAR_XOR contours, and neither does v1-v2's rasteriser. PyRaDiSe and RT-Utils produced masks on a grid the benchmark could match only where the pixels are square.

**Accuracy**, on the 176 cases all six converters and AutoSeg v3 converted:

| Method | Area error | Without the tiny square |
| --- | ---: | ---: |
| Best possible binary mask | 19.12 % | 11.03 % |
| **AutoSeg v3** | 22.46 % | 11.14 % |
| Plastimatch 1.9.4 | 22.47 % | 11.14 % |
| dcmrtstruct2nii 5 | 22.54 % | 11.22 % |
| AutoSeg v1-v2 | 122.25 % | 23.91 % |
| PlatiPy 0.7.2 | 122.25 % | 23.91 % |
| PyRaDiSe 0.2.3 | 123.46 % | 25.24 % |
| RT-Utils 1.2.7 | 123.46 % | 25.24 % |
| DicomRTTool 6.1.0 | 123.48 % | 25.27 % |

Case by case against Plastimatch 1.9.4, the most accurate converter, over the 1,188 cases both converted, AutoSeg v3 was more accurate on 42, tied on 1,118 and less accurate on 28.

**Robustness.** Rotating the whole CT and contours to an oblique frame, or moving them 10⁵ mm from the origin, changed none of AutoSeg v3's voxels, apart from pixels whose centre lies exactly on a contour edge.

**Clinical comparison.** v3's mask was smaller than v1-v2's for 355 of 357 structures, and the two agreed with a median Dice of 0.941 (lowest 0.634). By structure size:

| Volume (v1-v2 mask) | Structures | Median volume change, v1-v2 to v3 | Median Dice, v1-v2 against v3 |
| --- | ---: | ---: | ---: |
| Under 1 cc | 65 | -22.2 % | 0.875 |
| 1-10 cc | 77 | -11.9 % | 0.933 |
| 10-100 cc | 159 | -9.7 % | 0.948 |
| Over 100 cc | 56 | -4.5 % | 0.975 |

## Findings

- **AutoSeg v3's masks are as close to the contours as the best converter's.** Its area error was 22.46 %, the lowest of the seven methods, against 22.47 % for Plastimatch 1.9.4, the most accurate converter, and 19.12 % for the best possible binary mask (11.14 %, 11.14 % and 11.03 % without the tiny square).
- **It converts every kind of contour.** It converted all 1,512 cases, including the overlapping and nested contours three of the six converters cannot read, and no voxel changed under a rotated frame or distant coordinates.
- **Why dcmrtstruct2nii rather than Plastimatch.** The two were equally accurate (22.54 % and 22.47 %), but dcmrtstruct2nii is a Python library, so it could be built into AutoSeg directly. Assigning a voxel centre on an edge to one side only brought AutoSeg's version from 22.54 % to 22.46 %.
- **v1-v2's rasteriser was less accurate:** 122.25 % area error, 5.4 times v3's (23.91 % without the tiny square, 2.1 times), the same as PlatiPy's, which it followed. Snapping vertices to the voxel grid over-fills a structure: its masks were +26.39 mm² per plane too large on average, against 0.00 mm² for v3.
- **On real structures the change matters most for small organs.** The median volume change from v1-v2's mask to v3's was -22.2 % for structures under 1 cc and -4.5 % for those over 100 cc. Mask-based metrics of small organs computed by v1-v2 are therefore not directly comparable with v3's.
- **Limits.** No binary mask reproduces a contour exactly: even the best possible mask missed or added 11.03 % of a structure's area on these shapes, without the tiny square. That error shrinks as structures grow relative to the pixel, and the benchmark's shapes are small, so the percentages here are larger than for most organs; the ranking of the methods is what carries over. AutoSeg's 2D metrics (Supplementary 3) work on the contours themselves and avoid this error altogether.

## References

1. dcmrtstruct2nii: convert DICOM RT-Struct contours to NIfTI masks. GitHub. https://github.com/Sikerdebaard/dcmrtstruct2nii

## Reproduce

```
python scripts/validate_rasterisation_fidelity.py --study <benchmark>/project --clinical <folder of CT and RTSTRUCT files>
```

The benchmark is about 5 GB and is not stored with AutoSeg; `--clinical` is optional. This run took 4 minutes, reusing the masks of an earlier run; `--work <folder> --reuse` keeps finished configurations between runs, and `--render-only` rewrites this report from the last run's results. Regenerate this report rather than editing it.
