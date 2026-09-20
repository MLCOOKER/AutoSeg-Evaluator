# Native polygon metrics — implementation spec

Roadmap item #5, Stream B. Six metrics computed directly on the line segments
RTSTRUCT stores, with no rasterisation anywhere in the path.

Written before the code, so the decisions and every deliberate deviation from
the supplied reference are reviewable rather than discovered later.

**Status: not implemented.** Nothing here is in the application yet.

**Revision 2, 2026-09-20.** Revised for the compiled `0.2.0.dev1` engine
delivered that morning. It removes the decision this spec previously made about
precision, changes the output schema entirely, and drops the cost of a polygon
pair by roughly two orders of magnitude. Revision 1 (2026-09-19) planned around
the v0.1 sampling engine alone.

---

## 1. What is being integrated

Two packages implementing the same definitions, from

> Boukerroui D, Vasquez Osorio E, Brunenberg E, Gooding MJ. *Analytic
> calculations and synthetic shapes for validation of quantitative contour
> comparison software.* PIRO 26 (2023), 100436.

| | `native_contour_metrics` 0.1.0 | `native_contour_metrics_fast` 0.2.0.dev1 |
|---|---|---|
| Delivered | 16 Sep 2026 | 20 Sep 2026 |
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

| Check | v0.1 | v0.2 |
|---|---|---|
| Unit tests | 56/56 pass | 25/25 pass |
| Public acceptance | 150/150 | 150/150, 3,000 comparisons |
| Max distance error vs audited golden | — | 4.856e-10 mm (their claim: 4.86e-10) |
| Suite wall time | 399 s (supplier's machine) | 31.7 s (this machine) |

The shipped DLL's SHA-256 matches the supplier's release record, and every
`*_failures.json` in their evidence folder is an empty list.

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

### D1 — Nested rings are composed as holes, via the supplier's opt-in parser

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

**Before enabling it per exporter**, compare representative brain, lung and
larynx cases against the exporting system or a confirmed export specification,
as the supplier advises. The option is per-exporter and off by default.

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

### D4 — Tab 5 puts the two geometry methods side by side

Two rows: 3D mask and 2D contour as equal columns, DVH full width beneath.

### D5 — Two engines ship; the compiled one is the default

Mirrors the existing rasteriser pattern (`continuous` default, `legacy` opt-in,
`AUTOSEG_RASTERISER` to switch). Here: `fast` default, `reference` opt-in, via
`AUTOSEG_POLYGON_ENGINE`.

The reference engine earns its place three times over: it is the audit trail the
supplier asks us to keep, it is the differential check behind §3.3, and it is the
automatic fallback on any platform with no compiled binary. Its brain-scale
limitation is stated where a user can meet it.

`0.2.0.dev1` is a prototype by its own label. Shipping it as the default is
defensible because it reproduces the published acceptance set exactly and agrees
with the independently-written v0.1 to 1e-12 mm — but the version is recorded on
every result row so a number can always be traced to the engine that produced it.

### D6 — Platform binaries are a supplier deliverable, with a fallback

The C++ is portable by inspection: one translation unit, standard headers only,
a single `#ifdef _WIN32` for the export macro that already has a non-Windows
branch, no MSVC intrinsics, and a plain C ABI. Building `.so` / `.dylib` is
routine; the supplier has offered to provide precompiled libraries, which keeps
"no compiler needed" true for users and leaves revalidation with the people who
wrote the kernel.

Needed: **Windows x64, Linux x86-64, macOS arm64 and x86-64**, each with the
150-pair suite rerun on that platform.

Two things to hold them to:

- The loader hardcodes `bin/fast_native.dll`; it needs platform dispatch. Better
  fixed upstream than patched in our vendored copy.
- MSVC built with `/fp:strict`. gcc and clang default to `-ffp-contract=fast`,
  which fuses `a*b+c` into an FMA and moves the last bits. Platform builds need
  **`-ffp-contract=off`**, alongside the supplier's existing instruction never to
  enable fast-math. The code calls `std::fma` explicitly for a discriminant, so
  the author is alert to this; the 150-pair suite at 4.86e-10 mm has the headroom
  to detect a drifting build.

Where no binary matches the platform, D5's reference engine runs instead and the
UI says which engine produced the numbers.

---

## 5. Where the code goes

```
src/autoseg_evaluator/
  vendor/
    native_contour_metrics/          v0.1, 11 files, byte-identical  (reference engine)
    native_contour_metrics_fast/     v0.2, + bin/<platform>/         (default engine)
    README.md                        provenance, versions, "do not edit"
  core/
    contour_grid.py                  NEW  CT headers -> grid, cached per series
    polygon_metrics.py               NEW  ROI -> planes -> engine -> row keys
```

**Vendored, not restyled.** Acceptance is 1e-10 mm on APL; reformatting code that
dense is how a silent numerical change happens. The repo already has this pattern
— the continuous rasteriser adapted from dcmrtstruct2nii, with `NOTICE`. Both
packages ship a `MANIFEST.sha256.json`, so a test asserts the vendored copies and
the compiled binaries are unmodified.

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

**Raises at runtime** — no common planes, `AmbiguousQuantileError`, resource
limit (v0.2 keeps a five-million-envelope-piece guard), invalid geometry.

Both land in a dedicated `poly_status` column, caught at the ROI-pair boundary. A
polygon failure must not void the mask metrics on the same row, so it is separate
from the existing row-level `error`. An empty numeric cell beside a stated
reason; never a zero, never a silent omission from a cohort summary.

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

*Open:* whether the sidecar is written automatically beside the CSV or on request.

---

## 8. Tab 5

Per D4. Both geometry groups carry a sentence naming their method, so the split
reads as two ways of measuring rather than two lists of names:

- **3D mask metrics (rasterised)** — Dice, HD100, HD95, MSD, Surface Dice,
  volume, COM. Computed on binary masks rasterised onto the CT lattice.
- **2D contour metrics (native RTSS polygons)** — APL, NAPL, HD100, HD95, mean,
  median. Computed on the stored contour segments; no rasterisation. One control
  only: tolerance τ. **No precision control** — per D2 there is nothing for it to
  set. The engine in use is shown, not chosen, unless the environment override is
  set.

Polygon metrics can now default **on**: at ~37 ms per pair they are no longer the
expensive option they were under v0.1.

Session schema **v6 → v7** adds the polygon selections and the τ list. Older
sessions load unchanged. There is no `error_mm` to persist.

---

## 9. Report tab

`metric_family()` gains a polygon family so the grouped selector separates 2D from
3D — a reader must not slide from `hausdorff95` to `poly_hd95_mm` without noticing
the change of method. `readable.py` needs prose, units, tolerance notes and
scale/direction for each new key.

---

## 10. Validation

Four layers. Passing a supplier's own tests proves their kernel works and nothing
about our adapter.

1. **Vendored kernels** — v0.1's 56 tests and v0.2's 25, plus manifest-integrity
   tests over both packages and every compiled binary.
2. **Adapter** — synthetic polygons with analytic answers, D1's composition and
   its partial-overlap guard, empty ROIs, no common planes, consensus
   unavailability, irregular slice spacing, engine selection and fallback.
3. **Differential** — the two engines on the same inputs: v0.2 inside v0.1's
   intervals, APL agreeing to the tolerances in §3.3. This is the check that
   catches a bad platform build, so it runs per platform.
4. **End to end** — the 150-pair synthetic acceptance set through *our* grid
   builder and *our* adapter, not only through a supplier's library, against
   `golden_metrics.json`.

Layer 4 needs the original archives, which do not belong in git. It follows the
existing repo idiom — `scripts/validate_polygon_metrics.py --data <folder> --out
docs/POLYGON_VALIDATION_REPORT.md`, report committed, data not. A small fixture
subset is committed for CI. v0.2 also ships compact fixtures at 1.7 MB total,
which may make the in-repo subset unnecessary.

Acceptance thresholds are the suppliers': distance within 0.001000002 mm of the
audited reference for v0.1, APL within 1e-8 mm; v0.2 measured 4.86e-10 mm and
2.91e-10 mm against the same goldens.

---

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
| 1 | Both engines vendored, integrity tests, 81 supplier tests, `shapely` declared | none |
| 2 | `contour_grid.py`, `polygon_metrics.py`, engine selection + fallback, tests | none |
| 3 | Differential and end-to-end acceptance, `docs/POLYGON_VALIDATION_REPORT.md` | none |
| 4 | Worker integration, availability, `prepare()` caching | metrics computed |
| 5 | Tab 5 split, session v7 | metrics selectable |
| 6 | Results columns, sidecar, Report tab families | metrics reportable |
| 7 | *(separate, per D3)* mask APL removed, docs rewritten | numbers move |

Phases 1–3 change nothing a user can see, deliberately: the numerical path is
proven against the published reference, through our own adapter, before it is
wired to a button.

**Phase 1 is blocked on D6** for platforms other than Windows x64. It is not
blocked for Windows, and the reference engine covers the others meanwhile.

---

## 13. Deviations from the suppliers, recorded

| # | Deviation | Why |
|---|---|---|
| D1 | Nested `CLOSED_PLANAR` rings composed even-odd, per exporter, opt-in | Recovers 22 real ROIs and matches what both of our mask backends have always done; refusing would make the two streams disagree. Uses the supplier's own guarded parser, not a local reimplementation |
| D5 | Ships a `0.2.0.dev1` prototype as the default engine | Reproduces the published acceptance set exactly and agrees with the independent v0.1 to 1e-12 mm; the v0.1 engine is retained as audit reference and fallback, and the engine version is recorded on every row |
| — | Shapely 2.0.6 rather than 2.1.2 | Permitted by both packages' ranges; 56/56 and 25/25 tests plus the full 150-pair suite verified on ours |
| ~~D2~~ | ~~`error_mm` 0.05~~ | Withdrawn in revision 2 — the setting no longer affects cost or value |

Nothing else in the numerical path is changed. The kernels are vendored
byte-identical and each supplier's acceptance suite runs at its own settings.
