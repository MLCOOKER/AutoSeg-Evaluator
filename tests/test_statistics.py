"""Tests for the paired non-parametric statistics.

Several of these exist because a statistical review found the original design
wrong. Each such test names the error it prevents, because the value is in the
specific failure, not in the coverage.

Where a value can be computed by hand it is checked against the hand value, not
against another implementation of the same idea — two wrong routines agree.
"""

from __future__ import annotations

from itertools import product

import numpy as np
import pytest
from scipy.stats import wilcoxon

from autoseg_evaluator.core.statistics import (
    MIN_N_FOR_MEDIAN_CI,
    IntervalStatus,
    _bisected_confidence_set,
    _null_distribution,
    _pratt_ranks,
    _probe_points,
    confidence_set,
    describe,
    hodges_lehmann,
    hodges_lehmann_ci,
    holm,
    holm_detection_ceiling,
    median_ci,
    median_ci_coverage,
    paired_comparison,
    rank_biserial,
    sign_test,
    signed_rank_exact_p,
    smallest_attainable_p,
    walsh_averages,
    with_holm,
)

# ---- Median CI ------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "expected_k", "expected_coverage"),
    [(6, 1, 0.96875), (7, 1, 0.984375), (9, 2, 0.9609375), (10, 2, 0.978515625), (20, 6, 0.9586)],
)
def test_median_ci_matches_the_binomial_table(n, expected_k, expected_coverage):
    """Endpoints and coverage are arithmetic, so they are checked as arithmetic."""
    data = list(range(1, n + 1))
    low, high = median_ci(data)
    assert low == expected_k
    assert high == n - expected_k + 1
    assert median_ci_coverage(n) == pytest.approx(expected_coverage, abs=1e-4)


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5])
def test_no_median_ci_below_six_observations(n):
    """No order-statistic interval reaches 95% coverage here, so none is returned.

    The register's own illustrative table printed an interval at n = 4. That is
    the error this prevents.
    """
    assert median_ci(list(range(1, n + 1))) is None
    assert median_ci_coverage(n) is None
    assert n < MIN_N_FOR_MEDIAN_CI


def test_describe_reports_the_absent_interval_rather_than_omitting_it():
    summary = describe([0.74, 0.76, 0.79, 0.82])
    assert summary.n == 4
    assert not summary.ci_available
    assert summary.median == pytest.approx(0.775)
    # Quartiles are descriptive, so they survive where the interval does not.
    assert summary.q1 == pytest.approx(0.755)


# ---- Exact signed-rank ----------------------------------------------------


def _brute_force_p(diffs):
    """Enumerate every sign flip. Independent of the module's DP implementation."""
    d = np.asarray(diffs, dtype=float)
    magnitudes = np.abs(d)
    order = np.argsort(magnitudes, kind="mergesort")
    ranks = np.empty(len(d))
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and magnitudes[order[j + 1]] == magnitudes[order[i]]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    nonzero = d != 0
    weights = ranks[nonzero]
    observed = ranks[d > 0].sum()
    below = above = 0
    for signs in product([0, 1], repeat=int(nonzero.sum())):
        total = weights[np.array(signs, dtype=bool)].sum()
        if total <= observed + 1e-9:
            below += 1
        if total >= observed - 1e-9:
            above += 1
    return min(1.0, 2 * min(below, above) / 2 ** int(nonzero.sum()))


@pytest.mark.parametrize(
    "diffs",
    [
        [0.04, 0.03, 0.05, 0.01, 0.02, 0.06, 0.07, 0.02, 0.03, 0.05],
        [0.04, -0.03, 0.05, -0.01, 0.02, 0.06, -0.07, 0.02, 0.03, 0.05],
        [0.0, 0.03, 0.05, 0.0, 0.02, -0.06, 0.07, 0.0, 0.03, 0.05],
        [0.02, 0.02, 0.02, -0.02, 0.02, -0.02],
        [0.05, -0.05],
    ],
)
def test_exact_p_matches_exhaustive_enumeration(diffs):
    """The dynamic program must agree with brute force, ties and zeros included."""
    assert signed_rank_exact_p(diffs) == pytest.approx(_brute_force_p(diffs), abs=1e-12)


