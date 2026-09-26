# AutoSeg Evaluator v3 — Project Overview

A comprehensive reference for the v3 codebase. Written for two audiences:

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
4. [User-facing workflow (the seven tabs)](#user-facing-workflow-the-seven-tabs)
5. [Data model](#data-model)
6. [Matching pipeline (organ-name canonicalisation)](#matching-pipeline-organ-name-canonicalisation)
7. [Reading the contours (both streams)](#reading-the-contours-both-streams)
8. [Mask creation (RTSTRUCT → binary)](#mask-creation-rtstruct--binary)
9. [Data linking](#data-linking)
10. [3D mask metrics](#3d-mask-metrics)
11. [Volume + centre-of-mass metrics](#volume--centre-of-mass-metrics)
12. [2D contour metrics](#2d-contour-metrics)
13. [Dose-volume histogram (DVH)](#dose-volume-histogram-dvh)
14. [STAPLE consensus](#staple-consensus)
15. [Build Consensus GT workflow](#build-consensus-gt-workflow)
16. [Organ grouping and the Report tab](#organ-grouping-and-the-report-tab)
17. [Session save / load](#session-save--load)
18. [Performance engineering](#performance-engineering)
19. [Validation & test suite](#validation--test-suite)
20. [Theming & accessibility](#theming--accessibility)
21. [Distribution model](#distribution-model)
22. [Known limitations / future work](#known-limitations--future-work)
23. [Quick literature index](#quick-literature-index)
24. [Glossary of acronyms](#glossary-of-acronyms)

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

**v2 → v3 highlights** (see [`V3_RELEASE_STATUS.md`](V3_RELEASE_STATUS.md)
for what each moves):
- **Two metric streams.** 3D mask metrics sit beside 2D contour metrics
  measured on the RTSTRUCT outlines themselves (APL, NAPL, 2D Hausdorff, mean
  and median contour distance). Mask APL is removed: added path length needs
  the edge as drawn, so it now comes only from the 2D stream.
- **One reading of every contour** shared by both streams, and a half-open
  3D fill that keeps shapes' true area.
- **Sub-voxel (`continuous`) rasteriser as the default**; the v1 fill is kept
  as the opt-in `legacy` backend.
- **Data linking by explicit reference**, dose → structure set → series, with
  no RTPLAN and ambiguity settled by the user.
- **Canonical organ grouping** and a **Report tab** with per-organ paired
  statistics, figures and PDF export.
- **Metric definitions** dialog and an optional **audit record** beside every
  export.

---

## Tech stack & licensing

| Component | Version | Licence | Role |
|---|---|---|---|
| Python | 3.12+ | PSF | Runtime |
| PySide6 | 6.11+ | LGPL-3.0 | GUI toolkit |
| qt-material | latest | BSD-2 | Theming (light + custom dark palette) |
| SimpleITK | latest | Apache-2.0 | Image I/O, STAPLE filter, resampling |
| pydicom | latest | MIT | DICOM dataset parsing |
| dicompyler-core | 0.5.6 | BSD-3 | v2's DVH, now scored only by the DVH benchmark (`validation` extra) |
| numpy | latest | BSD-3 | Array math |
| scipy | latest | BSD-3 | Distance transforms, filters |
| scikit-image | latest | BSD-3 | `legacy` rasteriser fill; contour tracing in the viewer |
| shapely | 2.x | BSD-3 | Contour reading (loops → regions) and 2D geometry |
| native contour metrics | 0.1.0 / 0.2.0.dev2 | see `third_party/` | 2D contour metric engines, vendored byte-identical |
| matplotlib | latest | PSF-based | Report figures; dose colour map |
| pytest | latest | MIT | Test runner |

**Overall project licence**: Apache-2.0 (see `LICENSE`).

**Why these choices:**
- PySide6 over PyQt5 for clean LGPL dynamic linking (avoids the
  static-linking grey area that PyInstaller `--onefile` creates with PyQt5
  + GPL).
- SimpleITK over raw ITK for the higher-level Pythonic API + bundled
  STAPLEImageFilter.
- The DVH is integrated over the contours by AutoSeg itself (v3) rather than
  taken from dicompyler-core (v2): measured against analytic truth,
  dicompyler-core's sampling and D{X} lookup were the largest DVH errors
  (`docs/DVH_METHOD_VALIDATION.md`).

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
│   │   ├── contour_reading.py       # Loops → regions, shared by both streams
│   │   ├── masks.py                 # RTSTRUCT→mask (reading + half-open fill), truncation, DICOM I/O
│   │   ├── metrics.py               # 3D mask metrics: compute_geometric_metrics
│   │   ├── surface_distance.py      # google-deepmind port (verbatim)
│   │   ├── contour_grid.py          # Image series → frame for the 2D metrics
│   │   ├── polygon_metrics.py       # 2D contour metrics: placement, engines, results
│   │   ├── staple.py                # STAPLE wrapper + adaptive bbox
│   │   ├── dvh.py                   # DVH integrated over the contours (or a consensus mask)
│   │   ├── dose.py                  # Dose resampled onto the CT (viewer overlay)
│   │   ├── organ_groups.py          # Canonical organ key: base / laterality / qualifier
│   │   ├── statistics.py            # Wilcoxon, Hodges–Lehmann, rank-biserial, sign test
│   │   ├── acquisition.py           # Allowlisted, non-identifying acquisition tags
│   │   ├── likert.py                # Qualitative assessment model
│   │   ├── readable.py              # Metric prose, units, axis scales, tolerances
│   │   └── source_labels.py         # Vendor-detection cascade
│   ├── vendor/                      # Supplied 2D metric engines — do not edit
│   ├── data/                        # In-memory models + persistence
│   │   ├── metadata.py              # MetadataLibrary, RTSTRUCTEntry, etc.
│   │   ├── linkage.py               # Explicit-reference series / dose resolution
│   │   ├── organ_index.py           # Organ grouping over the loaded cohort
│   │   ├── results.py               # ResultsManager + canonical column order
│   │   ├── report.py                # Report model: cases, pairing, directions
│   │   ├── sidecar.py               # .audit.json beside an export
│   │   ├── session.py               # Session JSON schema (v7)
│   │   └── synonyms.py              # TG-263 dict loader / flattener
│   ├── workers/
│   │   ├── scan_worker.py           # Background folder scan
│   │   └── metrics_worker.py        # Background metric compute (QThread)
│   ├── ui/
│   │   ├── main_window.py           # 7-tab shell, menus, session I/O
│   │   ├── theme.py                 # qt-material theme apply
│   │   ├── tabs/                    # One file per tab
│   │   │   ├── load_data.py
│   │   │   ├── build_consensus.py
│   │   │   ├── match_contours.py
│   │   │   ├── qualitative.py
│   │   │   ├── compute.py
│   │   │   ├── results.py
│   │   │   └── report.py
│   │   ├── dialogs/
│   │   │   ├── source_labels.py     # Manage Source Labels (sortable + bulk)
│   │   │   ├── data_links.py        # Review Data Links (ambiguous series / dose)
│   │   │   ├── replacement_rules.py # Find→Replace rules
│   │   │   ├── template.py          # Define auto-match template
│   │   │   ├── organ_labels.py      # Label Organs (curation for the statistics)
│   │   │   ├── metric_definitions.py  # How every metric is computed
│   │   │   └── visualization.py     # Match Contours viewer: GT + tests, dose wash
│   │   └── widgets/
│   │       ├── collapsible_box.py   # Accordion-section primitive
│   │       ├── dicom_tree.py        # Tab 1 cohort tree
│   │       ├── loaded_contours_tree.py  # Tab 3 left-side tree
│   │       ├── organ_drawer.py      # Tab 3 accordion drawer
│   │       ├── multiplanar_viewer.py  # Shared CT + contour viewer
│   │       ├── progress_panel.py    # Tab 5 progress UI
│   │       ├── stat_plots.py        # Report figures
│   │       └── signal_bar.py        # Similarity pip strip
│   ├── resources/
│   │   └── synonyms.json            # 663 canonicals, ~17k variants
│   └── utils/
│       ├── paths.py
│       └── settings.py              # settings.json round-trip
├── tests/                           # pytest suite (tests/vendor: supplier acceptance)
├── scripts/                         # Validators, report generators, bundle build
│   ├── validate_against_upstream.py     # Masks + 3D metrics vs PlatiPy / DeepMind
│   ├── validate_polygon_metrics.py      # 2D metrics vs published values
│   ├── validate_contour_reading.py      # Shared reading vs what it replaced, on a cohort
│   ├── validate_staple_against_upstream.py
│   ├── validate_dvh_methods.py          # DVH vs Nelms et al. 2015 + analytic phantoms
│   ├── compare_rasterisers.py
│   ├── make_register_tables.py          # Worked examples for the statistics register
│   ├── build_portable.py
│   └── build_synonyms.py            # Regenerate synonyms.json from TG-263 CSV
├── third_party/native_contour_metrics/  # Provenance, licences, C++ source, acceptance data
├── docs/                            # This file, specs, registers, validation reports
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

## User-facing workflow (the seven tabs)

```
1. Load Data        → 2. Build Consensus GT → 3. Match Contours    → 4. Qualitative
   folder scan,        (optional)              drawers per organ,     (optional)
   data links,         STAPLE-derive           organ labels,          Likert grading
   source labels       a synthetic GT          visualise

→ 5. Compute         → 6. Results           → 7. Report
   3D + 2D + DVH        table, CSV,             per-organ statistics,
   metric run           audit record            figures, PDF
```

### Tab 1 — Load Data

**File:** [`src/autoseg_evaluator/ui/tabs/load_data.py`](../src/autoseg_evaluator/ui/tabs/load_data.py)

**Responsibility:** Recursively scan a folder for DICOM files; build a
`MetadataLibrary` keyed by `PatientID → ImagingContext (one per
FrameOfReferenceUID) → [RTSTRUCT, RTDOSE, ImageSeries]`. The scan runs in
a background `ScanWorker` thread so the UI stays responsive. Files sharing a
`SOPInstanceUID` are ingested once — the same object exported to two paths
(`X.dcm` and `X.0001.dcm`) is one object, not two.

The Frame of Reference grouping above is a *container*, not the answer to
"which CT was this drawn on" — one FoR can hold several studies, structure
sets and doses. Those links are resolved separately by
[`data/linkage.py`](../src/autoseg_evaluator/data/linkage.py); see
[Data linking](#data-linking).

**Key UI elements:**
- **Review Data Links…** opens [`ui/dialogs/data_links.py`](../src/autoseg_evaluator/ui/dialogs/data_links.py)
  — a table of every structure set with the image series and dose it resolved
  to, the rule that decided it, and a dropdown to override. Links that could
  not be decided automatically are badged on the button, listed in the Issues
  panel, and block Tab 3 until settled.
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
the Compute tab's STAPLE settings.

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
4. **Label Organs…** — [`ui/dialogs/organ_labels.py`](../src/autoseg_evaluator/ui/dialogs/organ_labels.py):
   set the canonical organ each drawer is reported under in the statistics.
   Labels are label-only — they never merge drawers — and persist on
   explicit session save. See [Organ grouping and the Report tab](#organ-grouping-and-the-report-tab).

**Visualize** (per patient in each drawer) opens
[`ui/dialogs/visualization.py`](../src/autoseg_evaluator/ui/dialogs/visualization.py):
the ground truth and every matched test contour on the reference CT, in the
same multiplanar viewer the Qualitative tab uses (planes, level/window, zoom,
pan, contour opacity and thickness). It adds a per-source legend and, when the
patient has an RT Dose, a dose colour wash with an opacity control and scale.
The ground truth is drawn over every test and the view opens on its middle
slice.

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

### Tab 4 — Qualitative Assessment (Optional)

**File:** [`src/autoseg_evaluator/ui/tabs/qualitative.py`](../src/autoseg_evaluator/ui/tabs/qualitative.py)

Qualitative (Likert) scoring of each matched contour on the 5-point MD Anderson
scale (Baroudi et al., *Cancers* 2023), shown as a reference table in the tab.
The rating **stack** is built from the Tab 3 drawer snapshot by
`core/likert.build_rating_stack` — one item per contour.

**Per-grader configuration** (chosen in a dialog when the grader is added, then
fixed): *blinded* (one contour at a time, source hidden) vs *transparent* (every
source for the organ shown with labels + per-source visibility toggles, and the
contour being graded named in the status line — *Rating: VendorA — Parotid_L*,
or *ground truth* — and in bold in the list, so a score cannot be given to the
wrong outline unnoticed);
*include GT*; and *randomize* (group-aware — all sources of an organ stay
consecutive). Each grader gets their own order, scores and cursor.

**Multiplanar viewer** (`ui/widgets/multiplanar_viewer.py`, `QGraphicsView`):
axial / coronal / sagittal planes resliced from the CT (coronal & sagittal
flipped superior-up), ctrl+scroll zoom, Level/Window sliders, contour outlines
(`skimage.measure.find_contours`, cosmetic pens) with opacity + thickness, and
an active-contour highlight. CT/mask loading reuses `core/masks` with a small
per-patient cache; the card swipes (`QPropertyAnimation`) on each rating.

While grading, **the other tabs are locked** (`assessmentLockChanged` →
`QTabWidget.setTabEnabled(False)`) to prevent blinded-data leakage; an **Unlock**
button leaves and keeps progress. Scores are emitted via `qualitativeScored` and
stored in the Results tab as `likert_<grader>`, `scored_at_<grader>` (when the
score was given) + `qualitative_assessed` / `qualitative_blinded` columns. A
score is keyed by patient, drawer, source, **structure set** and ROI number.
Graders, their fixed configs and all scores, each with its time, are saved in the
session and re-emitted into Results on load. A score whose contour is no longer
among the drawers on restore — a drawer renamed or removed, drawers that did not
restore — is kept, saved again, listed as a row of its own, and rejoins its
grader once the contour is back; a consensus ground truth is shown by building
its mask from its raters, as Compute does.

### Tab 5 — Compute

**File:** [`src/autoseg_evaluator/ui/tabs/compute.py`](../src/autoseg_evaluator/ui/tabs/compute.py)

**Two geometry groups side by side**, because they are two methods, not one
list: a reader compares them where they sit together.

- **3D mask metrics (rasterised)** — Dice, precision + recall (one checkbox,
  two columns), Surface Dice, 3D Hausdorff 100% and 95%, Mean Surface Distance,
  Volume, COM offset; all on by default. **Surface Dice τ (mm)**, default
  3 mm (Nikolov 2018), takes a list — "1, 2, 3" — and each tolerance fills its
  own column; the surface distances are computed once.
- **2D contour metrics (native RTSS polygons)** — APL, NAPL, 2D Hausdorff 100%
  and 95%, 2D mean and median contour distance; all off by default, so an
  existing install does not start producing a second set of columns because it
  was upgraded. **APL τ (mm)**, default 3 mm, also takes a list; both 2D
  engines measure every tolerance in one call. A note states which 2D engine
  will run (compiled, or the portable reference engine where no library is
  packaged). All six come from one engine call, so a narrower selection buys a
  narrower table, not a shorter run.

**Metric definitions…** opens [`ui/dialogs/metric_definitions.py`](../src/autoseg_evaluator/ui/dialogs/metric_definitions.py),
the reference for exactly how every metric is computed: what it is measured
on, how the two directions are combined, how contours are read, which planes
take part in the 2D metrics and why truncation does not affect them.

**Record audit detail** decides, before the run, whether the detail behind
every number is kept for a `.audit.json` beside the export (it cannot be
recovered from a finished table).

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
silently mutate parameter values. **Compute All is disabled while a run is
in progress**; Cancel stops it after the current structure.

**One computation per results table.** When Results already holds computed
rows, Compute All asks first — *Export, then replace*, *Replace* or *Cancel* —
and replaces the table. Rows are never updated or merged, so every row in a
table comes from one run with one set of settings. Likert scores are not part of
a computation and are kept.

**Live progress panel** (`ui/widgets/progress_panel.py`): the metrics
worker drives it with structured updates. Progress is measured in
(drawer × patient) work units weighted by rater count — known exactly up
front, so the bar is honest and monotonic regardless of how many result
rows a group emits (v2.4.1; the previous row-count estimate left the bar
stuck/short). "Drawers complete" counts every drawer × patient
evaluation, not deduped unique organs; the panel also shows the current
patient / drawer / test / metric, elapsed, ETA, and error count.

### Tab 6 — Results

**File:** [`src/autoseg_evaluator/ui/tabs/results.py`](../src/autoseg_evaluator/ui/tabs/results.py)

**Sortable QTableWidget** with one row per metric computation. Column
order is **deterministic across runs** (defined in
`CANONICAL_METRIC_COLUMNS` — see [data model](#data-model) below) so
Excel paste-align stays consistent. DVH columns sit at the rightmost
end of the canonical block.

**Visual column groups:** custom `_BandedHeaderView` paints a 4-pixel
coloured stripe along the bottom of each header section, keyed to the
column's family — identifier (indigo), volumetric overlap (cyan),
surface distance (orange), 2D contour metrics (teal), volume + COM
(green), STAPLE (pink), qualitative/Likert (brown), DVH (purple). Tooltip
names the band on hover. 2D and 3D Hausdorff are named as such in the
headers, so the two streams cannot be confused.

**2D status column:** a 2D metric that could not be computed leaves its cell
empty and says why here — a consensus ground truth has no contours, the
structures share no plane, a contour is off its slice plane, a median or 95%
value is undetermined. Never a zero.

**Qualitative (Likert) columns (v2.6.0):** `likert_<grader>` scores plus
`Qualitative` (assessed yes/no) and `Blinded` flags. They are stored
against the contour and **overlaid onto its existing metric row** at read
time (so each contour stays one row), positioned immediately before the
first dose column.

**Tolerance in every tolerance-dependent column:** the tolerance is part of
the metric key — `surface_dice@3mm`, `poly_apl_mm@1.5mm` — so each tolerance
is its own column, headed e.g. `Surface Dice @ 3.00 mm`, and its own metric in
the report. A column can only ever show the values computed at its tolerance
([`core/tolerance_keys.py`](../src/autoseg_evaluator/core/tolerance_keys.py);
every lookup by metric name goes through `base_metric`).

**Computed at** (a metadata column): when each row was produced, local time with
its UTC offset. **Clear** discards the computed rows only; Likert scores belong
to the Qualitative tab.

**DVH Δ-vs-GT columns (v2.4.1):** when DVH is enabled, each test row's
DVH metric also gets a `… Δ vs GT` column (test − GT) — e.g. `D2cc (Gy)
Δ vs GT` — clustered after the absolute DVH columns, in both the table
and CSV.

**Excel-friendly copy:** `Ctrl+A` then `Ctrl+C` copies all rows + headers
as TSV. **Export CSV…** writes to disk, and when the run recorded audit
detail, a `results.audit.json` beside it ([`data/sidecar.py`](../src/autoseg_evaluator/data/sidecar.py)):
both directions of each distance, what each stream measured over, how each
contour was read, the rasteriser and 2D engine with their settings. It holds
no patient identifiers beyond those already in the table.

### Tab 7 — Report

**File:** [`src/autoseg_evaluator/ui/tabs/report.py`](../src/autoseg_evaluator/ui/tabs/report.py)

Per-organ comparison of sources on the computed results. Pick the ground truth
(a consensus is preferred where one exists), a metric — grouped into 3D mask,
2D contour, dosimetric and other families so a reader never slides between two
Hausdorffs unnoticed — and a comparison; the tab shows paired statistics,
forest, paired and distribution figures, a coverage table, and an acquisition
summary. **Export PDF** writes the page as a clinical report. See
[Organ grouping and the Report tab](#organ-grouping-and-the-report-tab).

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
- The rows of **one computation** (`rows()` returns a copy with Likert scores
  overlaid). `clear_computed()` discards them and keeps the scores — what
  replacing a computation does; `clear()` discards both, for a new cohort.
- `metric_columns()` returns the canonical column order
  (`CANONICAL_METRIC_COLUMNS`), with each tolerance-dependent metric expanded
  in place to one column per tolerance present, PLUS any dynamic columns (user
  `D{X}_gy`, `V{X}gy_cc`) appended via `_dynamic_metric_sort_key`.
- `metric_display_label(key)` — static `_METRIC_LABELS` dict + dynamic DVH
  naming + the tolerance read from the key (`Surface Dice @ 3.00 mm`).
- `tolerances_in_use()` — every tolerance the columns carry, by stream, for
  the audit record.
- `session_state()` / `apply_session_state()` — the computed rows for the
  session file (NumPy values made plain; ±∞ kept).
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

## Reading the contours (both streams)

**File:** [`src/autoseg_evaluator/core/contour_reading.py`](../src/autoseg_evaluator/core/contour_reading.py)

An RTSTRUCT stores outlines, not regions. Before either metric stream measures
anything, `read_outlines()` reads each structure's outlines on each slice into
a region — which loops are islands, which are holes, what an outline that
touches or crosses itself encloses. The 3D stream fills those regions onto
voxels and the 2D stream measures their outlines, so the two can differ only in
how they measure, never in what they take the contour to be. (Before v3 the
mask path combined every loop by exclusive-or and the 2D path used a stricter
parser, and they disagreed on touching, overlapping and self-crossing loops;
spec D10.)

| What is on the slice | How it is read |
|---|---|
| Loops declared `CLOSEDPLANAR_XOR` | overlaps cancel, as declared |
| Loops apart | separate islands |
| A loop inside a loop | a hole; an island inside a hole is inside again, by depth |
| Loops touching at an edge or a point | merged |
| Loops partially overlapping, or one loop drawn twice | **refused** — union and hole are both plausible and give different tissue |
| One outline touching or crossing itself | the region it encloses when the even-odd and non-zero winding rules agree on it; lines enclosing nothing (a spike) dropped; **refused** when the rules disagree |
| An outline enclosing no area | dropped |

Decisions are area-based at 1e-8 mm², so rounding cannot flip a classification
between the two streams' coordinate frames. A refused structure gets no
metrics in either stream, and the row's error names the rule and the slice.
Anything interpreted rather than read as declared is recorded per structure in
the audit record (`contour_reading`).

**Placement stays per stream**, because the streams need different things: the
3D fill uses the nearest slice and allows half a slice of tilt; the 2D metrics
need every contour within 0.001 mm of its slice plane and inside the image.

`scripts/validate_contour_reading.py` checks the reading on a real cohort
against what it replaced. On the tender H&N cohort (5,166 structures) the 2D
regions were identical to the supplier's parser on all 5,143 structures both
read, and every one of the 22,123 3D voxels that changed had its centre exactly
on an outline edge.

---

## Mask creation (RTSTRUCT → binary)

**File:** [`src/autoseg_evaluator/core/masks.py`](../src/autoseg_evaluator/core/masks.py)

**Two selectable backends:** per call via `backend=`, process-wide via
`set_default_rasteriser()`, or with the `AUTOSEG_RASTERISER` env var.

**`continuous` (default)** — derived from dcmrtstruct2nii v5's
`DcmPatientCoords2Mask` (MIT; see [`NOTICE`](../NOTICE)):

1. Map each contour's physical points to **continuous** voxel coordinates
   (`physical_points_to_continuous_index`, one NumPy matmul), preserving
   sub-voxel position.
2. Assign each contour to the nearest slice; a contour spanning more than
   `PLANARITY_TOLERANCE_VOXELS` through-plane refuses the structure, and
   contours on slices outside the image are skipped.
3. Read the loops on each slice into regions with
   [the shared reading](#reading-the-contours-both-streams) (in voxel units,
   so the coordinates filled are the coordinates read).
4. Fill each region with a **half-open scanline**: a voxel is inside when its
   centre is inside the region, and a centre exactly on an edge belongs to one
   side only (`low <= row < high`, `start <= column < end`). Shapes keep their
   true area, and two regions sharing an edge neither both claim nor both drop
   the voxels along it. Away from such ties the voxels are exactly those
   scikit-image's point-in-polygon fill selected before v3.
5. Output a binary `sitk.Image` that shares spacing / origin / direction with
   the reference image.

`mask_with_reading()` returns the mask with what the reading interpreted, and
raises `MaskConversionError` naming the reason; `extract_mask_for_roi()` keeps
the older contract of returning `None`.

**`legacy`** — the v1 PlatiPy-derived port, kept to reproduce v1: every vertex
is snapped to the nearest voxel centre (`TransformPhysicalPointToIndex`), each
contour is filled with `skimage.draw.polygon`, and loops on a slice are
combined by exclusive-or. It does not use the shared reading, and the audit
record says so.

**Why the default changed.** The fill rule includes a voxel when its *centre*
lies inside the polygon — equivalent to ">50 % of the voxel covered" for a
locally straight boundary. Snapping vertices first places the contour boundary
exactly on the sampling lattice, the degenerate case where that test is
ambiguous; ties resolve as "inside", so every voxel the boundary touches is
filled. The result is a systematic one-directional dilation of ~0.76 voxels
around the perimeter, i.e. a relative volume over-estimate of ~`1.5 / R`
(R = radius in voxels): ~3 % for large organs, >50 % for structures one to two
voxels across. Measured against analytic disc phantoms the continuous backend
is within ~2 % for R ≥ 10 while legacy is +13.6 %; across 357 HN1 ROIs legacy
was larger in **every** case. See
[`docs/RASTERISER_COMPARISON.md`](RASTERISER_COMPARISON.md) and
`scripts/compare_rasterisers.py`.

**Supported contour geometry:** `legacy` accepts only `CLOSED_PLANAR` (judged
from the first contour in the ROI). `continuous` accepts what the shared
reading accepts — `CLOSED_PLANAR`, `INTERPOLATED_PLANAR` and `CLOSEDPLANAR_XOR`
(which before v3 got no mask) — and refuses open and point contours as a failed
conversion rather than a zero-volume structure.

**The half-open fill moved some masks.** On the tender H&N cohort 547 of 5,166
masks changed, every changed voxel an edge tie. Almost all were one vendor's —
the only one drawing along rows and columns of voxel centres — whose volumes
fell 0.1–3.5 % (6.25 % on a small eye): the previous fill had over-counted them.
Results for that vendor from before v3 are superseded.

**Truncation** (`truncate_to_gt_z_extent`): zeroes out test-mask slices
that fall outside the GT's craniocaudal extent. Returns the truncated
mask plus `{slices_removed, extent_removed_mm}` for results-table
reporting (per-row `Truncated slices` / `Truncated extent (mm)` columns).
Useful for cord, rectum, oesophagus where the AI may legitimately
extend beyond the manual GT. The companion `gt_z_extent_mm` returns the
same extent in physical mm so the DVH can apply the equivalent
contour-plane truncation (see [DVH](#dose-volume-histogram-dvh)) — keeping
dose and geometry on the same craniocaudal range.

**Reference-image lookup** (`find_reference_image_folder`): delegates to
[Data linking](#data-linking) to locate the CT folder backing a given RTSS.
Returns `None` when the link is ambiguous rather than picking a candidate.

---

## Data linking

**File:** [`src/autoseg_evaluator/data/linkage.py`](../src/autoseg_evaluator/data/linkage.py)

Decides which image series each RTSTRUCT was contoured on, and which RTDOSE
belongs with it. A `FrameOfReferenceUID` on its own cannot answer either
question: one Frame of Reference can legitimately contain several imaging
studies, structure sets and dose distributions, which is routine in
re-irradiation, replans and composite plans.

Resolution runs through an ordered cascade, strongest first:

| Tier | Rule |
| --- | --- |
| `override` | The user chose it in Review Data Links |
| `explicit` | A referenced UID names the target outright |
| `sop-overlap` | The structure set's per-slice `ContourImageSequence` UIDs are in the series |
| `for+study` | Same Frame of Reference *and* same study |
| `for` | Same Frame of Reference (v2's behaviour, unaided) |
| `singleton` | Exactly one candidate exists for the patient |
| `none` | Nothing matched |

When two or more distinct candidates survive at the winning tier the result is
**ambiguous**: no target is returned and every candidate is reported, so the
Load Data tab can ask. `DoseSummationType == "PLAN"` breaks a tie within a
tier only when exactly one candidate is a PLAN dose — two PLAN doses stay
ambiguous, which is the re-irradiation case v2 resolved silently to whichever
file it walked first.

**RTPLAN is never required nor traversed.** The dose → structure set edge is
read from the dose object's own `ReferencedStructureSetSequence`, including
the copy some writers nest inside `ReferencedRTPlanSequence`, so the chain
dose → structure set → series closes with no plan file present.

`assign_linkage_ids()` stamps a `linkage_id` on every series, structure set
and dose — the connected component of the explicit reference graph, i.e. one
coherent treatment context. Frame of Reference is never used to *merge*
components; doing so would recreate the collision this module prevents.

**Relationship to 3D Slicer.** The RTSTRUCT → series traversal is tag for tag
what SlicerRT does in
`vtkSlicerDicomRtReader::GetReferencedSeriesInstanceUID()`, and
[`tests/test_slicer_linkage_equivalence.py`](../tests/test_slicer_linkage_equivalence.py)
pins it against a port of that function. One deliberate difference: SlicerRT
calls `gotoFirstItem()` at each sequence level, so a structure set referencing
two series resolves silently to the first; here that is an ambiguity. For
dose, SlicerRT goes via `ReferencedRTPlanSequence`, and its DVH module does no
automatic dose↔structure pairing at all — `ComputeDvh` requires the user to
select both. So there is no external oracle for dose linking, and the
user-settles-it design matches Slicer's own answer, just moved earlier and
persisted.

---

## 3D mask metrics

**File:** [`src/autoseg_evaluator/core/metrics.py`](../src/autoseg_evaluator/core/metrics.py)
**Underlying primitives:** [`core/surface_distance.py`](../src/autoseg_evaluator/core/surface_distance.py),
Google DeepMind's surface-distance embedded verbatim

Computed on the binary masks from [Mask creation](#mask-creation-rtstruct--binary).
Each mask's surface elements come from its 2×2×2 voxel neighbourhoods, each
weighted by its surface area (mm²), and distances to the other surface come
from a Euclidean distance transform, so every distance is quantised to the
voxel lattice. The two directions are combined by taking the **larger** for
the Hausdorffs and the **equal average** for the mean surface distance.

`compute_geometric_metrics(gt_mask, test_mask, config)` is the
high-level aggregator; it dispatches to:

| Metric key | Function | Definition | Reference |
|---|---|---|---|
| `dice` | `compute_dice_coefficient` | 2·\|A∩B\| / (\|A\| + \|B\|) | Dice 1945 |
| `precision` / `recall` | `precision_recall` (local) | \|A∩B\| / \|B\| and \|A∩B\| / \|A\| for GT A, test B; NaN when the denominator is empty. Precision falls with over-segmentation, recall with under-segmentation; Dice is their harmonic mean, so F1 is not reported | — |
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
was verified by computing every surface-distance metric against the upstream
`surface-distance` package on the SAMPLE DATA HN1 cohort — Δ = 0.00e+00
across every metric.

Added path length is deliberately not among the 3D metrics: it measures
boundary to be redrawn, which needs the edge as drawn rather than as a voxel
staircase, so it is a [2D contour metric](#2d-contour-metrics).

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

## 2D contour metrics

**Files:** [`core/polygon_metrics.py`](../src/autoseg_evaluator/core/polygon_metrics.py)
(placement, engine choice, results), [`core/contour_grid.py`](../src/autoseg_evaluator/core/contour_grid.py)
(the image series as a frame), [`vendor/`](../src/autoseg_evaluator/vendor/)
(the supplied engines, byte-identical). Design and every decision:
[`V3_POLYGON_METRICS_SPEC.md`](V3_POLYGON_METRICS_SPEC.md).

Measured directly on the line segments the RTSTRUCT stores — nothing
rasterised, nothing sampled onto a grid, no vertex moved — to the definitions of
Boukerroui, Vasquez Osorio, Brunenberg & Gooding (2023), Supplement A. Both
structures are placed in one orthonormal millimetre frame built from the ground
truth's image series, their loops are read by
[the shared reading](#reading-the-contours-both-streams), and each contour is
assigned to the slice plane it lies on.

| Column | Definition |
|---|---|
| `poly_apl_mm` / `poly_apl_reverse_mm` | Length of one contour lying further than τ from the other (mm). Forward: ground truth not covered by the test — boundary to draw. Reverse: test not covered by the ground truth — boundary to remove |
| `poly_napl` / `poly_napl_reverse` | The same as a fraction of that contour's total length, 0–1 |
| `poly_hd100_mm` | Largest distance from either contour to the other on the same plane, found continuously along the segments |
| `poly_hd95_mm` | 95th percentile of those distances, weighted by arc length |
| `poly_mean_distance_mm` | Arc-length-weighted mean distance |
| `poly_median_distance_mm` | Arc-length-weighted median distance |
| `poly_planes_joint` / `_gt_only` / `_test_only` | How many planes both contours reach, and how many only one does (diagnostics, not metrics) |

**Planes.** Distance metrics use only planes where **both** structures have a
contour; a plane reached by only one is excluded and counted in the plane
columns rather than hidden. APL counts a ground-truth plane the test never
reached in full. Because the shared-plane rule already excludes test contours
outside the ground truth's range, the truncation option (a 3D-stream control)
does not affect the 2D columns.

**Directions.** Each direction is computed separately; HD100, HD95 and the
median take the **larger**, the mean takes the **equal average**. APL and NAPL
are reported in both directions. All are zero for identical contours, and in
the Report tab lower is better.

**Tolerance.** APL / NAPL take their own τ (default 3 mm), set in the 2D group
on the Compute tab and decorated into the headers.

**Engines.** `fast` — the compiled continuous-envelope engine, the default,
roughly 60–150× quicker. `reference` — pure Python, the audit trail and the
fallback wherever no compiled library is packaged. `AUTOSEG_POLYGON_ENGINE`
forces one. The engine and its settings are recorded with every result in the
audit record.

**Undefined is reported, never substituted.** A comparison the metrics cannot
describe leaves its cells empty and says why in the **2D status** column: a
consensus ground truth (born as a mask, no contours), no shared plane, a contour
off its slice plane or outside the image. A median or 95% value the contours do
not determine — the distance distribution has a gap at exactly that share of
the length — blanks only its own cell and the status gives the range; it is
judged on the reported (larger-direction) value (spec D9). A structure set
whose references name nothing in the loaded data is read from its coordinates
(spec D8).

**Validation.** [`POLYGON_VALIDATION_REPORT.md`](POLYGON_VALIDATION_REPORT.md):
the supplier's 150 published pairs and 44 stress cases, then the same pairs
through this application's adapter and from the DICOM files through its own
grid and reading, all within the suppliers' thresholds (largest disagreement
~5e-10 mm).

**Mask APL, removed in v3.** v1 and v2 reported `apl_mean` / `apl_total`, a
port of PlatiPy's per-slice dilation on the rasterised masks (Vaassen 2020).
It was removed on the principle that edge metrics belong to the contour stream
(spec D3), so **v1 APL values are historical, not reproducible by v3**.
Commit `7b6cec1` is the last that computes it.

---

## Dose-volume histogram (DVH)

**File:** [`src/autoseg_evaluator/core/dvh.py`](../src/autoseg_evaluator/core/dvh.py).
**Validated in** [`DVH_METHOD_VALIDATION.md`](DVH_METHOD_VALIDATION.md)
(`scripts/validate_dvh_methods.py`).

Since v3.0.0 the dose is integrated over the contours themselves; until then
it came from dicompyler-core (see *Why it changed* below).

**Method** (`structure_dvh`):

- The structure's loops are read by `core.masks.read_structure`, the reading
  both geometric streams use, into one region per CT slice. Only the CT's
  geometry is read, so a mask made on the CT serves as the reference after the
  CT volume itself has been released.
- Each region stands for a slab one slice thick. The slab is divided into
  sub-cells aligned to the CT voxels, at the finest of 0.25, 0.5 and 1 mm
  (`SPACINGS_MM`) that keeps the structure within ten million samples
  (`MAX_SAMPLES`), rounded to an odd number per voxel edge so one sits on the
  voxel centre. Small organs get 0.25 mm; only large targets get coarser. A
  structure past the cap even at 1 mm (a body contour: on a 1.37 mm CT, "1 mm"
  is still 27 samples a voxel) is sampled once per voxel (`VOXEL_CENTRES`).
- Each sub-cell is weighted by the exact area of the region inside it and
  sampled at that area's centroid. Both come from signed-area accumulation
  along each row (`polygon_cells`), with no clipping, so the extra work grows
  with the outline and the dose look-ups dominate the cost: about 0.4 s for a
  33 cc sphere, 0.8 s at 268 cc and 1.2 s for a 6,220 cc cylinder,
  single-threaded (the report's large-structure table).
- The dose (`DoseGrid`: any patient orientation; Gy, or cGy converted) is
  interpolated trilinearly, and the samples accumulate a slice at a time into
  a histogram of 1 mGy bins (`DoseHistogram`).
- Dmin, Dmean and Dmax are exact over the samples; D{X}% and D{X}cc are the
  lowest dose the hottest X receives, read to the bin's centre; V{X}Gy is the
  volume receiving at least X Gy.

**Consensus structures** (`mask_dvh`): a STAPLE consensus, or a Tab 2
consensus used as ground truth, has no contours. Its voxels are sub-sampled by
the same spacing rule and read the same way, so a comparison against a
consensus uses one DVH method on both sides.

**Dose grid coverage:** the statistics describe the part of a structure
inside the dose grid. A part outside it has no calculated dose, so it is left
out rather than given one, and every dose row carries `dose_coverage_pct`
(*Dose grid coverage (%)*), the share of the structure's volume the statistics
describe: 100 when the grid covers all of it. On the tender H&N cohort, 89 of
993 structures were partly outside (0.3–1.5 %): spinal cords, oesophagi, lungs
and bodies running below the dose grid. It is a diagnostic, so the Report tab
leaves it out. A structure wholly outside the grid has no DVH, and says so.

**Reported, not hidden:** a D{X}cc larger than the covered volume is left
empty, with the reason in the `dvh_status` column (*Dose status*). A failure
goes in the row's error as `DVH: …` and leaves the geometric columns standing.
The audit sidecar records each DVH's source, sub-sample spacing, sample count
and the volume inside and outside the grid.

**Cranio-caudal truncation:** when the drawer's *Truncate* option is active,
only the slices whose centre lies within the GT's extent
(`core.masks.gt_z_extent_mm`, ±½ slice) are integrated: the same slices the
truncated test mask keeps. Applied to test rows and per-rater STAPLE rows;
never to the GT, which defines the extent.

**GT-vs-dose row:** when DVH is enabled on the Compute tab, the worker emits one
extra row per (patient × organ) with the GT contour's own dose
statistics, `comparison_mode = "gt_dose"`, geometric columns empty,
DVH columns populated. Lets the user compare manual-GT dose against
each AI vendor's dose side-by-side.

**Δ-vs-GT columns (v2.4.1):** each test row also carries a `{metric}_diff`
(test − GT) for every DVH metric — e.g. `d2cc_gy_diff` — so the table
reports both the absolute value and the deviation from the reference.
The Δ columns cluster after the absolute DVH columns.

**Why it changed (roadmap #7):** scored against the analytic datasets of
Nelms et al. 2015, v2's dicompyler-core path had 140 of 195 dose-volume
parameters more than 3 % off on their Test 2, against 10 for this method and
18 for PlanIQ in the paper. It had three defects:

- its D{X} lookup returns 0 Gy once the coldest 1 cGy bin holds more than
  2 × (100 − X) % of the volume, which put D99 at 0 Gy in 49 of the 100 Nelms
  cases;
- it samples the dose only at dose-grid points in each contour plane;
- its in-plane supersampling, v2's retry for the smallest structures,
  misplaces the dose by half a dose pixel on average.

A mask-based DVH was rejected in v2.4.2 because it differed from dicompyler
by up to ~50 % on V{X}Gy. That was measured against dicompyler, not against
truth.

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

2. **Synthetic GT (Tab 2 → Tab 3 → Compute)** — Tab 2 builds a STAPLE
   consensus from 2+ manual observers, registers it as a synthetic
   RTSS, and Tab 3 designates it as the GT. The worker rasterises
   constituents on-the-fly and runs STAPLE at compute time using
   the Compute tab's STAPLE settings. The drawer's `vs STAPLE` auto-disables
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
   consensusGenerated signal → MainWindow hands the library to Match Contours + Compute
       │
       ▼
   Tab 3: synthetic RTSS appears in Loaded Contours tree with source
   label "STAPLE Consensus" and bold styling.
       │
       ▼
   Compute: worker detects synthetic GT in _get_mask via _find_rtstruct_entry,
   calls _synthesise_consensus_mask which rasterises every constituent
   and runs compute_staple. Cached under the synthetic key for the rest
   of the run.
```

### Inter-observer variability dialog

**Class:** `_InterManualMetricsDialog` in `build_consensus.py`

Pre-flight settings dialog (`_InterObserverSettingsDialog`) lets the
user pick which 3D mask metrics to compute + override the Surface Dice
tolerance. Progress dialog ticks per `(group × organ)` with full
cancel responsiveness (cancel-check threaded into both the per-organ
loop AND the per-rater-pair inner loop). Results table:

- Columns: `Patient`, `Source label`, `Organ`, `Rater A`, `Rater B`,
  then metric columns with the τ value baked into the Surface Dice
  header.
- Sortable QTableWidget; Ctrl+A → Ctrl+C copies all rows + headers as
  TSV; **Export CSV…** writes a CSV file.
- Per-organ mask eviction inside `_compute_inter_manual_rows` caps peak
  RAM at `~raters × 1` mask instead of `raters × organs`.

### Conceptual difference from Tab 3's per-drawer "vs STAPLE"

| | Build Consensus GT (Tab 2) | Tab 3 "vs STAPLE" |
|---|---|---|
| **Use case** | Multi-manual studies — combine N manuals into the single GT used for AI comparison | All-as-raters — every contour in a drawer (GT + tests) is one rater |
| **STAPLE input** | Only the user-selected manual RTSSes | GT + every test contour |
| **STAPLE output use** | Synthetic GT visible in Tab 3, designated as `Manual` reference | Per-rater sens/spec + consensus summary rows in Results (Tab 6) |
| **Compute timing** | At the Compute tab's run (on-the-fly) | At the Compute tab's run (also on-the-fly) |
| **Allowed together?** | No — if the GT is a synthetic STAPLE consensus, Tab 3's `vs STAPLE` is auto-disabled |

---

## Organ grouping and the Report tab

**Grouping:** [`core/organ_groups.py`](../src/autoseg_evaluator/core/organ_groups.py),
[`data/organ_index.py`](../src/autoseg_evaluator/data/organ_index.py),
[`ui/dialogs/organ_labels.py`](../src/autoseg_evaluator/ui/dialogs/organ_labels.py).
The many spellings of one organ are collapsed so statistics can pool them,
without ever pooling two organs. Names are grouped on a structured key —
`(base, laterality, qualifier)` — and **only `base` is ever fuzzy-matched**:
on this project's own matcher left/right pairs of one organ score 0.82–0.93
while different organs score 0.38–0.45, so no threshold separates them and
laterality has to be extracted. Assignment runs in tiers — manual >
dictionary > stripped > fuzzy > unassigned — and a fuzzy result is a
proposal, never applied unconfirmed. Curation happens in the Matching tab
(**Label Organs…**); labels never merge drawers, and persist on explicit
session save (`organ_assignments`, session v6).

**Report:** [`ui/tabs/report.py`](../src/autoseg_evaluator/ui/tabs/report.py),
[`data/report.py`](../src/autoseg_evaluator/data/report.py),
[`core/statistics.py`](../src/autoseg_evaluator/core/statistics.py),
[`ui/widgets/stat_plots.py`](../src/autoseg_evaluator/ui/widgets/stat_plots.py).
A case is a (patient, planning CT) pair, so two courses are never collapsed
into one; a patient with more than one case is left out of the paired
comparison, and the tab says so, rather than having one course picked for it.
For each organ and metric the tab compares two sources on their paired cases: Wilcoxon signed-rank with Pratt's zero handling and an exact
conditional p-value, a Hodges–Lehmann estimate with the confidence set
obtained by inverting the same test, rank-biserial correlation, and an exact
sign test alongside. **Each organ is its own question and its p-value is
reported unadjusted.** Metric directions (lower or higher is better) and axis
bounds come from [`core/readable.py`](../src/autoseg_evaluator/core/readable.py)
and `data/report.py`, and the 2D plane counts are left out as diagnostics.
The acquisition summary reads only an allowlist of non-identifying tags
([`core/acquisition.py`](../src/autoseg_evaluator/core/acquisition.py)); the
PDF export writes the page as a clinical report.

Every statistical decision, with worked examples regenerated from the shipped
code by `scripts/make_register_tables.py`, is in
[`V3_REPORT_STATISTICS_REGISTER.md`](V3_REPORT_STATISTICS_REGISTER.md); the tab's
design is in [`V3_REPORT_TAB_SPEC.md`](V3_REPORT_TAB_SPEC.md).

---

## Session save / load

**File:** [`src/autoseg_evaluator/data/session.py`](../src/autoseg_evaluator/data/session.py)

**Schema version: 7.** Past versions still load (missing fields default
to empty); future versions are refused with a clear error. **v4** adds the
`qualitative` block (graders, their fixed per-grader configs, and each
grader's order / scores / cursor) so an in-progress qualitative run resumes
and its scores re-populate the Results tab. **v5** adds `link_overrides` —
the answers given in Review Data Links, keyed by
`linkage.override_key(patient_id, rtstruct_sop_uid, kind)` — so a cohort whose
links had to be settled by hand does not have to be settled again on reload.
They are applied by Tab 1 as soon as the rescan completes, and an override
naming data that is no longer present is ignored rather than fatal. **v6** adds
`organ_assignments`, mapping a raw ROI name to the organ it was grouped under,
so a cohort whose names had to be sorted out by hand is not sorted out again.
**v7** adds `results`, the computed results table, so qualitative scoring can
continue over several sessions without computing the metrics again; each row
keeps its `computed_at`, and each Likert score in `qualitative` its
`scored_at`. The consensus entries also record the planning series they were
built on (`series_uids`).

Opening a session, or loading a different folder, replaces the work in progress
— results, scores, organ labels, the session path — after the user confirms,
with the option to save first. A rescan of the same folder keeps everything.

Metric selections and tolerances are application settings (`settings.json`),
not session data. Settings for removed features — mask APL's `apl_mean`,
`apl_total` and `apl_tolerance_mm` — are dropped on load.

**Top-level JSON shape:**

```json
{
  "schema_version": 7,
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
  ],
  "link_overrides": {
    "PATIENT_ID|<rtstruct SOP UID>|series": "<chosen SeriesInstanceUID>",
    "PATIENT_ID|<rtstruct SOP UID>|dose": "<chosen dose SOPInstanceUID>"
  },
  "organ_assignments": {"Parotid Lt": "Parotid_L"},
  "qualitative": {
    "started": true, "active_rater": "Alice",
    "raters": ["Alice", "Bob"],
    "rater_config": {"Alice": {"mode": "blinded", "include_gt": false,
                               "randomize": true, "seed": 12345}},
    "per_rater": {"Alice": {"order": ["HN1|Parotid_L|<sop>|12"],
                            "scores": {"HN1|Parotid_L|<sop>|12": 4},
                            "index": 1}}
  }
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

### Per-patient cache eviction (metrics worker)

**File:** [`src/autoseg_evaluator/workers/metrics_worker.py`](../src/autoseg_evaluator/workers/metrics_worker.py)

Group iteration is sorted **patient-major** in `_enumerate_groups`. When
the loop crosses a patient boundary, `_evict_patient_caches` pops the
CT, dose dataset, all masks, and all RTSTRUCT pydicom datasets for the
previous patient, and the 2D stream's grids and prepared structures with
them. Peak RAM is bounded by a single patient's data instead of the entire
cohort's. `gc.collect()` is called to reclaim SimpleITK's C++-backed memory.

### Reading and filling contours once

Each structure is read and filled once per run however many sources it is
compared against; the 2D stream likewise prepares each structure once. The
half-open scanline fill replaced scikit-image's per-voxel point-in-polygon
test, taking the tender H&N cohort's fills from 815 s to 219 s (3.7×),
reading step included.

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

**Test runner:** pytest, over 1,100 tests, about 80 s wall clock.

**Coverage highlights:**

| File | Tests | What it pins |
|---|---|---|
| `test_surface_distance.py` | ~30 | Bit-for-bit parity with `google-deepmind/surface-distance` on synthetic + SAMPLE-DATA fixtures |
| `test_metrics.py` | ~20 | 3D metric aggregator; Surface Dice keyed by its tolerance, one value per tolerance; a configuration still asking for the removed mask APL gets none |
| `test_audit_fixes.py` | 11 | The external audit's findings on real DICOM: two series in one folder, a second course refused by matching, the worker and the consensus builder, the viewer's dose and consensus ground truth, Undo, the consensus UID |
| `test_main_window_flows.py` | ~16 | One computation per table and its confirmation, a folder change and its confirmation, the results table in the session, STAPLE settings, the session file suffix |
| `test_tolerance_keys.py` | ~16 | Tolerance lists from every settings shape, keys that carry their tolerance, every lookup by metric name seeing through it |
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
| `test_platipy_equivalence.py` | 4 | `legacy` rasteriser equivalence with PlatiPy on a synthetic CT + RTSTRUCT (square, donut with XOR hole, multi-slice) |
| `test_rasteriser_backends.py` | ~17 | `continuous` fill: dcmrtstruct2nii conformance, identity with the previous fill away from edge ties, half-open edge rule, no seam between touching loops, refusals with reasons, and the 3D mask being exactly the fill of the region the 2D stream reads |
| `test_contour_reading.py` | ~25 | Every loop rule on its smallest case (holes, islands, touching, overlap, duplicates, pinches, spikes, figure-of-eight, double winding), and identity with the vendored parser on everything it accepts |
| `test_polygon_metrics.py` / `test_metrics_worker_polygon.py` | ~45 | 2D adapter: engine choice, placement, dangling references, undetermined quantiles per metric, availability, caching, audit notes |
| `tests/vendor/` | ~105 | The suppliers' own acceptance tests, and every vendored file against its SHA-256 manifest |
| `test_report_model.py` / `test_report_tab.py` / `test_statistics.py` | ~255 | Pairing, coverage, metric directions and diagnostics, the statistics against worked examples, figures and PDF export |
| `test_metrics_equivalence.py` | 6 | Bit-for-bit equivalence of Dice / HD100 / HD95 / Surface Dice @ 3 mm / mean surface distance against ``google-deepmind/surface-distance`` |
| `test_settings.py` | 3 | Settings for removed features dropped on load and on the next save |
| `test_staple_equivalence.py` | 3 | Bit-for-bit equivalence of AutoSeg's STAPLE wrapper (per-rater sensitivity/specificity + binary consensus) against a direct ``SimpleITK.STAPLEImageFilter`` invocation, on a synthetic 3-rater fixture |
| `test_dvh.py` | ~34 | The DVH against answers known exactly: mean dose at the centroid in any linear dose, D{X} through-plane and in-plane, holes, a single plane; accumulated coverage against clipping; the spacing rule; dose grids in every orientation and GridFrameOffsetVector convention; units, the dose grid's edge, truncation, D{X}cc beyond the structure; mask against contours; the worker's dose columns, status and audit |
| `test_version.py` | 3 | `__version__` resolution + portable-bundle `_version.py` fallback |

**Empirical clinical validation:** every numerical engine was cross-checked
against its upstream reference on the SAMPLE DATA HN1 cohort, each producing
a PHI-safe markdown report under `docs/` and reproducible via a script under
`scripts/`:

| Engine | Reference | Result | Report |
|---|---|---|---|
| `legacy` mask rasterisation | PlatiPy 0.7.2 | 110/110 ROIs voxel-exact | `VALIDATION_REPORT.md` |
| 5 surface-distance metrics | `surface-distance` | 45/45 comparisons Δ = 0 | `VALIDATION_REPORT.md` (its 63 also covered the since-removed mask APL) |
| 2D contour metrics | the suppliers' published values | 150/150 pairs through the adapter and from DICOM; largest error ~5e-10 mm; 44 stress cases | `POLYGON_VALIDATION_REPORT.md` |
| Shared contour reading | vendored parser (2D), previous fill (3D) | 5,143/5,143 regions identical; all 22,123 changed voxels edge ties | `scripts/validate_contour_reading.py`, run on the tender cohort (V3_POLYGON_METRICS_SPEC.md, D10) |
| STAPLE consensus | `SimpleITK.STAPLEImageFilter` | 55/55 consensus runs bit-exact (sens/spec + voxels) | `STAPLE_VALIDATION_REPORT.md` |
| DVH | Nelms et al. 2015 analytic datasets; analytic disc phantoms | Test 1: 0/260 parameters > 3 % (PlanIQ 5); Test 2: 10/195 (PlanIQ 18); discs: worst 0.11 Gy at 1 Gy/mm | `DVH_METHOD_VALIDATION.md` (v2's dicompyler equivalence: `DVH_VALIDATION_REPORT.md`, historical) |

The STAPLE reference library (`SimpleITK`) is a core dependency, so its
equivalence test runs in CI with no extra install, and the DVH tests need no
reference library at all, because they test against answers known exactly; `surface-distance` and `platipy` are installed explicitly in the CI
workflow for the mask/metric equivalence tests. The 2D engines' own acceptance
scripts run in CI too, on a compiled library built on the runner for Linux.

**Bit-for-bit PlatiPy parity for mask rasterisation** (v2.3.1):
[`test_platipy_equivalence.py`](../tests/test_platipy_equivalence.py)
generates a tiny synthetic CT + RTSTRUCT and asserts AutoSeg's `legacy`
rasteriser produces a numpy-equal output to PlatiPy's
`transform_point_set_from_dicom_struct` across three ROI shapes:
single-slice square, donut (XOR-produced hole), and multi-slice
square. CI installs `platipy>=0.7` explicitly so this test runs on
every push. The test was also verified offline against the full HN1
sample cohort: **110/110 ROIs voxel-exact across two RTSSes**,
including donut-shaped Spinal_Canal, 4.9M-voxel BODY, and structures
down to 88 voxels (Lens_L).

**Bit-for-bit metric parity across the surface-distance metrics** (v2.3.2):
[`test_metrics_equivalence.py`](../tests/test_metrics_equivalence.py)
extends the regression net to the metric implementations themselves.
On a synthetic GT/Test pair with non-trivial overlap (offset 12-mm
half-width squares spanning four CT slices each), AutoSeg's output
is asserted equal to:

* `surface_distance.compute_dice_coefficient` — volumetric Dice,
* `surface_distance.compute_robust_hausdorff` at 100% and 95% — HD100 / HD95,
* `surface_distance.compute_surface_dice_at_tolerance` at 3 mm — Surface Dice,
* `surface_distance.compute_average_surface_distance` — mean surface distance.

The test was also verified offline on the HN1 cohort across nine
shared organ pairs (brainstem, spinal cord, mandible, larynx, oral
cavity, bilateral parotids, bilateral cochleae): **every metric–ROI
comparison produced zero absolute difference** (max |Δ| = 0.000e+00).
At v2.3.2 that was 63/63, including mask APL against PlatiPy; mask APL was
removed in v3 and its parity checks with it. CI installs `surface-distance`
so the regression test runs on every push.

**Reviewer-ready validation report** ([`docs/VALIDATION_REPORT.md`](VALIDATION_REPORT.md)):
generated by [`scripts/validate_against_upstream.py`](../scripts/validate_against_upstream.py),
it contains the full per-ROI per-metric breakdown on the HN1 cohort
(110 ROIs of mask comparison, 9 ROIs × 7 metrics at v2.3.2) plus the software
environment versions. The script now compares the five surface-distance
metrics and pins its PlatiPy mask check to the `legacy` rasteriser, the only
backend meant to match PlatiPy; regenerating the report needs the HN1 data. PHI-safe by construction: no SOPInstanceUIDs,
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
- 3D mask metrics are quantised to the voxel lattice: anything thinner than
  a voxel — a narrow gap, a sliver, a thin hole — can appear or vanish
  depending on where voxel centres fall. The 2D contour metrics have no such
  limit.
- Closed contours only (`CLOSED_PLANAR`, `INTERPOLATED_PLANAR`,
  `CLOSEDPLANAR_XOR`); `POINT` and `OPEN_*` structures are refused.
- Loops with no single reading — partial overlaps, duplicated loops, an
  outline wound twice round an area — are refused in both streams rather than
  guessed.
- The two streams check placement differently: a contour lying outside the
  image is trimmed by the 3D fill but refuses the 2D metrics, and a contour up
  to half a slice off-plane is filled in 3D but must lie within 0.001 mm of its
  plane for the 2D metrics.
- The 2D compiled engine ships for Windows and is built in CI for Linux;
  macOS runs the slower reference engine.
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
- **v2.5.3** — Transparent-background **application icon** refresh + a GUI
  **screenshot** in the README.
- **v2.6.0** — **Qualitative (Likert) assessment — new Tab 4** (Compute → 5,
  Results → 6). Per-grader config (blinded/transparent, include-GT, randomize),
  a `QGraphicsView` multiplanar viewer (axial/coronal/sagittal, zoom, W/L,
  opacity, thickness), swipe navigation, tab-lock, multiple graders, and
  session save/resume (**schema v4**). Scores overlay onto the contour's
  Results row as `likert_<grader>` + `Qualitative` / `Blinded` columns. Also a
  **dose-overlay colour wash** in the Tab-3 slice viewer (`core/dose.py`).

### v3.0.0 (in development)
- **Two metric streams** — 2D contour metrics (APL, NAPL, 2D Hausdorff 100 /
  95 %, mean and median contour distance) beside the 3D mask metrics; **mask
  APL removed** (v1 APL values become historical). Metric definitions dialog,
  optional audit record.
- **One shared contour reading** for both streams, and a half-open 3D fill
  (moves one vendor's masks on the tender cohort; see
  [Mask creation](#mask-creation-rtstruct--binary)).
- **Sub-voxel `continuous` rasteriser as the default**, `legacy` opt-in.
- **Data linking by explicit reference**, no RTPLAN; ambiguity settled in Tab 1.
- **Canonical organ grouping**, **Report tab** (Tab 7) with per-organ paired
  statistics, figures and PDF export.
- The Match Contours visualiser uses the Qualitative tab's multiplanar viewer.
- **External audit fixes (September 2026):** one computation per results table,
  several tolerances in one run with the tolerance in each column's key, the
  results table saved with the session (schema 7), *Computed at* and *Scored at*
  columns, one planning image per comparison from matching to pairing, CT read
  by series, the viewer showing the computed dose, and Likert scores that
  survive restores. See the CHANGELOG.

See [`V3_RELEASE_STATUS.md`](V3_RELEASE_STATUS.md) for what remains before
release.

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
| Added Path Length (concept; v1–v2 mask version) | Vaassen et al., *Phys Med* 2020 (PlatiPy implementation) |
| 2D contour metrics (APL, NAPL, HD, mean / median distance) | Boukerroui, Vasquez Osorio, Brunenberg & Gooding, *Phys Imaging Radiat Oncol* 26 (2023) 100436, Supplement A |
| Wilcoxon signed-rank, Hodges–Lehmann, rank-biserial | see [`V3_REPORT_STATISTICS_REGISTER.md`](V3_REPORT_STATISTICS_REGISTER.md) |
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
- **NAPL** — Normalised Added Path Length (APL as a fraction of contour length)
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

*Document version 7 — updated 2026-09-24 for v3.0.0 (in development): two
metric streams with mask APL removed, the shared contour reading and half-open
fill, data linking, organ grouping and the Report tab, and the shared viewer in
Match Contours.

Version 6 — updated 2026-06-18 against v2.6.0.
Tracks: portable bundle + CI/release pipeline (v2.1), `D at volume (cc)`
DVH input + window-title fix (v2.2), template source-label match +
expanded Manage Source Labels columns (v2.3), the Tab 2 multi-observer
consensus redesign + STAPLE result-row parity (v2.4.0), STAPLE/DVH
empirical validation + DVH Δ-vs-GT + single-slice DVH + bundle/progress
fixes (v2.4.1), truncation-aware DVH (v2.4.2), application icon + splash +
icon'd launchers (v2.5.0), sub-dose-grid DVH recovery (v2.5.1), removal
of the inert STAPLE `target_fg_ratio_min` parameter (v2.5.2), icon refresh
+ README screenshot (v2.5.3), and the qualitative (Likert) assessment tab +
dose overlay (v2.6.0).*
