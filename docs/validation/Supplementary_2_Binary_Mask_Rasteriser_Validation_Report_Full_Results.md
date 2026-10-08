# Supplementary 2 - Binary Mask Rasteriser Validation Report: full results

AutoSeg Evaluator 3.0.0, commit 85e6c28 · computed 2026-10-08 by `scripts/validate_rasterisation_fidelity.py` · 108 configurations, 14 shape families. The summary is [Supplementary 2](Supplementary_2_Binary_Mask_Rasteriser_Validation_Report.md). Regenerate this file rather than editing it.

## The benchmark

The question it asks: which binary mask best preserves the continuous planar region encoded by the polygon actually stored in, and read back from, an RTSTRUCT? Each pixel stands for its whole footprint; its reference fraction *f* is the share of that footprint inside the polygon. A mask's error on a plane is the area it wrongly includes plus the area it wrongly leaves out,

E = pixel area × Σ [ b(1 − f) + (1 − b) f ],

and the mask that includes exactly the pixels with f ≥ 0.5 minimises it. That majority-area mask is the attainable optimum, not a competitor. The *area error* below is E divided by the polygon's area, averaged over a structure's planes, then over configurations within a shape family, then equally over families: the benchmark's primary aggregation. *Beyond the optimum* is the same for E minus the optimum's E. Tiny regions (0.36 mm squares) can exceed 100 %, so every table is also given without them.

The 14 families: sphere and ellipsoid cross-sections, square and rectangle, triangle, concave L, annulus, nested shells, disconnected components, a narrow bridge, a tiny region, a keyhole, an irregular star and overlapping XOR contours. The configurations sweep sub-pixel phase, rotation, anisotropic spacing, size, vertex density, feature width, near-boundary offsets, oblique frames and large coordinates. Converter versions: DicomRTTool 6.1.0, Plastimatch 1.9.4, PlatiPy 0.7.2, PyRaDiSe 0.2.3, RT-Utils 1.2.7, dcmrtstruct2nii 5.

AutoSeg's masks were made by `mask_with_reading`, as a computation run makes them, from the benchmark's own CT and RTSTRUCT files; mapped onto its reference grid by its own `canonicalize`; and scored by its own `metrics` against its own stored references. The aggregation is the benchmark's, and reproduces its published tables from its raw rows:

| Published table | Methods | Largest difference in area error |
| --- | ---: | ---: |
| six_way_common | 6 | 2.2e-16 |
| four_way_nonsquare_common | 4 | 2.2e-16 |
| algorithm_summary | 11 | 2.2e-16 |

## What each method could convert

Of 1512 ROI/configuration attempts per method:

| Method | execution error | geometry mismatch | success | unsupported input |
| --- | ---: | ---: | ---: | ---: |
| dcmrtstruct2nii | 0 | 0 | 1188 | 324 |
| DicomRTTool | 0 | 0 | 1512 | 0 |
| Plastimatch | 0 | 0 | 1188 | 324 |
| PlatiPy | 0 | 0 | 1188 | 324 |
| PyRaDiSe | 0 | 1288 | 224 | 0 |
| RT-Utils | 0 | 1288 | 224 | 0 |
| **AutoSeg (continuous)** | 0 | 0 | 1512 | 0 |
| **AutoSeg (legacy)** | 324 | 0 | 1188 | 0 |

The benchmark marked dcmrtstruct2nii, PlatiPy and Plastimatch *unsupported* for the three families stored as CLOSEDPLANAR_XOR (annulus, nested, overlap); PyRaDiSe and RT-Utils produced grids the benchmark could only match on its square-grid companions. AutoSeg reads XOR contours through the same shared reading as its 2D metrics.

## All six converters and AutoSeg, on the cases they share

176 ROI/configurations every one of them converted (the six-way set, square-grid companions only), with the optimum on the same cases.

| Method | Area error | Beyond the optimum | Mean error per plane (mm²) | Geometric Dice | ROI cases |
| --- | ---: | ---: | ---: | ---: | ---: |
| majority-area mask (the attainable optimum) | 19.12 % | 0.00 % | 14.20 | 0.8598 | 176 |
| **AutoSeg (continuous)** | 22.46 % | 3.34 % | 14.34 | 0.8598 | 176 |
| Plastimatch | 22.47 % | 3.34 % | 14.34 | 0.8598 | 176 |
| dcmrtstruct2nii | 22.54 % | 3.42 % | 14.46 | 0.8606 | 176 |
| **AutoSeg (legacy)** | 122.25 % | 103.12 % | 29.25 | 0.8297 | 176 |
| PlatiPy | 122.25 % | 103.12 % | 29.25 | 0.8297 | 176 |
| PyRaDiSe | 123.46 % | 104.33 % | 30.65 | 0.8254 | 176 |
| RT-Utils | 123.46 % | 104.33 % | 30.65 | 0.8254 | 176 |
| DicomRTTool | 123.48 % | 104.36 % | 31.02 | 0.8250 | 176 |

Without the tiny-region family:

| Method | Area error | Beyond the optimum | Mean error per plane (mm²) | Geometric Dice | ROI cases |
| --- | ---: | ---: | ---: | ---: | ---: |
| majority-area mask (the attainable optimum) | 11.03 % | 0.00 % | 15.60 | 0.9458 | 160 |
| **AutoSeg (continuous)** | 11.14 % | 0.10 % | 15.76 | 0.9444 | 160 |
| Plastimatch | 11.14 % | 0.10 % | 15.76 | 0.9444 | 160 |
| dcmrtstruct2nii | 11.22 % | 0.19 % | 15.89 | 0.9452 | 160 |
| **AutoSeg (legacy)** | 23.91 % | 12.87 % | 32.04 | 0.8948 | 160 |
| PlatiPy | 23.91 % | 12.87 % | 32.04 | 0.8948 | 160 |
| PyRaDiSe | 25.24 % | 14.20 % | 33.57 | 0.8901 | 160 |
| RT-Utils | 25.24 % | 14.20 % | 33.57 | 0.8901 | 160 |
| DicomRTTool | 25.27 % | 14.23 % | 33.98 | 0.8896 | 160 |

## The four converters with valid non-square grids, and AutoSeg

The second common set: every configuration but the square-grid companions, on the ROI/configurations dcmrtstruct2nii, DicomRTTool, Plastimatch, PlatiPy and AutoSeg all converted.

| Method | Area error | Beyond the optimum | Mean error per plane (mm²) | Geometric Dice | ROI cases |
| --- | ---: | ---: | ---: | ---: | ---: |
| majority-area mask (the attainable optimum) | 19.68 % | 0.00 % | 13.82 | 0.8574 | 1012 |
| **AutoSeg (continuous)** | 38.40 % | 18.72 % | 14.01 | 0.8620 | 1012 |
| Plastimatch | 38.41 % | 18.73 % | 14.01 | 0.8620 | 1012 |
| dcmrtstruct2nii | 38.52 % | 18.84 % | 14.17 | 0.8624 | 1012 |
| **AutoSeg (legacy)** | 171.62 % | 151.93 % | 26.94 | 0.8287 | 1012 |
| PlatiPy | 171.62 % | 151.93 % | 26.94 | 0.8287 | 1012 |
| DicomRTTool | 171.73 % | 152.05 % | 34.15 | 0.7865 | 1012 |

Without the tiny-region family:

| Method | Area error | Beyond the optimum | Mean error per plane (mm²) | Geometric Dice | ROI cases |
| --- | ---: | ---: | ---: | ---: | ---: |
| majority-area mask (the attainable optimum) | 11.66 % | 0.00 % | 15.19 | 0.9424 | 920 |
| **AutoSeg (continuous)** | 11.81 % | 0.15 % | 15.38 | 0.9408 | 920 |
| Plastimatch | 11.82 % | 0.16 % | 15.38 | 0.9408 | 920 |
| dcmrtstruct2nii | 11.95 % | 0.29 % | 15.56 | 0.9413 | 920 |
| **AutoSeg (legacy)** | 24.55 % | 12.89 % | 29.48 | 0.8934 | 920 |
| PlatiPy | 24.55 % | 12.89 % | 29.48 | 0.8934 | 920 |
| DicomRTTool | 29.67 % | 18.01 % | 37.42 | 0.8477 | 920 |

