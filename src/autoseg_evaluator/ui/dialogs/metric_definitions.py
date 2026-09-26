"""Metric definitions — exactly what each number means and how it was computed.

Two streams measure the same structures by different means and report metrics
with overlapping names. A reader comparing a 2D Hausdorff with a 3D one, or
wondering why truncation moved one column and not another, needs somewhere to
find the answer that is not the source code.

The text is deliberately specific: which representation each metric is computed
on, how the two directions are combined, what the measurement is weighted by,
which planes take part, and what each is quantised to. Everything stated here is
what the code does — where the two streams differ, the difference is named
rather than smoothed over.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

_STYLE = """
<style>
  body { font-size: 10pt; }
  h2 { color: #15606E; margin: 16px 0 4px 0; }
  h3 { margin: 14px 0 2px 0; }
  p  { margin: 4px 0 8px 0; }
  .note { color: #6B7B85; }
  table { border-collapse: collapse; margin: 6px 0 10px 0; }
  th { background: #E8EFF2; text-align: left; padding: 4px 8px; }
  td { padding: 4px 8px; border-bottom: 1px solid #D8E0E4; }
  code { background: #F2F5F6; }
</style>
"""

_BODY = """
<h2>Two ways of measuring the same structures</h2>

<p>Every comparison is between one ground-truth structure and one test
structure. The application can measure that comparison twice, by two
independent routes, and they are not interchangeable.</p>

<table>
<tr><th></th><th>3D mask metrics</th><th>2D contour metrics</th></tr>
<tr><td><b>Measured on</b></td>
    <td>binary masks filled onto the CT voxel grid</td>
    <td>the contour line segments as stored in the RTSTRUCT</td></tr>
<tr><td><b>Geometry</b></td>
    <td>a 3D surface</td>
    <td>independent 2D outlines, one plane at a time</td></tr>
<tr><td><b>Weighted by</b></td>
    <td>surface area of each surface element (mm&sup2;)</td>
    <td>arc length along each boundary (mm)</td></tr>
<tr><td><b>Resolution limit</b></td>
    <td>the voxel lattice — distances are quantised to it</td>
    <td>none from sampling; computed on the segments directly</td></tr>
</table>

<p>Because the weighting measure differs, a percentile taken over one is not a
percentile over the other <i>even for identical anatomy</i>. The two streams are
complementary, not redundant, and a difference between them is not an error in
either.</p>

<h2>Reading the contours (both streams)</h2>

<p>A structure set stores outlines, not regions. Before anything is measured, the
outlines on each slice are read as an area: which loops are islands, which are
holes, and what an outline that touches or crosses itself encloses. Both streams
use this one reading. The 3D metrics fill the regions it produces and the 2D
metrics measure their outlines, so a difference between a 2D and a 3D number
comes from how each measures, never from reading the contour differently.</p>

<table>
<tr><th>What is on the slice</th><th>How it is read</th></tr>
<tr><td>Loops declared <code>CLOSEDPLANAR_XOR</code></td>
    <td>Overlaps cancel, as the declaration states</td></tr>
<tr><td>Loops apart from each other</td><td>Separate islands</td></tr>
<tr><td>A loop inside another loop</td>
    <td>A hole. An island inside a hole is inside again, and so on by depth</td></tr>
<tr><td>Loops touching at an edge or a point</td><td>Merged into one region</td></tr>
<tr><td>Loops partially overlapping, or the same loop drawn twice</td>
    <td><b>Refused.</b> Union and hole are both plausible, they give different
    tissue, and the file does not say which was meant</td></tr>
<tr><td>One outline touching or crossing itself</td>
    <td>Read as the region it encloses when the even-odd and non-zero winding
    rules agree on that region. Lines enclosing nothing, such as a spike drawn
    out and back, are dropped. If the rules disagree the outline goes around
    some area twice, and it is <b>refused</b></td></tr>
<tr><td>An outline enclosing no area</td><td>Dropped</td></tr>
</table>

<p>A refused structure gets neither 3D nor 2D metrics, and the row says which
rule refused it. When the audit sidecar is on, it records for each structure
anything the reading had to interpret: holes, merges, repaired outlines and
dropped outlines.</p>

<p class="note">Which slice a contour belongs to is checked separately by each
stream, because they need different things. The 3D fill uses the nearest slice
and allows up to half a slice of tilt. The 2D metrics need each contour within
0.001&nbsp;mm of its slice plane and wholly inside the image.</p>

<h2>3D mask metrics</h2>

<p>Contours are filled onto the CT voxel grid and the resulting binary masks are
compared in three dimensions. Every distance is therefore quantised to the voxel
size, which for a typical planning CT is around 1&nbsp;mm in plane and 2–3&nbsp;mm
between slices.</p>

<p>Dice and all surface-distance metrics (3D Hausdorff, Mean Surface Distance,
Surface Dice) are computed with Google DeepMind's <b>surface-distance</b>
implementation (github.com/google-deepmind/surface-distance). It is embedded
in this application, as it was in AutoSeg Evaluator v1. It finds each mask's
surface elements from the 2&times;2&times;2 voxel neighbourhoods along the
boundary, gives each one its surface area in mm&sup2;, and measures its distance
to the other mask's surface with a Euclidean distance transform.</p>

<h3>Dice</h3>
<p>Twice the overlapping volume divided by the sum of the two volumes; 0&nbsp;to&nbsp;1.
Biased toward larger structures — a fixed boundary error costs a small organ far
more Dice than a large one.</p>

<h3>Precision and recall</h3>
<p><b>Precision</b> is the share of the test's volume that lies inside the
ground truth, and falls when the test over-segments. <b>Recall</b> is the share
of the ground truth's volume the test covers, and falls when the test
under-segments. Both run from 0 to 1. Dice cannot tell those two failures
apart — a contour drawn too large and one drawn too small can score the same
Dice — and these two can. Dice is their harmonic mean, which is why F1 is not
reported: on masks it is Dice. Each is left empty when its denominator is: an
empty test has no precision, and an empty ground truth no recall.</p>

<h3>3D Hausdorff (100%) and (95%)</h3>
<p>Distances are measured from each surface element of one mask to the nearest
point of the other, in both directions. Each direction's value is taken
separately — the maximum for 100%, the 95th percentile <i>weighted by surface
area</i> for 95% — and the two are combined by taking the <b>larger</b>.</p>
<p class="note">The 100% figure is a maximum over discrete surface elements, so
it cannot see a peak that falls between them.</p>

<h3>Mean Surface Distance</h3>
<p>The area-weighted mean distance is computed in each direction, and the two are
combined by taking their <b>equal average</b> — not by pooling all distances into
one population, which would weight the direction with more surface area more
heavily.</p>

<h3>Surface Dice</h3>
<p>The fraction of each surface lying within a tolerance &tau; of the other,
summed over both directions and divided by the total surface area of both. Reads
as "how much of the boundary is already close enough to accept".</p>
<p>Several tolerances can be given at once; each is computed from the same
surface distances and fills its own column, named with its tolerance. Values at
different tolerances are different measurements and are never compared with each
other. The 2D added path length takes its tolerances the same way.</p>

<h3>Volume, volume difference and ratio, centre-of-mass offset</h3>
<p>Volumes are voxel counts multiplied by the voxel volume. The difference is
signed (test &minus; ground truth) and the ratio is test &divide; ground truth, so
neither has a "better" direction — both are best at their target, not at their
extreme. The centre-of-mass offset is the distance between the two centroids in
patient coordinates, with signed components, and catches a positional shift that
a high overlap score can hide.</p>

<h3>Rasterisation</h3>
<p>How each structure becomes a mask, adapted from dcmrtstruct2nii:</p>
<ol>
<li>Every vertex is converted from patient millimetres to <b>continuous</b> voxel
coordinates, using the image's origin, spacing and orientation. Vertices are not
rounded to the nearest voxel, so the outline keeps its sub-voxel position.</li>
<li>Each contour is assigned to the nearest slice. A contour whose vertices span
more than half a slice through-plane is not planar in the image, and that
structure produces no mask. Contours on slices outside the image are
skipped.</li>
<li>The loops on each slice are read into regions by the rules in
<i>Reading the contours</i> above, the same reading the 2D metrics measure.</li>
<li>Each region is filled onto its slice. A voxel is inside when its
<b>centre</b> lies inside the region. A centre lying exactly on an edge belongs
to one side only, so a shape keeps its true area and two regions sharing an
edge never both claim, or both leave out, the voxels along it.</li>
</ol>
<p>A structure the reading refuses gets no mask, and its row says why.</p>

<h2>2D contour metrics</h2>

<p>Computed directly on the line segments the RTSTRUCT stores. Nothing is
rasterised, nothing is sampled onto a grid, and no vertex is moved. Both
structures are projected into one orthonormal millimetre frame derived from the
image series, and each contour is assigned to the slice it lies on.</p>

<h3>Which planes take part — and why truncation does not apply here</h3>

<p><b>Distance metrics use only the planes where <i>both</i> structures have a
contour.</b> A plane reached by just one of them is excluded from the distance
measurement and counted separately, in the "planes GT only" and "planes test
only" columns.</p>

<p>This matters for interpretation. If a vendor contours five slices further
than the manual ground truth, those five slices do not inflate its distance
metrics — but they are not hidden either, because the plane counts report them.
A structure that shares no plane at all with its comparator has <b>no defined
distance</b>, and the status column says so rather than showing a zero.</p>

<p><b>The truncation option in the Match Contours tab applies to the 3D mask
stream only.</b> It shortens the test mask to the ground truth's craniocaudal
range before the masks are compared. The 2D stream needs no equivalent: its
shared-plane rule already excludes every test contour lying outside the ground
truth's planes, so truncation would remove exactly what has been excluded
already. Turning truncation on or off therefore changes the 3D columns and
leaves the 2D columns unmoved. That is expected, not a fault.</p>

<h3>2D Added Path Length and 2D NAPL</h3>
<p>The length of ground-truth contour lying further than &tau; from the test
contour — the boundary an editor would have to redraw. It is <b>directional</b>,
and both directions are reported: the forward value is what must be drawn, the
reverse what must be removed. NAPL divides by the total ground-truth contour
length, making it comparable between structures of different size where raw
length is not.</p>
<p>Ground-truth planes the test never reached count in full, since none of that
boundary is covered. A test-only plane contributes nothing — there is no
ground-truth boundary there to redraw.</p>

<h3>2D Hausdorff (100%) and (95%)</h3>
<p>Distance is measured from each boundary to the nearest point of the other
<i>on the same plane</i>, in both directions, with each direction's value taken
separately and the two combined by taking the <b>larger</b>. The 95% figure is
the 95th percentile <b>weighted by arc length</b>, so a long stretch of boundary
counts for more than a short one regardless of how many vertices each has.</p>
<p>The 100% figure is a true continuous maximum, found by subdivision over the
segments — it can locate a peak lying <i>between</i> vertices, which a
vertex-only or voxel-based maximum cannot.</p>

<h3>2D Mean and Median Contour Distance</h3>
<p>Both are arc-length weighted. The mean is computed per direction and the two
are combined by their <b>equal average</b>; the median is computed per direction
and the two combined by taking the <b>larger</b>.</p>

<h3>Contour topology</h3>
<p>The loops are read by the rules in <i>Reading the contours</i> above, the same
reading the 3D fill uses. The 2D metrics are then measured along the outlines of
the regions it produces.</p>

<h2>Dose-volume statistics</h2>

<p>The dose is integrated over the contours themselves, read by the same rules
as both geometric streams (<i>Reading the contours</i> above). Each contour stands
for a slab one CT slice thick, centred on its slice: the convention of treatment
planning systems and of the analytic benchmarks this method is validated
against.</p>
<ol>
<li>Each slab is divided into sub-cells aligned to the CT voxels. Their spacing
is chosen per structure: the finest of 0.25, 0.5 and 1 mm that keeps the
structure within ten million samples. Small organs are sampled at 0.25 mm; only
the largest targets are sampled more coarsely, where it no longer matters, and a
structure too large for that even at 1 mm, such as a body contour, is sampled
once per voxel.</li>
<li>A sub-cell counts for exactly the area of the region inside it, and the dose
is interpolated trilinearly from the dose grid at the centroid of that area.</li>
<li>The samples accumulate into a histogram of 1 mGy bins.</li>
</ol>
<p><b>D<sub>x%</sub></b> and <b>D<sub>x cc</sub></b> are the lowest dose the
hottest x of the structure receives; <b>V<sub>x Gy</sub></b> is the volume
receiving at least x Gy. Dmin, Dmean and Dmax are exact over the samples. A
D<sub>x cc</sub> larger than the structure is left empty, and the <i>Dose
status</i> column says why.</p>
<p>The statistics describe the part of a structure inside the dose grid. A part
lying outside it has no calculated dose, so it is left out rather than given
one, and the <i>Dose grid coverage</i> column gives the share of the structure's
volume the statistics describe: 100% when the grid covers all of it. A consensus ground truth
has no contours, so its voxels are sampled instead, by the same spacing rule.</p>
<p>Validated against the analytic datasets of Nelms et al., <i>Methods, software
and datasets to verify DVH calculations against analytical values</i>, Medical
Physics 42 (2015) 4435, and against analytic disc phantoms
(<code>docs/DVH_METHOD_VALIDATION.md</code>). Versions before 3.0 used
dicompyler-core, which that report scores alongside.</p>

<h2>What is reported when a metric cannot be computed</h2>

<p>An undefined metric is never filled in with a zero, because a zero reads as
perfect agreement and would be indistinguishable from one. The 2D status column
carries the reason instead, and the numeric cells stay empty. The common causes
are a consensus ground truth (which is created as a mask and has no contours at
all), no shared planes, and a contour that is not on a slice plane or lies
partly outside the image. These blank every 2D metric in the row. A 2D median
or 95% value that the contours do not determine blanks only its own cell, and
the status column gives the range it could take. A structure whose loops the
reading refuses has no metrics in either stream, and the row's error says
why.</p>

<p class="note">A failure in one stream does not void the other. A row can carry
3D metrics and a 2D status explaining why the 2D columns are blank.</p>

<h2>Provenance</h2>

<p>Both streams record what produced their numbers — the rasteriser and voxel
spacing for the 3D stream, the engine, its version and its settings for the 2D
stream — in the optional audit sidecar written beside an export, as does the
dose: its source, sub-sample spacing and sample count. A number that cannot be
traced to the method and settings that produced it cannot be reproduced.</p>

<p>The 2D definitions follow Boukerroui, Vasquez Osorio, Brunenberg and Gooding,
<i>Analytic calculations and synthetic shapes for validation of quantitative
contour comparison software</i>, Physics and Imaging in Radiation Oncology 26
(2023) 100436.</p>
"""


class MetricDefinitionsDialog(QDialog):
    """Read-only reference for how every metric in this application is computed."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Metric definitions")
        self.setModal(False)
        self.resize(760, 680)

        layout = QVBoxLayout(self)
        browser = QTextBrowser(self)
        browser.setOpenExternalLinks(True)
        browser.setHtml(_STYLE + _BODY)
        # Start at the top even when Qt restores a scroll position.
        browser.moveCursor(QTextCursor.MoveOperation.Start)
        layout.addWidget(browser)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        buttons.setCenterButtons(False)
        layout.addWidget(buttons)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)


__all__ = ["MetricDefinitionsDialog"]
