# Changelog

All notable changes to AutoSeg Evaluator are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — Linux: the compiled 2D engine and a portable download, October 2026
- **The 2D contour metrics' compiled engine ships for Linux x86-64.** It was
  built once by a new workflow (`.github/workflows/linux-library.yml`) with
  the supplier's build script, for glibc 2.28, and validated with the
  supplier's acceptance suites and our own call path on glibc 2.28, Ubuntu
  22.04 and Ubuntu 24.04 — the same result as the Windows library on all
  three (largest distance error 4.86e-10 mm). It is committed and pinned by
  hash like the Windows one; the record is in
  `third_party/native_contour_metrics/builds/linux-x86_64/`. A Linux install
  no longer needs a compiler to get the fast engine, and CI validates the
  committed library on every run instead of building its own.
- **A portable Linux bundle on the Releases page**,
  `AutoSegEvaluator-v…-linux-x86_64.tar.gz`, beside the Windows one: a
  self-contained CPython 3.11 with every dependency as plain files, a
  `run-autoseg-evaluator.sh` launcher and an `add-to-applications-menu.sh`
  helper. Its `README.txt` states the oldest glibc it needs, read from the
  wheels it was built with.
- **Both bundles are started before they are released.**
  `scripts/smoke_test_bundle.py` runs each with its own interpreter: every
  module imports, the main window opens off-screen, and the 2D metrics run on
  the compiled engine with the committed library.

### Fixed — the portable bundle, October 2026
- **PySide6 is held below 6.12.** PySide6 6.12.0 (released 2026-10-08)
  releases Python's `None` more often than it takes it. On Python 3.11 and
  older, where `None` is reference-counted, that eventually aborts the process
  ("Fatal Python error: none_dealloc"); CI's test runs crashed at exit from
  the day it was released. Both bundles ship Python 3.11, so a long session
  could have crashed the same way.
- **shapely is installed.** The bundles install from `requirements.txt`,
  which lacked it, so a v3 bundle would have failed on the first contour read.
  A test now keeps `requirements.txt` and `pyproject.toml` in step.

### Fixed — TG-263 laterality errors in the synonym dictionary, October 2026
- **The TG-263 worksheet's own left/right errors are overridden.** The
  2017-08-15 worksheet describes 13 structures as the opposite side to their
  names — the femoral necks, common iliac and internal jugular veins, condyloid
  processes, and the right axillary central nodes, level II nodes, frontal lobe,
  sternocleidomastoid and retrostyloid space. `scripts/build_synonyms.py` now
  corrects those descriptions before generating variants, and reports any
  other row that contradicts its name. In the dictionary, 24 spellings of the
  femoral necks and common iliac veins that sat under the opposite side are now
  under their own (the matcher had been re-filing them on load), and 14
  sternocleidomastoid and retrostyloid spellings, previously dropped for both
  sides, are restored. The corrections are listed in the dictionary under
  `_tg263_corrections`.
- **Elbow spellings resolve to one structure.** TG-263 lists both `Elbow_L` and
  `Joint_Elbow_L` (and `Bowel` and `Spc_Bowel`). Dropping the prefix from
  `Joint_Elbow_L` gave it `Elbow_L`'s spellings too, so "Elbow_L" resolved to
  Elbow_L but "Elbow Left" to Joint_Elbow_L. A shortened name that is a TG-263
  name in its own right is now left to that name: 63 duplicate listings go, no
  spelling stops being recognised, and ten elbow spellings now resolve to
  Elbow_L or Elbow_R. Every variant in the dictionary now resolves to the
  name it is listed under.
- **"Small" and "Large" no longer stand for small and large bowel.** The
  generator treated `Bowel_` as a category prefix and stripped it, leaving bare
  adjectives as synonyms. "Small Bowel", "Large Bowel" and the rest still
  resolve.
- **Other shortened names that name nothing on their own are gone.** Dropping
  the category prefix also gave single letters ("R" for LN_R, "C", "S" and
  "T" for the vertebral regions), bare Roman numerals ("VII" for CN_VII, which
  also reads as neck level VII) and qualifying words ("Base", "All", "Oral" for
  the tongue; "Common", "Anal", "Pelvic", "Surface", "TM"), each with its
  left/right spellings. The full names still resolve, and so do vertebra codes
  such as "C1" and "T12".

### Added — Contour Discontinuity, October 2026
- **Contour discontinuity**, a check recorded on every run (no checkbox: it is
  not a metric), adds a yes/no column, *Contour Discontinuity*: yes when the test
  contour skips a slice, a CT slice between its first and last contoured slices
  with no contour on it. Read from the contours as stored rather than from the
  mask, so a contour too small to fill a voxel still counts as present; the
  whole contour, never the truncated one. Separate pieces on one slice are not a
  discontinuity. The ground truth is not checked. The audit sidecar lists the
  skipped slices' positions.
- **The coverage table counts discontinuous contours**: per organ and source,
  *k of n*, on the Report tab and in the PDF.
- Design and decisions: `docs/V3_SKIPPED_SLICE_SPEC.md`.

### Added — overlap with the PTV, October 2026
- **PTV overlap**, recorded on every run for every comparison with a ground
  truth (no checkbox: it is not a metric), adds three columns:
  the ground truth's volume inside the PTV, the test contour's, and the
  difference, test − GT, in cc. Any overlap counts; 0 means none. The PTV is
  every structure whose type (RTROIInterpretedType) is PTV in the ground truth's
  own structure set, combined into one region; names are not read. The test
  contour is the truncated one when the drawer truncates. The columns are blank
  for a consensus ground truth and for a structure set with no structure typed
  PTV; the audit sidecar records why, and which PTVs were combined.
