# Statistics Decision Register — v3 Report Tab

_AutoSeg Evaluator · for external audit · written 2026-09-17_

Fourteen decisions behind the Report tab, each with the alternative it rejected and the
assumption it rests on. Written to be argued with: every decision carries an ID so it can
be cited, and a statement of what would prompt revisiting it.

Each entry has the same six fields:

| Field | What it is |
|---|---|
| **Rationale** | Why this was chosen. |
| **Rejected** | The most credible alternative, and why it lost. Named even where it is the more conventional choice. |
| **Assumes** | What has to be true for the decision to hold. |
| **Costs** | What is given up. Every decision here gives something up. |
| **Revisit if** | The observation that would prompt changing it. Not a falsification criterion — these are design decisions, not hypotheses — but a stated trigger, so the decision cannot be quietly defended forever. |
| **In code** | Where it lives, and how it is checked. |

| | |
|---|---|
| **Status** | Implemented and under test |
| **Spec** | [`docs/V3_REPORT_TAB_SPEC.md`](V3_REPORT_TAB_SPEC.md) |
| **Code** | [`core/statistics.py`](../src/autoseg_evaluator/core/statistics.py) · [`data/report.py`](../src/autoseg_evaluator/data/report.py) · [`ui/tabs/report.py`](../src/autoseg_evaluator/ui/tabs/report.py) |
| **Tests** | `test_statistics.py` · `test_report_model.py` · `test_report_tab.py` |
| **Typical n** | 10 patients per site |
| **Sources** | 4–7 per cohort |

---

## The question being answered

From peer review of the v2 manuscript:

> "Is the difference observed statistically significant and therefore warrants altering my
> clinical practice guidelines to the team? Having automatic paired non-parametric test of
> significance between the data points would provide me data if we should change our
> practice, or if a larger dataset is required."

Three questions are folded into that sentence, and they need three different outputs.

- **Is the difference real?** — a paired test.
- **Does it matter?** — an effect size with an interval, in the metric's own units. A Dice
  difference of 0.004 can reach p = 0.001 at n = 30 and change nothing clinically.
- **Do I need more data?** — the width of that interval, which is the only one of the three
  a p-value genuinely cannot address.

---

# The register

**"Revisit if" is the field to attack.** It is where each decision states the terms of its
own replacement; a decision with a vague one is a decision that has not been thought through.

---

## D1 — One observation per patient, per organ, per source — where a *case* identifies it

Analysis runs separately within each canonical organ. Organs are never pooled into a single
test, and left/right are separate by default.

Making that hold needs the observation identified properly, and **a patient identifier does
not identify one**. A re-irradiation or a replan gives one patient two planning images and two
sets of structure sets, all carrying the same `PatientID`. The unit is therefore a **case** —
a patient together with the planning CT the contours are drawn on.

Cases are not then analysed as independent observations, because they are not: two courses of
one patient share an anatomy. Where a patient contributes more than one case to an organ,
**that patient is withheld from the comparison** and named in the tab. Choosing between their
courses is a study-design decision, and picking whichever sorted first is not a decision the
software should be making silently.

The case key is the **resolved planning series**, not the `linkage_id` stamped at ingest. That
distinction was settled by measurement, not argument. Linkage unions only on strong reference
tiers and otherwise falls back to Frame of Reference — and vendors get Frame of Reference
wrong. Across this project's own 60 patients in six sites:

| Case key | Patients split into several cases |
|---|---|
| `linkage_id` stamped at ingest | **1** — `Pelvis Male / Prostate4`, wrongly |
| Resolved planning CT series | **0** |

`Prostate4` has one vendor exporting its structure set under a different
`FrameOfReferenceUID` for the same CT. Keyed on the ingest stamp that patient would have been
split in two and then dropped from every comparison involving that vendor — a silent data loss
worse than the problem being fixed. Resolving the planning series reunites all seven structure
sets on the one CT.

- **Rationale** — Within one organ each patient contributes exactly one observation, so the
  independence assumption holds without modelling it. Pooling organs would also produce a
  mean Dice over anatomically unlike structures, which is not a quantity anyone can interpret.
- **Rejected** — *Mixed-effects model with a random intercept per patient.* Correct, and it
  would allow pooling — but it imports distributional assumptions to answer a question that
  per-organ analysis answers assumption-free.
