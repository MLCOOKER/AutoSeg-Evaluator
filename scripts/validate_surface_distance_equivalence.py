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

Every comparison is exact equality: no tolerance. A clinical sample is compared
port-to-package by ``validate_against_upstream.py`` (``docs/VALIDATION_REPORT.md``).

Usage::

    python scripts/validate_surface_distance_equivalence.py \\
        --out docs/SURFACE_DISTANCE_EQUIVALENCE.md [--cases 1000]

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
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import SimpleITK as sitk

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "src"))

import surface_distance as deepmind  # noqa: E402
from validation_common import git_revision  # noqa: E402

# The package calls scipy.ndimage.morphology, deprecated in SciPy, and divides by
# zero on the empty-mask cases, where an undefined value is the expected answer.
warnings.filterwarnings("ignore", category=DeprecationWarning, module="surface_distance")
warnings.filterwarnings("ignore", category=RuntimeWarning, module="surface_distance")

from autoseg_evaluator.core import surface_distance as port  # noqa: E402
from autoseg_evaluator.core.metrics import compute_geometric_metrics  # noqa: E402
from autoseg_evaluator.core.tolerance_keys import tolerance_key  # noqa: E402

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

#: The random reference structures, by kind: (spheres in the union, radius range
#: in voxels).
RANDOM_KINDS = {
    "organ-sized ellipsoid": (1, (8.0, 16.0)),
    "irregular, several components": (None, (3.0, 8.0)),
    "small (1-3 voxels in radius)": (1, (1.2, 3.0)),
    "large, lobulated": (3, (6.0, 14.0)),
}


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
        shift = self.rng.integers(-3, 4, size=3)
        out = np.roll(mask, tuple(int(s) for s in shift), axis=(0, 1, 2))
        local = self.blob(int(self.rng.integers(0, 3)), (2.0, 6.0))
        return (out | local) if self.rng.random() < 0.5 else (out & ~local)

    def __iter__(self) -> Iterator[tuple[str, np.ndarray, np.ndarray]]:
        same = self.blob(2, (5.0, 12.0))
        yield "identical", same, same.copy()
        one_a, one_b = np.zeros(SHAPE, bool), np.zeros(SHAPE, bool)
        one_a[18, 20, 20] = one_b[18, 21, 22] = True
        yield "a single voxel each", one_a, one_b
        yield "disjoint", self.blob(1, (3.0, 5.0)), self.blob(1, (3.0, 5.0))
        yield "test empty", self.blob(2, (4.0, 9.0)), np.zeros(SHAPE, bool)
        yield "both empty", np.zeros(SHAPE, bool), np.zeros(SHAPE, bool)
        sheet = np.zeros(SHAPE, bool)
        sheet[10:20, 10:40, 25] = True
        yield "a sheet one voxel thick", sheet, np.roll(sheet, 1, axis=2)
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
    """Comparisons per quantity: how many, how many identical, the largest gap."""

    def __init__(self) -> None:
        self.count: Counter = Counter()
        self.identical: Counter = Counter()
        self.largest: dict[str, float] = defaultdict(float)
        self.mismatches: list[tuple[str, str, str]] = []

    def add(self, quantity: str, a: float, b: float, case: str) -> None:
        a, b = float(a), float(b)
        self.count[quantity] += 1
        if _same(a, b):
            self.identical[quantity] += 1
        else:
            self.mismatches.append((case, quantity, f"{a!r} against {b!r}"))
            if math.isfinite(a) and math.isfinite(b):
                self.largest[quantity] = max(self.largest[quantity], abs(a - b))

    def add_array(self, quantity: str, a: np.ndarray, b: np.ndarray, case: str) -> None:
        self.count[quantity] += 1
        if a.shape == b.shape and np.array_equal(a, b):
            self.identical[quantity] += 1
        else:
            self.mismatches.append((case, quantity, f"arrays differ ({a.shape} and {b.shape})"))


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
    reversed_changes: dict[str, list[float]] = {"hd95": [], "msd": []}
    for n, (kind, gt, test) in enumerate(Cases(n_random)):
        spacing = SPACINGS[n % len(SPACINGS)]
        kinds[kind] += 1
        spacings[spacing] += 1
        label = f"{kind} #{n} at {spacing}"

        ours = port.compute_surface_distances(gt, test, spacing)
        theirs = deepmind.compute_surface_distances(gt, test, spacing)
        for key, name in ARRAYS.items():
            port_tally.add_array(name, ours[key], theirs[key], label)
        dice = deepmind.compute_dice_coefficient(gt, test)
        port_tally.add("Dice", port.compute_dice_coefficient(gt, test), dice, label)
        for p in PERCENTS:
            port_tally.add(
                f"Hausdorff {p:g}%",
                port.compute_robust_hausdorff(ours, p),
                deepmind.compute_robust_hausdorff(theirs, p),
                label,
            )
        o_ab, o_ba = port.compute_average_surface_distance(ours)
        t_ab, t_ba = deepmind.compute_average_surface_distance(theirs)
        port_tally.add("Mean distance, reference → test", o_ab, t_ab, label)
        port_tally.add("Mean distance, test → reference", o_ba, t_ba, label)
        for tau in TOLERANCES_MM:
            port_tally.add(
                f"Surface Dice @ {tau:g} mm",
                port.compute_surface_dice_at_tolerance(ours, tau),
                deepmind.compute_surface_dice_at_tolerance(theirs, tau),
                label,
            )

        app = compute_geometric_metrics(_image(gt, spacing), _image(test, spacing), _app_config())
        app_tally.add("Dice", app["dice"], dice, label)
        app_tally.add(
            "Hausdorff 100%",
            app["hausdorff100"],
            deepmind.compute_robust_hausdorff(theirs, 100),
            label,
        )
        app_tally.add(
            "Hausdorff 95%",
            app["hausdorff95"],
            deepmind.compute_robust_hausdorff(theirs, 95),
            label,
        )
        app_tally.add(
            "Mean surface distance", app["mean_surface_distance"], _mean(t_ab, t_ba), label
        )
        for tau in APP_TOLERANCES_MM:
            app_tally.add(
                f"Surface Dice @ {tau:g} mm",
                app[tolerance_key("surface_dice", tau)],
                deepmind.compute_surface_dice_at_tolerance(theirs, tau),
                label,
            )

        # What passing the spacing in reverse (x, y, z) order would have done.
        if len(set(spacing)) > 1 and gt.any() and test.any():
            flipped = deepmind.compute_surface_distances(gt, test, tuple(reversed(spacing)))
            right_hd = deepmind.compute_robust_hausdorff(theirs, 95)
            wrong_hd = deepmind.compute_robust_hausdorff(flipped, 95)
            right_msd = _mean(t_ab, t_ba)
            wrong_msd = _mean(*deepmind.compute_average_surface_distance(flipped))
            if right_hd > 0 and math.isfinite(right_hd):
                reversed_changes["hd95"].append(abs(wrong_hd - right_hd) / right_hd * 100)
            if right_msd > 0 and math.isfinite(right_msd):
                reversed_changes["msd"].append(abs(wrong_msd - right_msd) / right_msd * 100)
    return {
        "port": port_tally,
        "app": app_tally,
        "kinds": kinds,
        "spacings": spacings,
        "reversed": reversed_changes,
        "cases": sum(kinds.values()),
    }


