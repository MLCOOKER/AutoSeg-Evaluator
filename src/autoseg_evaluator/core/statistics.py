"""Paired non-parametric statistics for the Report tab.

Pure functions on sequences of floats — no Qt, no I/O — so every result here can
be checked against a hand-computed value.

The headline test is the **Wilcoxon signed-rank test**, which is what the
auto-segmentation literature uses for paired method comparison and what a
reviewer asking for "a paired non-parametric test" means. An exact **sign test**
is computed alongside it, always, never as an alternative to choose between: the
two answer different questions — magnitude versus direction — and showing both
removes any opportunity to pick one after seeing the result.

Three things here exist because a statistical review found them missing:

*The confidence interval is obtained by inverting the test that is reported next
to it*, using the same zero convention, rather than by the textbook Walsh-average
formula which assumes no zeros or ties. So the interval excludes zero exactly
when the test rejects — verified over several hundred randomised samples.

The one exception is worth stating rather than burying. When a paired difference
is *exactly* zero, δ = 0 is itself a Walsh average, and under Pratt the p-value
spikes there: shifting by any ε turns those zeros into non-zeros all pointing the
same way. The acceptance set is then genuinely not an interval, and an interval
cannot represent it faithfully. Every result therefore carries
:attr:`PairedResult.ci_agrees_with_test`, checked directly rather than assumed.
For continuous metrics at full precision this does not arise; it appears when two
sources produce identical contours.

*An interval that does not exist is reported as absent.* Below six observations
no distribution-free median CI with order-statistic endpoints reaches 95%
coverage, and at four paired observations no shift can be rejected at all, so
inverting the test yields an unbounded set. Both return ``None`` rather than a
narrower number carrying a 95% label.

*The detection ceiling is computable and is reported.* At n = 10 the smallest
attainable two-sided p is 0.001953, so a Holm family of 26 or more can reject
nothing whatever the data show. A report that prints a column of 1.000 without
saying this invites the reader to conclude the sources are equivalent.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import Enum

import numpy as np
from scipy.stats import binom

# ---- Descriptive ----------------------------------------------------------


@dataclass(frozen=True)
class Description:
    """Descriptive summary of one (organ, source, metric) sample."""

    n: int
    median: float
    q1: float
    q3: float
    ci_low: float | None
    ci_high: float | None
    mean: float
    sd: float
    minimum: float
    maximum: float

    @property
    def ci_available(self) -> bool:
        return self.ci_low is not None and self.ci_high is not None


#: Fewest observations admitting a distribution-free 95% median CI whose
#: endpoints are order statistics. Below this the interval is *unavailable by
#: this method*, which is not the same as no interval existing — the known
#: support of a bounded metric always gives a trivial one.
MIN_N_FOR_MEDIAN_CI = 6


def median_ci(values: Sequence[float], alpha: float = 0.05) -> tuple[float, float] | None:
    """Distribution-free CI for the population median, from order statistics.

    The interval is ``[x₍k₎, x₍n−k+1₎]`` for the largest k with
    ``P(Bin(n, ½) < k) ≤ α/2``; its coverage is ``1 − 2·P(Bin(n, ½) < k)``.
    Returns ``None`` when no such k exists, which happens for n < 6 at the
    conventional 95% level.

    Assumes observations are **independent** draws from the population.
    Exchangeability alone is not enough — dependent observations can be
    exchangeable and break the binomial calculation.
    """
    data = sorted(float(v) for v in values if v is not None and not math.isnan(v))
    n = len(data)
    if n == 0:
        return None
    best_k = None
    for k in range(1, n // 2 + 2):
        if binom.cdf(k - 1, n, 0.5) <= alpha / 2:
            best_k = k
        else:
            break
    if best_k is None:
        return None
    return data[best_k - 1], data[n - best_k]


def median_ci_coverage(n: int, alpha: float = 0.05) -> float | None:
    """Actual coverage of :func:`median_ci` at this n, or ``None`` if unavailable."""
    best_k = None
    for k in range(1, n // 2 + 2):
        if binom.cdf(k - 1, n, 0.5) <= alpha / 2:
            best_k = k
        else:
            break
    if best_k is None:
        return None
    return float(1 - 2 * binom.cdf(best_k - 1, n, 0.5))


def describe(values: Sequence[float], alpha: float = 0.05) -> Description | None:
    """Median [Q1, Q3] with its CI, plus mean (SD) and range."""
    data = np.asarray([float(v) for v in values if v is not None and not math.isnan(v)])
    if data.size == 0:
        return None
    ci = median_ci(data, alpha)
    return Description(
        n=int(data.size),
        median=float(np.median(data)),
        q1=float(np.percentile(data, 25, method="linear")),
        q3=float(np.percentile(data, 75, method="linear")),
        ci_low=ci[0] if ci else None,
        ci_high=ci[1] if ci else None,
        mean=float(np.mean(data)),
        sd=float(np.std(data, ddof=1)) if data.size > 1 else float("nan"),
        minimum=float(np.min(data)),
        maximum=float(np.max(data)),
    )


# ---- Exact signed-rank machinery ------------------------------------------


def _pratt_ranks(diffs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Midranks of |d| including zeros (Pratt), and the sign of each difference.

    Pratt's convention ranks every observation, zeros included, then leaves the
    zeros' ranks out of the positive and negative sums. Discarding zeros before
    ranking — the more common convention, and SciPy's default — shrinks the
    sample and changes the null distribution.
    """
    magnitudes = np.abs(diffs)
    order = np.argsort(magnitudes, kind="mergesort")
    ranks = np.empty(len(diffs), dtype=float)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and magnitudes[order[j + 1]] == magnitudes[order[i]]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks, np.sign(diffs)


