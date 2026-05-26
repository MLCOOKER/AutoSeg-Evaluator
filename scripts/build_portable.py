"""Build the portable, hospital-IT-friendly Python bundle for AutoSeg Evaluator.

Produces a self-contained Windows distribution that requires no Python
installation, no admin rights, no registry writes, and no internet access at
runtime. Every dependency is a normal ``.py`` / ``.pyd`` file in
``python\\Lib\\site-packages\\`` so hospital IT can virus-scan and inspect
each one — there is no frozen PyInstaller blob.

Bundle layout::

    AutoSegEvaluator-v{version}/
        python/                       CPython 3.11 embeddable distribution
            python.exe
            python311.dll
            python311.zip             stdlib
            Lib/site-packages/        PySide6, pydicom, SimpleITK, numpy, ...
            python311._pth            search-path config (patched by us)
        app/
            autoseg_evaluator/        the project source, copied from src/
        Run AutoSeg Evaluator.bat     double-click launcher
        README.txt                    bundle-specific quick-start
        LICENSE                       Apache 2.0

The ``.bat`` launcher resolves Python via ``%~dp0python\\python.exe`` so it
always uses the bundled interpreter, never any system Python.

Usage (from the repo root)::

    python scripts/build_portable.py
    python scripts/build_portable.py --out custom_dist
    python scripts/build_portable.py --no-zip       # leave the folder, skip the .zip

The GitHub Actions release workflow calls this script on a ``windows-latest``
runner and uploads the resulting ``.zip`` as a release asset.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

# Pin the embeddable Python version so the bundle is reproducible across
# build runs. Bump deliberately, not opportunistically.
PY_VERSION = "3.11.9"
PY_ARCH = "amd64"
EMBED_URL = (
    f"https://www.python.org/ftp/python/{PY_VERSION}/"
    f"python-{PY_VERSION}-embed-{PY_ARCH}.zip"
)
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_version() -> str:
    """Read the project version from pyproject.toml without importing tomllib."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    in_project = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
            continue
        if in_project and line.startswith("version"):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError("Could not find [project] version in pyproject.toml")


def _download(url: str, dest: Path) -> None:
    print(f"    download: {url}")
    with urllib.request.urlopen(url) as response:  # noqa: S310 — trusted URL
        dest.write_bytes(response.read())


def _pth_filename() -> str:
    major, minor = PY_VERSION.split(".")[:2]
    return f"python{major}{minor}._pth"


def _write_launcher(bundle: Path) -> None:
    """Write the double-click .bat that launches the app from the bundle."""
    launcher = bundle / "Run AutoSeg Evaluator.bat"
    # CRLF line endings so Windows handles it natively even when extracted
    # from a zip on a Linux host. ``%~dp0`` is the directory of the .bat
    # itself, so the launcher works regardless of where the bundle is
    # extracted to.
    launcher.write_bytes(
        b"@echo off\r\n"
        b"rem AutoSeg Evaluator portable launcher\r\n"
        b"rem Runs entirely from this folder. No Python install required.\r\n"
        b"start \"AutoSeg Evaluator\" \"%~dp0python\\python.exe\" -m autoseg_evaluator\r\n"
    )


def _write_readme(bundle: Path, version: str) -> None:
    """Write the bundle-local README.txt the user sees after extracting."""
    text = (
        f"AutoSeg Evaluator v{version} - portable bundle\r\n"
        "===============================================\r\n"
        "\r\n"
        "To launch the application:\r\n"
        "  Double-click \"Run AutoSeg Evaluator.bat\".\r\n"
        "\r\n"
        "Requirements:\r\n"
        "  - Windows 10 or 11 (64-bit).\r\n"
        "  - No Python installation required.\r\n"
        "  - No administrator rights required.\r\n"
        "  - No registry writes, no installer.\r\n"
        "  - No internet connection required at runtime.\r\n"
        "\r\n"
        "What is in this folder?\r\n"
        "  python\\           A self-contained CPython 3.11 runtime.\r\n"
        "                    Every dependency is a plain .py / .pyd file\r\n"
        "                    under python\\Lib\\site-packages\\ so hospital\r\n"
        "                    IT teams can virus-scan and inspect them.\r\n"
        "  app\\              The application source code.\r\n"
        "  *.bat             Launcher (invokes the bundled Python).\r\n"
        "  LICENSE           Apache 2.0 license.\r\n"
        "\r\n"
        "Settings:\r\n"
        "  Preferences are stored in settings.json next to the .bat.\r\n"
        "  Session files (.session.json) are saved wherever the user\r\n"
        "  chooses. Nothing is written outside this folder unless the\r\n"
        "  user explicitly exports results to a different location.\r\n"
        "\r\n"
        "USB / network-share deployment:\r\n"
        "  This bundle is portable. You can extract it to a USB stick\r\n"
        "  or shared drive and launch it from there. No installation\r\n"
        "  is performed.\r\n"
        "\r\n"
        "Documentation, source, and citation:\r\n"
        "  https://github.com/MLCOOKER/AutoSeg-Evaluator\r\n"
    )
    (bundle / "README.txt").write_bytes(text.encode("ascii"))


