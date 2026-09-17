# v3 — Statistical Report Tab (Implementation Spec)

_Status: agreed design, not yet implemented. Written 2026-09-17._

Specifies a new **Report** tab, placed after Results, that turns the per-contour
metric rows into an analysis a clinician can act on. This is the consumer of the
canonical organ grouping added earlier in v3 — grouping exists so that results
from drawers named differently across patients can be pooled into one organ.

---

## 1. Motivation

From peer review of the v2 manuscript:

> _"If I were to use this tool, I would want to know: Is the difference observed
> statistically significant and therefore warrants altering my clinical practice
> guidelines to the team? Having automatic paired non-parametric test of
> significance between the data points would provide me data if we should change
> our practice, or if a larger dataset is required."_

Three separate questions sit inside that, and the report must answer all three:

1. **Is the difference real?** — a paired non-parametric test.
2. **Does it matter?** — an effect size with a confidence interval, in the
   metric's own units. A p-value cannot answer this; with 30 patients a Dice
   difference of 0.004 can reach p = 0.001.
3. **Do I need more data?** — answered by the width of the interval, not by
   post-hoc power, which is a monotone re-expression of the p-value and says
   nothing the p-value did not already say.

Users currently answer these by exporting CSV and running their own pandas +
seaborn scripts. Those scripts are the requirement made concrete: a box-plot per
metric grouped by organ with vendor as hue, and a Mean(SD) / Median(Min/Max)
pivot of organ against vendor. The report absorbs both and adds the inference.

### Design decisions (locked)

| Topic | Decision |
|---|---|
| Primary descriptive | **Median [Q1, Q3] with a 95% CI for the median.** Mean (SD) and min/max are supplementary. |
| Paired test | **Wilcoxon signed-rank** for two sources; **Friedman** plus post-hoc pairwise Wilcoxon for three or more. |
| Point estimate | **Hodges–Lehmann**, with its exact Wilcoxon-derived CI — so estimate and test can never disagree. |
| Multiplicity | **Holm**, applied per metric per source-pair across organs. Raw and adjusted p both shown. |
| Distribution figure | **Violin with overlaid points and median/IQR marks.** No bare box plots. |
| Comparison figure | **Forest plot** against a user-chosen reference source. |
| Coverage | A first-class result, distinguishing "declined to contour" from "never ran". |
| Unit of analysis | One (patient, canonical organ, source) observation. Never pooled across organs. |

---

## 2. Scope and non-goals

**In scope:** descriptive statistics, paired significance testing with
multiplicity control, coverage accounting, violin and forest figures, Likert
analysis, CSV and figure export, and an auto-written methods paragraph.

**Explicitly not in scope**, each for a reason:

- **Mixed-effects models.** Organs within a patient are not independent, and a
  random intercept per patient would model that properly. Per-organ analysis
  sidesteps it instead — within one organ each patient contributes exactly one
  observation — which is simpler, assumption-free, and enough for the question
  asked. Revisit only if pooling across organs is ever wanted.
- **Pooling organs, or pooling left with right, by default.** Both break the
  one-observation-per-patient property. `OrganKey.pooled` already exists for
  laterality pooling; expose it as an opt-in that warns.
- **Comparing DVH metrics across patients on different prescriptions** without
  normalisation. D95 in Gy is not comparable between a 70 Gy and a 54 Gy plan.
- **Post-hoc power.** See §1.
- **Parametric tests.** Dice is bounded and left-skewed; Hausdorff has a heavy
  right tail. Neither is usefully normal at these sample sizes.

---

## 3. Data model

### 3.1 Input

`ResultsManager.rows()`, which already carries everything needed:

| Field | Use |
|---|---|
| `patient_id` | Pairing key |
| `canonical_organ`, `organ_laterality`, `organ_qualifier` | Grouping key |
| `test_source_label` | The thing being compared |
| `gt_source_label` | Which reference the metric was computed against |
| `metrics` | The values |
| `organ_tier` | Whether the organ was recognised or stands alone |

**Row filtering.** The results table also holds rows that are not vendor-vs-GT
comparisons — GT-vs-dose rows, STAPLE summary rows, qualitative-only rows. The
report includes only rows with a test source and at least one computed metric.
The exact predicate must be read off `data/results.py` at implementation time
rather than assumed here.

### 3.2 Unit of analysis

One observation is **(patient, canonical organ, source) → metric value**.

Comparisons pair on `(patient, canonical organ)`, so a patient acts as their own
control. That is the whole reason to use a paired test: at n = 10 it is far more
powerful than an unpaired comparison, because between-patient variation in
anatomy dwarfs between-vendor variation in performance.

### 3.3 Metric direction

Every metric needs a declared direction or the report cannot say "better",
only "different" — and a reader will guess wrong roughly half the time.

```
higher is better : dice, surface_dice, precision, recall, volume_ratio*
lower is better  : hausdorff95, hausdorff100, mean_surface_distance,
                   apl_mean, apl_total, com_offset_mm, volume_diff_cc*
```

