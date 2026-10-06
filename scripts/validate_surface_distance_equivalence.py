"""AutoSeg's surface-distance port against Google DeepMind's package, case by case.

Dice, the Hausdorff distances, mean surface distance and Surface Dice come from
``core/surface_distance.py``, a port of google-deepmind/surface-distance carried
since v1. The port has changed since (a cached surface-area table, a vectorised
sort, the caller's crop to the pair's bounding box), each meant to change
nothing, and the application calls it its own way: spacing in the array's axis
order, the two directional means averaged. This asks whether any of that moved a
single value.

Two comparisons per case, on synthetic mask pairs:

port
    ``compute_surface_distances`` and every metric function, called exactly as
    the package's are, on the same arrays: the per-element distance and area
    arrays in both directions, Dice, the robust Hausdorff distance at several
    percentiles, both directional means and Surface Dice at several tolerances.
application
    ``compute_geometric_metrics``, as a computation run calls it, from SimpleITK
    images carrying physical spacing, against the package applied to the full
    arrays with the spacing in the arrays' axis order. This is the path that
    would catch a spacing passed in the wrong order, which an isotropic case
    cannot.

Every comparison is exact equality: no tolerance.

Writes two files: Supplementary 4, a short report (aim, method, results,
findings), and its full results, every table behind it.

Usage::

    python scripts/validate_surface_distance_equivalence.py [--cases 1000]

Needs the ``surface-distance`` package (``pip install
git+https://github.com/google-deepmind/surface-distance``). Every input is
synthetic.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import math
import sys
import time
import warnings
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import SimpleITK as sitk

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "src"))

import surface_distance as deepmind  # noqa: E402
from validation_common import VALIDATION_DOCS, md_table, stamp, unwrap  # noqa: E402

# The package calls scipy.ndimage.morphology, deprecated in SciPy, and divides by
# zero on the empty-mask cases, where an undefined value is the expected answer.
warnings.filterwarnings("ignore", category=DeprecationWarning, module="surface_distance")
warnings.filterwarnings("ignore", category=RuntimeWarning, module="surface_distance")

from autoseg_evaluator.core import surface_distance as port  # noqa: E402
from autoseg_evaluator.core.metrics import compute_geometric_metrics  # noqa: E402
from autoseg_evaluator.core.tolerance_keys import tolerance_key  # noqa: E402

SCRIPT = Path(__file__).name
TITLE = "Supplementary 4 - Google-deepmind/surface-distance Implementation"
REPORT = VALIDATION_DOCS / "Supplementary_4_Google-deepmind_surface-distance_Implementation.md"
FULL_RESULTS = REPORT.with_name(REPORT.stem + "_Full_Results.md")

SEED = 20261001
#: Array shape, (z, y, x): large enough for organ-sized shapes with room to move.
SHAPE = (36, 56, 56)
#: Voxel spacings in the arrays' axis order (z, y, x), mm: isotropic, and the
#: anisotropic grids of planning CT, including the cohort's in-plane pixels.
SPACINGS: tuple[tuple[float, float, float], ...] = (
    (1.0, 1.0, 1.0),
    (3.0, 0.98, 0.98),
    (2.0, 1.074, 1.074),
    (2.0, 1.367, 1.367),
    (1.0, 0.468, 0.468),
    (0.5, 0.5, 0.5),
    (2.5, 1.2, 0.8),
)
PERCENTS = (50.0, 90.0, 95.0, 99.0, 100.0)
TOLERANCES_MM = (0.0, 0.5, 1.0, 2.0, 3.0, 5.0)
#: The tolerances the application path is asked for.
APP_TOLERANCES_MM = (1.0, 2.0, 3.0)
#: The per-surface-element arrays, and how the report names them.
ARRAYS = {
    "distances_gt_to_pred": "Distances, reference → test (every surface element)",
    "distances_pred_to_gt": "Distances, test → reference (every surface element)",
    "surfel_areas_gt": "Surface-element areas, reference",
    "surfel_areas_pred": "Surface-element areas, test",
}

#: The hand-made pairs that open the run, before the random ones.
EDGE_CASES = (
    "identical",
    "a single voxel each",
    "disjoint",
    "test empty",
    "both empty",
    "a sheet one voxel thick",
)
#: The random reference structures, by kind: (spheres in the union, radius range
#: in voxels).
RANDOM_KINDS = {
    "organ-sized ellipsoid": (1, (8.0, 16.0)),
    "irregular, several components": (None, (3.0, 8.0)),
    "small (1-3 voxels in radius)": (1, (1.2, 3.0)),
    "large, lobulated": (3, (6.0, 14.0)),
}
#: A test structure is its reference moved by up to this many voxels per axis...
MAX_SHIFT = 3
#: ...then enlarged or eroded by up to this many small ellipsoids, radius in voxels.
MAX_LOCAL, LOCAL_RADIUS = 2, (2.0, 6.0)


class Cases:
    """The mask pairs, deterministic from :data:`SEED`."""

    def __init__(self, n_random: int) -> None:
        self.rng = np.random.default_rng(SEED)
        self.n_random = n_random
        self.grid = np.indices(SHAPE)

    def blob(self, n_spheres: int, radius: tuple[float, float]) -> np.ndarray:
        """A union of random ellipsoids."""
        z, y, x = self.grid
        out = np.zeros(SHAPE, bool)
        for _ in range(n_spheres):
            c = [self.rng.uniform(6, s - 6) for s in SHAPE]
            r = self.rng.uniform(*radius, size=3)
            out |= ((z - c[0]) / r[0]) ** 2 + ((y - c[1]) / r[1]) ** 2 + (
                (x - c[2]) / r[2]
            ) ** 2 <= 1
        return out

    def perturb(self, mask: np.ndarray) -> np.ndarray:
        """A test structure: the reference moved, then locally enlarged or eroded."""
        shift = self.rng.integers(-MAX_SHIFT, MAX_SHIFT + 1, size=3)
        out = np.roll(mask, tuple(int(s) for s in shift), axis=(0, 1, 2))
        local = self.blob(int(self.rng.integers(0, MAX_LOCAL + 1)), LOCAL_RADIUS)
        return (out | local) if self.rng.random() < 0.5 else (out & ~local)

    def __iter__(self) -> Iterator[tuple[str, np.ndarray, np.ndarray]]:
        identical, single, disjoint, test_empty, both_empty, sheet_case = EDGE_CASES
        same = self.blob(2, (5.0, 12.0))
        yield identical, same, same.copy()
        one_a, one_b = np.zeros(SHAPE, bool), np.zeros(SHAPE, bool)
        one_a[18, 20, 20] = one_b[18, 21, 22] = True
        yield single, one_a, one_b
        yield disjoint, self.blob(1, (3.0, 5.0)), self.blob(1, (3.0, 5.0))
        yield test_empty, self.blob(2, (4.0, 9.0)), np.zeros(SHAPE, bool)
        yield both_empty, np.zeros(SHAPE, bool), np.zeros(SHAPE, bool)
        sheet = np.zeros(SHAPE, bool)
        sheet[10:20, 10:40, 25] = True
        yield sheet_case, sheet, np.roll(sheet, 1, axis=2)
        kinds = list(RANDOM_KINDS)
        for i in range(self.n_random):
            kind = kinds[i % len(kinds)]
            spheres, radius = RANDOM_KINDS[kind]
            count = int(self.rng.integers(2, 5)) if spheres is None else spheres
            reference = self.blob(count, radius)
            yield kind, reference, self.perturb(reference)


def _same(a: float, b: float) -> bool:
    """Exact equality, with two NaNs equal: both undefined is agreement."""
    return (math.isnan(a) and math.isnan(b)) or a == b


class Tally:
    """Comparisons per quantity and per group: how many, how many identical."""

    def __init__(self) -> None:
        self.count: Counter = Counter()
        self.identical: Counter = Counter()
        self.largest: dict[str, float] = defaultdict(float)
        self.mismatches: list[tuple[str, str, str]] = []
        #: By (group, member), e.g. ("spacing", (2.0, 1.074, 1.074)).
        self.group_count: Counter = Counter()
        self.group_identical: Counter = Counter()
        self.both_infinite = 0
        self.both_undefined = 0

    def _record(self, quantity: str, same: bool, groups: tuple) -> None:
        self.count[quantity] += 1
        self.identical[quantity] += same
        for group in groups:
            self.group_count[group] += 1
            self.group_identical[group] += same

    def add(self, quantity: str, a: float, b: float, case: str, groups: tuple = ()) -> None:
        a, b = float(a), float(b)
        same = _same(a, b)
        self._record(quantity, same, groups)
        if same:
            self.both_undefined += math.isnan(a)
            self.both_infinite += math.isinf(a)
        else:
            self.mismatches.append((case, quantity, f"{a!r} against {b!r}"))
            if math.isfinite(a) and math.isfinite(b):
                self.largest[quantity] = max(self.largest[quantity], abs(a - b))

    def add_array(
        self, quantity: str, a: np.ndarray, b: np.ndarray, case: str, groups: tuple = ()
    ) -> None:
        same = a.shape == b.shape and bool(np.array_equal(a, b))
        self._record(quantity, same, groups)
        if not same:
            self.mismatches.append((case, quantity, f"arrays differ ({a.shape} and {b.shape})"))

    @property
    def total(self) -> int:
        return sum(self.count.values())

    @property
    def total_identical(self) -> int:
        return sum(self.identical.values())


def _app_config() -> dict:
    return {
        "geometric": {
            "dice": True,
            "hausdorff100": True,
            "hausdorff95": True,
            "mean_surface_distance": True,
            "surface_dice": True,
        },
        "tolerances": {"surface_dice_tau_mm": list(APP_TOLERANCES_MM)},
    }


def _image(mask: np.ndarray, spacing_zyx: tuple[float, float, float]) -> sitk.Image:
    image = sitk.GetImageFromArray(mask.astype(np.uint8))
    image.SetSpacing(tuple(reversed(spacing_zyx)))  # SimpleITK takes (x, y, z)
    return image


def _mean(a: float, b: float) -> float:
    return math.nan if (math.isnan(a) or math.isnan(b)) else 0.5 * (a + b)


def compare(n_random: int) -> dict:
    """Run every case; return the tallies and what the cases were."""
    port_tally, app_tally = Tally(), Tally()
    kinds: Counter = Counter()
    spacings: Counter = Counter()
    edge_values: list[tuple[str, tuple, list[tuple[str, float, float]]]] = []
    for n, (kind, gt, test) in enumerate(Cases(n_random)):
        spacing = SPACINGS[n % len(SPACINGS)]
        kinds[kind] += 1
        spacings[spacing] += 1
        label = f"{kind} #{n} at {spacing}"
        groups = (("spacing", spacing), ("kind", kind))

        ours = port.compute_surface_distances(gt, test, spacing)
        theirs = deepmind.compute_surface_distances(gt, test, spacing)
        for key, name in ARRAYS.items():
            port_tally.add_array(name, ours[key], theirs[key], label, groups)
        dice = deepmind.compute_dice_coefficient(gt, test)
        port_tally.add("Dice", port.compute_dice_coefficient(gt, test), dice, label, groups)
        for p in PERCENTS:
            port_tally.add(
                f"Hausdorff {p:g}%",
                port.compute_robust_hausdorff(ours, p),
                deepmind.compute_robust_hausdorff(theirs, p),
                label,
                groups,
            )
        o_ab, o_ba = port.compute_average_surface_distance(ours)
        t_ab, t_ba = deepmind.compute_average_surface_distance(theirs)
        port_tally.add("Mean distance, reference → test", o_ab, t_ab, label, groups)
        port_tally.add("Mean distance, test → reference", o_ba, t_ba, label, groups)
        for tau in TOLERANCES_MM:
            port_tally.add(
                f"Surface Dice @ {tau:g} mm",
                port.compute_surface_dice_at_tolerance(ours, tau),
                deepmind.compute_surface_dice_at_tolerance(theirs, tau),
                label,
                groups,
            )

        app = compute_geometric_metrics(_image(gt, spacing), _image(test, spacing), _app_config())
        reported = [
            ("Dice", app["dice"], dice),
            ("Hausdorff 100%", app["hausdorff100"], deepmind.compute_robust_hausdorff(theirs, 100)),
            ("Hausdorff 95%", app["hausdorff95"], deepmind.compute_robust_hausdorff(theirs, 95)),
            ("Mean surface distance", app["mean_surface_distance"], _mean(t_ab, t_ba)),
            *(
                (
                    f"Surface Dice @ {tau:g} mm",
                    app[tolerance_key("surface_dice", tau)],
                    deepmind.compute_surface_dice_at_tolerance(theirs, tau),
                )
                for tau in APP_TOLERANCES_MM
            ),
        ]
        for quantity, ours_value, theirs_value in reported:
            app_tally.add(quantity, ours_value, theirs_value, label, groups)
        if kind in EDGE_CASES:
            edge_values.append((kind, spacing, reported))
    return {
        "port": port_tally,
        "app": app_tally,
        "kinds": kinds,
        "spacings": spacings,
        "edge_values": edge_values,
        "cases": sum(kinds.values()),
    }


# ---- Reports ------------------------------------------------------------------


def _version(*names: str) -> str:
    for name in names:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return "unknown"


def _software() -> str:
    return (
        f"surface-distance {_version('surface-distance', 'Surface-Distance-Based-Measures')}, "
        f"NumPy {_version('numpy')}, SciPy {_version('scipy')}, "
        f"SimpleITK {_version('SimpleITK', 'simpleitk')}"
    )


def _spacing(s: tuple[float, float, float]) -> str:
    z, y, x = s
    return f"{x:g} × {y:g} × {z:g}"


def _of(identical: int, total: int) -> str:
    return f"{identical:,} of {total:,}"


def _value(v: float) -> str:
    if math.isnan(v):
        return "undefined"
    if math.isinf(v):
        return "∞"
    return f"{v:.6g}"


def _per_quantity(tally: Tally) -> list[list[str]]:
    rows = []
    for quantity in tally.count:
        n, same = tally.count[quantity], tally.identical[quantity]
        gap = tally.largest.get(quantity, 0.0)
        rows.append([quantity, f"{n:,}", f"{same:,}", "0" if same == n else f"{gap:.3g}"])
    return rows


def _per_group(results: dict, group: str, members, label) -> list[list[str]]:
    port_tally, app_tally = results["port"], results["app"]
    rows = []
    for member in members:
        key = (group, member)
        rows.append(
            [
                label(member),
                f"{results[group + 's'][member]:,}",
                _of(port_tally.group_identical[key], port_tally.group_count[key]),
                _of(app_tally.group_identical[key], app_tally.group_count[key]),
            ]
        )
    return rows


def _empty_mask_behaviour(results: dict) -> tuple[bool, bool]:
    """Whether the library's ∞ Hausdorff and undefined mean, for an empty test, were matched."""
    for kind, _, reported in results["edge_values"]:
        if kind == "test empty":
            values = {q: (ours, theirs) for q, ours, theirs in reported}
            hd = values["Hausdorff 100%"]
            msd = values["Mean surface distance"]
            return (
                math.isinf(hd[0]) and math.isinf(hd[1]),
                math.isnan(msd[0]) and math.isnan(msd[1]),
            )
    return False, False