- **Test-only PTV overlap**, a fourth column: True when the test contour
  overlaps the PTV and the ground truth's does not, the case the Report tab
  counts. Blank wherever the volumes are.
- **The coverage table counts test-only overlaps**: per organ and source, how
  many test contours overlap the PTV where the ground truth's does not, as
  *k of n*, on the Report tab and in the PDF. The test overlap and the
  difference can also be selected as metrics in the Report tab.
- Design and decisions: `docs/V3_PTV_OVERLAP_SPEC.md`.

### Changed — the Compute tab opens with nothing selected, October 2026
- **No metric is selected, and no value filled in, when the tab opens.** The
  3D, 2D and dose checkboxes start unticked and the tolerance, D-at-volume and
  V-at-dose fields start empty, at every launch: the selection is no longer
  carried over from the last run, and the `compute_geometric`,
  `compute_polygon` and `dvh` sections are dropped from `settings.json` when
  it is next read. Compute All asks for at least one metric, and for the
  Surface Dice or APL tolerance when that metric is ticked, rather than taking
  3 mm for an empty field.

### Changed — the auto-match template, October 2026
- **The GT RTSS is identified by its source label only.** The template no
  longer offers a filename criterion; one saved in an older template is
  ignored, and dropped from `settings.json` when it is next read. The source
  label is the name the cohort tree and every drawer show, so it is the one a
  match can be checked against. The dialog's explanation is shortened to one
  sentence.

### Changed — Report tab tables and figures, October 2026
- **Tables are only as wide as their columns.** Every column used to be
  stretched to share the window's width equally, which spread a three-column
  table across the screen. Each column now takes the width of its widest
  entry, and the table keeps to the left of its section; in a window narrower
  than a table, that table scrolls sideways within itself.
- **The exported PDF centres its tables** on three quarters of the page
  width, with each table's section label and note on the same measure. Full
  width spread a few short columns apart; sized to their contents, the tables
  sat at the left edge with most of the page white.
- **The paired comparison says which test it ran.** A note above the table,
  on the tab and in the PDF: an exact two-sided Wilcoxon signed-rank test per
  row, the Hodges–Lehmann difference with its 95% confidence interval, an
  exact sign test alongside, and p-values not adjusted for multiplicity. The
  methods paragraph at the foot still gives it in full.
- **A wide figure scrolls inside its own section.** The distributions widen
  with the organ count, and the figure's width used to become the tab's: every
  table and caption stretched to match, and reading a row meant scrolling the
  whole tab sideways. Each figure now has its own horizontal scroll bar, and the
  tab keeps the window's width.
- **Every axis with a unit shows it.** The DVH difference columns
  (`d2cc_gy_diff` and the like) had no unit and read "D2cc gy diff"; they now
  read "D2cc difference from ground truth (Gy)". The paired figure's median
  labels carry the unit, and the viewer's dose scale reads "0 Gy" at its foot.
  Dimensionless measures (Dice, NAPL, ratios) stay without one.

### Changed — the exported PDF, October 2026
- **Type prints at its stated size.** The report was laid out for the screen
  and printed at the PDF's 150 dpi, so every size printed at 96/150 of itself:
  tables came out at about 4.8 pt and the prose at about 5 pt. It is now laid out
  on the PDF writer, and set at 9 pt for tables and 10–10.5 pt for prose. The
  report runs to more pages as a result.
- **Page numbers and a running header.** Every page has a footer with the
  version and *Page n of N*; every page after the first is headed with the
  metric and the comparison, and the date it was produced. Margins are set
  rather than left at the writer's 3.5 mm.
- **Nothing is split across a page break.** An organ's rows stay on one page,
  so no row starts a page without its organ; a table running onto the next
  page is split there into a table of its own, with its header row, instead of
  carrying its column rules to the foot of the page; a section heading stays
  with the rows it introduces; and a figure's heading stays above its figure,
  where *Paired differences* and *Forest plot* had been printed below the
  figures before them.
- **Figures are drawn at the size they print at.** The forest takes the page's
  full width, with its rows closed up to fit the page, where it had filled half
  the width at a reduced size. The distributions stand their organ labels
  upright on paper, where twenty angled labels ran into each other. The forest
  and paired figures' titles no longer overlap their subtitles.
- **The coverage table says which organs were truncated**: *yes*, *no*, or
  *partly* where drawers pooled under one organ were set differently. On the
  Report tab as well.
- **Notes are written for paper.** "Hover those rows", "the paired view below"
  and pointers to other tabs are reworded or left out, and the patients excluded
  for having two treatment courses are counted rather than named: the Export
  button already promised that no patient identifiers are written, and this
  note listed up to four.
- Data tables take more than the central three quarters of the page only where
  their columns need it to stay on one line.
- The methods paragraph no longer says "Every comparison is reported rather
  than a selected subset, which is what makes that defensible", on the tab or
  in the PDF.
- **A cohort with one test source says why it has no paired comparison.** The
  ground truth is never a comparator, so one source has nothing to pair with.
  The tab said so only by an empty table and a paired figure claiming no
  patient had both sources; it now says it in the section, and both figures
  give the reason. The PDF prints the reason under the section heading and
  leaves out the empty table and the two comparison figures. Choosing the
  reference as its own challenger is explained the same way.