`volume_ratio` and `volume_diff_cc` are signed and best-at-a-target rather than
monotone; treat them as **two-sided with no direction** and suppress the
better/worse wording.

---

## 4. Statistics

All of this lives in `core/statistics.py` as pure functions on sequences of
floats — no Qt, no pandas, no I/O — so it is testable against hand-computed
values. `scipy` and `numpy` are already hard dependencies; nothing new is added.
Holm is about fifteen lines and is not worth a `statsmodels` dependency in an
application that ships as a portable executable.

### 4.1 Descriptive summary

For each (organ, source, metric):

```
n, median, q1, q3, ci_low, ci_high, mean, sd, minimum, maximum
```

**The CI is distribution-free, from order statistics.** For sample size `n` and
the largest `k` satisfying `P(Bin(n, ½) < k) ≤ 0.025`, the interval is
`[x₍k₎, x₍n−k+1₎]`. This makes no distributional assumption, which a bootstrap
percentile interval effectively does at these sample sizes.

It has a hard floor:

| n | Interval | Coverage |
|---|---|---|
| ≤ 5 | **not estimable** | — |
| 6 | [x₁, x₆] (the full range) | 0.969 |
| 10 | [x₂, x₉] | 0.979 |
| 20 | [x₆, x₁₅] | 0.959 |

Below n = 6 the report prints `—`, not a fabricated number. At n = 10 — a common
cohort size here — the interval spans the 2nd to 9th values and looks wide. It
is wide correctly, and the report should not be tuned to make it look narrower.

### 4.2 Paired comparison of two sources

Restricted to patients where **both** sources produced the organ.

- **Test:** Wilcoxon signed-rank, exact where `scipy` offers it (n ≤ 25),
  normal approximation with continuity correction above that.
- **Zeros:** Pratt's method (`zero_method="pratt"`), not the scipy default.
  Discarding zero differences inflates the apparent effect when two sources
  agree exactly on some cases, which happens for easy organs.
- **Point estimate:** the **Hodges–Lehmann** estimator — the median of the
  `n(n+1)/2` Walsh averages `(dᵢ + dⱼ)/2` for `i ≤ j`. At n = 50 that is 1275
  values; cost is irrelevant.
- **Interval:** the exact CI derived from the signed-rank distribution — the
  k-th and (M+1−k)-th ordered Walsh averages, with k from the same critical
  value the test uses.

  Pairing Hodges–Lehmann with its own Wilcoxon-derived interval is the point.
  A plain median-of-differences with a bootstrap interval is defensible in
  isolation but can produce p = 0.03 beside a CI that crosses zero, which is
  exactly the internal inconsistency a statistical reviewer looks for.
- **Effect size:** rank-biserial correlation, `r = (W⁺ − W⁻) / (W⁺ + W⁻)`,
  in [−1, 1]. Comparable across metrics with different units.

### 4.3 Three or more sources

Friedman test across sources on complete blocks — patients where *every* source
produced the organ. Report the number of complete blocks prominently, because it
can be far smaller than any individual source's coverage.

A significant Friedman result is followed by pairwise Wilcoxon post-hoc tests,
Holm-corrected within that organ's pairwise family.

### 4.4 Multiplicity

**Holm–Bonferroni**, applied **per metric, per source-pair, across organs**.

That family is the set interpreted together: *"across these organs, where does
vendor X differ from vendor Y on Dice?"* Extending the family across metrics as
well would be badly over-conservative — Dice, surface-DSC and mean surface
distance on the same contours are strongly correlated, and Holm assumes nothing
about dependence.

The family definition is printed beside the table. A correction whose scope is
not stated is not auditable.

Implementation: sort p ascending; `p_adj[i] = max(p_adj[i−1], (m − i) · p[i])`,
clipped at 1.0. The running maximum enforces monotonicity, which a naive
implementation gets wrong.

### 4.5 Coverage

Three distinct states, which a results table renders identically:

| State | Meaning |
|---|---|
| `8/10` | Ran on 10 patients, produced this organ for 8 — **declined to contour** |
| `8/8`, 2 patients absent | Only ran on 8 — **missing data** |
| `—` | Never produced this organ at all |

Only the first is a performance finding.

**The bias this exposes must be surfaced, not just recorded.** A paired test uses
only patients where both sources produced the organ. If A covers 10 and B covers
6, the comparison runs on those 6 — and that subset is unlikely to be random,
because models fail on hard cases. The comparison silently becomes "A versus B
on the easy cases", and B is rewarded for refusing the difficult patients.

Every comparison row therefore shows `n_pairs`, `n_a` and `n_b`, and the report
warns when `n_pairs` is materially below either. This interacts directly with
the reviewer's question: "significantly better" means something quite different
when the better model attempted two thirds of the cases.

---

## 5. Figures

`ui/widgets/plots.py`, matplotlib on a `FigureCanvas`, following the existing
DVH plotting widget.

### 5.1 Violin — distribution per organ per source