def test_all_zero_differences_give_p_of_one():
    """No evidence of a difference — and none of equivalence either."""
    assert signed_rank_exact_p([0.0] * 8) == 1.0
    assert rank_biserial([0.0] * 8) is None


def test_zeros_are_ranked_not_discarded():
    """Pratt keeps zeros in the ranking; SciPy's default drops them.

    The two give different p-values on the same data, which is exactly why the
    convention has to be stated rather than inherited.

    Both sides are exact, so the comparison does not depend on which SciPy
    version is installed: dropping the zeros and asking for the exact test is
    what ``zero_method="wilcox"`` means. The data need mixed signs. When every
    non-zero difference has the same sign both conventions give 2 / 2**k, which
    is why an earlier version of this test passed only while SciPy fell back to
    a normal approximation.
    """
    diffs = [0.0, 0.0, 0.01, 0.02, 0.03, 0.04, 0.05, -0.06]
    ours = signed_rank_exact_p(diffs)
    discarded = wilcoxon([d for d in diffs if d != 0], method="exact").pvalue
    assert ours == pytest.approx(0.28125)
    assert discarded == pytest.approx(0.4375)


def test_smallest_attainable_p_is_two_over_two_to_the_n():
    assert smallest_attainable_p(10) == pytest.approx(0.001953125)
    unanimous = [0.01 * (i + 1) for i in range(10)]
    assert signed_rank_exact_p(unanimous) == pytest.approx(0.001953125)


# ---- Hodges–Lehmann and its interval --------------------------------------


def test_walsh_averages_are_complete_and_sorted():
    walsh = walsh_averages([1.0, 3.0, 5.0])
    assert len(walsh) == 6  # n(n+1)/2
    assert list(walsh) == sorted(walsh)
    assert set(np.round(walsh, 6)) == {1.0, 2.0, 3.0, 4.0, 5.0}


def test_hodges_lehmann_is_the_pseudomedian_not_the_median():
    """They differ on skewed differences, which is why the name matters.

    Three zeros and two differences of 0.05: the sample median is 0, while the
    median of the Walsh averages is 0.025. Reporting the latter as "the median
    difference" would be wrong, and under asymmetry it is not the centre of the
    distribution either — it is the pseudomedian, and that is what it is called.
    """
    diffs = [0.0, 0.0, 0.0, 0.05, 0.05]
    assert float(np.median(diffs)) == pytest.approx(0.0)
    assert hodges_lehmann(diffs) == pytest.approx(0.025)


def test_hodges_lehmann_is_the_median_of_the_walsh_averages():
    diffs = [0.01, -0.02, 0.04, 0.03]
    assert hodges_lehmann(diffs) == pytest.approx(float(np.median(walsh_averages(diffs))))


def test_no_interval_at_four_pairs():
    """At n = 4 the smallest attainable p is 0.125, so nothing is rejectable.

    Inverting the test therefore gives an unbounded set. The register printed a
    finite [-0.008, +0.130] here; this is the error that prevents.
    """
    assert smallest_attainable_p(4) == pytest.approx(0.125)
    assert hodges_lehmann_ci([0.02, 0.05, 0.08, 0.11]) is None


@pytest.mark.parametrize(
    "diffs",
    [
        [0.04, 0.03, 0.05, 0.01, 0.02, 0.06, 0.07, 0.02, 0.03, 0.05],
        [0.04, -0.03, 0.05, -0.01, 0.02, 0.06, -0.07, 0.02, 0.03, 0.05],
        [0.0, 0.03, 0.05, 0.0, 0.02, -0.06, 0.07, 0.0, 0.03, 0.05],
        [-0.02, -0.04, -0.01, -0.03, -0.05, -0.02, -0.06, -0.01],
    ],
)
def test_interval_and_test_agree_by_construction(diffs):
    """The property the whole Hodges–Lehmann choice rests on.

    The interval is obtained by inverting the reported test with the reported
    zero convention, so it excludes zero exactly when the test rejects. Pairing
    a Pratt p-value with the textbook Walsh-average interval — which assumes no
    zeros or ties — does not guarantee this.
    """
    p = signed_rank_exact_p(diffs)
    ci = hodges_lehmann_ci(diffs, alpha=0.05)
    if ci is None:
        assert p > 0.05
        return
    excludes_zero = ci[0] > 0 or ci[1] < 0
    assert excludes_zero == (p <= 0.05), f"p={p}, ci={ci}"


