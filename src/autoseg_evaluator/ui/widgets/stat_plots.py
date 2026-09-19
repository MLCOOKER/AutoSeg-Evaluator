"""Matplotlib canvases for the Report tab.

Two figures, each shaped by what small samples can honestly show.

**Distributions** are drawn as individual points with median and quartile marks.
A violin is overlaid only once there are enough observations for a density
estimate to mean anything — at ten points the bandwidth invents the shape, and
the shape is what a reader takes away. Below that threshold the points are the
figure, which is also the honest answer to whether a distribution is bimodal.

**Forest** plots the Hodges–Lehmann difference and its interval per organ
against one chosen reference source. Each row is its own question, so the
interval and the marker agree by construction — both come from the same
uncorrected test on that organ. Nothing about a row changes because another row
is on screen.
"""

from __future__ import annotations

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import QSizePolicy

from autoseg_evaluator.core.readable import (
    SCALE_BOUNDED_UNIT,
    SCALE_NON_NEGATIVE,
    SCALE_SIGNED,
    metric_scale,
)
from autoseg_evaluator.data.report import metric_direction

#: Below this many observations a kernel density estimate says more about the
#: bandwidth than about the data, so only the points are drawn.
MIN_N_FOR_VIOLIN = 15

_PALETTE = [
    "#0F6E6E",
    "#A6641C",
    "#4A5F8E",
    "#8C2F39",
    "#3F7A3F",
    "#6B4E8C",
    "#996515",
]


#: Smallest canvas that still leaves room for the axes once the decorations —
#: rotated organ labels, the legend, the axis title — have been placed.
#:
#: Without a floor the splitter can squeeze a canvas until constrained_layout
#: gives up ("axes sizes collapsed to zero") and the figure renders essentially
#: blank, with only a warning on stderr that no user sees. Measured: at 14
#: organs with long names the layout survives 6.0 x 2.0 inches and fails by
#: 4.0 x 1.2, so the floor sits comfortably above that. The tab scrolls, so a
#: minimum costs nothing but a scrollbar on a short window.
MIN_CANVAS_WIDTH = 360
MIN_CANVAS_HEIGHT = 260

#: The forest needs more width than the distributions do: its row labels sit
#: beside the interval rather than under it, so the two compete for the same
#: horizontal space. Measured with labels at the 30-character elision limit —
#: clean from 540 px, collapsing below 500 — so the floor keeps some headroom.
MIN_FOREST_WIDTH = 620

#: Organ labels longer than this are elided on the axis. Long TG-263 names
#: rotated at 30 degrees are the single largest consumer of vertical space, and
#: they are the reason the layout collapses at all.
MAX_TICK_LABEL = 22

#: Figure ink. Darker than matplotlib's defaults because these figures are read
#: in slides and printed manuscripts, not only on the screen that drew them.
_TEXT = "#22272E"
_MUTED_TEXT = "#5A6572"
_GRID = "#D6DAE0"
_ZERO_LINE = "#5A6572"
_SIGNIFICANT = "#0F6E6E"
_NOT_SIGNIFICANT = "#6B7683"
_BETTER = "#0F6E6E"
_WORSE = "#B06A1E"
_TIED = "#A8AFB8"


def _elide(text: str, limit: int = MAX_TICK_LABEL) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


#: Said in full because none of it is guessable from the marks themselves: a
#: vertical bar could be a range, an error bar or an interval, and the counts
#: above the clusters are per source rather than per organ.
_DISTRIBUTION_CAPTION = "\n".join(
    (
        "Each dot is one patient; the number above a cluster is how many that source produced.",
        "The vertical bar spans the interquartile range (25th–75th percentile); "
        "the wide tick is the median.",
        "Every available observation is shown — these counts are not the paired-comparison sample.",
    )
)


def _apply_scale(axes, metric: str, values: list[float]) -> None:
    """Bound a value axis by what the metric can be, not by what it happened to be.

    Autoscaling flatters small differences. Ten Dice values between 0.78 and
    0.86 autoscale to an axis where 0.01 spans a third of the figure, and a
    reader who does not check the ticks reads a chasm. On the full 0-1 range the
    same figure shows four good contours differing slightly, which is the truth.

    For distances the floor is zero — a perfect contour — and hiding it hides how
    far from perfect everything on the axis is.
    """
    finite = [float(v) for v in values if v is not None and np.isfinite(v)]
    scale = metric_scale(metric)
    if scale == SCALE_BOUNDED_UNIT:
        axes.set_ylim(-0.02, 1.02)
        return
    if not finite:
        return
    top = max(finite)
    if scale == SCALE_NON_NEGATIVE:
        axes.set_ylim(min(0.0, min(finite)), top * 1.08 if top > 0 else 1.0)
    elif scale == SCALE_SIGNED:
        reach = max(abs(min(finite)), abs(top)) or 1.0
        axes.set_ylim(-reach * 1.12, reach * 1.12)