def build(out_dir: Path, *, make_zip: bool, keep_cache: bool) -> Path:
    """Build the portable bundle. Returns the path to the bundle folder."""
    version = _read_version()
    bundle_name = f"AutoSegEvaluator-v{version}"
    bundle = out_dir / bundle_name
    if bundle.exists():
        print(f"[clean] removing previous {bundle}")
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True)
    print(f"[init]  bundle root: {bundle}")

    # ---- 1. CPython embeddable ----------------------------------------
    print(f"[1/6] CPython {PY_VERSION} embeddable")
    py_dir = bundle / "python"
    py_dir.mkdir()
    embed_zip = out_dir / f"python-{PY_VERSION}-embed-{PY_ARCH}.zip"
    if not embed_zip.exists() or not keep_cache:
        _download(EMBED_URL, embed_zip)
    else:
        print(f"    cached: {embed_zip.name}")
    with zipfile.ZipFile(embed_zip) as zf:
        zf.extractall(py_dir)

    # ---- 2. Patch the ._pth file to enable site + extra import paths --
    # The embeddable distribution ships with site disabled and a minimal
    # search path. We need it to:
    #   * find the stdlib (the bundled .zip)
    #   * find packages installed by pip (Lib\site-packages)
    #   * find the project source we copy into ..\app
    # ``import site`` is required so that .pth files inside site-packages
    # (e.g. PySide6's Qt plugin discovery) are processed at startup.
    print(f"[2/6] patching {_pth_filename()}")
    pth_path = py_dir / _pth_filename()
    pth_path.write_text(
        f"python{PY_VERSION.split('.')[0]}{PY_VERSION.split('.')[1]}.zip\n"
        ".\n"
        "Lib\\site-packages\n"
        "..\\app\n"
        "import site\n",
        encoding="ascii",
    )

    # ---- 3. Bootstrap pip into the embedded Python --------------------
    print("[3/6] bootstrapping pip")
    get_pip = out_dir / "get-pip.py"
    if not get_pip.exists() or not keep_cache:
        _download(GET_PIP_URL, get_pip)
    subprocess.check_call(
        [str(py_dir / "python.exe"), str(get_pip), "--no-warn-script-location"]
    )

    # ---- 4. Install runtime dependencies ------------------------------
    print("[4/6] installing runtime dependencies")
    req = REPO_ROOT / "requirements.txt"
    subprocess.check_call(
        [
            str(py_dir / "python.exe"),
            "-m",
            "pip",
            "install",
            "--no-warn-script-location",
            "--no-cache-dir",
            "-r",
            str(req),
        ]
    )

    # ---- 5. Copy the project source -----------------------------------
    print("[5/6] copying app source")
    src_app = REPO_ROOT / "src" / "autoseg_evaluator"
    dst_app = bundle / "app" / "autoseg_evaluator"
    dst_app.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        src_app,
        dst_app,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )

    # ---- 6. Launcher + docs -------------------------------------------
    print("[6/6] launcher + README + LICENSE")
    _write_launcher(bundle)
    _write_readme(bundle, version)
    shutil.copy2(REPO_ROOT / "LICENSE", bundle / "LICENSE")

    print(f"[ok]    bundle assembled at {bundle}")

    if make_zip:
        archive = out_dir / f"{bundle_name}.zip"
        if archive.exists():
            archive.unlink()
        print(f"[zip]   {archive}")
        shutil.make_archive(
            str(archive.with_suffix("")),
            "zip",
            root_dir=out_dir,
            base_dir=bundle_name,
        )
        size_mb = archive.stat().st_size / (1024 * 1024)
        print(f"[done]  {archive.name} ({size_mb:.1f} MB)")

    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out",
        default="dist",
        help="Output directory (default: dist/)",
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Skip the final .zip step (leave the folder for inspection)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Re-download the embeddable + get-pip even if cached",
    )
    args = parser.parse_args(argv)

    out = REPO_ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    build(out, make_zip=not args.no_zip, keep_cache=not args.no_cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