def test_agreement_holds_across_randomised_samples_without_exact_zeros():
    """The property the Hodges-Lehmann choice rests on, stress-tested.

    Zero violations over several hundred samples spanning shifted, null and
    rounded (tied) differences. Chosen cases can be lucky; this cannot.
    """
    rng = np.random.default_rng(11)
    checked = 0
    for _ in range(300):
        n = int(rng.integers(4, 14))
        shape = int(rng.integers(0, 3))
        if shape == 0:
            d = rng.normal(0.01, 0.03, n)
        elif shape == 1:
            d = rng.normal(0.0, 0.03, n)
        else:
            d = np.round(rng.normal(0.005, 0.02, n), 3)
        if np.any(d == 0):
            continue
        checked += 1
        p = signed_rank_exact_p(d)
        ci = hodges_lehmann_ci(d)
        if ci is None:
            assert p > 0.05
            continue
        assert (ci[0] > 0 or ci[1] < 0) == (p <= 0.05)
        assert ci[0] <= hodges_lehmann(d) <= ci[1]
    assert checked > 200


def test_exact_zero_differences_break_agreement_and_say_so():
    """Pratt spikes p at delta = 0 when a difference is exactly zero.

    Shifting by any epsilon turns those zeros into non-zeros all pointing the
    same way, so the acceptance set is not an interval and no pair of endpoints
    represents it. The result flags this rather than presenting a clean
    interval that disagrees with the p-value beside it.
    """
    a = [0.80, 0.80, 0.80, 0.80, 0.86, 0.87, 0.88, 0.89, 0.90, 0.91]
    b = [0.80, 0.80, 0.80, 0.80, 0.82, 0.83, 0.84, 0.85, 0.86, 0.87]
    result = paired_comparison(a, b)
    assert result.n_zero == 4
    assert result.ci_agrees_with_test is False


def test_agreement_flag_is_true_for_ordinary_data():
    a = [0.80, 0.83, 0.79, 0.85, 0.81, 0.84, 0.86, 0.82]
    b = [0.74, 0.77, 0.75, 0.79, 0.76, 0.78, 0.80, 0.75]
    result = paired_comparison(a, b)
    assert result.n_zero == 0
    assert result.ci_agrees_with_test is True


def test_interval_contains_the_point_estimate():
    diffs = [0.04, 0.03, 0.05, 0.01, 0.02, 0.06, 0.07, 0.02, 0.03, 0.05]
    estimate = hodges_lehmann(diffs)
    low, high = hodges_lehmann_ci(diffs)
    assert low <= estimate <= high


# ---- Rank-biserial --------------------------------------------------------


def test_rank_biserial_is_one_when_every_nonzero_difference_agrees():
    """Nine zeros and one positive difference still give r = 1 under Pratt.

    Which is why the zero count is reported next to it.
    """
    assert rank_biserial([0.0] * 9 + [0.05]) == pytest.approx(1.0)


def test_rank_biserial_sign_follows_direction():
    assert rank_biserial([0.05, 0.03, 0.04]) > 0
    assert rank_biserial([-0.05, -0.03, -0.04]) < 0
    assert abs(rank_biserial([0.05, -0.05, 0.03, -0.03])) < 1e-9


# ---- Sign test ------------------------------------------------------------


def test_sign_test_counts_and_exact_p():
    result = sign_test([0.1, 0.2, -0.1, 0.0, 0.3, 0.4])
    assert (result.n_positive, result.n_negative, result.n_zero) == (4, 1, 1)
    assert result.n_nonzero == 5
    assert result.p_value == pytest.approx(2 * (5 + 1) / 32)


def test_sign_test_needs_nine_of_ten_at_the_conventional_level():
    """Recorded because it bounds what this design can show.

    Eight of ten does not reach 0.05 however large the differences are.
    """
    assert sign_test([1.0] * 9 + [-1.0]).p_value == pytest.approx(0.021484375)
    assert sign_test([1.0] * 8 + [-1.0] * 2).p_value > 0.05


# ---- Holm -----------------------------------------------------------------


