# Native metrics: platform loader and build update

**0.2.0.dev2 — 20 September 2026**

This revision addresses the receiving team's portability review. It provides a platform-aware loader, native build commands with explicit floating-point settings, platform-specific wheel packaging, portable acceptance tests and a manifest-refresh procedure. The continuous metric algorithm, C++ source, topology adapter and metric definitions are unchanged from the reviewed `0.2.0.dev1` efficiency package. That original package and its integrity manifest remain unchanged.

## Targets and actual validation status

| Requested platform | Selected relative library path | Status in this delivery |
|---|---|---|
| Windows x64 | `bin/windows-x86_64/fast_native.dll` | Rebuilt with MSVC; numerical and loader tests passed; binary included |
| Linux x86-64 | `bin/linux-x86_64/libfast_native.so` | Loader selection tested; native build and numerical validation pending |
| macOS Apple Silicon | `bin/macos-arm64/libfast_native.dylib` | Loader selection tested; native build and numerical validation pending |
| macOS Intel | `bin/macos-x86_64/libfast_native.dylib` | Loader selection tested; native build and numerical validation pending |

Paths are relative to `native_contour_metrics_fast/`. **This is not a delivery of four validated binaries.** Only a Windows execution environment was available. Mocked OS/architecture selection tests are explicitly distinct from running the native algorithm on that OS. The other three targets need their own build/test jobs; no SDKs or compilers for them were installed here.

The loader uses the current process architecture. On Apple Silicon, an arm64 Python selects the arm64 library, while an x86-64 Python under Rosetta selects the Intel library. There is no universal2 dylib in this release. Unsupported targets, 32-bit processes, missing binaries, loader errors and a missing C ABI symbol produce explicit errors. The loader searches only its package-relative target path; it never silently searches the system for a different binary. Initialization binds the C signature before publishing the cached library and is protected by a lock.

The API remains `compare()`, `prepare()` and `ROI`. The receiving team can vendor this revision unchanged; `api.py` no longer hardcodes a Windows filename. Package data includes the `.dll`, `.so` and `.dylib` patterns, and wheel creation uses a platform tag rather than `py3-none-any`.

## Floating-point contract

The reviewers are correct that implicit multiply-add contraction is a separate control from fast-math. The build script fixes these flags:

* **MSVC:** `/O2 /MT /LD /std:c++17 /fp:strict /EHsc`.
* **GCC/Clang:** `-std=c++17 -O2 -fPIC -fno-fast-math -ffp-contract=off`, plus the relevant shared-library and architecture options. Linux targets baseline x86-64 with `-march=x86-64 -mtune=generic`; macOS selects `-arch arm64` or `-arch x86_64` explicitly.

`-ffp-contract=off` disables the compiler's implicit fusion of expressions such as `a*b+c`. The explicit `std::fma` call in the discriminant calculation remains intentional and unchanged. The script does not interpolate `CXXFLAGS` or `LDFLAGS`, and removes MSVC's inherited `CL`/`_CL_` option variables so they cannot override the selected flags. Do not add `-ffast-math`, `-Ofast`, `/fp:fast` or architecture-specific tuning without separate validation.

