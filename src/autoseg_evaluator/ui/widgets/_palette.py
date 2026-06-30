"""Shared colourblind-friendly palette for contour overlays.

Used by the qualitative-assessment multiplanar viewer (and available to any
other overlay UI). Colours are the Wong / Tol-derived set also used by the
matplotlib quick-view popup.
"""

from __future__ import annotations

# Reference colour for the ground-truth / manual contour.
GT_COLOR = "#F0B400"  # amber

# Test sources cycle through this colourblind-distinguishable palette.
TEST_PALETTE: list[str] = [
    "#E41A1C",  # red
    "#377EB8",  # blue
    "#4DAF4A",  # green
    "#984EA3",  # purple
    "#FF7F00",  # orange
    "#A65628",  # brown
    "#F781BF",  # pink
    "#999999",  # grey
]


def color_for_index(index: int) -> str:
    """Return a stable test-source colour for the given 0-based index."""
    return TEST_PALETTE[index % len(TEST_PALETTE)]
