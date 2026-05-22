# AutoSeg Evaluator

> A GUI tool for segmentation quality assessment in radiotherapy.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.17383138.svg)](https://zenodo.org/records/17383138)

AutoSeg Evaluator is a Python-based desktop application that computes segmentation quality metrics
from DICOM image and RT Structure Set (RTSS) files. It is designed for clinicians, medical physicists,
and researchers performing commissioning, ongoing QA, or comparative evaluation of auto-contouring
systems in radiotherapy — without requiring command-line or coding expertise.

## Features

- **Validated metrics**: Dice, Hausdorff (100th and 95th percentile), Mean Surface Distance, Surface Dice, Added Path Length (mean and total)
- **Dosimetric evaluation** *(v2)*: per-contour DVH metrics from RT Dose files — Dmean, Dmax, Dmin, plus user-defined `D@volume%` and `V@dose(Gy)` thresholds
- **Smart auto-matching**: rapidfuzz token-aware string matching with user-defined replacement rules, template-driven batch selection across patients
- **Robust DICOM linking**: groups CT, RTSTRUCT, and RT Dose by FrameOfReferenceUID — handles AI vendors that change StudyInstanceUID
- **Source identification**: cascading fallback (Manufacturer → StructureSetLabel → SoftwareVersions → filename) handles in-house models lacking metadata
- **Modern UI**: 4-tab workflow (Load → Match → Compute → Results), accordion drawers grouped by organ, colourblind-safe similarity indicators
- **Save/Load sessions**: resume large evaluations across multiple sittings
- **Portable**: ships as a standalone Windows executable — no installation, no registry writes, runs from USB

## Quick start

### For end users (recommended)

1. Download the latest `AutoSegEvaluator-v*.zip` from the [Releases](https://github.com/branrusanov/autoseg-evaluator/releases) page
2. Extract anywhere (USB stick, local folder, network share)
3. Run `AutoSegEvaluator.exe` — no installation required

### From source

```bash
git clone https://github.com/branrusanov/autoseg-evaluator.git
cd autoseg-evaluator
pip install -e ".[dev]"
python -m autoseg_evaluator
```

Python 3.10 or newer required.

## Usage

The application is organised into four sequential tabs:

1. **Load Data** — point at a folder containing DICOM data; the application recursively scans and groups by patient and study
2. **Match Contours** — define replacement rules and a structure template, then run auto-match to populate accordion drawers grouped by organ
3. **Compute** — select geometric and dosimetric metrics, then run computation with detailed live progress
4. **Results** — review the metrics table, export to CSV

See [`docs/USAGE.md`](docs/USAGE.md) for a step-by-step walkthrough with screenshots.

## Citation

If you use AutoSeg Evaluator in your research, please cite:

> Rusanov B, Rowshanfarzad P, Barry N, Ebert MA, Kendrick J. *AutoSeg Evaluator: An Efficient GUI Tool for Segmentation Quality Assessment.* Physics in Medicine and Biology Note, 2025.

BibTeX:

```bibtex
@article{rusanov2025autoseg,
  author  = {Rusanov, Branimir and Rowshanfarzad, Pejman and Barry, Nathaniel and Ebert, Martin A and Kendrick, Jake},
  title   = {{AutoSeg Evaluator: An Efficient GUI Tool for Segmentation Quality Assessment}},
  journal = {Physics in Medicine and Biology},
  year    = {2025}
}
```

A Zenodo DOI is also available: [10.5281/zenodo.17383138](https://zenodo.org/records/17383138).

## License

Apache License 2.0 — see [LICENSE](LICENSE).

## Contributing

Contributions are welcome. Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidance.

## Acknowledgements

This project incorporates ideas and validated implementations from
[PlatiPy](https://github.com/pyplati/platipy),
[google-deepmind/surface-distance](https://github.com/google-deepmind/surface-distance),
and [dicompyler-core](https://github.com/dicompyler/dicompyler-core).
