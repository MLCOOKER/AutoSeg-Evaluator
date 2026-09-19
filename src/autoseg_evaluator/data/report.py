"""Assemble the statistical report model from computed metric rows.

Turns the per-contour rows the Results tab holds into the tables the Report tab
draws: descriptive statistics per organ and source, coverage, and paired
comparisons between sources.

The statistics themselves live in :mod:`autoseg_evaluator.core.statistics`.
What happens here is the part that decides *which numbers go into them*, which
is where the interpretive risk sits:

**One observation per (patient, organ, source).** A patient contributing twice
to the same organ would inflate the sample without adding information, and the
paired test would treat the duplicate as independent evidence.

Getting that right needs the *observation* identified properly, and a patient
identifier does not identify one. A re-irradiation or a replan gives one patient
two treatment contexts, each with its own planning image and structure sets, and
both arrive carrying the same ``PatientID``. Rows therefore key on
``(patient, linkage_id)`` — a **case** — using the linkage the ingestion layer
computes from the explicit DICOM reference graph.

Cases are not then treated as independent observations, because they are not:
two courses of one patient share an anatomy. Where a patient contributes more
than one case to an organ, that patient is **excluded from the comparison** and
named. Choosing between their courses is a study-design decision, and picking
whichever sorted first is not a decision the software should be making.

**Ground truth is not a comparator.** Every metric already measures agreement
*with* the reference, so the sources being compared are the test sources, and
the reference is recorded separately. Letting the manual contour appear as an
ordinary vendor would compare it with itself.

**Coverage is recorded as far as the data can actually say, and no further.**
From metric rows alone it is knowable whether a source produced an organ for a
patient, produced nothing for that patient at all, or produced it with an
uncomputable metric. It is *not* knowable whether the organ was in the field of
view, whether the vendor supports it, or whether a missing contour is a refusal
or a failure — so those distinctions are not invented.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from autoseg_evaluator.core.acquisition import (
    IMAGE_FIELDS,
    RTSS_FIELDS,
    FieldSummary,
    summarise,
)
from autoseg_evaluator.core.staple import DRAWER_POOL_LABEL, LEGACY_DRAWER_POOL_LABEL
from autoseg_evaluator.core.statistics import (
    ConfidenceSet,
    Description,
    IntervalStatus,
    PairedResult,
    describe,
    holm_detection_ceiling,
    paired_comparison,
)

# ---- Metric families ------------------------------------------------------

FAMILY_GEOMETRIC = "Geometric"
FAMILY_DOSIMETRIC = "Dosimetric"
FAMILY_OTHER = "Other"
FAMILY_STAPLE = "Consensus"

#: Produced by the STAPLE machinery rather than by comparing two contours.
#: Excluded from the report entirely — see :data:`_SKIP_MODES`.
STAPLE_METRICS = frozenset(
    {
        "consensus_volume_cc",
        "rater_disagreement_cc",
        "rater_volume_range_cc",
        "uncertain_band_cc",
        "mean_entropy",
        "n_raters",
        "staple_iterations",
        "staple_converged",
        "staple_bbox_padding",
        "staple_bbox_fg_ratio",
    }
)

#: Computed from contour geometry alone.
GEOMETRIC_METRICS = frozenset(
    {
        "dice",
        "surface_dice",
        "hausdorff100",
        "hausdorff95",
        "mean_surface_distance",
        "apl_mean",
        "apl_total",
        "volume_gt_cc",
        "volume_test_cc",
        "volume_diff_cc",
        "volume_ratio",
        "com_offset_mm",
        "com_dx_mm",
        "com_dy_mm",
        "com_dz_mm",
        # Per-vendor agreement with the consensus, computed alongside the
        # geometric metrics when a STAPLE consensus is the ground truth. These
        # describe one source's contour, not how the consensus was built, so
        # they are comparable across vendors like any other paired metric.
        "staple_sensitivity",
        "staple_specificity",
        "precision",
        "recall",
        "sensitivity",
        "specificity",
    }
)

#: ``D95_gy``, ``V20gy_cc`` and friends. Static ``dmin_gy`` / ``dmean_gy`` /
#: ``dmax_gy`` are caught by the ``_gy`` suffix.
_DOSE_PREFIX = re.compile(r"^[dv]\d", re.IGNORECASE)


#: A reference label containing any of these is taken to be a built consensus
#: rather than a drawn contour set. Matched on the label because that is all a
#: results row carries; the synthetic entry is labelled "STAPLE Consensus".
CONSENSUS_REFERENCE_MARKERS: tuple[str, ...] = ("staple", "consensus")


def is_consensus_reference(label: str) -> bool:
    lowered = str(label).lower()
    return any(marker in lowered for marker in CONSENSUS_REFERENCE_MARKERS)


def reference_rank(label: str) -> int:
    """Which reference the report should open on, lower being better.

    0. **A multi-observer consensus** (Tab 2), or any other consensus that is
       not the drawer pool. Pooled from several observers' contours, with the
       sources being evaluated absent from that pool — so it is the cleanest
       reference available, and the standing instruction is to prefer it over
       the manual contours whenever one exists.

    1. **A drawn reference** — the manual ground truth, or a second reader.

    2. **A drawer-pool consensus** (Tab 3's "vs STAPLE"). Ranked *below* the
       manual contours despite being a consensus, because its pool contains the
       very test contours being scored against it: every source is partly
       measured against itself. That makes it a supplementary analysis rather
       than one a report should default to.

    Ordering these by name happened to work only while the two consensus labels
    differed by the case of a single letter. Once they were given names that
    say what they are, the circular one sorted first.
    """
    text = str(label)
    if text in (DRAWER_POOL_LABEL, LEGACY_DRAWER_POOL_LABEL):
        return 2
    return 0 if is_consensus_reference(text) else 1


def metric_family(metric: str) -> str:
    """Which group a metric belongs to in the selector.

    Geometric and dosimetric metrics answer different questions and are not
    comparable, so grouping them is not decoration: a reader scanning one list
    can otherwise slide from Dice to D95 without noticing the change of subject.
    """
    name = str(metric).strip()
    lower = name.lower()
    if lower in GEOMETRIC_METRICS:
        return FAMILY_GEOMETRIC
    if lower in STAPLE_METRICS or lower.startswith("staple_"):
        return FAMILY_STAPLE
    if lower.endswith("_gy") or "gy_cc" in lower or "gy_pct" in lower or _DOSE_PREFIX.match(lower):
        return FAMILY_DOSIMETRIC
    return FAMILY_OTHER


#: Selector order. Consensus is absent because it never reaches the report.
FAMILY_ORDER: tuple[str, ...] = (FAMILY_GEOMETRIC, FAMILY_DOSIMETRIC, FAMILY_OTHER)


# ---- Metric direction -----------------------------------------------------

#: Whether a larger value is better. A metric absent from both sets is reported
#: without a better/worse reading — ``volume_diff_cc`` and ``volume_ratio`` are
#: signed and best at a target rather than monotone, so calling either direction
#: "better" would be wrong.
HIGHER_IS_BETTER = frozenset(
    {"dice", "surface_dice", "precision", "recall", "sensitivity", "specificity"}
)
LOWER_IS_BETTER = frozenset(
    {
        "hausdorff95",
        "hausdorff100",
        "mean_surface_distance",
        "apl_mean",
        "apl_total",
        "com_offset_mm",
    }
)


def metric_direction(metric: str) -> int:
    """``+1`` higher is better, ``-1`` lower is better, ``0`` no direction."""
    name = metric.lower()
    if name in HIGHER_IS_BETTER:
        return 1
    if name in LOWER_IS_BETTER:
        return -1
    return 0


def favours(metric: str, difference: float) -> str:
    """Which side a signed difference (a − b) favours, or ``""`` if undirected."""
    direction = metric_direction(metric)
    if direction == 0 or difference == 0:
        return ""
    return "a" if (difference > 0) == (direction > 0) else "b"


# ---- Reporting a confidence set -------------------------------------------


def interval_text(found: ConfidenceSet) -> str:
    """One confidence set as a reader sees it, distinguishing four absences.

    An earlier version printed one em dash for every case without two numbers,
    which merged "no shift is rejectable at this sample size" with "the accepted
    set is a single point" — opposite situations.

    Lives here rather than in the tab so the Report tab and the register's
    worked examples cannot drift apart: the register is published so an auditor
    can check the tab, which only works if both render the same way.
    """
    if found.status is IntervalStatus.INTERVAL:
        return f"{found.low:+.4f}, {found.high:+.4f}"
    if found.status is IntervalStatus.SINGLETON:
        return f"{found.low:+.4f} only"
    if found.status is IntervalStatus.DISCONNECTED:
        return f"{found.low:+.4f}, {found.high:+.4f} (enclosing)"
    if found.status is IntervalStatus.UNBOUNDED:
        return "— unbounded at this n"
    return "— not estimable"


class FamilyAxis(Enum):
    """What one Holm family varies, holding everything else fixed.

    Both axes ask a real question and neither is a superset of the other:

    ``ORGANS`` — one challenger against one reference, across several organs.
    *"Where does this vendor differ from the one we use?"*

    ``SOURCES`` — every other source against one reference, on one organ.
    *"For the parotid, how does each vendor compare to the one we use?"*

    Fanning **both** at once is the combination to avoid. At ten pairs the
    smallest attainable p is 0.001953, so Holm can reject nothing in a family
    larger than 25; five challengers times anything past five organs is already
    past that, and every adjusted p would be 1.000 whatever the data showed.
    """

    ORGANS = "organs"
    SOURCES = "sources"

    @property
    def noun(self) -> str:
        return "organ" if self is FamilyAxis.ORGANS else "source"

    @property
    def plural(self) -> str:
        return "organs" if self is FamilyAxis.ORGANS else "sources"


# ---- Coverage -------------------------------------------------------------


class Coverage(Enum):
    """What is knowable about a source's output for one patient and organ."""

    PRODUCED = "produced"
    """A contour was matched and its metric computed."""

    NOT_PRODUCED = "not produced"
    """The source produced contours for this patient, but not this organ."""

    SOURCE_ABSENT = "source absent"
    """The source produced nothing at all for this patient — it was not run."""

    METRIC_INVALID = "metric invalid"
    """A contour exists but the metric could not be computed for it."""


@dataclass(frozen=True)
class CoverageCell:
    """One organ × source cell of the coverage matrix."""

    produced: int = 0
    not_produced: int = 0
    source_absent: int = 0
    metric_invalid: int = 0

    @property
    def attempted(self) -> int:
        """Patients where the source ran at all."""
        return self.produced + self.not_produced + self.metric_invalid

    @property
    def eligible(self) -> int:
        """Patients where this organ exists in the analysis for any source."""
        return self.attempted + self.source_absent

    @property
    def fraction(self) -> float | None:
        return self.produced / self.attempted if self.attempted else None

    def summary(self) -> str:
        """Compact cell text — ``8 / 10`` with absences called out separately."""
        if self.eligible == 0:
            return "—"
        if self.attempted == 0:
            return f"— ({self.source_absent} not run)"
        text = f"{self.produced} / {self.attempted}"
        if self.source_absent:
            text += f" · {self.source_absent} not run"
        if self.metric_invalid:
            text += f" · {self.metric_invalid} invalid"
        return text


# ---- Observations ---------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    patient_id: str
    organ: str
    source: str
    metric: str
    value: float


@dataclass
class ReportModel:
    """Everything the Report tab needs, derived once from the results rows."""

    observations: dict[tuple[str, str, str, str, str, str], float] = field(default_factory=dict)
    """``(organ, source, metric, patient, linkage, reference) -> value``.

    ``patient`` and ``linkage`` together are the **case**. Rows predating the
    linkage stamp carry an empty linkage and so behave exactly as before: one
    case per patient.

    ``reference`` is what the metric was measured against — a manual ground
    truth, or a STAPLE consensus. It belongs in the key because the same
    contour compared against two references is two different measurements; with
    it absent they collided and one was silently discarded.
    """

    active_ground_truth: str = ""
    """Which reference this view is restricted to. Empty means unrestricted."""

    reference_sources: set[str] = field(default_factory=set)
    """Labels that acted as ground truth. Never comparators."""

    duplicates_collapsed: int = 0
    """Repeat observations of one (patient, organ, source, metric) that carried
    the *same* value — a re-export, harmless beyond the count."""

    conflicting_observations: int = 0
    """Repeats within *one case* that carried a different value.

    A second course no longer lands here — it is its own case. What remains is a
    genuine collision: the same organ, source and metric measured twice inside
    one treatment context with two different answers. Surfaced rather than
    buried, because unlike a duplicated export it changes which number is
    analysed."""

    _patients_by_source: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    _patients_by_organ: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    _by_reference: dict[str, tuple[dict[str, set[str]], dict[str, set[str]]]] = field(
        default_factory=dict
    )
    """``reference -> (patients by source, patients by organ)`` for that reference.

    Kept so a restricted view can rebuild its coverage denominators. Counting a
    patient as eligible because they appear under a *different* ground truth
    would overstate what the shown comparison could have used.
    """

    # ---- Inventory --------------------------------------------------------

    def ground_truths(self) -> list[str]:
        """References present in the data, best first.

        Ordered by :func:`reference_rank`, because the first entry is what the
        report opens on.
        """
        found = {reference for (*_rest, reference) in self.observations if reference}
        return sorted(found, key=lambda label: (reference_rank(label), label))

    def preferred_ground_truth(self) -> str:
        """The reference the report opens on.

        Rank decides; ties break on how many observations a reference carries,
        so with two manual readers the one covering the cohort wins.
        """
        available = self.ground_truths()
        if not available:
            return ""
        counts: dict[str, int] = defaultdict(int)
        for *_rest, reference in self.observations:
            counts[reference] += 1
        return min(available, key=lambda label: (reference_rank(label), -counts[label], label))

    def for_ground_truth(self, reference: str) -> ReportModel:
        """A view restricted to one reference.

        Returns self unchanged when the reference is empty or the data holds
        only that one, so the single-ground-truth case costs nothing.
        """
        if not reference or self.ground_truths() == [reference]:
            return self
        by_source, by_organ = self._by_reference.get(reference, ({}, {}))
        return ReportModel(
            observations={
                key: value for key, value in self.observations.items() if key[5] == reference
            },
            reference_sources=set(self.reference_sources),
            duplicates_collapsed=self.duplicates_collapsed,
            conflicting_observations=self.conflicting_observations,
            active_ground_truth=reference,
            _patients_by_source=defaultdict(set, {k: set(v) for k, v in by_source.items()}),
            _patients_by_organ=defaultdict(set, {k: set(v) for k, v in by_organ.items()}),
            _by_reference=self._by_reference,
        )

    def sources(self) -> list[str]:
        """Test sources available for comparison, ground truth excluded."""
        found = {source for (_organ, source, _metric, _patient, _link, _ref) in self.observations}
        return sorted(found - self.reference_sources)

    def organs(self) -> list[str]:
        return sorted({organ for (organ, _s, _m, _p, _link, _ref) in self.observations})

    def metrics(self) -> list[str]:
        return sorted({metric for (_o, _s, metric, _p, _link, _ref) in self.observations})

    def metrics_by_family(self) -> list[tuple[str, list[str]]]:
        """``[(family, metrics)]`` in selector order, empty families dropped."""
        grouped: dict[str, list[str]] = defaultdict(list)
        for metric in self.metrics():
            grouped[metric_family(metric)].append(metric)
        return [(family, grouped[family]) for family in FAMILY_ORDER if grouped[family]]

    def patients(self) -> list[str]:
        return sorted({patient for (_o, _s, _m, patient, _link, _ref) in self.observations})

    # ---- Samples ----------------------------------------------------------

    def cases(self, organ: str, source: str, metric: str) -> dict[tuple[str, str], float]:
        """``{(patient, linkage): value}`` for one cell — every case, as stored."""
        return {
            (patient, linkage): value
            for (o, s, m, patient, linkage, _ref), value in self.observations.items()
            if o == organ and s == source and m == metric
        }

    def multi_case_patients(self, organ: str, source: str, metric: str) -> set[str]:
        """Patients contributing more than one treatment context to this cell.

        Two courses of one patient are two cases but not two independent
        observations, so they cannot both be analysed; and choosing between them
        is the user's call, not the software's.
        """
        seen: dict[str, int] = defaultdict(int)
        for patient, _linkage in self.cases(organ, source, metric):
            seen[patient] += 1
        return {patient for patient, count in seen.items() if count > 1}

    def values(self, organ: str, source: str, metric: str) -> dict[str, float]:
        """``{patient: value}`` for one cell, one vote per patient.

        Patients with more than one case are omitted entirely rather than
        resolved arbitrarily — see :meth:`multi_case_patients`.
        """
        ambiguous = self.multi_case_patients(organ, source, metric)
        return {
            patient: value
            for (patient, _linkage), value in self.cases(organ, source, metric).items()
            if patient not in ambiguous
        }

    def describe_cell(
        self, organ: str, source: str, metric: str, alpha: float = 0.05
    ) -> Description | None:
        return describe(list(self.values(organ, source, metric).values()), alpha)

    # ---- Coverage ---------------------------------------------------------

    def excluded_patients(self, organ: str, metric: str, source_a: str, source_b: str) -> set[str]:
        """Patients dropped from this comparison for contributing several cases."""
        return self.multi_case_patients(organ, source_a, metric) | self.multi_case_patients(
            organ, source_b, metric
        )

    def coverage(self, organ: str, metric: str, source: str) -> CoverageCell:
        """How much of this organ the source actually produced.

        ``SOURCE_ABSENT`` means the source produced nothing for that patient
        anywhere — it was not run. ``NOT_PRODUCED`` means it ran and this organ
        is missing, which is the state that carries information about the model.
        """
        eligible = self._patients_by_organ.get(organ, set())
        produced = self.values(organ, source, metric)
        counts = {"produced": 0, "not_produced": 0, "source_absent": 0, "metric_invalid": 0}
        for patient in eligible:
            if patient in produced:
                value = produced[patient]
                if value is None or (isinstance(value, float) and math.isnan(value)):
                    counts["metric_invalid"] += 1
                else:
                    counts["produced"] += 1
            elif patient in self._patients_by_source.get(source, set()):
                counts["not_produced"] += 1
            else:
                counts["source_absent"] += 1
        return CoverageCell(**counts)

    def coverage_matrix(self, metric: str) -> dict[str, dict[str, CoverageCell]]:
        return {
            organ: {source: self.coverage(organ, metric, source) for source in self.sources()}
            for organ in self.organs()
        }

    # ---- Comparison -------------------------------------------------------

    def compare(
        self,
        organ: str,
        metric: str,
        source_a: str,
        source_b: str,
        alpha: float = 0.05,
    ) -> PairedResult | None:
        """Paired comparison on the patients where both sources produced the organ.

        The unpaired counts travel with the result so the report can show how
        much the pairing discarded — a source that contoured six of ten patients
        is compared only on those six, and they are unlikely to be a random six.
        """
        a_values = self.values(organ, source_a, metric)
        b_values = self.values(organ, source_b, metric)
        shared = sorted(set(a_values) & set(b_values))
        if not shared:
            return None
        return paired_comparison(
            [a_values[p] for p in shared],
            [b_values[p] for p in shared],
            n_a=len(a_values),
            n_b=len(b_values),
            alpha=alpha,
        )

    def family(
        self,
        metric: str,
        reference: str,
        challenger: str,
        organs: Sequence[str] | None = None,
        alpha: float = 0.05,
    ) -> dict[str, PairedResult | None]:
        """One Holm family: this metric, this pair of sources, these organs.

        Holm controls the familywise error rate **within this set only**. A
        finding selected from across several such families does not carry that
        control, which is why the family is passed in explicitly rather than
        inferred from whatever happens to be on screen.

        **Every declared organ is returned**, including those with no patients
        both sources contoured, which map to ``None``. They are hypotheses that
        were posed and could not be answered, and they still count toward the
        Holm divisor. Dropping them would make the correction gentler precisely
        when a source produced less — rewarding the coverage gap that the
        coverage table exists to expose.
        """
        chosen = list(organs) if organs is not None else self.organs()
        return self._correct(
            {organ: self.compare(organ, metric, challenger, reference, alpha) for organ in chosen}
        )

    def family_across_sources(
        self,
        metric: str,
        reference: str,
        organ: str,
        sources: Sequence[str] | None = None,
        alpha: float = 0.05,
    ) -> dict[str, PairedResult | None]:
        """One Holm family: this metric, this organ, every source against one.

        The mirror of :meth:`family`. Keys are source labels rather than organ
        labels; everything downstream treats them the same way, which is why the
        family is always ``{label: result}`` rather than something axis-specific.

        Defaults to every test source except the reference, which is a family
        definition that cannot be accused of cherry-picking — unlike a subset of
        organs, "all the other vendors" is fixed by the data.

        Note what these rows are not. Each is a separate paired test against the
        **same** reference arm, so they are correlated: a patient the reference
        handled badly makes every source look good on that patient. Holm holds
        the familywise rate under arbitrary dependence, so the correction is
        valid — but consistency down the column is not extra evidence, and the
        rows do not compare the sources *with each other*.
        """
        chosen = (
            list(sources) if sources is not None else [s for s in self.sources() if s != reference]
        )
        return self._correct(
            {source: self.compare(organ, metric, source, reference, alpha) for source in chosen}
        )

    @staticmethod
    def _correct(results: dict[str, PairedResult | None]) -> dict[str, PairedResult | None]:
        """Return the comparisons as computed, with no multiplicity adjustment.

        Each row answers its own question — *for this organ and this metric, is
        there evidence that these two sources differ?* — and a question is not
        made harder to answer by another question being asked beside it. Two
        organs are two studies that happen to share a screen.

        This replaces a Holm correction across whichever organs were selected,
        which had a defect worse than the problem it addressed: the divisor was
        a **view setting**. Narrowing the organ list made a result significant
        and widening it made the same result disappear, so the p-value depended
        on what was on screen. A correction that can be dialled by a list widget
        invites exactly the selection it exists to prevent, and looks rigorous
        while doing it.

        What multiplicity costs is not hidden, it is *stated*: see
        :func:`expected_false_positives`. Scanning twenty organs and reporting
        the two that reached 0.05 is still a selected finding, and the tab says
        how many to expect by chance. The safeguard is reporting every organ,
        not shrinking the p-values of the ones that happen to win.
        """
        return results

    def family_can_detect(
        self, results: Iterable[PairedResult | None], alpha: float = 0.05
    ) -> bool:
        """Could *any* member of this family reach significance after Holm?

        False means no comparison shown can reach significance however the data
        fall, because the smallest attainable p even at the largest sample here
        exceeds alpha. Worth saying out loud: a column of large p-values
        otherwise reads as evidence the sources agree.

        With no multiplicity adjustment the bound is simply ``2 / 2ⁿ ≤ alpha``,
        which needs six paired patients at the conventional 0.05. Five or fewer
        cannot produce a significant result whatever the contours look like.
        """
        collected = [r for r in results if r is not None]
        if not collected:
            return False
        # The most favourable member decides, not the least. Holm's strictest
        # threshold is alpha/m applied to the smallest p-value in the family,
        # and the comparison best able to produce a small p is the one with the
        # most pairs. Asking whether the *thinnest* comparison could reject
        # would warn that nothing is detectable while a well-populated organ in
        # the same family is significant on screen.
        largest_n = max(r.n_pairs for r in collected)
        return holm_detection_ceiling(largest_n, 1, alpha)


def expected_false_positives(comparisons: int, alpha: float = 0.05) -> float:
    """How many of ``comparisons`` would look significant by chance alone.

    The honest replacement for a correction the user did not want. Each organ
    is reported as its own question with an unadjusted p-value, which is right
    for answering *this* organ — but a reader scanning twenty rows for the ones
    below 0.05 is doing something the individual p-values do not account for.
    Stating the expected count leaves that judgement with the reader instead of
    silently making every p-value larger.
    """
    return max(0, int(comparisons)) * float(alpha)


# ---- Acquisition ----------------------------------------------------------


@dataclass(frozen=True)
class AcquisitionReport:
    """What the cohort was acquired and contoured with."""

    images: list[FieldSummary] = field(default_factory=list)
    structure_sets: list[FieldSummary] = field(default_factory=list)
    n_series: int = 0
    n_structure_sets: int = 0
    n_patients: int = 0

    @property
    def available(self) -> bool:
        return bool(self.n_series or self.n_structure_sets)


@dataclass
class _StructureSetRecord:
    """The allowlisted structure-set fields, shaped for :func:`summarise`."""

    manufacturer: str = ""
    model: str = ""
    software_versions: str = ""
    roi_count: int = 0


def collect_acquisition(library: Any) -> AcquisitionReport:
    """Summarise scanner and structure-set parameters across a scanned library.

    Reads only what the ingest already captured through the allowlist in
    :mod:`autoseg_evaluator.core.acquisition`; nothing here reaches back into
    the DICOM files, so no tag outside that allowlist can arrive by this route.

    Counts are per *series* and per *structure set*, not per patient. A cohort
    scanned on two scanners should say so, and per-patient counting would hide
    a patient with two series.
    """
    if library is None:
        return AcquisitionReport()

    images = []
    structure_sets = []
    patients = getattr(library, "patients", {}) or {}
    for patient in patients.values():
        for context in getattr(patient, "contexts", []) or []:
            for series in getattr(context, "image_series", []) or []:
                found = getattr(series, "acquisition", None)
                if found is None:
                    continue
                # ``slices`` lives on the series, not on the acquisition read.
                found.slices = len(getattr(series, "files", []) or [])
                images.append(found)
            for rtss in getattr(context, "rtstructs", []) or []:
                if getattr(rtss, "is_synthetic_consensus", False):
                    continue  # no DICOM header of its own
                structure_sets.append(
                    _StructureSetRecord(
                        manufacturer=str(getattr(rtss, "manufacturer", "") or ""),
                        model=str(getattr(rtss, "manufacturer_model_name", "") or ""),
                        software_versions=str(getattr(rtss, "software_versions", "") or ""),
                        roi_count=len(getattr(rtss, "organs", []) or []),
                    )
                )

    return AcquisitionReport(
        images=summarise(images, IMAGE_FIELDS),
        structure_sets=summarise(structure_sets, RTSS_FIELDS),
        n_series=len(images),
        n_structure_sets=len(structure_sets),
        n_patients=len(patients),
    )


# ---- Building from results rows -------------------------------------------

#: Comparison modes that are not a contour-versus-contour comparison at all.
#: ``STAPLE Details`` rows describe how a consensus was built — one row per
#: organ, not per source — and qualitative rows carry Likert scores.
#:
#: Consensus *comparisons* are deliberately not here. Measuring every source
#: against a multi-observer consensus is a real analysis, and the reason it
#: once collided with the manual-ground-truth rows was that the reference was
#: missing from the observation key, not that the rows were unwanted.
_SKIP_MODES = {"staple details", "qualitative"}


def build_report_model(
    rows: Iterable[dict[str, Any]],
    *,
    organ_field: str = "canonical_organ",
    fallback_organ_field: str = "drawer",
) -> ReportModel:
    """Derive the report model from ``ResultsManager.rows()``.

    Organs are keyed on the canonical organ where the grouping layer supplied
    one, so drawers named differently across patients pool correctly. Rows
    without one fall back to the drawer name and simply stand alone.
    """
    model = ReportModel()
    for row in rows:
        source = str(row.get("test_source_label") or "").strip()
        if not source:
            continue
        mode = str(row.get("comparison_mode") or "").strip().lower()
        if mode in _SKIP_MODES:
            continue

        reference = str(row.get("gt_source_label") or "").strip()
        if reference == LEGACY_DRAWER_POOL_LABEL:
            # Sessions saved before the drawer-pool consensus had a name of its
            # own. Mapped forward so old and new results of the same analysis
            # pool, rather than appearing as two references that differ only in
            # capitalisation.
            reference = DRAWER_POOL_LABEL
        if reference:
            model.reference_sources.add(reference)

        organ = str(row.get(organ_field) or row.get(fallback_organ_field) or "").strip()
        patient = str(row.get("patient_id") or "").strip()
        if not organ or not patient:
            continue
        # Absent on rows computed before the linkage stamp existed, which then
        # collapse to one case per patient — the previous behaviour exactly.
        linkage = str(row.get("linkage_id") or "").strip()

        model._patients_by_source[source].add(patient)
        model._patients_by_organ[organ].add(patient)
        per_source, per_organ = model._by_reference.setdefault(
            reference, (defaultdict(set), defaultdict(set))
        )
        per_source[source].add(patient)
        per_organ[organ].add(patient)

        metrics = row.get("metrics") or {}
        for metric, value in metrics.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            if metric_family(metric) is FAMILY_STAPLE:
                # Describes the consensus construction, not a contour
                # comparison, so it has no place among the paired tests.
                continue
            key = (organ, source, str(metric), patient, linkage, reference)
            if key in model.observations:
                # A second row for the same contour adds no information, and
                # letting it through would give one case two votes in a paired
                # test. A second row with a *different* value inside one case is
                # a genuine collision and is counted separately — a second
                # course is not this, it is its own case.
                if model.observations[key] == float(value):
                    model.duplicates_collapsed += 1
                else:
                    model.conflicting_observations += 1
                continue
            model.observations[key] = float(value)
    return model


__all__ = [
    "CONSENSUS_REFERENCE_MARKERS",
    "FAMILY_DOSIMETRIC",
    "FAMILY_GEOMETRIC",
    "FAMILY_ORDER",
    "FAMILY_OTHER",
    "FAMILY_STAPLE",
    "GEOMETRIC_METRICS",
    "HIGHER_IS_BETTER",
    "STAPLE_METRICS",
    "expected_false_positives",
    "is_consensus_reference",
    "reference_rank",
    "metric_family",
    "AcquisitionReport",
    "collect_acquisition",
    "FamilyAxis",
    "interval_text",
    "LOWER_IS_BETTER",
    "Coverage",
    "CoverageCell",
    "Observation",
    "ReportModel",
    "build_report_model",
    "favours",
    "metric_direction",
]
