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

<h2>3D mask metrics</h2>

<p>Contours are filled onto the CT voxel grid and the resulting binary masks are
compared in three dimensions. Every distance is therefore quantised to the voxel
size, which for a typical planning CT is around 1&nbsp;mm in plane and 2–3&nbsp;mm
between slices.</p>

<h3>Dice</h3>
<p>Twice the overlapping volume divided by the sum of the two volumes; 0&nbsp;to&nbsp;1.
Biased toward larger structures — a fixed boundary error costs a small organ far
more Dice than a large one.</p>

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

<h3>Volume, volume difference and ratio, centre-of-mass offset</h3>
<p>Volumes are voxel counts multiplied by the voxel volume. The difference is
signed (test &minus; ground truth) and the ratio is test &divide; ground truth, so
neither has a "better" direction — both are best at their target, not at their
extreme. The centre-of-mass offset is the distance between the two centroids in
patient coordinates, with signed components, and catches a positional shift that
a high overlap score can hide.</p>

<h3>Rasterisation</h3>
<p>Contour vertices are transformed to continuous sub-voxel coordinates before
filling. A second, legacy backend that snapped vertices to the nearest voxel
centre first is retained for reproducing older results; it systematically
over-estimates volume, by roughly 3% on large organs and more than 50% on
structures one to two voxels across. <b>The backend in use is recorded in the
audit sidecar</b>, because switching it moves every mask-derived number.</p>

<p>Where a structure's contours stack more than one loop on a plane, the loops
are combined by parity — a loop inside another becomes a hole.</p>

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
and the two combined by taking the <b>larger</b>. Where a quantile falls on a
rounding-sensitive gap in the distance distribution, it is <b>refused</b> rather
than resolved arbitrarily, and the status column says so.</p>

<h3>Contour topology</h3>
<p>Simple closed loops and explicitly declared parity contours are read as
given. Where an exporter stacks a loop inside another without declaring it, the
inner loop is read as a hole — the same reading the mask stream has always
applied, so the two agree. Loops that touch, cross or partially overlap have no
single correct reading: those structures are <b>refused</b> rather than guessed,
and can therefore carry 3D metrics and no 2D metrics.</p>

<h2>What is reported when a metric cannot be computed</h2>

<p>An undefined metric is never filled in with a zero, because a zero reads as
perfect agreement and would be indistinguishable from one. The 2D status column
carries the reason instead, and the numeric cells stay empty. The common causes
are a consensus ground truth (which is created as a mask and has no contours at
all), no shared planes, contour topology that cannot be read unambiguously, and
a quantile refused as described above.</p>

<p class="note">A failure in one stream does not void the other. A row can carry
3D metrics and a 2D status explaining why the 2D columns are blank.</p>

<h2>Provenance</h2>

<p>Both streams record what produced their numbers — the rasteriser backend and
voxel spacing for the 3D stream, the engine, its version and its settings for the
2D stream — in the optional audit sidecar written beside an export. A number
that cannot be traced to the method and settings that produced it cannot be
reproduced.</p>

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