# ---- Report -------------------------------------------------------------------


def _version(*names: str) -> str:
    for name in names:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return "unknown"


def _table(headers: list[str], rows: list[list[str]], align: str) -> list[str]:
    marks = {"l": "---", "r": "---:"}
    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(marks[a] for a in align) + " |",
        *("| " + " | ".join(row) + " |" for row in rows),
        "",
    ]


def _tally_rows(tally: Tally) -> list[list[str]]:
    rows = []
    for quantity in tally.count:
        n, same = tally.count[quantity], tally.identical[quantity]
        gap = tally.largest.get(quantity, 0.0)
        rows.append([quantity, f"{n}", f"{same}", "0" if same == n else f"{gap:.3g}"])
    return rows


def _spacing(s: tuple[float, float, float]) -> str:
    z, y, x = s
    return f"{x:g} × {y:g} × {z:g}"


def write_report(target: Path, results: dict, seconds: float, revision: str) -> None:
    port_tally, app_tally = results["port"], results["app"]
    all_same = not port_tally.mismatches and not app_tally.mismatches
    n_cases = results["cases"]
    port_values = sum(port_tally.count.values())
    app_values = sum(app_tally.count.values())
    flips = results["reversed"]
    lines = [
        "# The surface-distance port against Google DeepMind's package",
        "",
        "Generated by `scripts/validate_surface_distance_equivalence.py`. Regenerate it",
        "rather than editing it.",
        "",
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · AutoSeg Evaluator, {revision} · "
        f"surface-distance {_version('surface-distance', 'Surface-Distance-Based-Measures')} · "
        f"numpy {_version('numpy')} · scipy {_version('scipy')} · {n_cases} mask pairs · "
        f"{seconds:.0f} s",
        "",
        "## In brief",
        "",
        (
            f"- **Every value is identical, bit for bit.** {port_values:,} comparisons of the port with "
            f"the package and {app_values:,} of the application's metric path with it, over "
            f"{n_cases:,} mask pairs at {len(SPACINGS)} voxel spacings, with no tolerance: every "
            "distance and area array, every metric, every percentile and tolerance, and the "
            "infinite and undefined values both return for empty masks."
            if all_same
            else f"- **{len(port_tally.mismatches) + len(app_tally.mismatches)} comparisons differ**; "
            "see *Differences* below."
        ),
        "- **The application path applies spacing along the right axes.** Its values equal the",
        "  package's with the spacing given in the arrays' (z, y, x) order at every anisotropic",
        "  spacing, where the reverse order gives different distances (below).",
        "",
        "## What is compared",
        "",
        "Dice, the Hausdorff distances, mean surface distance and Surface Dice are computed by",
        "`core/surface_distance.py`, a port of google-deepmind/surface-distance carried since",
        "v1. Since then the surface-area table is built once per spacing and reused, the",
        "library's element-by-element sort is replaced by a vectorised one giving the same",
        "order, and the application crops both masks to the pair's bounding box before calling",
        "it, the box the library crops to itself. Each is meant to change nothing.",
        "",
        "- **Port.** The port's functions against the package's, called identically on the same",
        "  arrays and spacing.",
        "- **Application.** `compute_geometric_metrics`, as a computation run calls it, from",
        "  SimpleITK images carrying physical spacing (x, y, z), against the package on the full",
        "  arrays with the spacing in their (z, y, x) order. Mean surface distance is the",
        "  equal-weight average of the package's two directional means, as the application",
        "  reports it.",
        "",
        "Every comparison is exact equality. Two undefined values (both NaN) count as equal.",
        "",
        "## The cases",
        "",
        f"Arrays of {SHAPE[2]} × {SHAPE[1]} × {SHAPE[0]} voxels; seed {SEED}. Each random",
        "reference is paired with a test made from it: moved by up to 3 voxels along each axis,",
        "then locally enlarged or eroded by up to two small random ellipsoids.",
        "",
        *_table(
            ["Pair", "Count"],
            [[kind, f"{n}"] for kind, n in results["kinds"].items()],
            "lr",
        ),
        "Voxel spacings, x × y × z in mm, taken in turn:",
        "",
        *_table(
            ["Spacing", "Pairs"],
            [[_spacing(s), f"{n}"] for s, n in results["spacings"].items()],
            "lr",
        ),
        "## Results",
        "",
        "### The port against the package",
        "",
        *_table(
            ["Quantity", "Comparisons", "Identical", "Largest difference"],
            _tally_rows(port_tally),
            "lrrr",
        ),
        "### The application's metric path against the package",
        "",
        *_table(
            ["Quantity", "Comparisons", "Identical", "Largest difference"],
            _tally_rows(app_tally),
            "lrrr",
        ),
    ]
    if flips["hd95"]:
        lines += [
            "## Why the axis order matters",
            "",
            "The package takes the spacing in the same axis order as the array. On the",
            f"{len(flips['hd95'])} anisotropic pairs with both masks drawn, giving it in",
            "reverse (x, y, z) order instead would have changed the 95 % Hausdorff distance",
            f"by up to {max(flips['hd95']):.1f} % (median {float(np.median(flips['hd95'])):.1f} %)",
            f"and the mean surface distance by up to {max(flips['msd']):.1f} % (median "
            f"{float(np.median(flips['msd'])):.1f} %). `core/metrics.py` records that v1 passed it in",
            "that order; the application comparison above holds the current order to the",
            "package's.",
            "",
        ]
    mismatches = port_tally.mismatches + app_tally.mismatches
    if mismatches:
        lines += ["## Differences", ""]
        lines += [f"- {case}: {quantity}, {detail}" for case, quantity, detail in mismatches[:50]]
        lines += [""]
    lines += [
        "## What this does not cover",
        "",
        "The masks are synthetic, so they exercise the computation rather than any clinical",
        "anatomy, and the arrays are smaller than a planning CT; nothing in the computation",
        "depends on array size beyond the crop. A clinical head-and-neck sample was compared",
        "port-to-package by `scripts/validate_against_upstream.py` (`docs/VALIDATION_REPORT.md`),",
        "with an earlier version of the port. The package compared is the one installed above.",
        "",
        "## Source",
        "",
        "Google DeepMind, surface-distance: https://github.com/google-deepmind/surface-distance",
        "(Apache License 2.0).",
        "",
        "Reproduce with:",
        "",
        "```",
        "python scripts/validate_surface_distance_equivalence.py \\",
        "    --out docs/SURFACE_DISTANCE_EQUIVALENCE.md",
        "```",
        "",
    ]
    target.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="Write the markdown report here.")
    parser.add_argument(
        "--cases", type=int, default=1000, help="Mask pairs in total, edge cases included."
    )
    args = parser.parse_args(argv)

    started = time.perf_counter()
    results = compare(max(0, args.cases - 6))
    seconds = time.perf_counter() - started
    write_report(args.out, results, seconds, git_revision())
    differ = len(results["port"].mismatches) + len(results["app"].mismatches)
    print(f"wrote {args.out}: {results['cases']} pairs, {differ} differences ({seconds:.0f} s)")
    return 1 if differ else 0


if __name__ == "__main__":
    raise SystemExit(main())