class _Canvas(FigureCanvasQTAgg):
    def __init__(self, width: float = 9.0, height: float = 5.0) -> None:
        self.figure = Figure(figsize=(width, height), layout="constrained")
        super().__init__(self.figure)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(MIN_CANVAS_WIDTH, MIN_CANVAS_HEIGHT)

    def clear(self) -> None:
        self.figure.clear()


class DistributionCanvas(_Canvas):
    """Per-organ distributions, one colour per source."""

    def plot(
        self,
        data: dict[str, dict[str, list[float]]],
        metric: str,
        *,
        display: str = "",
        title: str = "",
    ) -> None:
        """``data`` is ``{organ: {source: [values]}}``.

        ``metric`` is the metric **key**, because direction is looked up from it.
        ``display`` is what the axis says. They are separate parameters because
        passing the label where the key belongs silently disables the direction
        logic — the lookup misses and every metric reads as undirected.
        """
        label = display or metric
        self.clear()
        axes = self.figure.add_subplot(111)
        organs = list(data.keys())
        if not organs:
            axes.text(0.5, 0.5, "No data to plot", ha="center", va="center")
            axes.set_axis_off()
            self.draw_idle()
            return

        sources = sorted({s for per_source in data.values() for s in per_source})
        colours = {s: _PALETTE[i % len(_PALETTE)] for i, s in enumerate(sources)}
        width = 0.8 / max(len(sources), 1)
        rng = np.random.default_rng(0)  # reproducible jitter

        for organ_index, organ in enumerate(organs):
            for source_index, source in enumerate(sources):
                values = [
                    v for v in data[organ].get(source, []) if v is not None and np.isfinite(v)
                ]
                if not values:
                    continue
                centre = organ_index + (source_index - (len(sources) - 1) / 2) * width
                colour = colours[source]

                if len(values) >= MIN_N_FOR_VIOLIN:
                    body = axes.violinplot(
                        values,
                        positions=[centre],
                        widths=width * 0.9,
                        showextrema=False,
                        showmedians=False,
                    )
                    for part in body["bodies"]:
                        part.set_facecolor(colour)
                        part.set_alpha(0.22)
                        part.set_edgecolor("none")

                jitter = rng.uniform(-width * 0.2, width * 0.2, len(values))
                axes.scatter(
                    centre + jitter,
                    values,
                    s=16,
                    color=colour,
                    alpha=0.8,
                    edgecolors="none",
                    zorder=3,
                )
                axes.annotate(
                    str(len(values)),
                    (centre, max(values)),
                    textcoords="offset points",
                    xytext=(0, 7),
                    ha="center",
                    fontsize=8,
                    color=colour,
                )
                q1, median, q3 = np.percentile(values, [25, 50, 75])
                axes.plot(
                    [centre, centre], [q1, q3], color=colour, linewidth=2.2, alpha=0.9, zorder=4
                )
                axes.plot(
                    [centre - width * 0.34, centre + width * 0.34],
                    [median, median],
                    color=colour,
                    linewidth=2.6,
                    zorder=5,
                )

        # A hairline between organ groups. With several sources per organ the
        # clusters run together, and the eye has to count colours to work out
        # where one organ ends.
        for boundary in range(1, len(organs)):
            axes.axvline(boundary - 0.5, color="#C8CDD4", linewidth=0.8, zorder=0, linestyle="-")

        axes.set_xticks(range(len(organs)))
        # No aggregate n on the organ label. One number under an organ whose
        # sources have different coverage says "this organ has ten patients",
        # which is exactly the thing that is not true; the per-source counts
        # above each cluster say what each source actually produced.
        axes.set_xticklabels(
            [_elide(organ) for organ in organs],
            rotation=30,
            ha="right",
            fontsize=9.5,
            color=_TEXT,
        )
        axes.set_ylabel(label)
        direction = metric_direction(metric)
        if direction:
            axes.set_ylabel(f"{label}  ({'higher' if direction > 0 else 'lower'} is better)")
        if title:
            axes.set_title(title, fontsize=10)
        _apply_scale(
            axes, metric, [v for per in data.values() for vals in per.values() for v in vals]
        )
        axes.grid(axis="y", color=_GRID, alpha=0.6, linewidth=0.7)
        axes.set_axisbelow(True)
        for source in sources:
            axes.scatter([], [], color=colours[source], label=source, s=22)
        # Outside the axes, to the right. Inside, it sits over the data — and
        # with several sources and rotated organ labels there is no corner it
        # can occupy without covering points.
        axes.legend(
            fontsize=9.5,
            frameon=False,
            loc="upper left",
            bbox_to_anchor=(1.01, 1.0),
            borderaxespad=0.0,
            labelcolor=_TEXT,
        )
        self.figure.supxlabel(_DISTRIBUTION_CAPTION, fontsize=9, color=_MUTED_TEXT, ha="center")
        self.draw_idle()