### Fixed — exported report, October 2026
- **Two bold cells one above the other printed with no rule between them.**
  Bold was written as a style on the cell, and in Qt's HTML a style on a cell
  replaces the stylesheet's border; it is now written as bold text inside the
  cell.

### Changed — validation reports, October 2026
- **The validation reports are rewritten as Supplementary 1–5** of the paper,
  in `docs/validation/`: each a short report (aim, method, results, findings)
  for readers who do not know the code, with a full-results file holding every
  table behind it. Both are generated by the scripts, which keep what they
  compute in `.validation_cache/` (not committed) so a report can be rewritten
  without computing again (`--render-only`; `scripts/supplementary_1_dvh.py`).
  Supplementary 1 joins the two DVH reports; Supplementary 2 adds a clinical
  comparison of the v1–v2 and v3 rasterisers; Supplementary 5 measures what
  the earlier 100-iteration STAPLE cap changed. The four reports of earlier
  versions moved to `docs/archive/`.

### Fixed — external audit, September 2026
Each has a regression test that reproduces the audit's case
(`tests/test_audit_fixes.py` and beside the module concerned).
- **The report could hang on an empty contour.** An empty mask gives an
  infinite Hausdorff distance, and inverting the signed-rank test over a
  difference of −∞ never terminated; +∞ returned an interval silently. Every
  statistic now uses finite values only, and the report counts an infinite value
  as *metric invalid*, like NaN, so the case still shows in the coverage table.
  The Results table still shows ∞, which is the true value.
- **Two image series in one folder shared one volume.** The link resolver found
  the right series, but the image was then read by folder, and GDCM took
  whichever series it listed first; the cache was keyed by folder too. The CT
  is now read by its SeriesInstanceUID, in the worker, both viewers and the
  consensus tab, and cached per series. A folder holding one series reads
  exactly as before.
- **Matching and consensus could combine different treatment courses.**
  Auto-match searched every structure set of the patient; a second course's
  contour could be matched to the first course's ground truth, unflagged, and
  was then rasterised on the wrong CT. A structure set is now a candidate only
  when it resolves to the ground truth's planning series — not its Frame of
  Reference, which vendors get wrong (Prostate4). Auto-match skips the others
  and says how many, a hand-added one is refused, and the worker turns any that
  remain in an older session into an error row instead of a number. The
  consensus builder keeps only raters on the majority planning image and names
  the rest.
- **Paired tests could pair different courses.** Pairing joined on the patient
  and dropped the treatment context, so one vendor's first course was paired
  with another's second. A pair is now the same patient on the same planning
  image; a patient whose sources were assessed on different ones is withheld and
  named with the others.
- **Restarting a computation could destroy a running thread.** Compute All is
  disabled while a run is active; a thread that has not stopped is kept until
  it finishes rather than deleted; the previous run's signals are disconnected
  so a late row cannot reach the next run; closing the window waits for it.
- **The viewer could show a different dose from the one computed.** It chose
  by its own rule. It now uses the metrics' dose resolver, with the user's link
  answers, and samples the same dose grid the DVH integrates.
- **Likert scores could land on another contour's row.** The score key had no
  structure set, so two files from one source sharing an ROI number collided.
- **A consensus ground truth could not be viewed.** It has no file; the viewer
  and the Qualitative tab now build its mask from its raters as Compute does.
- **STAPLE settings were not saved**; **Undo could not restore a drawer removed
  directly** (and removing a drawer's last patient needed two Undos); **saving a
  session without an extension raised an error**.
- **A test assumed a SciPy fallback.** With exact computation (SciPy ≥ 1.14) its
  data gave both zero conventions the same p; it now uses data that tells them
  apart, computed exactly on both sides.
- **The consensus UID changed between runs** (`hash()` is salted per process);
  it is now a digest.

### Changed — speed, from an external review, September 2026
- **Results and Report no longer rebuild after every row.** Every result row
  and every Likert score rebuilt both tabs in full, seen or not, so the work
  grew with the square of the rows: about half an hour of the window's time
  over a 1,000-row computation, and seconds on each Likert click. A hidden tab
  now catches up when it is shown. A visible Results tab appends the new rows
  about once a second, and rebuilds only when rows it already shows could
  change (a new column, a score, an organ label, a cleared or restored table).
  What the tabs show is unchanged; only when it is drawn. Simulated with 1,000
  rows: under a second of the window's time for the whole run, then 0.9 s to
  open Results and 2.4 s to open Report.
- **STAPLE summaries are read from the cropped images.** The probability map
  was padded to the whole CT and scanned there for the uncertainty summaries,
  as were the rater masks for the disagreement volume. Outside STAPLE's box
  every one of them is zero, so the values are identical; each call no longer
  spends about half a second and 400 MB on it (512 × 512 × 150 CT, five
  raters). The whole-image probability map is still available, built on
  request, and nothing in a computation requests it.
- **Surface distances are sorted with NumPy.** The same order as the DeepMind
  port's tuple sort (distance, then surfel area among ties, which the
  percentile Hausdorff distances depend on), without a Python tuple per surfel;
  and the surface-area table is built once per voxel spacing rather than once
  per pair. About a fifth off each surface-distance calculation.