- **Assumes** — Patients are independent of one another, and that one planning image means
  one course. The first is not checkable from the data. The second is checkable and is
  checked: a second planning image is detected rather than assumed away.
- **Costs** — Statistical power, twice over. A mixed model borrows strength across organs;
  this does not, so each organ is tested on ten observations alone. And a patient with two
  courses contributes nothing at all to the organs they overlap on, where a declared rule
  ("use the first course") would keep them.
- **Revisit if** — Cohorts routinely contain re-irradiation, at which point withholding those
  patients costs more than a stated rule for choosing a course would — or organ-level
  conclusions are shown to differ from a mixed model fitted to the same data.

**A pair is one case, not one patient.** An external audit (September 2026) found pairing
joined the two sources on the patient alone, after each had been reduced to one case per
patient — so a vendor assessed only on the first course was paired with another assessed
only on the second, with no exclusion shown. A pair now requires the same planning image
on both sides, and a patient whose sources were assessed on different ones is withheld and
named with the multi-case patients.

**An infinite value is *metric invalid*.** A Hausdorff distance to an empty contour is
infinite. It has no magnitude a shift estimate or its interval can use — inverting the test
over a difference of −∞ never terminated — so the report treats it as it treats NaN: the
case is counted as *metric invalid* in the coverage table and left out of that metric's
statistics. The same contour's Dice, recall and Surface Dice of 0 still count against the
source. Treating it as the worst rank was considered and rejected: the signed-rank p-value
would survive it, but the Hodges–Lehmann estimate and its interval would not.

**Each tolerance is its own metric.** Surface Dice and the 2D APL columns carry their
tolerance in the metric's name (`surface_dice@2mm`), so values computed at different
tolerances are never pooled or compared with each other. The multiplicity note counts the
comparisons in one table; reporting several tolerances of one metric is several tables, and
a primary tolerance should be named in advance.
- **In code** — `ReportModel.observations` is keyed on
  `(organ, source, metric, patient, linkage)`; `multi_case_patients()` finds patients with
  several cases and `values()` omits them. Repeats *within* one case are collapsed, and
  counted separately according to whether they carried the same value.

## D2 — Median [Q1, Q3] is primary; mean (SD) is supplementary

Every descriptive table leads with the median and quartiles. Mean, SD, minimum and maximum
are reported alongside but subordinate.

- **Rationale** — Dice is bounded above at 1 and left-skewed; Hausdorff distance has an
  unbounded right tail where a single failed case moves the mean by more than the rest of the
  cohort combined. The median describes the typical case; the mean describes neither the
  typical case nor the failure.
- **Rejected** — *Mean (SD) as primary.* Familiar, and what most vendor literature reports —
  which is part of why it should not be primary here.
- **Assumes** — Nothing distributional. That is the point.
- **Costs** — Comparability with vendor white papers, which almost universally report mean (SD).
  Both are printed, so the comparison is still possible; only the emphasis differs.
- **Revisit if** — The metrics in use turn out symmetric and light-tailed in practice, making
  the distinction cosmetic.
- **In code** — `statistics.describe()`; the descriptive table leads with median [Q1, Q3].

## D3 — The median's 95% CI comes from order statistics, and none attains 95% below n = 6

The interval is `[x₍k₎, x₍n−k+1₎]` for the largest k with `P(Bin(n, ½) < k) ≤ 0.025`. Below
six observations the report prints an em dash.

The precise claim matters. It is not that no interval can be written down below n = 6 — it
is that **no interval built from the order statistics attains 95% coverage**. The widest one
available is the full range, and its coverage is `1 − 2·2⁻ⁿ`: 0.875 at n = 4, 0.9375 at
n = 5, 0.96875 at n = 6. Five observations cannot reach 95% however they fall, so nothing
is reported rather than something reported at 93.75%.

| n | Interval | Actual coverage |
|---|---|---|
| ≤ 5 | **not estimable** | — |
| 6 | [x₁, x₆] — the full range | 0.969 |
| 10 | [x₂, x₉] | 0.979 |
| 20 | [x₆, x₁₅] | 0.959 |

- **Rationale** — Distribution-free and exact. At n = 10 a bootstrap percentile interval is
  narrower, but its coverage is not the nominal 95% and its narrowness is an artefact of
  resampling ten points.
- **Rejected** — *Bootstrap percentile CI.* Would always return a number, including where no
  valid 95% interval exists — which is worse than returning nothing.
