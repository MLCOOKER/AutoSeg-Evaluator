# Native polygon metrics — implementation spec

Roadmap item #5, Stream B. Six metrics computed directly on the line segments
RTSTRUCT stores, with no rasterisation anywhere in the path.

This spec is written before the code, records the decisions taken on
2026-09-19, and states every deliberate deviation from the reference handoff so
the deviations are reviewable rather than discovered later.

**Status: not implemented.** Nothing in this document is in the application yet.

---

## 1. What is being integrated

A reference package, `native_contour_metrics` 0.1.0, delivered 16 September
2026 as `Native_Polygon_Metrics_Handoff`. It implements the definitions in

> Boukerroui D, Vasquez Osorio E, Brunenberg E, Gooding MJ. *Analytic
> calculations and synthetic shapes for validation of quantitative contour
> comparison software.* PIRO 26 (2023), 100436.

It is **not** Gooding's Chapter-15 code. That is preserved in the handoff under
`upstream/` for reference and the package deliberately departs from it:

| | Chapter 15 | This package |
|---|---|---|
| Sampling | fixed 1 mm steps, equally weighted | arclength-weighted bins of `2·error_mm` |
| APL | GEOS polygon buffers | exact segment/capsule intersection in `Decimal` |
| HD100 | maximum over sampled points | continuous branch-and-bound over segments |
| Median | **mean of the two directional medians** | **maximum**, as the paper defines |

The last row is not cosmetic: the handoff records an example where the two
differ by 2.57 mm. We follow the paper.

### Verified here before planning

- The 56-test unit suite passes in the project environment on **Shapely 2.0.6**
  (the handoff was validated on 2.1.2 — the version range permits ours, but the
  handoff warns not to infer validation from the range, so this was checked).
- `examples/compare_polygons.py` reproduces the delivered `example_result.json`
  to the last digit.

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
(`missing_plane_policy="exclude"`, the paper profile). APL counts reference-only
planes in full, and NAPL's denominator includes every reference plane.

---

## 3. Measurements this plan is based on

Both were taken on 2026-09-19 against real data, not assumed.

### 3.1 The strict parser accepts 335 of 389 real vendor ROIs (86 %)

Run over the seven vendor RTSTRUCTs of the HN1 sample series:

| Rejection | Count |
|---|---|
| Missing contour sequence | 32 |
| Ambiguous overlapping `CLOSED_PLANAR` loops | 22 |

The 32 are genuinely empty ROIs, not a parser limit: 389 − 32 = 357, exactly the
count both mask rasteriser backends convert. The two streams agree on emptiness.

Every one of the 22 was classified: **all are nested rings, none partially
overlap.** They include `Brain`, `Lung_L`, `Lung_R`, `Larynx_SG`, `Supraglottic
Larynx`, `Skeleton`, `Body` — evaluation targets, not only couch and rings.

### 3.2 `error_mm = 0.001` does not survive clinical geometry

A parotid-sized ROI — 40 **distinct** planes, ~4,750 mm of total boundary:

| `error_mm` | Time per pair | HD95 | Interval half-width |
|---|---|---|---|
| 0.001 | **RuntimeError: sample limit exceeded** | — | — |
| 0.005 | 10.2 s | 1.6692 | ±0.005 |
| 0.01 | 5.7 s | 1.6691 | ±0.010 |
| 0.05 | 2.1 s | 1.6693 | ±0.050 |

HD95 agrees to four decimal places across every setting that completes.

The sampling step is `2·error_mm` and the 2,000,000-point budget is shared
across *distinct* plane pairs. The handoff's own benchmark never hit the limit
because its synthetic planes are identical and collapse under multiplicity;
real anatomy has no two identical planes. This is a property of clinical input,
not a defect in the package.

---

## 4. Decisions

Taken 2026-09-19.

### D1 — Nested rings are composed as holes

Our adapter detects rings that are strictly contained within another ring on the
same plane and composes them even-odd — the reading `CLOSEDPLANAR_XOR` declares
explicitly and which these vendors simply failed to declare.

**Guarded**: composition applies only when no pair on the plane *partially*
overlaps. Anything ambiguous is still rejected, so the extension cannot silently
reinterpret geometry that has more than one reading.

This is a **deliberate deviation** from the handoff's parser, which rejects all
of these. Rationale: it takes polygon coverage from 335 to 357 ROIs, level with
the mask path, and without it a user running both streams sees mask numbers and
blank polygon numbers for Brain, Lungs and Larynx. It lives in our adapter; the
vendored parser is not edited.

