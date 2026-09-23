# Native polygon metrics — implementation spec

Roadmap item #5, Stream B. Six metrics computed directly on the line segments
RTSTRUCT stores, with no rasterisation anywhere in the path.

Written before the code, so the decisions and every deliberate deviation from
the supplied reference are reviewable rather than discovered later.

**Status: phases 1–6 landed 2026-09-22.** Both engines are vendored, the adapter
reads real RTSTRUCTs and measures them, every acceptance layer passes —
including the published archives driven from DICOM through our own grid builder
and parser — and the worker computes the metrics in a run, verified on the real
multi-vendor sample cohort, Tab 5 offers them, and the results table, Report tab
and audit sidecar all carry them. Phase 7 landed 2026-09-24: mask APL is
removed, and added path length now comes only from this stream.

**Revision 3, 2026-09-20.** Updated for `0.2.0.dev2`, which answers the
portability review: a platform-aware loader, explicit floating-point build
flags, platform wheels and refreshed manifests. The numerical kernel is
unchanged. D6 is rewritten — the packaging half is delivered, the binaries half
is Windows-only.

*Revision 2 (2026-09-20)* moved to the compiled `0.2.0.dev1` engine, withdrawing
D2 and replacing the output schema. *Revision 1 (2026-09-19)* planned around the
v0.1 sampling engine alone.

---

## 1. What is being integrated

Two packages implementing the same definitions, from

> Boukerroui D, Vasquez Osorio E, Brunenberg E, Gooding MJ. *Analytic
> calculations and synthetic shapes for validation of quantitative contour
> comparison software.* PIRO 26 (2023), 100436.

| | `native_contour_metrics` 0.1.0 | `native_contour_metrics_fast` 0.2.0.dev2 |
|---|---|---|
| Delivered | 16 Sep 2026 | 20 Sep 2026 (`dev1` engine, `dev2` packaging) |
| Method | uniform arclength sampling at `2·error_mm` | compiled continuous distance envelope |
| Implementation | pure Python + Shapely | Python + a 219-line C++17 library via `ctypes` |
| Output bounds | discretisation intervals, provable in exact arithmetic | **none** — analytic floating evaluation |
| Role here | audit reference, and fallback where no binary exists | default engine |

Neither is Gooding's Chapter-15 code. That is preserved in the v0.1 handoff
under `upstream/` and both packages deliberately depart from it — most sharply
on the symmetric median, which Chapter 15 takes as the *mean* of the two
directional medians where the paper takes the *maximum*. The handoff records an
example where those differ by 2.57 mm. We follow the paper.

### Verified here, independently, before planning

On this project's environment — **Shapely 2.0.6**, which the 0.2 supplier
explicitly flags as not rerun on their side:

