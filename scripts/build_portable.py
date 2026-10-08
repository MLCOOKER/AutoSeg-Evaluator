"""Build the portable, hospital-IT-friendly Python bundle for AutoSeg Evaluator.

Produces a self-contained distribution for the platform it runs on, Windows or
Linux x86-64, that requires no Python installation, no admin rights, no
registry writes, and no internet access at runtime. Every dependency is a
normal ``.py`` / ``.pyd`` / ``.so`` file under ``site-packages`` so hospital IT
can virus-scan and inspect each one — there is no frozen PyInstaller blob.

Windows bundle layout::

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

Linux bundle layout::

    AutoSegEvaluator-v{version}-linux-x86_64/
        python/                       CPython 3.11, python-build-standalone
            bin/python3
            lib/python3.11/site-packages/
                autoseg_evaluator_app.pth   puts ../app on the path
        app/
            autoseg_evaluator/
        run-autoseg-evaluator.sh      launcher
        add-to-applications-menu.sh   optional menu entry, with the icon
        README.txt
        LICENSE

Each launcher resolves Python relative to its own location, so it always uses
the bundled interpreter, never any system Python. Each bundle carries only its
own platform's compiled polygon-metric library.

Usage (from the repo root, on the platform to build for)::

    python scripts/build_portable.py
    python scripts/build_portable.py --out custom_dist
    python scripts/build_portable.py --no-zip       # leave the folder, skip the archive

The GitHub Actions release workflow calls this script on a ``windows-latest``
and an ``ubuntu-22.04`` runner and attaches the archives to the release.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

# Pin the Python version so the bundle is reproducible across build runs. Bump
# deliberately, not opportunistically. Both platforms ship the same one.
PY_VERSION = "3.11.9"
PY_ARCH = "amd64"
EMBED_URL = (
    f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-{PY_ARCH}.zip"
)
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

#: Linux has no python.org embeddable build. python-build-standalone's
#: relocatable CPython (the build uv installs) is the usual answer; this is its
#: 3.11.9, pinned by release and by the checksum that release publishes. It
#: ships pip and needs glibc 2.17 - the bundle as a whole needs whatever its
#: newest wheel does, which the build works out and states in the README.
LINUX_PYTHON_RELEASE = "20240814"
LINUX_PYTHON_ARCHIVE = (
    f"cpython-{PY_VERSION}+{LINUX_PYTHON_RELEASE}-x86_64-unknown-linux-gnu-install_only.tar.gz"
)
LINUX_PYTHON_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    f"{LINUX_PYTHON_RELEASE}/{LINUX_PYTHON_ARCHIVE.replace('+', '%2B')}"
)
LINUX_PYTHON_SHA256 = "9a332ba354f3b4e8a96a15db6b2805a7a31dcc1b6b9c1b7b93e5246949fbb50f"

#: The polygon-metric library each bundle carries; the others are left out.
LIBRARY_FOLDERS = {"windows": "windows-x86_64", "linux": "linux-x86_64"}

#: For the Linux README: distributions that ship at least each glibc.
GLIBC_EXAMPLES = {
    (2, 28): "Debian 10, RHEL / Rocky / AlmaLinux 8, Ubuntu 20.04",
    (2, 31): "Ubuntu 20.04, Debian 11, RHEL / Rocky / AlmaLinux 9",
    (2, 34): "Ubuntu 22.04, Debian 12, RHEL / Rocky / AlmaLinux 9",
    (2, 35): "Ubuntu 22.04, Debian 12, RHEL / Rocky / AlmaLinux 10",
    (2, 39): "Ubuntu 24.04, Debian 13, RHEL / Rocky / AlmaLinux 10",
}

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


def _write_version_file(app_pkg_dir: Path, version: str) -> Path:
    """Stamp ``_version.py`` into the bundled package so __init__ can read it.

    The portable bundle copies the package source instead of pip-installing it,
    so ``importlib.metadata.version`` has no dist-info to read at runtime. The
    package's ``__init__`` falls back to ``from ._version import __version__``;
    this writes that file with the build-time version.
    """
    target = app_pkg_dir / "_version.py"
    target.write_text(
        '"""Generated by scripts/build_portable.py - do not edit or commit."""\n'
        f'__version__ = "{version}"\n',
        encoding="ascii",
    )
    return target


def _download(url: str, dest: Path) -> None:
    print(f"    download: {url}")
    with urllib.request.urlopen(url) as response:  # noqa: S310 — trusted URL
        dest.write_bytes(response.read())


def _host_platform() -> str:
    """``windows`` or ``linux``: a bundle is built on the platform it is for."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    raise SystemExit("Portable bundles are built on Windows or Linux; macOS runs from source.")


