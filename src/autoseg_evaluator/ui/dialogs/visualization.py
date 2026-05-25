"""Visualization popup — slice-by-slice CT viewer with GT and test contours overlaid.

A modal QDialog hosting a matplotlib FigureCanvasQTAgg. The user scrolls
through axial slices via a slider (or the keyboard arrow keys); the GT
contour is drawn in a fixed reference colour and each test source gets a
distinct colour drawn from a colourblind-friendly palette. A legend lists
which colour belongs to which source.

This is intentionally a basic 2D axial viewer — enough for sanity-checking
outliers and verifying the auto-match landed on the right structure. The
v1 paper's §2.5 ("Visualization") describes the same minimal feature.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
import SimpleITK as sitk
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)


# Reference colour for GT (yellow). Test sources cycle through this
# colourblind-distinguishable palette (Wong / Tol-derived).
_GT_COLOR = "#F0B400"
_TEST_PALETTE = [
    "#E41A1C",  # red
    "#377EB8",  # blue
    "#4DAF4A",  # green
    "#984EA3",  # purple
    "#FF7F00",  # orange
    "#A65628",  # brown
    "#F781BF",  # pink
    "#999999",  # grey
]


class VisualizationWindow(QDialog):
    """Modal slice viewer for one patient's GT + tests against the reference CT."""

    def __init__(
        self,
        ct_image: sitk.Image,
        gt_mask: sitk.Image,
        gt_label: str,
        test_masks_with_labels: Sequence[tuple[str, sitk.Image]],
        title: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(960, 800)
        self.setSizeGripEnabled(True)

        self._ct_arr = sitk.GetArrayFromImage(ct_image)  # shape (z, y, x)
        self._gt_arr = sitk.GetArrayFromImage(gt_mask)
        self._gt_label = gt_label
        self._tests: list[tuple[str, np.ndarray]] = [
            (label, sitk.GetArrayFromImage(m)) for label, m in test_masks_with_labels
        ]
        self._test_visible: list[bool] = [True] * len(self._tests)
        self._gt_visible = True
        self._n_slices = int(self._ct_arr.shape[0])

        # Default to the middle-of-GT slice if GT non-empty, else mid-volume
        z_with_gt = np.any(self._gt_arr > 0, axis=(1, 2))
        if z_with_gt.any():
            zs = np.nonzero(z_with_gt)[0]
            self._current_slice = int((zs.min() + zs.max()) // 2)
        else:
            self._current_slice = self._n_slices // 2

        self._build_ui()
        self._refresh_plot()

    # ---- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # Matplotlib canvas — claim almost the entire figure for the image
        self._figure = Figure(figsize=(10, 10))
        self._figure.subplots_adjust(left=0.01, right=0.99, top=0.96, bottom=0.01)
        self._canvas = FigureCanvas(self._figure)
        self._ax = self._figure.add_subplot(111)
        self._figure.patch.set_facecolor("#000000")
        self._ax.set_facecolor("#000000")
        # Mouse-wheel slice navigation on the image
        self._canvas.mpl_connect("scroll_event", self._on_canvas_scroll)
        outer.addWidget(self._canvas, stretch=1)

        # Slider row
        slider_row = QHBoxLayout()
        slider_row.addWidget(QLabel("Slice:"))
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, max(0, self._n_slices - 1))
        self._slider.setValue(self._current_slice)
        self._slider.valueChanged.connect(self._on_slice_changed)
        slider_row.addWidget(self._slider, stretch=1)
        self._slice_label = QLabel(self._format_slice_label())
        slider_row.addWidget(self._slice_label)
        outer.addLayout(slider_row)

        # Legend / visibility toggles
        legend_scroll = QScrollArea()
        legend_scroll.setWidgetResizable(True)
        legend_scroll.setMaximumHeight(140)
        legend_inner = QWidget()
        legend_layout = QVBoxLayout(legend_inner)
        legend_layout.setContentsMargins(4, 4, 4, 4)
        legend_layout.setSpacing(2)

        gt_row = self._make_legend_row(_GT_COLOR, f"GT — {self._gt_label}", checked=True)
        gt_row.checkbox.toggled.connect(self._on_gt_toggled)
        legend_layout.addWidget(gt_row)

        self._test_rows: list[_LegendRow] = []
        for i, (label, _arr) in enumerate(self._tests):
            color = _TEST_PALETTE[i % len(_TEST_PALETTE)]
            row = self._make_legend_row(color, label, checked=True)
            row.checkbox.toggled.connect(lambda checked, idx=i: self._on_test_toggled(idx, checked))
            legend_layout.addWidget(row)
            self._test_rows.append(row)

        legend_layout.addStretch(1)
        legend_scroll.setWidget(legend_inner)
        outer.addWidget(legend_scroll)

        # Close button
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        outer.addLayout(btn_row)

    @staticmethod
    def _make_legend_row(color: str, label: str, checked: bool) -> "_LegendRow":
        return _LegendRow(color, label, checked)

    # ---- Event handlers ---------------------------------------------------

    def _on_slice_changed(self, value: int) -> None:
        self._current_slice = int(value)
        self._slice_label.setText(self._format_slice_label())
        self._refresh_plot()

    def _on_gt_toggled(self, checked: bool) -> None:
        self._gt_visible = checked
        self._refresh_plot()

    def _on_test_toggled(self, index: int, checked: bool) -> None:
        if 0 <= index < len(self._test_visible):
            self._test_visible[index] = checked
            self._refresh_plot()

    def keyPressEvent(self, event):
        # Arrow keys navigate slices
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Right):
            self._slider.setValue(min(self._slider.value() + 1, self._slider.maximum()))
            return
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Left):
            self._slider.setValue(max(self._slider.value() - 1, 0))
            return
        super().keyPressEvent(event)

    def _on_canvas_scroll(self, event) -> None:
        """Mouse-wheel scroll over the image → advance slice up/down."""
        # ``event.button`` is 'up' or 'down' for vertical wheel events
        if event.button == "up":
            delta = 1
        elif event.button == "down":
            delta = -1
        else:
            return
        new_value = max(0, min(self._slider.maximum(), self._slider.value() + delta))
        self._slider.setValue(new_value)

    # ---- Drawing ----------------------------------------------------------

    def _refresh_plot(self) -> None:
        self._ax.clear()
        self._ax.set_facecolor("#000000")
        if self._n_slices == 0:
            self._canvas.draw_idle()
            return
        ct_slice = self._ct_arr[self._current_slice]
        vmin, vmax = _window_level(ct_slice)
        self._ax.imshow(
            ct_slice,
            cmap="gray",
            aspect="equal",
            vmin=vmin,
            vmax=vmax,
            interpolation="nearest",
        )
        # Draw test contours FIRST (so GT lands on top of every test).
        for i, (_label, arr) in enumerate(self._tests):
            if not self._test_visible[i]:
                continue
            if self._current_slice >= arr.shape[0]:
                continue
            tslice = arr[self._current_slice]
            if not tslice.any():
                continue
            color = _TEST_PALETTE[i % len(_TEST_PALETTE)]
            self._ax.contour(tslice, levels=[0.5], colors=[color], linewidths=0.7)
        # GT contour painted last → guaranteed to render above the tests
        if self._gt_visible and self._current_slice < self._gt_arr.shape[0]:
            gt_slice = self._gt_arr[self._current_slice]
            if gt_slice.any():
                self._ax.contour(
                    gt_slice, levels=[0.5], colors=[_GT_COLOR], linewidths=1.2,
                )
        self._ax.set_title(
            f"Slice {self._current_slice + 1} / {self._n_slices}",
            color="white",
            fontsize=10,
        )
        self._ax.axis("off")
        self._canvas.draw_idle()

    def _format_slice_label(self) -> str:
        return f"{self._current_slice + 1} / {self._n_slices}"


