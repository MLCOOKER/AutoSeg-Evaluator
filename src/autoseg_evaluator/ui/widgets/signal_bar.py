"""Colourblind-safe similarity indicator widget.

Renders an 8-pip bar where the number of filled pips is proportional to
the similarity score. The colour is **blue** when at-or-above the
similarity threshold and **amber** when below — both wavelengths remain
distinguishable for all common forms of colour blindness, and the bar
length itself carries the same information independent of hue.

Pair this widget with a numeric label and a ⚠ icon to provide three
redundant signals (length, colour, glyph) so users can rely on whichever
they prefer.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget


class SignalBar(QWidget):
    """A small fixed-size pip strip showing 0..similarity..1 visually."""

    PIP_COUNT = 8
    PIP_WIDTH = 8
    PIP_GAP = 2
    PIP_HEIGHT = 12

    # Colourblind-safe palette: blue → amber, both distinct under deuteranopia,
    # protanopia, and tritanopia, with high luminance contrast against the grey.
    COLOR_HIGH = QColor("#1976D2")   # blue
    COLOR_LOW = QColor("#E69500")    # amber
    COLOR_EMPTY = QColor("#D0D0D0")  # neutral grey

    def __init__(
        self,
        similarity: float = 0.0,
        below_threshold: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._similarity = self._clamp(similarity)
        self._below_threshold = bool(below_threshold)
        total_width = (
            self.PIP_COUNT * self.PIP_WIDTH + (self.PIP_COUNT - 1) * self.PIP_GAP + 2
        )
        self.setFixedSize(total_width, self.PIP_HEIGHT + 2)

    # ---- API -------------------------------------------------------------

    def set_similarity(self, similarity: float, below_threshold: bool = False) -> None:
        new_sim = self._clamp(similarity)
        new_flag = bool(below_threshold)
        if new_sim == self._similarity and new_flag == self._below_threshold:
            return
        self._similarity = new_sim
        self._below_threshold = new_flag
        self.update()

    def similarity(self) -> float:
        return self._similarity

    def below_threshold(self) -> bool:
        return self._below_threshold

    def filled_pips(self) -> int:
        """Number of pips currently rendered as filled (0..PIP_COUNT)."""
        return max(0, min(self.PIP_COUNT, round(self._similarity * self.PIP_COUNT)))

    # ---- Painting --------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        fill = self.COLOR_LOW if self._below_threshold else self.COLOR_HIGH
        n_filled = self.filled_pips()
        for i in range(self.PIP_COUNT):
            x = 1 + i * (self.PIP_WIDTH + self.PIP_GAP)
            y = 1
            color = fill if i < n_filled else self.COLOR_EMPTY
            painter.fillRect(QRect(x, y, self.PIP_WIDTH, self.PIP_HEIGHT), color)

    # ---- Helpers ---------------------------------------------------------

    @staticmethod
    def _clamp(value: float) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return 0.0
        if v < 0.0:
            return 0.0
        if v > 1.0:
            return 1.0
        return v
