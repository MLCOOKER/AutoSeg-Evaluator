# v3.0.0 — release status

Eight changes were scoped together on 2026-09-15 and are being released as one
version, because several of them move numbers and shipping them separately
would produce three releases whose results cannot be compared with each other.

Nothing here is released. This file records where each item stands so the scope
of v3.0.0 is legible from the repository rather than from memory.

**Checkpoint: 2026-09-24**, with #7 done: branch `v3-dev`; 1199 tests pass;
`ruff check` and `ruff format --check` clean.

---

## Scoreboard

| # | Item | Status |
|---|------|--------|
| 1 | Precision / recall metrics | **Done** |
| 2 | Validate dcmrtstruct2nii against PlatiPy | **Done** |
| 3 | Robust DICOM ingestion and grouping, without RTPLAN | **Done** |
| 4 | Performance / parallelisation | **Not started** — needs re-profiling first |
| 5 | Two-stream metric architecture | **Done**, all seven phases |
| 6 | Canonical organ bucketing + statistics | **Done**, both halves |
| 7 | Quantify DVH on mask vs on RTSS | **Done** — DVH now integrated over the contours |
| 8 | Validation report for the Stream B metrics | **Partly done** — synthetic half written |

---

## Done

### 2 — Rasteriser validation, and the default flip

`scripts/compare_rasterisers.py` and `docs/RASTERISER_COMPARISON.md` quantify
per-ROI agreement and runtime between the two backends on a real cohort. The
sub-voxel (`continuous`) backend is now the default, which **moves every
mask-derived number**; the old path survives as the opt-in `legacy` backend and
is still pinned voxel-identical to PlatiPy 0.7.2.

Measured against analytic disc phantoms, the old path over-estimated volume by
+13.6 % at R=10 and +55.6 % at R=1.5. Across 357 ROIs from 7 vendor RTSSs it
produced a larger volume in every single case.

### 3 — DICOM linking by explicit reference

`data/linkage.py` resolves dose → structure set → series from the UID
references DICOM already carries, through an ordered cascade, and **reports an
ambiguity rather than guessing it**. RTPLAN is never required or traversed.
Ambiguities are settled in Tab 1 before any computation, which makes the two
wrong-answer defects this replaced (dose matched by PatientID alone; the CT
cached per patient) structurally impossible rather than patched.

Pinned against a port of SlicerRT's traversal in
`tests/test_slicer_linkage_equivalence.py`, with the one deliberate divergence
recorded: SlicerRT takes the first referenced series silently, we report the
ambiguity.

### 6 — Canonical organ grouping, and the statistics on top of it

**Grouping** (`core/organ_groups.py`, `data/organ_index.py`). Names collapse on
a structured key — `(base, laterality, qualifier)` — and only `base` is ever
fuzzy-matched. That is not a stylistic choice: on this project's own matcher,
left/right pairs score 0.82–0.93 while genuinely different organs score
0.38–0.45, so no threshold separates them and laterality has to be an extracted
axis. Fuzzy matching is a proposal, never applied unconfirmed, and cannot cross
four barriers, each added after a real merge failure on the user's data.
Measured: 280 HN1 names → 226 groups with no left/right contamination; 1439
names → 1289 on the larger corpus.

Curation lives in the Matching tab only, labels are label-only and never merge
drawers, and answers persist on explicit session save.

**Statistics** — Tab 7, Report (`core/statistics.py`, `data/report.py`,
`ui/widgets/stat_plots.py`, `ui/tabs/report.py`). Wilcoxon signed-rank with
Pratt's zero handling and an exact conditional p-value, Hodges–Lehmann with a
confidence set obtained by inverting the same test, rank-biserial, and an exact
sign test alongside. Each organ is its own question and its p-value is reported
**unadjusted** — see D8 in the register for why that reversed, and what is
reported in place of a correction.

Design decisions are in `docs/V3_REPORT_STATISTICS_REGISTER.md`, written for
external audit, with worked examples regenerated from the shipped code by
`scripts/make_register_tables.py` so every cell is recomputable.

---

## Remaining

### 1 — Precision / recall

**Done (2026-09-24).** `precision_recall()` in `core/metrics.py`, behind one
*Precision + recall* checkbox in the 3D group (on by default), two results
columns in the overlap band, and a definitions entry. An empty test has no
precision and an empty ground truth no recall, so those cells are empty, not
zero. The audit record gains `overlap_voxels`. The Report tab already knew
their names, direction and 0–1 axis. Not added to Tab 2's inter-observer table:
between two observers, neither is the reference, so precision and recall would
depend on which one happened to be listed first.

**F1 is deliberately omitted** — it is identical to Dice on binary masks.

### 4 — Performance / parallelisation