def bundle_name(version: str, platform: str) -> str:
    """The folder (and archive) name. Windows keeps the name it has always had."""
    if platform == "windows":
        return f"AutoSegEvaluator-v{version}"
    return f"AutoSegEvaluator-v{version}-{LIBRARY_FOLDERS[platform]}"


def source_ignore(platform: str):
    """``shutil.copytree`` filter for the app source.

    Leaves out bytecode, a local native build's leftovers, and every other
    platform's compiled polygon-metric library.
    """
    keep = LIBRARY_FOLDERS[platform]

    def ignore(directory: str, names: list[str]) -> set[str]:
        here = Path(directory)
        skipped = {n for n in names if n == "__pycache__" or n.endswith((".pyc", ".pyo"))}
        if here.name == "vendor":
            skipped |= {"build", "evidence"} & set(names)
        if here.name == "bin" and here.parent.name == "native_contour_metrics_fast":
            skipped |= {n for n in names if n != keep}
        return skipped

    return ignore


def _copy_app(bundle: Path, platform: str, version: str) -> None:
    src_app = REPO_ROOT / "src" / "autoseg_evaluator"
    dst_app = bundle / "app" / "autoseg_evaluator"
    dst_app.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_app, dst_app, ignore=source_ignore(platform))
    # The bundle copies source rather than pip-installing, so there is no
    # dist-info metadata. Stamp the version into a _version.py the package's
    # __init__ falls back to (otherwise the title bar reads "0.0.0+unknown").
    _write_version_file(dst_app, version)


# ---- Windows ---------------------------------------------------------------


def _pth_filename() -> str:
    major, minor = PY_VERSION.split(".")[:2]
    return f"python{major}{minor}._pth"


def _write_launcher(bundle: Path) -> None:
    """Write the double-click .bat that launches the app from the bundle."""
    launcher = bundle / "Run AutoSeg Evaluator.bat"
    # CRLF line endings so Windows handles it natively even when extracted
    # from a zip on a Linux host. ``%~dp0`` is the directory of the .bat
    # itself, so the launcher works regardless of where the bundle is
    # extracted to. ``pythonw.exe`` (the windowless interpreter) launches the
    # GUI without a flashing console window; ``start`` detaches it so the
    # transient command window closes immediately.
    launcher.write_bytes(
        b"@echo off\r\n"
        b"rem AutoSeg Evaluator portable launcher\r\n"
        b"rem Runs entirely from this folder. No Python install required.\r\n"
        b'start "AutoSeg Evaluator" "%~dp0python\\pythonw.exe" -m autoseg_evaluator\r\n'
    )


def _write_shortcut_vbs(bundle: Path) -> None:
    """Write a VBScript that creates a Desktop shortcut with the app icon.

    A ``.bat`` can't carry an icon, so this optional helper builds a proper
    ``.lnk`` (which can). It resolves the bundle from its own location, so it
    works wherever the bundle was extracted. The Linux bundle has its own
    ``add-to-applications-menu.sh``; macOS runs from source and gets its dock
    icon from Qt.
    """
    vbs = bundle / "Create Desktop Shortcut.vbs"
    vbs.write_bytes(
        b"' Creates a Desktop shortcut to AutoSeg Evaluator (with the app icon).\r\n"
        b'Set fso = CreateObject("Scripting.FileSystemObject")\r\n'
        b'Set shell = CreateObject("WScript.Shell")\r\n'
        b"bundle = fso.GetParentFolderName(WScript.ScriptFullName)\r\n"
        b'desktop = shell.SpecialFolders("Desktop")\r\n'
        b'Set lnk = shell.CreateShortcut(desktop & "\\AutoSeg Evaluator.lnk")\r\n'
        b'lnk.TargetPath = bundle & "\\python\\pythonw.exe"\r\n'
        b'lnk.Arguments = "-m autoseg_evaluator"\r\n'
        b"lnk.WorkingDirectory = bundle\r\n"
        b'lnk.IconLocation = bundle & "\\app\\autoseg_evaluator\\assets\\icon.ico"\r\n'
        b'lnk.Description = "AutoSeg Evaluator"\r\n'
        b"lnk.Save\r\n"
        b'MsgBox "Shortcut created on your Desktop.", 64, "AutoSeg Evaluator"\r\n'
    )