def write_report(target: Path, results: dict, seconds: float) -> None:
    """Supplementary 4: what was validated, how, what was found, and what it decided."""
    port_tally, app_tally = results["port"], results["app"]
    n_cases = results["cases"]
    n_random = n_cases - len(EDGE_CASES)
    random_counts = [results["kinds"][k] for k in RANDOM_KINDS if k in results["kinds"]]
    port_exact = not port_tally.mismatches
    app_exact = not app_tally.mismatches
    anisotropic = [s for s in SPACINGS if len(set(s)) > 1]
    isotropic = [s for s in SPACINGS if len(set(s)) == 1]
    spacing_exact = all(
        app_tally.group_identical[("spacing", s)] == app_tally.group_count[("spacing", s)]
        for s in SPACINGS
    )
    infinite_matched, undefined_matched = _empty_mask_behaviour(results)
    port_quantities, app_quantities = len(port_tally.count), len(app_tally.count)

    lines = [
        f"# {TITLE}",
        "",
        f"{stamp(SCRIPT)}. Every table behind this report is in its "
        f"[full results]({FULL_RESULTS.name}).",
        "",
        "## Aim",
        "",
        "AutoSeg Evaluator computes its 3D metrics on binary masks: Dice, the 100 % and 95 %",
        "Hausdorff distances, the mean surface distance and Surface Dice. The calculation is",
        "Google DeepMind's open-source surface-distance library [1], published with the study",
        "that introduced Surface Dice [2]. AutoSeg carries its own copy of the library's code",
        "(Apache License 2.0) rather than installing it, and the copy has been changed in three",
        "ways to make it faster:",
        "",
        "- the table of surface-element areas is built once for each voxel spacing and reused,",
        "  instead of being rebuilt for every structure;",
        "- one sorting step was rewritten to run faster, keeping the same order;",
        "- AutoSeg trims both masks to the smallest box that holds them before the calculation,",
        "  the same box the library trims them to itself.",
        "",
        "None of these should change a result. This validation asks two questions:",
        "",
        "1. Does AutoSeg's copy give exactly the values the original library gives?",
        "2. In a real computation the masks come from CT images, whose voxels are rarely cubes:",
        "   typically about 1 mm across and 2-3 mm between slices. Is the voxel spacing applied",
        "   along the correct axes? Spacing applied along the wrong axis gives distances that",
        "   are wrong but still look plausible.",
        "",
        "## Method",
        "",
        f"**Data.** {n_cases:,} pairs of synthetic 3D masks, each a reference and a test,",
        "generated by the validation script from a fixed random seed, so that every run",
        f"produces the same pairs. Each mask sits in an array of {SHAPE[2]} × {SHAPE[1]} ×",
        f"{SHAPE[0]} voxels.",
        "",
        f"- *{len(EDGE_CASES)} edge cases:* identical masks; a single voxel each; two masks",
        "  that do not touch; an empty test; both empty; and a sheet one voxel thick.",
        f"- *{n_random:,} random pairs,* in four kinds of reference structure "
        f"({min(random_counts)}-{max(random_counts)} of each): organ-sized ellipsoids;",
        "  irregular shapes built from 2-4 random ellipsoids, often in separate pieces; small",
        "  structures 1-3 voxels in radius, comparable to a cochlea or the optic chiasm; and",
        "  large lobulated shapes. Each test is its reference moved by up to",
        f"  {MAX_SHIFT} voxels along each axis, then locally enlarged or eroded.",
        "",
        f"The pairs take {len(SPACINGS)} voxel spacings in turn (x × y × z, mm): "
        + ", ".join(_spacing(s) for s in isotropic)
        + " (isotropic); and "
        + ", ".join(_spacing(s) for s in anisotropic)
        + " (anisotropic, as in planning CT; the last differs along all three axes).",
        "",
        "**Comparisons.** Each pair is measured twice.",
        "",
        "1. *AutoSeg's copy against the library.* Both are given the same masks and spacing.",
        "   Compared: the distance from every surface element to the other surface, in both",
        "   directions, and every element's area; Dice; the Hausdorff distance at the 50th,",
        "   90th, 95th, 99th and 100th percentiles; the mean distance in each direction; and",
        f"   Surface Dice at 0, 0.5, 1, 2, 3 and 5 mm: {port_quantities} quantities per pair.",
        "2. *The metrics AutoSeg reports against the library.* The masks are given to AutoSeg",
        "   as images carrying their physical voxel spacing, exactly as in a computation run,",
        "   and its reported Dice, Hausdorff 100 % and 95 %, mean surface distance and Surface",
        f"   Dice at 1, 2 and 3 mm ({app_quantities} quantities per pair) are compared with the",
        "   library's. Because the spacing travels with the image, this is the comparison that",
        "   would catch it being applied along the wrong axes.",
        "",
        "**Criterion.** Every value must be exactly equal: no tolerance is allowed. Where both",
        "give an undefined value, as for an empty mask, that counts as agreement.",
        "",
        f"**Software.** {_software()}.",
        "",
        "**What AutoSeg reports.** Apart from Dice, each metric is first measured in two",
        "directions, from the reference surface to the test and from the test to the",
        "reference. AutoSeg combines the two as follows:",
        "",
        *md_table(
            ["Metric", "Measured in each direction as", "Reported as"],
            [
                ["Dice", "Not directional: 2 \\|R ∩ T\\| ÷ (\\|R\\| + \\|T\\|)", "One value"],
                [
                    "Hausdorff 100 %",
                    "The largest distance from one surface to the other",
                    "The larger of the two directions",
                ],
                [
                    "Hausdorff 95 %",
                    "The 95th percentile of distance, weighted by surface area",
                    "The larger of the two directions",
                ],
                [
                    "Mean surface distance",
                    "The average distance, weighted by surface area",
                    "The average of the two directions",
                ],
                [
                    "Surface Dice at τ",
                    "The surface area within τ of the other surface",
                    "Both directions pooled: (reference area within τ + test area within τ)"
                    " ÷ (total area of both)",
                ],
            ],
            "lll",
        ),
        "## Results",
        "",
        *md_table(
            ["Comparison", "Mask pairs", "Values compared", "Identical"],
            [
                [
                    "AutoSeg's copy against the library",
                    f"{n_cases:,}",
                    f"{port_tally.total:,}",
                    f"{port_tally.total_identical:,}",
                ],
                [
                    "The metrics AutoSeg reports against the library",
                    f"{n_cases:,}",
                    f"{app_tally.total:,}",
                    f"{app_tally.total_identical:,}",
                ],
            ],
            "lrrr",
        ),
        "By voxel spacing:",
        "",
        *md_table(
            ["Voxel spacing (mm)", "Mask pairs", "Copy: identical", "Reported metrics: identical"],
            _per_group(results, "spacing", SPACINGS, _spacing),
            "lrrr",
        ),
    ]
    if infinite_matched and undefined_matched:
        lines += [
            "Where the test mask is empty, both return an infinite Hausdorff distance and an",
            "undefined mean surface distance, so these agree too.",
            "",
        ]
    lines += ["## Findings", ""]
    if port_exact and app_exact:
        lines += [
            f"- **AutoSeg's copy of the library is exact.** All {port_tally.total + app_tally.total:,}",
            "  values agreed bit for bit, edge cases included, so the speed changes altered",
            "  nothing and AutoSeg's 3D metrics are the library's. AutoSeg can therefore carry",
            "  its own faster copy, without depending on the library being installed.",
        ]
    else:
        n_diff = len(port_tally.mismatches) + len(app_tally.mismatches)
        lines += [
            f"- **{n_diff:,} values differ from the library.** AutoSeg's copy cannot be taken",
            "  as equivalent until they are explained; the full results list them.",
        ]
    if spacing_exact:
        lines += [
            "- **The voxel spacing is applied along the correct axes.** At all",
            f"  {len(SPACINGS)} spacings, including the {len(anisotropic)} anisotropic ones, the",
            "  metrics AutoSeg reports equal the library's with the spacing given in the masks'",
            "  own axis order.",
        ]
    else:
        lines += [
            "- **At some spacings the reported metrics differ from the library's,** so the",
            "  spacing may not be applied along the correct axes; see the full results.",
        ]
    lines += [
        "- **The two directions are combined as in AutoSeg's 2D contour metrics**",
        "  (Supplementary 3): the larger direction for the Hausdorff distances and the",
        "  average for the mean distance. The library returns the two directional means",
        "  separately; averaging them is AutoSeg's choice, and the convention its 2D metrics",
        "  follow too. Surface Dice, which AutoSeg computes only in 3D, pools both directions",
        "  into one value.",
        "- **Limits.** The masks are synthetic and smaller than a planning CT, so they test",
        "  the calculation rather than any anatomy. On clinical structures the metrics also",
        "  depend on how faithfully contours are converted to masks, which Supplementary 2",
        "  assesses.",
        "",
        "## References",
        "",
        "1. google-deepmind. surface-distance: library to compute surface distance based",
        "   performance metrics for segmentation tasks. GitHub.",
        "   https://github.com/google-deepmind/surface-distance",
        "2. Nikolov S, Blackwell S, Zverovitch A, et al. Clinically applicable segmentation of",
        "   head and neck anatomy for radiotherapy: deep learning algorithm development and",
        "   validation study. J Med Internet Res. 2021;23(7):e26151.",
        "",
        "## Reproduce",
        "",
        "```",
        f"python scripts/{SCRIPT}",
        "```",
        "",
        "It needs the library itself (`pip install",
        "git+https://github.com/google-deepmind/surface-distance`). Every input is synthetic;",
        f"the run took {seconds:.0f} s. Regenerate this report rather than editing it.",
        "",
    ]
    target.write_text(unwrap(lines), encoding="utf-8")