Not started; no `multiprocessing` or `concurrent.futures` anywhere in `src/`.
The original case rested on profiling that put ~91 % of rasterisation time in
`skimage.draw.polygon`. **That call is gone from the default path**: the shared
contour reading (spec D10) fills with a scanline that took the tender H&N
cohort from 815 s to 219 s. Re-profile a full run before deciding whether
parallelism is still the lever.

### 5 — Two-stream metric architecture

Phases 1–6 are done: both polygon engines vendored and validated, the grid and
adapter, worker integration, the split Tab 5, `2D`/`3D` naming, Report
families, the definitions dialog and the optional two-stream audit sidecar.
Design and decisions are in `docs/V3_POLYGON_METRICS_SPEC.md`.

The first run on real data (tender H&N) produced three further decisions, all
landed:

- **D8** — structure sets whose references name nothing in the loaded data are
  read from their coordinates instead of refused. One vendor lost every
  structure to this before (0 → 435 of 447).
- **D9** — an undetermined median or HD95 blanks only its own cell, judged on
  the reported value, instead of voiding every 2D metric in the pair.
- **D10** — both streams read contours through one function
  (`core/contour_reading.py`), and the 3D fill is half-open. ⚠️ This moved
  vendor A's 3D masks (volumes −0.1 to −3.5 %, Eye_L up to −6.25 %): every
  changed voxel had its centre exactly on an outline edge.

**Phase 7 is done** (2026-09-24): mask APL is removed from the code, the
Compute tab, Tab 2's inter-observer dialog, results, the Report tab and the
tests, and `settings.json` drops its retired keys on load. Added path length
now comes only from the 2D stream. This makes the v1 (Rusanov et al. 2025) APL
values historical rather than reproducible. `7b6cec1` is the last commit that
computes mask APL. The manuscript Methods text lives outside this repository
and still needs the matching change.

### 7 — DVH on a mask versus DVH on RTSS

**Done (2026-09-24).** ⚠️ **This moves every DVH number.** The decision:
integrate the dose over the contours themselves (**polygon**). Among the
methods compared it is the most accurate, and at equal spacing it runs at the
same speed as the voxel alternative (below). The sub-sample spacing is the
finest of 0.25, 0.5 and 1 mm that keeps a structure within **ten million
samples** (user's choice of cap). A structure past the cap even at 1 mm is
sampled once per voxel. That was added after the cohort check: on its 1.37 mm
CT, "1 mm" still meant 27 samples a voxel, and a 30 L external took 220 M
samples and 35 s. That keeps no structure much over a second.

**Speed on a real patient** (one tender H&N patient, 496 structures from seven
structure sets, idle machine): 189 s against v2's 986 s. By size, median per
structure:

| Size | v3 | v2 |
|---|---|---|
| under 1 cc | 0.01 s | 0.03 s |
| 1–10 cc | 0.11 s | 0.14 s |
| 10–100 cc | 0.48 s | 0.48 s |
| 100–1,000 cc | 0.46 s | 1.44 s |
| over 1,000 cc | 1.1 s | 15 s |

On the benchmark's synthetic spheres v2 is faster up to about 33 cc (0.17 s
against 0.44 s). Their dose grids are small, and v2's cost grows with the dose
grid, because it tests every dose-grid point on each contour plane.

What was implemented:

- **`core/dvh.py` is rewritten.** It holds `structure_dvh`, `mask_dvh`,
  `DoseGrid` (any orientation) and `DoseHistogram`.
- **The worker's five DVH call sites use it.** A consensus (STAPLE, or Tab 2 as
  ground truth) is sampled over its voxels by the same rule.
- **Nothing is hidden.** A *Dose status* column says when part of a structure
  lies outside the dose grid (counted at 0 Gy) or a D{X}cc exceeds the
  structure, and the audit sidecar records each DVH's spacing and sample count.
- **dicompyler-core leaves the runtime dependencies.** It moves to the
  `validation` extra.
- **Tests.** `tests/test_dvh.py` tests against answers known exactly; the
  dicompyler equivalence tests are retired.
- **The report measures the shipped code.** It scores the rule exactly as
  shipped, as **autoseg**. v2's calls are frozen in the script and reproduce
  v2's numbers exactly on all 100 Nelms rows.

The measurements below were made before the decision; the report now
includes the shipped rule itself.

`scripts/validate_dvh_methods.py` scores the candidate DVH methods against
analytic truth and writes `docs/DVH_METHOD_VALIDATION.md`. The benchmarks:

- **Nelms et al. 2015** (Med Phys 42:4435), Tests 1–3, beside the paper's own
  Pinnacle3 and PlanIQ results.
- **576 disc phantoms** with closed-form DVHs.
- **Large structures up to 6,220 cc**, for timing.

**The manuscript will cite this report; regenerate it whenever a DVH method
changes.** The Nelms data live outside the repository (see the report).

Parameters more than 3 % off the analytic value, without Dmin and Dmax:

| Method | Nelms Test 1 | Nelms Test 2 |
|---|---|---|
| dicompyler (production today) | 119/260 | 140/195 |
| mask: the 3D metrics' voxels, dose at centres | 8/260 | 56/195 |
| mask-ss: same voxels, dose sampled ≤ 0.25 mm apart | 0/260 | 10/195 |
| polygon: shared reading, exact sub-cell areas | 0/260 | 10/195 |
| PlanIQ (paper) | 5 | 18 |
| Pinnacle3 (paper) | 32 | 53 |

Three defects in today's DVH path, all in dicompyler-core 0.5.6:

- **D*x* lookup returns 0 Gy.** It picks the bin *nearest* the target volume
  and takes the first bin on a tie. D99 was 0 Gy in 49 of the 100 Nelms cases.
  Any D99 or D95 of 0 Gy in earlier results is this.
- **Sampling only at dose-grid points** in each contour plane. With the lookup
  corrected it is still 103/195 on Test 2.
- **In-plane supersampling misplaces the dose.** Each value lands 0–1 dose pixel
  from where it belongs, half a pixel on average. Production uses this as the
  retry for structures too small to contain a dose point.

**mask-ss against polygon.**

- **On Nelms they tie.** What both still miss are shapes that change between
  contour planes, the same cases PlanIQ misses.
- **On the discs polygon is more accurate.** Its volume is exact, where the
  voxels err by up to 16 % at R = 2.5 mm. Its worst dose error is 0.11 against
  0.64 mm-equivalent.
- **Speed is the same.** Both are dominated by dose look-ups, which scale with
  volume ÷ spacing³, and are within a few percent of each other from run to
  run. At 0.5 mm, polygon against mask-ss:

  | Structure | polygon | mask-ss |
  |---|---|---|
  | 268 cc | 0.68 s | 0.71 s |
  | 6,220 cc | 18.7 s | 18.4 s |

  At 1 mm, a 6,220 cc cylinder takes about 1 s with at most 0.15 mm-equivalent
  error. This parity needs polygon's signed-area accumulation; clipping each
  sub-cell with shapely was 20× slower.

**Two corrections to what was recorded here before.**

- **Volume.** The two engines were said to differ in "contour integration versus
  voxel counting". That was wrong: dicompyler counts dose-grid points too. They
  differ in grid, loop rules, edge rule and dose sampling.
- **The v2.4.2 rejection.** The mask path was rejected in v2.4.2 because an
  all-mask prototype differed from dicompyler by up to ~50 % on V{X}Gy. That
  was measured against dicompyler, not against truth. Against truth,
  dicompyler is the outlier.

### 8 — Validation report for the Stream B metrics

Partly done. `docs/POLYGON_VALIDATION_REPORT.md` covers the published synthetic
pairs, the stress cases, the adapter and the DICOM path, and
`scripts/validate_contour_reading.py` checks the shared reading on a real
cohort. The report on this paper's own data is still to be written. Because
mask-APL is going, there is no cross-stream head-to-head metric left, so the 2D
metrics are validated against synthetic and analytic ground truth rather than
against Stream A.

---

## Documentation

**Done with phase 7 (2026-09-24).** `README.md` and `docs/PROJECT_OVERVIEW.md`
now describe v3: seven tabs, both metric streams, the shared contour reading
and half-open fill, organ grouping and the Report tab, the audit record, and
mask APL's removal with what it means for v1 values. `NOTICE` records the new
changes to the code adapted from dcmrtstruct2nii.
`docs/POLYGON_VALIDATION_REPORT.md` was regenerated through the shared reading.

**Still to do, outside this repository:** the manuscript Methods text describes
mask APL and needs the matching change.

**Stale by design:** `docs/VALIDATION_REPORT.md` was generated at v2.3.2 and
includes the since-removed mask APL rows. Regenerating it needs the HN1 data;
the README and overview say what it covers.

## Carried risks

- **The Report tab has had one real run** (tender H&N, 2026-09-23), which found
  the 2D metrics reported without a direction — fixed. Every automated test
  behind it is still a synthetic fixture.
- **The PDF export's typography is unverified on this machine.** The offscreen
  Qt platform reports zero font families, so headless renders show every glyph
  as a box. Layout, tables, figures and the masthead are verified; the type is
  not.
- **`main` is 16 commits ahead of `origin/main`.** Nothing is at risk — every
  one of those commits is contained in `origin/v3-dev` — but `origin/main` does
  not yet show the rasteriser, linking or organ-grouping work.
- **Four changes move numbers**: the DVH now integrated over the contours
  (every dose statistic), the rasteriser default, the half-open fill
  under the shared contour reading (vendor A in particular), and the removal of
  mask-APL in phase 7. Results produced before and after v3.0.0 are not directly
  comparable, and the release notes have to say so plainly.