def _write_readme(bundle: Path, version: str) -> None:
    """Write the bundle-local README.txt the user sees after extracting."""
    text = (
        f"AutoSeg Evaluator v{version} - portable bundle\r\n"
        "===============================================\r\n"
        "\r\n"
        "To launch the application:\r\n"
        '  Double-click "Run AutoSeg Evaluator.bat".\r\n'
        "\r\n"
        "Optional - put an icon on your Desktop:\r\n"
        '  Double-click "Create Desktop Shortcut.vbs". It adds an\r\n'
        '  "AutoSeg Evaluator" shortcut (with the app icon) to your\r\n'
        "  Desktop that you can also pin to the taskbar/Start menu.\r\n"
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
        "  *.vbs             Optional 'create Desktop shortcut' helper.\r\n"
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


def _build_windows(bundle: Path, out_dir: Path, *, keep_cache: bool) -> None:
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
    subprocess.check_call([str(py_dir / "python.exe"), str(get_pip), "--no-warn-script-location"])

    # ---- 4. Install runtime dependencies ------------------------------
    print("[4/6] installing runtime dependencies")
    _install_requirements(py_dir / "python.exe")


def _install_requirements(python: Path) -> None:
    subprocess.check_call(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-warn-script-location",
            "--no-cache-dir",
            "-r",
            str(REPO_ROOT / "requirements.txt"),
        ]
    )


# ---- Linux -----------------------------------------------------------------


def _site_packages(bundle: Path) -> Path:
    major, minor = PY_VERSION.split(".")[:2]
    return bundle / "python" / "lib" / f"python{major}.{minor}" / "site-packages"


def write_linux_path_file(bundle: Path) -> Path:
    """Put the bundle's ``app/`` on the interpreter's path, relative to itself.

    A relative line in a ``.pth`` file is read against ``site-packages``: up
    four levels (site-packages, python3.11, lib, python) is the bundle root.
    The Windows bundle does the same with ``..\\app`` in its ``._pth``.
    """
    target = _site_packages(bundle) / "autoseg_evaluator_app.pth"
    target.write_text("../../../../app\n", encoding="ascii")
    return target


def _write_executable(path: Path, text: str) -> None:
    path.write_bytes(text.encode("ascii"))
    path.chmod(0o755)


def write_linux_launcher(bundle: Path) -> Path:
    launcher = bundle / "run-autoseg-evaluator.sh"
    _write_executable(
        launcher,
        "#!/bin/sh\n"
        "# AutoSeg Evaluator portable launcher.\n"
        "# Runs entirely from this folder. No Python install required.\n"
        'here=$(cd "$(dirname "$0")" && pwd)\n'
        'exec "$here/python/bin/python3" -m autoseg_evaluator "$@"\n',
    )
    return launcher


def write_linux_menu_helper(bundle: Path) -> Path:
    """The Linux counterpart of ``Create Desktop Shortcut.vbs``.

    Writes the same freedesktop entry as ``scripts/install-linux-desktop.sh``
    does for a source install, pointed at this folder's launcher and icon.
    """
    helper = bundle / "add-to-applications-menu.sh"
    _write_executable(
        helper,
        "#!/bin/sh\n"
        "# Adds AutoSeg Evaluator, with its icon, to your applications menu.\n"
        "# The entry points at this folder: run this again if you move it.\n"
        "set -eu\n"
        'here=$(cd "$(dirname "$0")" && pwd)\n'
        'apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"\n'
        'mkdir -p "$apps"\n'
        'cat > "$apps/autoseg-evaluator.desktop" <<EOF\n'
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=AutoSeg Evaluator\n"
        "GenericName=Segmentation Quality Assessment\n"
        "Comment=Segmentation quality assessment for radiotherapy\n"
        'Exec="$here/run-autoseg-evaluator.sh"\n'
        "Icon=$here/app/autoseg_evaluator/assets/icon.png\n"
        "Terminal=false\n"
        "Categories=Science;MedicalSoftware;Education;\n"
        "StartupNotify=true\n"
        "StartupWMClass=autoseg-evaluator\n"
        "EOF\n"
        'update-desktop-database "$apps" >/dev/null 2>&1 || true\n'
        'echo "Added AutoSeg Evaluator to your applications menu."\n',
    )
    return helper