class PairedCanvas(_Canvas):
    """Every patient's two values, joined.

    The distribution figure shows what each source produced; it cannot show
    which two points came from the same patient, and that is the whole basis of
    a paired test. Two sources can have near-identical distributions while every
    patient moved the same way, and identical distributions while the movement
    was noise — the summary looks the same in both cases and the conclusion is
    opposite.

    So each patient is one line between two columns, following Weissgerber's
    argument that a summary statistic should not be the only thing shown when
    the individual observations are what carry the claim.
    """

    def plot(
        self,
        pairs: list[tuple[str, float, float]],
        metric: str,
        *,
        display: str = "",
        reference: str,
        challenger: str,
        organ: str = "",
        units: str = "",
        subtitle: str = "",
    ) -> None:
        """``pairs`` is ``[(patient, reference value, challenger value)]``.

        ``metric`` is the metric **key** — direction is looked up from it, and a
        display label passed here would silently colour every line as tied.
        ``display`` is what the reader sees.
        """
        label = display or metric
        self.clear()
        axes = self.figure.add_subplot(111)
        if not pairs:
            axes.text(
                0.5,
                0.5,
                "No patient has both sources for this organ",
                ha="center",
                va="center",
                fontsize=11,
                color=_MUTED_TEXT,
            )
            axes.set_axis_off()
            self.draw_idle()
            return

        direction = metric_direction(metric)
        improved = worsened = tied = 0
        for _patient, before, after in pairs:
            change = after - before
            if change == 0 or not direction:
                colour, width = _TIED, 1.0
                tied += change == 0
            elif (change > 0) == (direction > 0):
                colour, width = _BETTER, 1.2
                improved += 1
            else:
                colour, width = _WORSE, 1.2
                worsened += 1
            axes.plot([0, 1], [before, after], color=colour, linewidth=width, alpha=0.75, zorder=2)

        befores = [before for _p, before, _a in pairs]
        afters = [after for _p, _b, after in pairs]
        for x, values in ((0, befores), (1, afters)):
            axes.scatter(
                [x] * len(values),
                values,
                s=38,
                zorder=4,
                color="white",
                edgecolors=_TEXT,
                linewidths=1.3,
            )
            median = float(np.median(values))
            axes.plot(
                [x - 0.12, x + 0.12],
                [median, median],
                color=_TEXT,
                linewidth=2.4,
                zorder=5,
                solid_capstyle="round",
            )
            axes.annotate(
                f"median {median:.3f}",
                (x, median),
                textcoords="offset points",
                xytext=(0, 10),
                ha="center",
                fontsize=9,
                color=_TEXT,
            )

        axes.set_xlim(-0.45, 1.45)
        axes.set_xticks([0, 1])
        axes.set_xticklabels([reference, challenger], fontsize=11, color=_TEXT)
        axes.tick_params(axis="x", length=0)
        axes.tick_params(axis="y", labelsize=9.5, colors=_TEXT, length=3)
        unit_text = f" ({units})" if units else ""
        axes.set_ylabel(f"{label}{unit_text}", fontsize=10, color=_TEXT)
        _apply_scale(axes, metric, befores + afters)
        axes.grid(axis="y", color=_GRID, alpha=0.6, linewidth=0.7, zorder=0)
        axes.set_axisbelow(True)
        for spine in ("top", "right"):
            axes.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            axes.spines[spine].set_color(_GRID)

        title = f"{organ}: {label}" if organ else label
        axes.set_title(title, fontsize=12, color=_TEXT, fontweight="bold", loc="left", pad=16)
        if subtitle:
            axes.annotate(
                subtitle,
                xy=(0.0, 1.0),
                xycoords="axes fraction",
                xytext=(0, 7),
                textcoords="offset points",
                ha="left",
                va="bottom",
                fontsize=9.5,
                color=_MUTED_TEXT,
            )

        caption = [
            f"{len(pairs)} patients, one line each — exactly the patients the paired "
            "test used for this row, which may not be the same patients as another row."
        ]
        if direction:
            caption.append(
                f"{improved} improved with {challenger}, {worsened} worsened"
                + (f", {tied} identical" if tied else "")
                + ". A line's colour is that patient's direction, not its size."
            )
        else:
            caption.append(f"{metric} has no better or worse direction, so lines are not coloured.")
        self.figure.supxlabel("\n".join(caption), fontsize=9, color=_MUTED_TEXT, ha="center")
        self.draw_idle()