def _null_distribution(weights: Sequence[float]) -> tuple[dict[int, int], int]:
    """Counts of every attainable positive-rank-sum under sign flipping.

    Ranks may be midranks ending in .5, so they are doubled into integers and
    accumulated exactly. This is the conditional sign-flip distribution of the
    observed ranks — the review's requirement — rather than a formula that
    assumes no ties.
    """
    scaled = [int(round(w * 2)) for w in weights]
    counts: dict[int, int] = {0: 1}
    for value in scaled:
        nxt: dict[int, int] = {}
        for total, freq in counts.items():
            nxt[total] = nxt.get(total, 0) + freq
            nxt[total + value] = nxt.get(total + value, 0) + freq
        counts = nxt
    return counts, 2 ** len(scaled)


def signed_rank_exact_p(diffs: Sequence[float]) -> float:
    """Exact two-sided Wilcoxon signed-rank p, Pratt zero handling.

    Computed from the conditional sign-flip distribution of the observed ranks,
    so ties and zeros are handled correctly rather than assumed away. Returns
    1.0 when every difference is zero, which is the honest answer — no evidence
    of any difference, and none of equivalence either.
    """
    d = np.asarray([float(x) for x in diffs], dtype=float)
    d = d[~np.isnan(d)]
    if d.size == 0 or np.all(d == 0):
        return 1.0
    ranks, signs = _pratt_ranks(d)
    nonzero = signs != 0
    weights = ranks[nonzero]
    observed = float(ranks[signs > 0].sum())

    counts, total = _null_distribution(weights)
    target = int(round(observed * 2))
    at_or_below = sum(f for s, f in counts.items() if s <= target)
    at_or_above = sum(f for s, f in counts.items() if s >= target)
    return float(min(1.0, 2 * min(at_or_below, at_or_above) / total))


def rank_biserial(diffs: Sequence[float]) -> float | None:
    """``(W⁺ − W⁻) / (W⁺ + W⁻)`` in [−1, 1]; ``None`` when every difference is zero.

    Zeros are excluded from both sums under Pratt, so a sample of nine zeros and
    one positive difference gives r = 1. The zero count therefore has to be
    reported beside it — see :class:`PairedResult.n_zero`.
    """
    d = np.asarray([float(x) for x in diffs], dtype=float)
    d = d[~np.isnan(d)]
    if d.size == 0 or np.all(d == 0):
        return None
    ranks, signs = _pratt_ranks(d)
    w_plus = float(ranks[signs > 0].sum())
    w_minus = float(ranks[signs < 0].sum())
    if w_plus + w_minus == 0:
        return None
    return (w_plus - w_minus) / (w_plus + w_minus)


def walsh_averages(diffs: Sequence[float]) -> np.ndarray:
    """All ``(dᵢ + dⱼ)/2`` for i ≤ j — the candidate shifts, sorted."""
    d = np.asarray([float(x) for x in diffs], dtype=float)
    d = d[~np.isnan(d)]
    if d.size == 0:
        return np.asarray([])
    sums = d[:, None] + d[None, :]
    iu = np.triu_indices(d.size)
    return np.sort(sums[iu] / 2.0)