_MANYLINUX_LEGACY = {"manylinux1": (2, 5), "manylinux2010": (2, 12), "manylinux2014": (2, 17)}


def _tag_glibc(platform_tag: str) -> tuple[int, int] | None:
    """The glibc a wheel's platform tag needs; ``None`` if it needs none."""
    if platform_tag == "any":
        return (0, 0)
    found = re.fullmatch(r"manylinux_(\d+)_(\d+)_\w+", platform_tag)
    if found:
        return int(found.group(1)), int(found.group(2))
    for legacy, glibc in _MANYLINUX_LEGACY.items():
        if platform_tag.startswith(legacy + "_"):
            return glibc
    return None


def glibc_requirement(site_packages: Path) -> tuple[int, int]:
    """The oldest glibc every installed wheel runs on.

    Each wheel needs the least of its tags (any one will do); the bundle needs
    the most of those. A package built from source on this machine is tagged
    ``linux_x86_64`` and needs this machine's glibc.
    """
    host = os.confstr("CS_GNU_LIBC_VERSION") if hasattr(os, "confstr") else None
    host_glibc = tuple(int(p) for p in host.split()[1].split(".")[:2]) if host else (2, 17)
    needed = (2, 17)  # python-build-standalone's own floor
    for wheel in site_packages.glob("*.dist-info/WHEEL"):
        tags = [
            line.split(":", 1)[1].strip()
            for line in wheel.read_text(encoding="utf-8").splitlines()
            if line.startswith("Tag:")
        ]
        options = []
        for tag in tags:
            # A compressed tag set ("manylinux_2_17_x86_64.manylinux2014_x86_64")
            # offers each of its parts.
            for platform_tag in tag.rsplit("-", 1)[-1].split("."):
                glibc = _tag_glibc(platform_tag)
                options.append(host_glibc if glibc is None else glibc)
        if options:
            needed = max(needed, min(options))
    return needed


def glibc_examples(glibc: tuple[int, int]) -> str:
    """Distributions that ship at least ``glibc``, or ``""`` if none listed."""
    for floor in sorted(GLIBC_EXAMPLES):
        if floor >= glibc:
            return GLIBC_EXAMPLES[floor]
    return ""


def write_linux_readme(bundle: Path, version: str, glibc: tuple[int, int]) -> Path:
    major, minor = glibc
    examples = glibc_examples(glibc)
    distributions = f"\n    for example {examples}, or later" if examples else ""
    text = (
        f"AutoSeg Evaluator v{version} - portable bundle for Linux\n"
        "=========================================================\n"
        "\n"
        "To launch the application:\n"
        "  ./run-autoseg-evaluator.sh\n"
        "\n"
        "Optional - add it, with its icon, to your applications menu:\n"
        "  ./add-to-applications-menu.sh\n"
        "\n"
        "Requirements:\n"
        f"  - 64-bit x86 Linux with glibc {major}.{minor} or newer"
        f"{distributions}.\n"
        "    `ldd --version` shows yours.\n"
        "  - A desktop session (X11, or Wayland with XWayland).\n"
        "  - No Python installation, no administrator rights, no internet\n"
        "    connection at runtime.\n"
        "\n"
        'If it does not start and says the Qt platform plugin "xcb" could\n'
        "not be loaded, install the X11 libraries Qt needs:\n"
        "  Ubuntu / Debian:  sudo apt install libxcb-cursor0 libxkbcommon-x11-0\n"
        "  Fedora / RHEL:    sudo dnf install xcb-util-cursor libxkbcommon-x11\n"
        "\n"
        "What is in this folder?\n"
        "  python/   A self-contained CPython 3.11 runtime. Every dependency is\n"
        "            a plain .py / .so file under python/lib/python3.11/\n"
        "            site-packages/, so it can be scanned and inspected.\n"
        "  app/      The application source code.\n"
        "  *.sh      The launcher and the applications-menu helper.\n"
        "  LICENSE   Apache 2.0 license.\n"
        "\n"
        "Settings:\n"
        "  Preferences are stored in settings.json in this folder, so extract\n"
        "  it somewhere you can write to, such as your home folder or a USB\n"
        "  stick. Session files (.session.json) are saved wherever you choose.\n"
        "\n"
        "Documentation, source, and citation:\n"
        "  https://github.com/MLCOOKER/AutoSeg-Evaluator\n"
    )
    target = bundle / "README.txt"
    target.write_bytes(text.encode("ascii"))
    return target


