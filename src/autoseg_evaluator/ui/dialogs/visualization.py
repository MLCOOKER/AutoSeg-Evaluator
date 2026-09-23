"""Visualise popup — checking that a match landed on the right structure.

Shows one patient's ground truth and every matched test contour on the
reference CT, in the same multiplanar viewer the Qualitative tab uses: axial,
coronal and sagittal planes, level/window sliders, ctrl+scroll zoom and
left-drag panning, contour opacity and thickness.

Around the viewer, this dialog adds what matching needs and grading does not: a
legend that turns each source on and off, and — when the patient has an RT
Dose — an optional dose colour wash with its scale.

The ground truth is drawn last, so it stays on top of every test contour, and
the viewer opens on the middle of the ground truth's extent. Colours come from
the shared palette, so a source is the same colour here as in the Qualitative
tab.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import SimpleITK as sitk
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
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

from autoseg_evaluator.ui.widgets._palette import GT_COLOR, color_for_index
from autoseg_evaluator.ui.widgets.multiplanar_viewer import (
    AXIAL,
    CORONAL,
    SAGITTAL,
    MultiPlanarViewer,
    Overlay,
)

#: The ``jet`` ramp the dose wash is drawn with, as stops for the scale bar.
_JET_STOPS = (
    (0.0, "#00007F"),
    (0.125, "#0000FF"),
    (0.375, "#00FFFF"),
    (0.625, "#FFFF00"),
    (0.875, "#FF0000"),
    (1.0, "#7F0000"),
)


class VisualizationWindow(QDialog):
    """Modal viewer for one patient's GT + matched tests against the reference CT."""

    def __init__(
        self,
        ct_image: sitk.Image,
        gt_mask: sitk.Image,
        gt_label: str,
        test_masks_with_labels: Sequence[tuple[str, sitk.Image]],
        title: str,
        parent: QWidget | None = None,
        dose_arr: np.ndarray | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(960, 900)
        self.setSizeGripEnabled(True)

        tests = [
            (label, sitk.GetArrayFromImage(mask).astype(bool))
            for label, mask in test_masks_with_labels
        ]
        overlays = [
            Overlay(label=label, color=color_for_index(i), mask=arr)
            for i, (label, arr) in enumerate(tests)
        ]
        # Last, so it is drawn over every test contour.
        overlays.append(
            Overlay(
                label=f"GT — {gt_label}",
                color=GT_COLOR,
                mask=sitk.GetArrayFromImage(gt_mask).astype(bool),
            )
        )
        self._gt_index = len(overlays) - 1
        self._overlays = overlays

        self._viewer = MultiPlanarViewer(self)
        # Open on the ground truth without dimming the tests the way an
        # "active" overlay would.
        self._viewer.set_data(ct_image, overlays, focus=self._gt_index)

        self._dose_alpha = 0.40
        self._dose_visible = False
        has_dose = self._viewer.set_dose(dose_arr)
        self._dose_arr: np.ndarray | None = dose_arr if has_dose else None
        self._dose_vmax = self._viewer.dose_max
        self._viewer.set_dose_opacity(self._dose_alpha)

        self._build_ui()
        self._bind_keys()

    # ---- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)
        outer.addWidget(self._viewer, stretch=1)

        # Dose wash: an on/off toggle, an opacity slider, and the scale. Shown
        # disabled when the patient has no RT Dose, so it is still discoverable.
        dose_row = QHBoxLayout()
        self._dose_checkbox = QCheckBox("Dose overlay")
        self._dose_checkbox.setChecked(False)
        if self._dose_arr is None:
            self._dose_checkbox.setEnabled(False)
            self._dose_checkbox.setToolTip("No RT Dose is loaded for this patient.")
        else:
            self._dose_checkbox.setToolTip(
                f"Overlay the planned dose as a colour wash (0–{self._dose_vmax:.0f} Gy). "
                "Dose below 5% of the maximum is not washed in."
            )
            self._dose_checkbox.toggled.connect(self._on_dose_toggled)
        dose_row.addWidget(self._dose_checkbox)
        dose_row.addSpacing(12)
        dose_row.addWidget(QLabel("Opacity:"))
        self._dose_opacity = QSlider(Qt.Orientation.Horizontal)
        self._dose_opacity.setRange(10, 90)
        self._dose_opacity.setValue(int(round(self._dose_alpha * 100)))
        self._dose_opacity.setFixedWidth(140)
        self._dose_opacity.setEnabled(self._dose_arr is not None)
        self._dose_opacity.valueChanged.connect(self._on_dose_opacity_changed)
        dose_row.addWidget(self._dose_opacity)
        dose_row.addSpacing(12)
        self._dose_scale = self._make_dose_scale()
        self._dose_scale.setVisible(False)
        dose_row.addWidget(self._dose_scale)
        dose_row.addStretch(1)
        outer.addLayout(dose_row)

        # Legend / visibility toggles: ground truth first, as a reader looks for it.
        legend_scroll = QScrollArea()
        legend_scroll.setWidgetResizable(True)
        legend_scroll.setMaximumHeight(140)
        legend_inner = QWidget()
        legend_layout = QVBoxLayout(legend_inner)
        legend_layout.setContentsMargins(4, 4, 4, 4)
        legend_layout.setSpacing(2)
        self._legend_rows: list[_LegendRow] = []
        order = [self._gt_index] + [i for i in range(len(self._overlays)) if i != self._gt_index]
        for index in order:
            overlay = self._overlays[index]
            row = _LegendRow(overlay.color, overlay.label, checked=True)
            row.checkbox.toggled.connect(
                lambda checked, i=index: self._viewer.set_overlay_visible(i, checked)
            )
            legend_layout.addWidget(row)
            self._legend_rows.append(row)
        legend_layout.addStretch(1)
        legend_scroll.setWidget(legend_inner)
        outer.addWidget(legend_scroll)

        btn_row = QHBoxLayout()
        hint = QLabel(
            "Scroll: slices · Ctrl+scroll: zoom · Drag: pan · A / C / S: plane · Arrow keys: slices"
        )
        hint.setStyleSheet("color: #888;")
        btn_row.addWidget(hint)
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        outer.addLayout(btn_row)

    def _make_dose_scale(self) -> QWidget:
        """``0 Gy [ramp] max Gy`` — the colour bar for the wash."""
        scale = QWidget(self)
        layout = QHBoxLayout(scale)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(QLabel("0"))
        ramp = QLabel(scale)
        ramp.setFixedSize(120, 12)
        stops = ", ".join(f"stop:{at} {colour}" for at, colour in _JET_STOPS)
        ramp.setStyleSheet(
            f"border: 1px solid #888; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, {stops});"
        )
        layout.addWidget(ramp)
        layout.addWidget(QLabel(f"{self._dose_vmax:.0f} Gy"))
        return scale

    def _bind_keys(self) -> None:
        """Arrow keys step slices, A / C / S pick the plane, as in the Qualitative tab."""
        for key, delta in (
            (Qt.Key.Key_Up, 1),
            (Qt.Key.Key_Right, 1),
            (Qt.Key.Key_Down, -1),
            (Qt.Key.Key_Left, -1),
        ):
            QShortcut(QKeySequence(key), self, activated=lambda d=delta: self._viewer.step_slice(d))
        for key, plane in (
            (Qt.Key.Key_A, AXIAL),
            (Qt.Key.Key_C, CORONAL),
            (Qt.Key.Key_S, SAGITTAL),
        ):
            QShortcut(QKeySequence(key), self, activated=lambda p=plane: self._viewer.set_plane(p))

    # ---- Event handlers ---------------------------------------------------

    def _on_dose_toggled(self, checked: bool) -> None:
        self._dose_visible = checked
        self._dose_scale.setVisible(checked)
        self._viewer.set_dose_visible(checked)

    def _on_dose_opacity_changed(self, value: int) -> None:
        self._dose_alpha = max(0.05, min(0.95, value / 100.0))
        self._viewer.set_dose_opacity(self._dose_alpha)


# ---- Legend row widget ---------------------------------------------------


class _LegendRow(QWidget):
    """One row in the legend: colour swatch + label + visibility checkbox."""

    def __init__(
        self, color: str, label: str, checked: bool, parent: QWidget | None = None
    ) -> None:
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

        # No explicit colour: the theme palette keeps the label readable in both
        # light and dark mode.
        text = QLabel(label, self)
        layout.addWidget(text, stretch=1)
