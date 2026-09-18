"""Regenerate the worked-example tables in ``docs/V3_REPORT_STATISTICS_REGISTER.md``.

    python scripts/make_register_tables.py > tables.md

The register is written to be audited, so every number in it should be one the
shipped code actually produces from stated inputs rather than a plausible-looking
figure typed into a table. An earlier hand-written draft drifted into
combinations that cannot occur — a confidence interval at n = 4, and a
rank-biserial r of 0.80 beside p = 0.125 when p = 0.125 at n = 4 forces
r = +/-1.00 exactly.

The Dice values below are synthetic and chosen to exercise each state the report
has to display. They are not measured results.
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "src")

from autoseg_evaluator.core.statistics import (  # noqa: E402
    describe,
    paired_comparison,
    with_holm,
)
from autoseg_evaluator.data.report import interval_text  # noqa: E402

# Synthetic Dice, chosen to exercise each state the report must show:
#   Parotid (L)        clear, consistent difference
#   Parotid (R)        real but smaller — Holm removes it
#   Brainstem          no detectable difference, one patient short
#   SpinalCord         slight edge the other way
#   Glnd Submand (R)   the challenger declined 6 of 10 -> n = 4
LIMBUS = {
    "Parotid (L)": [0.842, 0.811, 0.868, 0.855, 0.799, 0.881, 0.826, 0.837, 0.862, 0.818],
    "Parotid (R)": [0.836, 0.804, 0.859, 0.847, 0.792, 0.873, 0.821, 0.829, 0.854, 0.812],
    "Brainstem": [0.871, 0.850, 0.889, 0.864, 0.828, 0.902, 0.858, 0.879, 0.845],
    "SpinalCord": [0.812, 0.838, 0.795, 0.824, 0.851, 0.807, 0.833, 0.819, 0.842, 0.801],
    "Glnd Submand (R)": [0.821, 0.796, 0.840, 0.808, 0.833, 0.779, 0.815, 0.827, 0.802, 0.844],
}
MVISION = {
    "Parotid (L)": [0.806, 0.779, 0.834, 0.812, 0.766, 0.845, 0.788, 0.799, 0.827, 0.781],
    "Parotid (R)": [0.816, 0.789, 0.871, 0.829, 0.770, 0.882, 0.807, 0.823, 0.843, 0.808],
    "Brainstem": [0.866, 0.855, 0.879, 0.869, 0.819, 0.897, 0.851, 0.884, 0.838],
    "SpinalCord": [0.819, 0.844, 0.788, 0.836, 0.847, 0.816, 0.829, 0.828, 0.838, 0.812],
    "Glnd Submand (R)": [0.774, 0.751, 0.802, 0.762],  # declined on six patients
}
COVERAGE = {"Glnd Submand (R)": (4, 10)}

organs = list(LIMBUS)
results = {}
for organ in organs:
    a, b = MVISION[organ], LIMBUS[organ]
    n = min(len(a), len(b))
    results[organ] = paired_comparison(a[:n], b[:n], n_a=len(a), n_b=len(b), alpha=0.05)
adjusted = with_holm(list(results.values()))
results = dict(zip(organs, adjusted, strict=True))


def fmt(value, spec="+.4f"):
    return "—" if value is None else format(value, spec)


print("## Descriptive statistics · Dice\n")
print("| Organ | Source | n | Median [Q1, Q3] | 95% CI (median) | Mean (SD) | Min / Max |")
print("|---|---|---|---|---|---|---|")
for organ in ("Parotid (L)", "Brainstem", "Glnd Submand (R)"):
    for label, table in (("Limbus", LIMBUS), ("MVision", MVISION)):
        d = describe(table[organ])
        ci = f"{d.ci_low:.3f} – {d.ci_high:.3f}" if d.ci_available else "**— not estimable**"
        print(
            f"| {organ} | {label} | {d.n} | {d.median:.3f} [{d.q1:.3f}, {d.q3:.3f}] | "
            f"{ci} | {d.mean:.3f} ({d.sd:.3f}) | {d.minimum:.3f} / {d.maximum:.3f} |"
        )

print(f"\n\n## Paired comparison · Dice · MVision vs Limbus  (family = {len(organs)} organs)\n")
print(
    "| Organ | n pairs | n chall. / n ref. | HL difference | 95% CI (unadjusted) "
    "| r | zeros | p | p (Holm) | Sign test | Reading |"
)
print("|---|---|---|---|---|---|---|---|---|---|---|")
for organ, r in results.items():
    # Rendered by the same function the Report tab uses, so the published table
    # cannot drift away from what the software actually prints.
    ci = interval_text(r.ci) if r.ci.available else f"**{interval_text(r.ci)}**"
    reading = (
        "no detectable difference"
        if r.p_adjusted > 0.05
        else ("favours Limbus" if (r.hl_estimate or 0) < 0 else "favours MVision")
    )
    print(
        f"| {organ} | {r.n_pairs} | {r.n_a} / {r.n_b} | {fmt(r.hl_estimate)} | {ci} | "
        f"{fmt(r.effect_r, '+.2f')} | {r.n_zero} | {r.p_value:.4f} | {r.p_adjusted:.4f} | "
        f"{r.sign.n_positive}/{r.sign.n_nonzero} · p={r.sign.p_value:.3f} | {reading} |"
    )

print("\n\n## Consistency checks an auditor can repeat\n")
for organ, r in results.items():
    print(
        f"  {organ:<18} n={r.n_pairs:<3} p={r.p_value:.6f}  r={fmt(r.effect_r, '+.3f')}  "
        f"ci_agrees={r.ci_agrees_with_test}  coverage={r.coverage_fraction:.2f}"
    )

smallest_attainable = {n: 2.0 / (2**n) for n in (4, 9, 10)}
print(
    "\n  smallest attainable two-sided p:", {n: round(p, 6) for n, p in smallest_attainable.items()}
)
print(f"  Holm threshold for the most significant of {len(organs)}: {0.05 / len(organs):.4f}")


# ---- ASCII forest, drawn from the same results -----------------------------

LOW, HIGH, WIDTH = -0.050, 0.010, 60
ZERO = round((0.0 - LOW) / (HIGH - LOW) * WIDTH)


def col(value):
    return max(0, min(WIDTH, round((value - LOW) / (HIGH - LOW) * WIDTH)))


print("\n\n## Forest · Dice · MVision vs Limbus (reference)\n")
print("```")
print(" " * 22 + "← favours Limbus" + " " * 16 + "favours MVision →")
print()
ordered = sorted(results.items(), key=lambda kv: kv[1].hl_estimate)
for organ, r in ordered:
    line = [" "] * (WIDTH + 1)
    line[ZERO] = "│"
    if r.ci_available:
        for c in range(col(r.ci_low), col(r.ci_high) + 1):
            line[c] = "─"
        line[col(r.ci_low)] = "├"
        line[col(r.ci_high)] = "┤"
    line[col(r.hl_estimate)] = "●" if r.p_adjusted <= 0.05 else "○"
    tail = (
        f"{r.hl_estimate:+.4f}   p_holm {r.p_adjusted:.4f}"
        if r.ci_available
        else f"{r.hl_estimate:+.4f}   p_holm {r.p_adjusted:.4f}   no interval at n=4"
    )
    print(f"  {organ:<18} n={r.n_pairs:<3}" + "".join(line) + "  " + tail)
print()
axis = [" "] * (WIDTH + 1)
for value in (-0.05, -0.04, -0.03, -0.02, -0.01, 0.0, 0.01):
    text = f"{value:+.2f}"
    start = max(0, col(value) - len(text) // 2)
    axis[start : start + len(text)] = list(text)
print(" " * 25 + "".join(axis))
print(" " * 25 + "Hodges–Lehmann difference in Dice   (MVision − Limbus)")
print()
print("  ● significant after Holm correction        ○ not significant")
print("```")