# ---- Legend row widget ---------------------------------------------------


class _LegendRow(QWidget):
    """One row in the legend: colour swatch + label + visibility checkbox."""

    def __init__(self, color: str, label: str, checked: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.checkbox = QCheckBox(self)
        self.checkbox.setChecked(checked)
        layout.addWidget(self.checkbox)

        swatch = QLabel(self)
        swatch.setFixedSize(20, 14)
        # Mid-grey border reads on both light and dark backgrounds.
        swatch.setStyleSheet(
            f"background-color: {color}; border: 1px solid #888; border-radius: 2px;"
        )
        layout.addWidget(swatch)

        # No explicit colour — let the application's theme palette decide so the
        # label is readable in both light AND dark mode. Previously we used
        # ``color: inherit`` which is not valid in Qt stylesheets and silently
        # forced the text to default black, making it unreadable on the dark
        # background.
        text = QLabel(label, self)
        layout.addWidget(text, stretch=1)


# ---- Helpers --------------------------------------------------------------


def _window_level(slice_arr: np.ndarray) -> tuple[float, float]:
    """Pick a reasonable display window/level for a CT slice via robust percentiles."""
    finite = slice_arr[np.isfinite(slice_arr)]
    if finite.size == 0:
        return 0.0, 1.0
    lo = float(np.percentile(finite, 1))
    hi = float(np.percentile(finite, 99))
    if hi <= lo:
        hi = lo + 1.0
    return lo, hi
