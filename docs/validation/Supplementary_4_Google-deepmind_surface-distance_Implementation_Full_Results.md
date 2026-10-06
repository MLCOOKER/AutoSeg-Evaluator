# Supplementary 4 - Google-deepmind/surface-distance Implementation: full results

AutoSeg Evaluator 2.6.1, commit 9dc078f · generated 2026-10-02 by `scripts/validate_surface_distance_equivalence.py` · surface-distance 0.1, NumPy 1.26.4, SciPy 1.13.1, SimpleITK 2.3.1 · 1,000 mask pairs · 29 s. The summary is [Supplementary 4](Supplementary_4_Google-deepmind_surface-distance_Implementation.md). Regenerate this file rather than editing it.

## What was compared

Dice, the Hausdorff distances, mean surface distance and Surface Dice are computed by `core/surface_distance.py`, a port of google-deepmind/surface-distance carried since v1. Since then the surface-area table is built once per spacing and reused, the library's element-by-element sort is replaced by a vectorised one giving the same order, and the application crops both masks to the pair's bounding box before calling it, the box the library crops to itself. Each is meant to change nothing.

- **The port** (*AutoSeg's copy* in the report). The port's functions against the package's, called identically on the same arrays and spacing.
- **The application** (*the metrics AutoSeg reports*). `compute_geometric_metrics`, as a computation run calls it, from SimpleITK images carrying physical spacing (x, y, z), against the package on the full arrays with the spacing in their (z, y, x) order. Mean surface distance is the equal-weight average of the package's two directional means, as the application reports it.

Every comparison is exact equality. Two undefined values (both NaN) count as equal. Over both comparisons, 15 were infinite in both and 16 undefined in both.

## The cases

Arrays of 56 × 56 × 36 voxels; seed 20261001. Each random reference is paired with a test made from it: moved by up to 3 voxels along each axis, then enlarged or eroded by up to 2 random ellipsoids of radius 2-6 voxels (radii in voxels throughout):

| Pair | Count | Built from |
| --- | ---: | --- |
| identical | 1 | hand-made |
| a single voxel each | 1 | hand-made |
| disjoint | 1 | hand-made |
| test empty | 1 | hand-made |
| both empty | 1 | hand-made |
| a sheet one voxel thick | 1 | hand-made |
| organ-sized ellipsoid | 249 | 1 ellipsoid(s), radius 8-16 |
| irregular, several components | 249 | 2-4 ellipsoid(s), radius 3-8 |
| small (1-3 voxels in radius) | 248 | 1 ellipsoid(s), radius 1.2-3 |
| large, lobulated | 248 | 3 ellipsoid(s), radius 6-14 |

Voxel spacings, x × y × z in mm, taken in turn:

| Spacing | Pairs |
| --- | ---: |
| 1 × 1 × 1 | 143 |
| 0.98 × 0.98 × 3 | 143 |
| 1.074 × 1.074 × 2 | 143 |
| 1.367 × 1.367 × 2 | 143 |
| 0.468 × 0.468 × 1 | 143 |
| 0.5 × 0.5 × 0.5 | 143 |
| 0.8 × 1.2 × 2.5 | 142 |

## The port against the package, by quantity

| Quantity | Comparisons | Identical | Largest difference |
| --- | ---: | ---: | ---: |
| Distances, reference → test (every surface element) | 1,000 | 1,000 | 0 |
| Distances, test → reference (every surface element) | 1,000 | 1,000 | 0 |
| Surface-element areas, reference | 1,000 | 1,000 | 0 |
| Surface-element areas, test | 1,000 | 1,000 | 0 |
| Dice | 1,000 | 1,000 | 0 |
| Hausdorff 50% | 1,000 | 1,000 | 0 |
| Hausdorff 90% | 1,000 | 1,000 | 0 |
| Hausdorff 95% | 1,000 | 1,000 | 0 |
| Hausdorff 99% | 1,000 | 1,000 | 0 |
| Hausdorff 100% | 1,000 | 1,000 | 0 |
| Mean distance, reference → test | 1,000 | 1,000 | 0 |
| Mean distance, test → reference | 1,000 | 1,000 | 0 |
| Surface Dice @ 0 mm | 1,000 | 1,000 | 0 |
| Surface Dice @ 0.5 mm | 1,000 | 1,000 | 0 |
| Surface Dice @ 1 mm | 1,000 | 1,000 | 0 |
| Surface Dice @ 2 mm | 1,000 | 1,000 | 0 |
| Surface Dice @ 3 mm | 1,000 | 1,000 | 0 |
| Surface Dice @ 5 mm | 1,000 | 1,000 | 0 |

## The application's metric path against the package, by quantity

| Quantity | Comparisons | Identical | Largest difference |
| --- | ---: | ---: | ---: |
| Dice | 1,000 | 1,000 | 0 |
| Hausdorff 100% | 1,000 | 1,000 | 0 |
| Hausdorff 95% | 1,000 | 1,000 | 0 |
| Mean surface distance | 1,000 | 1,000 | 0 |
| Surface Dice @ 1 mm | 1,000 | 1,000 | 0 |
| Surface Dice @ 2 mm | 1,000 | 1,000 | 0 |
| Surface Dice @ 3 mm | 1,000 | 1,000 | 0 |

## By voxel spacing

| Spacing (mm) | Pairs | Port: identical | Application: identical |
| --- | ---: | ---: | ---: |
| 1 × 1 × 1 | 143 | 2,574 of 2,574 | 1,001 of 1,001 |
| 0.98 × 0.98 × 3 | 143 | 2,574 of 2,574 | 1,001 of 1,001 |
| 1.074 × 1.074 × 2 | 143 | 2,574 of 2,574 | 1,001 of 1,001 |
| 1.367 × 1.367 × 2 | 143 | 2,574 of 2,574 | 1,001 of 1,001 |
| 0.468 × 0.468 × 1 | 143 | 2,574 of 2,574 | 1,001 of 1,001 |
| 0.5 × 0.5 × 0.5 | 143 | 2,574 of 2,574 | 1,001 of 1,001 |
| 0.8 × 1.2 × 2.5 | 142 | 2,556 of 2,556 | 994 of 994 |

## By kind of pair

| Pair | Pairs | Port: identical | Application: identical |
| --- | ---: | ---: | ---: |
| identical | 1 | 18 of 18 | 7 of 7 |
| a single voxel each | 1 | 18 of 18 | 7 of 7 |
| disjoint | 1 | 18 of 18 | 7 of 7 |
| test empty | 1 | 18 of 18 | 7 of 7 |
| both empty | 1 | 18 of 18 | 7 of 7 |
| a sheet one voxel thick | 1 | 18 of 18 | 7 of 7 |
| organ-sized ellipsoid | 249 | 4,482 of 4,482 | 1,743 of 1,743 |
| irregular, several components | 249 | 4,482 of 4,482 | 1,743 of 1,743 |
| small (1-3 voxels in radius) | 248 | 4,464 of 4,464 | 1,736 of 1,736 |
| large, lobulated | 248 | 4,464 of 4,464 | 1,736 of 1,736 |

## The edge cases, value by value

What the application reports and what the package gives, for each hand-made pair.

### Identical (1 × 1 × 1 mm)

| Quantity | Application | Package |
| --- | ---: | ---: |
| Dice | 1 | 1 |
| Hausdorff 100% | 0 | 0 |
| Hausdorff 95% | 0 | 0 |
| Mean surface distance | 0 | 0 |
| Surface Dice @ 1 mm | 1 | 1 |
| Surface Dice @ 2 mm | 1 | 1 |
| Surface Dice @ 3 mm | 1 | 1 |

### A single voxel each (0.98 × 0.98 × 3 mm)

| Quantity | Application | Package |
| --- | ---: | ---: |
| Dice | 0 | 0 |
| Hausdorff 100% | 2.19135 | 2.19135 |
| Hausdorff 95% | 2.19135 | 2.19135 |
| Mean surface distance | 1.62932 | 1.62932 |
| Surface Dice @ 1 mm | 0.25 | 0.25 |
| Surface Dice @ 2 mm | 0.75 | 0.75 |
| Surface Dice @ 3 mm | 1 | 1 |

### Disjoint (1.074 × 1.074 × 2 mm)

| Quantity | Application | Package |
| --- | ---: | ---: |
| Dice | 0 | 0 |
| Hausdorff 100% | 51.556 | 51.556 |
| Hausdorff 95% | 50.0426 | 50.0426 |
| Mean surface distance | 44.9139 | 44.9139 |
| Surface Dice @ 1 mm | 0 | 0 |
| Surface Dice @ 2 mm | 0 | 0 |
| Surface Dice @ 3 mm | 0 | 0 |

### Test empty (1.367 × 1.367 × 2 mm)

| Quantity | Application | Package |
| --- | ---: | ---: |
| Dice | 0 | 0 |
| Hausdorff 100% | ∞ | ∞ |
| Hausdorff 95% | ∞ | ∞ |
| Mean surface distance | undefined | undefined |
| Surface Dice @ 1 mm | 0 | 0 |
| Surface Dice @ 2 mm | 0 | 0 |
| Surface Dice @ 3 mm | 0 | 0 |

### Both empty (0.468 × 0.468 × 1 mm)

| Quantity | Application | Package |
| --- | ---: | ---: |
| Dice | undefined | undefined |
| Hausdorff 100% | ∞ | ∞ |
| Hausdorff 95% | ∞ | ∞ |
| Mean surface distance | undefined | undefined |
| Surface Dice @ 1 mm | undefined | undefined |
| Surface Dice @ 2 mm | undefined | undefined |
| Surface Dice @ 3 mm | undefined | undefined |

### A sheet one voxel thick (0.5 × 0.5 × 0.5 mm)

| Quantity | Application | Package |
| --- | ---: | ---: |
| Dice | 0 | 0 |
| Hausdorff 100% | 0.5 | 0.5 |
| Hausdorff 95% | 0.5 | 0.5 |
| Mean surface distance | 0.25 | 0.25 |
| Surface Dice @ 1 mm | 1 | 1 |
| Surface Dice @ 2 mm | 1 | 1 |
| Surface Dice @ 3 mm | 1 | 1 |

## What this does not cover

The masks are synthetic, so they exercise the computation rather than any clinical anatomy, and the arrays are smaller than a planning CT; nothing in the computation depends on array size beyond the crop. An earlier version of the port was compared with the package on a clinical head-and-neck sample by `scripts/validate_against_upstream.py`. The package compared is the one installed above.

## Reproduce

```
python scripts/validate_surface_distance_equivalence.py
```
