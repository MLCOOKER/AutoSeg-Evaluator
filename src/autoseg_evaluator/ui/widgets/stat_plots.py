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

#: Organ labels longer than this are elided on the axis. Long TG-263 names
#: rotated at 30 degrees are the single largest consumer of vertical space, and
#: they are the reason the layout collapses at all.
MAX_TICK_LABEL = 22


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
    """Paired differences against one reference source, one row per family member."""

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
        """
        self.clear()
        axes = self.figure.add_subplot(111)
        usable = [
            (organ, result)
            for organ, result in results.items()
            if result is not None and result.hl_estimate is not None
        ]
        dropped = 0
        if scales is not None:
            kept = [(o, r) for o, r in usable if scales.get(o)]
            dropped = len(usable) - len(kept)
            usable = kept
        if not usable:
            axes.text(0.5, 0.5, "Nothing to compare", ha="center", va="center")
            axes.set_axis_off()
            self.draw_idle()
            return

        def rescale(value: float | None, organ: str) -> float | None:
            if value is None:
                return None
            if scales is None:
                return value
            denominator = scales.get(organ)
            return None if not denominator else value / abs(denominator) * 100.0

        usable.sort(key=lambda item: rescale(item[1].hl_estimate, item[0]) or 0.0)
        positions = range(len(usable))

        for y, (_organ, result) in zip(positions, usable, strict=True):
            estimate = rescale(result.hl_estimate, _organ)
            significant = result.p_value <= alpha
            colour = "#0F6E6E" if significant else "#4A5866"

            low = rescale(result.ci_low, _organ)
            high = rescale(result.ci_high, _organ)
            if result.ci_available and low is not None and high is not None:
                axes.plot(
                    [low, high],
                    [y, y],
                    color=colour,
                    linewidth=1.6,
                    solid_capstyle="butt",
                    zorder=2,
                )
                for endpoint in (low, high):
                    axes.plot(
                        [endpoint, endpoint],
                        [y - 0.16, y + 0.16],
                        color=colour,
                        linewidth=1.6,
                        zorder=2,
                    )
            else:
                # No finite interval exists at this sample size — shown as an
                # open span rather than omitted, so the row is not mistaken for
                # a precise estimate.
                axes.annotate(
                    "no interval at this n",
                    (estimate, y),
                    textcoords="offset points",
                    xytext=(10, -3),
                    fontsize=7,
                    color="#8A5414",
                )
            axes.scatter(
                [estimate],
                [y],
                s=46,
                zorder=4,
                color=colour if significant else "white",
                edgecolors=colour,
                linewidths=1.6,
            )

        axes.axvline(0.0, color="#4A5866", linestyle="--", linewidth=1, zorder=1)
        axes.set_yticks(list(positions))
        axes.set_yticklabels(
            [f"{_elide(organ)}   n={result.n_pairs}" for organ, result in usable], fontsize=8
        )
        measured = challenger or "each source"
        if scales is None:
            axis_units = f" {units}" if units else ""
            axes.set_xlabel(
                f"Hodges–Lehmann difference in {metric}{axis_units}   ({measured} − {reference})"
            )
        else:
            axes.set_xlabel(
                f"Hodges–Lehmann difference in {metric}, % of {reference}'s median"
                f"   ({measured} − {reference})"
            )
        axes.grid(axis="x", alpha=0.25, linewidth=0.6)
        axes.set_axisbelow(True)

        direction = metric_direction(metric)
        if direction:
            rows = challenger or "the source in each row"
            better, worse = (rows, reference) if direction > 0 else (reference, rows)
            axes.set_title(
                f"← favours {worse}          favours {better} →", fontsize=8, color="#4A5866"
            )

        axes.margins(x=0.18, y=0.08)
        self.figure.text(
            0.01,
            0.01,
            "Filled markers are significant at p <= 0.05 for that row alone; p-values "
            "and intervals are per organ and uncorrected."
            + (
                f"  {dropped} row(s) omitted: the reference median is zero, so a "
                "relative difference is undefined."
                if dropped
                else ""
            )
            + (
                ""
                if challenger
                else "  Every row shares the same reference arm, so rows are correlated "
                "and do not compare the sources with each other."
            ),
            fontsize=7,
            color="#7A8794",
        )
        self.draw_idle()


__all__ = [
    "MAX_TICK_LABEL",
    "MIN_CANVAS_HEIGHT",
    "MIN_CANVAS_WIDTH",
    "MIN_N_FOR_VIOLIN",
    "DistributionCanvas",
    "ForestCanvas",
]