def test_holm_matches_a_worked_example():
    raw = [0.01, 0.02, 0.03, 0.04]
    assert holm(raw) == pytest.approx([0.04, 0.06, 0.06, 0.06])


def test_holm_is_monotone_and_never_below_raw():
    """The running maximum a naive implementation leaves out."""
    rng = np.random.default_rng(20260917)
    for _ in range(200):
        raw = list(rng.uniform(0, 1, size=int(rng.integers(2, 12))))
        adjusted = holm(raw)
        assert all(a >= r - 1e-12 for a, r in zip(adjusted, raw, strict=True))
        pairs = sorted(zip(raw, adjusted, strict=True))
        assert all(pairs[i][1] <= pairs[i + 1][1] + 1e-12 for i in range(len(pairs) - 1))


def test_holm_preserves_input_order():
    assert holm([0.04, 0.01]) == pytest.approx([0.04, 0.02])


# ---- The detection ceiling ------------------------------------------------


def test_ten_pairs_cannot_reject_anything_in_a_family_of_twenty_six():
    """0.05/26 = 0.001923 sits below the 0.001953 floor.

    The report states this rather than printing a column of 1.000 that reads as
    evidence the sources are equivalent.
    """
    assert holm_detection_ceiling(10, 25) is True
    assert holm_detection_ceiling(10, 26) is False


def test_a_larger_family_becomes_reachable_with_more_patients():
    assert holm_detection_ceiling(10, 40) is False
    assert holm_detection_ceiling(20, 40) is True


# ---- The paired comparison ------------------------------------------------


def test_paired_comparison_carries_the_unpaired_counts():
    """A comparison on four of ten patients has to say so."""
    result = paired_comparison([0.8, 0.82, 0.79, 0.81], [0.74, 0.77, 0.75, 0.73], n_a=10, n_b=4)
    assert result.n_pairs == 4
    assert result.n_a == 10 and result.n_b == 4
    assert result.coverage_fraction == pytest.approx(0.4)
    # …and at four pairs it cannot have an interval.
    assert not result.ci_available


def test_paired_comparison_drops_incomplete_pairs():
    result = paired_comparison(
        [0.80, float("nan"), 0.79, 0.81, 0.83, 0.78, 0.82],
        [0.74, 0.77, 0.75, 0.73, 0.76, 0.72, 0.75],
        n_a=6,
        n_b=7,
    )
    assert result.n_pairs == 6


def test_with_holm_adjusts_across_the_declared_family():
    samples = [
        ([0.9] * 10, [0.80, 0.81, 0.82, 0.83, 0.84, 0.85, 0.86, 0.87, 0.88, 0.89]),
        ([0.9] * 10, [0.88, 0.91, 0.89, 0.92, 0.90, 0.93, 0.88, 0.91, 0.89, 0.92]),
    ]
    results = with_holm([paired_comparison(a, b) for a, b in samples])
    assert all(r.p_adjusted is not None for r in results)
    assert all(r.p_adjusted >= r.p_value - 1e-12 for r in results)


def test_identical_sources_are_not_called_equivalent():
    """Every difference zero gives p = 1 and no effect size.

    The interval is a separate question, and a sample-size dependent one — see
    the two tests below. What must never happen is a p-value of 1 being read as
    evidence that the sources agree.
    """
    values = [0.81, 0.83, 0.79, 0.85, 0.80, 0.82, 0.84, 0.78]
    result = paired_comparison(values, values)
    assert result.p_value == 1.0
    assert result.effect_r is None
    assert result.n_zero == len(values)


def test_identical_differences_give_a_singleton_from_six_observations():
    """Raised in external review: the acceptance set here is not empty.

    With every difference equal, shifting by any amount makes all residuals
    non-zero and unanimous, so p away from the common value is 2^(1-n). From
    six observations that is 0.03125 and rejectable, leaving exactly one
    accepted point. Reporting nothing would hide a defined result; the earlier
    version did, on the grounds that a reader might misread it.
    """
    for n in (6, 8, 10):
        found = confidence_set([0.0] * n)
        assert found.status is IntervalStatus.SINGLETON, n
        assert found.low == found.high == 0.0

    # A non-zero common difference puts the singleton at that value.
    found = confidence_set([0.1] * 6)
    assert found.status is IntervalStatus.SINGLETON
    assert found.low == pytest.approx(0.1)


