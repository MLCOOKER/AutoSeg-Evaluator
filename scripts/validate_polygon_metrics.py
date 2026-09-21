"""Run the polygon-metric suppliers' acceptance suites against the vendored copy.

The suppliers ship their acceptance scripts expecting their package to sit
beside them as a top-level import. Ours lives under
``autoseg_evaluator.vendor``. Rather than edit scripts that are covered by an
integrity manifest, this puts the vendor directory on ``sys.path`` so the
original name resolves, and runs them unmodified.

That shim is safe *here* and deliberately not used for the vendored unit tests.
These scripts run in a process of their own, so the top-level name is the only
route to those modules. The unit tests run in the same session as the
application, where a second import path would give one compiled library two
module identities, two ctypes handles and two caches — so those had their import
lines rewritten instead.

What it checks:

* **public** — 150 pairs from the published synthetic study, 3,000 numeric
  comparisons against audited golden values.
* **stress** — 44 geometric edge cases: irregular stars, thin and tiny
  structures, holes, disconnected components, coordinates translated to 1e12 mm,
  rotated tolerance plateaus, and quantile mass gaps that must be *refused*
  rather than scored.

This is the check a freshly compiled library needs. The hash pin in
``tests/test_vendor_integrity.py`` says a library is the one whose acceptance was
recorded; only running the suite says a *new* library computes the right
numbers. Builds are not reproducible, so a rebuild always needs this.

Usage::

    python scripts/validate_polygon_metrics.py
    python scripts/validate_polygon_metrics.py --suite public
"""

from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR = REPO_ROOT / "src" / "autoseg_evaluator" / "vendor"
SUPPLIER = REPO_ROOT / "third_party" / "native_contour_metrics" / "v0.2"

SUITES = {
    "public": SUPPLIER / "scripts" / "validate_public.py",
    "stress": SUPPLIER / "scripts" / "validate_stress.py",
}


def _run(name: str, script: Path, output_dir: Path | None) -> bool:
    """Execute one supplier script, reporting whether it passed.

    Run in-process rather than as a subprocess so the ``sys.path`` entry and the
    chosen interpreter are guaranteed to be the ones this script set up. The
    scripts signal failure by raising ``SystemExit`` with a non-zero code.
    """
    print(f"\n=== {name} " + "=" * (68 - len(name)))
    argv = [str(script)]
    if output_dir is not None:
        argv += ["--output-dir", str(output_dir / name)]
    saved = sys.argv
    sys.argv = argv
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exit_signal:
        if exit_signal.code:
            print(f"{name}: FAILED (exit {exit_signal.code})")
            return False
    finally:
        sys.argv = saved
    print(f"{name}: passed")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--suite",
        choices=[*SUITES, "all"],
        default="all",
        help="Which acceptance suite to run (default: all).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write the suppliers' JSON/CSV records (default: theirs).",
    )
    args = parser.parse_args(argv)

    if not (VENDOR / "native_contour_metrics_fast").is_dir():
        print(f"No vendored package at {VENDOR}", file=sys.stderr)
        return 2

    # Ahead of anything else, so the supplier scripts resolve their own name to
    # the vendored copy and never to an installed release of the same package.
    sys.path.insert(0, str(VENDOR))

    chosen = list(SUITES) if args.suite == "all" else [args.suite]
    results = {name: _run(name, SUITES[name], args.output_dir) for name in chosen}

    print("\n" + "=" * 72)
    for name, passed in results.items():
        print(f"  {name:10s} {'passed' if passed else 'FAILED'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