- **Assumes** — Only that observations are exchangeable within the organ.
- **Costs** — At n = 10 the interval spans the 2nd to 9th ordered values. It looks wide. It
  is wide correctly, and should not be tuned. Cohorts of five leave the column empty.
- **Revisit if** — A defensible interval for n ≤ 5 is proposed that does not silently trade
  coverage for the appearance of precision.
- **In code** — `statistics.median_ci()` returns `None` below six observations; both the
  descriptive table and the CSV export print `— not estimable` rather than a number.

## D4 — Wilcoxon signed-rank, paired within patient

Two sources compared with Wilcoxon signed-rank. **Friedman is not implemented**, and an
earlier version of this entry claimed it was the plan for three or more sources.

That plan was wrong for this data. Friedman needs *complete blocks*: a patient counts only
where every source produced the organ. Coverage here is exactly what is not complete — one
vendor declines submandibular glands on six of ten patients, another was never run on two —
so complete blocks would discard most of the cohort, and discard it non-randomly, which is
the bias D10 exists to expose.

What is offered instead is a **source-wise family** (D8): every other source compared with
one reference on one organ, each comparison paired and exact, corrected across the sources.
That answers the same question without requiring any patient to have been contoured by
everybody.

- **Rationale** — Between-patient anatomical variation dwarfs between-vendor performance
  variation, so pairing removes the dominant noise term. At n = 10 an unpaired test on these
  data is close to useless.
- **Rejected** — *Paired t-test.* Not defensible on bounded, skewed, heavy-tailed metrics at
  this sample size.
- **Assumes** — Differences are symmetric about their median. Weaker than normality, but not
  nothing — worth checking on real data.
- **Costs** — Power relative to a t-test when the differences really are normal, and an
  assumption (symmetry) that is weaker than normality but not free — see D14.
- **Revisit if** — The paired differences on real data are strongly asymmetric. The exact
  sign test reported beside every comparison (D14) is the assumption-free fallback. Friedman
  becomes worth revisiting if cohorts arrive where every source contours everything, since
  complete blocks would then cost nothing.
- **In code** — `statistics.signed_rank_exact_p()`, exact for every n the cohort can produce.
  No Friedman implementation exists, deliberately.

## D5 — Zero differences handled by Pratt's method, not discarded

Ties at zero are kept in the ranking rather than dropped, which is **not** the SciPy default.

- **Rationale** — Two sources agreeing exactly on an easy organ is evidence of similarity.
  Discarding those cases removes exactly the evidence that argues against a difference, and
  inflates the apparent effect.
- **Rejected** — *Wilcoxon's original zero-discarding.* The SciPy default, and the more
  commonly reported — which makes stating the choice mandatory.
- **Assumes** — Exact ties are genuine agreement rather than rounding. True for Dice at full
  precision; check for metrics reported to two decimals.
- **Costs** — Pratt's method makes the p-value non-monotone in the shift parameter at exact
  ties, which breaks the clean equivalence between the test and its interval — see D6.
- **Revisit if** — Ties are found to arise from quantisation rather than genuine agreement.
- **In code** — `signed_rank_exact_p(..., zero_method="pratt")`; `n_zero` is reported per row
  so the reader can see how many ties the result rests on.

## D6 — Hodges–Lehmann estimate with its own exact interval

The point estimate is the median of all `n(n+1)/2` Walsh averages `(dᵢ + dⱼ)/2` for `i ≤ j`,
and its interval is derived from the same signed-rank distribution the test uses.

The interval is obtained by **inverting the reported test**: the values of δ that would not
be rejected at α. Three implementation details turned out to matter, all found by testing or
by review rather than by reading:

1. `p(δ)` is a step function that is constant **between** consecutive Walsh averages, not at
   them. Inverting at the grid points produced coverage violations in 4 of 400 randomised
   samples, because zero can sit in an accepted gap between two rejected grid points.
2. The breakpoints are **not** interchangeable with the gaps beside them. At a Walsh average
   some residuals become exactly zero, which under Pratt (D5) changes the ranks and so the
   null distribution; a breakpoint can be accepted while both neighbouring gaps are rejected.
   Both are probed.
3. Under Pratt, `p(δ)` is not monotone across exact ties, so the acceptance region need not be
   an interval at all.