def test_the_same_data_give_an_unbounded_set_below_six_observations():
    """The companion case, and why the claim had to be made sample-size aware.

    At five identical differences p away from the common value is 0.0625, which
    does not reject at 0.05 — so nothing is rejectable and the acceptance set is
    the whole line, not a point.
    """
    for n in (2, 3, 4, 5):
        found = confidence_set([0.1] * n)
        assert found.status is IntervalStatus.UNBOUNDED, n
        assert not found.available


@pytest.mark.parametrize("infinity", [float("inf"), float("-inf")])
def test_an_infinite_difference_is_ignored_not_iterated_on(infinity):
    """External audit: ``-inf`` never terminated the probe-step loop.

    A Hausdorff distance to an empty contour is infinite. No finite step moves a
    probe past ``-inf``, so the widening loop spun forever; ``+inf`` returned an
    interval silently. Every entry point now uses the finite values only.
    """
    finite = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    with_infinity = [infinity, *finite]
    assert confidence_set(with_infinity) == confidence_set(finite)
    assert signed_rank_exact_p(with_infinity) == signed_rank_exact_p(finite)
    assert hodges_lehmann(with_infinity) == hodges_lehmann(finite)
    assert sign_test(with_infinity) == sign_test(finite)
    assert describe(with_infinity) == describe(finite)
    assert median_ci(with_infinity) == median_ci(finite)
    assert _probe_points(np.asarray([infinity, 1.0, 2.0]))[0][0] < 1.0
    paired = paired_comparison([infinity, *finite], [0.0] * 8)
    assert paired.n_pairs == 7


def test_a_clean_sweep_is_detectable_at_ten_pairs():
    """The one shape that survives correction at this sample size."""
    a = [0.80, 0.81, 0.82, 0.83, 0.84, 0.85, 0.86, 0.87, 0.88, 0.89]
    b = [v - 0.03 - 0.001 * i for i, v in enumerate(a)]
    result = paired_comparison(a, b)
    assert result.p_value == pytest.approx(0.001953125)
    assert result.ci_available
    assert result.ci_low > 0
    assert result.sign.win_fraction == 1.0


# ---- Claims raised in external statistical review -------------------------


def test_the_null_distribution_keeps_tied_multiplicities():
    """Raised in review: 2^k assignments are not 2^k distinct statistic values.

    Different sign assignments can produce the same rank sum, and the counts of
    each must be preserved or the tail probabilities are wrong. The reviewer
    supplied this fixture and computed it independently.
    """
    diffs = [0.0, 0.0, 1.0, 1.0, -2.0, 3.0]
    ranks, signs = _pratt_ranks(np.asarray(diffs))
    weights = ranks[signs != 0]

    assert [int(round(w * 2)) for w in weights] == [7, 7, 10, 12]

    counts, total = _null_distribution(weights)
    assert total == 16  # sign assignments
    assert len(counts) == 12  # distinct sums
    assert sum(counts.values()) == 16  # multiplicities preserved

    # Independently enumerated, rather than trusting the DP.
    brute: dict[int, int] = {}
    for mask in product([0, 1], repeat=4):
        key = sum(v for v, take in zip([7, 7, 10, 12], mask) if take)
        brute[key] = brute.get(key, 0) + 1
    assert counts == brute

    assert signed_rank_exact_p(diffs) == pytest.approx(0.5)


def test_the_null_distribution_counts_are_arbitrary_precision():
    """Raised in review: counts overflow fixed-width integers at ~100 pairs.

    They do — the largest count here exceeds 2^63. Python integers are unbounded
    so the arithmetic is exact, but a well-meant rewrite onto a numpy integer
    array would silently wrap. The invariant that catches it is that the
    multiplicities sum to exactly 2^k.
    """
    rng = np.random.default_rng(0)
    diffs = rng.normal(0, 1, 100)
    ranks, signs = _pratt_ranks(diffs)
    counts, total = _null_distribution(ranks[signs != 0])

    assert total == 2**100
    assert sum(counts.values()) == 2**100
    assert max(counts.values()) > 2**63 - 1  # would have wrapped in int64
    assert all(isinstance(v, int) for v in counts.values())


