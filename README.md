# AutoSeg Evaluator

> A GUI tool for segmentation quality assessment in radiotherapy.

[![CI](https://github.com/MLCOOKER/AutoSeg-Evaluator/actions/workflows/ci.yml/badge.svg)](https://github.com/MLCOOKER/AutoSeg-Evaluator/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.17383138.svg)](https://zenodo.org/records/17383138)

AutoSeg Evaluator is a Python desktop application that computes segmentation
quality metrics from DICOM image, RT Structure Set (RTSS), and RT Dose files.
It is designed for clinicians, medical physicists, and researchers performing
commissioning, ongoing QA, comparative evaluation of auto-contouring systems,
or inter-observer variability studies in radiotherapy — without requiring
command-line or coding expertise.

## Features

- **Validated geometric metrics**: Dice, Hausdorff 100% / 95%, Mean Surface
  Distance, Surface Dice (configurable tolerance), Added Path Length (mean /
  total, configurable tolerance), centre-of-mass offset, signed volume
  difference and volume ratio.
- **Dosimetric metrics**: per-contour DVH from RT Dose via
  [`dicompyler-core`](https://github.com/dicompyler/dicompyler-core) — Dmin /
  Dmean / Dmax plus user-defined `D{X}_gy` (dose to hottest X%) and
  `V{X}gy_cc` (volume receiving ≥ X Gy) points.
- **STAPLE consensus**: per-drawer Simultaneous Truth and Performance Level
  Estimation (Warfield 2004) with per-rater sensitivity / specificity plus
  consensus uncertainty metrics (uncertain-band volume, mean entropy, rater
  disagreement, rater volume range).
- **Inter-observer variability**: pairwise Dice / surface-distance / APL
  matrices over manual rater groups with configurable metric selection +
  tolerance overrides.
- **Synthetic consensus GT generation**: build STAPLE-derived consensus
  contours, write them back as a synthetic RTSS, and feed them into the
  evaluation pipeline as the designated ground truth.
- **Smart auto-matching**: hybrid Levenshtein + cosine matcher backed by a
  TG-263 synonym dictionary (~17 000 variants from the official worksheet),
  user-defined replacement rules, and template-driven batch selection.
- **Robust DICOM linking**: groups CT, RTSTRUCT, and RT Dose by
  `FrameOfReferenceUID` — handles AI vendors that change `StudyInstanceUID`.
- **Source identification**: cascading fallback (Manufacturer →
  StructureSetLabel → SoftwareVersions → filename) handles in-house models
  that lack metadata, with a manual override dialog persisted in
  `settings.json`.
- **Modern UI**: 5-tab workflow with accordion organ drawers, colourblind-safe
  similarity indicators, banded results table, dark / light themes, full undo
  stack on Match Contours.
- **Save / load sessions**: resume curated multi-patient evaluations across
  sittings; session schema is versioned and forward-compatible.
- **Portable**: ships as a hospital-IT-friendly Python bundle (see below) —
  every dependency is a normal `.py` / `.pyd` file, no PyInstaller blob.

## Quick start

### For end users — portable bundle (recommended)

1. Download the latest `AutoSegEvaluator-v*.zip` from the
   [Releases](https://github.com/MLCOOKER/AutoSeg-Evaluator/releases) page.
2. Extract it anywhere — local folder, USB stick, or shared drive.
3. Double-click `Run AutoSeg Evaluator.bat`.

The bundle ships a self-contained CPython 3.11 runtime and every dependency
as inspectable files under `python\Lib\site-packages\`. No Python install,
no admin rights, no registry writes, no internet access required at runtime —
suited to locked-down clinical Windows environments.

### From source

```bash
git clone https://github.com/MLCOOKER/AutoSeg-Evaluator.git
cd AutoSeg-Evaluator
pip install -e ".[dev]"
python -m autoseg_evaluator
```

Python 3.10 or newer required.

### Building the portable bundle locally

```bash
python scripts/build_portable.py
```

Produces `dist/AutoSegEvaluator-v{version}/` and a matching `.zip`. The same
script runs on a `windows-latest` GitHub Actions runner whenever a `v*` tag
is pushed (see [`.github/workflows/release.yml`](.github/workflows/release.yml)).

## Workflow overview

The application is organised into five sequential tabs:

1. **Load Data** — point at a folder of DICOM data; the app recursively scans
   and groups by patient, study, and frame-of-reference. Source labels can be
   overridden per RTSS via *Manage Source Labels*.
2. **Build Consensus GT** *(optional)* — cluster manually contoured raters,
   compute inter-observer variability metrics, and optionally generate a
   STAPLE-derived synthetic ground-truth RTSS that flows into Tab 3.
3. **Match Contours** — define replacement rules and an organ template, then
   run auto-match to populate accordion drawers grouped by organ. Per-drawer
   toggles control truncation, vs-GT vs vs-STAPLE mode, and whether the
   designated GT is fed into the STAPLE expectation-maximisation step.
4. **Compute** — select geometric and dosimetric metrics with tolerance
   controls, then run computation with detailed live progress and cancel.
5. **Results** — review the banded metrics table (tolerance values baked into
   the headers) and export to CSV.

See [`docs/PROJECT_OVERVIEW.md`](docs/PROJECT_OVERVIEW.md) for the full
architecture reference (~5 000 words) covering the matching pipeline, mask
creation, every metric implementation, STAPLE algorithm details, session
schema, performance engineering, and a literature index.

## Citation

If you use AutoSeg Evaluator in your research, please cite:

> Rusanov B, Rowshanfarzad P, Barry N, Ebert MA, Kendrick J. *AutoSeg
> Evaluator: An Efficient GUI Tool for Segmentation Quality Assessment.*
> Physics in Medicine and Biology Note, 2025.

BibTeX:

```bibtex
@article{rusanov2025autoseg,
  author  = {Rusanov, Branimir and Rowshanfarzad, Pejman and Barry, Nathaniel and Ebert, Martin A and Kendrick, Jake},
  title   = {{AutoSeg Evaluator: An Efficient GUI Tool for Segmentation Quality Assessment}},
  journal = {Physics in Medicine and Biology},
  year    = {2025}
}
```

A Zenodo DOI is also available:
[10.5281/zenodo.17383138](https://zenodo.org/records/17383138).

## License

Apache License 2.0 — see [LICENSE](LICENSE).

## Contributing

Contributions are welcome. Please see [CONTRIBUTING.md](CONTRIBUTING.md) for
guidance.

## Acknowledgements

This project incorporates ideas and validated implementations from
[PlatiPy](https://github.com/pyplati/platipy),
[google-deepmind/surface-distance](https://github.com/google-deepmind/surface-distance),
and [dicompyler-core](https://github.com/dicompyler/dicompyler-core).