def _build_linux(bundle: Path, out_dir: Path, *, keep_cache: bool) -> None:
    # ---- 1. CPython, relocatable --------------------------------------
    print(f"[1/6] CPython {PY_VERSION} (python-build-standalone {LINUX_PYTHON_RELEASE})")
    archive = out_dir / LINUX_PYTHON_ARCHIVE
    if not archive.exists() or not keep_cache:
        _download(LINUX_PYTHON_URL, archive)
    else:
        print(f"    cached: {archive.name}")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != LINUX_PYTHON_SHA256:
        raise RuntimeError(
            f"{archive.name} has SHA-256 {digest}, not the pinned {LINUX_PYTHON_SHA256}."
        )
    with tarfile.open(archive) as tf:
        # The archive's top-level folder is python/.
        extract = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        tf.extractall(bundle, **extract)

    # ---- 2. Put app/ on the path --------------------------------------
    print("[2/6] app path file")
    write_linux_path_file(bundle)

    # ---- 3. pip ships with this build -----------------------------------
    print("[3/6] pip: included")

    # ---- 4. Install runtime dependencies ------------------------------
    print("[4/6] installing runtime dependencies")
    _install_requirements(bundle / "python" / "bin" / "python3")


def build(out_dir: Path, *, make_zip: bool, keep_cache: bool) -> Path:
    """Build the portable bundle. Returns the path to the bundle folder."""
    platform = _host_platform()
    version = _read_version()
    name = bundle_name(version, platform)
    bundle = out_dir / name
    if bundle.exists():
        print(f"[clean] removing previous {bundle}")
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True)
    print(f"[init]  bundle root: {bundle}")

    if platform == "windows":
        _build_windows(bundle, out_dir, keep_cache=keep_cache)
    else:
        _build_linux(bundle, out_dir, keep_cache=keep_cache)

    # ---- 5. Copy the project source -----------------------------------
    print("[5/6] copying app source")
    _copy_app(bundle, platform, version)

    # ---- 6. Launcher + docs -------------------------------------------
    if platform == "windows":
        print("[6/6] launcher + shortcut helper + README + LICENSE")
        _write_launcher(bundle)
        _write_shortcut_vbs(bundle)
        _write_readme(bundle, version)
    else:
        glibc = glibc_requirement(_site_packages(bundle))
        print(
            f"[6/6] launcher + menu helper + README + LICENSE (needs glibc {glibc[0]}.{glibc[1]})"
        )
        write_linux_launcher(bundle)
        write_linux_menu_helper(bundle)
        write_linux_readme(bundle, version, glibc)
    shutil.copy2(REPO_ROOT / "LICENSE", bundle / "LICENSE")

    print(f"[ok]    bundle assembled at {bundle}")

    if make_zip:
        # A tarball on Linux, because a zip does not reliably keep the
        # launchers executable.
        fmt, suffix = ("zip", ".zip") if platform == "windows" else ("gztar", ".tar.gz")
        archive = out_dir / f"{name}{suffix}"
        if archive.exists():
            archive.unlink()
        print(f"[archive] {archive}")
        shutil.make_archive(str(out_dir / name), fmt, root_dir=out_dir, base_dir=name)
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
        help="Skip the final archive step (leave the folder for inspection)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Re-download the Python runtime + get-pip even if cached",
    )
    args = parser.parse_args(argv)

    out = REPO_ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    build(out, make_zip=not args.no_zip, keep_cache=not args.no_cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
