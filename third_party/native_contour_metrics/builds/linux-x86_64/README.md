# Linux x86-64 library: build and validation record

`src/autoseg_evaluator/vendor/native_contour_metrics_fast/bin/linux-x86_64/libfast_native.so`
is ours, not the supplier's. The supplier delivered a validated Windows library
only (see `../../v0.2/platform_status.json`), so this one was built from the
vendored C++ with the supplier's own build script and validated with their
acceptance suites. `tests/test_vendor_integrity.py` pins it by hash, on the same
terms as the Windows library: the pin says this is the exact file whose
validation is recorded here, not that the build is reproducible.

| | |
|---|---|
| SHA-256 | `cbc3d6afb2ce1e4dcaa1900a011bad77a877c2a5bc34e165750f9620023bc895` |
| Built | 2026-10-08, [workflow run 37756980994](https://github.com/MLCOOKER/AutoSeg-Evaluator/actions/runs/37756980994), commit `6c5f8ef` |
| Workflow | [`.github/workflows/linux-library.yml`](../../../../.github/workflows/linux-library.yml) |
| Build image | `quay.io/pypa/manylinux_2_28_x86_64@sha256:39df0042d5cc900b085aa25a0659368b42a0006c54c474299b785b44c1b4ff82` (AlmaLinux 8, glibc 2.28) |
| Compiler | GCC 14.2.1, `-std=c++17 -O2 -fPIC -fno-fast-math -ffp-contract=off -shared -march=x86-64 -mtune=generic` |
| Source | `cpp/fast_native.cpp`, SHA-256 `5e723e0e…b45b`, as in the supplier's v0.2 manifest |
| Needs from the system | `libstdc++.so.6`, `libm.so.6`, `libgcc_s.so.1`, `libc.so.6`; newest symbols GLIBC_2.14 and GLIBCXX_3.4 |

## Validation

The suppliers' 150 published pairs (3,000 numeric comparisons) and 44 stress
cases, then the same 150 pairs through this application's own call path, with
the compiled engine forced. Run on three systems, because `hypot`, `asinh` and
`fma` come from each system's own maths library:

| System | glibc | Public (150 pairs) | Stress (44) | Our call path (150) |
|---|---|---|---|---|
| manylinux_2_28 (AlmaLinux 8) | 2.28 | passed | 0 failures | 0 failures |
| Ubuntu 22.04 | 2.35 | passed | 0 failures | 0 failures |
| Ubuntu 24.04 | 2.39 | passed | 0 failures | 0 failures |

The largest errors were identical on all three, and the same as the Windows
library's recorded acceptance: distance 4.86e-10 mm, APL 2.91e-10 mm,
normalised APL 1.67e-15.

## Files

- `build-record.json` — written by the supplier's build script: command,
  compiler, source and binary hashes.
- `system-requirements.txt` — what the library asks of the system, checked in
  the workflow against what manylinux_2_28 promises.
- `validation-*.log` — the acceptance output on each system.

CI validates the committed library again on every pull request and every push
to `main`.
