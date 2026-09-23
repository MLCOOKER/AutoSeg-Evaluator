# v3.0.0 — release status

Eight changes were scoped together on 2026-09-15 and are being released as one
version, because several of them move numbers and shipping them separately
would produce three releases whose results cannot be compared with each other.

Nothing here is released. This file records where each item stands so the scope
of v3.0.0 is legible from the repository rather than from memory.

**Checkpoint: 2026-09-24.** Branch `v3-dev` at `a63845a`; 1166 tests pass;
`ruff check` and `ruff format --check` clean.

---

## Scoreboard

| # | Item | Status |
|---|------|--------|
| 1 | Precision / recall metrics | **Not started** |
| 2 | Validate dcmrtstruct2nii against PlatiPy | **Done** |
| 3 | Robust DICOM ingestion and grouping, without RTPLAN | **Done** |
| 4 | Performance / parallelisation | **Not started** — needs re-profiling first |
| 5 | Two-stream metric architecture | **Done**, all seven phases |
| 6 | Canonical organ bucketing + statistics | **Done**, both halves |
| 7 | Quantify DVH on mask vs on RTSS | **Not started** |
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

Not started. No `precision` or `recall` in `core/metrics.py`. Cheap, and it is
the only Stream A delta in #5, so it can land on its own.

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

Not started. Both engines are live and they disagree by construction — grid,
volume definition (contour integration versus voxel counting) and
interpolation all differ:

- `core/dvh.py:73` `compute_dvh_metrics` — dicompyler, rasterises contours onto
  the dose grid.
- `workers/metrics_worker.py:730` `_dvh_for_consensus_mask` — resamples dose
  onto the mask grid and samples voxel doses.

An earlier all-mask prototype diverged by up to ~50 % on V{X}Gy, which is why
the mask path was rejected for RTSS in v2.4.2. Worth re-measuring now the
rasteriser has changed and the masks are smaller. This is measurement only, no
new behaviour, and it converts an acknowledged limitation in the manuscript
Discussion into a quantified one.

### 8 — Validation report for the Stream B metrics

Partly done. `docs/POLYGON_VALIDATION_REPORT.md` covers the published synthetic
pairs, the stress cases, the adapter and the DICOM path, and
`scripts/validate_contour_reading.py` checks the shared reading on a real
cohort. The report on this paper's own data is still to be written. Because
mask-APL is going, there is no cross-stream head-to-head metric left, so the 2D
metrics are validated against synthetic and analytic ground truth rather than
against Stream A.

---

## Documentation still to write

The features above landed with their design records — the statistics register,
the report tab spec, the rasteriser comparison — but the **user-facing** docs
still describe the application as it was before v3:

- `docs/PROJECT_OVERVIEW.md` walks through "the six tabs". There are seven; the
  Report tab is not described anywhere in it, and neither is the organ-grouping
  curation added to the Matching tab.
- `README.md`'s numbered workflow stops at Results, so the Report tab is
  missing from it, as is the organ curation now in step 3.
- Mask-APL is described as a shipped metric throughout both, and in the
  manuscript Methods. That text has to be rewritten when #5 removes it, not
  before — otherwise the docs describe an application that does not exist yet.

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
- **Three changes move numbers**: the rasteriser default, the half-open fill
  under the shared contour reading (vendor A in particular), and the removal of
  mask-APL in phase 7. Results produced before and after v3.0.0 are not directly
  comparable, and the release notes have to say so plainly.