def test_breakpoints_are_probed_as_well_as_the_gaps_between_them():
    """Raised in review: a breakpoint can accept while both neighbours reject.

    At a Walsh average some residuals become exactly zero, which under Pratt
    changes the ranks and so the null distribution. Its status cannot be
    inferred from the gaps on either side, so both are probed.
    """
    probes = _probe_points(np.asarray([1.0, 2.0, 3.0]))
    kinds = [is_break for _delta, is_break in probes]
    # exterior, bp, gap, bp, gap, bp, exterior
    assert kinds == [False, True, False, True, False, True, False]
    assert [d for d, is_break in probes if is_break] == [1.0, 2.0, 3.0]
    assert [d for d, is_break in probes if not is_break] == [0.98, 1.5, 2.5, 3.02]


def test_duplicate_walsh_averages_define_no_gap():
    """Repeated breakpoints do not create extra non-empty regions."""
    probes = _probe_points(np.asarray([1.0, 1.0, 1.0]))
    assert [d for d, is_break in probes if is_break] == [1.0]
    # Only the two exterior probes remain, and both must be strictly outside.
    outside = [d for d, is_break in probes if not is_break]
    assert len(outside) == 2
    assert outside[0] < 1.0 < outside[1]


def test_the_exterior_probe_survives_a_zero_spread():
    """A step of '1% of the spread' is zero when every Walsh average is equal."""
    for value in (0.0, 0.1, -5.0, 1e-300):
        probes = _probe_points(np.asarray([value] * 4))
        outside = [d for d, is_break in probes if not is_break]
        assert outside[0] < value < outside[1], value


def test_the_full_scan_and_the_bisection_agree():
    """The scan removes an unproved unimodality assumption; it should not move
    any answer at the sample sizes that reach it.

    Measured over randomised samples spanning tie structures from continuous to
    heavily quantised. Also asserts no acceptance set was disconnected, which is
    the failure mode bisection could not have detected.
    """
    rng = np.random.default_rng(20260918)
    shapes = (
        lambda n: rng.normal(0, 1, n),
        lambda n: np.round(rng.normal(0, 1, n) * 2) / 2,
        lambda n: np.round(rng.normal(0, 0.6, n)),
        lambda n: np.round(rng.normal(-0.03, 0.02, n), 3),
    )
    checked = 0
    for shape in shapes:
        for _ in range(60):
            n = int(rng.integers(4, 13))
            d = shape(n)
            if np.all(d == 0):
                continue
            checked += 1
            found = confidence_set(d)
            assert found.exhaustive
            assert found.status is not IntervalStatus.DISCONNECTED
            bisected = _bisected_confidence_set(d, walsh_averages(d), 0.05)
            if found.available and bisected.available:
                assert found.low == pytest.approx(bisected.low)
                assert found.high == pytest.approx(bisected.high)
            else:
                assert found.available == bisected.available
    assert checked > 200


def test_holm_does_not_shrink_to_the_estimable_hypotheses():
    """Raised in review: silent family reduction is anti-conservative.

    A family of five organs where only three could be compared must still
    divide by five. Otherwise a source that produced fewer organs is rewarded
    with a gentler correction.
    """
    p_values = [0.004, 0.02, 0.30]

    shrunk = holm(p_values)
    declared = holm(p_values, family_size=5)

    assert shrunk[0] == pytest.approx(0.012)  # 3 x 0.004
    assert declared[0] == pytest.approx(0.020)  # 5 x 0.004
    assert all(d >= s for d, s in zip(declared, shrunk))

    # A declared size below the number supplied cannot weaken the correction.
    assert holm(p_values, family_size=1) == shrunk


def test_a_declared_family_size_changes_whether_a_result_survives():
    """The reduction is not cosmetic — it moves results across 0.05."""
    a = [0.80, 0.81, 0.82, 0.83, 0.84, 0.85, 0.86, 0.87, 0.88, 0.89]
    b = [v - 0.03 - 0.001 * i for i, v in enumerate(a)]
    result = paired_comparison(a, b)

    as_estimated = with_holm([result])[0]
    as_declared = with_holm([result], family_size=30)[0]

    assert as_estimated.p_adjusted <= 0.05
    assert as_declared.p_adjusted > 0.05