- **Masks are released after their last use.** Each mask is a full-CT volume
  (37.5 MiB on a 512 × 512 × 150 CT), and every mask of a patient stayed cached
  until the patient was finished: 30 organs against 8 contour sets held about
  9 GB at once. The worker now counts, before the run, how many drawers use
  each mask and drops it after the last, so at most one drawer's masks are held,
  plus any a later drawer shares. A shared mask is still rasterised once, and
  no result changes.
- **The mask metrics work on the pair's shared region.** Each pair copied the
  whole CT four times and every metric scanned it in turn, and volume computed
  both centroids even when centre-of-mass was not ticked. The masks are now read
  in place, cropped once to the smallest box holding both structures, and every
  metric is taken there; the centroid only when asked for. Outside the box both
  masks are empty, so every value is bit-for-bit identical (a centroid adds the
  crop's corner to the integer indices before averaging). On a 512 × 512 × 150
  CT: a parotid-sized pair from 504 to 14 ms with every geometric metric, a
  liver-sized pair from 813 to 347 ms, volume alone from about 350 ms to 5 ms.
- **The audit record comes from the metrics' own pass.** With the audit
  sidecar on, each pair's record computed the surface distances a second time,
  over another copy of the whole CT. It now reads them, and its voxel counts,
  from the metrics' cropped masks and distances; the record is identical. With
  the audit on, a parotid-sized pair from 221 to 13 ms, a liver-sized pair from
  840 to 338 ms.
- **A contour's DVH is integrated once per drawer.** With both the ground-truth
  comparison and the drawer's STAPLE consensus on, every contour's dose
  statistics were integrated twice with identical inputs, once for each row.
  Within a drawer the dose and CT are the ground truth's, so the structure set,
  ROI and truncation extent decide the result; it is now kept for the drawer
  and reused, a failure with its wording. `core/dvh.py` is unchanged.

### Fixed
- **2D metrics were unavailable for structure sets with dangling references.**
  A structure set naming a Frame of Reference it never declares, or image slices
  absent from the loaded CT, was refused outright; on one cohort that voided
  every 2D metric in the run. References that name nothing are now set aside,
  and contours are placed from their coordinates, still within 0.001 mm of a
  slice plane. References that contradict the image are still refused.
- **One undetermined quantile voided every 2D metric in the pair.** A 2D median
  or 95% Hausdorff that the contours leave undetermined now blanks only its own
  cell, with the range it could take. It is judged on the reported value, so a
  median the other direction settles is reported.
- **Dose was matched to structure sets by PatientID alone.** `_load_dose()`
  walked every imaging context for the patient and kept the first
  PLAN-summation dose it found, never consulting the structure set. On a
  patient with two courses (re-irradiation, replan, composite) that silently
  applied one course's dose to both — a wrong answer, reported without any
  warning. Dose is now resolved per structure set.
- **The reference CT was cached per patient.** `_load_ct()` resolved the right
  series for the first structure set, then served that same volume to every
  later structure set of the same patient regardless of which image it was
  actually drawn on. Both the metrics worker and the qualitative (Likert)
  viewer are now keyed by the resolved image folder. The same fix applies to
  the dose cache, keyed by dose SOPInstanceUID.
- **Files sharing a SOPInstanceUID are ingested once.** A SOP UID identifies a
  DICOM object, so the same object exported to two paths (`X.dcm` and
  `X.0001.dcm`, which Eclipse exports routinely produce) was being counted
  twice — presenting one structure set as two vendor entries and one dose as
  two candidates.

### Added
- **Transparent Likert mode names the contour being graded** — *Rating: VendorA
  — Parotid_L*, or *ground truth* — in the status line, and in bold in the list
  of sources. Every outline was already labelled in that mode; with several in
  similar colours, a score could be given to the wrong one without anyone ever
  finding out. Blinded mode is unchanged.
- **Several tolerances in one run.** Surface Dice τ and APL τ accept a list,
  such as "1, 2, 3". Each tolerance fills its own column, named with it
  (`Surface Dice @ 1.00 mm`), and is its own metric in the report. Surface
  distances are computed once, so extra tolerances cost almost nothing, and both
  2D engines take the whole list in one call. The tolerance is part of the
  metric's key (`surface_dice@3mm`), so a value can never be shown under
  another run's tolerance; the single tolerance once stored for a whole results
  table is gone. A single number in an older settings file still reads.
- **The results table is saved with the session** (schema 7), so qualitative
  scoring can continue over several days without computing the metrics again.
- **A *Computed at* column** — when each row was produced — and **a *Scored at*
  column beside each rater's Likert score**, since scoring can come sessions
  later. Both are local time with the UTC offset. A score restored from an
  older session has a blank time rather than a wrong one.
- **Likert scores are never lost across sessions.** A score whose contour is no
  longer among the drawers — a drawer renamed, a test row removed, drawers that
  failed to restore — used to be dropped at restore and lost on the next save.
  It is now kept, saved again, listed in Results as a row of its own, and rejoins
  its grader once the contour is back.
- **DVH method validation** (`scripts/validate_dvh_methods.py`,
  Supplementary 1 in `docs/validation/`). It scores the DVH this version ships, v2's
  dicompyler-core DVH and the other alternatives considered against analytic
  truth:
  - the Nelms et al. 2015 datasets, Tests 1–3, beside the paper's Pinnacle3 and
    PlanIQ results;
  - 576 disc phantoms with closed-form DVHs;
  - structures up to 6,220 cc, for timing.

  Every method but v2's is the application's own `core.dvh`, so the report
  measures the code that runs. It needs the new `validation` extra.
- **The surface-distance port against Google DeepMind's package**
  (`scripts/validate_surface_distance_equivalence.py`, written as
  Supplementary 4 and its full results in `docs/validation/`). 1,000 synthetic
  mask pairs (edge cases, organ-sized, irregular, small and large shapes) at
  seven voxel spacings, anisotropic ones included. The port's every
  per-element distance and area, Dice, Hausdorff at five percentiles, both
  directional means and Surface Dice at six tolerances, and the application's
  whole metric path from images with physical spacing, are identical to the
  package's bit for bit.
- **2D metrics against the analytical shapes**
  (`scripts/validate_polygon_analytic.py`,
  Supplementary 3 in `docs/validation/`). The 150 pairs of Boukerroui et al.
  (2023), read from their DICOM files by the application and measured as a run
  measures them, against three references kept apart: the ideal shapes'
  closed-form values, the authors' own published results, and audited values
  for the polygons as stored. Distances fall within 0.058 mm of the ideal shapes
  on the coarse grid (0.008 mm on the fine), all of it the stored circles' own
  polygonal approximation; within 0.013 mm of the authors' software; and within
  5e-10 mm of the audited values.
- **The rasteriser in the authors' six-converter benchmark**
  (`scripts/validate_rasterisation_fidelity.py`,
  Supplementary 2 in `docs/validation/`). Both backends run on the benchmark's own 108
  configurations and 14 shape families, scored with its own code against its
  own references, aggregated by its own rules — which reproduce its published
  tables to 2e-16. The default backend converts all 1,512 cases, XOR contours
  included; on the 176 cases every converter shares its error is 22.46 %, level
  with Plastimatch (22.47 %), the best converter, against 19.12 % for the
  attainable optimum; it is unbiased and unchanged by oblique frames and large
  coordinates. The legacy backend scores identically to PlatiPy, its origin.
- **DVH from a mask against DVH from the contours**
  (`scripts/validate_dvh_mask_vs_polygon.py`, Supplementary 1, part B).
  A STAPLE consensus has no contours, so its dose statistics come from its
  voxels; every other structure's from its contours. Each Nelms et al. (2015)
  structure is taken both ways, the mask made from its own RTSTRUCT (and shown
  to be what a unanimous STAPLE consensus returns), and scored against the
  analytic truth and against each other, on the dataset's 0.6 mm CT and on the
  cohort's 1.07 and 1.37 mm pixels at four sub-pixel offsets. On 0.6 mm pixels
  a mask's Dmean is within 0.12 % of the contours'; on 1.37 mm pixels within
  2.2 % (a boundary shift under half a pixel), with volume up to 9.4 % and D99
  up to 13.8 % apart. The contour path is unaffected by the CT grid. Shifted
  copies of each structure then compare the ways of setting a test contour
  against a mask reference. Taking the test from its own mask too cancels the
  mask error when the two coincide on the voxels (copies shifted across the
  dose field, true difference zero: Dmean error 2.0 → 0.25 % at the 95th
  percentile on 1.37 mm pixels), but adds an independent one when they do not
  (copies shifted along it, true difference 0.5-1.5 Gy: 2.1 → 3.0 %, and every
  dose statistic worse), so the test is taken from its contours.
- **A *Dose grid coverage (%)* column.** Dose statistics describe the part of
  a structure inside the dose grid; the part outside has no calculated dose,
  so it is left out rather than given one. The column gives the share of the
  structure's volume the statistics describe on every dose row: 100 when the
  grid covers all of it. It is left out of the Report tab, like the 2D plane
  counts, because it qualifies a statistic rather than being one.
- **A *Dose status* column.** It is blank unless a requested D{X}cc is larger
  than the covered volume; that cell is left empty rather than zero.
- **Precision and recall** as 3D mask metrics, behind one *Precision + recall*
  checkbox (on by default). Precision is the share of the test's volume inside
  the ground truth and falls with over-segmentation; recall is the share of the
  ground truth the test covers and falls with under-segmentation. Dice cannot
  tell those two failures apart. F1 is not added: on binary masks it is Dice. An
  empty test has no precision and an empty ground truth no recall, so those
  cells are empty rather than zero. The audit record gains `overlap_voxels`, so
  Dice, precision and recall can be recomputed from it.
- **Canonical organ grouping (`core/organ_groups.py`, `data/organ_index.py`).**
  Collapses the spellings of one organ into one group so statistics can pool
  them, without ever pooling two organs. Grouping is on a structured key —
  `(base, laterality, qualifier)` — and **only `base` is ever fuzzy-matched**.
  That is forced by measurement, not taste: on this project's own matcher,
  left/right pairs of the same organ score 0.82–0.93 while genuinely different
  organs score 0.38–0.45, so no similarity threshold separates them and
  laterality has to be an extracted axis rather than a matching outcome.

  Assignment runs in tiers — `manual` > `dictionary` > `stripped` > `fuzzy` >
  `unassigned` — and a fuzzy result is **a proposal, never applied without
  confirmation**. It also cannot cross four barriers, each added after a real
  merge failure on cohort data: laterality extraction, index signature
  (including roman numerals, for Rib 1–12), positional signature (sup/inf,
  asc/desc, prox/dist), and a short-code difference (`UJ_Front_L` vs
  `LJ_Front_L` — upper versus lower jaw). An unassigned name is not an error:
  it forms a group of one, and the only thing lost is pooling.

  Measured: 280 names → 226 groups on the HN1 set with no left/right
  contamination, 1439 → 1289 on the larger corpus. A TG-263 mismatch is a flag,
  not a block — over 5483 ground-truth × producer pairs it blocked nothing and
  changed no selection. Fixed 11 canonical hijacks and 24 laterality inversions
  in the shipped `synonyms.json` along the way.
- **Review Organ Groups and Label Organs (Matching tab).** Frequency-ranked and
  bulk-capable, because after type triage 527 names still need assignment and a
  one-row-at-a-time dialog would not survive that; the top 100 cover 66 % of
  unresolved ROI instances. Curation lives in the Matching tab only, labels are
  label-only and never merge drawers, and answers persist on explicit session
  save.
- **Report tab (Tab 7)** — `core/statistics.py`, `data/report.py`,
  `ui/widgets/stat_plots.py`, `ui/tabs/report.py`. Paired comparison of two
  sources against a common ground truth, per organ and per metric.

  Wilcoxon signed-rank with **Pratt's** zero handling and an exact conditional
  p-value; Hodges–Lehmann point estimate with a confidence set obtained by
  **inverting that same test** rather than by a normal approximation; a
  rank-biserial effect size; and an exact sign test reported alongside always,
  so direction and magnitude can be read separately and neither can be chosen
  after the fact.

  **Each organ is its own question and its p-value is reported unadjusted.** An
  earlier design corrected across whichever organs happened to be selected,
  which made the divisor a view setting — narrowing the list made a result
  significant and widening it took the result away, on identical data. What
  multiplicity costs is stated instead (how many rows would fall below 0.05 by
  chance), and every comparison is shown, which is what makes reporting
  uncorrected p-values defensible. Recorded as a reversal in D8 of the register.

  Coverage is shown **before** any comparison, because a paired test silently
  uses only the patients both sources contoured, and a model that declines the
  hard cases is otherwise rewarded for declining them. Every comparison carries
  the counts it was computed from, and rows with equal counts but different
  patients are flagged as such.

  A comparison case is `(patient, planning CT)`, not `PatientID` — a patient
  with two courses contributes two cases, and multi-case patients are excluded
  from the paired analysis rather than silently collapsed.

  Where the design cannot reach significance at all the tab says so, rather
  than printing a column of 1.000 that reads as agreement. With unadjusted
  p-values that floor is **six paired patients**.
- **Acquisition parameters section (Report tab).** Scanner, reconstruction and
  geometry summarised across the cohort, so a report says what the images
  actually were. `core/acquisition.py` is an **allowlist** — the tags it may
  read are enumerated and the read loop iterates that list, so the allowlist is
  the code path rather than a comment next to it. Values that vary across the
  cohort are summarised as ranges, never averaged.
- **PDF export (Report tab).** Everything visible on the page, in reading
  order, as one document: a masthead band, the conditions the report was
  produced under, the tables, the figures as drawn, and a sign-off naming the
  software and version. A table exported on its own loses the selections that
  make it interpretable — which ground truth, which metric, which sources — so
  the export is the whole page or nothing. Figures can also be saved on their
  own as PNGs at 200 dpi.
- **Explicit-reference DICOM linking (`autoseg_evaluator.data.linkage`).**
  Answers the reviewer criticism that `FrameOfReferenceUID` alone cannot match
  dose to structure set when one Frame of Reference holds several imaging
  studies, structure sets and doses. Links are now resolved from the UID
  references DICOM already carries, through an ordered cascade — `explicit`
  (a referenced UID names the target outright) → `sop-overlap` (per-slice
  `ContourImageSequence` UIDs) → `for+study` → `for` → `singleton` — and,
  critically, **an ambiguous link is reported rather than guessed**. Where two
  candidates tie, every candidate is surfaced for the user to choose between,
  and the choice is recorded in `MetadataLibrary.link_overrides`.

  RTPLAN is never required nor traversed: the dose → structure set edge comes
  straight off the dose object's `ReferencedStructureSetSequence` (including
  the copy some writers nest inside `ReferencedRTPlanSequence`), so the chain
  dose → structure set → series closes with no plan file present. Verified on
  the HN1 sample set, where all 7 vendor structure sets and the dose resolve at
  the `explicit` tier.