def hodges_lehmann(diffs: Sequence[float]) -> float | None:
    """The Hodges–Lehmann estimator: median of the Walsh averages.

    This estimates the **pseudomedian** of the difference distribution. Under
    symmetry that coincides with its median; without symmetry it does not, and
    it is not in general the median of the differences nor the difference of the
    two medians. Naming it accurately is the whole of the fix here.
    """
    walsh = walsh_averages(diffs)
    return float(np.median(walsh)) if walsh.size else None


class IntervalStatus(Enum):
    """What the inverted acceptance set turned out to be.

    A bare ``None`` conflated four different situations, three of which are
    mathematically well defined and should be explained rather than hidden.
    """

    INTERVAL = "interval"
    """A bounded, connected set — the ordinary case."""

    SINGLETON = "singleton"
    """One point. Arises when every difference is identical: shifting by any
    amount makes all residuals non-zero and unanimous, which is rejectable once
    there are at least six of them. Below six it is not, so the same data give
    an unbounded set instead — the distinction is sample-size dependent."""

    UNBOUNDED = "unbounded"
    """No shift is rejectable at this sample size, so no finite interval
    exists. At four pairs the smallest attainable p is 0.125."""

    DISCONNECTED = "disconnected"
    """The accepted set has gaps. Possible under Pratt at exact ties, because p
    is then not monotone in the shift. The enclosing interval is reported and is
    conservative."""

    NO_DATA = "no data"
    """Nothing to invert."""


@dataclass(frozen=True)
class ConfidenceSet:
    """The result of inverting the signed-rank test over all shifts."""

    status: IntervalStatus
    low: float | None = None
    high: float | None = None
    components: int = 0
    exhaustive: bool = True
    """False when the set was bracketed by bisection rather than scanned in
    full, which happens only above :data:`MAX_BREAKPOINTS_FOR_FULL_SCAN`."""

    @property
    def available(self) -> bool:
        return self.low is not None and self.high is not None

    @property
    def excludes_zero(self) -> bool:
        return self.available and (self.low > 0 or self.high < 0)


#: Above this many distinct Walsh averages the full scan gives way to
#: bisection. The scan costs one exact p-value per breakpoint and per gap; at
#: ten pairs there are 55 breakpoints and it takes about 12 ms, so the sample
#: sizes this tool actually sees are always scanned in full. The limit exists so
#: that a large cohort degrades in speed rather than hanging.
MAX_BREAKPOINTS_FOR_FULL_SCAN = 200


def _probe_points(walsh: np.ndarray) -> list[tuple[float, bool]]:
    """Shifts at which p must be evaluated, as ``(delta, is_breakpoint)``.

    ``p(delta)`` is piecewise constant, changing only where delta crosses a
    Walsh average — but the breakpoints themselves are **not** interchangeable
    with the gaps beside them. At a breakpoint one or more residuals become
    exactly zero, which under Pratt changes the ranks and therefore the null
    distribution. A breakpoint can be accepted while both neighbouring gaps are
    rejected, so both have to be probed.

    Duplicate Walsh averages define no gap between them and are collapsed.
    """
    distinct = np.unique(walsh)
    if distinct.size == 0:
        return []
    spread = float(distinct[-1] - distinct[0])
    # A relative step is meaningless when every Walsh average is identical, and
    # can round away to nothing when the spread is denormal. Fall back to an
    # absolute step, then widen until the probe really is outside.
    step = spread * 0.01 if spread > 0 else 1.0
    while step > 0 and float(distinct[0]) - step >= float(distinct[0]):
        step *= 2.0
    if step <= 0:
        step = 1.0

    probes: list[tuple[float, bool]] = [(float(distinct[0]) - step, False)]
    for index, value in enumerate(distinct):
        probes.append((float(value), True))
        if index + 1 < distinct.size:
            probes.append((float((value + distinct[index + 1]) / 2.0), False))
    probes.append((float(distinct[-1]) + step, False))
    return probes