Replaces the box plot, for an analytical reason rather than an aesthetic one.
Dice distributions in auto-contouring are frequently **bimodal**: the model
either finds the organ (~0.85) or misses it entirely (~0.1). A box plot renders
that as a wide box with a median near 0.6 — a value no individual case took,
describing a behaviour no patient experienced. The violin shows two lobes, which
is a different clinical message: "usually fine, occasionally catastrophic"
rather than "uniformly mediocre".

**Always violin + overlaid individual points + median and IQR marks.** At n = 10
a kernel density estimate invents smoothness the data does not support; the
points carry the truth and the violin is interpretation. Never a bare violin.

X axis: organ, labelled `Organ (N=…)`. Hue: source. This mirrors the existing
external script so its output is reproduced, not replaced.

### 5.2 Forest — the "should I change practice" figure

One figure per metric, against a **user-chosen reference source**. With five
sources there are ten pairs, which nobody reads; a reference reduces it to four
and asks the real question — *should we move off what we currently use?*

```
Dice — vendors vs Limbus (reference)

Parotid_L    n=10  ├────●────┤                     +0.04   p=0.002  p_holm=0.012
Parotid_R    n=10     ├───●───┤                    +0.03   p=0.014  p_holm=0.056
Brainstem     n=9  ├──●──┤                         +0.01   p=0.38   p_holm=1.00
SpinalCord   n=10        ├──────○──────┤           +0.02   p=0.31   p_holm=1.00
                     ╎          ╎
              favours MVision   0   favours Limbus
```

- Point: Hodges–Lehmann estimate. Whiskers: its exact CI. Reference line at 0.
- Rows sorted by effect size, so actionable organs rise to the top.
- Filled marker when Holm-adjusted p < 0.05; hollow otherwise.
- **Direction annotation computed from §3.3**, never left to the reader.
- Optional shaded trivial zone if a minimum clinically important difference is
  supplied. The figure works without one.

### 5.3 Coverage heatmap

Organ × source, colour by proportion contoured, annotated with the counts from
§4.5. Makes an absent row visible as absence rather than as a gap.

---

## 6. The tab

Tab 7, after Results. Sections stacked in a scroll area, each collapsible.

**Controls:** metric selector · reference source · organ filter (all / recognised
only / OAR only) · laterality pooling toggle (off by default, warns) · optional
MCID per metric · Recompute · Export.

**Sections:**

1. **Cohort summary** — patients, organs, sources; acquisition summary drawn
   from DICOM (scanner make/model/software, kVp, kernel, and the distribution of
   voxel geometries, which is the single most transfer-relevant fact); coverage
   matrix.
2. **Descriptive statistics** — organ × source, median [Q1, Q3] and CI primary,
   mean (SD) and min/max supplementary.
3. **Distributions** — violin figures per metric.
4. **Paired comparisons** — table and forest plot.
5. **Qualitative** — Likert distributions, Wilcoxon between sources, and
   inter-rater agreement (Krippendorff's α) when more than one grader exists. A
   difference between vendors means little if the graders disagree with each
   other. The Dice-versus-Likert relationship is worth surfacing: where they
   diverge, the geometric metric is failing to capture clinical judgement.
6. **Methods** — auto-written and copy-pasteable: tests used, zero handling,
   correction method and family, CI construction, n, software versions.

**Computation is on demand**, behind the existing `ProgressPanel` if it proves
slow. Nothing recomputes a metric; this reads what Results already holds.

---

## 7. Export

- Every table as CSV, PHI-safe by the existing rules.
- Every figure as PNG and PDF at publication resolution.
- The methods paragraph as text.
- Optionally the whole thing as one self-contained HTML report.

---

## 8. Testing

`tests/test_statistics.py` — pure functions, no Qt:

- Median CI against the order-statistic table in §4.1, including `n ≤ 5`
  returning "not estimable".
- Hodges–Lehmann against hand-computed Walsh averages on a small sample.
- **Agreement property:** across randomised samples, the HL interval excludes
  zero if and only if Wilcoxon reports p < 0.05. This is the invariant that
  justifies the pairing, so it is tested directly rather than assumed.
- Holm against a worked example, including the monotonicity a naive
  implementation breaks, and `p_adj ≥ p_raw` always.
- Wilcoxon zero handling: Pratt versus discard on a sample containing ties.
- Rank-biserial sign and bounds.

`tests/test_report_model.py`:

- Coverage accounting distinguishes all three states of §4.5.
- Pairing uses only patients present on both sides; `n_pairs` is correct when
  coverage differs.
- Organ grouping feeds through: two drawers labelled as one organ contribute to
  one row.
- Direction: a metric where lower is better reports "better" for the lower
  median.

`tests/test_report_tab.py` — offscreen: sections populate, an empty results set
degrades to a clear message rather than an exception, export writes files.

---

## 9. Open questions

1. **Reference source** — remembered per session, or chosen each time?
2. **MCID** — worth the UI, or leave the report neutral and let the reader judge
   the CI?
3. **Likert–geometric correlation** — worth a figure, or a table line?
4. **Self-contained HTML export** — wanted, or is CSV plus figures enough?