The exact compiler defaults depend on compiler, language mode and target; GCC and Clang do not have identical defaults. The practical correction is to set the policy explicitly. See the primary documentation: [GCC contraction options](https://gcc.gnu.org/onlinedocs/gcc/Optimize-Options.html#index-ffp-contract) and [Clang floating-point controls](https://clang.llvm.org/docs/UsersManual.html#controlling-floating-point-behavior).

These controls reduce compiler-induced differences. They do **not** promise bitwise equality across operating systems: math-library implementations, including `hypot`, `asinh` and explicit `fma`, and other floating-point evaluation details still require testing. These GCC/Clang options are not a claim of complete equivalence to every MSVC `/fp:strict` behaviour. Acceptance uses the same audited numerical tolerances as the reviewed release. Default round-to-nearest/ordinary IEEE floating-point operation is assumed; application-wide changes to rounding or subnormal handling were not evaluated.

## Build and validate each target

Use a matching 64-bit Python interpreter and native toolchain. A compiler is needed when preparing a release, not by users who receive its prebuilt library. The script intentionally refuses to label a build for a different OS/architecture as a native build.

From the package root, after installing the application's compatible NumPy/Shapely versions and pytest:

```text
python tools/build_native_library.py
python scripts/validate_release.py
python tools/seal_release.py
```

Toolchains: MSVC C++ Build Tools on Windows; GCC or Clang with C++17 on Linux; Apple Clang/Xcode command-line tools on macOS. Windows discovers the installed x64 toolchain via `vswhere`, or accepts an existing x64 Native Tools environment. `--compiler /path/to/compiler` selects a compiler executable. `--target` may explicitly state the expected target and must match the running interpreter. Use separate native Intel/arm64 environments for the two macOS builds.

Build logs and the exact compiler version, flags, source hash and binary hash are retained under `build/<target>/` and `evidence/builds/`. The acceptance runner writes to `validation_runs/<target>/`; it does not alter shipped source or evidence. It executes:

1. **49 unit/loader/build-contract tests**, including the original 25 numerical/topology tests.
2. **150 public synthetic pairs / 3,000 numeric comparisons** against the audited native-polygon reference, with the previous distance/APL/NAPL acceptance thresholds and plane-count checks.
3. **44 geometric stress cases**, now portable with their Decimal reference, including rotated threshold plateaus and expected ambiguous-percentile refusals.

`seal_release.py` first verifies `SOURCE_MANIFEST.sha256.json`, then requires successful validation for the current binary and C++ source hashes. It copies the results into `evidence/platforms/<target>/`, updates platform status and refreshes `MANIFEST.sha256.json`. Thus adding a tested Linux/macOS binary needs no edits to vendored Python files, and changed binary checksums are handled explicitly. Rebuilding a library naturally changes its binary checksum even if source is identical; keep the new build and validation records with that binary. Do not retain an obsolete release manifest after rebuilding.

Before broad distribution, build against the oldest Linux runtime and macOS deployment target supported by the host application, and test in those environments. An arbitrary Linux build does not establish a manylinux compatibility level. Architecture selection alone does not establish compatibility with every OS version, C++ runtime or application bundler. Include the target-specific library subdirectory in frozen application builds.

## Wheel packaging

After a successful native build, a standard build frontend can make a wheel:

```text
python -m pip wheel --no-deps . --wheel-dir dist
```

The build backend requires `setuptools>=82` as a build-time dependency. NumPy and Shapely remain the runtime dependencies. Wheel creation refuses to proceed if the current target's native binary is absent. Windows wheel packaging was exercised locally and produced `native_contour_metrics_fast-0.2.0.dev2-py3-none-win_amd64.whl`; it contains the correctly located DLL and was tested from an extracted wheel. The `py3-none` component describes the ctypes interface, not compatibility with unsupported Python versions; project metadata still requires Python >=3.10.

Linux/macOS wheel builds and their deployment tags remain untested. Do not manually relabel a Linux wheel as manylinux or a single-architecture macOS binary as universal2. The included Windows wheel is also available under `dist/`.

## Evidence and unchanged numerical scope

On Windows, the rebuilt DLL passes all 49 tests, all 150 public cases and all 44 stress cases. The largest public-reference differences remain approximately **4.86e-10 mm for distances**, **2.91e-10 mm for APL**, and **1.67e-15 for NAPL**. These are observed regression differences, not arbitrary-input error guarantees.

The old clinical and performance results are retained under `evidence/prior_dev1/`; they were not rerun as new cross-platform performance measurements. The original efficiency explanation is in `docs/EFFICIENCY_REPORT_DEV1.md`, whose old loader/build instructions are superseded by this document. The old 0.1 mathematical reference remains separate and unchanged. The native metric kernel remains a validated floating-point algorithm without certified global roundoff intervals.

Machine-readable current status is `evidence/platform_status.json`. The source and release manifests distinguish the unchanged implementation files from platform-specific binaries and validation outputs. See `THIRD_PARTY_NOTICES.md` for dataset and implementation provenance.