Because of (3) the set is **enumerated, not bracketed**, wherever that is affordable — up to
200 distinct Walsh averages, about 12 ms at ten pairs, so every sample size this tool sees.
An earlier version bisected on the premise that `p` is unimodal in δ, which holds without
exact ties and is unproved with them. Bisection survives above the threshold and is flagged
where used.

Removing that assumption changed no answer. Measured across **8,357 randomised samples**
spanning six difference distributions, from continuous to heavily quantised, the full scan and
the bisection agreed everywhere and **no acceptance set was ever disconnected**. This is
insurance against a case not yet seen, not the repair of a wrong number.

**What the set can be.** A bare "not estimable" merged four situations, of which three are
well defined and should be explained instead of hidden:

| Status | Meaning | Shown as |
|---|---|---|
| `interval` | Bounded and connected — the ordinary case | `-0.0380, -0.0340` |
| `singleton` | Exactly one accepted shift | `+0.1000 only` |
| `unbounded` | No shift is rejectable at this n | `— unbounded at this n` |
| `disconnected` | Accepted region has gaps; the enclosing interval is reported and is conservative | `-0.02, +0.03 (enclosing)` |
| `no data` | Nothing to invert | `— not estimable` |

The singleton is **sample-size dependent**, which an earlier draft of this entry got wrong by
stating it unconditionally. With every difference identical, shifting by any amount makes all
residuals non-zero and unanimous, so `p` away from the common value is `2¹⁻ⁿ`. From six
observations that is 0.03125 and rejects, leaving one accepted point; at five it is 0.0625 and
rejects nothing, so the same data give an unbounded set.

- **Rationale** — This is the location estimate the Wilcoxon test is built around, so the
  interval and the p-value agree by construction wherever no paired difference is exactly
  zero. Pair a plain median-of-differences with a bootstrap interval instead and the report
  will eventually print p = 0.03 beside an interval crossing zero, with no principled account
  of which to believe.
- **Rejected** — *Median of paired differences + bootstrap CI.* Defensible in isolation,
  incoherent in combination with the test being reported next to it.
- **Assumes** — Same symmetry assumption as D4 — they are the same procedure.
- **Costs** — The agreement is not unconditional, and saying it were would be the easy
  overclaim. With exact ties present it can break, so every row carries a
  `ci_agrees_with_test` flag and the tab warns when any row in the family has one. The full
  scan also costs a p-value per breakpoint and per gap, which is why it has a ceiling.
- **Revisit if** — Exact ties prove common enough in real cohorts that the flag fires
  routinely, at which point the zero-handling choice in D5 is what should be reconsidered; or
  cohorts grow past the scan ceiling often enough that bisection becomes the normal path.
- **In code** — `statistics.confidence_set()` returning a `ConfidenceSet`;
  `hodges_lehmann_ci()` is now a thin wrapper for callers wanting only two numbers.
  `data/report.interval_text()` renders it, and is shared with the worked examples below so
  the published table cannot drift from what the tab prints. Verified by a property test over
  randomised samples: **0 violations in 770 samples with no exact ties**, and scan-versus-
  bisection agreement over the 8,357 described above.

## D7 — Rank-biserial correlation as the standardised effect size

`r = (W⁺ − W⁻) / (W⁺ + W⁻)`, bounded in [−1, 1], reported beside the unstandardised
difference rather than instead of it.

- **Rationale** — Lets Dice and Hausdorff be compared on one axis. The unstandardised
  difference stays primary because it is the one a clinician can judge.
- **Rejected** — *Cliff's delta.* Near-equivalent; rank-biserial falls directly out of the
  statistic already computed.
- **Assumes** — Nothing beyond the test itself.
- **Costs** — No conventional small/medium/large thresholds are printed. Importing
  Cohen-style cut-offs would invent clinical meaning the statistic does not carry, so the
  reader gets a number without a label for it.
- **Revisit if** — A radiotherapy-specific convention for interpreting rank effect sizes on
  contour metrics is established in the literature.
- **In code** — `statistics.rank_biserial()`. Note the ceiling: whenever every paired
  difference has the same sign, `r = ±1.00` exactly, which at n = 4 is the *only* value
  compatible with the smallest attainable p. An r of ±1.00 beside a large p is arithmetic,
  not a contradiction.

## D8 — No multiplicity correction; each organ is reported as its own question

