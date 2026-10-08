# PTV overlap — design

**Status: implemented, 2026-10-07**, as specified in revision 3. Tested in
`tests/test_ptv_overlap.py`, and run on the head-and-neck cohort. Written
before the code, so the decisions can be reviewed rather than discovered
later. Revision 2 recorded the answers to the first draft's
questions (D5–D9) and reduced the results to three columns. Revision 3 selects
PTVs by structure type alone (D10) and leaves consensus rows blank (D5).

---

## 1. Purpose

An auto-contoured organ at risk that reaches into the PTV, or stops short of
where the clinician's contour reaches into it, changes the plan: the optimiser
trades target coverage against that overlap, and dose constraints on the organ
are judged partly inside the target. Dice can be high while the part of the
contour that matters to the plan is wrong, because the overlap with the PTV is
usually a small fraction of the organ.

So each test contour row records how much of the ground truth, and how much of
the test contour, lies inside the PTV, and the difference. Many organs overlap
the PTV legitimately, such as a parotid beside a nodal PTV, so an overlap on
its own says little. The comparison with the ground truth is the measure, and
the case worth flagging is a test contour that overlaps where the ground truth
does not.

## 2. Decisions

### D1 — Any overlap counts

No threshold: a contour overlaps the PTV when its volume inside it is above
zero. With masks this includes a single voxel shared at a border, about
0.002 cc on a 1.07 × 1.07 × 2 mm grid, which can arise from rasterisation
alone. Showing the volume, not just yes/no, is what lets a reader spot those
(D7).

### D2 — The comparison is test minus ground truth, in cc

`ptv_overlap_diff_cc` = test volume in the PTV − ground-truth volume in the
PTV. Positive: the test reaches further into the PTV than the ground truth.
Negative: it reaches less far. Zero: the same amount, though not necessarily
the same voxels.

### D3 — Several PTVs are combined into one

All PTVs selected for a case are combined into one region (a union) before
anything is measured. A voxel inside two PTVs counts once.

### D4 — No PTV means no value, not zero

Where the ground truth's structure set has no PTV, the three columns are left
blank. Zero would claim "no overlap", which nobody measured. The same holds for
a consensus ground truth (D5), and where a PTV cannot be rasterised, since a
union missing one would understate the overlap. There is no status column
(D7), so the reason is in the audit sidecar's record for the row.

### D5 — Only the ground truth's structure set supplies the PTV

The PTVs are taken from the structure set the ground truth contour belongs to,
and from nowhere else. No search of other structure sets, so no ambiguity to
resolve.

A STAPLE consensus ground truth has no structure set of its own, so its rows
are left blank (D4), and the audit record says that a consensus ground truth
has no PTV to measure against.

### D6 — The test contour is the truncated one

When the drawer truncates, the test contour is measured as cut to the ground
truth's craniocaudal extent, as for the other 3D metrics. That is what makes
the difference from the ground truth (D2) fair. The ground truth is never
truncated; it defines the extent.

### D7 — Three result columns as volumes, and one yes/no

| Column | Heading | Meaning |
|---|---|---|
| `gt_ptv_overlap_cc` | GT overlap with PTV (cc) | The ground truth's volume inside the PTV; 0 = no overlap |
| `test_ptv_overlap_cc` | Test overlap with PTV (cc) | The test contour's volume inside the PTV; 0 = no overlap |
| `ptv_overlap_diff_cc` | PTV overlap difference (cc) | Test − GT (D2) |
| `test_only_ptv_overlap` | Test-only PTV overlap | True: the test overlaps the PTV and the ground truth does not (D9) |

Volumes rather than yes/no flags: with no threshold (D1), "overlaps" is
"above zero", so a volume carries the yes/no and the amount together, and the
difference can only be read next to the two volumes it comes from. The fourth
column (added 2026-10-08) flags, row by row, the one case D9 counts, so it can
be found in the table without comparing two columns. Blank wherever the
volumes are blank.

The names of the PTVs combined, and the row's status, go in the audit
sidecar's record for the row, not in the table. The record already names the
ground truth it belongs to; no UID is added.

### D8 — Masks, not contours, measure it

Section 5 compares the two. Masks are used, so the overlap is measured the same
way as Dice and volume. With consensus rows blank (D5), the contour route's
inability to measure a consensus no longer counts against it; it stays a
possible later companion if one-voxel contacts (D1) prove a nuisance.

### D9 — The Report tab and PDF flag the test-only overlaps

The coverage table gains a column, beside Truncated, counting for each organ
and source the test contours that overlap the PTV **where the ground truth
does not**: test overlap above zero and ground-truth overlap zero. Shown as
"k of n", n being the cases where both were measured. Only that case is
counted. A test contour that misses an overlap the ground truth has is visible
in the difference column, but is not flagged here.