def write_full_results(target: Path, results: dict, seconds: float) -> None:
    """Every table behind Supplementary 4."""
    port_tally, app_tally = results["port"], results["app"]
    lines = [
        f"# {TITLE}: full results",
        "",
        f"{stamp(SCRIPT)} · {_software()} · {results['cases']:,} mask pairs · {seconds:.0f} s.",
        f"The summary is [Supplementary 4]({REPORT.name}). Regenerate this file rather than",
        "editing it.",
        "",
        "## What was compared",
        "",
        "Dice, the Hausdorff distances, mean surface distance and Surface Dice are computed by",
        "`core/surface_distance.py`, a port of google-deepmind/surface-distance carried since",
        "v1. Since then the surface-area table is built once per spacing and reused, the",
        "library's element-by-element sort is replaced by a vectorised one giving the same",
        "order, and the application crops both masks to the pair's bounding box before calling",
        "it, the box the library crops to itself. Each is meant to change nothing.",
        "",
        "- **The port** (*AutoSeg's copy* in the report). The port's functions against the",
        "  package's, called identically on the same arrays and spacing.",
        "- **The application** (*the metrics AutoSeg reports*). `compute_geometric_metrics`,",
        "  as a computation run calls it, from SimpleITK images carrying physical spacing",
        "  (x, y, z), against the package on the full arrays with the spacing in their",
        "  (z, y, x) order. Mean surface distance is the equal-weight average of the package's",
        "  two directional means, as the application reports it.",
        "",
        "Every comparison is exact equality. Two undefined values (both NaN) count as equal.",
        f"Over both comparisons, {port_tally.both_infinite + app_tally.both_infinite:,} were",
        f"infinite in both and {port_tally.both_undefined + app_tally.both_undefined:,} undefined",
        "in both.",
        "",
        "## The cases",
        "",
        f"Arrays of {SHAPE[2]} × {SHAPE[1]} × {SHAPE[0]} voxels; seed {SEED}. Each random",
        f"reference is paired with a test made from it: moved by up to {MAX_SHIFT} voxels along",
        f"each axis, then enlarged or eroded by up to {MAX_LOCAL} random ellipsoids of radius",
        f"{LOCAL_RADIUS[0]:g}-{LOCAL_RADIUS[1]:g} voxels (radii in voxels throughout):",
        "",
        *md_table(
            ["Pair", "Count", "Built from"],
            [
                [
                    kind,
                    f"{results['kinds'][kind]:,}",
                    (
                        "hand-made"
                        if kind in EDGE_CASES
                        else (
                            f"{RANDOM_KINDS[kind][0] or '2-4'} ellipsoid(s), radius "
                            f"{RANDOM_KINDS[kind][1][0]:g}-{RANDOM_KINDS[kind][1][1]:g}"
                        )
                    ),
                ]
                for kind in results["kinds"]
            ],
            "lrl",
        ),
        "Voxel spacings, x × y × z in mm, taken in turn:",
        "",
        *md_table(
            ["Spacing", "Pairs"],
            [[_spacing(s), f"{n:,}"] for s, n in results["spacings"].items()],
            "lr",
        ),
        "## The port against the package, by quantity",
        "",
        *md_table(
            ["Quantity", "Comparisons", "Identical", "Largest difference"],
            _per_quantity(port_tally),
            "lrrr",
        ),
        "## The application's metric path against the package, by quantity",
        "",
        *md_table(
            ["Quantity", "Comparisons", "Identical", "Largest difference"],
            _per_quantity(app_tally),
            "lrrr",
        ),
        "## By voxel spacing",
        "",
        *md_table(
            ["Spacing (mm)", "Pairs", "Port: identical", "Application: identical"],
            _per_group(results, "spacing", SPACINGS, _spacing),
            "lrrr",
        ),
        "## By kind of pair",
        "",
        *md_table(
            ["Pair", "Pairs", "Port: identical", "Application: identical"],
            _per_group(results, "kind", list(results["kinds"]), str),
            "lrrr",
        ),
        "## The edge cases, value by value",
        "",
        "What the application reports and what the package gives, for each hand-made pair.",
        "",
    ]
    for kind, spacing, reported in results["edge_values"]:
        lines += [f"### {kind[0].upper() + kind[1:]} ({_spacing(spacing)} mm)", ""]
        lines += md_table(
            ["Quantity", "Application", "Package"],
            [[q, _value(ours), _value(theirs)] for q, ours, theirs in reported],
            "lrr",
        )
    mismatches = port_tally.mismatches + app_tally.mismatches
    if mismatches:
        lines += ["## Differences", ""]
        lines += [f"- {case}: {quantity}, {detail}" for case, quantity, detail in mismatches[:200]]
        if len(mismatches) > 200:
            lines += [f"- ... and {len(mismatches) - 200:,} more"]
        lines += [""]
    lines += [
        "## What this does not cover",
        "",
        "The masks are synthetic, so they exercise the computation rather than any clinical",
        "anatomy, and the arrays are smaller than a planning CT; nothing in the computation",
        "depends on array size beyond the crop. An earlier version of the port was compared",
        "with the package on a clinical head-and-neck sample by",
        "`scripts/validate_against_upstream.py`. The package compared is the one installed",
        "above.",
        "",
        "## Reproduce",
        "",
        "```",
        f"python scripts/{SCRIPT}",
        "```",
        "",
    ]
    target.write_text(unwrap(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, default=REPORT, help="Write Supplementary 4 here.")
    parser.add_argument(
        "--full-results", type=Path, default=FULL_RESULTS, help="Write its full results here."
    )
    parser.add_argument(
        "--cases", type=int, default=1000, help="Mask pairs in total, edge cases included."
    )
    args = parser.parse_args(argv)

    started = time.perf_counter()
    results = compare(max(0, args.cases - len(EDGE_CASES)))
    seconds = time.perf_counter() - started
    for path in (args.report, args.full_results):
        path.parent.mkdir(parents=True, exist_ok=True)
    write_report(args.report, results, seconds)
    write_full_results(args.full_results, results, seconds)
    differ = len(results["port"].mismatches) + len(results["app"].mismatches)
    print(
        f"wrote {args.report} and {args.full_results.name}: {results['cases']} pairs, "
        f"{differ} differences ({seconds:.0f} s)"
    )
    return 1 if differ else 0


if __name__ == "__main__":
    raise SystemExit(main())