Every comparison answers one question — *for this organ and this metric, is there evidence
that these two sources differ?* — and its p-value is reported **unadjusted**. Two organs are
two studies that happen to share a screen, and a question is not made harder to answer by
another question being asked beside it.

**This reverses an earlier decision, and the reason is worth recording.** The tab previously
applied Holm across whichever organs were selected in the family list. A user found the
consequence in ordinary use: with one organ selected a comparison was significant, and
selecting more organs took the significance away — on identical data. The divisor was a
**view setting**.

That is worse than not correcting at all. A correction that can be dialled by a list widget
invites precisely the selection it exists to prevent, and lends it the appearance of rigour.
The choice was never really between corrected and uncorrected; it was between a correction
whose family was fixed in advance and one whose family was whatever happened to be on screen.
Only the first is honest, and this software cannot enforce it, because it cannot know what was
declared before the data were seen.

- **Rationale** — The analysis this tool exists for is per-organ: *is vendor A better than
  vendor B for the parotid?* then, separately, *for the brainstem?* Correcting across organs
  answers a different question — *does vendor A differ from vendor B anywhere among these
  organs?* — which nobody asked.
- **Rejected** — *Holm across a declared family.* Correct where the family genuinely is
  declared in advance and reported whole. Rejected because nothing here can verify that it
  was, and the failed implementation showed what happens when it is not.
- **Rejected** — *Benjamini–Hochberg FDR.* Same objection: the family is still whatever is
  selected.
- **Assumes** — That every comparison is reported, not only the ones below 0.05. This is the
  entire safeguard, and it is the reader's to keep.
- **Costs** — Scanning many organs for the significant ones inflates the error rate, and
  nothing in the p-values accounts for it.
- **Revisit if** — A confirmatory analysis is needed with a family registered in advance, at
  which point `holm()` and `with_holm()` are still in the statistics core and still tested.
- **In code** — `ReportModel._correct()` is deliberately a no-op that documents why;
  `PairedResult.p_adjusted` stays `None` throughout the report.

**What multiplicity costs is stated rather than applied.** The tab reports how many of the
displayed comparisons would be expected below 0.05 by chance alone —
`expected_false_positives(n, α) = n × α` — so a reader scanning for significant rows knows
what that scan costs. Across five organs that is 0.25 rows; across twenty it is one. Stating
the number leaves the judgement with the reader instead of silently enlarging every p-value.

## D9 — No post-hoc power; interval width answers "do I need more data"

- **Rationale** — Observed power is a monotone function of the observed p-value and carries
  no information the p-value did not already carry. An interval from −0.02 to +0.03 excludes
  anything clinically meaningful; one from −0.15 to +0.20 does not. Only the second means
  "collect more".
- **Rejected** — *Post-hoc power calculation.* What the reviewer's phrasing most directly
  suggests, and a known statistical anti-pattern.
- **Assumes** — Readers will interpret an interval. Mitigated by stating the verdict in words
  alongside it.
- **Costs** — An interval demands more of the reader than a single number, and some readers
  will want the number anyway.
- **Revisit if** — Clinicians supply a minimum clinically important difference, which would
  let each result be classified directly. Still unresolved; see questions below.
- **In code** — No power calculation exists anywhere in the module, deliberately.

## D10 — Coverage is a result, and its effect on pairing is surfaced

Three distinct states are distinguished: produced, declined, and never ran. Each comparison
reports `n_pairs` against each source's own n.

- **Rationale** — A paired test silently uses only patients both sources contoured. If one
  source covers 10 and the other 6, the comparison runs on 6 — and that subset is unlikely to
  be random, because models fail on hard cases. A source is otherwise rewarded for declining
  the difficult patients.
- **Rejected** — *Reporting n only.* Hides the selection entirely.
- **Assumes** — Absence is informative. A vendor not run on a patient is different from one
  that ran and produced nothing; the matrix separates them.
- **Costs** — The warning can be ignored. A reader determined to quote the estimate from a
  four-of-ten comparison is not prevented from doing so.
- **Revisit if** — Reports are found in circulation quoting low-coverage comparisons without
  the caveat, which would argue for refusing rather than warning.
- **In code** — `CoverageCell` separates `produced` / `not_produced` / `source_absent` /
  `metric_invalid`; `PairedResult` carries `n_a`, `n_b` and `coverage_fraction`; the tab warns
  below 80% coverage.

