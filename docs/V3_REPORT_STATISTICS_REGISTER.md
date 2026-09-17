# Statistics Decision Register — v3 Report Tab

_AutoSeg Evaluator · for external audit · written 2026-09-17_

Twelve decisions behind the proposed Report tab, each with the alternative it rejected
and the assumption it rests on. Written to be argued with: every decision carries an ID
so it can be cited, and a note on what evidence would overturn it.

| | |
|---|---|
| **Status** | Agreed design, not implemented |
| **Spec** | [`docs/V3_REPORT_TAB_SPEC.md`](V3_REPORT_TAB_SPEC.md) |
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

Each decision states what was chosen, why, what was rejected, what it assumes, and what
would falsify it. **The last field is the one to attack.**

---

## D1 — One observation per patient, per organ, per source

Analysis runs separately within each canonical organ. Organs are never pooled into a single
test, and left/right are separate by default.

- **Rationale** — Within one organ each patient contributes exactly one observation, so the
  independence assumption holds without modelling it. Pooling organs would also produce a
  mean Dice over anatomically unlike structures, which is not a quantity anyone can interpret.
- **Rejected** — *Mixed-effects model with a random intercept per patient.* Correct, and it
  would allow pooling — but it imports distributional assumptions to answer a question that
  per-organ analysis answers assumption-free.
- **Assumes** — Patients are independent of one another. Fails if the cohort contains repeat
  scans of the same patient.
- **Falsified by** — A cohort where organ-level conclusions differ from a mixed model fitted
  to the same data.

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
- **Falsified by** — Metrics that turn out symmetric and light-tailed in practice, making the
  distinction cosmetic.

## D3 — The median's 95% CI comes from order statistics, and does not exist below n = 6

The interval is `[x₍k₎, x₍n−k+1₎]` for the largest k with `P(Bin(n, ½) < k) ≤ 0.025`. Below
six observations the report prints an em dash.

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
- **Consequence** — At n = 10 the interval spans the 2nd to 9th ordered values. It looks
  wide. It is wide correctly, and should not be tuned.

## D4 — Wilcoxon signed-rank, paired within patient

Two sources compared with Wilcoxon signed-rank; three or more with Friedman on complete
blocks, followed by pairwise post-hoc.

- **Rationale** — Between-patient anatomical variation dwarfs between-vendor performance
  variation, so pairing removes the dominant noise term. At n = 10 an unpaired test on these
  data is close to useless.
- **Rejected** — *Paired t-test.* Not defensible on bounded, skewed, heavy-tailed metrics at
  this sample size.
- **Assumes** — Differences are symmetric about their median. Weaker than normality, but not
  nothing — worth checking on real data.
- **Falsified by** — Strongly asymmetric paired differences, which would argue for a sign
  test instead.

## D5 — Zero differences handled by Pratt's method, not discarded

Ties at zero are kept in the ranking rather than dropped, which is **not** the SciPy default.

- **Rationale** — Two sources agreeing exactly on an easy organ is evidence of similarity.
  Discarding those cases removes exactly the evidence that argues against a difference, and
  inflates the apparent effect.
- **Rejected** — *Wilcoxon's original zero-discarding.* The SciPy default, and the more
  commonly reported — which makes stating the choice mandatory.
- **Assumes** — Exact ties are genuine agreement rather than rounding. True for Dice at full
  precision; check for metrics reported to two decimals.
- **Falsified by** — Ties arising from quantisation rather than agreement.

## D6 — Hodges–Lehmann estimate with its own exact interval

The point estimate is the median of all `n(n+1)/2` Walsh averages `(dᵢ + dⱼ)/2` for `i ≤ j`,
and its interval is derived from the same signed-rank distribution the test uses.

- **Rationale** — This is the location estimate the Wilcoxon test is built around, so the
  interval and the p-value are guaranteed to agree. Pair a plain median-of-differences with a
  bootstrap interval instead and the report will eventually print p = 0.03 beside an interval
  crossing zero.
- **Rejected** — *Median of paired differences + bootstrap CI.* Defensible in isolation,
  incoherent in combination with the test being reported next to it.
- **Assumes** — Same symmetry assumption as D4 — they are the same procedure.
- **Verified by** — A property test asserting that, across randomised samples, the interval
  excludes zero if and only if the test reports p < 0.05.