def confidence_set(diffs: Sequence[float], alpha: float = 0.05) -> ConfidenceSet:
    """Invert the reported test over every shift: the set of accepted shifts.

    Uses the same p-value function, and therefore the same zero convention, as
    the test printed beside it — the textbook Walsh-average formula assumes no
    zeros and no ties and would not agree with it.

    Where affordable the whole acceptance set is enumerated rather than
    bracketed, so nothing is assumed about its shape. An earlier version
    bisected on the premise that p is unimodal in the shift, which is true
    without exact ties and unproved with them. Measured across 8,357 randomised
    samples spanning six difference distributions the two agreed everywhere and
    no disconnected set was ever produced — but "never observed" is not "cannot
    happen", and the scan costs 12 ms at ten pairs.

    The interval reported is the **closure** of the accepted set. Where a gap is
    accepted but the breakpoint bounding it is not, the true set is open there;
    reporting the breakpoint is conservative, and is the convention the Wilcoxon
    interval is normally stated in.
    """
    d = np.asarray([float(x) for x in diffs], dtype=float)
    d = d[~np.isnan(d)]
    if d.size == 0:
        return ConfidenceSet(IntervalStatus.NO_DATA)

    walsh = walsh_averages(d)
    probes = _probe_points(walsh)
    if not probes:
        return ConfidenceSet(IntervalStatus.NO_DATA)

    breakpoints = sum(1 for _delta, is_break in probes if is_break)
    if breakpoints > MAX_BREAKPOINTS_FOR_FULL_SCAN:
        return _bisected_confidence_set(d, walsh, alpha)

    accepted = [signed_rank_exact_p(d - delta) > alpha for delta, _is_break in probes]
    if not any(accepted):
        # p peaks at the Hodges-Lehmann estimate, so something is always
        # accepted unless alpha is degenerate.
        return ConfidenceSet(IntervalStatus.NO_DATA)
    if accepted[0] or accepted[-1]:
        return ConfidenceSet(IntervalStatus.UNBOUNDED)

    components = sum(1 for i, ok in enumerate(accepted) if ok and (i == 0 or not accepted[i - 1]))
    indices = [i for i, ok in enumerate(accepted) if ok]

    # Close the set onto the bounding breakpoints.
    low_index = indices[0]
    while not probes[low_index][1] and low_index > 0:
        low_index -= 1
    high_index = indices[-1]
    while not probes[high_index][1] and high_index < len(probes) - 1:
        high_index += 1
    low, high = probes[low_index][0], probes[high_index][0]

    if components > 1:
        return ConfidenceSet(IntervalStatus.DISCONNECTED, low, high, components)
    if low == high:
        return ConfidenceSet(IntervalStatus.SINGLETON, low, high, 1)
    return ConfidenceSet(IntervalStatus.INTERVAL, low, high, 1)


def _bisected_confidence_set(d: np.ndarray, walsh: np.ndarray, alpha: float) -> ConfidenceSet:
    """Bracket the accepted block by bisection, for samples too large to scan.

    Exploits unimodality of p in the shift, which holds without exact ties.
    Flagged ``exhaustive=False`` so the caller knows the shape was assumed
    rather than established.
    """
    m = walsh.size
    if m == 0:
        return ConfidenceSet(IntervalStatus.NO_DATA)
    spread = float(walsh[-1] - walsh[0])
    step = spread * 0.01 if spread > 0 else 1.0

    def representative(index: int) -> float:
        if index == 0:
            return float(walsh[0]) - step
        if index == m:
            return float(walsh[m - 1]) + step
        return float(walsh[index - 1] + walsh[index]) / 2.0

    def accepts(index: int) -> bool:
        return signed_rank_exact_p(d - representative(index)) > alpha

    start = int(np.searchsorted(walsh, float(np.median(walsh)), side="left"))
    if not accepts(start):
        start = next((i for i in range(m + 1) if accepts(i)), -1)
        if start < 0:
            return ConfidenceSet(IntervalStatus.NO_DATA, exhaustive=False)

    low, high = 0, start
    while low < high:
        middle = (low + high) // 2
        if accepts(middle):
            high = middle
        else:
            low = middle + 1
    first = low

    low, high = start, m
    while low < high:
        middle = (low + high + 1) // 2
        if accepts(middle):
            low = middle
        else:
            high = middle - 1
    last = low

    if first == 0 or last == m:
        return ConfidenceSet(IntervalStatus.UNBOUNDED, exhaustive=False)
    return ConfidenceSet(
        IntervalStatus.INTERVAL,
        float(walsh[first - 1]),
        float(walsh[last]),
        1,
        exhaustive=False,
    )


