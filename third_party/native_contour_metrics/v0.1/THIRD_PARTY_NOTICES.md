# Attribution and provenance

## Published synthetic dataset

Boukerroui, Djamal; Gooding, Mark (2022). **The Vitruvian Man Dataset of Analytic calculations and synthetic shapes for validation of quantitative contour comparison software**, version 1. Mendeley Data. https://doi.org/10.17632/9xjyrftzth.1

The dataset is distributed under **Creative Commons Attribution 4.0 International**, https://creativecommons.org/licenses/by/4.0/ . Original license metadata and contributors are retained in `data/DATASET_ATTRIBUTION.json`. Original downloaded archives in `data/original/` are unchanged and checksum-verified.

Derived `fixtures.json.gz` projects contours into each CT's common local orthonormal frame and serializes complete region geometry and plane membership. Derived golden result tables and validation outputs were generated for this study. These transformations and calculations are ours, not outputs endorsed by the dataset authors. Preserve attribution and identify subsequent changes when redistributing derived data.

## Upstream code

`upstream/VitruvianPhantomPy/` is a source/reference snapshot from https://github.com/Vitruvian-phantom-for-RadOnc/VitruvianPhantomPy at commit `cc4106a421067b82d01bdf9225513cdb79e259d1`. BSD 3-Clause License; copyright (c) 2022 Djamal Boukerroui, Vitruvian man Phantom for RT. Full license retained in that directory.

`upstream/Chapter15/` contains the original source and license from https://github.com/Auto-segmentation-in-Radiation-Oncology/Chapter-15 at commit `16720a4262a1b5c6ec84673c49b970350e513182`. BSD 3-Clause License; copyright (c) 2020 Mark Gooding, Mirada Medical Ltd. Full license retained. This historical source is for comparison and is not the runtime engine; its older Shapely API and median convention differ from this package.

Do not remove these copyright notices, license conditions or disclaimers from redistributed upstream code. No endorsement by the authors is implied.

## New implementation

The native metric kernels, high-precision audit algorithms, API facade, tests and handoff documents were developed with an AI assistant for this project. They implement the cited mathematical definitions; they are not copied from or claimed to be authored by Gooding's team. The capsule primitives are shared between our optimized APL and exhaustive reference, as disclosed in the documentation.

No new open-source license has been selected for this project's original implementation by this handoff. The project owner and receiving team should apply their chosen project license or internal distribution terms to that code. Upstream BSD notices and dataset CC BY attribution remain separately applicable.

Runtime dependencies NumPy and Shapely retain their respective distribution licenses; dependencies are installed separately and are not vendored into this package.
