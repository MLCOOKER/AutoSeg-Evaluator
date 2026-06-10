# AutoSeg Evaluator

> A GUI tool for segmentation quality assessment in radiotherapy.

[![CI](https://github.com/MLCOOKER/AutoSeg-Evaluator/actions/workflows/ci.yml/badge.svg)](https://github.com/MLCOOKER/AutoSeg-Evaluator/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)


Created by Branimir Rusanov

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
- **STAPLE consensus**: Simultaneous Truth and Performance Level Estimation
  (Warfield 2004) with per-rater sensitivity / specificity plus consensus
  uncertainty metrics (uncertain-band volume, mean entropy, rater
  disagreement, rater volume range), surfaced in the results table as a
  dedicated **STAPLE Details** row. Results are tagged by mode —
  *Multi-observer STAPLE* (Tab 2 consensus), *Generic STAPLE with GT* and
  *Generic STAPLE no GT* (Tab 3 per-drawer) — and every test contour scored
  against a consensus GT also carries its own sensitivity / specificity.
- **Multi-observer consensus ground truth**: assign each manual observer a
  distinct source label in Tab 1, select which labels are observers, and
  build a per-patient STAPLE consensus from them. The consensus is written
  back as a synthetic RTSS and flows into the evaluation pipeline as the
  designated ground truth — editable organ groupings, an unmatched tray for
  manual re-assignment, and an independent fuzzy-match threshold per patient.
- **Inter-observer variability**: pairwise Dice / surface-distance / APL
  matrices over the selected observers with configurable metric selection +
  tolerance overrides.
- **Smart auto-matching**: hybrid Levenshtein + cosine matcher backed by a
  TG-263 synonym dictionary (~17 000 variants from the official worksheet),
  user-defined replacement rules, and template-driven batch selection.
- **Robust DICOM linking**: groups CT, RTSTRUCT, and RT Dose by
  `FrameOfReferenceUID` — handles AI vendors that change `StudyInstanceUID`.
- **Source identification**: cascading fallback (Manufacturer →
  StructureSetLabel → SoftwareVersions → filename) handles in-house models
  that lack metadata, with a manual override dialog persisted in
  `settings.json`. Six raw DICOM identification columns plus assisted
  propagation let you disambiguate two RTSSes from the same vendor — e.g.
  giving each manual observer a distinct label for consensus building.
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

### From source (Windows)

```powershell
git clone https://github.com/MLCOOKER/AutoSeg-Evaluator.git
cd AutoSeg-Evaluator
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m autoseg_evaluator
```

Python 3.10 or newer required.

### From source (macOS)

```bash
# Python 3.11 via Homebrew (or python.org installer, or pyenv)
brew install python@3.11

git clone https://github.com/MLCOOKER/AutoSeg-Evaluator.git
cd AutoSeg-Evaluator
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m autoseg_evaluator
```

PySide6's macOS wheels are self-contained — no extra OS-level Qt
dependencies needed.

### From source (Linux — Ubuntu / Debian)

PySide6's Qt platform plugin links against system C libraries; install
them first, then proceed as on macOS:

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip git \
    libegl1 libxkbcommon0 libdbus-1-3 libxcb-cursor0 \
    libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
    libxcb-render-util0 libxcb-shape0 libxcb-sync1 libxcb-xfixes0 \
    libxcb-xinerama0 libxkbcommon-x11-0 libxcb-xkb1

git clone https://github.com/MLCOOKER/AutoSeg-Evaluator.git
cd AutoSeg-Evaluator
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m autoseg_evaluator
```

For Fedora / RHEL use:
```bash
sudo dnf install -y python3.11 python3-pip git \
    libxkbcommon mesa-libEGL dbus-libs xcb-util-cursor \
    xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm
```

For Arch:
```bash
sudo pacman -S python git qt6-base libxkbcommon-x11
```

The full 336-test suite is exercised on `windows-latest` and
`ubuntu-latest` in CI on every push — macOS is not in CI, but the same
PySide6 / SimpleITK / pydicom stack ships official wheels for macOS so
the app is expected to run identically there.

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
2. **Build Consensus GT** *(optional)* — pick which source labels are your
   manual observers (each observer = one distinct label), review the
   auto-clustered per-organ groupings for each eligible patient, compute
   inter-observer variability metrics, and generate a STAPLE-derived
   synthetic ground-truth RTSS per patient that flows into Tab 3.
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

## Validation

Every numerical engine in AutoSeg Evaluator — mask rasterisation, geometric
metrics, STAPLE consensus, and DVH dose statistics — produces bit-for-bit
identical output to its upstream reference implementation on a clinical
head-and-neck sample dataset:

- **Mask rasterisation** — 110 / 110 ROIs voxel-identical to PlatiPy
  0.7.2's `transform_point_set_from_dicom_struct`.
- **Geometric metrics** — 63 / 63 ROI–metric comparisons (Dice, HD100,
  HD95, Surface Dice @ 3 mm, mean surface distance, total APL, mean APL)
  produce zero absolute difference vs `google-deepmind/surface-distance`
  (Nikolov et al. 2018) and PlatiPy 0.7.2 (Finnegan et al., JOSS 2022).
- **STAPLE consensus** — 55 / 55 multi-rater consensus computations
  bit-identical to a direct `SimpleITK.STAPLEImageFilter` invocation
  (Warfield et al. 2004): every per-rater sensitivity/specificity matched
  to zero, every binary consensus voxel-identical.
- **DVH dose statistics** — 2 970 / 2 970 dose-statistic comparisons across
  297 ROIs (Dmin/Dmean/Dmax, D95/D50/D2 %, D0.1cc/D2cc, V20Gy/V30Gy)
  produce zero absolute difference vs `dicompyler-core` 0.5.6.

The full per-ROI breakdowns are in
[`docs/VALIDATION_REPORT.md`](docs/VALIDATION_REPORT.md) (masks + metrics),
[`docs/STAPLE_VALIDATION_REPORT.md`](docs/STAPLE_VALIDATION_REPORT.md), and
[`docs/DVH_VALIDATION_REPORT.md`](docs/DVH_VALIDATION_REPORT.md). Each report
is auto-generated by a script under [`scripts/`](scripts/) and contains no
PHI (no DICOM UIDs, filenames, patient identifiers, dates, or institution
metadata — only anonymised ROI display names and numeric values). Anyone can
re-run the validators against their own data to verify the parity claims
independently:

```bash
python scripts/validate_against_upstream.py        --data <CT+RTSS folder>          --out docs/VALIDATION_REPORT.md
python scripts/validate_staple_against_upstream.py --data <CT+multi-RTSS folder>    --out docs/STAPLE_VALIDATION_REPORT.md
python scripts/validate_dvh_against_upstream.py     --data <CT+RTSS+RTDOSE folder>  --out docs/DVH_VALIDATION_REPORT.md
```

All four equivalences are locked in CI via `tests/test_platipy_equivalence.py`,
`tests/test_metrics_equivalence.py`, `tests/test_staple_equivalence.py`, and
`tests/test_dvh_equivalence.py`.

## Citation

If you use AutoSeg Evaluator in your research, please cite:

> Rusanov B *AutoSeg
> Evaluator: An Efficient GUI Tool for Segmentation Quality Assessment.*


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