## Every configuration: AutoSeg beside the direct rasterisers

The benchmark also ran planar rasterisers directly on the read-back polygons, on all 108 configurations and 14 families. AutoSeg is compared here on every ROI/configuration it converted.

| Method | Area error | Beyond the optimum | Mean error per plane (mm²) | Geometric Dice | ROI cases |
| --- | ---: | ---: | ---: | ---: | ---: |
| majority-area mask (the attainable optimum) | 21.03 % | 0.00 % | 16.85 | 0.8588 | 1512 |
| 32 × 32 supersampling | 21.03 % | 0.00 % | 16.85 | 0.8592 | 1512 |
| **AutoSeg (continuous)** | 34.10 % | 13.07 % | 17.06 | 0.8624 | 1512 |
| vtk_zero | 34.11 % | 13.07 % | 17.06 | 0.8623 | 1512 |
| skimage_float | 34.24 % | 13.20 % | 17.25 | 0.8628 | 1512 |
| vtk_default | 34.24 % | 13.21 % | 17.26 | 0.8632 | 1512 |
| vtk_tol1e3 | 34.26 % | 13.23 % | 17.28 | 0.8633 | 1512 |
| opencv_integer | 143.22 % | 122.19 % | 35.70 | 0.8175 | 1512 |
| opencv_shift8 | 143.31 % | 122.28 % | 35.82 | 0.8171 | 1512 |
| **AutoSeg (legacy)** | 164.30 % | 144.70 % | 27.28 | 0.8289 | 1188 |

Without the tiny-region family:

| Method | Area error | Beyond the optimum | Mean error per plane (mm²) | Geometric Dice | ROI cases |
| --- | ---: | ---: | ---: | ---: | ---: |
| majority-area mask (the attainable optimum) | 14.96 % | 0.00 % | 18.14 | 0.9244 | 1404 |
| 32 × 32 supersampling | 14.96 % | 0.00 % | 18.14 | 0.9248 | 1404 |
| **AutoSeg (continuous)** | 15.24 % | 0.28 % | 18.35 | 0.9237 | 1404 |
| vtk_zero | 15.24 % | 0.28 % | 18.35 | 0.9237 | 1404 |
| skimage_float | 15.38 % | 0.42 % | 18.56 | 0.9242 | 1404 |
| vtk_default | 15.39 % | 0.43 % | 18.56 | 0.9247 | 1404 |
| vtk_tol1e3 | 15.41 % | 0.44 % | 18.59 | 0.9248 | 1404 |
| **AutoSeg (legacy)** | 24.45 % | 12.89 % | 29.86 | 0.8936 | 1080 |
| opencv_integer | 34.03 % | 19.06 % | 38.33 | 0.8664 | 1404 |
| opencv_shift8 | 34.12 % | 19.16 % | 38.46 | 0.8659 | 1404 |

`skimage_float` is scikit-image's polygon fill on continuous vertices, the rule dcmrtstruct2nii and AutoSeg's continuous backend descend from; `opencv_*` rounds vertices to integers first, as DicomRTTool, PyRaDiSe and RT-Utils do.

## By shape family

Area error, averaged over the configurations AutoSeg converted:

| Family | Optimum | skimage_float | AutoSeg (continuous) | AutoSeg (legacy) |
| --- | ---: | ---: | ---: | ---: |
| sphere | 15.31 % | 15.32 % | 15.32 % | 43.25 % |
| ellipsoid | 11.07 % | 11.07 % | 11.07 % | 28.94 % |
| cube | 7.60 % | 7.76 % | 7.67 % | 15.00 % |
| cuboid | 7.83 % | 8.00 % | 7.89 % | 15.42 % |
| triangle | 9.70 % | 9.94 % | 9.79 % | 14.47 % |
| concave | 10.63 % | 10.89 % | 10.77 % | 20.89 % |
| annulus | 51.46 % | 53.45 % | 53.33 % | – |
| nested | 14.06 % | 14.43 % | 14.20 % | – |
| disconnected | 14.48 % | 15.15 % | 14.74 % | 30.78 % |
| bridge | 15.89 % | 16.51 % | 16.21 % | 32.69 % |
| tiny | 99.93 % | 279.34 % | 279.34 % | 1562.79 % |
| keyhole | 11.43 % | 11.69 % | 11.57 % | 22.23 % |
| irregular | 11.74 % | 12.08 % | 12.08 % | 20.86 % |
| overlap_xor | 13.32 % | 13.69 % | 13.48 % | – |

The tiny region is a 0.36 mm square, smaller than any pixel here. The optimum drops it (100 % lost); a rule that includes a pixel whose centre lies inside keeps a whole pixel whenever the square covers a centre, which is several times its area. Every centre-inclusion rasteriser, AutoSeg's among them, shares this, and it dominates any average that includes it.

## What a binary mask loses

Every configuration AutoSeg converted, tiny regions excluded. *Bias* is the mask's area minus the polygon's, per plane: positive over-fills. Boundary distances are between the mask's outline and the polygon's, sampled every 0.1 mm along both; *within 0.5 mm* is the share of boundary that close.

| Method | Area error per plane (mm²) | Bias per plane (mm²) | Mean boundary distance (mm) | Boundary HD95 (mm) | Boundary within 0.5 mm | Topology kept |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| majority-area mask (the attainable optimum) | 18.14 | 3.90 | 0.236 | 0.401 | 97.49 % | 85.83 % |
| skimage_float | 18.56 | 3.78 | 0.240 | 0.399 | 97.46 % | 84.50 % |
| AutoSeg (continuous) | 18.35 | 0.00 | 0.239 | 0.407 | 97.42 % | 86.97 % |
| AutoSeg (legacy) | 29.86 | 26.39 | 0.463 | 0.830 | 62.35 % | 71.11 % |

Pixel sizes range from 0.5 to 1.3 mm across the configurations, so these distances are fractions of a pixel. They are the resolution floor under every mask-based metric AutoSeg reports: Dice, the 3D surface distances and the mask DVH all start from a mask like these. The 2D stream measures the polygons themselves and is not subject to it.

## Robustness