- `linkage.assign_linkage_ids()` stamps a `linkage_id` on every series,
  structure set and dose — the connected component of the explicit reference
  graph, i.e. one coherent treatment context. Frame of Reference is never used
  to *merge* components, only as a per-entry fallback label.
- `tests/test_slicer_linkage_equivalence.py` pins the RTSTRUCT → series
  traversal against a port of SlicerRT's
  `vtkSlicerDicomRtReader::GetReferencedSeriesInstanceUID()`, and records the
  one deliberate divergence: SlicerRT's `gotoFirstItem()` silently returns the
  first of several referenced series, where we report an ambiguity.
- `tests/test_linkage.py` — 19 tests over synthetic DICOM, including a
  two-course re-irradiation fixture sharing one Frame of Reference.
- **Review Data Links dialog (Tab 1).** Lists every structure set with the
  image series and dose it resolved to and the rule that decided it, with a
  dropdown to override any of them. Unsettled links are badged on the button,
  listed in the Issues panel, and **block Tab 3 from starting a run** — the
  choice belongs to the user and is made before computation, not guessed
  during it or flagged afterwards on a results row. Dose ambiguities only
  block when a dose metric is actually switched on.
- `tests/test_data_links_ui.py` — 12 tests over the dialog and the compute
  gate.