## 3. Which structures count as a PTV

### D10 — The structure type decides, and only the type

Within the ground truth's structure set (D5), a structure is a PTV when its
`RTROIInterpretedType` (3006,00A4) is `PTV`. This is already read for every
ROI (`OrganEntry.interpreted_type`). The name is not read: a structure named
`PTV_70` but typed otherwise is not used, and one typed `PTV` is used whatever
it is called.

What that means in practice:

- A structure set whose writer leaves the type empty has no PTV, and its rows
  are blank (D4). The code notes that MIM and MVision never fill it in;
  clinical planning systems such as Eclipse do.
- A planning aid typed `PTV` (an optimisation structure, say) is included in
  the union. The audit sidecar lists every structure combined (D7), so this
  can be checked.

## 4. How it is measured

- **PTV:** each selected PTV is rasterised onto the CT grid by the same
  rasteriser as every other structure (`mask_with_reading`). The masks are
  combined with OR and only the union is kept, cached per patient and structure
  set, so each organ reuses it; the cache is emptied with the patient's other
  caches.
- **Test and ground truth:** the masks the 3D metrics already use, the test
  one truncated (D6).
- **Overlap volume** = voxels in both × voxel volume, exactly as `volume_cc`
  computes volume.

## 5. Masks or contours: the effort and the gain

The contour route would intersect each slice's polygons with shapely, which
the shared contour reader (`core/contour_reading.py`) already produces, and
multiply each area by the slice spacing.

| | Masks | Contours |
|---|---|---|
| Overlap calculation | A few lines: AND two masks, count, × voxel volume | Short too: shapely intersection per slice, × slice spacing |
| What it rests on | The rasteriser and mask cache every 3D metric uses | The contour reader the 2D metrics use |
| New conventions to define and validate | None | Slice thickness at the first and last slice, uneven slice spacing, PTV and organ on different slices |
| Consensus ground truth | Works | Does not: a consensus has no contours, so its side needs masks |
| Accuracy | Off by up to about half a voxel along the shared boundary (the rasteriser's own error, Supplementary 2) | Exact in the plane; the same as masks through the plane |
| Relative work | 1 | About 1.5, and still needs masks for consensus cases |

Most of the work is the same either way: selecting and combining the PTVs, the
columns, the Report tab and the tests. The 2D route adds a volume convention of
its own and a gap for consensus ground truths. Its gain is precision on small
overlaps, including the one-voxel contacts of D1.

## 6. Still open

Nothing. The first draft's questions are answered in D1, D2, D5, D6 and D9;
the second draft's in D5 and D10.

## 7. The Compute tab

No checkbox, and no setting: there is no threshold (D1) and the source of the
PTV is fixed (D5). It is not a metric of agreement, so it is not offered among the metrics: it is
recorded on every run, for every test contour compared with a ground truth,
whatever is ticked (decided 2026-10-07). A note on the Compute tab says so.
Counting the overlap only inside the PTV's bounding box keeps the cost to about
1.5 s per patient on the head-and-neck cohort, most of it filling the PTVs once.

## 8. Report tab and PDF

- The D9 column in the coverage table, on the tab and in the PDF, headed
  "Test-only PTV overlap". It is hidden when no case had its overlap measured,
  and reads "—" for an organ and source with none measured.
- `test_ptv_overlap_cc` and `ptv_overlap_diff_cc` join the metric selector as a
  family of their own. Neither has a better direction: the target is the
  ground truth's overlap, not more or less. So they are treated like the
  volume difference, with no best value marked. A paired comparison of
  `ptv_overlap_diff_cc` between two vendors then answers "does one vendor's
  contour encroach on the PTV more than the other's?"

## 9. Validation

- **Analytic:** two boxes, and a cylinder overlapping a box, with known
  intersection volumes, on the synthetic CT grids of Supplementary 2. These
  measure the mask error against the exact value.
- **Clinical:** a few patients checked against the overlap reported by the
  planning system (a Boolean intersection structure in Eclipse).
- **Selection:** a structure typed `PTV` is used whatever its name; one named
  like a PTV but typed otherwise, or untyped, is not; several typed `PTV` are
  combined; a structure set with none gives blank rows, as does a consensus
  ground truth.
- **D9:** a case where only the test overlaps is counted, and cases where both
  or only the ground truth overlap are not.

## 10. Where the code goes

| Piece | Place |
|---|---|
| PTV selection (by type) | `core/organ_groups.py`, next to `TARGET_TYPES` |
| Overlap of two masks | `core/metrics.py`, beside `volume_cc` |
| PTV masks, union and cache; the columns | `workers/metrics_worker.py` |
| The note saying it runs on every run | `ui/tabs/compute.py` |
| Metric family, direction and the D9 count | `data/report.py`, `ui/tabs/report.py` |