## D7 — Rank-biserial correlation as the standardised effect size

`r = (W⁺ − W⁻) / (W⁺ + W⁻)`, bounded in [−1, 1], reported beside the unstandardised
difference rather than instead of it.

- **Rationale** — Lets Dice and Hausdorff be compared on one axis. The unstandardised
  difference stays primary because it is the one a clinician can judge.
- **Rejected** — *Cliff's delta.* Near-equivalent; rank-biserial falls directly out of the
  statistic already computed.
- **Assumes** — Nothing beyond the test itself.
- **Caveat** — No conventional small/medium/large thresholds are printed. Importing
  Cohen-style cut-offs would invent clinical meaning the statistic does not carry.

## D8 — Holm correction, applied per metric per source-pair across organs

Both raw and adjusted p are shown. The family definition is printed beside the table.

- **Rationale** — That family is the set read as one question: *"across these organs, where
  does vendor X differ from vendor Y on Dice?"*. Extending it across metrics would be badly
  over-conservative, since Dice, surface-DSC and mean surface distance on the same contours
  are strongly correlated and Holm assumes nothing about dependence.
- **Rejected** — *Benjamini–Hochberg FDR.* More powerful and common in this literature. Holm
  controls the familywise error rate, which is the stricter claim and the more appropriate one
  when a single organ's result could change practice.
- **Assumes** — The family is the right one. **This is a judgement, not a fact, and is the
  decision most open to challenge.**
- **Falsified by** — An argument that the whole report is one family, which would make the
  correction far harsher and is defensible.

## D9 — No post-hoc power; interval width answers "do I need more data"

- **Rationale** — Observed power is a monotone function of the observed p-value and carries
  no information the p-value did not already carry. An interval from −0.02 to +0.03 excludes
  anything clinically meaningful; one from −0.15 to +0.20 does not. Only the second means
  "collect more".
- **Rejected** — *Post-hoc power calculation.* What the reviewer's phrasing most directly
  suggests, and a known statistical anti-pattern.
- **Assumes** — Readers will interpret an interval. Mitigated by stating the verdict in words
  alongside it.
- **Open** — Whether to go further and classify each result against a user-supplied
  clinically important difference. Unresolved; see questions below.

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
- **Unresolved** — Whether to refuse the comparison below some coverage ratio, rather than warn.

## D11 — Violin plots with overlaid points, never bare box plots

- **Rationale** — Auto-contouring Dice is frequently bimodal: the model finds the organ
  (≈ 0.85) or misses it entirely (≈ 0.1). A box plot renders that as a wide box with a median
  near 0.6, describing a case that never occurred. The violin shows two lobes, which is a
  different clinical message.
- **Rejected** — *Box plot.* Conventional, and what the current external scripts produce.
- **Assumes** — A kernel density estimate at n = 10 is interpretable. It largely is not —
  which is why individual points are always overlaid and the violin is treated as
  interpretation rather than evidence.
- **Falsified by** — Consistently unimodal distributions, making the violin ornament.

## D12 — Forest plot against one user-chosen reference source

- **Rationale** — Five sources give ten pairwise comparisons, which nobody reads. A reference
  reduces it to four and frames the actual clinical question: *should we move off what we
  currently use?* Direction ("favours X") is computed from the metric, never left to the reader.
- **Rejected** — *All-pairs matrix.* Complete, unreadable, and invites the reader to find the
  one comparison that suits them.
- **Assumes** — A meaningful reference exists, normally the vendor in current clinical use.
- **Caveat** — Choosing the reference after seeing results is a form of selection. The report
  should record which reference was chosen.

---

# What the report looks like

> **All tables below are illustrative and do not represent measured results.**

## Descriptive statistics · Dice

| Organ | Source | n | Median [Q1, Q3] | 95% CI (median) | Mean (SD) | Min / Max |
|---|---|---|---|---|---|---|
| Parotid (L) | Limbus | 10 | 0.842 [0.811, 0.868] | 0.798 – 0.879 | 0.833 (0.041) | 0.742 / 0.881 |
| Parotid (L) | MVision | 10 | 0.806 [0.779, 0.834] | 0.761 – 0.849 | 0.799 (0.048) | 0.698 / 0.862 |
| Brainstem | Limbus | 9 | 0.871 [0.850, 0.889] | 0.828 – 0.901 | 0.864 (0.033) | 0.799 / 0.902 |
| Glnd Submand (R) | MVision | 4 | 0.774 [0.751, 0.802] | **— not estimable** | 0.778 (0.036) | 0.740 / 0.821 |