## D11 — Violin plots with overlaid points, never bare box plots

- **Rationale** — Auto-contouring Dice is frequently bimodal: the model finds the organ
  (≈ 0.85) or misses it entirely (≈ 0.1). A box plot renders that as a wide box with a median
  near 0.6, describing a case that never occurred. The violin shows two lobes, which is a
  different clinical message.
- **Rejected** — *Box plot.* Conventional, and what the current external scripts produce.
- **Assumes** — A kernel density estimate at n = 10 is interpretable. It largely is not —
  which is why individual points are always overlaid and the violin is treated as
  interpretation rather than evidence.
- **Costs** — Below fifteen observations no violin is drawn at all, so the figure is plainer
  than the equivalent from a vendor report.
- **Revisit if** — Distributions prove consistently unimodal, making the violin ornament.
- **In code** — `stat_plots.MIN_N_FOR_VIOLIN = 15`; points, median and IQR are always drawn,
  the density only above that threshold.

## D12 — Forest plot against one user-chosen reference source

- **Rationale** — Five sources give ten pairwise comparisons, which nobody reads. A reference
  reduces it to four and frames the actual clinical question: *should we move off what we
  currently use?* Direction ("favours X") is computed from the metric, never left to the reader.
- **Rejected** — *All-pairs matrix.* Complete, unreadable, and invites the reader to find the
  one comparison that suits them.
- **Assumes** — A meaningful reference exists, normally the vendor in current clinical use.
- **Costs** — Choosing the reference after seeing results is a form of selection, and one the
  software cannot detect.
- **Revisit if** — Reference choice is observed to shift between drafts of the same analysis.
- **In code** — The methods paragraph names the reference, the challenger, the metric and the
  family size, so the choice travels with any text copied out of the tab.

## D13 — When the design cannot reach significance, say so before showing p-values

If the smallest attainable p at the available sample size exceeds α, no comparison can be
significant however the data fall. The tab says this above the table rather than letting a
column of large p-values be read as evidence of agreement.

Uncorrected, the bound is arithmetic and has nothing to do with how many organs are shown:

| Paired patients | Smallest attainable two-sided p | Can reach 0.05? |
|---|---|---|
| 4 | 0.125 | no |
| 5 | 0.0625 | no |
| **6** | **0.03125** | **yes** |
| 10 | 0.001953 | yes |

So **six paired patients** is the floor. Five or fewer cannot produce a significant result
whatever the contours look like, and their large p-values are not evidence of similarity.

- **Rationale** — A property of the design, knowable before the data are seen, and the honest
  answer to "why is nothing significant?". Without it an under-powered comparison is
  indistinguishable on screen from a genuine null.
- **Rejected** — *Silently reporting the p-values.* Formally correct and routinely misread.
- **Assumes** — Nothing. The bound is arithmetic.
- **Costs** — The warning says a comparison cannot detect anything, not how many more
  patients would be needed. That is a power calculation, which D9 rejects.
- **Revisit if** — Cohorts grow past the point where the floor can bind on any organ.
- **In code** — `statistics.holm_detection_ceiling(n, 1, alpha)`, surfaced by
  `family_can_detect()`. Judged on the table's **most favourable** row, not its thinnest —
  otherwise one four-patient organ would announce that nothing on screen could reach
  significance while a ten-patient organ sat two rows below at p = 0.002. Rows individually
  below the floor are counted separately in the banner.

## D14 — An exact sign test is reported beside every comparison

Every row shows the signed-rank result and, alongside it, `k/m · p` from an exact binomial
sign test on the same pairs.

- **Rationale** — D4 assumes the paired differences are symmetric about their median. That
  assumption is untestable at n = 10, so rather than assert it, the analysis that does not
  need it is printed next to the one that does. Where they agree, the symmetry assumption is
  not doing any work. Where they diverge, the reader knows the conclusion rests on it.
- **Rejected** — *Reporting a symmetry diagnostic.* At ten observations a skewness test has
  no power, and would give false reassurance rather than information.
- **Assumes** — Only that the pairs are independent.
- **Costs** — A second p-value per row, which invites reporting whichever is smaller. The
  signed-rank result is the declared primary analysis and the sign test is labelled as a
  robustness check, in that order, in the table and in the methods paragraph.
- **Revisit if** — The two are found to diverge often enough that the primary test should
  change rather than be accompanied.
