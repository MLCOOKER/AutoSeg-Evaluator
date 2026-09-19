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


def _elide(text: str, limit: int = MAX_TICK_LABEL) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


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
        title: str = "",
    ) -> None:
        """``data`` is ``{organ: {source: [values]}}``."""
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
        axes.set_xticklabels(
            [
                f"{_elide(organ)}\n(n={max((len(v) for v in data[organ].values()), default=0)})"
                for organ in organs
            ],
            rotation=30,
            ha="right",
            fontsize=8,
        )
        axes.set_ylabel(metric)
        direction = metric_direction(metric)
        if direction:
            axes.set_ylabel(f"{metric}  ({'higher' if direction > 0 else 'lower'} is better)")
        if title:
            axes.set_title(title, fontsize=10)
        axes.grid(axis="y", alpha=0.25, linewidth=0.6)
        axes.set_axisbelow(True)
        for source in sources:
            axes.scatter([], [], color=colours[source], label=source, s=22)
        # Outside the axes, to the right. Inside, it sits over the data — and
        # with several sources and rotated organ labels there is no corner it
        # can occupy without covering points.
        axes.legend(
            fontsize=8,
            frameon=False,
            loc="upper left",
            bbox_to_anchor=(1.01, 1.0),
            borderaxespad=0.0,
        )
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
]