- `scripts/compare_rasterisers.py` + `docs/archive/RASTERISER_COMPARISON.md` —
  quantifies per-ROI agreement and runtime between the two backends on a real
  cohort (PHI-safe report). RTSTRUCTs are discovered by DICOM Modality rather
  than filename, so vendor exports are no longer missed.
- `NOTICE` — the continuous backend is adapted from dcmrtstruct2nii v5 (MIT);
  full upstream licence plus an enumerated list of local modifications.
- CI now installs `dcmrtstruct2nii` so the default backend's upstream
  conformance test cannot silently skip.
- `docs/V3_RELEASE_STATUS.md` — what v3.0.0 contains, what is outstanding, and
  which numbers the release moves.
- **A *DVH from* column** on every dose row says whether its statistics were
  integrated over the structure's contours or sampled over a mask. Only a
  consensus, which has no contours, is taken from a mask; every contour is
  taken from its contours, whatever it is compared against, so against a
  consensus the mask's error is common to every source and cancels between
  them (Supplementary 1 has why the contour is not taken from its
  own mask too).

### Changed
- ⚠️ **STAPLE runs to convergence by default.** The iteration cap's default
  rises from 100 to 500, the most the Compute tab allows. SimpleITK itself sets
  no practical limit and stops when its estimates converge; the cap of 100 was
  justified by a SimpleITK default of 5 that does not exist, and on the HN1
  sample it stopped 2 of 55 organs short of convergence (the slowest needs 151
  iterations). Their consensus masks were already identical; their per-rater
  sensitivity and specificity move by up to 8e-6. The STAPLE report now also
  compares every organ with SimpleITK run on its own defaults. A settings file
  that saved 100 keeps it until *Reset to defaults* is pressed.
