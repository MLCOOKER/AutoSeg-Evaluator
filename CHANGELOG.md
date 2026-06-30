# Changelog

All notable changes to AutoSeg Evaluator are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.6.0] — 2026-06-18

### Added
- **Qualitative (Likert) assessment — new Tab 4.** A grader scores each matched
  contour on the 5-point MD Anderson Likert scale (Baroudi et al., *Cancers*
  2023), shown as a reference table in the tab. Tabs are renumbered: Compute → 5,
  Results → 6.
  - **Per-grader configuration**, chosen when the grader is added and fixed
    thereafter: **blinded** (one contour at a time, source hidden) vs
    **transparent** (every source for the organ shown with labels + per-source
    visibility toggles); **include GT**; and **randomize** (group-aware — all
    sources of an organ stay consecutive).
  - **Multiplanar viewer** (`QGraphicsView`): axial / coronal / sagittal planes
    (coronal & sagittal oriented superior-up), ctrl+scroll zoom, Level/Window
    sliders, contour opacity + thickness, active-contour highlight.
  - Tinder-style swipe between contours, a progress bar, **Back** (revisit,
    scores kept), and a **tab lock** during grading with an explicit
    **Unlock** (no blinded-data leakage).
  - **Multiple graders** scored in turn; each grader's scores land in their own
    `Likert — <grader>` column in Results, with `Qualitative` (assessed yes/no)
    and `Blinded` flags, overlaid onto the contour's existing metric row and
    placed immediately before the dose columns (own colour band).
  - **Session save / resume** (schema v3 → **v4**): graders, their fixed
    configs, and every score are restored, and prior grades are re-populated
    into the Results tab.
- **Dose overlay in the slice viewer.** When an RT Dose is loaded, the Tab-3
  contour visualiser can overlay the planned dose as a colour wash (jet, Gy)
  toggled on/off, with an opacity control and a Gy colorbar. A new
  `core/dose.py` reads the RTDOSE in Gy and resamples it onto the CT grid.

## [2.5.3] — 2026-06-17

### Changed
- **Refreshed the application icon** with a transparent background (previously
  an opaque rounded square). Regenerated `icon.png` (512×512) and the
  multi-resolution `icon.ico` (16–256 px) from the new source so the
  taskbar / dock / title-bar icon now has clean transparent corners.
- **Added a GUI screenshot to the README** (the Match Contours workflow with
  the organ-template dialog) and extended the `docs/PROJECT_OVERVIEW.md`
  change history and footer through v2.5.2.

### Fixed
- **Corrected a stale STAPLE "Reset to defaults" tooltip** that still cited
  removed/renamed parameters (`max_iterations=30`, `bbox_padding_voxels=5`).
  It now reflects the actual defaults (max iterations 100, confidence weight
  1.0, adaptive bbox FG ratio max 0.50) and is generated from `_STAPLE_DEFAULTS`
  so it cannot drift from the real values again.

## [2.5.2] — 2026-06-16

### Changed
- **Removed the inert STAPLE `target_fg_ratio_min` parameter** (and its
  Compute-tab spinbox). The adaptive bounding box only ever *grows* the box,
  which can only *lower* the foreground/bbox ratio — so a lower-ratio target
  was never enforceable and the knob had no effect on results. Only the upper
  target (`target_fg_ratio_max`) is kept, and the docs/manuscript wording is
  corrected from "within a range" to "until the ratio falls to or below the
  upper target". STAPLE output is unchanged (still 55/55 bit-exact vs
  `SimpleITK.STAPLEImageFilter` on the HN1 cohort). Older `settings.json`
  files carrying `target_fg_ratio_min` still load — the key is ignored.

## [2.5.1] — 2026-06-16

