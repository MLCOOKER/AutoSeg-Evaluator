# AutoSeg Evaluator v2 — Project Overview

A comprehensive reference for the v2.1 codebase. Written for two audiences:

1. **A clinician/researcher writing a technical note manuscript** — every
   algorithm, parameter default, and design decision is documented with the
   relevant literature citation so the methods section can be assembled
   without spelunking through code.
2. **A future LLM (or human developer) modifying the software** — every
   major component is listed with its file path, class/function name,
   responsibility, and the upstream/downstream interfaces it relies on.

The document is intentionally exhaustive. Skip to the section you need via
the table of contents.

---

## Table of contents

1. [Motivation & scope](#motivation--scope)
2. [Tech stack & licensing](#tech-stack--licensing)
3. [Repository layout](#repository-layout)
4. [User-facing workflow (the five tabs)](#user-facing-workflow-the-five-tabs)
5. [Data model](#data-model)
6. [Matching pipeline (organ-name canonicalisation)](#matching-pipeline-organ-name-canonicalisation)
7. [Mask creation (RTSTRUCT → binary)](#mask-creation-rtstruct--binary)
8. [Geometric metrics](#geometric-metrics)
9. [Volume + centre-of-mass metrics](#volume--centre-of-mass-metrics)
10. [Added Path Length (APL)](#added-path-length-apl)
11. [Dose-volume histogram (DVH)](#dose-volume-histogram-dvh)
12. [STAPLE consensus](#staple-consensus)
13. [Build Consensus GT workflow](#build-consensus-gt-workflow)
14. [Session save / load](#session-save--load)
15. [Performance engineering](#performance-engineering)
16. [Validation & test suite](#validation--test-suite)
17. [Theming & accessibility](#theming--accessibility)
18. [Distribution model](#distribution-model)
19. [Known limitations / future work](#known-limitations--future-work)
20. [Quick literature index](#quick-literature-index)

---

## Motivation & scope

AutoSeg Evaluator is a desktop tool for **quantitatively comparing
auto-segmentation (AI) contours against manual (clinician) ground-truth
contours** in radiation oncology. v2 is a complete rewrite of the v1
single-window prototype (`GUI23v13.py`), built to scale from a single
patient to multi-vendor inter-observer studies without the user having
to leave the GUI.

**Target users:**
- Radiation oncology medical physicists / clinical scientists evaluating
  vendor autocontouring tools (Limbus AI, MIM, MVision, Radformation,
  Mirada, etc.).
- Researchers running inter-observer variability studies (multiple manual
  contours per patient).
- Clinical departments doing periodic QA on their autocontour pipeline.

**Deliberate non-goals:**
- Not a contour editor. Editing happens in the TPS (Eclipse, Pinnacle,
  RayStation, Monaco) before RTSSes are exported.
- Not a dose calculator. RT-Dose grids are consumed as-is.
- Not a DICOM SCP/SCU. All I/O is filesystem-based.

**v1 → v2 highlights:**
- PyQt5 → PySide6 (LGPL, hospital-friendly).
- Single window → 5-tab workflow with persistent state.
- `StudyInstanceUID`-based linking → `FrameOfReferenceUID`-based (correctly
  handles vendor RTSS exports that change the StudyUID).
- ~17 organ synonym dictionary → ~17 000 variants from official TG-263.
- Inter-observer STAPLE workflow added (both as `vs STAPLE` per-drawer
  mode and as the `Build Consensus GT` pre-processing tab).
- Volume + COM metrics, truncation extent reporting, dose-VS-GT row.
- Per-patient + per-organ RAM eviction.
- Save / load session JSON for reproducible analysis.
- Bit-for-bit validated against `google-deepmind/surface-distance` and
  PlatiPy APL on the sample-data cohort (HN1/HN2/HN3).

---

## Tech stack & licensing

| Component | Version | Licence | Role |
|---|---|---|---|
| Python | 3.12+ | PSF | Runtime |
| PySide6 | 6.11+ | LGPL-3.0 | GUI toolkit |
| qt-material | latest | BSD-2 | Theming (light + custom dark palette) |
| SimpleITK | latest | Apache-2.0 | Image I/O, STAPLE filter, resampling |
| pydicom | latest | MIT | DICOM dataset parsing |
| dicompyler-core | 0.5.6 | BSD-3 | DVH calculation (`dvhcalc.get_dvh`) |
| numpy | latest | BSD-3 | Array math |
| scipy | latest | BSD-3 | Distance transforms, filters |
| scikit-image | latest | BSD-3 | Polygon rasterisation (RTSTRUCT → mask) |
| pytest | latest | MIT | Test runner |

**Overall project licence**: Apache-2.0 (see `LICENSE`).

**Why these choices:**
- PySide6 over PyQt5 for clean LGPL dynamic linking (avoids the
  static-linking grey area that PyInstaller `--onefile` creates with PyQt5
  + GPL).
- SimpleITK over raw ITK for the higher-level Pythonic API + bundled
  STAPLEImageFilter.
- dicompyler-core (rather than a hand-rolled DVH calculator) for
  conformance with the RTOG DVH convention and to avoid re-implementing
  dose-grid → mask resampling.

---

## Repository layout

```
autoseg-evaluator/
├── src/autoseg_evaluator/
│   ├── __init__.py                  # version
│   ├── __main__.py                  # ``python -m autoseg_evaluator``
│   ├── app.py                       # QApplication setup, theme apply
│   ├── core/                        # Pure algorithms (no Qt deps)
│   │   ├── matching.py              # Levenshtein+cosine, canonicalise, Match
│   │   ├── masks.py                 # RTSTRUCT→mask, truncation, DICOM I/O
│   │   ├── metrics.py               # compute_geometric_metrics aggregator
│   │   ├── surface_distance.py      # google-deepmind port (verbatim)
│   │   ├── staple.py                # STAPLE wrapper + adaptive bbox
│   │   ├── dvh.py                   # dicompyler-core wrapper
│   │   └── source_labels.py         # Vendor-detection cascade
│   ├── data/                        # In-memory models + persistence
│   │   ├── metadata.py              # MetadataLibrary, RTSTRUCTEntry, etc.
│   │   ├── results.py               # ResultsManager + canonical column order
│   │   ├── session.py               # Session JSON schema (v3)
│   │   └── synonyms.py              # TG-263 dict loader / flattener
│   ├── workers/
│   │   ├── scan_worker.py           # Background folder scan
│   │   └── metrics_worker.py        # Background metric compute (QThread)
│   ├── ui/
│   │   ├── main_window.py           # 5-tab shell, menus, session I/O
│   │   ├── theme.py                 # qt-material theme apply
│   │   ├── tabs/                    # One file per tab
│   │   │   ├── load_data.py
│   │   │   ├── build_consensus.py
│   │   │   ├── match_contours.py
│   │   │   ├── compute.py
│   │   │   └── results.py
│   │   ├── dialogs/
│   │   │   ├── source_labels.py     # Manage Source Labels (sortable + bulk)
│   │   │   ├── replacement_rules.py # Find→Replace rules
│   │   │   ├── template.py          # Define auto-match template
│   │   │   └── visualization.py     # CT + contour overlay viewer
│   │   └── widgets/
│   │       ├── collapsible_box.py   # Accordion-section primitive
│   │       ├── dicom_tree.py        # Tab 1 cohort tree
│   │       ├── loaded_contours_tree.py  # Tab 3 left-side tree
│   │       ├── organ_drawer.py      # Tab 3 accordion drawer
│   │       ├── progress_panel.py    # Tab 4 progress UI
│   │       └── signal_bar.py        # Similarity pip strip
│   ├── resources/
│   │   └── synonyms.json            # 663 canonicals, ~17k variants
│   └── utils/
│       ├── paths.py
│       └── settings.py              # settings.json round-trip
├── tests/                           # 341-test pytest suite
├── scripts/
│   └── build_synonyms.py            # Regenerate synonyms.json from TG-263 CSV
├── docs/PROJECT_OVERVIEW.md         # (this file)
├── pyproject.toml
├── requirements.txt
├── README.md
├── CHANGELOG.md
└── CITATION.cff
```

**Module dependency direction (top-down only — `core/` never imports `ui/`):**

```
ui/  →  data/  →  core/
            ↘     ↗
         workers/
```

This keeps `core/` algorithms unit-testable without Qt and lets `workers/`
run in a QThread cleanly.

---

## User-facing workflow (the five tabs)

```
1. Load Data           → 2. Build Consensus GT  → 3. Match Contours    → 4. Compute → 5. Results
   folder scan,          (optional)               drawers per organ,     metric run    table + CSV
   source labels,        STAPLE-derive            GT + tests per         + DVH
   cohort tree           a synthetic GT           patient
```

### Tab 1 — Load Data

**File:** [`src/autoseg_evaluator/ui/tabs/load_data.py`](../src/autoseg_evaluator/ui/tabs/load_data.py)

**Responsibility:** Recursively scan a folder for DICOM files; build a
`MetadataLibrary` keyed by `PatientID → ImagingContext (one per
FrameOfReferenceUID) → [RTSTRUCT, RTDOSE, ImageSeries]`. The scan runs in
a background `ScanWorker` thread so the UI stays responsive.

**Key UI elements:**
- **Manage Source Labels…** opens [`ui/dialogs/source_labels.py`](../src/autoseg_evaluator/ui/dialogs/source_labels.py)
  — sortable table with bulk-apply for overriding the auto-detected
  vendor label per RTSTRUCT. Six raw DICOM identification fields are
  surfaced as separate columns alongside the detected source
  (`Manufacturer`, `StructureSetLabel`, `SoftwareVersions`,
  `StructureSetName`, `StructureSetDescription`,
  `ManufacturerModelName`) so the user can disambiguate two RTSSes
  from the same vendor at a glance. Columns are user-resizable;
  right-click any column header to show or hide individual DICOM
  columns. File / Patient / Custom label are pinned visible.
- **Cohort tree (left) + Issues panel (right)** — side-by-side splitter,
  default 2:1. Issues panel surfaces things like orphan RTSSes, missing
  reference CTs, anonymisation merges.

**Source-label cascade** (in [`core/source_labels.py`](../src/autoseg_evaluator/core/source_labels.py)):
1. Custom override from settings (SOPInstanceUID → user-defined string).
2. `Manufacturer` tag.
3. `StructureSetLabel`.
4. `SoftwareVersions`.
5. `StructureSetName`.
6. Filename stem.
7. Folder name.
8. Literal `"unknown"`.

**Anonymisation merge:** when 2+ `PatientID`s share a
`FrameOfReferenceUID`, they're merged into the one with image-series
attached. Surfaces as a "merged: X, Y" suffix on the canonical patient.
See `MetadataLibrary._merge_anonymisation_aliases` in
[`data/metadata.py`](../src/autoseg_evaluator/data/metadata.py).

### Tab 2 — Build Consensus GT (Optional)

**File:** [`src/autoseg_evaluator/ui/tabs/build_consensus.py`](../src/autoseg_evaluator/ui/tabs/build_consensus.py)

**v2.4 multi-observer model.** The consensus unit is the **patient**, and
each manual observer is identified by a **distinct source label** assigned
in Tab 1 (the source label *is* the rater identity, so it stays consistent
across patients for free). The user clicks **Manual observers…** to pick
which source labels count as observers (persisted as
`consensus_observer_labels` in `settings.json`); a patient is *eligible*
when it has 2+ RTSSes whose source label is in that set. This inverts the
pre-v2.4 model, which grouped by `(patient, same source_label)` — multiple
files sharing *one* label — and so could not tell two clinicians apart.

**Three-column layout, each with its own scroll zone:**
- **Eligible patients** (narrow, left) — one row per patient with 2+ of the
  selected observers. A `labelling warning(s)` badge appears if a patient
  has a duplicate observer label (the same observer label on >1 file).
- **Organ groupings** (centre) — the selected patient's ROIs auto-clustered
  into per-organ "buckets" via **best-score-first threshold clustering**.
  Every organ is pre-scored against every existing bucket and processed in
  descending best-score order, so a perfect 1.0 match claims its bucket
  before a noisier 0.65 near-match can steal it. Each bucket shows the
  fuzzy score of each member against the bucket's **representative** (the
  seed organ name — the actual clustering decision, and the name the
  consensus carries into Tab 3). Buckets are **editable**: an `X` button
  removes a contour to the tray, and `Assign ▾` / drag-drop moves contours
  in. Editing a patient **locks** it from threshold re-clustering until
  **Reset** (which discards edits and re-runs auto-match).
- **Unmatched** tray (right) — contours that didn't cluster into a 2+ rater
  bucket, grouped by source label and sorted alphabetically, draggable back
  onto any bucket.

**Per-patient match threshold.** Each patient keeps its own fuzzy-match
threshold (footer spinbox edits the selected patient's value); changing it
re-clusters only that patient.

**Inter-observer variability dialog** (via `_InterObserverSettingsDialog`)
computes pairwise Dice / Surface Dice / HD100 / HD95 / MSD / Volume /
COM-offset between every observer pair for every matched organ. Progress
dialog + cancel. Results table sortable; CSV export + Ctrl+A+C with headers.

**Generate STAPLE for selected patients** / **for all patients** registers
synthetic `RTSTRUCTEntry` objects in `MetadataLibrary` with source label
`"STAPLE Consensus"`. Downstream tabs see these like any other RTSS but the
actual STAPLE consensus is computed *on-the-fly at compute time* using
Tab 4's STAPLE config.

**Synthetic RTSS model:** `is_synthetic_consensus=True`, `file_path=""`,
`constituent_groups: dict[synthetic_roi_number, list[(real_sop_uid, real_roi_number)]]`.
The UID is deterministic on `(patient_id|representative_organ)` so re-running
Generate replaces the entry in place.

### Tab 3 — Match Contours

**File:** [`src/autoseg_evaluator/ui/tabs/match_contours.py`](../src/autoseg_evaluator/ui/tabs/match_contours.py)

**Three-step auto-match workflow** along the top:
1. **Replacement Rules…** — site-specific `find → replace` substring
   rules applied before TG-263 canonicalisation.
2. **Define Template…** — a list of organs to find + the GT-identification
   criterion (`Source label contains:` substring and/or filename
   substring). The source-label criterion is matched against
   `rtss.source_label` — the cascade-resolved name with any Manage
   Source Labels override applied — so it works regardless of which
   DICOM tag the cascade resolved through and honours user overrides.
   Before v2.3 this matched the raw `Manufacturer` tag only; the
   settings key migrated from `gt_manufacturer` → `gt_source_label`
   with the legacy key still read as a fallback.
3. **Run Auto-Match** — for each patient, identify the GT RTSS via the
   template criterion, find the best-matching ROI per organ, build an
   `OrganDrawer` per organ with the GT and one auto-matched test row per
   non-GT RTSTRUCT.

**Drawer model** (one per organ, in [`ui/widgets/organ_drawer.py`](../src/autoseg_evaluator/ui/widgets/organ_drawer.py)):
- Per-drawer header toggles: `Truncate`, `vs GT`, `vs STAPLE`, `GT in pool`.
- Per-patient sub-section showing the GT + test rows with similarity
  signal bars + colour-coded match-method badges (`tg263` vs `fuzzy`).
- Drag-drop from the Loaded Contours tree on the left to add tests.

**Workflow features:**
- **Undo** (Ctrl+Z, 20-step bounded stack). Every drawer mutation snapshots
  the session state first.
- **Clear All** — wipes drawers + denylist + last-auto-match identifier
  (preserves undo stack so a Clear All can be undone with Ctrl+Z).
- **Template-change clear**: if Run Auto-Match is re-invoked with a
  different GT identifier (manufacturer / filename) than the previous
  run, drawers are wiped first to prevent stale GTs lingering.
- **Test-source refresh (Option A workflow)**: when re-running Auto-Match
  with the SAME identifier, any RTSSes added since the last run (e.g. a
  new vendor dropped into the folder a year later) are merged into the
  existing drawers as new test rows. Previously-removed test rows are
  remembered via a per-`(organ, patient_id)` **denylist** and not re-added.
- **`vs STAPLE` auto-disable**: when a drawer's GT is a synthetic
  `STAPLE Consensus` entry, the drawer's `vs STAPLE` and `GT in pool`
  checkboxes grey out (running STAPLE on STAPLE is meaningless).

**Loaded Contours tree** ([`ui/widgets/loaded_contours_tree.py`](../src/autoseg_evaluator/ui/widgets/loaded_contours_tree.py))
shows `Patient → (Context →) RTSTRUCT → Organ` with check marks for
already-assigned organs (re-derived authoritatively after every mutation
to avoid stale ticks). Constituent RTSSes of a synthetic consensus get a
"▸ in STAPLE consensus" suffix + explanatory tooltip; they remain
selectable and draggable.

### Tab 4 — Compute

**File:** [`src/autoseg_evaluator/ui/tabs/compute.py`](../src/autoseg_evaluator/ui/tabs/compute.py)

**Per-metric checkboxes** with descriptive tooltips. Default ON:
Dice, Surface Dice, HD100, HD95, MSD, Volume, COM offset. Default OFF: APL.

**Surface Dice τ** and **APL τ** tolerance spinboxes (default 3 mm each
— matches Nikolov 2018 and PlatiPy / Vaassen 2020).

**STAPLE consensus parameters** (collapsible group):
- `max_iterations` default 100 (BraTS / Asman & Landman convention).
- `confidence_weight` default 1.0 (ITK docstring recommendation).
- `target_fg_ratio_max` default 0.50 — the adaptive bbox's upper
  foreground-ratio target (Iglesias & Sabuncu 2015; Asman & Landman 2011).
  Only an upper target is exposed: padding can only *lower* the ratio, so a
  lower bound is not enforceable.
- `bbox_padding_min_voxels` / `bbox_padding_max_voxels` default 2 / 25.
- **Reset to defaults** button restores all of the above.

**DVH section:**
- Built-in toggles: `Dmin / Dmean / Dmax`.
- Three free-text inputs for user-defined DVH points:
  - `D at volume (%)` (e.g. `95, 50, 5, 2`) — dose to the hottest X%
    of the structure, keyed `d{X}_gy`.
  - `D at volume (cc)` (e.g. `0.1, 1, 2`) — dose to the hottest X cc
    of the structure (small-OAR hotspot constraints), keyed
    `d{X}cc_gy`. *(Added in v2.2.)*
  - `V at dose (Gy)` (e.g. `20, 30`) — volume in cc receiving ≥ X Gy,
    keyed `v{X}gy_cc`.

**Compute / Cancel** at the bottom. Spinbox scroll-wheel events are
ignored (`_NoScrollSpinBox` subclass) so scrolling the tab doesn't
silently mutate parameter values.

**Live progress panel** (`ui/widgets/progress_panel.py`): the metrics
worker drives it with structured updates. Progress is measured in
(drawer × patient) work units weighted by rater count — known exactly up
front, so the bar is honest and monotonic regardless of how many result
rows a group emits (v2.4.1; the previous row-count estimate left the bar
stuck/short). "Drawers complete" counts every drawer × patient
evaluation, not deduped unique organs; the panel also shows the current
patient / drawer / test / metric, elapsed, ETA, and error count.

### Tab 5 — Results

**File:** [`src/autoseg_evaluator/ui/tabs/results.py`](../src/autoseg_evaluator/ui/tabs/results.py)

**Sortable QTableWidget** with one row per metric computation. Column
order is **deterministic across runs** (defined in
`CANONICAL_METRIC_COLUMNS` — see [data model](#data-model) below) so
Excel paste-align stays consistent. DVH columns sit at the rightmost
end of the canonical block.

**Visual column groups:** custom `_BandedHeaderView` paints a 4-pixel
coloured stripe along the bottom of each header section, keyed to the
column's family — identifier (indigo), volumetric overlap (cyan),
surface distance (orange), APL (yellow), volume + COM (green), STAPLE
(pink), DVH (purple). Tooltip names the band on hover.

**Tolerance values in headers:** Surface Dice / APL columns read e.g.
`Surface Dice @ 3.00 mm`, `Mean APL @ 3.00 mm`, `Total APL @ 3.00 mm`
so CSVs computed at different tolerances can't silently merge.

**DVH Δ-vs-GT columns (v2.4.1):** when DVH is enabled, each test row's
DVH metric also gets a `… Δ vs GT` column (test − GT) — e.g. `D2cc (Gy)
Δ vs GT` — clustered after the absolute DVH columns, in both the table
and CSV.

**Excel-friendly copy:** `Ctrl+A` then `Ctrl+C` copies all rows + headers
as TSV. **Export CSV…** writes to disk.

---

## Data model

**File:** [`src/autoseg_evaluator/data/metadata.py`](../src/autoseg_evaluator/data/metadata.py)

```
MetadataLibrary
├── patients: dict[patient_id, PatientEntry]
│   └── PatientEntry
│       ├── patient_id
│       ├── merged_aliases: set[str]    # FoR-UID-merged sibling PIDs
│       └── contexts: list[ImagingContext]   # one per FrameOfReferenceUID
│           ├── frame_of_reference_uid
│           ├── image_series: list[ImageSeriesEntry]
│           ├── rtstructs: list[RTSTRUCTEntry]
│           │   ├── sop_instance_uid
│           │   ├── file_path                # "" for synthetic consensus
│           │   ├── manufacturer / source_label / source_origin
│           │   ├── frame_of_reference_uid
│           │   ├── organs: list[OrganEntry]
│           │   ├── is_synthetic_consensus: bool        # ← Tab 2 synthetic
│           │   ├── constituent_groups: dict[int, list[tuple[str,int]]]
│           │   └── structure_set_label / software_versions /
│           │       structure_set_name / structure_set_description /
│           │       manufacturer_model_name        # ← v2.3 raw DICOM fields
│           │       # exposed in Manage Source Labels for disambiguation;
│           │       # all default to "" so older sessions load unchanged.
│           └── rtdoses: list[RTDOSEEntry]
└── issues: list[ScanIssue]
```

**Library helpers for synthetic STAPLE consensus:**
- `register_synthetic_consensus(patient_id, for_uid, entry)`
- `unregister_synthetic_consensus(sop_uid)`
- `clear_synthetic_consensus()`
- `synthetic_consensus_entries()` → list of `(pid, for_uid, entry)`

**ResultsManager** ([`data/results.py`](../src/autoseg_evaluator/data/results.py)):
- Append-only row list (`rows()` returns a deep copy).
- `metric_columns()` returns the canonical column order
  (`CANONICAL_METRIC_COLUMNS`) PLUS any dynamic columns (user `D{X}_gy`,
  `V{X}gy_cc`) appended via `_dynamic_metric_sort_key`.
- `metric_display_label(key, *, sd_tau_mm=None, apl_tau_mm=None)` —
  static `_METRIC_LABELS` dict + dynamic DVH naming + τ-decorated
  Surface Dice / APL labels.
- `set_tolerances(sd_tau_mm, apl_tau_mm)` — stamped at compute start by
  `MainWindow._on_compute_requested`; consulted by the table refresh
  and CSV export.
- `export_csv(path)` — meta columns + display-label headers + formatted
  cells (`_format_cell` — 6-sig-fig floats, NaN → empty, bools as
  `True`/`False`).

---

## Matching pipeline (organ-name canonicalisation)

**File:** [`src/autoseg_evaluator/core/matching.py`](../src/autoseg_evaluator/core/matching.py)

Hybrid Levenshtein + cosine similarity (the v1 algorithm). Five-step
canonicalisation pipeline runs on each name *before* comparison:

1. Lowercase + trim.
2. Apply user replacement rules (`find` substring → `replace`, case-insensitive).
3. Convert `_` / `-` to spaces; collapse whitespace.
4. Look up the spaceless form in `synonyms_flat` (flattened TG-263 dict);
   if found, replace with the canonical (lowercased).
5. Compare:
   - If **both** sides resolved to the **same** canonical → `Match(1.0, "tg263")`.
   - Else compare the cleaned-but-NOT-substituted raw forms via the
     hybrid Lev + cosine (50/50 blend) → `Match(score, "fuzzy")`.

**Why the raw forms are used for the fuzzy fallback** (rather than the
canonical forms): substituting the canonical when only ONE side resolves
gives apples-to-oranges comparison (short canonical vs long verbose
name) and the case-preserved canonical kills cosine overlap against
lowercase fallback strings. Reverting to raw forms eliminated four
real-world regressions on the SAMPLE DATA cohort
(Eye_L→Kidney vs Eye Globe Left; lens_L→Lung vs Lens_Eye_L;
eye_R→eye_L_experimental; SubmanG_L→Buccal_Mucosa_L).

**TG-263 synonyms dictionary** ([`resources/synonyms.json`](../src/autoseg_evaluator/resources/synonyms.json)):
- 663 canonical primary names from the official TG-263 nomenclature CSV
  (anatomic rows only — PRV, Derived, Target, Non-Anatomic excluded).
- ~17 000 total variants generated by [`scripts/build_synonyms.py`](../scripts/build_synonyms.py)
  via four rules:
  1. **Primary + Reverse-order** names always included.
  2. **Paired-laterality expansion** — `Stem_L`/`Stem_R` get
     `L_Stem`, `Lt_Stem`, `Left_Stem`, `StemL`, `LeftStem`, etc. ONLY
     when both `_L` and `_R` exist in TG-263 (avoids false positives
     like `VB_L` = "Lumbar Vertebra", not "Left Vertebra").
  3. **Category-prefix collapse** — `Bone_Mandible` also accepts the
     naked `Mandible`, ONLY when no other canonical collapses to the
     same naked form.
  4. **Conservative description mining** — short, filler-word-free
     descriptions like "Optic nerve" → variants of `OpticNrv` with
     laterality propagated to `OpticNrv_L` / `_R` siblings.

**Mapping from match method to UI:** signal-bar colour distinguishes
TG-263 (blue) from fuzzy (amber) matches in the Tab 3 drawers.

---

## Mask creation (RTSTRUCT → binary)

**File:** [`src/autoseg_evaluator/core/masks.py`](../src/autoseg_evaluator/core/masks.py)

**Algorithm** (PlatiPy-derived; matches `rt-utils` and `dcmrtstruct2nii`):

1. For each slice's `ContourData`:
   - Reshape physical points to `(N, 3)`.
   - Use `dicom_image.TransformPhysicalPointToIndex` to map physical →
     voxel coordinates.
   - Verify all points share the same Z index (skip ROI otherwise).
2. Rasterise the 2D polygon via `skimage.draw.polygon`.
3. **XOR** the slice mask into the running 3D mask (handles donut shapes
   like rectum lumen / spinal canal).
4. Output a binary `sitk.Image` that shares spacing / origin / direction
   with the reference image.

**Limitation acknowledged in the implementation:** only `CLOSED_PLANAR`
contour geometry is supported (matches v1, PlatiPy). `POINT` and
`OPEN_PLANAR` ROIs are skipped.

**Truncation** (`truncate_to_gt_z_extent`): zeroes out test-mask slices
that fall outside the GT's craniocaudal extent. Returns the truncated
mask plus `{slices_removed, extent_removed_mm}` for results-table
reporting (per-row `Truncated slices` / `Truncated extent (mm)` columns).
Useful for cord, rectum, oesophagus where the AI may legitimately
extend beyond the manual GT. The companion `gt_z_extent_mm` returns the
same extent in physical mm so the DVH can apply the equivalent
contour-plane truncation (see [DVH](#dose-volume-histogram-dvh)) — keeping
dose and geometry on the same craniocaudal range.

**Reference-image lookup** (`find_reference_image_folder`): walks the
`MetadataLibrary` by `FrameOfReferenceUID` to locate the CT folder that
backs a given RTSS. Replaces v1's `StudyInstanceUID`-based logic, which
broke when vendor RTSS exports changed the StudyUID.

---

## Geometric metrics

**File:** [`src/autoseg_evaluator/core/metrics.py`](../src/autoseg_evaluator/core/metrics.py)
**Underlying primitives:** [`core/surface_distance.py`](../src/autoseg_evaluator/core/surface_distance.py)

`compute_geometric_metrics(gt_mask, test_mask, config)` is the
high-level aggregator; it dispatches to:

| Metric key | Function | Definition | Reference |
|---|---|---|---|
| `dice` | `compute_dice_coefficient` | 2·\|A∩B\| / (\|A\| + \|B\|) | Dice 1945 |
| `hausdorff100` | `compute_robust_hausdorff(sd, 100)` | Max symmetric surface distance (mm) | google-deepmind/surface-distance |
| `hausdorff95` | `compute_robust_hausdorff(sd, 95)` | 95th-percentile surface distance (mm) | Aydin 2021 |
| `mean_surface_distance` | `compute_average_surface_distance(sd)` | Mean of both directional means (mm); NaN if either is NaN | v1 convention (preserved) |
| `surface_dice` | `compute_surface_dice_at_tolerance(sd, τ)` | Fraction of each surface within τ mm of the other | Nikolov 2018 |
| `volume_gt_cc` / `volume_test_cc` / `volume_diff_cc` / `volume_ratio` | local | Voxel-count × voxel volume in cc | RTOG retrospective convention |
| `com_offset_mm` / `com_dx_mm` / `com_dy_mm` / `com_dz_mm` | local | Euclidean + signed per-axis centroid offsets (mm) | — |

**Axis-ordering correctness:** SimpleITK reports spacing as `(x, y, z)`
but `sitk.GetArrayFromImage` returns `(z, y, x)`. `compute_surface_distances`
(via `scipy.ndimage.distance_transform_edt(sampling=…)`) expects
`sampling` in the SAME axis order as the input array. v1 inadvertently
passed `(x, y, z)` to a `(z, y, x)` array; v2 reorders correctly. This
was verified by computing all six metric families against the upstream
`surface-distance` and PlatiPy packages on the SAMPLE DATA HN1 cohort —
Δ = 0.00e+00 across every metric.

---

## Volume + centre-of-mass metrics

Computed inline in `metrics.py` from the binary masks:

- `volume_gt_cc`, `volume_test_cc`: `voxel_count × (sx × sy × sz) / 1000`.
- `volume_diff_cc`: `test − gt`.
- `volume_ratio`: `test / gt` (`NaN` when gt empty).
- `com_offset_mm`: Euclidean magnitude of the centroid difference
  vector in physical units (image spacing applied).
- `com_dx_mm / dy_mm / dz_mm`: signed per-axis components.

Volume uses the standard voxel-counting convention (matches `rt-utils`,
`PlatiPy`, RTOG retrospective analyses). Contour-integral volume from
the original DICOM points (used by some TPS engines like Eclipse) is
NOT computed; the difference is typically <1 % for medium structures
and a few % for tiny structures (lens, cochlea). Documented in the
methods section of any manuscript using this tool.

---

## Added Path Length (APL)

**Implementation in:** [`core/metrics.py`](../src/autoseg_evaluator/core/metrics.py)
(`_apl_per_slice`, `apl_total`, `apl_mean`)

Faithful port of **PlatiPy's** `compute_metric_total_apl` /
`compute_metric_mean_apl`. Per slice, the APL is the count of GT
contour voxels NOT within τ mm of the test contour. The total is the
sum across slices × in-plane spacing; the mean is the average over
slices that contributed any voxels (returns `NaN` when no slice
contributed, matching PlatiPy's `np.mean([])` semantics).

**τ default = 3 mm** (PlatiPy default; Vaassen 2020).

APL operates in SimpleITK's `(x, y, z)` space directly (no numpy
reordering), per PlatiPy. Validated bit-for-bit against the upstream
package on the SAMPLE DATA cohort.

---

## Dose-volume histogram (DVH)

**File:** [`src/autoseg_evaluator/core/dvh.py`](../src/autoseg_evaluator/core/dvh.py)

Thin wrapper around `dicompylercore.dvhcalc.get_dvh`. The wrapper:

- Accepts a `DVHConfig` dataclass (`include_dmin / include_dmean /
  include_dmax / d_at_volumes_pct / d_at_volumes_cc / v_at_doses_gy`).
  *(d_at_volumes_cc added in v2.2.)*
- Outputs keys in canonical order: `dmin_gy, dmean_gy, dmax_gy, d{X}_gy
  (for each X in d_at_volumes_pct), d{X}cc_gy (for each X in
  d_at_volumes_cc), v{X}gy_cc (for each X in v_at_doses_gy)`.
- Raises a clean `DVHError` (rather than letting `dicompylercore`'s
  internal exceptions bubble) so the worker can write a `DVH: …` error
  row instead of crashing the batch.

**dicompylercore 0.5.6 syntax quirks** (already handled):
- `dvh.statistic()` rejects `"D{X}%"` as an attribute name. The bare
  `"D{X}"` form is interpreted as a relative-volume percentage and
  returns dose in Gy.
- `"D{X}cc"` returns dose to the hottest X cc — useful for small OARs
  (cord, brainstem, chiasm) where a fixed % is noisy. Typical clinical
  hotspot constraints: D0.1cc, D1cc, D2cc.
- `"V{X}Gy"` still requires the unit suffix; the alternatives
  `V{X}cc` / `V{X}%` mean different things in dicompyler-core.

**Single-slice OARs (v2.4.1):** dicompyler-core derives slice thickness
from the gap between adjacent contour planes, so a structure contoured
on a *single* slice gets thickness 0 → volume 0 → no DVH. For that case
the wrapper passes an explicit `thickness` (the dose grid's z-spacing,
via `_single_plane_thickness`) so single-slice OARs still yield dose
statistics.

**Sub-dose-grid OARs (v2.5.1):** dicompyler rasterises a structure by a
point-in-polygon test at each *dose-grid* voxel centre, so a structure
smaller than the dose grid spacing (~1–2 voxels) can fall between the
sample points, rasterise to zero volume, and get no DVH. When a structure
that *has* contours yields zero volume, the wrapper retries once with
`interpolation_resolution` = ¼ of the dose spacing (`_supersample_resolution`)
so the tiny OAR is recovered. Only triggers for sub-grid structures (cheap),
and never changes a structure that already computed.

**Cranio-caudal truncation (v2.4.2):** `compute_dvh_metrics` takes an
optional `z_extent_mm = (z_lo, z_hi)`. When the drawer's *Truncate*
option is active, the worker computes the GT's physical z-extent
(`core.masks.gt_z_extent_mm`, ±½ voxel) and passes it so the test ROI's
contour planes outside that range are dropped (temporarily, restored in
a `finally`) before dicompyler integrates — the contour-space twin of
the test mask's voxel-space truncation, so the DVH describes the **same
craniocaudal range as the geometric metrics**. Applied to test rows and
per-rater STAPLE rows; **never the GT**, which defines the extent. The
default (`z_extent_mm = None`) path is byte-identical to before, so the
bit-for-bit dicompyler equivalence still holds.

**GT-vs-dose row:** when DVH is enabled in Tab 4, the worker emits one
extra row per (patient × organ) with the GT contour's own dose
statistics, `comparison_mode = "gt_dose"`, geometric columns empty,
DVH columns populated. Lets the user compare manual-GT dose against
each AI vendor's dose side-by-side.

**Δ-vs-GT columns (v2.4.1):** each test row also carries a `{metric}_diff`
(test − GT) for every DVH metric — e.g. `d2cc_gy_diff` — so the table
reports both the absolute value and the deviation from the reference.
The Δ columns cluster after the absolute DVH columns.

**Synthetic-GT DVH:** when GT is a synthetic STAPLE consensus, the
worker falls back to mask-based voxelwise dose statistics
(`_dvh_for_consensus_mask`) because there's no RTSS dataset to feed
dicompyler-core. (Switching all DVH to this mask method was prototyped
and rejected: it diverged from dicompyler-core by up to ~50 % on V{X}Gy
for small/low-dose structures, so contour-based DVH via dicompyler
remains the engine for real RTSS structures.)

---

## STAPLE consensus

**File:** [`src/autoseg_evaluator/core/staple.py`](../src/autoseg_evaluator/core/staple.py)

Wraps `SimpleITK.STAPLEImageFilter` (Warfield, Zou & Wells MICCAI 2002 /
IEEE TMI 2004). For each call:

1. Build the union of all rater foregrounds.
2. **Adaptive bounding-box sizing**: grow the union bbox padding one
   voxel-ring at a time until the foreground/total ratio falls to or below
   `target_fg_ratio_max` (default 0.50, per Iglesias & Sabuncu 2015 and
   Asman & Landman 2011). Only this upper target is enforced — padding can
   only *lower* the ratio, so a sparse structure simply keeps its natural
   (low) ratio. Cap at `bbox_padding_max_voxels = 25`. Keeps per-rater
   specificity informative even for small structures (without this, a 5×5×5
   lens in a 15×15×15 padded bbox has 3.7 % foreground → specificity ~1.0
   → no diagnostic information).
3. Run STAPLE on the cropped stack with `max_iterations=100`,
   `confidence_weight=1.0`.
4. Threshold at P ≥ 0.5 → binary consensus.
5. Pad both the probability map and the binary consensus back to the
   original image extent.
6. Compute scalar uncertainty summaries.

**Returned `StapleResult`:**

| Field | Definition |
|---|---|
| `consensus_mask` | Binary uint8 mask at P ≥ 0.5 |
| `probability_map` | Float32 P map |
| `sensitivities[]` / `specificities[]` | Per-rater EM estimates (Warfield 2004 Eqs. 7-8) |
| `elapsed_iterations` / `max_iterations` / `converged` | Diagnostic |
| `n_raters` | Pool size |
| `consensus_volume_cc` | Volume at P ≥ 0.5 |
| `uncertain_band_cc` | Volume of voxels with 0.2 < P < 0.8 |
| `mean_entropy` | Mean binary entropy −P·log(P) − (1−P)·log(1−P) over voxels with P > 0.05 |
| `rater_disagreement_cc` | Pre-EM `union − intersection` volume (model-free disagreement signal) |
| `rater_volume_range_cc` | Max − min per-rater volume |
| `bbox_padding_used` / `bbox_fg_ratio` | Adaptive sizer diagnostics |

**Two ways to use STAPLE in the app:**

1. **Per-drawer `vs STAPLE` mode** (Tab 3) — treats GT + tests as
   raters. The `GT in pool` sub-toggle controls whether the designated
   GT contributes to the EM (default ON — "no true truth" framing per
   Warfield 2004; OFF for evaluating AI ensemble vs reference framing).

2. **Synthetic GT (Tab 2 → Tab 3 → Tab 4)** — Tab 2 builds a STAPLE
   consensus from 2+ manual observers, registers it as a synthetic
   RTSS, and Tab 3 designates it as the GT. The worker rasterises
   constituents on-the-fly and runs STAPLE at compute time using
   Tab 4's STAPLE config. The drawer's `vs STAPLE` auto-disables
   (running STAPLE on STAPLE is methodologically meaningless).

**Result-row schema (v2.4).** Every STAPLE computation, from either
path, emits a dedicated **STAPLE Details** row (`mode = "STAPLE
Details"`) carrying the consensus volume + uncertainty diagnostics
(`mean_entropy`, `uncertain_band_cc`, `rater_disagreement_cc`,
`bbox_padding`, `bbox_fg_ratio`, iterations, convergence) and **no dose
columns**. Rows are tagged by provenance in the `Mode` column:

| Mode | Source |
|---|---|
| `Multi-observer STAPLE` | Tab 2 consensus used as GT |
| `Generic STAPLE with GT` | Tab 3 per-drawer `vs STAPLE`, `GT in pool` ON |
| `Generic STAPLE no GT` | Tab 3 per-drawer `vs STAPLE`, `GT in pool` OFF |
| `STAPLE Details` | the diagnostics row accompanying any of the above |

The `GT RTSS` column is left **blank** for all STAPLE computations (a
synthetic consensus has no source file). When a consensus is used as GT,
**each test contour's row also carries its own `staple_sensitivity` /
`staple_specificity`** versus that consensus — computed by
`sensitivity_specificity_vs_reference` over the same adaptive bbox the
EM uses — so reviewers see how each AI/test structure reproduces the
consensus alongside the geometric metrics. When dose is requested, the
consensus's own DVH is emitted as a separate `gt dose` row (parity
across both Tab 2 and Tab 3 paths), and `D at volume (cc)` points are
populated for synthetic-mask DVHs.

---

## Build Consensus GT workflow

**See [Tab 2 — Build Consensus GT](#tab-2--build-consensus-gt-optional)** for the UI.

### Algorithm flow

```
User loads folder
        │
        ▼
   Tab 1 scans → MetadataLibrary
        │
        ▼
   User picks which source labels are observers (Manual observers…)
        │
        ▼
   Tab 2 detects patients with 2+ RTSSes among the selected observers
        │
        ▼
   For each eligible patient (members = RTSSes in the observer set):
       │
       ▼
   _auto_match_group   ← uses best-score-first threshold clustering
   (per-patient similarity threshold, TG-263 dictionary)
       │
       ▼
   Per-organ buckets: {organ_display_name: [(sop_uid, roi_number, roi_name), ...]}
   (single-rater buckets → Unmatched tray for manual assignment)
       │
       ▼
   User reviews/edits buckets, optionally clicks "Compute inter-observer
   variability…" to see pairwise metrics.
       │
       ▼
   Generate STAPLE for SELECTED / ALL patients:
       │
       ▼
   _build_synthetic_entry → RTSTRUCTEntry(is_synthetic_consensus=True, …)
       │
       ▼
   MetadataLibrary.register_synthetic_consensus(...)
       │
       ▼
   consensusGenerated signal → MainWindow refreshes Tab 3 + Tab 4 trees
       │
       ▼
   Tab 3: synthetic RTSS appears in Loaded Contours tree with source
   label "STAPLE Consensus" and bold styling.
       │
       ▼
   Tab 4: worker detects synthetic GT in _get_mask via _find_rtstruct_entry,
   calls _synthesise_consensus_mask which rasterises every constituent
   and runs compute_staple. Cached under the synthetic key for the rest
   of the run.
```

### Inter-observer variability dialog

**Class:** `_InterManualMetricsDialog` in `build_consensus.py`

Pre-flight settings dialog (`_InterObserverSettingsDialog`) lets the
user pick which metrics to compute + override Surface Dice / APL
tolerances. Progress dialog ticks per `(group × organ)` with full
cancel responsiveness (cancel-check threaded into both the per-organ
loop AND the per-rater-pair inner loop). Results table:

- Columns: `Patient`, `Source label`, `Organ`, `Rater A`, `Rater B`,
  then metric columns with τ values baked into the Surface Dice / APL
  headers.
- Sortable QTableWidget; Ctrl+A → Ctrl+C copies all rows + headers as
  TSV; **Export CSV…** writes a CSV file.
- Per-organ mask eviction inside `_compute_inter_manual_rows` caps peak
  RAM at `~raters × 1` mask instead of `raters × organs`.

### Conceptual difference from Tab 3's per-drawer "vs STAPLE"

| | Build Consensus GT (Tab 2) | Tab 3 "vs STAPLE" |
|---|---|---|
| **Use case** | Multi-manual studies — combine N manuals into the single GT used for AI comparison | All-as-raters — every contour in a drawer (GT + tests) is one rater |
| **STAPLE input** | Only the user-selected manual RTSSes | GT + every test contour |
| **STAPLE output use** | Synthetic GT visible in Tab 3, designated as `Manual` reference | Per-rater sens/spec + consensus summary rows in Tab 5 |
| **Compute timing** | At Tab 4 metric run (on-the-fly) | At Tab 4 metric run (also on-the-fly) |
| **Allowed together?** | No — if the GT is a synthetic STAPLE consensus, Tab 3's `vs STAPLE` is auto-disabled |

---

## Session save / load

**File:** [`src/autoseg_evaluator/data/session.py`](../src/autoseg_evaluator/data/session.py)

**Schema version: 3.** Past versions still load (missing fields default
to empty); future versions are refused with a clear error.

**Top-level JSON shape:**

```json
{
  "schema_version": 3,
  "saved_at": "2026-05-25T14:30:00+00:00",
  "folder": "C:/path/to/cohort",
  "replacement_rules": [{"find": "...", "replace": "..."}],
  "last_template": {"organs": ["Parotid_L", ...],
                    "gt_manufacturer": "Varian",
                    "gt_filename": "manual"},
  "drawers": [
    {
      "organ_name": "Parotid_L",
      "truncate": false,
      "gt_comparison": true,
      "staple_consensus": false,
      "staple_include_gt": true,
      "expanded": false,
      "patients": [
        {"patient_id": "HN1",
         "gt": {"rtstruct_sop_uid": "...", "rtstruct_filename": "...",
                "source_label": "...", "roi_number": 5, "roi_name": "..."},
         "tests": [
           {"rtstruct_sop_uid": "...", "source_label": "...",
            "organ_name": "...", "roi_number": 12, "similarity": 0.92,
            "below_threshold": false, "match_method": "tg263"}
         ]}
      ],
      "denylist": [{"patient_id": "HN1", "sop_uid": "...", "roi_number": 99}]
    }
  ],
  "consensus_groups": [
    {"patient_id": "HN1", "source_label": "Manual",
     "for_uid": "...", "synthetic_sop_uid": "AUTOSEG.SYNTHETIC.123",
     "organs": [{"roi_number": 1, "roi_name": "Parotid_L",
                 "constituents": [{"sop_uid": "...", "roi_number": 5},
                                  {"sop_uid": "...", "roi_number": 8}]}]}
  ]
}
```

**Restore order:** consensus_groups are restored *before* drawers, so
synthetic RTSSes exist by the time drawer-restore code resolves their
SOP UIDs.

**Year-later workflow:** load an old session against a folder that's
gained a new vendor's RTSSes → drawers restore exactly as saved → click
Run Auto-Match → existing patient subsections get their test list
refreshed (`_refresh_tests_for_existing`) with the new RTSSes, denylist
respected, manually-curated existing tests preserved.

---

## Performance engineering

### Per-patient cache eviction (Tab 4 metrics worker)

**File:** [`src/autoseg_evaluator/workers/metrics_worker.py`](../src/autoseg_evaluator/workers/metrics_worker.py)

Group iteration is sorted **patient-major** in `_enumerate_groups`. When
the loop crosses a patient boundary, `_evict_patient_caches` pops the
CT, dose dataset, all masks, and all RTSTRUCT pydicom datasets for the
previous patient. Peak RAM is bounded by a single patient's data
instead of the entire cohort's. `gc.collect()` is called to reclaim
SimpleITK's C++-backed memory.

### Early CT eviction within the last group per patient

When iteration is about to leave a patient, the CT can be dropped after
mask building but *before* the slow metric-computation phase begins. The
worker tracks `last_group_idx_for_patient` and passes a
`drop_ct_after_masks` flag into `_compute_group`. Saves ~200 MB during
the slowest phase.

### Per-organ mask eviction (Tab 2 inter-observer)

`_compute_inter_manual_rows` pops `(sop_uid, roi_number)` mask cache
entries as soon as each organ's pairwise loop finishes. Each ROI
belongs to exactly one organ bucket so cached masks are never reused
across organs — keeping them inflated peak RAM linearly with
`(raters × organs)`. A 5-rater × 12-organ patient drops from ~3 GB peak
to ~250 MB.

### Cancel granularity

Both the metrics worker and the inter-observer dialog check
`_cancelled` / `progress.wasCanceled()` inside every inner loop (per
test mask load, per metric row, per pairwise comparison) — not just at
group boundaries. Cancel responds within ~1 second on real data.

The QProgressDialog in Tab 2 has `setAutoReset(False)` and
`setAutoClose(False)` so it doesn't hide itself on cancel and re-show
on the next `setValue` tick.

---

## Validation & test suite

**Test runner:** pytest, 341 tests, ~9 s wall clock.

**Coverage highlights:**

| File | Tests | What it pins |
|---|---|---|
| `test_surface_distance.py` | ~30 | Bit-for-bit parity with `google-deepmind/surface-distance` on synthetic + SAMPLE-DATA fixtures |
| `test_metrics.py` | ~15 | APL parity with PlatiPy; geometric metric aggregator |
| `test_matching.py` | ~20 | Levenshtein + cosine algorithm, canonicalisation, method labelling (tg263 / fuzzy / none), Match dataclass |
| `test_tg263_synonyms.py` | ~20 | Bridging cases (Eyeball_L → Eye_L, OpticNerve_L → OpticNrv_L, etc.) AND pitfall non-collapses (Eye_L ≠ Eye_R, VB_L ≠ VB_R, Bone_Lacrimal ≠ Glnd_Lacrimal); regression tests for the four matcher-substitution bugs |
| `test_staple.py` | ~15 | StapleConfig defaults (MICCAI), adaptive bbox sizing, outlier-rater detection |
| `test_synthetic_consensus.py` | 14 | RTSTRUCTEntry synthetic fields, library register/unregister/clear/list, session round-trip with consensus_groups, v1 backward-compat |
| `test_session.py` | ~10 | Round-trip preserving drawers + rules + template + (v3) denylist + (v2) consensus_groups |
| `test_results.py` | ~10 | ResultsManager column ordering, display labels with τ decoration, CSV export shape |
| `test_metadata.py` | ~15 | MetadataLibrary scan, source-label cascade, FoR-UID merging, anonymisation aliases |
| `test_truncation.py` | ~7 | truncate_to_gt_z_extent slice/mm reporting; gt_z_extent_mm physical extent |
| `test_metrics_worker.py` | ~6 | STAPLE-summary extraction, mode labels, progress weighting, sens/spec helper, DVH Δ-vs-GT |
| `test_widgets.py` | ~10 | OrganDrawer mutations, drag-drop payload format |
| `test_compute_tab.py` | ~10 | Compute config emission, settings round-trip |
| `test_match_logic.py` / `test_source_labels.py` | ~10 | Smaller utility tests |
| `test_platipy_equivalence.py` | 4 | PlatiPy mask-rasteriser equivalence on a synthetic CT + RTSTRUCT (square, donut with XOR hole, multi-slice) |
| `test_metrics_equivalence.py` | 8 | Bit-for-bit equivalence of Dice / HD100 / HD95 / Surface Dice @ 3 mm / mean surface distance against ``google-deepmind/surface-distance`` and APL total / mean against PlatiPy 0.7.2 |
| `test_staple_equivalence.py` | 3 | Bit-for-bit equivalence of AutoSeg's STAPLE wrapper (per-rater sensitivity/specificity + binary consensus) against a direct ``SimpleITK.STAPLEImageFilter`` invocation, on a synthetic 3-rater fixture |
| `test_dvh_equivalence.py` | ~7 | Bit-for-bit equivalence of AutoSeg's DVH statistics (Dmin/Dmean/Dmax, D% , Dcc, VGy) against ``dicompyler-core``; single-slice OAR recovery (explicit thickness); z-extent truncation drops/restores contour planes |
| `test_version.py` | 3 | `__version__` resolution + portable-bundle `_version.py` fallback |

**Empirical clinical validation:** every numerical engine was cross-checked
against its upstream reference on the SAMPLE DATA HN1 cohort, each producing
a PHI-safe markdown report under `docs/` and reproducible via a script under
`scripts/`:

| Engine | Reference | Result | Report |
|---|---|---|---|
| Mask rasterisation | PlatiPy 0.7.2 | 110/110 ROIs voxel-exact | `VALIDATION_REPORT.md` |
| 7 geometric metrics | surface-distance + PlatiPy | 63/63 comparisons Δ = 0 | `VALIDATION_REPORT.md` |
| STAPLE consensus | `SimpleITK.STAPLEImageFilter` | 55/55 consensus runs bit-exact (sens/spec + voxels) | `STAPLE_VALIDATION_REPORT.md` |
| DVH dose statistics | `dicompyler-core` 0.5.6 | 2970/2970 stat comparisons Δ = 0 (297 ROIs) | `DVH_VALIDATION_REPORT.md` |

The STAPLE and DVH reference libraries (`SimpleITK`, `dicompyler-core`) are
core dependencies, so their equivalence tests run in CI with no extra
install; `surface-distance` and `platipy` are installed explicitly in the CI
workflow for the mask/metric equivalence tests.

**Bit-for-bit PlatiPy parity for mask rasterisation** (v2.3.1):
[`test_platipy_equivalence.py`](../tests/test_platipy_equivalence.py)
generates a tiny synthetic CT + RTSTRUCT and asserts AutoSeg's
`extract_mask_for_roi` produces a numpy-equal output to PlatiPy's
`transform_point_set_from_dicom_struct` across three ROI shapes:
single-slice square, donut (XOR-produced hole), and multi-slice
square. CI installs `platipy>=0.7` explicitly so this test runs on
every push. The test was also verified offline against the full HN1
sample cohort: **110/110 ROIs voxel-exact across two RTSSes**,
including donut-shaped Spinal_Canal, 4.9M-voxel BODY, and structures
down to 88 voxels (Lens_L).

**Bit-for-bit metric parity across seven metrics** (v2.3.2):
[`test_metrics_equivalence.py`](../tests/test_metrics_equivalence.py)
extends the regression net to the metric implementations themselves.
On a synthetic GT/Test pair with non-trivial overlap (offset 12-mm
half-width squares spanning four CT slices each), AutoSeg's output
is asserted equal to:

* `surface_distance.compute_dice_coefficient` — volumetric Dice,
* `surface_distance.compute_robust_hausdorff` at 100% and 95% — HD100 / HD95,
* `surface_distance.compute_surface_dice_at_tolerance` at 3 mm — Surface Dice,
* `surface_distance.compute_average_surface_distance` — mean surface distance,
* `platipy.imaging.label.comparison.compute_metric_total_apl` — Total APL,
* `platipy.imaging.label.comparison.compute_metric_mean_apl` — Mean APL.

The test was also verified offline on the HN1 cohort across nine
shared organ pairs (brainstem, spinal cord, mandible, larynx, oral
cavity, bilateral parotids, bilateral cochleae): **63/63 metric–ROI
comparisons produced zero absolute difference** (max |Δ| = 0.000e+00
across every metric). CI installs both `platipy>=0.7` and
`surface-distance` so the regression test runs on every push.

**Reviewer-ready validation report** ([`docs/VALIDATION_REPORT.md`](VALIDATION_REPORT.md)):
generated by [`scripts/validate_against_upstream.py`](../scripts/validate_against_upstream.py),
it contains the full per-ROI per-metric breakdown on the HN1 cohort
(110 ROIs of mask comparison, 9 ROIs × 7 metrics) plus the software
environment versions. PHI-safe by construction: no SOPInstanceUIDs,
no filenames, no patient identifiers, no dates, no institution
metadata — only anonymised ROI display names and numeric voxel /
metric values. Anyone can re-run the script against their own
data to reproduce the parity property independently.

---

## Theming & accessibility

**File:** [`src/autoseg_evaluator/ui/theme.py`](../src/autoseg_evaluator/ui/theme.py)

- **Light mode** — qt-material stock `light_blue.xml` unchanged.
- **Dark mode** — VS Code-inspired high-contrast palette overridden via
  qt-material's `extra` dict: `secondaryColor=#1e1e1e` (editor grey),
  `secondaryTextColor=#d4d4d4` (near-white body text), `primaryColor=#1f9bff`
  (VS Code blue). Fixes qt-material's default dark-on-dark text issues.
- Toggle via View → Theme. Choice persists in `settings.json`.

**Colourblind-safe similarity indicator:** `SignalBar` uses blue/amber
with 8 pips of length, so colour + length both encode the score.

**Cancel-button responsiveness:** progress dialogs use `QApplication.processEvents()`
between work units so the cancel button stays clickable during long
operations.

---

## Distribution model

**Approach:** portable Python bundle (NOT PyInstaller).

**Why:** hospital IT acceptance. The bundle ships the official Python
embeddable distribution (signed by the PSF) + `Lib/site-packages` +
your source tree as plain `.py` files + a 3-line `.bat` launcher.
Everything in the zip is auditable; no obfuscated `.exe`; no installer;
no registry writes; no admin rights; no AV false positives.

**Settings location:** `%USERPROFILE%\.autoseg_evaluator\settings.json`
— per-user, survives folder replacement on update. Matches what every
mainstream clinical app does.

**Build pipeline** (added v2.1):
[`scripts/build_portable.py`](../scripts/build_portable.py) downloads
the official CPython 3.11 embeddable distribution, patches the `._pth`
file to enable site-packages, bootstraps pip via the upstream
`get-pip.py`, installs the runtime dependencies from
`requirements.txt` into the bundle's `Lib/site-packages/`, copies the
project source into `app/autoseg_evaluator/`, and writes the `.bat`
launcher + bundle-local `README.txt`. Output:
`dist/AutoSegEvaluator-v{version}/` plus a matching `.zip` of the same.

**Release workflow**
([`.github/workflows/release.yml`](../.github/workflows/release.yml)):
on every `v*` tag push, builds the portable bundle on a
`windows-latest` runner, attaches the resulting `.zip` to a GitHub
Release (with auto-generated release notes), and also uploads it as a
30-day workflow artifact. The release body links the README and notes
that no Python install is required at the end user.

**CI workflow**
([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)): ruff
check + `ruff format --check` on Ubuntu, plus the full pytest suite on
`windows-latest` + `ubuntu-latest` with `QT_QPA_PLATFORM=offscreen`
for headless Qt. Runs on every push to `main` and every PR.

**Version single-source-of-truth** (v2.2): `autoseg_evaluator.__version__`
reads from package metadata via `importlib.metadata.version()`, so
bumping `pyproject.toml` updates the window title bar and every other
`__version__` reference automatically — no hardcoded duplicate.

---

## Known limitations / future work

### Acknowledged limitations
- Voxel-counting volume (vs. contour-integral volume from the original
  DICOM points). ~1-3 % difference for small structures. Documented in
  the data-model section.
- Mask creation rounds physical points to integer voxels via
  `TransformPhysicalPointToIndex` — standard practice but introduces
  sub-voxel error. Matches PlatiPy / rt-utils / dcmrtstruct2nii.
- `CLOSED_PLANAR` contour geometry only; `POINT` and `OPEN_PLANAR` are
  skipped (matches v1).
- DVH for the STAPLE consensus uses the thresholded binary mask, not
  the probabilistic mask (which would require a different DVH
  formulation entirely).
- DVH for a **single-slice** OAR assumes a slab thickness equal to the
  dose grid's z-spacing (dicompyler can't infer it from one plane). Dose
  points (Dmax/Dmean/D{X}) are well-defined regardless; volume-based
  points (D{X}cc) carry that assumption.
- A **truncated** test's DVH describes the dose over the GT's
  craniocaudal range, not the structure's full delivered dose — the
  consistent choice for a like-for-like comparison, but worth noting it
  is not the whole-structure DVH.

### Shipped since v2.1
- **v2.1.0** — Portable Windows bundle + GitHub Actions CI / release
  pipeline + README rewrite (Linux / macOS install sections added).
- **v2.2.0** — `D at volume (cc)` DVH input; window-title fix via
  `importlib.metadata.version()`.
- **v2.3.0** — Template GT identifier queries `source_label` (cascade
  + override) instead of raw Manufacturer tag; six raw DICOM columns
  in Manage Source Labels (Manufacturer, StructureSetLabel,
  SoftwareVersions, StructureSetName, StructureSetDescription,
  ManufacturerModelName); user-resizable columns + right-click
  show/hide.
- **v2.4.0** — Tab 2 redesigned around a **multi-observer model**:
  observers are distinct source labels selected by the user; eligibility
  and grouping are per-patient over that observer set. Three-column
  layout (Eligible patients / editable Organ groupings / Unmatched
  tray) with independent scroll zones, per-patient match threshold,
  lock-on-edit + Reset, drag-drop and `Assign ▾`, representative-based
  bucket scoring, and labelling-warning badges. Source-label
  disambiguation columns + assisted propagation (so each observer gets
  a distinct label). Results gained a dedicated **STAPLE Details** row,
  provenance-tagged modes (`Multi-observer STAPLE`, `Generic STAPLE
  with/no GT`), per-test sensitivity/specificity vs a consensus GT,
  `gt dose` parity across Tab 2/Tab 3 STAPLE, and `D at volume (cc)`
  for synthetic-mask DVHs. Fixed a `GetArrayViewFromImage`
  use-after-free in the sens/spec helper that produced garbage on
  Linux; STAPLE constituents are freed + `gc.collect()`-ed to cap RAM.
- **v2.4.1** — Empirical **STAPLE** validation (vs
  `SimpleITK.STAPLEImageFilter`, 55/55 bit-exact) and **DVH** validation
  (vs `dicompyler-core`, 2970/2970 bit-exact) with PHI-safe reports +
  reproducer scripts + CI-locked equivalence tests. **DVH Δ-vs-GT**
  columns (test − GT per metric). **Single-slice OAR DVH** via an explicit
  thickness (works around a dicompyler plane-thickness limitation). Fixed
  the portable bundle reporting `0.0.0+unknown` (stamps `_version.py`).
  Tab 4 progress bar reworked to weighted (drawer × patient) work units —
  exact and monotonic; "Drawers complete" now counts every evaluation,
  not deduped unique organs.
- **v2.4.2** — **Truncation-aware DVH**: when *Truncate* is active the
  test DVH is computed over the GT's craniocaudal extent (contour planes
  outside it are dropped before dicompyler), matching the geometric
  comparison. A whole-mask DVH engine was prototyped for this and rejected
  (~50 % V{X}Gy divergence vs dicompyler); the untruncated path stays
  bit-for-bit identical to dicompyler-core.
- **v2.5.0** — **Application icon + splash screen**. Window / taskbar / dock
  icon (multi-resolution `.ico` on Windows via an explicit `AppUserModelID`
  so it no longer inherits the interpreter icon; PNG via Qt on macOS / Linux)
  and a startup splash shown before the heavy imports load. Assets live in
  `autoseg_evaluator/assets/` (packaged via `package-data`). **Icon'd
  launchers**: the portable bundle launches via `pythonw.exe` (no console
  flash) and ships a `Create Desktop Shortcut.vbs`; `scripts/install-linux-
  desktop.sh` registers a freedesktop `.desktop` entry. Splash added as the
  README hero banner.
- **v2.5.1** — **Sub-dose-grid OARs no longer dropped from the DVH**. A
  structure smaller than the dose-grid spacing (~1–2 voxels) could fall
  between dicompyler's point-in-polygon sample points, rasterise to zero
  volume, and get no DVH. `compute_dvh_metrics` now retries once on a
  supersampled grid (¼ of the dose spacing) when a contoured structure
  yields zero volume — cheap, sub-grid-only, and never alters a structure
  that already computed, so the bit-for-bit dicompyler validation still holds.
- **v2.5.2** — **Removed the inert STAPLE `target_fg_ratio_min` parameter**
  (and its Compute-tab spinbox). The adaptive bbox only ever *grows*, which
  can only *lower* the foreground/bbox ratio, so a lower-ratio target was
  never enforceable; only the upper target (`target_fg_ratio_max`) is kept.
  STAPLE output is unchanged (still 55/55 bit-exact). Older `settings.json`
  files carrying the key still load — it is ignored.

### Pending features
- **Docs**: full user guide, hospital deployment doc, metrics reference,
  developer guide.
- Inter-rater Dice matrix as an optional results-table addition
  (currently only available in the Tab 2 inter-observer dialog).
- Optional dose-weighted Dice / HD (mentioned in earlier brainstorms but
  deferred).

### Suggested deferral
- Excel multi-sheet export with frozen headers + conditional formatting.
  Would require `openpyxl` (~50 KB pure-Python, Apache-2.0 — IT-friendly)
  but the user explicitly chose to defer in favour of plain CSV +
  in-Excel analysis.

---

## Quick literature index

| Algorithm / concept | Reference |
|---|---|
| Levenshtein + cosine hybrid matching | v1 (Rusanov 2025 prototype `GUI23v13.py`) |
| TG-263 nomenclature | Mayo et al., *AAPM TG-263* (Pract Radiat Oncol 2018) |
| Dice coefficient | Dice, *Ecology* 1945 |
| Hausdorff 95 % robust metric | Aydin et al., *Med Phys* 2021 |
| Mean surface distance | Standard summary; google-deepmind/surface-distance impl. |
| Surface Dice + tolerance | Nikolov et al., DeepMind 2018 (arXiv:1809.04430) |
| Added Path Length | Vaassen et al., *Phys Med* 2020 (PlatiPy implementation) |
| STAPLE | Warfield, Zou & Wells, MICCAI 2002 / IEEE TMI 2004 |
| STAPLE in multi-atlas pipelines | Heckemann *NeuroImage* 2006; Iglesias & Sabuncu *Med Image Anal* 2015 |
| STAPLE adaptive prior / bbox | Asman & Landman, *IEEE TMI* 2011 ("COLLATE") |
| Inter-observer Dice reporting | ICRU Report 91 |
| Volume convention (voxel-count) | RTOG retrospective analyses |
| Dose interpolation onto mask | ICRU Report 83 |

---

## Glossary of acronyms

- **APL** — Added Path Length
- **COM** — Centre of Mass
- **DSC** — Dice Similarity Coefficient
- **DVH** — Dose-Volume Histogram
- **EM** — Expectation-Maximisation (STAPLE's algorithm)
- **FoR UID** — FrameOfReferenceUID (DICOM tag linking image + structure + dose)
- **GT** — Ground Truth contour
- **HD** — Hausdorff Distance
- **MSD** — Mean Surface Distance
- **OAR** — Organ at Risk
- **PRV** — Planning Risk Volume (expanded OAR)
- **PMB** — Physics in Medicine and Biology (journal)
- **ROI** — Region of Interest (one contour in an RTSTRUCT)
- **RTSS** — RT Structure Set (DICOM RTSTRUCT file)
- **SOP UID** — Service-Object Pair Instance UID (unique identifier per DICOM instance)
- **STAPLE** — Simultaneous Truth And Performance Level Estimation
- **TG-263** — AAPM Task Group 263 (standardised nomenclature)
- **TPS** — Treatment Planning System

---

*Document version 5 — updated 2026-06-17 against v2.5.2.
Tracks: portable bundle + CI/release pipeline (v2.1), `D at volume (cc)`
DVH input + window-title fix (v2.2), template source-label match +
expanded Manage Source Labels columns (v2.3), the Tab 2 multi-observer
consensus redesign + STAPLE result-row parity (v2.4.0), STAPLE/DVH
empirical validation + DVH Δ-vs-GT + single-slice DVH + bundle/progress
fixes (v2.4.1), truncation-aware DVH (v2.4.2), application icon + splash +
icon'd launchers (v2.5.0), sub-dose-grid DVH recovery (v2.5.1), and removal
of the inert STAPLE `target_fg_ratio_min` parameter (v2.5.2).*
