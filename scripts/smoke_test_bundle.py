"""Start a built portable bundle far enough to know it works, with no display.

Run it with the bundle's own interpreter, so every import resolves inside the
bundle rather than in this repository::

    dist/AutoSegEvaluator-v3.0.0/python/python.exe scripts/smoke_test_bundle.py
    dist/AutoSegEvaluator-v3.0.0-linux-x86_64/python/bin/python3 scripts/smoke_test_bundle.py

The release workflow runs it on both bundles before anything is attached to a
release. It checks that:

* every module of the application imports. A dependency missing from
  ``requirements.txt`` fails here. The bundles install from that file while a
  source install reads ``pyproject.toml``, which is how a v3 bundle would
  otherwise have shipped without shapely;
* the version was stamped, and the main window opens off-screen, themed;
* the 2D metrics run on the compiled engine, and the bundled library is
  byte-identical to the one the repository pins and validates.

Stops at the first failure, naming it, with a non-zero exit.
"""

from __future__ import annotations

import argparse
import importlib
import os
import pkgutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _fail(message: str) -> None:
    print(f"FAILED: {message}")
    raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--allow-source",
        action="store_true",
        help="Run against the repository's own source (for testing this script).",
    )
    args = parser.parse_args(argv)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import autoseg_evaluator

    package = Path(autoseg_evaluator.__file__).resolve().parent
    if not args.allow_source and (REPO_ROOT / "src") in package.parents:
        _fail(f"imported the repository's source ({package}), not the bundle's")
    print(f"package   {package}")

    if autoseg_evaluator.__version__ == "0.0.0+unknown":
        _fail("the version was not stamped into the bundle")
    print(f"version   {autoseg_evaluator.__version__}")

    names = [
        m.name for m in pkgutil.walk_packages(autoseg_evaluator.__path__, "autoseg_evaluator.")
    ]
    broken = []
    for name in names:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - every failure is reported
            broken.append(f"{name}: {exc!r}")
    if broken:
        _fail("modules that do not import:\n  " + "\n  ".join(broken))
    print(f"imports   {len(names)} modules")

    from PySide6.QtWidgets import QApplication

    from autoseg_evaluator.ui.main_window import MainWindow
    from autoseg_evaluator.ui.theme import apply_theme
    from autoseg_evaluator.utils.settings import load_settings

    app = QApplication.instance() or QApplication(sys.argv[:1])
    settings = load_settings()
    apply_theme(app, theme=settings.get("theme", "light_blue.xml"))
    window = MainWindow(settings=settings)
    window.show()
    app.processEvents()
    print(f"window    {window.windowTitle()!r}")

    from autoseg_evaluator.core.polygon_metrics import ENGINE_FAST, select_engine
    from autoseg_evaluator.vendor.native_contour_metrics_fast import loader

    shipped = loader.library_path()
    if not shipped.is_file():
        _fail(f"no compiled polygon-metric library at {shipped}")
    loader.library()  # loads it and binds every function, or raises
    engine = select_engine()
    if engine.name != ENGINE_FAST:
        _fail(f"the 2D metrics would run on the {engine.name} engine")
    committed = REPO_ROOT / "src" / shipped.relative_to(package.parent)
    if shipped.read_bytes() != committed.read_bytes():
        _fail(f"{shipped.name} differs from the committed {committed}")
    print(f"engine    {engine.label}, {shipped.name} identical to the committed library")

    print("passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