class ForestCanvas(_Canvas):
    """Paired differences against one reference source, one row per member.

    Sized to its content rather than to a fixed box: a three-row comparison gets
    three rows of height. A forest with generous row spacing and a two-inch gap
    under the last row reads as though data are missing.

    Type is larger and darker than a default matplotlib figure, because these
    are read in slides and printed manuscripts rather than on the screen that
    drew them, and the caption sits in reserved space below the axis instead of
    floating over the figure corner.
    """

    #: Vertical space per row, in inches. Tight enough that ten organs stay on
    #: one screen, loose enough that the interval caps do not touch.
    ROW_INCHES = 0.30

    #: Everything that is not a row: titles, axis, tick labels, caption.
    CHROME_INCHES = 2.35

    def __init__(self, width: float = 9.0, height: float = 5.0) -> None:
        super().__init__(width, height)
        self.setMinimumWidth(MIN_FOREST_WIDTH)

    def plot(
        self,
        results: dict,
        metric: str,
        *,
        reference: str,
        challenger: str | None,
        alpha: float = 0.05,
        scales: dict[str, float] | None = None,
        units: str = "",
        title: str = "",
        subtitle: str = "",
        sort_by_effect: bool = False,
    ) -> None:
        """``results`` is ``{label: PairedResult}`` from either family method.

        ``challenger`` is the single source every row was measured against, or
        ``None`` when the rows *are* the sources. In that second case no row can
        be labelled with one challenger's name, and the direction annotation has
        to say "the source in each row" instead — writing one vendor's name
        across a figure comparing several would be a plain misstatement.

        ``scales`` divides each row by a per-row denominator — the reference's
        own median — turning the axis into a relative one. Raw units are the
        default because they are what a clinician judges and what goes in a
        paper, but on an unbounded metric they make organs incomparable: a 25%
        degradation is 10 mm on bowel and 0.4 mm on a cochlea, and on a shared
        axis the cochlea collapses onto zero. A row whose denominator is zero or
        missing cannot be expressed relatively and is dropped, with the figure
        saying how many.

        ``sort_by_effect`` reorders rows largest-difference-first. Off by
        default: the caller's order is anatomical or vendor order and is stable
        across metrics, so a reader comparing two figures finds the same row in
        the same place. Sorting by effect moves rows whenever the data move.
        """
        self.clear()
        axes = self.figure.add_subplot(111)
        usable = [
            (label, result)
            for label, result in results.items()
            if result is not None and result.hl_estimate is not None
        ]
        dropped = 0
        if scales is not None:
            kept = [(label, r) for label, r in usable if scales.get(label)]
            dropped = len(usable) - len(kept)
            usable = kept

        if not usable:
            self._resize_for(0)
            axes.text(0.5, 0.5, "Nothing to compare", ha="center", va="center", fontsize=11)
            axes.set_axis_off()
            self.draw_idle()
            return

        def rescale(value: float | None, label: str) -> float | None:
            if value is None:
                return None
            if scales is None:
                return value
            denominator = scales.get(label)
            return None if not denominator else value / abs(denominator) * 100.0

        if sort_by_effect:
            usable.sort(key=lambda item: rescale(item[1].hl_estimate, item[0]) or 0.0)
        else:
            # Drawn bottom-up, so reverse the caller's order to keep the first
            # row at the top where a reader looks for it.
            usable.reverse()

        self._resize_for(len(usable))
        positions = range(len(usable))

        for y, (label, result) in zip(positions, usable, strict=True):
            estimate = rescale(result.hl_estimate, label)
            significant = result.p_value <= alpha
            colour = _SIGNIFICANT if significant else _NOT_SIGNIFICANT

            low = rescale(result.ci_low, label)
            high = rescale(result.ci_high, label)
            if result.ci_available and low is not None and high is not None:
                axes.plot(
                    [low, high],
                    [y, y],
                    color=colour,
                    linewidth=1.8,
                    solid_capstyle="butt",
                    zorder=3,
                )
                for endpoint in (low, high):
                    axes.plot(
                        [endpoint, endpoint],
                        [y - 0.14, y + 0.14],
                        color=colour,
                        linewidth=1.8,
                        zorder=3,
                    )
            else:
                axes.annotate(
                    "no interval at this n",
                    (estimate, y),
                    textcoords="offset points",
                    xytext=(11, -3),
                    fontsize=8.5,
                    color=_MUTED_TEXT,
                )
            axes.scatter(
                [estimate],
                [y],
                s=52,
                zorder=5,
                color=colour if significant else "white",
                edgecolors=colour,
                linewidths=1.8,
            )

        # The zero line carries the whole reading, so it stays prominent while
        # the reference grid drops back.
        axes.axvline(0.0, color=_ZERO_LINE, linestyle="-", linewidth=1.5, zorder=2)
        axes.grid(axis="x", color=_GRID, alpha=0.6, linewidth=0.7, zorder=0)
        axes.set_axisbelow(True)
        for spine in ("top", "right", "left"):
            axes.spines[spine].set_visible(False)
        axes.spines["bottom"].set_color(_GRID)

        axes.set_yticks(list(positions))
        axes.set_yticklabels(
            [f"{_elide(label, 30)}   n={result.n_pairs}" for label, result in usable],
            fontsize=10,
            color=_TEXT,
        )
        axes.tick_params(axis="x", labelsize=9.5, colors=_TEXT, length=3)
        axes.tick_params(axis="y", length=0)

        if scales is None:
            unit_text = f" ({units})" if units else ""
            axes.set_xlabel(f"Difference{unit_text}", fontsize=10, color=_TEXT)
        else:
            axes.set_xlabel(f"Difference, % of {reference}'s median", fontsize=10, color=_TEXT)

        if title:
            axes.set_title(title, fontsize=12, color=_TEXT, fontweight="bold", loc="left", pad=16)
        if subtitle:
            axes.annotate(
                subtitle,
                xy=(0.0, 1.0),
                xycoords="axes fraction",
                xytext=(0, 7),
                textcoords="offset points",
                ha="left",
                va="bottom",
                fontsize=9.5,
                color=_MUTED_TEXT,
            )

        direction = metric_direction(metric)
        caption = []
        if direction:
            rows_are = challenger or "the source in each row"
            better, worse = (rows_are, reference) if direction > 0 else (reference, rows_are)
            caption.append(f"← favours {worse}    |    favours {better} →")
        caption.append(
            "Filled marker: p ≤ 0.05 for that row alone. p-values and intervals are "
            "per row and uncorrected."
        )
        if challenger is None:
            caption.append(
                "Every row shares the same reference arm, so rows are correlated and "
                "do not compare the sources with each other."
            )
        if dropped:
            caption.append(
                f"{dropped} row(s) omitted: the reference median is zero, so a relative "
                "difference is undefined."
            )
        # supxlabel is laid out by constrained_layout, so the caption gets its
        # own reserved band instead of floating over the figure.
        self.figure.supxlabel("\n".join(caption), fontsize=9, color=_MUTED_TEXT, ha="center")

        axes.margins(x=0.20, y=0.10 if len(usable) > 2 else 0.30)
        self.draw_idle()

    def _resize_for(self, rows: int) -> None:
        """Height follows the row count; width is left to the layout."""
        inches = self.CHROME_INCHES + max(rows, 1) * self.ROW_INCHES
        width = self.figure.get_size_inches()[0]
        self.figure.set_size_inches(width, inches, forward=False)
        pixels = int(round(inches * self.figure.dpi))
        self.setMinimumHeight(max(pixels, MIN_CANVAS_HEIGHT))
        self.setMaximumHeight(max(pixels, MIN_CANVAS_HEIGHT))


__all__ = [
    "MAX_TICK_LABEL",
    "MIN_CANVAS_HEIGHT",
    "MIN_CANVAS_WIDTH",
    "MIN_N_FOR_VIOLIN",
    "DistributionCanvas",
    "ForestCanvas",
    "PairedCanvas",
]