The fourth row shows **D3** in operation: at n = 4 no distribution-free 95% interval exists,
so none is invented. Quartiles are still shown because they are descriptive rather than
inferential.

## Paired comparison · Dice · MVision vs Limbus

_Holm family = Dice × this pair × 14 organs_

| Organ | n pairs | nA / nB | HL difference | 95% CI | r | p | p Holm | Verdict |
|---|---|---|---|---|---|---|---|---|
| Parotid (L) | 10 | 10 / 10 | +0.038 | +0.014, +0.061 | 0.82 | 0.002 | **0.028** | favours Limbus |
| Parotid (R) | 10 | 10 / 10 | +0.031 | +0.004, +0.058 | 0.71 | 0.014 | 0.168 | not significant |
| Brainstem | 9 | 9 / 10 | +0.009 | −0.011, +0.028 | 0.29 | 0.380 | 1.000 | no difference |
| Glnd Submand (R) | 4 | 10 / 4 | +0.061 | −0.008, +0.130 | 0.80 | 0.125 | 1.000 | **coverage 40%** |

**D10** in operation on the last row: one source produced this organ for four of ten patients,
so the comparison rests on those four — very likely the easier four. The estimate is shown,
flagged, and should not be read as a performance comparison.

Note also **Parotid (R)**: raw p = 0.014 would be "significant" and Holm correction removes it.
That is the multiplicity control doing its job across 14 organs.

## Forest · Dice · vendors vs Limbus (reference)

```
                              ← favours MVision    0    favours Limbus →

  Parotid (L)      n=10                            │    ├────●────┤          +0.038  p_holm 0.028
  Parotid (R)      n=10                            ├──────○──────┤           +0.031         0.168
  Brainstem         n=9                      ├─────○─────┤                   +0.009         1.000
  SpinalCord       n=10          ├───────────○───────────┤                   −0.012         1.000
  Glnd Submand (R)  n=4                ├──────────○──────────────────┤       +0.061  coverage 40%

                   −0.04     −0.02       0      +0.02     +0.04     +0.06
                            Hodges–Lehmann difference in Dice

  ● significant after Holm correction        ○ not significant
```

Rows sorted by effect size so the actionable organs sit at the top.

## Coverage · organs × sources

| Organ | Limbus | MVision | Radformation | TheraPanacea |
|---|---|---|---|---|
| Parotid (L) | 10 / 10 | 10 / 10 | 10 / 10 | 10 / 10 |
| Brainstem | 10 / 10 | 9 / 10 | 10 / 10 | 8 / 8 *(2 absent)* |
| Glnd Submand (R) | 10 / 10 | **4 / 10** | 10 / 10 | — |
| Cochlea (L) | 10 / 10 | — | 7 / 10 | — |

Three states, deliberately distinguished:

- `4 / 10` — a model that ran and **declined to contour**
- `8 / 8 · 2 absent` — **missing data**, the model was not run on those patients
- `—` — an organ the source **never produces at all**

Only the first is a performance finding.

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

1. **Is the Holm family right?** Correcting per metric per source-pair across organs is a
   judgement (D8). A reviewer could reasonably argue the whole report is one family, which
   would be substantially harsher.
2. **Should a clinically important difference be user-supplied?** It would let each result be
   classified as meaningful, trivial, equivalent, or inconclusive — answering the practice
   question directly, at the cost of asking clinicians for a number they may not have.
3. **Should low coverage block a comparison rather than warn it?** Currently the estimate is
   shown and flagged (D10). Refusing below some ratio would be safer and less informative.
4. **Is Wilcoxon's symmetry assumption met?** Worth checking empirically on the paired
   differences before the design is fixed; a sign test is the fallback.
5. **Does the reference-source choice need recording?** Selecting it after seeing results is a
   mild form of selection, currently mitigated only by convention (D12).

---

_AutoSeg Evaluator v3 · statistical design for the Report tab · not yet implemented._
_All tables and figures on this page are illustrative and do not represent measured results._