### Fixed
- **Very small OARs are no longer silently dropped from the DVH.** dicompyler
  rasterises a structure by a point-in-polygon test at each dose-grid voxel
  centre, so a structure smaller than the dose grid spacing (~1–2 voxels) can
  fall *between* the sample points, rasterise to zero volume, and get no DVH.
  `compute_dvh_metrics` now retries once on a supersampled grid (¼ of the dose
  spacing) when a structure that has contours yields zero volume, recovering
  its dose statistics. It only triggers for sub-grid structures (so it's cheap
  — a few ms) and never changes a structure that already computed, so the
  bit-for-bit dicompyler DVH validation still holds.

## [2.5.0] — 2026-06-15

### Added
- **Application icon + splash screen.** The app now has a window / taskbar /
  dock icon and a startup splash. On Windows the taskbar shows the app's own
  icon — via an explicit `AppUserModelID` (so it no longer inherits the
  python interpreter's icon) and a multi-resolution `.ico`; macOS and Linux
  use the PNG through Qt (`setWindowIcon` + `setDesktopFileName`). The splash
  is shown before the heavy imports load, so the window appears responsive on
  startup. Assets live in `autoseg_evaluator/assets/` (packaged via
  `package-data`).
- **Icon'd desktop launchers.** The portable Windows bundle now launches via
  `pythonw.exe` (no flashing console window) and ships a
  `Create Desktop Shortcut.vbs` that creates an icon'd `.lnk` on the Desktop.
  `scripts/install-linux-desktop.sh` registers a freedesktop `.desktop` entry
  (with the app icon) for Linux source installs.

## [2.4.2] — 2026-06-10

### Fixed
- **DVH now respects cranio-caudal truncation.** When a drawer's *Truncate*
  option is active, the test structure's geometric metrics were computed on
  the z-truncated mask while its DVH was still computed from the full,
  untruncated RTSS contours (dicompyler reads the original contour points) —
  so dose and geometry described different volumes. `compute_dvh_metrics`
  gained an optional `z_extent_mm` parameter that drops the ROI's contour
  planes outside the GT's craniocaudal extent before computing, so the DVH
  describes the same range as the geometric comparison. Applied to test rows
  and per-rater STAPLE rows (never the GT, which defines the extent). The
  default (untruncated) path is unchanged and remains bit-for-bit identical
  to dicompyler-core — so the DVH validation report still holds. New helper
  `core.masks.gt_z_extent_mm`; covered by `tests/test_dvh_equivalence.py`
  and `tests/test_truncation.py`.

## [2.4.1] — 2026-06-08

### Added
- **STAPLE consensus validation.** New
  `scripts/validate_staple_against_upstream.py` +
  `docs/STAPLE_VALIDATION_REPORT.md` demonstrate that AutoSeg's
  `compute_staple` reproduces `SimpleITK.STAPLEImageFilter` (Warfield et
  al. 2004) bit-for-bit — per-rater sensitivity/specificity and the binary
  consensus mask — on the HN1 multi-observer cohort (55/55 organs exact,
  up to 6 raters each). Locked in CI by `tests/test_staple_equivalence.py`.
- **DVH dose-statistic validation.** New
  `scripts/validate_dvh_against_upstream.py` +
  `docs/DVH_VALIDATION_REPORT.md` demonstrate that AutoSeg's
  `compute_dvh_metrics` reproduces `dicompyler-core` 0.5.6 exactly across
  Dmin/Dmean/Dmax, D95/D50/D2 %, D0.1cc/D2cc, and V20Gy/V30Gy on the HN1
  CT + RT Dose + RT Structure cohort (2970/2970 comparisons exact, 297
  ROIs). Locked in CI by `tests/test_dvh_equivalence.py`.
- Both reports are PHI-safe (organ names + numeric values only) and
  reproducible by any user against their own data; the reference libraries
  are core dependencies, so the new equivalence tests need no extra install.
- **DVH difference-vs-GT columns.** Every DVH metric on a test row now also
  reports its deviation from the ground truth — a `{metric} Δ vs GT` column
  (test − GT) alongside the absolute value (e.g. `D2cc (Gy) Δ vs GT`). The
  Δ columns cluster after the absolute DVH columns in the table and CSV.
- **Single-slice OAR DVH.** Structures contoured on a single slice now yield
  DVH statistics. dicompyler-core derives slice thickness from the gap
  between adjacent contour planes, so a single-plane structure got thickness
  0 → zero volume → no DVH; AutoSeg now passes an explicit slab thickness
  (the dose grid's z-spacing) for that case so the DVH is computed.

### Fixed
- **Portable bundle reported version `0.0.0+unknown`.** The Windows bundle
  copies the package source instead of pip-installing it, so there was no
  dist-info for `importlib.metadata` to read. `build_portable.py` now stamps
  a `_version.py` into the bundled package and `__init__` falls back to it,
  so the title bar shows the real version.
- **Tab 4 progress bar and "Drawers complete" counter.** The bar was driven
  by a stale row-count estimate that no longer matched the rows actually
  emitted (gt-dose / STAPLE-detail / per-rater), leaving it stuck or short.
  Progress is now measured in (drawer × patient) work units weighted by
  rater count — known exactly up front and monotonic. The counter now shows
  every drawer×patient evaluation completed (total work), not the deduped
  number of unique organs.

## [2.4.0] — 2026-06-01

### Changed
- **Tab 2 (Build Consensus GT) redesigned around a multi-observer model.**
  Each manual observer is now identified by a **distinct source label**
  (assigned in Tab 1); the user selects which labels are observers via
  **Manual observers…** (persisted as ``consensus_observer_labels``). A
  patient is eligible when it has 2+ RTSSes among the selected observers,
  and grouping is per-patient over that observer set. This replaces the
  pre-v2.4 model that grouped by ``(patient, same source_label)`` and could
  not distinguish two clinicians.
- The consensus synthetic-RTSS UID is now deterministic on
  ``(patient_id | representative_organ)`` rather than
  ``(patient_id | source_label)``.

### Added
- **Three-column Tab 2 layout** — *Eligible patients* / *Organ groupings* /
  *Unmatched* tray, each with an independent scroll zone.
- **Editable organ groupings** — remove a contour with the ``X`` button,
  add via ``Assign ▾`` or drag-drop (including from the Unmatched tray).
  Editing a patient **locks** it from threshold re-clustering until
  **Reset** re-runs auto-match.
- **Per-patient match threshold** — each patient keeps its own fuzzy-match
  threshold; changing it re-clusters only that patient.
- **Representative-based bucket scoring** — each member shows its fuzzy
  score against the bucket's seed (representative) organ name, which is the
  actual clustering decision and the name the consensus carries into Tab 3.
- **Labelling-warning badges** when a patient has a duplicate observer
  label (same observer on >1 file).
- **Source-label disambiguation columns + assisted propagation** so two
  RTSSes from the same vendor can each get a distinct observer label.
- **STAPLE Details results row** (``mode = "STAPLE Details"``) for every
  STAPLE computation from either path, carrying consensus volume +
  uncertainty diagnostics (mean entropy, uncertain-band, rater
  disagreement, bbox padding/ratio, iterations, convergence) and **no
  dose columns**.
- **Provenance-tagged STAPLE modes** in the ``Mode`` column:
  ``Multi-observer STAPLE`` (Tab 2 consensus), ``Generic STAPLE with GT``
  and ``Generic STAPLE no GT`` (Tab 3 per-drawer, by ``GT in pool``).
- **Per-test sensitivity / specificity vs a consensus GT** — when a Tab 2
  consensus is the GT, every test contour's row also carries
  ``staple_sensitivity`` / ``staple_specificity`` versus that consensus.

### Fixed
- **``GetArrayViewFromImage`` use-after-free** in
  ``sensitivity_specificity_vs_reference``: the view aliased the temporary
  cropped image's buffer without keeping it alive, producing garbage counts
  on Linux (sensitivity ~0.028 instead of 0.5) while reading intact memory
  on Windows. Switched to ``GetArrayFromImage`` (a copy).
- **STAPLE RAM** — constituent masks are freed and ``gc.collect()``-ed
  after each synthesis to cap peak memory.
- **Dose parity** — Tab 3 STAPLE now emits a separate ``gt dose`` row like
  Tab 2 (dose was previously folded into / missing from the details row),
  and ``D at volume (cc)`` points are populated for synthetic-mask DVHs
  (previously always blank).
- The ``GT RTSS`` column is left blank for all STAPLE computations (a
  synthetic consensus has no source file).

## [2.3.0] — 2026-05-26

### Fixed
- **Template GT identifier now matches the source label, not just the
  raw Manufacturer tag.** ``_find_gt_rtss`` queries ``rtss.source_label``
  — the cascade-resolved display name with any Manage Source Labels
  override applied — instead of the raw DICOM ``Manufacturer`` tag.
  This makes the template work for RTSSes whose source came from a
  later cascade step (StructureSetLabel, SoftwareVersions, filename)
  AND honours user overrides, which were previously ignored.
- The Define Template dialog field is renamed from
  ``Manufacturer contains:`` to ``Source label contains:`` to reflect
  the new behaviour, with updated hint text. Settings key migrated to
  ``gt_source_label`` (the legacy ``gt_manufacturer`` key is still
  read as a fallback so v2.2-era templates load unchanged).

### Added
- **Manage Source Labels dialog** now displays six raw DICOM
  identification fields as separate columns alongside the detected
  source: ``Manufacturer``, ``StructureSetLabel``, ``SoftwareVersions``,
  ``StructureSetName``, ``StructureSetDescription``,
  ``ManufacturerModelName``. Lets users disambiguate two RTSSes from
  the same vendor (e.g. v3 vs v4 of a product, or 'manual' vs 'auto'
  exports that share a Manufacturer string) and bulk-override
  accordingly. Dialog defaults to 1400 × 560 to accommodate the new
  columns.
- ``RTSTRUCTEntry`` data model gains 5 optional fields backing the new
  columns. All default to empty string so older sessions load
  unchanged.

## [2.2.0] — 2026-05-26

### Added
- **D at volume (cc)** DVH input on the Compute tab — request the dose
  received by the hottest X cc of a structure (key shape ``d{X}cc_gy``,
  header ``D{X}cc (Gy)``). Common OAR hotspot constraints (D0.1cc,
  D1cc, D2cc) are now first-class metrics alongside the existing
  ``D{X}%`` and ``V{X}Gy`` inputs.
- Headers and CSV columns for the new metric sort into their own block:
  D-percent (descending) → D-cc (ascending) → V-Gy (ascending).

### Fixed
- **Window title shows the correct version** at runtime —
  ``__version__`` now reads from package metadata via
  ``importlib.metadata.version()`` instead of a hardcoded string.
  Future version bumps update everywhere (title bar, ``--version``,
  any ``__version__`` reference) from pyproject.toml alone.

## [2.1.0] — 2026-05-26

First public portable-bundle release. Adds the Build Consensus GT tab,
the distribution + CI pipeline, and a substantial accuracy / UX pass on
top of the in-development 2.0.0 baseline.

### Added
- **Build Consensus GT tab** (Tab 2): cluster manual rater RTSSes by
  organ via best-score-first thresholded matching, compute pairwise
  inter-observer variability across multiple groups in one batch
  (configurable metrics, tolerance overrides, progress bar, cancel),
  and optionally generate STAPLE-derived synthetic ground-truth RTSSes
  that flow into Match Contours as designated GT (with deterministic
  synthetic SOPInstanceUID).
- **Per-organ RAM eviction** in the inter-observer worker — peak RAM
  bounded by a single organ's masks rather than a whole patient's
  contour set; drops ~12× on 5-rater × 12-organ patients.
- **Tolerance values baked into headers** for Surface Dice and APL in
  both the results table and CSV export (`Surface Dice @ 3.00 mm`),
  preventing silent cross-tolerance merges in Excel.
- **Portable Windows bundle** (`scripts/build_portable.py`): builds a
  self-contained CPython 3.11 embeddable distribution with every
  dependency as inspectable `.py` / `.pyd` files under
  `python\Lib\site-packages\`. No PyInstaller blob, no Python install,
  no admin rights, no registry writes, no internet required at the
  end user — hospital-IT friendly.
- **GitHub Actions release pipeline** (`.github/workflows/release.yml`):
  builds + attaches the portable bundle to a GitHub Release on every
  `v*` tag push.
- **GitHub Actions CI** (`.github/workflows/ci.yml`): ruff + 259-test
  pytest suite on Windows + Linux with headless Qt
  (`QT_QPA_PLATFORM=offscreen`) on every push and PR.
- **`docs/PROJECT_OVERVIEW.md`**: ~5 000-word architecture reference for
  manuscript drafting and future LLM-assisted modifications.
- **README**: from-source install instructions for macOS and Linux
  (apt / dnf / pacman) including the libxcb-* Qt deps.

### Fixed
- **Tab 3 cancel** now interrupts computation mid-patient (not just
  between drawers) — `_cancelled` checks threaded into per-test mask
  load, per-GT row, before STAPLE, and per-rater STAPLE row.
- **Tab 2 inter-observer cancel** no longer reopens the progress dialog
  one contour-pair later — `QProgressDialog.setAutoReset(False)` +
  `setAutoClose(False)`, cancel check inside the organ loop, and
  `_tick_progress` skips `setValue` when already cancelled.
- **Greedy first-fit clustering bug** ("Parotid_L matched with
  A_Carotid_L") replaced with best-score-first ordering: pre-score all
  organs, sort by descending best-score, then assign — ensures the
  strongest matches claim their natural bucket first.
- **Help dialog on Match Contours** now opens (missing `QMessageBox`
  import + dialog parented to `self.window()` to render through the
  `QScrollArea` wrapper).
- **Clear All** is now undoable via Ctrl+Z (pushes a session snapshot
  onto the undo stack before wiping, instead of clearing the stack).

### Changed
- **Synthetic RTSS UIDs** are now fully deterministic
  (`AUTOSEG.SYNTHETIC.{hash(patient_id|source_label)}`) — re-running
  consensus generation on the same group produces the same UID,
  enabling reproducible session round-trips.
- **Ruff lint config** ignores N802 / N803 / N813 / N815 (Qt API
  convention) plus stylistic-only rules B905 / SIM108 / SIM102. The
  remaining ruleset (E / F / W / I / UP / B / SIM minus the above)
  is enforced in CI.

### Removed
- "Re-run auto-match for selected group" button in Tab 2 (redundant
  with the main Run Auto-Match flow).

## [2.0.0] — 2026-05-22

First public release of the v2 rewrite. Full migration from the single-window
PyQt5 prototype (`GUI23v13.py`) to a modular five-tab PySide6 application with
substantially expanded clinical functionality.

### Added
- Five-tab workflow: Load Data → Build Consensus GT *(optional)* → Match
  Contours → Compute → Results.
- Build Consensus GT tab — cluster manual rater RTSSes, compute pairwise
  inter-observer variability (multi-group selection, configurable metrics,
  tolerance overrides, progress + cancel), and optionally generate a
  STAPLE-derived synthetic ground-truth RTSS that flows into Tab 3 as a
  designated GT (with deterministic synthetic UID).
- Organ-drawer accordion UI grouping multi-patient comparisons by structure.
- Save / load session JSON for resuming curated matches across runs.
- TG-263 synonym dictionary (663 canonical names, ~17 000 variants generated
  from the official TG-263 worksheet via `scripts/build_synonyms.py`) with
  per-match provenance badges (`tg263_exact` vs `fuzzy`).
- STAPLE consensus mode per drawer, including per-rater sensitivity /
  specificity (Warfield et al. 2004) and consensus uncertainty summary
  metrics (uncertain-band volume, mean entropy, rater disagreement).
- Volume + centre-of-mass offset metrics (cc, signed Δx/Δy/Δz mm).
- Truncation reporting (slices removed + extent in mm) for fair comparison
  on structures with high-variability craniocaudal extent.
- DVH integration via `dicompyler-core` with user-defined D<sub>X</sub>%
  and V<sub>X</sub>Gy points alongside Dmin/Dmean/Dmax.
- Visualisation dialog with CT overlay + mouse-wheel slice scrolling.
- Dark / light theme toggle (`View → Theme`) with a VS Code-inspired
  high-contrast dark palette.
- Manage Source Labels dialog with bulk-apply for multi-row overrides.
- Tolerance values (τ for Surface Dice and APL) baked into results table
  and CSV column headers (e.g. `Surface Dice @ 3.00 mm`) so CSVs computed at
  different tolerances can't be silently merged.
- Portable Windows bundle build script (`scripts/build_portable.py`)
  producing a self-contained CPython 3.11 embeddable distribution with all
  dependencies as inspectable `.py` / `.pyd` files — hospital-IT friendly,
  no PyInstaller blob, no installation required.
- GitHub Actions CI (ruff + pytest on Windows + Linux, headless Qt) and
  release pipeline (builds + attaches portable bundle on `v*` tag push).

### Changed
- GUI framework migrated PyQt5 → PySide6 (LGPL).
- DICOM linking now uses `FrameOfReferenceUID` instead of `StudyInstanceUID`,
  correctly grouping RTSSes whose vendors changed the StudyUID (e.g. ZZZ_AC
  anonymisation pipelines).
- Organ-name matching uses the v1 Levenshtein + cosine hybrid with the
  TG-263 dictionary as the bridging layer; `rapidfuzz` was evaluated and
  rejected (subset-overlap behaviour scored unrelated organs too high).
- Results table emits columns in a stable canonical order regardless of run
  configuration; column headers carry physical units (mm, cc, Gy).
- Anonymisation alias merging: shared `FrameOfReferenceUID` across different
  `PatientID` values now collapse to the patient with image series attached.

### Fixed
- Surface-distance spacing order: v1 inadvertently passed `(x, y, z)` to
  `(z, y, x)`-ordered numpy arrays, producing incorrect physical distances
  on anisotropic CT. v2 reorders correctly and is bit-for-bit identical to
  `google-deepmind/surface-distance` and PlatiPy's APL on the SAMPLE DATA
  cohort.
- APL implementation now matches PlatiPy's `compute_metric_mean_apl` /
  `compute_metric_total_apl` exactly, including the NaN return for empty
  slice sets.
- Mean Surface Distance now returns NaN when either direction is NaN
  (matches v1's published values).
- Matcher canonical-case bug: when the dictionary resolved one side of a
  comparison but not the other, the case mismatch between the (preserved)
  TG-263 canonical and the (lowercased) fuzzy fallback broke Lev/cosine
  comparisons (`Eye_L` could lose to `Kidney_L` over `Eye Globe Left`).
  Fix lowercases canonicals and uses the cleaned-but-not-substituted raw
  forms for fuzzy fallback.
- Stale ticks in the Loaded Contours tree after drawer mutations — replaced
  the incremental mark/unmark approach with an authoritative re-sync from
  current drawer state after every mutation.

### Performance
- Per-patient cache eviction in the metrics worker: peak RAM is now bounded
  by a single patient's CT + masks rather than the entire cohort.
- CT volume released after the last drawer in each patient finishes mask
  rasterisation but before metric computation begins (~200 MB peak saving).
- Result groups sorted patient-major so eviction can happen at patient
  boundaries with no re-reads from disk.

### Internal
- 245-test pytest suite covering: surface distance vs `google-deepmind`,
  APL vs PlatiPy, TG-263 bridging + pitfall pairs, matcher regressions,
  session save/load round-trip, results table layout, source-label cascade,
  truncation extent reporting, STAPLE consensus, widget assembly.

## [1.x] — Prior releases

The v1 prototype (`GUI23v13.py`) is archived on Zenodo at
[10.5281/zenodo.17383138](https://zenodo.org/records/17383138).