### D2 — `error_mm` defaults to 0.05 mm, user-adjustable

~2.1 s per pair with ±0.05 mm discretisation bounds — still an order of
magnitude finer than the ~1 mm voxel the mask metrics are quantised to, and it
matched the 0.005 mm setting to four decimals in §3.2.

The handoff requires this to be treated as a versioned profile rather than a
silent weakening: the value is exposed in Tab 5, recorded on every result row,
and written into every export. The reference acceptance suite continues to run
at the handoff's own settings, not at ours.

### D3 — Mask APL is removed separately, after this lands

`apl_mean` / `apl_total` stay for now. Polygon APL ships first so the two can be
compared on real data before anything is deleted. Removal is then its own
change, carrying the Methods, PROJECT_OVERVIEW, README and tooltip rewrite and
the session-key migration together.

### D4 — Tab 5 puts the two geometry methods side by side

Two rows: 3D mask and 2D contour as equal columns, DVH full width beneath. The
two geometry groups sit next to each other because that is where a reader
compares them; DVH gets the width its D@volume / V@dose lists need.

---

## 5. Where the code goes

```
src/autoseg_evaluator/
  vendor/
    native_contour_metrics/     11 files, 146 KB, byte-identical to the handoff
    README.md                   provenance, version, "do not edit"
  core/
    contour_grid.py             NEW  CT headers -> grid, cached per series
    polygon_metrics.py          NEW  ROI -> planes -> compare() -> row keys
```

**Vendored, not restyled.** Acceptance thresholds are 1e-8 mm on APL;
reformatting code that dense is how a silent numerical change happens. The repo
already has this pattern — the continuous rasteriser adapted from
dcmrtstruct2nii, with `NOTICE`. The handoff ships `MANIFEST.sha256.json`
covering all eleven package files, so a test asserts the vendored copy is
unmodified.

`shapely>=2.0,<3` becomes a **declared** dependency. It is currently installed
only transitively.

### 5.1 The grid

`scripts/prepare_fixtures.py` in the handoff is the working model:

- `basis` — `ImageOrientationPatient` reshaped to (2,3), plus their cross
  product as the third column.
- slices sorted by projection onto that normal; `dz` = median of the diffs.
- `spacing = [PixelSpacing[1], PixelSpacing[0], dz]`, `size = [Columns, Rows, n]`.
- `sops` — `{SOPInstanceUID: zero-based index}`.

It **asserts regular spacing**. Irregular series are explicitly the receiving
team's problem, so `contour_grid` must detect irregularity and refuse with a
stated reason rather than computing on a wrong `dz`.

The library already holds `files` in DICOM order and `sop_instance_uids` per
series, so the grid is built by re-reading slice headers with
`stop_before_pixels=True`, once per series, cached beside `_ct_cache`.

### 5.2 The adapter

`core/polygon_metrics.py` owns everything the handoff calls integration
responsibility: ROI selection by `ROINumber`, plane identity, D1's ring
composition, the call into `compare`, and the flattening of its nested result
into row keys. The vendored kernel sees only validated millimetre polygons.

---

## 6. Availability and failure

The handoff is emphatic: the API raises rather than returning a plausible
number, and a UI "must show an unavailable/error state, never substitute zero".

**Unavailable by construction** — a STAPLE consensus is born as a binary mask
and has no polygons. Availability is a property of the **comparison pair**, not
of a structure: both sides must be native RTSS. This covers the Tab 2
multi-observer consensus and Tab 3 drawer-pool modes.

**Raises at runtime** — no common planes, resource limit, `AmbiguousQuantileError`,
invalid geometry.

Both land in a dedicated `poly_status` column, caught at the ROI-pair boundary.
A polygon failure must not void the mask metrics on the same row, so it is
separate from the existing row-level `error`. An empty numeric cell beside a
stated reason; never a zero, never a silent omission from a cohort summary.

---

## 7. Results schema

Proposed columns, after the mask metrics and before the DVH block:

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

τ is baked into the header exactly as `surface_dice` already does.

Both APL directions are columns because APL is inherently directional and both
are clinically meaningful. The **directional distance values and every
discretisation interval** go to a per-run sidecar JSON rather than to columns —
four metrics × two directions × three bounds would add 24 columns nobody scans.
The sidecar satisfies the handoff's requirement to retain directions, settings,
identities and error status in exports.

*Open:* whether the sidecar is written automatically beside the CSV or only on
request.