- **In code** — `statistics.sign_test()`, exact, reported as `n_positive/n_nonzero · p`.

---

# What the report looks like

> Figures below use **synthetic data**, not measured results. Every number is produced by
> the shipped implementation, via `scripts/make_register_tables.py` — the tables are internally
> consistent and an auditor can recompute any cell. An earlier draft hand-wrote plausible
> rows and drifted into combinations that cannot occur: an interval at n = 4, and r = 0.80
> beside p = 0.125 when p = 0.125 at n = 4 forces r = ±1.00 exactly.

## Descriptive statistics · Dice

| Organ | Source | n | Median [Q1, Q3] | 95% CI (median) | Mean (SD) | Min / Max |
|---|---|---|---|---|---|---|
| Parotid (L) | Limbus | 10 | 0.839 [0.820, 0.860] | 0.811 – 0.868 | 0.840 (0.027) | 0.799 / 0.881 |
| Parotid (L) | MVision | 10 | 0.802 [0.783, 0.823] | 0.779 – 0.834 | 0.804 (0.026) | 0.766 / 0.845 |
| Brainstem | Limbus | 9 | 0.864 [0.850, 0.879] | 0.845 – 0.889 | 0.865 (0.023) | 0.828 / 0.902 |
| Brainstem | MVision | 9 | 0.866 [0.851, 0.879] | 0.838 – 0.884 | 0.862 (0.024) | 0.819 / 0.897 |
| Glnd Submand (R) | Limbus | 10 | 0.818 [0.804, 0.831] | 0.796 – 0.840 | 0.816 (0.021) | 0.779 / 0.844 |
| Glnd Submand (R) | MVision | 4 | 0.768 [0.759, 0.781] | **— not estimable** | 0.772 (0.022) | 0.751 / 0.802 |

The `Glnd Submand (R)` rows show **D3** and **D6** together. The challenger produced this
organ for four of ten patients; at n = 4 neither the median's interval nor the
Hodges–Lehmann interval attains 95%, so neither is drawn. The comparison names *which*
absence it is — `— unbounded at this n`, meaning no shift is rejectable rather than that
the data were missing. Quartiles are still shown, because they are descriptive rather
than inferential.

## Paired comparison · Dice · MVision vs Limbus  (5 organs, each its own question)

| Organ | n pairs | n chall. / n ref. | HL difference | 95% CI (unadjusted) | r | zeros | p | Sign test | Reading |
|---|---|---|---|---|---|---|---|---|---|
| Parotid (L) | 10 | 10 / 10 | -0.0360 | -0.0380, -0.0340 | -1.00 | 0 | 0.0020 | 0/10 · p=0.002 | favours Limbus |
| Parotid (R) | 10 | 10 / 10 | -0.0105 | -0.0175, -0.0010 | -0.71 | 0 | 0.0488 | 2/10 · p=0.109 | favours Limbus |
| Brainstem | 9 | 9 / 9 | -0.0025 | -0.0080, +0.0000 | -0.60 | 0 | 0.1172 | 3/9 · p=0.508 | no detectable difference |
| SpinalCord | 10 | 10 / 10 | +0.0025 | -0.0040, +0.0090 | +0.56 | 0 | 0.1289 | 6/10 · p=0.754 | no detectable difference |
| Glnd Submand (R) | 4 | 4 / 10 | -0.0453 | **— unbounded at this n** | -1.00 | 0 | 0.1250 | 0/4 · p=0.125 | no detectable difference |

Four things in that table are worth reading carefully.

- **Parotid (R)** — p = 0.0488, just under the line. Reported as it stands, because this
  organ is its own question (**D8**); an earlier version corrected it to 0.195 on the strength
  of four other organs being on screen, and to 0.049 when they were not. Read it as what it
  is: a marginal result on ten patients, worth a larger cohort rather than a conclusion.
- **Glnd Submand (R)** — the largest effect in the table, on the fewest patients, with no
  interval. The challenger declined this organ for six of ten patients and is compared only on
  the four it attempted — very likely the four it found easiest (**D10**).
- **r = −1.00 on two rows** — every paired difference has the same sign, which forces the
  rank-biserial correlation to its bound. Beside p = 0.002 that is a strong result; beside
  p = 0.125 at n = 4 it is the *only* value arithmetically available (**D7**).
