# Native contour metrics — provenance and audit material

The runtime code lives in [`src/autoseg_evaluator/vendor/`](../../src/autoseg_evaluator/vendor/).
This directory holds everything *about* it that is not imported: the integrity
manifests, the C++ source of the compiled library, the build and validation
scripts, the synthetic acceptance data, and the suppliers' own notices.

Nothing here is duplicated from the vendor tree. The manifests below are the
suppliers' own, and [`tests/test_vendor_integrity.py`](../../tests/test_vendor_integrity.py)
checks the vendored files against them, in both directions.

## What is vendored

| Package | Version | Role |
|---|---|---|
| `native_contour_metrics` | 0.1.0 | Pure-Python reference engine. Audit trail, differential check, and the fallback wherever no compiled library exists. |
| `native_contour_metrics_fast` | 0.2.0.dev2 | Compiled continuous-envelope engine. The default. |

Both implement Boukerroui et al. (2023) Supplement A. See
[`docs/V3_POLYGON_METRICS_SPEC.md`](../../docs/V3_POLYGON_METRICS_SPEC.md) for
the definitions, the decisions taken, and every deliberate deviation.

## Layout

```
v0.1/
  MANIFEST.sha256.json          supplier manifest for the 0.1.0 package
  THIRD_PARTY_NOTICES.md
v0.2/
  MANIFEST.sha256.json          full release manifest, including the library
  SOURCE_MANIFEST.sha256.json   platform-independent sources only
  README.md                     the supplier's own release notes
  platform_status.json          which targets are built and validated
  THIRD_PARTY_NOTICES.md
  scripts/validate_public.py    150-pair acceptance against the golden values
  scripts/validate_stress.py    44 geometric stress cases
  data/                         compact synthetic fixtures + golden values
  validation/                   the independent distance oracle those scripts use
```

The C++ source and the build script are **not** here. They live beside the
package they build, at `src/autoseg_evaluator/vendor/{cpp,tools}/`, because the
supplier's build script resolves the source and the destination package from a
single root. Splitting them would leave a platform build writing its library
into a tree the loader never looks at. Both are still covered by the v0.2
manifest, and the integrity test checks them there.

Two manifests exist because they answer different questions. `SOURCE_MANIFEST`
covers the files that are identical on every platform, so a platform build can
add its own library without invalidating it. `MANIFEST` covers the whole
release, library included.

## Running the acceptance suites

The unit suites are part of the normal test run — `tests/vendor/` holds both
suppliers' test files, unmodified except for their import lines. The acceptance
scripts are heavier and run separately:

```bash
python third_party/native_contour_metrics/v0.2/scripts/validate_public.py
python third_party/native_contour_metrics/v0.2/scripts/validate_stress.py
```

They exit non-zero on failure. The 150-pair run takes about 30 seconds.

## Rebuilding the compiled library

Only needed for a platform with no library packaged. It must be built **on**
that platform — the script refuses a cross-host build before it starts a
compiler.

```bash
python src/autoseg_evaluator/vendor/tools/build_native_library.py
```

The flags are fixed by the script and must not be relaxed: `/fp:strict` on
MSVC, `-fno-fast-math -ffp-contract=off` on GCC and Clang, never `-ffast-math`,
`-Ofast`, `/fp:fast` or `-march=native`. Contraction is a *separate* control
from fast-math, and leaving it at the compiler default lets `a*b+c` fuse into an
FMA and move results. The explicit `std::fma` in the discriminant is deliberate
and stays.

**A rebuilt library is a different artefact.** The build is not reproducible —
identical source yields different bytes with identical numerical output, which
is how the supplier's own `dev1` and `dev2` Windows binaries differ. So a
rebuild has to be revalidated with the scripts above, and the hash pinned in
`tests/test_vendor_integrity.py` has to move with it. The pin does not assert
that the library matches the source beside it; it asserts that the library is
the exact one whose acceptance run was recorded.
