# Vendored numerical kernels — do not edit

Everything in this directory is supplied third-party code, reproduced **exactly
as delivered**. It is not written in this project's style and must not be
reformatted into it.

That is not fussiness. These packages are accepted against thresholds of
1e-10 mm, and once a reformatting pass has landed there is no way to tell it
apart from a numerical change. [`tests/test_vendor_integrity.py`](../../../tests/test_vendor_integrity.py)
checks every file here against the supplier's own SHA-256 manifest, in both
directions, so an edit fails the suite rather than quietly moving a published
result. Ruff is configured to skip this directory for the same reason — linting
it proposes hundreds of changes, none of which may be made.

If something here has to change, it changes upstream, comes back with a new
manifest, and the pinned hashes move with it.

| Package | Version | Role |
|---|---|---|
| `native_contour_metrics` | 0.1.0 | Pure-Python reference engine. Audit trail, differential cross-check, and the fallback on any platform with no compiled library. |
| `native_contour_metrics_fast` | 0.2.0.dev2 | Compiled continuous-envelope engine. The default, and roughly 60–150× faster. |

`bin/<platform>/` holds the compiled library. Only the validated Windows x64
build is committed; other platforms are built and validated in CI and are
gitignored. When none matches the running process, the loader raises and the
application falls back to the reference engine rather than loading anything
else — it never searches the system for a library.

Provenance, licences, the C++ source, the build script and the acceptance data
are in [`third_party/native_contour_metrics/`](../../../third_party/native_contour_metrics/).
What is integrated and why is in
[`docs/V3_POLYGON_METRICS_SPEC.md`](../../../docs/V3_POLYGON_METRICS_SPEC.md).