| Check | v0.1 | v0.2 `dev1` | v0.2 `dev2` |
|---|---|---|---|
| Unit tests | 56/56 | 25/25 | **49/49** (24 new loader tests) |
| Public acceptance | 150/150 | 150/150 | 150/150, 3,000 comparisons |
| Max distance error vs audited golden | — | 4.856186563984011e-10 mm | **identical to `dev1`** |
| Stress cases | — | 44/44 | 44/44, 0 failures |
| Integrity manifests | 97 entries clean | — | 70 + 39 entries, 0 mismatched |
| Suite wall time | 399 s (supplier's machine) | 31.7 s | 32.3 s (this machine) |

Every `*_failures.json` in both evidence folders is an empty list.

**The `dev2` Windows binary is a different file from `dev1`'s** — `b1b4f6f…`
became `037c2f0…` — while the C++ source is byte-identical (`5e723e0…` in both).
They rebuilt it with the same flags; output is bit-identical, so it is a
non-reproducible link rather than a change. It matters only because "validated"
attaches to a hash: the integrity test pins **`037c2f0352aa05e03c1bccab569a1746
c7acf958cd533db57704dbe18ae6e1ca`**, and a rebuild invalidates that assertion
even when the numbers do not move.

A differential re-run against v0.1 on this project's own geometry still holds:
all four distance metrics inside v0.1's intervals, APL agreeing to 5.5e-12 mm,
38.3 ms per parotid-scale pair.

---

## 2. Frozen definitions

Reference `R` and test `T` boundaries on a common plane; `d(x,T)` is the
distance from a point on `R` to the nearest point of any target boundary **on
that same plane**. Hole boundaries count. Millimetres throughout.

| Output | Definition | Symmetrisation |
|---|---|---|
| APL(τ) | reference arclength where `d(x,T) > τ` | none — directional |
| NAPL(τ) | APL ÷ total reference length | none — directional |
| HD100 | continuous supremum of `d` | max of the two directions |
| HD95 | 95th arclength quantile | max of the two directions |
| Mean contour distance | arclength mean of `d` | **mean** of the two directions |
| Median contour distance | 50th arclength quantile | max of the two directions |

Quantiles use the lower generalised inverse, `Q(p) = inf{t : F(t) ≥ p}`.
Arclength is the weighting measure — never vertex count, never per-plane means
averaged equally. Directions are never pooled into one distribution.

**APL's direction is the clinical one.** The reference is the manual or accepted
contour, so APL is the length of correct boundary the auto contour failed to
cover — the length someone has to draw. In this application GT is the reference
and the vendor contour is the test, which maps directly. It must never be
silently swapped.

Distance metrics use only the **intersection** of the two plane sets
(`missing_plane_policy="exclude"`, the paper profile; both packages default to
`"error"` and require the choice to be explicit). APL counts reference-only
planes in full, and NAPL's denominator includes every reference plane.

---

## 3. Measurements this plan is based on

Taken here against real data and real geometry, not assumed.

### 3.1 The strict parser accepts 335 of 389 real vendor ROIs (86 %)

Over the seven vendor RTSTRUCTs of the HN1 sample series:

| Rejection | Count |
|---|---|
| Missing contour sequence | 32 |
| Ambiguous overlapping `CLOSED_PLANAR` loops | 22 |

The 32 are genuinely empty ROIs, not a parser limit: 389 − 32 = 357, exactly the
count both mask rasteriser backends convert. The two streams agree on emptiness.

Every one of the 22 was classified: **all nested, none partially overlapping.**
They include `Brain`, `Lung_L`, `Lung_R`, `Larynx_SG`, `Supraglottic Larynx`,
`Skeleton`, `Body`. Running v0.2's opt-in compatibility parser over the same
cohort with `allow_nested=True` returns **357 of 389** — precisely the mask
path's count, with no partial-overlap refusals.

### 3.2 The two engines, same geometry, same machine

| Case | v0.1 | v0.2 | Ratio |
|---|---:|---:|---:|
| Parotid-scale, 4,753 mm boundary | 2,113 ms at `error_mm`=0.05 | **36.7 ms** | 58× |
| " | 5,743 ms at `error_mm`=0.01 | " | 156× |
| Brain-scale, 50,392 mm boundary | 21,086 ms at 0.05 | **524 ms** | 40× |
| " | **RuntimeError: sample limit** at 0.01 | " | — |

v0.2 adds a one-off `prepare()` of 28 ms / 184 ms per ROI, cached and reused
across every comparison that ROI takes part in.

**v0.1's failure mode is structural, not a tuning problem.** Its sampling step is
`2·error_mm` and its two-million-point budget is shared across *distinct* plane
pairs. The supplier's own benchmark never hit it because synthetic planes repeat
and collapse under multiplicity; real anatomy has no two identical planes.

### 3.3 The two engines agree

Cross-validated here rather than taken on trust — an envelope algorithm and a
sampling algorithm are independent enough for this to mean something:

- All four distance metrics from v0.2 fell **inside v0.1's discretisation
  intervals** on the parotid-scale case.
- APL agreed to **3.6e-12 mm** on values near 4,000 mm — about 1e-15 relative —
  across three tolerances and both directions, with zero Decimal fallbacks.

### 3.4 `error_mm` no longer means anything about precision

Measured across 0.001, 0.01, 0.05 and 0.5 on the parotid-scale case: **36.1–36.9
ms and bit-identical results**. In v0.2 it only sets the width at which a
mass-ambiguous quantile is refused. It is not a sampling step, not a precision
control, and not a speed control.

---

## 4. Decisions

### D1 — Nested rings are composed as holes, unconditionally

> **Superseded by D10.** Holes are still read this way, now by the shared
> reading both streams use, not by `polygon_compat`.

`polygon_compat.parse_compatible(..., allow_nested=True)` composes rings that are
strictly nested, after checking every ring is valid and either strictly nested or
fully disjoint. Touching, duplicate, crossing and partially overlapping rings
still refuse. The source dataset is not modified.

Revision 1 called the nested reading "unambiguous". **That overstated it**, and
the supplier is right to push back: DICOM documents holes through a keyhole
contour or `CLOSEDPLANAR_XOR`, and a nested ordinary `CLOSED_PLANAR` loop is not,
by geometry alone, proof that the inner ring was intended as a hole. The
*geometry* is unambiguous; the *encoded intent* is not.

The decision stands anyway, for a reason revision 1 did not have. The supplier's
substantive warning is not to make a native-only reinterpretation that turns a
backend difference into an apparent metric difference. **Both of our mask
backends already compose these rings even-odd** — `image_blank[z_index] ^=
slice_arr` in the legacy path and `volume[z][rr, cc] ^= True` in the continuous
one, commented "produces holes for donut shapes". So composing in the polygon
path makes the two streams agree on 22 ROIs; refusing would make them disagree,
with the mask path reporting a donut and the polygon path reporting nothing.
This is not a new interpretation — it is the one this application has applied
since v1, now applied consistently.

**Revised 2026-09-22: there is no toggle.** The composition is unconditional.

The opt-in was the wrong shape, and the question that exposed it was simply *if
the mask path already does this automatically, why does the polygon path need a
switch?* It does not. The toggle could never prevent the assumption — the mask
path makes it on every run — it could only decide whether the two streams made
the **same** assumption. Off, they did not: 22 structures carried mask metrics
and no polygon metrics, which is a backend difference wearing the costume of a
metric difference. That is precisely what the supplier warned against, and the
opt-in caused it rather than avoiding it.

Confirmed by measurement rather than by reading the rasteriser: MVision's
`Brain`, one of the 22, rasterises to a mask containing genuine enclosed
background — 10 voxels across 2 slices. The mask path has produced holes for
these structures since v1, so every published result from this software already
rests on the interpretation.

With composition unconditional, the polygon adapter accepts **357 of 389** ROIs
on the reference cohort — exactly the mask path's count, with the only remaining
refusals being the 32 genuinely empty ROIs.

**One divergence remains, and is documented rather than hidden.** Rings that
touch, cross or partially overlap are refused here and silently combined by the
mask path. Those have no single reading, and refusing beats guessing — but it
means a structure can carry mask metrics and no polygon metrics. None were found
in the reference cohort.

### D2 — *Withdrawn.* `error_mm` is no longer a precision or speed setting

Revision 1 set `error_mm = 0.05` to buy speed. §3.4 shows the setting now has no
effect on either cost or value, so the rationale is gone. The default returns to
the supplier's **0.001**, which is the strictest ambiguity refusal, and there is
no precision control in Tab 5.

### D3 — Mask APL is removed separately, after this lands

`apl_mean` / `apl_total` stay for now. Polygon APL ships first so the two can be
compared on real data before anything is deleted. Removal is then its own change,
carrying the Methods, PROJECT_OVERVIEW, README and tooltip rewrite and the
session-key migration together.

> **Done (2026-09-24, phase 7).** Removed from the metric code, the Compute tab,
> Tab 2's inter-observer dialog, the results and CSV columns, the Report tab,
> the tests and the upstream validator. The "session-key migration" turned out
> to be a settings one: sessions never stored metric settings. `settings.json`
> drops `apl_mean`, `apl_total` and `apl_tolerance_mm` on load. Anyone needing
> the mask values for comparison can check out `7b6cec1`, the last commit that
> computes them.

### D4 — Tab 5 puts the two geometry methods side by side

Two rows: 3D mask and 2D contour as equal columns, DVH full width beneath.

### D7 — The two engines get separate precision settings

Both kernels take an argument called `error_mm` and it does not mean the same
thing in either one. Passing the same value to both — which this integration did
at first — costs three orders of magnitude.

In the compiled engine it is only the width at which a quantile sitting on a
rounding-sensitive gap is refused rather than resolved arbitrarily. Measured
from 0.001 to 0.5 it changes neither cost nor result by a single bit, so the
strictest setting is free: **`QUANTILE_GUARD_MM = 0.001`**.

In the reference engine it is the *sampling step*, and cost is inversely
proportional to it. At 0.001 a single real parotid pair took **29.7 seconds**
against the compiled engine's 34 ms, and a brain-sized structure exhausts the
two-million-sample budget outright. **`REFERENCE_SAMPLING_MM = 0.02`** brings
that pair to 1.94 s while holding the discretisation interval to ±0.02 mm —
still an order of magnitude finer than the voxel the mask metrics are quantised
to. Measured against the compiled engine on that same parotid: HD100 exact, mean
within 1.6e-6 mm, median within 1.3e-4 mm, APL within 2.8e-14 mm.

The engine and its settings are recorded with every result, because a number
that cannot be traced to the precision it was measured at cannot be reproduced.

### D5 — Two engines ship; the compiled one is the default

Mirrors the existing rasteriser pattern (`continuous` default, `legacy` opt-in,
`AUTOSEG_RASTERISER` to switch). Here: `fast` default, `reference` opt-in, via
`AUTOSEG_POLYGON_ENGINE`.

The reference engine earns its place three times over: it is the audit trail the
supplier asks us to keep, it is the differential check behind §3.3, and it is the
automatic fallback on any platform with no compiled binary. Its brain-scale
limitation is stated where a user can meet it.

`0.2.0.dev2` is a prototype by its own label. Shipping it as the default is
defensible because it reproduces the published acceptance set exactly and agrees
with the independently-written v0.1 to 1e-12 mm — but the version is recorded on
every result row so a number can always be traced to the engine that produced it.

### D6 — Windows ships a supplied binary; Linux is built and validated in CI

`0.2.0.dev2` resolved the portability review. Delivered and verified here:

- **A platform-aware loader.** `platforms.py` maps the running interpreter to one
  of four targets and `loader.py` loads only `bin/<target>/<library>` — it never
  searches system paths. A test plants a legacy `bin/fast_native.dll`, sets the
  host to Linux and fails if `CDLL` is called at all, so a missing binary can
  never silently load the wrong one. Initialisation takes a lock and publishes
  the handle only after every ctypes binding succeeds, which matters because the
  metrics worker runs on a QThread. Failed initialisation is not cached.
- **The floating-point contract.** `-fno-fast-math -ffp-contract=off` on
  GCC/Clang, `/fp:strict` on MSVC, the intentional `std::fma` retained, no
  `-march=native`, and no interpolation of inherited `CXXFLAGS` / `CL`. A test
  pins the flags *and their order* — fast-math would otherwise re-enable
  contraction, so the negation must precede the explicit off.
- **Platform wheels and refreshed manifests**, so nothing in the vendored package
  needs patching. `api.py` no longer hardcodes a Windows filename.

**What is not delivered is three of the four binaries.** The supplier states this
plainly — `platform_status.json` records `binary_included: false` and
`native_validation_passed: false` for Linux x86-64, macOS arm64 and macOS
x86-64, and the README says "this is not a delivery of four validated binaries".
Their selection tests for those targets mock the OS, which is explicitly not the
same as running the algorithm on it.

So:

| Target | Binary from | Validated by |
|---|---|---|
| Windows x86-64 | supplier, vendored | supplier + reproduced here |
| Linux x86-64 | **built in CI** on `ubuntu-latest` | their validation scripts, per push |
| macOS arm64 / x86-64 | none | — falls back to the reference engine |

**Linux is built in CI rather than requested from the supplier.** `ubuntu-latest`
ships g++; `tools/build_native_library.py` is one command and refuses cross-host
builds, which is correct on a native runner; `scripts/validate_public.py` and
`validate_stress.py` are one command each and run in well under a minute. That is
strictly better than a handed-over binary: it revalidates on the actual platform
on every change, which is the only way to catch the per-OS math-library
differences the supplier warns about — `hypot`, `asinh` and `fma` come from the
platform, and they are explicit that the build flags do **not** promise bitwise
equality across operating systems. It also keeps binaries out of git. Anyone who
needs a prebuilt Linux copy takes the CI artifact.

The consequence to accept: a CI-built Linux binary is validated by us, not by
them. Our CI must therefore run *their* acceptance scripts, not merely our own
test suite.

macOS is not in CI today and is left to the fallback engine until someone needs
it; adding `macos-latest` later is cheap and the build script already supports
both architectures. **`linux-aarch64` is not a supported target** — `platforms.py`
maps `aarch64 → arm64` but has no `linux-arm64` entry, so ARM Linux raises and
falls back rather than loading anything wrong.

Where no binary matches the platform, D5's reference engine runs instead and the
UI says which engine produced the numbers.

### D8 — References that point at nothing are set aside, not treated as mismatches

*Added after the first end-to-end run on real data (2026-09-23).* On the tender
H&N cohort (10 patients, 7 structure sets each), every 2D metric in the run came
back `unavailable: contours not readable: Frame mismatch or ambiguous ROI`. The
3D metrics on the same rows were fine.

The parser checks two references before it reads a contour: the ROI's
`ReferencedFrameOfReferenceUID` must equal the image series' frame, and every
`ContourImageSequence` entry must name the slice the contour lies on. Six of the
seven vendors satisfy both. The Varian (Eclipse) structure sets satisfy neither,
in all ten patients:

- They declare exactly one frame in `ReferencedFrameOfReferenceSequence`, and it
  is the CT's. Every ROI then names a *different* frame UID that the file never
  declares and that appears nowhere else in it. That breaks DICOM's own rule: a
  per-ROI frame must be one of the declared ones.
- None of their 47,495 contour image references name a slice in the loaded CT,
  although their series reference does name it.

This is the signature of a structure set exported against a copy of the CT whose
UIDs were remapped differently from the copy distributed with it. When Varian is
the ground truth, every row fails.

(RaySearch and Radformation carry a different PatientID from the CT, an
anonymisation alias. The metadata layer already merges those by Frame of
Reference, and their references all resolve. They are not part of this.)

The mask stream reads neither reference. It places every contour from its
coordinates, which is why it was unaffected.

**Decision.** A reference that *contradicts* the image is a refusal. A reference
to something *absent* from the data is unverifiable, and it is set aside. The
vendored parser does not tell the two apart. `grid['sops'].get(uid)` returns
`None` for a missing slice, and `None != z`. The adapter separates them before
the parser runs:

- **An undeclared per-ROI frame** is read in the structure set's declared frame.
  This only happens when the set declares exactly one frame and that frame is
  the image series'. There is no other frame it could mean.
- **Image references of which none resolve** are dropped from a one-ROI view
  handed to the parser. The rule is all-or-nothing: if some resolve and others
  do not, the structure is refused as ambiguous.

Still refused, each with its own message instead of the parser's combined one:
a declared frame that is not the series' (a structure drawn on another image);
an undeclared frame in a set declaring several; references that resolve to the
*wrong* slice; an ROI number listed more than once.

Once references are set aside, placement rests on geometry the parser still
enforces. Every contour must lie within 0.001 mm of a slice plane and inside the
image bounds. That is stricter than the mask stream, which has neither check.
Setting references aside therefore brings the polygon stream up to the mask
stream's evidence for placement, and no further.

The cached dataset is never modified, because it is shared with the mask path
and with every other pair. The view carries the original data elements by
reference, so it copies no coordinates. What was set aside is recorded per
structure in the audit sidecar under `references_set_aside`, in words and
without a UID.

**Measured on the cohort** (every ROI of all 70 structure sets, through the
adapter):

| Vendor | Parsed before | Parsed after | Still refused, and why |
|---|---|---|---|
| Varian | **0 / 447** | **435 / 447** | 11 out of CT bounds, 1 no contour sequence |
| A | 478 / 478 | 478 / 478 | — |
| B | 861 / 866 | 861 / 866 | 5 self-intersecting rings |
| C | 674 / 675 | 674 / 675 | 1 no contour sequence |
| D | 773 / 773 | 773 / 773 | — |
| Radformation | 892 / 896 | 892 / 896 | 4 out of CT bounds |
| RaySearch | 1030 / 1033 | 1030 / 1033 | 2 out of CT bounds, 1 self-intersecting ring |

The other six vendors are unchanged ROI for ROI. Their structure sets are
consistent, so the adapter passes them to the parser as loaded. Varian's twelve
remaining refusals are the same kinds the other vendors produce. Before, the
frame check hid them. Every Eye_L, Parotid_R and mandible across all seven
vendors now parses: 196 of 196, including Varian's 26 of 26.

---

## 5. Where the code goes

As built in phase 1:

```
src/autoseg_evaluator/vendor/          __init__.py + README.md are ours; the rest is not
  native_contour_metrics/                v0.1 reference engine, 10 files
  native_contour_metrics_fast/           v0.2.0.dev2 default engine
    bin/windows-x86_64/fast_native.dll     vendored, pinned by hash
    bin/linux-x86_64/libfast_native.so     built in CI, gitignored
  cpp/fast_native.cpp                    the library's source, 219 lines
  tools/build_native_library.py          rebuilds it, per platform
third_party/native_contour_metrics/    manifests, notices, acceptance data, scripts
tests/vendor/                          both suppliers' suites, 105 tests
tests/test_vendor_integrity.py         enforces "byte-identical", both directions
scripts/validate_polygon_metrics.py    runs the acceptance suites against our copy
```

To come in phase 2:

```
src/autoseg_evaluator/core/
  contour_grid.py                       CT headers -> grid, cached per series
  polygon_metrics.py                    ROI -> planes -> engine -> row keys
```

`cpp/` and `tools/` sit **inside** the vendor tree rather than with the other
audit material, because the supplier's build script resolves the source and the
destination package from one root. Separating them would leave a platform build
writing its library into a tree the loader never reads — a wrong answer rather
than an error. Both are covered by the v0.2 manifest either way.

**Vendored, not restyled.** Acceptance is 1e-10 mm on APL; reformatting code that
dense is how a silent numerical change happens. The repo already has this pattern
— the continuous rasteriser adapted from dcmrtstruct2nii, with `NOTICE`.

Integrity is pinned two ways, because they answer different questions. The
supplier's `SOURCE_MANIFEST.sha256.json` (39 entries) covers the
platform-independent sources, so a platform build can add its binary without
invalidating it; `MANIFEST.sha256.json` (70 entries) covers the whole release.
Our test asserts the vendored sources against the source manifest and the
Windows binary against its own recorded hash. A CI-built Linux binary is not
hash-pinned — it is validated by running the supplier's acceptance suite against
it, which is the check that actually matters for a freshly compiled library.

`shapely>=2.0,<3` becomes a **declared** dependency. It is currently installed
only transitively.

### 5.1 The grid

`scripts/prepare_fixtures.py` in the v0.1 handoff is the working model:

- `basis` — `ImageOrientationPatient` reshaped to (2,3), plus their cross product
  as the third column.
- slices sorted by projection onto that normal; `dz` = median of the diffs.
- `spacing = [PixelSpacing[1], PixelSpacing[0], dz]`, `size = [Columns, Rows, n]`.
- `sops` — `{SOPInstanceUID: zero-based index}`.

It **asserts regular spacing**. `contour_grid` must detect irregularity and refuse
with a stated reason rather than compute on a wrong `dz`. The supplier notes this
is an adapter limitation and not a mathematical one — the planar length
definitions do not require constant slice thickness — so it is a refusal we could
lift later, not a property of the metrics.

The library already holds `files` in DICOM order and `sop_instance_uids` per
series, so the grid is built by re-reading slice headers with
`stop_before_pixels=True`, once per series, cached beside `_ct_cache`.

### 5.2 The adapter

`core/polygon_metrics.py` owns everything the suppliers call integration
responsibility: ROI selection by `ROINumber`, plane identity, D1's opt-in ring
composition, engine selection per D5, the call, and the flattening of the result
into row keys. The vendored kernels see only validated millimetre polygons.

`prepare()` results are an internal cache keyed on ROI identity and **must be
invalidated** when coordinates, topology, plane assignment, engine version or
tolerance change. Since an ROI is immutable for the life of a run, the cache key
is `(patient, rtstruct_sop, roi_number)` — the same key `_mask_cache` uses.

---

## 6. Availability and failure

Both engines raise rather than returning a plausible number, and both suppliers
are explicit that a UI must show an unavailable state and never substitute zero.

**Unavailable by construction** — a STAPLE consensus is born as a binary mask and
has no polygons. Availability is a property of the **comparison pair**: both
sides must be native RTSS. This covers the Tab 2 multi-observer consensus and the
Tab 3 drawer-pool modes.

**Raises at runtime** — no common planes, resource limit (v0.2 keeps a
five-million-envelope-piece guard), invalid geometry, and, on the reference
engine only, `AmbiguousQuantileError`.

**Undetermined per metric** — on the compiled engine, a median or HD95 the
contours do not determine blanks that one cell (D9). The rest of the row stands.

All land in a dedicated `poly_status` column, caught at the ROI-pair boundary. A
polygon failure must not void the mask metrics on the same row, so it is separate
from the existing row-level `error`. An empty numeric cell beside a stated
reason; never a zero, never a silent omission from a cohort summary.

### D9 — An undetermined quantile blanks one metric, judged on the reported value

*Added 2026-09-23.* A quantile of the arc-length distance distribution is not
unique when the CDF is flat at exactly that share of the length. Example: half
the boundary coincides with the other contour, half sits 2 mm away, and nothing
lies in between. Every value in [0, 2] mm then fits, and floating-point rounding
of the cumulative length picks the side. The compiled engine measures that
interval (`quantile_mass_gap_*`) and raises when it is wider than `2 * error_mm`.
Measured on that example, both engines refused. Raising discards the whole
comparison: HD100 2.83 mm, HD95 2.44 mm, mean 1.11 mm and APL 80 mm, all
well-defined, were lost with the median.

Two over-refusals, corrected in the adapter:

1. **Scope.** The engine's `error_mm` does nothing except decide when to raise:
   the C++ only checks that it is positive. The adapter therefore passes
   `1e300`, reads the gaps, and applies the same `2 * QUANTILE_GUARD_MM` test
   itself, blanking only the affected cell. A test pins that lifting the guard
   leaves every output bit-identical.
2. **Symmetry.** The engine tests each direction, but the table reports the
   larger. The reported value's interval is `[max(a_lo, b_lo), max(a_hi, b_hi)]`.
   It is determined when that interval is within the guard, even if one
   direction's is not. In the same example drawn 2 mm *wider*, the ground-truth
   direction spans [0, 2] and the test direction is exactly 2, so the reported
   median is 2 mm either way. It used to be refused and is now reported.

A value is never picked from inside an undetermined interval. The cell stays
empty, `poly_status` names the interval (only for a metric the user selected),
and the audit record keeps it under `undefined`. The reference engine cannot
report gaps without raising, so it still refuses the whole pair. Its status now
reads `undefined:` like the compiled engine's, instead of `unavailable:`.

For comparison, the mask stream has the same exposure and resolves it silently:
DeepMind's `compute_robust_hausdorff` takes `np.searchsorted` on a floating-point
cumulative area.

### D10 — Both streams read contours through one function

*Added 2026-09-23. Supersedes D1, which it generalises.* Until now the two
streams read a structure's loops by different rules:

| Loops on a slice | Mask path (fill each loop, exclusive-or) | Polygon path (vendored parser) |
|---|---|---|
| Loop inside a loop | hole | hole (D1) |
| Touching at an edge | **one-voxel seam** | merged |
| Partial overlap | **overlap silently deleted** | refused |
| Outline crossing or touching itself | filled even-odd | refused |
| Declared `CLOSEDPLANAR_XOR` | **no mask** | read |
| Voxel centre exactly on an edge | counted inside for every loop | — |

So a difference between a 2D and a 3D number could come from the measurement
or from the two streams disagreeing about what the contour was, and nothing
said which. Measured on synthetic phantoms, plastimatch 1.9.4 is no better. Its
default unions everything, which fills every hole. `--xor-contours` behaves as
the mask path did, and it drops `CLOSEDPLANAR_XOR` structures silently. Its one
advantage is a half-open edge rule, adopted below.

**Decision.** `core/contour_reading.py` reads each structure's outlines into
regions once, by one set of rules, and both streams consume the result. The 3D
stream fills the regions; the 2D stream measures their outlines. The rules, with
the strongest evidence of intent first:

| Case | Reading |
|---|---|
| Declared `CLOSEDPLANAR_XOR` | exclusive-or, as declared |
| Loops apart | islands |
| Loop inside a loop | hole, alternating with depth; also when the inner touches the outer |
| Loops touching, not overlapping | merged |
| Partial overlap, or a duplicated loop | **refused**: union and hole are both plausible and give different tissue |
| Outline touching or crossing itself | the region it encloses if even-odd and non-zero winding agree; lines enclosing nothing are dropped; **refused** if they disagree |
| Outline enclosing no area | dropped |

Decisions are area-based with a 1e-8 mm² tolerance, the vendored parser's own,
so rounding cannot flip a classification between the two streams' frames.

**Placement stays per stream.** The 2D stream keeps the vendored parser's
checks, reimplemented with the same leading messages: within 0.001 mm of a
slice plane, inside the image, image references on the right slice (D8). The 3D
stream keeps its own: nearest slice, a planarity limit of half a slice, and
out-of-volume slices skipped. Only the loop reading is shared.

**The 3D fill** is now a half-open scanline over the region's rings. An edge
counts for a row when `low <= row < high`, and a centre is inside a span when
`start <= column < end`. So a centre exactly on an edge belongs to one side,
and shapes keep their true area. Away from such ties it fills exactly the
voxels the previous scikit-image fill did. A test compares 25 random outlines
voxel for voxel, and the dcmrtstruct2nii conformance test still passes
unchanged. The 3D stream reads in voxel units, so the coordinates filled are
the coordinates read. A structure the reading refuses gets no mask, and the
row's error now gives the reason instead of "could not be rasterised".

**The vendored parser leaves the production path** and is kept, unmodified, as
a test oracle. On everything it accepts, the shared reading must produce the
identical region. Where the reading goes further — a touching hole, an outline
with one unambiguous region, an outline without area — it is deliberate, and
listed here.

The legacy rasteriser (`AUTOSEG_RASTERISER=legacy`) is untouched. It exists to
reproduce v1 results, so it keeps v1's reading, and the audit record says so.

**Measured on the tender H&N cohort**: 5,166 structures in 70 structure sets,
run with `scripts/validate_contour_reading.py`.

| 2D: shared reading against the vendored parser | Structures |
|---|---|
| Both read, regions identical | 5,143 |
| Read now, refused before: an outline touching or crossing itself, with one region | 6 |
| Refused by both | 19 |
| Read before, refused now | **0** |

| 3D: new masks against the previous fill | Structures |
|---|---|
| Voxel-identical | 4,619 |
| Changed | 547 |
| Gained or lost a mask | 0 |

Every one of the 22,123 changed voxels has its centre exactly on an outline
edge. Every change is a tie settled by the half-open rule; none is a change in
how a contour was read. The ties are almost all in vendor A. Only vendor A
draws outlines along rows and columns of voxel centres: 6.2% of its edges on
the study organs, against 0.0% for every other vendor. The previous fill counted
every centre on those edges as inside. That inflated vendor A's masks by a
partial voxel layer along those stretches:

| Study organ | Vendor A masks changed | Volume change |
|---|---|---|
| Eye_L | 8 of 10 | 0 to −6.25% |
| Mandible | 8 of 10 | up to −0.82% |
| Parotid_R | 8 of 10 | up to −0.43% |

Vendor B changes by up to −0.22%, and C by −0.05% or less. Varian, D,
RaySearch and Radformation are unchanged on every study organ. Across all
structures, vendor A's largest changes are 0.1–3.5% of volume. **3D results for
vendor A computed before this change are superseded.** The previous numbers
carried a bias the other vendors did not.

The new fill is faster: 219 s against 815 s for the whole cohort (3.7x),
reading step included.

---

## 7. Results schema

### 7.1 What v0.2 returns

A flat dict, unlike v0.1's nested one. Every key name in revision 1 of this spec
changed:

| | v0.1 | v0.2 |
|---|---|---|
| Symmetric | `distance.hd100.mm` | `hd_mm`, `hd95_mm`, `mean_mm`, `median_mm` |
| Directional | `distance.hd95.reference_to_test.mm` | `a_hd95_mm`, `b_hd95_mm`, … |
| Bounds | `lower_mm` / `upper_mm` | **absent** |
| APL | `apl[i].reference_to_test.apl_mm` | `apl[i].apl_a_mm`, `apl_b_mm` |
| NAPL | `apl[i].reference_to_test.napl` | `apl[i].napl_a`, `napl_b` |
| Lengths | `reference_length_mm` | `a_length_mm`, `a_common_length_mm`, `a_missing_length_mm` |
| Planes | `plane_coverage.*` | `joint_planes`, `excluded_a_planes`, `excluded_b_planes` |
| Diagnostics | candidate counters | `a_pieces`, `a_candidate_segments`, `a_decimal_fallbacks`, `a_decimal_fallback_edges`, `a_quantile_mass_gap_median_mm`, `a_quantile_mass_gap_hd95_mm` |
| Scope | `numerical_scope` | `calculation`, `bound_scope` |

`a` is reference, `b` is test.

### 7.2 Table columns

After the mask metrics, before the DVH block:

| Key | Header |
|---|---|
| `poly_apl_mm` | Polygon APL (mm, τ=…) |
| `poly_napl` | Polygon NAPL (τ=…) |
| `poly_apl_reverse_mm` | Polygon APL reverse (mm, τ=…) |
| `poly_napl_reverse` | Polygon NAPL reverse (τ=…) |
| `poly_hd100_mm` | Polygon Hausdorff 100% (mm) |
| `poly_hd95_mm` | Polygon Hausdorff 95% (mm) |
| `poly_mean_distance_mm` | Polygon mean contour distance (mm) |
| `poly_median_distance_mm` | Polygon median contour distance (mm) |
| `poly_planes_joint` | Polygon planes compared |
| `poly_planes_gt_only` | Polygon planes GT only |
| `poly_planes_test_only` | Polygon planes test only |
| `poly_status` | Polygon status |

τ is baked into the header exactly as `surface_dice` already does. Both APL
directions are columns because APL is inherently directional and both are
clinically meaningful. `hd_mm` is surfaced as `poly_hd100_mm` — the v0.2 key
drops the 100 but the metric has not changed.

### 7.3 The sidecar

The directional distance values and the diagnostics go to a per-run sidecar JSON
rather than to columns; four metrics × two directions would add columns nobody
scans. **Revision 1 planned to put discretisation intervals here. There are none
in v0.2** — `bound_scope` says "analytic floating evaluation; no certified
roundoff interval", and the supplier warns specifically against inventing one in
exported metadata.

What takes their place is better audit material than the brackets were:

- `decimal_fallbacks` / `decimal_fallback_edges` — how many edges needed the
  60-digit APL path, i.e. how near the threshold the geometry sat.
- `quantile_mass_gap_median_mm` / `_hd95_mm` — how close a quantile came to being
  refused as ambiguous.
- `pieces`, `candidate_segments` — envelope size, for cost attribution.

Every row also records the engine name and version, the tolerances and the plane
policy, per both suppliers' requirement that numerical settings travel with the
numbers.

**Settled:** the sidecar is written automatically beside the CSV whenever the
run kept the detail, and is silently absent otherwise. Prompting at export would
ask about something already decided — the detail is produced while metrics are
computed and cannot be recovered from a finished table — so the decision lives on
Tab 5 as *Record audit detail*, off by default.

**It covers both streams.** The mask stream turned out to have as much to say as
the polygon stream: `compute_surface_distances` already returns both directions
with per-surfel areas and the aggregator discards one, and neither the rasteriser
backend nor the voxel spacing leaves the application in any export today —
although switching the backend moves every mask-derived number. So each record
carries a `mask` block and a `polygon` block, about 4 KB per comparison.

---

## 8. Tab 5

Per D4. Both geometry groups carry a sentence naming their method, so the split
reads as two ways of measuring rather than two lists of names:

- **3D mask metrics (rasterised)** — Dice, HD100, HD95, MSD, Surface Dice,
  volume, COM. Computed on binary masks rasterised onto the CT lattice.
- **2D contour metrics (native RTSS polygons)** — APL, NAPL, HD100, HD95, mean,
  median. Computed on the stored contour segments; no rasterisation. One control
  only: tolerance τ. **No precision control** — per D2 there is nothing for it to
  set. **No nested-ring toggle** either, per the D1 revision — the mask path
  composes them unconditionally, so a switch here could only put the two streams
  out of step. The engine in use is shown, not chosen, unless the environment
  override is set.

**Default off**, against this section's earlier "can now default on". Cost is no
longer the argument — a pair is milliseconds — but this is a second way of
measuring the same structures rather than a refinement of the first, and it adds
up to eleven columns. An install should start producing them because someone
asked, not because it was upgraded.

**No session change.** This spec previously said v6 → v7; that was wrong. Metric
selections have never lived in the session file, which carries matching state —
drawers, rules, template, organ assignments. They live in the application
settings alongside `compute_geometric`, `tolerances` and `dvh`, and the polygon
stream adds `compute_polygon` beside them. Settings carry no schema version and
an absent key reads as its default, so an existing install upgrades silently.

---

## 8a. Naming the two Hausdorffs

Both streams report a Hausdorff, and in a results table the two sit side by side
with no group heading to separate them. They are different measurements — one
between rasterised 3D surfaces, one between 2D contour segments on a plane — and
they do not agree, so neither may appear without saying which it is.

Every user-facing label therefore carries its dimension: **3D Hausdorff** for the
mask stream, **2D Hausdorff** for the polygon stream, in Tab 5, the results
table, the CSV export, the Report tab's prose and the consensus tab's
inter-observer table. The metric keys are unchanged — this is naming, not schema
— but **exported CSV headers change**, which matters to anyone parsing them.

## 9. Report tab

`metric_family()` gains a polygon family so the grouped selector separates 2D from
3D — a reader must not slide from `hausdorff95` to `poly_hd95_mm` without noticing
the change of method. `readable.py` needs prose, units, tolerance notes and
scale/direction for each new key.

---

## 10. Validation

Four layers. Passing a supplier's own tests proves their kernel works and nothing
about our adapter.

0. **Per platform, in CI** — on `ubuntu-latest`, build the library with
   `tools/build_native_library.py`, then run the supplier's own
   `scripts/validate_public.py` and `validate_stress.py` before our suite. This
   is the layer that catches a platform whose math library moves a result, and
   it is the only validation a CI-built binary gets. The existing
   `if: runner.os == 'Linux'` step idiom in `ci.yml` is where it goes.
1. **Vendored kernels** — v0.1's 56 tests and v0.2's 49, plus integrity tests
   over the vendored sources and the pinned Windows binary.
2. **Adapter** — synthetic polygons with analytic answers, D1's composition and
   its partial-overlap guard, empty ROIs, no common planes, consensus
   unavailability, irregular slice spacing, engine selection and fallback.
3. **Differential** — the two engines on the same inputs: v0.2 inside v0.1's
   intervals, APL agreeing to the tolerances in §3.3. This is the check that
   catches a bad platform build, so it runs per platform.
4. **End to end** — the 150-pair synthetic acceptance set through *our* grid
   builder and *our* adapter, not only through a supplier's library, against
   `golden_metrics.json`.

**Measured 2026-09-22, all five runs passing.** The compiled engine reproduces
the published values to 4.86e-10 mm through our own call path and, separately,
from the DICOM files up: 150 pairs, 300 ROIs parsed by our parser on grids built
by our builder. The reference engine reproduces them to 7.06e-04 mm.

One trap, recorded because it cost a run to find: the published thresholds
describe agreement with audited *continuous* values, and the reference engine
meets them only at the sampling step its own acceptance was recorded at. Judged
at the coarser step it runs at in the application it misses by up to 7e-3 mm —
inside its own stated interval, outside the suppliers' 1e-3 mm threshold. That
is the sampling step being measured, not a defect, and the acceptance run now
sets the step explicitly rather than inheriting the production one.

Layer 4 needs the original archives, which do not belong in git. It follows the
existing repo idiom — `scripts/validate_polygon_metrics.py --data <folder> --out
docs/POLYGON_VALIDATION_REPORT.md`, report committed, data not. A small fixture
subset is committed for CI. v0.2 also ships compact fixtures at 1.7 MB total,
which may make the in-repo subset unnecessary.

Acceptance thresholds are the suppliers': distance within 0.001000002 mm of the
audited reference for v0.1, APL within 1e-8 mm; v0.2 measured 4.86e-10 mm and
2.91e-10 mm against the same goldens.

---

## 10a. Measured in the worker, on real data

13 ground-truth-versus-vendor comparisons across three organs of the sample
cohort, driven through the worker's own linkage resolution, grid builder,
parser, caching and engine selection:

| | |
|---|---|
| Per pair | 32–269 ms |
| Grids built | 1, reused across all 13 pairs |
| Structures prepared | 16 — three ground truths and thirteen vendor contours, each once |

The plane bookkeeping shows the asymmetry it exists to show: most pairs share
every ground-truth plane while the vendor reaches 2–7 further, and one
(Radformation on the right parotid) leaves two ground-truth planes uncovered.
Those are exactly the slices a distance metric would otherwise average away.

## 11. Performance

Rewritten for v0.2. ~37 ms per parotid-scale pair, ~524 ms for a brain, against
the 2–21 s of the sampling engine.

Revision 1 asserted polygon pairs cost roughly 1000× a mask pair and told the
progress panel to weight them accordingly. **That is now wrong** — the ratio is
closer to 1–3×, and the supplier's own benchmark puts the compiled engine within
1.58× of a shared-mask implementation over 21 clinical pairs, ahead on spinal
cord and behind on oral cavity and brain. Weight polygon work comparably to mask
work and measure once it runs.

Two consequences:

- **Roadmap #4 (parallelisation) loses urgency.** It was going to be needed to
  make polygon metrics usable; now it is an ordinary throughput improvement.
- The brain remains the outlier, roughly half the total across a clinical set in
  the supplier's benchmark. Cost tracks boundary length, so whole-body and
  external contours are the cases to watch.

`prepare()` caching matters: preparation is per ROI, comparison is per pair, and
an ROI compared against five sources should be prepared once.

---

## 12. Sequence

| Phase | Lands | Behaviour change |
|---|---|---|
| 1 | ✅ **Done.** Both engines vendored, integrity tests, 105 supplier tests, acceptance wrapper, CI Linux build step, `shapely` declared | none |
| 2 | ✅ **Done.** `contour_grid.py`, `polygon_metrics.py`, engine selection + fallback, 30 tests | none |
| 3 | ✅ **Done.** Four acceptance layers, differential, `docs/POLYGON_VALIDATION_REPORT.md` | none |
| 4 | ✅ **Done.** Worker integration, availability, caching, 8 tests | metrics computed |
| 5 | ✅ **Done.** Tab 5 split, settings round-trip, 6 tests | metrics selectable |
| 6 | ✅ **Done.** Declared columns, `2D`/`3D` naming, Report families, definitions dialog, optional two-stream sidecar | metrics reportable |
| 7 | ✅ **Done.** Mask APL removed (D3), settings keys retired, validator pinned to legacy for PlatiPy parity; README, project overview and NOTICE brought up to v3 | numbers move |

Phases 1–3 change nothing a user can see, deliberately: the numerical path is
proven against the published reference, through our own adapter, before it is
wired to a button.

**Phase 1 is no longer blocked.** Windows ships the supplied binary, Linux is
built and validated in the CI job that phase 1 adds, and macOS falls back to the
reference engine. Nothing further is needed from the supplier to start.

---

## 13. Deviations from the suppliers, recorded

| # | Deviation | Why |
|---|---|---|
| D1 | Nested `CLOSED_PLANAR` rings composed even-odd, unconditionally | The mask path has done this since v1 — verified on a real structure whose mask contains holes — so an opt-in could not avoid the assumption, only decide whether the two streams shared it. Uses the supplier's own guarded parser; partial overlaps still refuse |
| D5 | Ships a `0.2.0.dev2` prototype as the default engine | Reproduces the published acceptance set exactly and agrees with the independent v0.1 to 1e-12 mm; the v0.1 engine is retained as audit reference and fallback, and the engine version is recorded on every row |
| D6 | Builds the Linux binary ourselves rather than taking one from the supplier | Per-platform revalidation on every push is what catches an OS whose math library moves a result; the supplier is explicit the flags do not promise bitwise cross-OS equality. Accepts that the Linux binary is validated by us |
| D7 | Separate `error_mm` for each engine | The argument names one thing and means two; one value for both costs three orders of magnitude |
| D8 | References naming nothing in the data are set aside before the strict parser runs | The parser cannot tell a reference that contradicts the image from one naming a slice that is not loaded. One vendor of seven on a real cohort wrote only the second kind, and lost every structure to it (0 → 435 of 447). Contradictions still refuse, geometric placement is still enforced, and the parser is unmodified |
| D9 | The engine's quantile refusal is lifted and reapplied per metric, on the reported (larger-direction) value | Raising discarded every well-defined metric with the one undetermined quantile, and refused medians the other direction had already settled. Same threshold; a value is still never chosen from inside the interval |
| D10 | The vendored parser leaves the production path; one shared reading serves both streams | The streams read loops by different rules, so a 2D/3D difference could be a reading difference. The parser stays as a test oracle; on everything it accepts the regions are identical |
| — | Shapely 2.0.6 rather than 2.1.2 | Permitted by both packages' ranges; 56/56 and 49/49 tests plus the full 150-pair suite and 44 stress cases verified on ours |
| ~~D2~~ | ~~`error_mm` 0.05~~ | Withdrawn in revision 2 — the setting no longer affects cost or value |

Nothing else in the numerical path is changed. The kernels are vendored
byte-identical and each supplier's acceptance suite runs at its own settings.
The vendored parser is no longer called in production (D10). It too is
byte-identical, and it now serves as the oracle the shared reading is tested
against.