The oblique configurations rotate the whole CT and contour frame together; the large ones move it 10⁵ mm away. Neither changes the geometry, so a mask should not change either, except where a pixel centre sits on the boundary (the benchmark's ambiguity band). *Absent planes* are CT planes a structure does not reach, where any area is spurious.

| Method | Voxels changed by an oblique frame | Voxels changed by large coordinates | Area on absent planes (mm²) |
| --- | ---: | ---: | ---: |
| AutoSeg (continuous) | 0 | 0 | 0.0 |
| AutoSeg (legacy) | 762 | 0 | 0.0 |

For comparison the benchmark found no unambiguous changes for Plastimatch and dcmrtstruct2nii under oblique frames, 4 for Plastimatch under large coordinates, and 11,072 mm² of spurious area from DicomRTTool.

## Case by case

AutoSeg (continuous) against each method on the ROI/configurations both converted. A tie is within 1e-07 in area error, the benchmark's reporting tolerance.

| Against | Shared cases | AutoSeg better | Tie | AutoSeg worse | Mean difference |
| --- | ---: | ---: | ---: | ---: | ---: |
| dcmrtstruct2nii | 1188 | 147 | 1032 | 9 | -0.12 % |
| DicomRTTool | 1512 | 1478 | 32 | 2 | -107.69 % |
| Plastimatch | 1188 | 42 | 1118 | 28 | -0.01 % |
| PlatiPy | 1188 | 1155 | 31 | 2 | -128.26 % |
| PyRaDiSe | 224 | 222 | 2 | 0 | -86.07 % |
| RT-Utils | 224 | 222 | 2 | 0 | -86.07 % |
| majority-area mask (the attainable optimum) | 1512 | 0 | 899 | 613 | 13.07 % |
| skimage_float | 1512 | 213 | 1290 | 9 | -0.13 % |
| AutoSeg (legacy) | 1188 | 1155 | 31 | 2 | -128.26 % |

## Clinical comparison: v1-v2's rasteriser against v3's

An anonymised head-and-neck case: CT 512 × 512 × 208, voxels 1.367 × 1.367 × 2.000 mm; 7 structure sets, labelled A, B, ...; 357 structures compared, 32 not (empty in one backend or both). Only organ names and numbers appear. *Δ* is v3's volume minus v1-v2's.

| Volume (v1-v2 mask) | Structures | Median volume change | Median Dice |
| --- | ---: | ---: | ---: |
| Under 1 cc | 65 | -22.2 % | 0.875 |
| 1-10 cc | 77 | -11.9 % | 0.933 |
| 10-100 cc | 159 | -9.7 % | 0.948 |
| Over 100 cc | 56 | -4.5 % | 0.975 |

| Structure set | Structure | v1-v2 (cc) | v3 (cc) | Δ (cc) | Δ (%) | Dice |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| RTSS A | Bone_Mandible | 68.51 | 63.16 | -5.344 | -7.8 | 0.9536 |
| RTSS A | BrachialPlex_L | 15.17 | 12.76 | -2.403 | -15.8 | 0.9078 |
| RTSS A | BrachialPlex_R | 17.12 | 14.48 | -2.635 | -15.4 | 0.9064 |
| RTSS A | Brain | 1343 | 1324 | -19.92 | -1.5 | 0.9916 |
| RTSS A | Brainstem | 28.91 | 26.3 | -2.605 | -9.0 | 0.9513 |
| RTSS A | Cavity_Oral | 114 | 109.7 | -4.376 | -3.8 | 0.9784 |
| RTSS A | Cochlea_L | 0.08596 | 0.04485 | -0.04111 | -47.8 | 0.6857 |
| RTSS A | Cochlea_R | 0.1046 | 0.04859 | -0.05606 | -53.6 | 0.6341 |
| RTSS A | Esophagus | 38.64 | 33.94 | -4.702 | -12.2 | 0.9330 |
| RTSS A | Eye_L | 7.781 | 7.011 | -0.7699 | -9.9 | 0.9449 |
| RTSS A | Eye_R | 7.523 | 6.828 | -0.6952 | -9.2 | 0.9490 |
| RTSS A | Glnd_Lacrimal_L | 0.6055 | 0.4447 | -0.1607 | -26.5 | 0.8399 |
| RTSS A | Glnd_Lacrimal_R | 0.6653 | 0.4335 | -0.2317 | -34.8 | 0.7891 |
| RTSS A | Glnd_Submand_L | 9.721 | 8.663 | -1.058 | -10.9 | 0.9388 |
| RTSS A | Glnd_Submand_R | 13.03 | 11.77 | -1.267 | -9.7 | 0.9468 |
| RTSS A | Glnd_Thyroid | 15.99 | 13.54 | -2.452 | -15.3 | 0.9147 |
| RTSS A | Larynx | 18.46 | 17.18 | -1.286 | -7.0 | 0.9616 |
| RTSS A | Lens_L | 0.284 | 0.1794 | -0.1046 | -36.8 | 0.7742 |
| RTSS A | Lens_R | 0.3065 | 0.1831 | -0.1233 | -40.2 | 0.7481 |
| RTSS A | Lips | 35.52 | 32.4 | -3.124 | -8.8 | 0.9505 |
| RTSS A | LN_Neck_L | 180.2 | 167.9 | -12.22 | -6.8 | 0.9618 |
| RTSS A | LN_Neck_R | 196 | 183.9 | -12.13 | -6.2 | 0.9651 |
| RTSS A | Musc_Constrict | 35.27 | 28.63 | -6.641 | -18.8 | 0.8920 |
| RTSS A | OpticChiasm | 0.9979 | 0.8185 | -0.1794 | -18.0 | 0.9012 |
| RTSS A | OpticNrv_L | 0.6279 | 0.4672 | -0.1607 | -25.6 | 0.8191 |
| RTSS A | OpticNrv_R | 0.7736 | 0.5494 | -0.2242 | -29.0 | 0.8305 |
| RTSS A | Parotid_L | 43.63 | 40.61 | -3.012 | -6.9 | 0.9615 |
| RTSS A | Parotid_R | 43.03 | 39.93 | -3.102 | -7.2 | 0.9597 |
| RTSS A | Pituitary | 0.4447 | 0.3588 | -0.08596 | -19.3 | 0.8930 |
| RTSS A | SpinalCord | 32.5 | 26.16 | -6.342 | -19.5 | 0.8910 |
| RTSS B | Cavity_Oral_Experimental | 132.2 | 122.9 | -9.272 | -7.0 | 0.9637 |
| RTSS B | Glnd_Thyroid_Experimental | 20.57 | 16.19 | -4.38 | -21.3 | 0.8808 |
| RTSS B | Brainstem_Experimental | 29.4 | 25.75 | -3.651 | -12.4 | 0.9338 |
| RTSS B | SpinalCord_Experimental | 34.08 | 26.13 | -7.953 | -23.3 | 0.8679 |
| RTSS B | OpticChiasm_Experimental | 0.2579 | 0.1345 | -0.1233 | -47.8 | 0.6857 |
| RTSS B | Pituitary_Experimental | 0.2654 | 0.1532 | -0.1121 | -42.3 | 0.7321 |
| RTSS B | Bone_Mandible_Experimental | 85.99 | 72.06 | -13.93 | -16.2 | 0.9119 |
| RTSS B | Brain_Experimental | 1426 | 1384 | -42.14 | -3.0 | 0.9850 |
| RTSS B | Lips_Experimental | 46.04 | 38.2 | -7.845 | -17.0 | 0.9069 |
| RTSS B | Glnd_Submand_L_Experimental | 9.127 | 7.467 | -1.659 | -18.2 | 0.9000 |
| RTSS B | Glnd_Submand_R_Experimental | 8.368 | 7.105 | -1.263 | -15.1 | 0.9184 |
| RTSS B | BrachialPlex_L_Experimental | 13.21 | 9.762 | -3.446 | -26.1 | 0.8500 |
| RTSS B | BrachialPlex_R_Experimental | 9.777 | 7.03 | -2.747 | -28.1 | 0.8366 |
| RTSS B | Lens_L_Experimental | 0.3214 | 0.2018 | -0.1196 | -37.2 | 0.7714 |
| RTSS B | Lens_R_Experimental | 0.3027 | 0.1831 | -0.1196 | -39.5 | 0.7538 |
| RTSS B | Cochlea_L_Experimental | 0.2242 | 0.1345 | -0.0897 | -40.0 | 0.7500 |
| RTSS B | Cochlea_R_Experimental | 0.1757 | 0.1046 | -0.07101 | -40.4 | 0.7467 |
| RTSS B | Eye_L_Experimental | 9.915 | 8.667 | -1.248 | -12.6 | 0.9328 |
| RTSS B | Eye_R_Experimental | 9.912 | 8.596 | -1.316 | -13.3 | 0.9289 |
| RTSS B | Glnd_Lacrimal_L_Experimental | 0.5905 | 0.3364 | -0.2541 | -43.0 | 0.7258 |
| RTSS B | Glnd_Lacrimal_R_Experimental | 0.5905 | 0.3438 | -0.2467 | -41.8 | 0.7360 |
| RTSS B | Parotid_L_Experimental | 37.81 | 33.66 | -4.148 | -11.0 | 0.9420 |
| RTSS B | Parotid_R_Experimental | 31.46 | 27.58 | -3.879 | -12.3 | 0.9343 |
| RTSS B | OpticNrv_R_Experimental | 0.6279 | 0.3476 | -0.2803 | -44.6 | 0.7126 |
| RTSS B | OpticNrv_L_Experimental | 0.4971 | 0.299 | -0.1981 | -39.8 | 0.7512 |
| RTSS C | A_Carotid_L | 5.871 | 4.773 | -1.099 | -18.7 | 0.8968 |
| RTSS C | A_Carotid_R | 9.485 | 7.538 | -1.947 | -20.5 | 0.8852 |
| RTSS C | Arytenoid_L | 0.3812 | 0.2915 | -0.0897 | -23.5 | 0.8667 |
| RTSS C | Arytenoid_R | 0.3065 | 0.2579 | -0.04859 | -15.9 | 0.9139 |
| RTSS C | Body | 1.812e+04 | 1.808e+04 | -41.66 | -0.2 | 0.9975 |
| RTSS C | Bone_Mandible | 66.7 | 62.83 | -3.868 | -5.8 | 0.9636 |
| RTSS C | BrachialPlex_L | 14.83 | 13.17 | -1.659 | -11.2 | 0.9234 |
| RTSS C | BrachialPlex_R | 17.72 | 15.88 | -1.835 | -10.4 | 0.9271 |
| RTSS C | Brain | 1335 | 1322 | -13.07 | -1.0 | 0.9935 |
| RTSS C | Brainstem | 30.5 | 29.36 | -1.144 | -3.7 | 0.9779 |
| RTSS C | Buccal_Mucosa_L | 8.966 | 8.439 | -0.527 | -5.9 | 0.9641 |
| RTSS C | Buccal_Mucosa_R | 13.52 | 12.54 | -0.9792 | -7.2 | 0.9581 |
| RTSS C | Cavity_Oral | 117.7 | 115 | -2.777 | -2.4 | 0.9849 |
| RTSS C | Cochlea_L | 0.06727 | 0.05232 | -0.01495 | -22.2 | 0.8750 |
| RTSS C | Cochlea_R | 0.07848 | 0.0598 | -0.01869 | -23.8 | 0.8649 |
| RTSS C | Cricophar_inlet | 3.655 | 3.046 | -0.6092 | -16.7 | 0.9035 |
| RTSS C | Esophagus_S | 2.369 | 2.186 | -0.1831 | -7.7 | 0.9565 |
| RTSS C | Eye_Ant_L | 0.9231 | 0.8446 | -0.07848 | -8.5 | 0.9429 |
| RTSS C | Eye_Ant_R | 0.9904 | 0.8671 | -0.1233 | -12.5 | 0.9296 |
| RTSS C | Eye_L | 8.151 | 7.785 | -0.3663 | -4.5 | 0.9751 |
| RTSS C | Eye_Post_L | 7.008 | 6.529 | -0.4784 | -6.8 | 0.9619 |
| RTSS C | Eye_Post_R | 6.955 | 6.563 | -0.3924 | -5.6 | 0.9677 |
| RTSS C | Eye_R | 8.162 | 7.83 | -0.3326 | -4.1 | 0.9745 |
| RTSS C | Glnd_Lacrimal_L | 0.37 | 0.2953 | -0.07475 | -20.2 | 0.8764 |
| RTSS C | Glnd_Lacrimal_R | 0.3999 | 0.2728 | -0.1271 | -31.8 | 0.8111 |
| RTSS C | Glnd_Submand_L | 9.448 | 8.958 | -0.4896 | -5.2 | 0.9665 |
| RTSS C | Glnd_Submand_R | 12.61 | 12.05 | -0.5606 | -4.4 | 0.9736 |
| RTSS C | Glnd_Thyroid | 16.2 | 14.8 | -1.394 | -8.6 | 0.9490 |
| RTSS C | Glottis | 4.362 | 3.861 | -0.5008 | -11.5 | 0.9282 |
| RTSS C | LN_Neck_IA | 2.026 | 1.701 | -0.3252 | -16.1 | 0.9027 |
| RTSS C | LN_Neck_IB_L | 27.68 | 26.21 | -1.469 | -5.3 | 0.9648 |
| RTSS C | LN_Neck_IB_R | 29.65 | 28.09 | -1.562 | -5.3 | 0.9638 |
| RTSS C | LN_Neck_III_L | 25.48 | 23.71 | -1.772 | -7.0 | 0.9606 |
| RTSS C | LN_Neck_III_R | 31.69 | 29.91 | -1.783 | -5.6 | 0.9663 |
| RTSS C | LN_Neck_II_L | 31.54 | 29.07 | -2.474 | -7.8 | 0.9552 |
| RTSS C | LN_Neck_II_R | 34.59 | 32.43 | -2.16 | -6.2 | 0.9632 |
| RTSS C | LN_Neck_IVA_L | 15.63 | 14.64 | -0.9867 | -6.3 | 0.9634 |
| RTSS C | LN_Neck_IVA_R | 14.64 | 13.91 | -0.7325 | -5.0 | 0.9694 |
| RTSS C | LN_Neck_IVB_L | 25.08 | 24.09 | -0.9941 | -4.0 | 0.9772 |
| RTSS C | LN_Neck_IVB_R | 30.06 | 29.11 | -0.9493 | -3.2 | 0.9793 |
| RTSS C | LN_Neck_IX_L | 3.992 | 3.517 | -0.4746 | -11.9 | 0.9308 |
| RTSS C | LN_Neck_IX_R | 5.864 | 5.322 | -0.5419 | -9.2 | 0.9435 |
| RTSS C | LN_Neck_VC_L | 17.14 | 16.35 | -0.7886 | -4.6 | 0.9740 |
| RTSS C | LN_Neck_VC_R | 11.18 | 10.88 | -0.2953 | -2.6 | 0.9819 |
| RTSS C | LN_Neck_VIA | 29.84 | 26.5 | -3.341 | -11.2 | 0.9285 |
| RTSS C | LN_Neck_VIB | 23.98 | 21.54 | -2.441 | -10.2 | 0.9423 |
| RTSS C | LN_Neck_VIIA_L | 3.685 | 3.024 | -0.6615 | -18.0 | 0.8958 |
| RTSS C | LN_Neck_VIIA_R | 3.476 | 2.908 | -0.5681 | -16.3 | 0.9075 |
| RTSS C | LN_Neck_VIIB_L | 3.741 | 3.352 | -0.3887 | -10.4 | 0.9410 |
| RTSS C | LN_Neck_VIIB_R | 6.028 | 5.386 | -0.6428 | -10.7 | 0.9384 |
| RTSS C | LN_Neck_V_L | 24.27 | 22.73 | -1.544 | -6.4 | 0.9640 |
| RTSS C | LN_Neck_V_R | 26.35 | 24.8 | -1.544 | -5.9 | 0.9665 |
| RTSS C | LN_Neck_XA_L | 14.47 | 12.46 | -2.007 | -13.9 | 0.9172 |
| RTSS C | LN_Neck_XA_R | 14.67 | 12.93 | -1.742 | -11.9 | 0.9206 |
| RTSS C | LN_Neck_XB_L | 10.29 | 9.538 | -0.755 | -7.3 | 0.9536 |
| RTSS C | LN_Neck_XB_R | 9.624 | 8.828 | -0.7961 | -8.3 | 0.9492 |
| RTSS C | Larynx_SG | 17.18 | 15.85 | -1.334 | -7.8 | 0.9499 |
| RTSS C | Lens_L | 0.284 | 0.2579 | -0.02616 | -9.2 | 0.9517 |
| RTSS C | Lens_R | 0.3027 | 0.2691 | -0.03364 | -11.1 | 0.9412 |
| RTSS C | Lips | 45.55 | 42.89 | -2.654 | -5.8 | 0.9628 |
| RTSS C | Lung_L | 857.4 | 847.5 | -9.889 | -1.2 | 0.9921 |
| RTSS C | Lung_R | 1010 | 1000 | -10.02 | -1.0 | 0.9930 |
| RTSS C | Musc_Constrict | 17.4 | 14.52 | -2.878 | -16.5 | 0.8998 |
| RTSS C | OpticChiasm | 0.2018 | 0.1532 | -0.04859 | -24.1 | 0.8632 |
| RTSS C | OpticChiasm_cnv | 0.8559 | 0.7437 | -0.1121 | -13.1 | 0.9252 |
| RTSS C | OpticNrv_L | 0.654 | 0.5382 | -0.1159 | -17.7 | 0.8966 |
| RTSS C | OpticNrv_R | 0.6727 | 0.5569 | -0.1159 | -17.2 | 0.8997 |
| RTSS C | OpticNrv_cnv_L | 0.583 | 0.4672 | -0.1159 | -19.9 | 0.8897 |
| RTSS C | OpticNrv_cnv_R | 0.6727 | 0.5606 | -0.1121 | -16.7 | 0.9091 |
| RTSS C | Parotid_L | 45.31 | 43.37 | -1.936 | -4.3 | 0.9744 |
| RTSS C | Parotid_R | 42.53 | 40.6 | -1.932 | -4.5 | 0.9734 |
| RTSS C | Pituitary | 0.2467 | 0.1869 | -0.0598 | -24.2 | 0.8621 |
| RTSS C | SpinalCanal | 68.36 | 64.71 | -3.644 | -5.3 | 0.9682 |
| RTSS C | SpinalCord | 25.32 | 22.87 | -2.459 | -9.7 | 0.9482 |
| RTSS D | Cochlea_R | 0.09717 | 0.06354 | -0.03364 | -34.6 | 0.7907 |
| RTSS D | Glnd_Submand_L | 9.732 | 8.906 | -0.826 | -8.5 | 0.9557 |
| RTSS D | Lens_R | 0.3476 | 0.3102 | -0.03737 | -10.8 | 0.9432 |
| RTSS D | LN_Neck_III_L | 28.06 | 25.74 | -2.317 | -8.3 | 0.9569 |
| RTSS D | Glnd_Thyroid | 16.2 | 14.61 | -1.592 | -9.8 | 0.9483 |
| RTSS D | Lips | 13.08 | 12.21 | -0.8671 | -6.6 | 0.9657 |
| RTSS D | LN_Neck_VIIB_L | 7.449 | 6.765 | -0.6839 | -9.2 | 0.9519 |
| RTSS D | Brainstem | 34.1 | 31.85 | -2.246 | -6.6 | 0.9659 |
| RTSS D | Trachea | 40.18 | 36.46 | -3.719 | -9.3 | 0.9515 |
| RTSS D | Lens_L | 0.3887 | 0.3438 | -0.04485 | -11.5 | 0.9388 |
| RTSS D | BrachialPlex_R | 28.21 | 26.45 | -1.76 | -6.2 | 0.9678 |
| RTSS D | LN_Neck_II-IV_L | 106 | 97.98 | -8.043 | -7.6 | 0.9606 |
| RTSS D | LN_Neck_IV_R | 38.1 | 36.28 | -1.816 | -4.8 | 0.9756 |
| RTSS D | LN_Neck_II-V_R | 164.4 | 153.4 | -11.07 | -6.7 | 0.9652 |
| RTSS D | Larynx | 32.71 | 30.48 | -2.235 | -6.8 | 0.9646 |
| RTSS D | LN_Neck_IV_L | 36.62 | 34.73 | -1.884 | -5.1 | 0.9736 |
| RTSS D | LN_Neck_II_R | 46.81 | 42.4 | -4.414 | -9.4 | 0.9505 |
| RTSS D | LN_Neck_II-IV_R | 117.9 | 108.8 | -9.078 | -7.7 | 0.9600 |
| RTSS D | LN_Neck_IB-V_R | 182.7 | 170.4 | -12.31 | -6.7 | 0.9652 |
| RTSS D | LN_Neck_IA | 1.839 | 1.723 | -0.1159 | -6.3 | 0.9675 |
| RTSS D | LN_Neck_VIIB_R | 8.29 | 7.587 | -0.7026 | -8.5 | 0.9557 |
| RTSS D | LN_Neck_VIIA_L | 5.535 | 5.176 | -0.3588 | -6.5 | 0.9665 |
| RTSS D | Glnd_Submand_R | 10.39 | 9.474 | -0.9119 | -8.8 | 0.9541 |
| RTSS D | Bone_Mandible | 82.62 | 74.82 | -7.8 | -9.4 | 0.9505 |
| RTSS D | LN_Neck_VIA | 41.59 | 37.93 | -3.651 | -8.8 | 0.9541 |
| RTSS D | Cochlea_L | 0.09343 | 0.07475 | -0.01869 | -20.0 | 0.8889 |
| RTSS D | LN_Neck_III_R | 32.97 | 30.13 | -2.848 | -8.6 | 0.9549 |
| RTSS D | LN_Neck_VIIA_R | 6.051 | 5.602 | -0.4485 | -7.4 | 0.9615 |
| RTSS D | Parotid_R | 37.67 | 35.6 | -2.067 | -5.5 | 0.9718 |
| RTSS D | Cavity_Oral | 75.76 | 71.96 | -3.797 | -5.0 | 0.9743 |
| RTSS D | Musc_Constrict | 25 | 21.67 | -3.326 | -13.3 | 0.9287 |
| RTSS D | Esophagus | 47.57 | 44.84 | -2.725 | -5.7 | 0.9705 |
| RTSS D | SpinalCord | 49.16 | 45.1 | -4.055 | -8.2 | 0.9570 |
| RTSS D | LN_Neck_IB-V_L | 171.1 | 159.7 | -11.34 | -6.6 | 0.9657 |
| RTSS D | BrachialPlex_L | 28.01 | 26.1 | -1.91 | -6.8 | 0.9647 |
| RTSS D | OpticNrv_L | 1.14 | 0.8932 | -0.2467 | -21.6 | 0.8787 |
| RTSS D | Eye_L | 8.723 | 8.204 | -0.5195 | -6.0 | 0.9693 |
| RTSS D | LN_Neck_II_L | 41.35 | 37.5 | -3.842 | -9.3 | 0.9513 |
| RTSS D | LN_Neck_II-V_L | 159 | 148.9 | -10.14 | -6.4 | 0.9671 |
| RTSS D | OpticNrv_R | 1.203 | 0.9642 | -0.2392 | -19.9 | 0.8897 |
| RTSS D | Eye_R | 8.506 | 7.912 | -0.5942 | -7.0 | 0.9638 |
| RTSS D | OpticChiasm | 1.02 | 0.8745 | -0.1458 | -14.3 | 0.9231 |
| RTSS D | Parotid_L | 39.86 | 37.61 | -2.246 | -5.6 | 0.9710 |
| RTSS E | Humerus_Head_L | 74.43 | 70.8 | -3.622 | -4.9 | 0.9751 |
| RTSS E | LN_Neck_VIIB_R | 6.507 | 5.685 | -0.8222 | -12.6 | 0.9326 |
| RTSS E | Atrium_R | 0.06354 | 0.04859 | -0.01495 | -23.5 | 0.8667 |
| RTSS E | Submand_Gland_L | 10.47 | 9.519 | -0.9493 | -9.1 | 0.9525 |
| RTSS E | Eyeball_L | 8.962 | 8.271 | -0.6914 | -7.7 | 0.9599 |
| RTSS E | Eyeball_R | 8.929 | 8.237 | -0.6914 | -7.7 | 0.9597 |
| RTSS E | Atrium_L | 0.04859 | 0.04859 | +0 | +0.0 | 1.0000 |
| RTSS E | Lens_Eye_L | 0.284 | 0.2504 | -0.03364 | -11.8 | 0.9371 |
| RTSS E | Optic_Nerve_R | 0.7213 | 0.6092 | -0.1121 | -15.5 | 0.9157 |
| RTSS E | LN_Neck_IB_L | 26.58 | 24.16 | -2.418 | -9.1 | 0.9523 |
| RTSS E | L_Main_Coro_Art | 0.04111 | 0.03364 | -0.007475 | -18.2 | 0.9000 |
| RTSS E | Optic_Nerve_L | 0.6877 | 0.5905 | -0.09717 | -14.1 | 0.9240 |
| RTSS E | Parotid_L | 40.77 | 37.96 | -2.811 | -6.9 | 0.9643 |
| RTSS E | LN_Neck_VIIB_L | 4.089 | 3.614 | -0.4746 | -11.6 | 0.9384 |
| RTSS E | Lens_Eye_R | 0.2953 | 0.2579 | -0.03737 | -12.7 | 0.9324 |
| RTSS E | Parotid_R | 40.38 | 37.53 | -2.852 | -7.1 | 0.9634 |
| RTSS E | LN_Neck_IB_R | 29.34 | 26.81 | -2.53 | -8.6 | 0.9549 |
| RTSS E | LN_Neck_IVB_R | 25.55 | 23.67 | -1.884 | -7.4 | 0.9617 |
| RTSS E | Optic_Chiasm | 0.8334 | 0.7213 | -0.1121 | -13.5 | 0.9279 |
| RTSS E | LN_Neck_IVB_L | 18.81 | 17.41 | -1.402 | -7.5 | 0.9613 |
| RTSS E | Humerus_Head_R | 77.56 | 73.52 | -4.044 | -5.2 | 0.9732 |
| RTSS E | Bronchia_L | 1.207 | 1.061 | -0.1458 | -12.1 | 0.9357 |
| RTSS E | Bronchia_R | 0.4373 | 0.3663 | -0.07101 | -16.2 | 0.9116 |
| RTSS E | Lips | 26.09 | 22.51 | -3.573 | -13.7 | 0.9265 |
| RTSS E | Bronchus_L | 6.477 | 5.703 | -0.7736 | -11.9 | 0.9365 |
| RTSS E | Bronchus_R | 7.176 | 6.331 | -0.8446 | -11.8 | 0.9375 |
| RTSS E | Cerebellum | 115.5 | 109.9 | -5.58 | -4.8 | 0.9752 |
| RTSS E | Submand_Gland_R | 14.86 | 13.74 | -1.114 | -7.5 | 0.9611 |
| RTSS E | Cochlea_R | 0.5382 | 0.4746 | -0.06354 | -11.8 | 0.9373 |
| RTSS E | Carina | 4.444 | 3.977 | -0.4672 | -10.5 | 0.9445 |
| RTSS E | Cochlea_L | 0.4634 | 0.4074 | -0.05606 | -12.1 | 0.9356 |
| RTSS E | TM_Joint_R | 2.908 | 2.612 | -0.2953 | -10.2 | 0.9465 |
| RTSS E | Pituitary_Gland | 0.5158 | 0.4784 | -0.03737 | -7.2 | 0.9624 |
| RTSS E | TM_Joint_L | 2.896 | 2.616 | -0.2803 | -9.7 | 0.9492 |
| RTSS E | Aorte_Thx_Asc | 4.989 | 4.496 | -0.4933 | -9.9 | 0.9480 |
| RTSS E | LAD_Coronary | 0.8073 | 0.6765 | -0.1308 | -16.2 | 0.9118 |
| RTSS E | LN_Neck_IA | 1.738 | 1.454 | -0.284 | -16.3 | 0.9110 |
| RTSS E | LN_Neck_VIIA_R | 6.791 | 5.662 | -1.129 | -16.6 | 0.9094 |
| RTSS E | LN_Neck_III_R | 27.49 | 24.58 | -2.911 | -10.6 | 0.9441 |
| RTSS E | LN_Neck_IVA_R | 18.36 | 16.58 | -1.779 | -9.7 | 0.9491 |
| RTSS E | LN_Neck_II_R | 45.07 | 40.26 | -4.806 | -10.7 | 0.9437 |
| RTSS E | LN_Neck_V_R | 20.57 | 18.64 | -1.932 | -9.4 | 0.9507 |
| RTSS E | LN_Neck_VIIA_L | 7.168 | 5.871 | -1.297 | -18.1 | 0.9005 |
| RTSS E | Thyroid_Gland | 18.93 | 16.7 | -2.227 | -11.8 | 0.9375 |
| RTSS E | LN_Neck_V_L | 25.49 | 23.27 | -2.22 | -8.7 | 0.9545 |
| RTSS E | LN_Neck_IVA_L | 16.22 | 14.69 | -1.525 | -9.4 | 0.9507 |
| RTSS E | LN_Neck_II_L | 39.02 | 34.21 | -4.806 | -12.3 | 0.9344 |
| RTSS E | LN_Neck_III_L | 25.95 | 23.01 | -2.941 | -11.3 | 0.9399 |
| RTSS E | Oral_Cavity | 113.2 | 107.3 | -5.864 | -5.2 | 0.9734 |
| RTSS E | Trachea | 24.53 | 22.39 | -2.145 | -8.7 | 0.9543 |
| RTSS E | Larynx | 57.27 | 53.39 | -3.876 | -6.8 | 0.9650 |
| RTSS E | Brachial_Plex_R | 14.67 | 12.74 | -1.921 | -13.1 | 0.9299 |
| RTSS E | Brachial_Plex_L | 16.25 | 13.78 | -2.467 | -15.2 | 0.9178 |
| RTSS E | Lung_R | 1004 | 978.1 | -25.87 | -2.6 | 0.9869 |
| RTSS E | Lung_L | 863 | 837.6 | -25.37 | -2.9 | 0.9851 |
| RTSS E | Mandible | 75.89 | 68.14 | -7.755 | -10.2 | 0.9462 |
| RTSS E | Chestwall_L | 901.4 | 856.9 | -44.44 | -4.9 | 0.9746 |
| RTSS E | Esophagus | 46.12 | 41.63 | -4.485 | -9.7 | 0.9489 |
| RTSS E | Chestwall_L (1) | 898.6 | 854.4 | -44.16 | -4.9 | 0.9746 |
| RTSS E | Pericardium | 358.1 | 346.9 | -11.18 | -3.1 | 0.9841 |
| RTSS E | Aorte_Thx_Desc | 122.3 | 115.3 | -7.034 | -5.8 | 0.9704 |
| RTSS E | Brain | 1371 | 1340 | -30.66 | -2.2 | 0.9887 |
| RTSS E | Spinal_Canal | 63.82 | 58.42 | -5.401 | -8.5 | 0.9558 |
| RTSS E | Spinal_Cord | 34.72 | 31.68 | -3.038 | -8.8 | 0.9542 |
| RTSS E | Brainstem | 32.49 | 30.23 | -2.257 | -6.9 | 0.9640 |
| RTSS E | Pulmonary_Arter | 101.7 | 95.92 | -5.759 | -5.7 | 0.9709 |
| RTSS E | Bronchial_Tree | 19.75 | 17.44 | -2.313 | -11.7 | 0.9378 |
| RTSS F | SubmanG_L | 7.142 | 6.051 | -1.091 | -15.3 | 0.9150 |
| RTSS F | Spinal Cord | 30.49 | 25.62 | -4.866 | -16.0 | 0.9107 |
| RTSS F | SC+5mm | 102.7 | 94.33 | -8.39 | -8.2 | 0.9560 |
| RTSS F | Pharyngeal con | 21.27 | 16.15 | -5.12 | -24.1 | 0.8610 |
| RTSS F | Parotid_R | 39.83 | 36.42 | -3.412 | -8.6 | 0.9526 |
| RTSS F | Parotid_L | 42.64 | 39.46 | -3.188 | -7.5 | 0.9594 |
| RTSS F | Oral cavity | 141.3 | 134.2 | -7.123 | -5.0 | 0.9728 |
| RTSS F | OpticNerve_R | 1.334 | 1.043 | -0.2915 | -21.8 | 0.8742 |
| RTSS F | OpticNerve_L | 1.155 | 0.8745 | -0.2803 | -24.3 | 0.8545 |
| RTSS F | Oesophagus | 20.83 | 18.23 | -2.605 | -12.5 | 0.9306 |
| RTSS F | Metal | 4.941 | 4.5 | -0.441 | -8.9 | 0.9533 |
| RTSS F | Mandible | 105 | 95.49 | -9.545 | -9.1 | 0.9520 |
| RTSS F | Lens_R | 0.3625 | 0.2392 | -0.1233 | -34.0 | 0.7950 |
| RTSS F | Lens_L | 0.3289 | 0.2242 | -0.1046 | -31.8 | 0.8108 |
| RTSS F | Larynx | 25.69 | 23.07 | -2.627 | -10.2 | 0.9423 |
| RTSS F | Eye_R | 7.516 | 6.63 | -0.8858 | -11.8 | 0.9347 |
| RTSS F | Eye_L | 8.626 | 7.684 | -0.9418 | -10.9 | 0.9413 |
| RTSS F | External | 2.761e+04 | 2.714e+04 | -466.8 | -1.7 | 0.9912 |
| RTSS F | BrainstemPRV | 61.09 | 57.23 | -3.857 | -6.3 | 0.9665 |
| RTSS F | Brainstem | 23.05 | 20.95 | -2.1 | -9.1 | 0.9507 |
| RTSS F | BBs | 0.5868 | 0.3663 | -0.2205 | -37.6 | 0.7686 |
| RTSS F | Artefact | 90.65 | 85.14 | -5.509 | -6.1 | 0.9549 |
| RTSS F | 3PTV_ | 723.9 | 695 | -28.89 | -4.0 | 0.9788 |
| RTSS F | 3GTV_ | 3.558 | 3.072 | -0.4859 | -13.7 | 0.9256 |
| RTSS F | 3CTV_ | 396.6 | 374.4 | -22.22 | -5.6 | 0.9697 |
| RTSS F | 2GTV_ | 15.3 | 13.79 | -1.506 | -9.8 | 0.9451 |
| RTSS F | 2CTV_ | 34.04 | 31.54 | -2.508 | -7.4 | 0.9598 |
| RTSS F | 1PTV_ | 403.6 | 388.5 | -15.05 | -3.7 | 0.9803 |
| RTSS F | 1GTV_ | 195.1 | 186.8 | -8.372 | -4.3 | 0.9771 |
| RTSS F | 1CTV_ | 229.2 | 218.3 | -10.88 | -4.7 | 0.9746 |
| RTSS F | Board | 1284 | 1314 | +30.06 | +2.3 | 0.8571 |
| RTSS F | CouchInterior | 9050 | 8777 | -272.9 | -3.0 | 0.9847 |
| RTSS F | CouchSurface | 2133 | 1883 | -250.3 | -11.7 | 0.9233 |
| RTSS F | BODY | 1.847e+04 | 1.829e+04 | -180.3 | -1.0 | 0.9949 |
| RTSS F | BODY -1.0 | 1.473e+04 | 1.457e+04 | -162.7 | -1.1 | 0.9942 |
| RTSS F | BODY -1.5 | 1.31e+04 | 1.295e+04 | -153.4 | -1.2 | 0.9939 |
| RTSS F | Cochlea_L | 0.4746 | 0.3962 | -0.07848 | -16.5 | 0.9013 |
| RTSS F | Cochlea_R | 0.8147 | 0.6466 | -0.1682 | -20.6 | 0.8849 |
| RTSS F | OpticNervePRV | 9.893 | 8.723 | -1.17 | -11.8 | 0.9344 |
| RTSS F | Metal1 | 0.3663 | 0.3065 | -0.0598 | -16.3 | 0.9111 |
| RTSS F | Ring 48Gy | 229.1 | 208 | -21.03 | -9.2 | 0.9128 |
| RTSS F | Ring 55Gy | 229.9 | 225.5 | -4.362 | -1.9 | 0.9230 |
| RTSS F | Dose 58.85[Gy] | 1.712 | 0.9082 | -0.8035 | -46.9 | 0.6876 |
| RTSS G | Body | 1.846e+04 | 1.821e+04 | -251.8 | -1.4 | 0.9931 |
| RTSS G | Skeleton | 2244 | 2008 | -235.8 | -10.5 | 0.9313 |
| RTSS G | Spinal Cord | 25.46 | 20.87 | -4.59 | -18.0 | 0.9009 |
| RTSS G | Brain | 1393 | 1356 | -37.52 | -2.7 | 0.9843 |
| RTSS G | Brainstem | 23.9 | 21.39 | -2.515 | -10.5 | 0.9445 |
| RTSS G | Eye Globe Left | 8.689 | 7.718 | -0.9717 | -11.2 | 0.9408 |
| RTSS G | Eye Globe Right | 9.071 | 8.017 | -1.054 | -11.6 | 0.9383 |
| RTSS G | Glottis | 4.197 | 3.166 | -1.032 | -24.6 | 0.8599 |
| RTSS G | Lens Left | 0.3102 | 0.2392 | -0.07101 | -22.9 | 0.8707 |
| RTSS G | Lens Right | 0.2878 | 0.228 | -0.0598 | -20.8 | 0.8841 |
| RTSS G | Level Ia: Submental triangle | 5.255 | 4.264 | -0.9904 | -18.8 | 0.8960 |
| RTSS G | Level Ib Left: Submandibular triangle | 25.85 | 22.58 | -3.27 | -12.7 | 0.9325 |
| RTSS G | Level Ib Right: Submandibular triangle | 29.34 | 25.71 | -3.636 | -12.4 | 0.9339 |
| RTSS G | Level II Left: Upper jugular nodes | 42.3 | 36.55 | -5.748 | -13.6 | 0.9271 |
| RTSS G | Level II Right: Upper jugular nodes | 45.34 | 38.73 | -6.611 | -14.6 | 0.9214 |
| RTSS G | Level III Left: Middle jugular nodes | 25 | 21.23 | -3.771 | -15.1 | 0.9184 |
| RTSS G | Level III Right: Middle jugular nodes | 31.59 | 26.94 | -4.649 | -14.7 | 0.9206 |
| RTSS G | Level IVa Left: Lower jugular group | 12.71 | 11.01 | -1.704 | -13.4 | 0.9281 |
| RTSS G | Level IVa Right: Lower jugular group | 16.23 | 14.06 | -2.164 | -13.3 | 0.9286 |
| RTSS G | Level IVb Left: Medial supraclavicular group | 18.39 | 16.18 | -2.205 | -12.0 | 0.9362 |
| RTSS G | Level IVb Right: Medial supraclavicular group | 24.4 | 22 | -2.403 | -9.8 | 0.9482 |
| RTSS G | Level V Left: Posterior triangle group | 39.97 | 35.28 | -4.69 | -11.7 | 0.9377 |
| RTSS G | Level V Right: Posterior triangle group | 22.01 | 19.09 | -2.919 | -13.3 | 0.9290 |
| RTSS G | Level Vc Left: Lateral supraclavicular group | 12.12 | 10.86 | -1.263 | -10.4 | 0.9450 |
| RTSS G | Level Vc Right: Lateral supraclavicular group | 16.31 | 14.39 | -1.921 | -11.8 | 0.9374 |
| RTSS G | Level VIa: Anterior jugular nodes | 28.8 | 21.19 | -7.609 | -26.4 | 0.8478 |
| RTSS G | Level VIb: Prelaryngeal, pretracheal, & paratracheal nodes | 16.97 | 13.84 | -3.136 | -18.5 | 0.8982 |
| RTSS G | Level VIIa Left: Retropharyngeal nodes | 0.6167 | 0.4709 | -0.1458 | -23.6 | 0.8660 |
| RTSS G | Level VIIa Right: Retropharyngeal nodes | 1.461 | 1.106 | -0.3551 | -24.3 | 0.8617 |
| RTSS G | Level VIIb Left: Retro-styloid nodes | 4.283 | 3.547 | -0.7363 | -17.2 | 0.9060 |
| RTSS G | Level VIIb Right: Retro-styloid nodes | 4.664 | 3.823 | -0.8409 | -18.0 | 0.9009 |
| RTSS G | Level VIII Left: Parotid group | 49.91 | 45.02 | -4.888 | -9.8 | 0.9485 |
| RTSS G | Level VIII Right: Parotid group | 45.88 | 40.8 | -5.079 | -11.1 | 0.9414 |
| RTSS G | Level IX Left: Bucco-facial group | 5.834 | 4.601 | -1.233 | -21.1 | 0.8818 |
| RTSS G | Level IX Right: Bucco-facial group | 5.341 | 4.092 | -1.248 | -23.4 | 0.8677 |
| RTSS G | Level Xa Left: Retroauricular & subauricular nodes | 7.8 | 5.961 | -1.839 | -23.6 | 0.8664 |
| RTSS G | Level Xa Right: Retroauricular & subauricular nodes | 9.437 | 7.535 | -1.902 | -20.2 | 0.8879 |
| RTSS G | Level Xb Left: Occipital nodes | 9.706 | 7.579 | -2.127 | -21.9 | 0.8770 |
| RTSS G | Level Xb Right: Occipital nodes | 10.56 | 8.495 | -2.063 | -19.5 | 0.8917 |
| RTSS G | Lips | 63.24 | 54.32 | -8.921 | -14.1 | 0.9241 |
| RTSS G | Optic Chiasm | 0.4111 | 0.3139 | -0.09717 | -23.6 | 0.8660 |
| RTSS G | Optic Nerve Left | 0.8895 | 0.6578 | -0.2317 | -26.1 | 0.8502 |
| RTSS G | Optic Nerve Right | 1.177 | 0.9119 | -0.2654 | -22.5 | 0.8730 |
| RTSS G | Mandible | 68.77 | 58.92 | -9.848 | -14.3 | 0.9229 |
| RTSS G | Oral Cavity | 146.9 | 137.5 | -9.426 | -6.4 | 0.9669 |
| RTSS G | Aorta | 113.9 | 105.6 | -8.357 | -7.3 | 0.9619 |
| RTSS G | Esophagus | 50.17 | 43.69 | -6.481 | -12.9 | 0.9310 |
| RTSS G | Female Breast Left | 65.1 | 58.52 | -6.585 | -10.1 | 0.9467 |
| RTSS G | Female Breast Right | 371.7 | 347.2 | -24.51 | -6.6 | 0.9659 |
| RTSS G | Lung Lobe Left Lower | 84.27 | 77.59 | -6.679 | -7.9 | 0.9587 |
| RTSS G | Lung Lobe Left Upper | 778.9 | 750.2 | -28.66 | -3.7 | 0.9811 |
| RTSS G | Lung Lobe Right Lower | 142.8 | 134.2 | -8.633 | -6.0 | 0.9646 |
| RTSS G | Lung Lobe Right Middle | 6.679 | 5.468 | -1.211 | -18.1 | 0.9003 |
| RTSS G | Lung Lobe Right Upper | 864 | 830.8 | -33.26 | -3.8 | 0.9803 |
| RTSS G | Lung Left | 859.7 | 827.7 | -32.01 | -3.7 | 0.9810 |
| RTSS G | Lung Right | 1005 | 970.4 | -34.84 | -3.5 | 0.9823 |
| RTSS G | Brachial Plexus Left | 24.59 | 19.97 | -4.619 | -18.8 | 0.8963 |
| RTSS G | Brachial Plexus Right | 22.67 | 18.48 | -4.193 | -18.5 | 0.8981 |
| RTSS G | Ribs | 313.8 | 261.8 | -51.98 | -16.6 | 0.9097 |
| RTSS G | Sternum | 51.34 | 46 | -5.348 | -10.4 | 0.9451 |
| RTSS G | Rib Left 1 | 14.91 | 12.7 | -2.209 | -14.8 | 0.9200 |
| RTSS G | Rib Left 2 | 20.18 | 16.96 | -3.225 | -16.0 | 0.9132 |
| RTSS G | Rib Left 3 | 23.07 | 18.98 | -4.085 | -17.7 | 0.9029 |
| RTSS G | Rib Left 4 | 24.64 | 20.01 | -4.634 | -18.8 | 0.8962 |
| RTSS G | Rib Left 5 | 28.25 | 23.44 | -4.806 | -17.0 | 0.9070 |
| RTSS G | Rib Left 6 | 21.98 | 18.31 | -3.67 | -16.7 | 0.9089 |
| RTSS G | Rib Left 7 | 3.169 | 2.62 | -0.5494 | -17.3 | 0.9051 |
| RTSS G | Rib Right 1 | 14.09 | 11.91 | -2.183 | -15.5 | 0.9161 |
| RTSS G | Rib Right 2 | 20.51 | 17.19 | -3.323 | -16.2 | 0.9119 |
| RTSS G | Rib Right 3 | 26.23 | 21.47 | -4.754 | -18.1 | 0.9003 |
| RTSS G | Rib Right 4 | 31.96 | 26.02 | -5.942 | -18.6 | 0.8975 |
| RTSS G | Rib Right 5 | 31.52 | 26.51 | -5.008 | -15.9 | 0.9137 |
| RTSS G | Rib Right 6 | 26.76 | 22.56 | -4.193 | -15.7 | 0.9150 |
| RTSS G | Rib Right 7 | 4.104 | 3.371 | -0.7325 | -17.9 | 0.9020 |
| RTSS G | Supraglottic Larynx | 18.61 | 15.84 | -2.769 | -14.9 | 0.9038 |

## What this does not cover

The fixtures are synthetic, planar and on regular grids, with deliberately empty planes between extrusions; no surface is reconstructed between planes, which is how both the benchmark and AutoSeg treat a contour stack. Non-uniform slice spacing and treatment-planning-system comparisons are outside the benchmark. AutoSeg's timings are not comparable with the converter timings, which include each converter's file output; for the record, the median AutoSeg rasterisation of one ROI took 3.9 ms (continuous), 2.5 ms (legacy).

## Sources

The benchmark's raw per-plane rows, fixtures and references were read unmodified. Its scoring code, by SHA-256:

- `study/__init__.py` `f4d848705bb30e50…`
- `study/algorithms.py` `916047aa42fe3174…`
- `study/analyze.py` `138364dce485c935…`
- `study/converters.py` `86e8d906b7e5bac2…`
- `study/fixtures.py` `5d538355c3951ed8…`
- `study/geometry.py` `6623f768ed0a42e6…`
- `study/metrics.py` `ee891cbde336b39b…`
- `study/reference.py` `6867f00ea19f6e40…`
- `study/run.py` `0d8795b68a0461ae…`
- `study/source_details.py` `caecb70b6115af6c…`
- `study/sources.py` `d6eaed048b571aa1…`
- `study/validation.py` `9ccdcd1c2585a331…`
