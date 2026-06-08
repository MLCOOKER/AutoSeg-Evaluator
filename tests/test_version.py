"""Version-resolution regression tests.

Guards the portable-bundle bug where the window title showed
``0.0.0+unknown``: the bundle copies the package source instead of
pip-installing it, so ``importlib.metadata`` has no dist-info and the
package must fall back to a build-time ``_version.py``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import autoseg_evaluator

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load_build_portable():
    spec = importlib.util.spec_from_file_location("build_portable", _SCRIPTS / "build_portable.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_version_is_resolved():
    """Installed package resolves a real version, not the unknown fallback."""
    v = autoseg_evaluator.__version__
    assert v, "version is empty"
    assert v != "0.0.0+unknown", "metadata version did not resolve"
    assert v[0].isdigit(), f"unexpected version string: {v!r}"


def test_build_portable_version_matches_pyproject():
    """The bundle stamps the same version the package declares in pyproject."""
    bp = _load_build_portable()
    assert bp._read_version() == bp._read_version()  # deterministic
    assert bp._read_version()[0].isdigit()


def test_write_version_file_is_importable(tmp_path):
    """_version.py written by the build is a valid, importable fallback."""
    bp = _load_build_portable()
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    target = bp._write_version_file(pkg, "9.9.9")
    assert target.exists()

    spec = importlib.util.spec_from_file_location("pkg._version", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.__version__ == "9.9.9"