- **The Reading column never says "no difference"** — a large p means no difference was
  detected, which is not the same claim and would need a margin nobody has supplied.

## Forest · Dice · MVision vs Limbus (reference)

```
                      ← favours Limbus                favours MVision →

  Glnd Submand (R)   n=4       ○                                            │            -0.0453   p 0.1250   no interval at n=4
  Parotid (L)        n=10             ├─●─┤                                 │            -0.0360   p 0.0020
  Parotid (R)        n=10                                 ├──────●─────────┤│            -0.0105   p 0.0488
  Brainstem          n=9                                            ├─────○─┤            -0.0025   p 0.1172
  SpinalCord         n=10                                               ├─────○──────┤   +0.0025   p 0.1289

                         -0.05   -0.04     -0.03     -0.02     -0.01     +0.00     +0.01
                         Hodges–Lehmann difference in Dice   (MVision − Limbus)

  ● p <= 0.05 for that organ alone        ○ not significant
```

Rows sorted by effect size. A filled marker means p ≤ 0.05 **for that organ alone**, and the
interval comes from inverting that same test, so marker and interval agree by construction.
Nothing about a row changes because another row is on screen — which was not true of the
Holm-corrected version this replaced (**D8**).

## Coverage · organs × sources

| Organ | Limbus | MVision | Radformation | TheraPanacea |
|---|---|---|---|---|
| Parotid (L) | 10 / 10 | 10 / 10 | 10 / 10 | 10 / 10 |
| Brainstem | 10 / 10 | 9 / 10 | 10 / 10 | 8 / 8 · 2 not run |
| Glnd Submand (R) | 10 / 10 | **4 / 10** | 10 / 10 | — |
| Cochlea (L) | 10 / 10 | — | 7 / 10 | — |

Three states, deliberately distinguished:

- `4 / 10` — a model that ran and **declined to contour**
- `8 / 8 · 2 not run` — **missing data**, the model was not run on those patients
- `—` — an organ the source **never produces at all**

Only the first is a performance finding. The cell text above is what the tab renders verbatim;
`test_report_tab.py` asserts these exact strings, so the three states cannot quietly collapse
into one.

---

# Deliberately not done

| Excluded | Why |
|---|---|
| **Pooling organs into one test** | Breaks one-observation-per-patient and produces a mean over anatomically unlike structures. Left/right pooling is available as an opt-in that warns, since it doubles n at the cost of independence. |
| **Cross-prescription DVH comparison** | D95 in Gy is not comparable between a 70 Gy and a 54 Gy plan. Normalisation would be required first and is not yet specified. |
| **Post-hoc power** | Carries no information beyond the p-value. The interval answers the question the reviewer actually asked. |
| **Effect-size thresholds** | No small/medium/large labels. Importing generic cut-offs would attach clinical meaning to a rank statistic that does not carry it. |

---

# Unresolved, and most open to challenge

1. **Is reporting unadjusted p-values right?** It is defensible only because every
   comparison is shown and each is its own question (D8). A reviewer could reasonably argue
   that a reader scanning twenty organs for the significant ones is running a family whether
   or not the software says so. The counter-argument is that a correction whose family is a
   view setting — which is what was tried — is worse than none, and that the expected count
   of chance findings is stated instead. **This is the decision most open to challenge.**
2. **Should a clinically important difference be user-supplied?** It would let each result be
   classified as meaningful, trivial, equivalent, or inconclusive — answering the practice
   question directly, at the cost of asking clinicians for a number they may not have.
3. **Should low coverage block a comparison rather than warn it?** Currently the estimate is
   shown and flagged (D10). Refusing below some ratio would be safer and less informative.
4. **Is Wilcoxon's symmetry assumption met?** Untestable at n = 10. Addressed rather than
   resolved: the exact sign test is reported beside every comparison (D14), so a reader can
   see whether the conclusion depends on the assumption.
5. **Does the reference-source choice need recording?** Partly resolved — the methods
   paragraph names it (D12) — but nothing prevents choosing it after seeing results.
6. **Should the organ list be recorded with the results?** Nothing now depends on it
   statistically (D8), but it still says which comparisons were looked at, and that is what a
   reader needs in order to judge the expected count of chance findings.

---

_AutoSeg Evaluator v3 · statistical design for the Report tab · implemented and under test._
_All tables and figures on this page are computed from synthetic data and do not represent_
_measured results._
