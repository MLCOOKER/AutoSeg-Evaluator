# Contour Discontinuity — design

**Status: implemented, 2026-10-07.** Tested in
`tests/test_contour_discontinuity.py`, and run on the head-and-neck cohort.
Written before the code, so the decisions can be reviewed rather than
discovered later. The column was named **Contour Discontinuity** and the Report
tab count confirmed (Q1) before it was built.

---

## 1. Purpose

An organ is continuous from its first slice to its last, so its contour should
be too. A slice inside that range with no contour on it is a failure of the
contour, typically a slice the model missed. Dice barely registers it on a
large organ, and nothing else in the table names it. This check flags it.

## 2. Decisions

### D1 — Test contours only

The ground truth is not checked. The flag describes the test contour on its
own, and needs nothing from the ground truth.

### D2 — The whole test contour

Checked on the contour as stored, never the truncated one. A slice missing
anywhere in the contour is a failure, whether or not it lies inside the ground
truth's craniocaudal extent.

### D3 — Any ground truth

Because only the test contour is read (D1), the flag applies whatever the
ground truth is, a consensus included.

### D4 — One yes/no column

`contour_discontinuity`, headed **Contour Discontinuity**: yes when the test
contour skips at least one slice, no otherwise. The Results table shows it as
True or False, as it does every yes/no column.

### D5 — Only whole slices, never islands

What is checked is whether a slice holds any contour at all. How many pieces a
slice holds is ignored: two islands on one slice are not a discontinuity, and
can arise for many reasons.

## 3. What counts as a skipped slice

A CT slice strictly between the test contour's first and last contoured slices
that holds no region once the contour is read.

- **Which slices.** Those of the CT every test is measured on: the ground
  truth's planning image. Each contour is assigned to its nearest slice, as it
  is for the masks.
- **Read, not filled.** Taken from the shared contour reading
  (`read_structure`, which the mask stream already performs), not from the
  mask. A small contour can enclose no voxel centre, which leaves its mask slice
  empty and would count as a gap that is not one.
- **An outline enclosing no area** is dropped by the reading, so a slice holding
  only such an outline counts as skipped: it holds no tissue.
- **The ends do not count.** Slices above the first contour or below the last
  are outside the contour, not gaps in it. A contour on one slice cannot skip
  one.

One consequence: a source that contours only every other slice of the CT is
flagged on every contour. That is right against the CT, but it then says
something about the source rather than about one contour, and is worth
recognising as such when it happens.

## 4. Column and audit

| Column | Heading | Meaning |
|---|---|---|
| `contour_discontinuity` | Contour Discontinuity | True: the test contour skips at least one slice |

Blank when the test contour could not be read; that row already carries the
error. With **Record audit detail** ticked, the audit sidecar lists the skipped
slices' positions in mm, so each gap can be found.

## 5. The Compute tab

No checkbox. It is a check, not a metric of agreement, so it is recorded on
every run for every test contour, whatever is ticked (decided 2026-10-07); a
note on the Compute tab says so. It costs well under a second per patient on
the head-and-neck cohort.

## 6. Report tab and PDF

A yes/no is not analysed statistically, so it does not join the metric
selector. The coverage table gains a **Contour Discontinuity** column, as for
the PTV overlap: for each organ and source, how many test contours skip a
slice, as "k of n", on the tab and in the PDF. Hidden when no contour was
checked, as in results computed before the check existed; "—" for an organ and
source with none checked.

The Generic STAPLE rows carry the flag on their test raters too, never on the
ground-truth rater (D1, D3).

## 7. Still open

Nothing. Q1 (the Report tab and PDF count) was answered yes.

## 8. Validation

Synthetic structures on a known CT grid:

- Contours on slices 1, 2 and 4: flagged. On 1, 2 and 3: not flagged.
- One slice: not flagged.
- Two islands on one slice, no slice missing: not flagged.
- A middle slice holding only an outline enclosing no area: flagged.
- A middle slice holding a contour too small to fill a voxel: not flagged,
  although its mask slice is empty.

Truncation and the kind of ground truth cannot change the flag by
construction: the worker reads the test contour as stored, before truncating
its mask, and never reads the ground truth for it.

## 9. Where the code goes

| Piece | Place |
|---|---|
| Skipped slices of one reading (`skipped_slices`, `skipped_slice_positions`) | `core/masks.py`, beside `read_structure` |
| The column and the audit entry, per test contour | `workers/metrics_worker.py` |
| The note saying it runs on every run | `ui/tabs/compute.py` |
| Column and label | `data/results.py`, `ui/tabs/results.py` |
| The count | `data/report.py`, `ui/tabs/report.py` |