def hodges_lehmann_ci(diffs: Sequence[float], alpha: float = 0.05) -> tuple[float, float] | None:
    """Bounded CI for the pseudomedian, or ``None`` when none exists.

    Thin wrapper over :func:`confidence_set` for callers that only want the two
    numbers. Prefer the full set where the distinction between unbounded, a
    single point and a disconnected region matters to the reader.
    """
    found = confidence_set(diffs, alpha)
    return (found.low, found.high) if found.available else None


# ---- Sign test ------------------------------------------------------------


@dataclass(frozen=True)
class SignResult:
    n_positive: int
    n_negative: int
    n_zero: int
    p_value: float

    @property
    def n_nonzero(self) -> int:
        return self.n_positive + self.n_negative

    @property
    def win_fraction(self) -> float | None:
        return self.n_positive / self.n_nonzero if self.n_nonzero else None


def sign_test(diffs: Sequence[float]) -> SignResult:
    """Exact two-sided sign test on the non-zero differences.

    Reported beside the signed-rank test rather than instead of it. It answers
    how *often* one source wins, ignoring by how much; the signed-rank test
    answers how much. Disagreement between them is informative — usually one
    patient carrying the magnitude.
    """
    d = np.asarray([float(x) for x in diffs], dtype=float)
    d = d[~np.isnan(d)]
    pos = int(np.sum(d > 0))
    neg = int(np.sum(d < 0))
    zero = int(np.sum(d == 0))
    n = pos + neg
    if n == 0:
        return SignResult(pos, neg, zero, 1.0)
    k = max(pos, neg)
    tail = sum(math.comb(n, i) for i in range(k, n + 1))
    return SignResult(pos, neg, zero, float(min(1.0, 2 * tail / 2**n)))


# ---- Multiplicity ---------------------------------------------------------


def holm(p_values: Sequence[float], family_size: int | None = None) -> list[float]:
    """Holm–Bonferroni adjusted p-values, in the input order.

    Uniformly more powerful than plain Bonferroni with the same familywise
    guarantee, and valid under arbitrary dependence between the tests. The
    running maximum enforces monotonicity, which a naive implementation omits.

    ``family_size`` allows the family to contain hypotheses with no p-value —
    comparisons that could not be evaluated. Those sit at the end of the
    step-down ordering and are never rejected, so their only effect is on the
    divisor, which is exactly the point: a family must not silently shrink to
    whatever the data happened to support.
    """
    values = [float(p) for p in p_values]
    if not values:
        return []
    supplied = len(values)
    # The unevaluable hypotheses have no p-value to place, but they still count
    # toward the divisor. They sort to the end of the step-down ordering — an
    # unevaluable comparison is never rejected — so the supplied p-values occupy
    # ranks 0..supplied-1 and only the multiplier changes.
    m = max(supplied, int(family_size or 0))
    order = sorted(range(supplied), key=lambda i: values[i])
    adjusted = [0.0] * supplied
    running = 0.0
    for rank, index in enumerate(order):
        candidate = (m - rank) * values[index]
        running = max(running, candidate)
        adjusted[index] = min(1.0, running)
    return adjusted


def smallest_attainable_p(n_pairs: int) -> float:
    """The smallest two-sided p any sample of this size could produce.

    Both the signed-rank and sign tests bottom out at ``2 / 2ⁿ``, reached when
    every difference falls the same way.
    """
    return 2.0 / 2**n_pairs if n_pairs > 0 else 1.0


def holm_detection_ceiling(n_pairs: int, family_size: int, alpha: float = 0.05) -> bool:
    """Can *anything* in a family this size be rejected at this sample size?

    False means the design cannot produce a significant result however the data
    fall — at n = 10 the floor is 0.001953 and a family of 26 needs 0.001923.
    A report that prints adjusted p = 1.000 without saying this invites the
    reader to conclude the sources are equivalent.
    """
    if n_pairs <= 0 or family_size <= 0:
        return False
    return smallest_attainable_p(n_pairs) <= alpha / family_size


# ---- The paired comparison ------------------------------------------------


