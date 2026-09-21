"""The vendored numerical kernels are byte-identical to what was supplied.

These packages are reproduced exactly rather than adapted into house style,
because their acceptance thresholds run to 1e-10 mm and a reformatting pass is
indistinguishable from a numerical change once it has landed. That promise is
only worth anything if something enforces it, which is what this module does:
every vendored file is checked against the supplier's own SHA-256 manifest, so
an edit — however well intentioned — fails the suite instead of quietly moving
a published result.

The compiled library is pinned separately and deliberately. Its build is *not*
reproducible: identical C++ source compiled twice yields different bytes with
identical output, which is how the supplier's own ``dev1`` and ``dev2`` Windows
binaries differ. So a hash here cannot mean "matches the source"; it means
"is the exact file whose numerical acceptance was recorded". A rebuilt library
is a different artefact and has to be revalidated, not waved through.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR = REPO_ROOT / "src" / "autoseg_evaluator" / "vendor"
MANIFESTS = REPO_ROOT / "third_party" / "native_contour_metrics"

#: The Windows library whose validation is recorded in
#: ``third_party/native_contour_metrics/v0.2/platform_status.json``: 150 pairs,
#: 3,000 numeric comparisons, 44 stress cases, maximum distance error
#: 4.86e-10 mm. Reproduced independently in this environment before vendoring.
WINDOWS_LIBRARY = "native_contour_metrics_fast/bin/windows-x86_64/fast_native.dll"
WINDOWS_LIBRARY_SHA256 = "037c2f0352aa05e03c1bccab569a1746c7acf958cd533db57704dbe18ae6e1ca"

#: Ours, not the suppliers', so no manifest covers them.
OUR_OWN = {"__init__.py", "README.md"}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(name: str) -> dict[str, str]:
    return json.loads((MANIFESTS / name).read_text(encoding="utf-8"))


def _vendored(prefix: str, manifest: dict[str, str]) -> list[tuple[str, str]]:
    return sorted((k, v) for k, v in manifest.items() if k.startswith(prefix))


@pytest.mark.parametrize(
    ("manifest_name", "prefix"),
    [
        ("v0.1/MANIFEST.sha256.json", "native_contour_metrics/"),
        ("v0.2/MANIFEST.sha256.json", "native_contour_metrics_fast/"),
        # The compiled library's source and its build script sit in the vendor
        # tree rather than beside the manifests, because the supplier's build
        # script resolves both from one root. Vendoring them keeps that script
        # runnable unmodified.
        ("v0.2/MANIFEST.sha256.json", "cpp/"),
        ("v0.2/MANIFEST.sha256.json", "tools/"),
    ],
)
def test_every_vendored_file_matches_the_suppliers_manifest(manifest_name, prefix):
    entries = _vendored(prefix, _manifest(manifest_name))
    assert entries, f"{manifest_name} covers nothing under {prefix}"

    changed = []
    for relative, expected in entries:
        path = VENDOR / relative
        assert path.is_file(), f"vendored file is missing: {relative}"
        if _digest(path) != expected:
            changed.append(relative)
    assert not changed, (
        "Vendored files differ from the supplied package: "
        + ", ".join(changed)
        + ". These are reproduced byte-identical on purpose; change them upstream "
        "and bring back a new manifest rather than editing them here."
    )


def test_nothing_unaccounted_for_sits_in_the_vendor_tree():
    """An extra file here would be code nobody has attested to.

    Checked in the other direction from the manifest comparison above, which
    can only see files it already knows about.
    """
    covered = set()
    for name in ("v0.1/MANIFEST.sha256.json", "v0.2/MANIFEST.sha256.json"):
        covered |= set(_manifest(name))

    present = {p.relative_to(VENDOR).as_posix() for p in VENDOR.rglob("*") if p.is_file()}
    present -= {p for p in present if "__pycache__" in p}

    assert present - covered - OUR_OWN == set(), (
        "Files in the vendor tree that no supplier manifest covers: "
        + ", ".join(sorted(present - covered - OUR_OWN))
    )


def test_the_compiled_library_is_the_one_that_was_validated():
    """Pinned by hash, because its build is not reproducible.

    Identical source compiled twice gives different bytes and identical numbers,
    so this cannot verify the library matches the C++ beside it. What it does
    verify is that nobody has swapped in a library whose acceptance run nobody
    has seen.
    """
    library = VENDOR / WINDOWS_LIBRARY
    if not library.is_file():  # pragma: no cover - non-Windows checkouts
        pytest.skip("Windows library not present in this checkout")
    assert _digest(library) == WINDOWS_LIBRARY_SHA256, (
        "The compiled library is not the validated build. A rebuild produces "
        "different bytes and needs its own acceptance run before the pin moves."
    )


def test_the_build_script_still_resolves_the_source_and_the_package():
    """Both have to sit under one root, or a platform build writes nowhere useful.

    The supplier's build script takes a single ``root`` and reads
    ``cpp/fast_native.cpp`` and ``native_contour_metrics_fast/platforms.py``
    beneath it, then writes the library into that same package. Splitting the
    source away from the package would leave the script writing a library into
    a tree the loader never looks at — which fails as a wrong answer in CI
    rather than as an error.
    """
    assert (VENDOR / "cpp" / "fast_native.cpp").is_file()
    assert (VENDOR / "tools" / "build_native_library.py").is_file()
    assert (VENDOR / "native_contour_metrics_fast" / "platforms.py").is_file()