- **What the STAPLE crop is for, said correctly and measured.** STAPLE runs in
  the raters' union bounding box. The code justified this as keeping small
  organs from collapsing to an empty consensus; on the HN1 sample whole-image
  STAPLE empties no organ, the cochleae included. What the crop does is choose
  the estimation domain: specificity and the foreground prior are estimated
  from every voxel given, and over a whole CT both would depend on the scan's
  field of view rather than the contours. The STAPLE report now compares the
  cropped consensus with whole-image STAPLE: identical for 50 of 55 organs,
  the other five larger over the whole image by up to 8 % (Dice ≥ 0.96). The
  per-rater specificity reported is relative to the box. No result changes.
- **One computation per results table.** Computing again replaces the table,
  after the user confirms, with the option to export it first; rows are never
  updated or merged, so every row in a table comes from one run with one set of
  settings. Before, a second run appended its rows beside the first. Likert
  scores are not part of a computation and are kept; *Clear* in Results
  likewise discards computed rows only.
- **Loading a different folder, or opening a session, clears the previous
  cohort's work** — results, Likert scores, organ labels and the session path —
  after the user confirms, with the option to save the session first. Before,
  they carried into the new cohort, and Save could overwrite the previous
  cohort's session. A rescan of the same folder keeps everything.
- **DVHs are integrated over the contours themselves, not taken from
  dicompyler-core.** ⚠️ **This moves every DVH number.**
  - **How it works.** Each structure is read by the same rules as the geometric
    metrics. Each contour stands for a slab one CT slice thick, divided into
    sub-cells that each count for exactly the area of the region inside them.
    The dose is interpolated trilinearly at each sub-cell's centroid, and the
    samples accumulate into a histogram of 1 mGy bins.
  - **Spacing.** Sub-cells are 0.25 mm for small organs. The spacing is the
    finest of 0.25, 0.5 and 1 mm that keeps a structure within ten million
    samples. A structure past that even at 1 mm, such as a body contour, is
    sampled once per voxel, so no structure takes much more than a second.
  - **Consensus structures.** A STAPLE or Tab 2 consensus has no contours. Its
    voxels are sampled by the same rule, so a consensus comparison no longer
    mixes two DVH methods.
  - **Accuracy.** Against the analytic datasets of Nelms et al. 2015, 10 of 195
    dose-volume parameters are more than 3 % off on their Test 2. v2's
    dicompyler-core DVH had 140; PlanIQ, in the paper, had 18.
  - **Defects this removes.** v2's DVH had three:
    - it reported D99 and D95 as 0 Gy whenever the coldest 1 cGy bin held more
      than 2 % or 10 % of the volume, which was D99 in 49 of the 100 Nelms
      cases;
    - it sampled the dose only at dose-grid points;
    - its supersampling for very small structures misplaced the dose by half a
      dose pixel on average.
  - **Single-plane structures.** A structure drawn on one slice now counts as
    one CT slice thick. v2 used the dose grid's slice spacing.
  - **Dependencies.** dicompyler-core leaves the runtime dependencies, for the
    `validation` extra.
- **Session schema v4 → v5 → v6.** v5 adds `link_overrides`, recording the
  answers given in Review Data Links so a cohort whose links needed settling by
  hand does not need settling again on reload. v6 adds `organ_assignments`,
  mapping a raw ROI name to the organ it was assigned — kept in the session
  rather than in the settings file, because the answers are about one cohort
  and not about the application. Earlier sessions load unchanged.
