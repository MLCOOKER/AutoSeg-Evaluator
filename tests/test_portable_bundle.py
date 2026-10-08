"""The portable bundles: what goes into them, and what the Linux one says it needs.

The bundles themselves are built and smoke-tested by the release workflow on
the platform each is for. These cover the pieces that can be wrong anywhere.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_build_portable():
    spec = importlib.util.spec_from_file_location(
        "build_portable", REPO_ROOT / "scripts" / "build_portable.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


bp = _load_build_portable()


def _names(lines):
    """Distribution names from requirement lines, normalised as pip does."""
    found = set()
    for line in lines:
        line = line.split("#", 1)[0].strip().strip('",')
        if line:
            found.add(re.split(r"[<>=!~;\[ ]", line, maxsplit=1)[0].lower().replace("_", "-"))
    return found


def test_requirements_txt_lists_every_runtime_dependency():
    """The bundles install from requirements.txt; a source install reads pyproject.toml.

    shapely was in one and not the other, so a bundle would have shipped
    without it and failed on the first contour read.
    """
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"^dependencies = \[(.*?)^\]", text, re.MULTILINE | re.DOTALL).group(1)
    declared = _names(block.splitlines())
    listed = _names((REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines())
    assert "shapely" in declared
    assert declared == listed


def _source_tree(root: Path) -> Path:
    bin_dir = root / "autoseg_evaluator" / "vendor" / "native_contour_metrics_fast" / "bin"
    for folder, library in (
        ("windows-x86_64", "fast_native.dll"),
        ("linux-x86_64", "libfast_native.so"),
        ("macos-arm64", "libfast_native.dylib"),
    ):
        (bin_dir / folder).mkdir(parents=True)
        (bin_dir / folder / library).write_bytes(b"\0")
    vendor = root / "autoseg_evaluator" / "vendor"
    for leftover in ("build/linux-x86_64/build.log", "evidence/builds/linux-x86_64.json"):
        (vendor / leftover).parent.mkdir(parents=True)
        (vendor / leftover).write_text("x")
    (root / "autoseg_evaluator" / "__pycache__").mkdir()
    (root / "autoseg_evaluator" / "__pycache__" / "app.cpython-311.pyc").write_bytes(b"\0")
    (root / "autoseg_evaluator" / "app.py").write_text("")
    return root / "autoseg_evaluator"


@pytest.mark.parametrize(
    ("platform", "library"),
    [("windows", "windows-x86_64/fast_native.dll"), ("linux", "linux-x86_64/libfast_native.so")],
)
def test_each_bundle_carries_only_its_own_library(tmp_path, platform, library):
    copied = tmp_path / "out"
    shutil.copytree(_source_tree(tmp_path / "src"), copied, ignore=bp.source_ignore(platform))
    files = {p.relative_to(copied).as_posix() for p in copied.rglob("*") if p.is_file()}
    assert files == {"app.py", f"vendor/native_contour_metrics_fast/bin/{library}"}


def test_the_bundle_names():
    assert bp.bundle_name("3.0.0", "windows") == "AutoSegEvaluator-v3.0.0"
    assert bp.bundle_name("3.0.0", "linux") == "AutoSegEvaluator-v3.0.0-linux-x86_64"


def test_the_linux_path_file_points_at_the_bundled_app(tmp_path):
    site = tmp_path / "python" / "lib" / "python3.11" / "site-packages"
    site.mkdir(parents=True)
    pth = bp.write_linux_path_file(tmp_path)
    assert pth.parent == site
    line = pth.read_text(encoding="ascii").strip()
    # How site.addpackage reads a relative line.
    assert Path(os.path.normpath(site / line)) == tmp_path / "app"


def test_the_linux_launchers_use_the_bundled_python(tmp_path):
    launcher = bp.write_linux_launcher(tmp_path)
    helper = bp.write_linux_menu_helper(tmp_path)
    text = launcher.read_text(encoding="ascii")
    assert text.startswith("#!/bin/sh\n")
    assert 'exec "$here/python/bin/python3" -m autoseg_evaluator "$@"' in text
    menu = helper.read_text(encoding="ascii")
    assert 'Exec="$here/run-autoseg-evaluator.sh"' in menu
    assert "Icon=$here/app/autoseg_evaluator/assets/icon.png" in menu
    assert (REPO_ROOT / "src" / "autoseg_evaluator" / "assets" / "icon.png").is_file()
    assert b"\r" not in launcher.read_bytes() + helper.read_bytes()
    if sys.platform != "win32":
        assert os.access(launcher, os.X_OK) and os.access(helper, os.X_OK)


def _wheel(site: Path, name: str, *tags: str) -> None:
    info = site / f"{name}-1.0.dist-info"
    info.mkdir(parents=True)
    lines = ["Wheel-Version: 1.0", *(f"Tag: {tag}" for tag in tags)]
    (info / "WHEEL").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_the_glibc_requirement_is_the_newest_any_wheel_needs(tmp_path):
    _wheel(tmp_path, "pure", "py3-none-any")
    _wheel(tmp_path, "legacy", "cp311-cp311-manylinux2014_x86_64")
    # Either tag will do, so this one needs only the older.
    _wheel(
        tmp_path, "either", "cp311-cp311-manylinux_2_28_x86_64", "cp311-cp311-manylinux_2_17_x86_64"
    )
    _wheel(tmp_path, "compressed", "cp311-cp311-manylinux_2_24_x86_64.manylinux_2_31_x86_64")
    assert bp.glibc_requirement(tmp_path) == (2, 24)
    _wheel(tmp_path, "qt", "cp39-abi3-manylinux_2_34_x86_64")
    assert bp.glibc_requirement(tmp_path) == (2, 34)


def test_the_glibc_requirement_never_falls_below_the_runtimes_own(tmp_path):
    _wheel(tmp_path, "pure", "py3-none-any")
    assert bp.glibc_requirement(tmp_path) == (2, 17)


def test_the_linux_readme_states_the_requirement_with_examples(tmp_path):
    readme = bp.write_linux_readme(tmp_path, "3.0.0", (2, 34)).read_text(encoding="ascii")
    assert "glibc 2.34 or newer" in readme
    assert "Ubuntu 22.04, Debian 12, RHEL / Rocky / AlmaLinux 9" in readme
    assert "./run-autoseg-evaluator.sh" in readme
    assert "\r" not in readme


def test_every_listed_distribution_ships_at_least_its_glibc():
    """Each example list is chosen for the smallest floor at or above a requirement."""
    assert bp.glibc_examples((2, 33)) == bp.GLIBC_EXAMPLES[(2, 34)]
    assert bp.glibc_examples((2, 99)) == ""