---

## 8. Tab 5

Per D4. Both geometry groups carry a sentence naming their method, so the split
reads as two ways of measuring rather than two lists of names:

- **3D mask metrics (rasterised)** — Dice, HD100, HD95, MSD, Surface Dice,
  volume, COM. Computed on binary masks rasterised onto the CT lattice.
- **2D contour metrics (native RTSS polygons)** — APL, NAPL, HD100, HD95, mean,
  median. Computed on the stored contour segments; no rasterisation.
  Controls: tolerance τ, precision (`error_mm`), and the fixed statement that
  distances use common planes only.

Polygon metrics default **off**, because they cost seconds per pair where the
mask metrics cost milliseconds.

Session schema **v6 → v7** adds the polygon selections, τ list and `error_mm`.
Older sessions load unchanged.

---

## 9. Report tab

`metric_family()` gains a polygon family so the grouped selector separates 2D
from 3D — a reader must not slide from `hausdorff95` to `poly_hd95_mm` without
noticing the change of method. `readable.py` needs prose, units, tolerance notes
and scale/direction for each new key.

---

## 10. Validation

Three layers, because passing the reference library's own tests proves the
kernel works and nothing about our adapter.

1. **Vendored kernel** — the 56 unit tests, plus a manifest-integrity test
   asserting the vendored files are unmodified.
2. **Adapter** — synthetic polygons with analytic answers, D1's ring
   composition and its partial-overlap guard, empty ROIs, no common planes,
   consensus unavailability, irregular slice spacing.
3. **End to end** — the handoff's step 4: the 150-pair synthetic acceptance set
   run through *our* grid builder and *our* adapter, not only through the
   reference library, against `golden_metrics.json`.

Layer 3 needs the 314 MB of original archives, which do not belong in git. It
follows the existing repo idiom — `scripts/validate_polygon_metrics.py --data
<folder> --out docs/POLYGON_VALIDATION_REPORT.md`, with the report committed and
the data not. A small fixture subset is committed for CI.

Acceptance thresholds are the handoff's: distance within 0.001000002 mm of the
audited reference, APL within 1e-8 mm, NAPL within 1e-10, correct plane counts
on all 150 pairs.

---

## 11. Performance

From §3.2, ~2.1 s per ROI pair at the chosen precision for a parotid-sized
structure. A 200-pair cohort is therefore several minutes of polygon work on top
of the mask metrics, and large structures (External, Body) will be materially
worse than the probe.

Two things follow:

- Progress weighting must account for polygon pairs costing ~1000× a mask pair,
  or the progress bar will lie. Cancellation checks go between pairs.
- **Roadmap #4 (parallelisation) gets much more valuable.** Polygon pairs are
  embarrassingly parallel and are now the dominant cost.

One safe optimisation is documented and deferred: the facade samples the same
distance distribution twice, once for HD95/mean and once for median, so sharing
it is close to a 2× saving. It must not be taken before layer-3 acceptance
passes, and acceptance must be re-run after.

---

## 12. Sequence

| Phase | Lands | Behaviour change |
|---|---|---|
| 1 | Vendored kernel, integrity test, 56 unit tests, `shapely` declared | none |
| 2 | `contour_grid.py`, `polygon_metrics.py` + tests | none |
| 3 | End-to-end acceptance, `docs/POLYGON_VALIDATION_REPORT.md` | none |
| 4 | Worker integration, availability, progress weighting | metrics computed |
| 5 | Tab 5 split, session v7 | metrics selectable |
| 6 | Results columns, Report tab families | metrics reportable |
| 7 | *(separate, per D3)* mask APL removed, docs rewritten | numbers move |

Phases 1–3 change nothing a user can see, which is deliberate: the numerical
path is proven against the published reference before it is wired to a button.

---

## 13. Deviations from the handoff, recorded

| # | Deviation | Why |
|---|---|---|
| D1 | Nested `CLOSED_PLANAR` rings composed even-odd | Recovers 22 real ROIs; all measured nested, none partially overlapping; guarded so ambiguous geometry still rejects |
| D2 | `error_mm` 0.05 mm rather than 0.001 mm | 0.001 fails outright on clinical structures (§3.2); ours is a versioned profile, recorded on every row |
| — | Shapely 2.0.6 rather than 2.1.2 | Permitted by the package's range; 56/56 tests verified on ours |

Nothing else in the numerical path is changed. The kernels are vendored
byte-identical and the acceptance suite runs at the handoff's own settings.