- **The sub-voxel (`continuous`) mask rasteriser is now the default.**
  ⚠️ **This changes numerical output.** Every mask-derived metric moves —
  volume, Dice, Hausdorff, MSD, Surface Dice, APL, centre-of-mass, STAPLE, and
  truncated DVH (whose craniocaudal window is derived from the GT mask).
  Results produced before and after this change are not directly comparable.

  The previous behaviour transformed contour vertices with
  `TransformPhysicalPointToIndex`, snapping each one to the nearest voxel
  centre *before* filling. That places the contour boundary exactly on the
  sampling lattice — the degenerate case for the point-in-polygon fill — and
  ties resolve as "inside", so every voxel the boundary touches is filled. The
  effect is a systematic, one-directional dilation of ~0.76 voxels around the
  perimeter: a volume over-estimate of roughly `1.5 / R` (R = structure radius
  in voxels), from ~3 % on large organs to >50 % on structures one to two
  voxels across. Against analytic disc phantoms the old path measured +13.6 %
  at R=10 and +55.6 % at R=1.5; the new path is within ~2 % for R ≥ 10.
  Across 357 ROIs from 7 vendor RTSSes in the HN1 sample set, the old path
  produced a larger volume in **every single case**.

  The old behaviour is preserved as the opt-in `legacy` backend — selectable
  per call (`backend=`), process-wide (`set_default_rasteriser()`), or via the
  `AUTOSEG_RASTERISER` environment variable — and is still pinned as 110/110
  voxel-identical to PlatiPy 0.7.2 by `tests/test_platipy_equivalence.py`.
  Case coverage is unchanged: on the HN1 set both backends converted exactly
  the same 357 of 389 ROIs.
- **Both metric streams read contours through one function**
  (`core/contour_reading.py`). ⚠️ **This changes 3D numerical output for some
  vendors.** Previously the 3D fill combined every loop on a slice by
  exclusive-or, while the 2D stream used a stricter parser. The two disagreed on
  touching loops (a one-voxel seam in 3D), partial overlaps (the overlap silently
  deleted in 3D), outlines crossing themselves (filled in 3D, refused in 2D) and
  declared `CLOSEDPLANAR_XOR` (no 3D mask at all). Now one set of rules applies
  to both. Holes, islands and touching loops are read as drawn. Partial overlaps
  and duplicated loops are refused, because union and hole are both plausible.
  An outline crossing itself is read only when the even-odd and non-zero rules
  agree on its region. A refused structure's row now says why, instead of
  "could not be rasterised".

  The 3D fill also changes its edge rule. A voxel centre exactly on an outline
  edge now belongs to one side (half-open), so shapes keep their true area.
  Previously it counted as inside for every loop, which over-filled. On the
  tender H&N cohort (5,166 structures), 4,619 masks are voxel-identical and 547
  change. Every one of the 22,123 changed voxels has its centre on an edge.
  They are almost all vendor A's, the only vendor drawing along rows and
  columns of voxel centres. Its volumes fall by 0.1–3.5% on most structures and
  up to 6.25% on the left eye. The 2D reading matches the vendored parser on all
  5,143 structures both read, and additionally reads 6 that it refused. The fill
  is also 3.7x faster.

### Removed
- **`scripts/validate_dvh_against_upstream.py` and `tests/test_dvh_equivalence.py`.**
  Both checked that v2's DVH reported dicompyler-core's numbers unaltered; with
  dicompyler-core gone from the application there is nothing left to check.
  `docs/DVH_VALIDATION_REPORT.md` stays, marked historical. `tests/test_dvh.py`
  replaces the tests, against answers known exactly.
- **Mask-based Added Path Length (`apl_mean`, `apl_total`).** ⚠️ **v1 APL values
  are now historical, not reproducible by this version.** Added path length
  measures boundary that would have to be redrawn. That needs the edge as drawn,
  not as a voxel staircase, so it now comes only from the 2D contour metrics
  (2D APL and NAPL, with both directions), measured on the RTSTRUCT outlines. The
  mask version was a port of PlatiPy's per-slice dilation.

  Gone with it:
  - the two checkboxes and the APL τ on the Compute tab;
  - the same in Tab 2's inter-observer dialog;
  - the results and CSV columns, and the Report tab entries;
  - PlatiPy APL parity, from the equivalence tests and from
    `scripts/validate_against_upstream.py`.

  Older `settings.json` files that still switch mask APL on load cleanly: the
  retired keys are dropped on load and left out of the next save. Sessions never
  stored metric settings, so no session schema change is needed.

  `scripts/validate_against_upstream.py` now pins its PlatiPy mask-parity check
  to the `legacy` rasteriser. That is the only backend meant to match PlatiPy;
  since the default changed, the check was comparing the wrong one.

## [2.6.1] — 2026-06-30

### Fixed
- **Window fits any screen on macOS / Linux.** The main window is now clamped
  to the display's available work area on startup — its size is capped to the
  screen and its position pulled fully on-screen — so it no longer opens larger
  than a laptop display or off-screen where the title bar sits under the macOS
  menu bar (and couldn't be dragged back). The **Qualitative Assessment** tab is
  also scroll-wrapped like every other tab, so the window can always be resized
  smaller to fit. Behaviour now matches Windows.

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