@dataclass(frozen=True)
class PairedResult:
    """One organ, one metric, one pair of sources."""

    n_pairs: int
    n_a: int
    n_b: int
    n_zero: int
    hl_estimate: float | None
    ci: ConfidenceSet
    p_value: float
    effect_r: float | None
    sign: SignResult
    ci_agrees_with_test: bool = True
    """Whether the interval excludes zero exactly when the test rejects.

    True in every case without exact zero differences. False flags the Pratt
    spike described in the module docstring, where the acceptance set is not an
    interval and the reported endpoints cannot represent it faithfully.
    """
    p_adjusted: float | None = None

    @property
    def ci_low(self) -> float | None:
        return self.ci.low

    @property
    def ci_high(self) -> float | None:
        return self.ci.high

    @property
    def ci_available(self) -> bool:
        return self.ci.available

    @property
    def ci_status(self) -> IntervalStatus:
        return self.ci.status

    @property
    def coverage_fraction(self) -> float | None:
        """Share of each source's own cases that the comparison could use.

        A paired test uses only patients both sources contoured. When this is
        well below 1, the comparison ran on a subset that is unlikely to be
        random — models fail on hard cases — and the source that contoured less
        is being credited for the cases it declined.
        """
        larger = max(self.n_a, self.n_b)
        return self.n_pairs / larger if larger else None


def paired_comparison(
    values_a: Sequence[float],
    values_b: Sequence[float],
    *,
    n_a: int | None = None,
    n_b: int | None = None,
    alpha: float = 0.05,
) -> PairedResult | None:
    """Compare two sources on the patients where both produced the organ.

    ``values_a`` and ``values_b`` must already be aligned pairwise. ``n_a`` and
    ``n_b`` are each source's own case count *before* pairing, carried through
    so the report can show how much the pairing discarded.
    """
    a = np.asarray([float(v) for v in values_a], dtype=float)
    b = np.asarray([float(v) for v in values_b], dtype=float)
    if a.size != b.size:
        raise ValueError("paired_comparison needs aligned samples")
    keep = ~(np.isnan(a) | np.isnan(b))
    a, b = a[keep], b[keep]
    if a.size == 0:
        return None

    diffs = a - b
    found = confidence_set(diffs, alpha)
    p_value = signed_rank_exact_p(diffs)
    if found.available:
        agrees = found.excludes_zero == (p_value <= alpha)
    else:
        # An unbounded set excludes nothing, so it is consistent with the test
        # only when the test also fails to reject.
        agrees = p_value > alpha
    return PairedResult(
        n_pairs=int(a.size),
        n_a=int(n_a if n_a is not None else a.size),
        n_b=int(n_b if n_b is not None else b.size),
        n_zero=int(np.sum(diffs == 0)),
        hl_estimate=hodges_lehmann(diffs),
        ci=found,
        p_value=p_value,
        effect_r=rank_biserial(diffs),
        sign=sign_test(diffs),
        ci_agrees_with_test=agrees,
    )


def with_holm(
    results: Sequence[PairedResult], family_size: int | None = None
) -> list[PairedResult]:
    """Attach Holm-adjusted p-values across one declared family.

    ``family_size`` is the number of hypotheses the family was **declared** to
    hold, which can exceed the number passed in: an organ selected for the
    family but with no patients both sources contoured is still a hypothesis
    that was posed, and it cannot be rejected. Letting the divisor shrink to the
    number that happened to be estimable would make the correction gentler
    exactly when a source produced less — the same perverse incentive the
    coverage columns exist to expose.

    Omit it and the family is taken to be the results given.
    """
    adjusted = holm([r.p_value for r in results], family_size)
    return [replace(r, p_adjusted=p) for r, p in zip(results, adjusted, strict=True)]


__all__ = [
    "MIN_N_FOR_MEDIAN_CI",
    "MAX_BREAKPOINTS_FOR_FULL_SCAN",
    "confidence_set",
    "IntervalStatus",
    "ConfidenceSet",
    "Description",
    "PairedResult",
    "SignResult",
    "describe",
    "hodges_lehmann",
    "hodges_lehmann_ci",
    "holm",
    "holm_detection_ceiling",
    "median_ci",
    "median_ci_coverage",
    "paired_comparison",
    "rank_biserial",
    "sign_test",
    "signed_rank_exact_p",
    "smallest_attainable_p",
    "walsh_averages",
    "with_holm",
]
