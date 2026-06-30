"""Interactive multiplanar CT + contour viewer (QGraphicsView based).

Built for the qualitative-assessment tab: shows one CT volume with any number
of contour overlays, in axial / coronal / sagittal planes (all resliced up
front; the user cycles the single viewport). Supports:

* slice scrolling (wheel or slider),
* zoom (ctrl + wheel, anchored under the cursor),
* window/level (left-drag),
* per-overlay visibility + an "active" highlight,
* adjustable contour opacity and line thickness.

The reslice / aspect helpers at the top are pure (numpy only) so they can be
unit-tested without Qt. Rendering converts a slice to a grayscale ``QImage``
and draws contour outlines (``skimage.measure.find_contours``) as cosmetic-pen
``QGraphicsPathItem``s so line thickness stays constant under zoom.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import SimpleITK as sitk
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QButtonGroup,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from skimage import measure

AXIAL = "axial"
CORONAL = "coronal"
SAGITTAL = "sagittal"
PLANES = (AXIAL, CORONAL, SAGITTAL)


# ---- Pure reslice helpers (numpy only, unit-tested) ----------------------


def n_slices(volume: np.ndarray, plane: str) -> int:
    """Number of slices a ``(z, y, x)`` volume yields in ``plane``."""
    if plane == AXIAL:
        return int(volume.shape[0])
    if plane == CORONAL:
        return int(volume.shape[1])
    if plane == SAGITTAL:
        return int(volume.shape[2])
    raise ValueError(f"Unknown plane {plane!r}")


def reslice(volume: np.ndarray, plane: str, index: int) -> np.ndarray:
    """Return the 2D slice of a ``(z, y, x)`` volume at ``index`` in ``plane``.

    Axial → ``(y, x)``; coronal → ``(z, x)``; sagittal → ``(z, y)``. ``index``
    is clamped into range. The coronal and sagittal planes are flipped
    vertically so the superior direction (high z) renders at the **top** —
    the radiological convention — instead of upside-down.
    """
    idx = int(max(0, min(n_slices(volume, plane) - 1, index)))
    if plane == AXIAL:
        return volume[idx, :, :]
    if plane == CORONAL:
        return np.flipud(volume[:, idx, :])
    if plane == SAGITTAL:
        return np.flipud(volume[:, :, idx])
    raise ValueError(f"Unknown plane {plane!r}")


def plane_pixel_size(spacing_xyz: tuple[float, float, float], plane: str) -> tuple[float, float]:
    """Return ``(col_mm, row_mm)`` physical pixel size for a plane's 2D slice.

    ``spacing_xyz`` is SimpleITK's ``(sx, sy, sz)``. Used to scale the displayed
    image so anisotropic voxels (e.g. 1×1×3 mm) are not distorted.
    """
    sx, sy, sz = (float(spacing_xyz[0]), float(spacing_xyz[1]), float(spacing_xyz[2]))
    if plane == AXIAL:  # rows = y, cols = x
        return sx, sy
    if plane == CORONAL:  # rows = z, cols = x
        return sx, sz
    if plane == SAGITTAL:  # rows = z, cols = y
        return sy, sz
    raise ValueError(f"Unknown plane {plane!r}")


# ---- Overlay model -------------------------------------------------------


@dataclass
class Overlay:
    """One contour overlay: a label, colour, and a ``(z, y, x)`` boolean mask."""

    label: str
    color: str
    mask: np.ndarray
    visible: bool = True
    active: bool = False
    _bbox_z: tuple[int, int] | None = field(default=None, repr=False)

    def z_extent(self) -> tuple[int, int] | None:
        """Cached (z_lo, z_hi) of the mask's foreground, or None if empty."""
        if self._bbox_z is None:
            zs = np.any(self.mask > 0, axis=(1, 2))
            if not zs.any():
                return None
            nz = np.nonzero(zs)[0]
            self._bbox_z = (int(nz.min()), int(nz.max()))
        return self._bbox_z


# ---- Graphics view with wheel/drag interactions --------------------------


class _PlaneView(QGraphicsView):
    """Graphics view: ctrl+wheel zooms (anchored under cursor); plain wheel
    steps slices. Window/level is driven by sliders, not the mouse."""

    sliceStepped = Signal(int)  # +1 / -1 on a plain wheel turn

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)  # left-drag pans
        self.setBackgroundBrush(QColor("#000000"))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
            self.scale(factor, factor)
        else:
            self.sliceStepped.emit(1 if event.angleDelta().y() > 0 else -1)
        event.accept()


# ---- The viewer widget ---------------------------------------------------


class MultiPlanarViewer(QWidget):
    """CT + contour viewer with axial/coronal/sagittal planes and interactions."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ct: np.ndarray | None = None
        self._spacing = (1.0, 1.0, 1.0)
        self._overlays: list[Overlay] = []
        self._plane = AXIAL
        self._slice = 0
        self._wl_center = 40.0
        self._wl_width = 400.0
        self._opacity = 0.9
        self._thickness = 2
        self._build_ui()

    # ---- UI ----
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # Plane selector row
        plane_row = QHBoxLayout()
        self._plane_group = QButtonGroup(self)
        self._plane_group.setExclusive(True)
        for plane, key in ((AXIAL, "A"), (CORONAL, "C"), (SAGITTAL, "S")):
            btn = QPushButton(f"{plane.capitalize()} ({key})")
            btn.setCheckable(True)
            btn.setChecked(plane == AXIAL)
            btn.clicked.connect(lambda _checked=False, p=plane: self.set_plane(p))
            self._plane_group.addButton(btn)
            plane_row.addWidget(btn)
        plane_row.addStretch(1)
        reset_btn = QPushButton("Reset view")
        reset_btn.setToolTip("Reset zoom + window/level to defaults.")
        reset_btn.clicked.connect(self._reset_view)
        plane_row.addWidget(reset_btn)
        outer.addLayout(plane_row)

        # Graphics view
        self._scene = QGraphicsScene(self)
        self._view = _PlaneView(self)
        self._view.setScene(self._scene)
        self._pixmap_item = QGraphicsPixmapItem()
        self._scene.addItem(self._pixmap_item)
        self._view.sliceStepped.connect(self._on_slice_step)
        outer.addWidget(self._view, stretch=1)

        # Slice slider
        slice_row = QHBoxLayout()
        slice_row.addWidget(QLabel("Slice:"))
        self._slice_slider = QSlider(Qt.Orientation.Horizontal)
        self._slice_slider.valueChanged.connect(self._on_slider)
        slice_row.addWidget(self._slice_slider, stretch=1)
        self._slice_label = QLabel("0 / 0")
        slice_row.addWidget(self._slice_label)
        outer.addLayout(slice_row)

        # Window / level sliders (level = centre, window = width).
        wl_row = QHBoxLayout()
        wl_row.addWidget(QLabel("Level:"))
        self._level_slider = QSlider(Qt.Orientation.Horizontal)
        self._level_slider.valueChanged.connect(self._on_level)
        wl_row.addWidget(self._level_slider, stretch=1)
        self._level_value = QLabel("0")
        self._level_value.setMinimumWidth(44)
        wl_row.addWidget(self._level_value)
        wl_row.addSpacing(12)
        wl_row.addWidget(QLabel("Window:"))
        self._window_slider = QSlider(Qt.Orientation.Horizontal)
        self._window_slider.valueChanged.connect(self._on_window)
        wl_row.addWidget(self._window_slider, stretch=1)
        self._window_value = QLabel("0")
        self._window_value.setMinimumWidth(44)
        wl_row.addWidget(self._window_value)
        outer.addLayout(wl_row)

        # Contour opacity + thickness
        ctl_row = QHBoxLayout()
        ctl_row.addWidget(QLabel("Contour opacity:"))
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(10, 100)
        self._opacity_slider.setValue(int(self._opacity * 100))
        self._opacity_slider.setFixedWidth(120)
        self._opacity_slider.valueChanged.connect(self._on_opacity)
        ctl_row.addWidget(self._opacity_slider)
        ctl_row.addSpacing(12)
        ctl_row.addWidget(QLabel("Thickness:"))
        self._thickness_slider = QSlider(Qt.Orientation.Horizontal)
        self._thickness_slider.setRange(1, 6)
        self._thickness_slider.setValue(self._thickness)
        self._thickness_slider.setFixedWidth(90)
        self._thickness_slider.valueChanged.connect(self._on_thickness)
        ctl_row.addWidget(self._thickness_slider)
        ctl_row.addStretch(1)
        outer.addLayout(ctl_row)

    # ---- Public API ----
    def set_data(self, ct_image: sitk.Image, overlays: list[Overlay]) -> None:
        """Load a CT volume + its contour overlays and render the first view."""
        self._ct = sitk.GetArrayFromImage(ct_image).astype(np.float32)
        self._spacing = tuple(float(s) for s in ct_image.GetSpacing())
        self._overlays = list(overlays)
        self._wl_center, self._wl_width = _default_window_level(self._ct)
        self._configure_wl_sliders()
        self._plane = AXIAL
        for btn in self._plane_group.buttons():
            btn.setChecked(btn.text().lower().startswith(AXIAL))
        self._slice = self._default_slice()
        self._refresh_slider_range()
        self._render()
        # Fit the image into the view once data is present.
        self._fit()

    def set_overlay_visible(self, index: int, visible: bool) -> None:
        if 0 <= index < len(self._overlays):
            self._overlays[index].visible = bool(visible)
            self._render()

    def set_active_overlay(self, index: int | None) -> None:
        for i, ov in enumerate(self._overlays):
            ov.active = i == index
        self._render()

    def set_plane(self, plane: str) -> None:
        if plane not in PLANES or plane == self._plane:
            return
        self._plane = plane
        self._slice = self._default_slice()
        self._refresh_slider_range()
        self._render()
        self._fit()

    def cycle_plane(self, step: int = 1) -> None:
        idx = (PLANES.index(self._plane) + step) % len(PLANES)
        target = PLANES[idx]
        for btn in self._plane_group.buttons():
            btn.setChecked(btn.text().lower().startswith(target))
        self.set_plane(target)

    # ---- Internal helpers ----
    def _default_slice(self) -> int:
        """Middle slice of the active overlay's extent (else volume middle)."""
        active = next((o for o in self._overlays if o.active and o.visible), None)
        if self._ct is None:
            return 0
        if active is not None and self._plane == AXIAL:
            ext = active.z_extent()
            if ext is not None:
                return (ext[0] + ext[1]) // 2
        return n_slices(self._ct, self._plane) // 2

    def _refresh_slider_range(self) -> None:
        if self._ct is None:
            return
        total = n_slices(self._ct, self._plane)
        self._slice_slider.blockSignals(True)
        self._slice_slider.setRange(0, max(0, total - 1))
        self._slice_slider.setValue(int(self._slice))
        self._slice_slider.blockSignals(False)
        self._slice_label.setText(f"{self._slice + 1} / {total}")

    def _on_slider(self, value: int) -> None:
        self._slice = int(value)
        if self._ct is not None:
            self._slice_label.setText(f"{self._slice + 1} / {n_slices(self._ct, self._plane)}")
        self._render()

    def _on_slice_step(self, delta: int) -> None:
        self._slice_slider.setValue(
            max(0, min(self._slice_slider.maximum(), self._slice + delta))
        )

    def _configure_wl_sliders(self) -> None:
        """Set the level/window slider ranges from the current volume."""
        if self._ct is None:
            return
        vmin = float(np.min(self._ct))
        vmax = float(np.max(self._ct))
        if vmax <= vmin:
            vmax = vmin + 1.0
        self._level_slider.blockSignals(True)
        self._level_slider.setRange(int(np.floor(vmin)), int(np.ceil(vmax)))
        self._level_slider.setValue(int(round(self._wl_center)))
        self._level_slider.blockSignals(False)
        self._window_slider.blockSignals(True)
        self._window_slider.setRange(1, max(2, int(np.ceil(vmax - vmin))))
        self._window_slider.setValue(int(round(self._wl_width)))
        self._window_slider.blockSignals(False)
        self._level_value.setText(str(int(round(self._wl_center))))
        self._window_value.setText(str(int(round(self._wl_width))))

    def _on_level(self, value: int) -> None:
        self._wl_center = float(value)
        self._level_value.setText(str(int(value)))
        self._render()

    def _on_window(self, value: int) -> None:
        self._wl_width = max(1.0, float(value))
        self._window_value.setText(str(int(value)))
        self._render()

    def _on_opacity(self, value: int) -> None:
        self._opacity = max(0.05, min(1.0, value / 100.0))
        self._render()

    def _on_thickness(self, value: int) -> None:
        self._thickness = int(value)
        self._render()

    def _reset_view(self) -> None:
        if self._ct is not None:
            self._wl_center, self._wl_width = _default_window_level(self._ct)
            self._configure_wl_sliders()
        self._view.resetTransform()
        self._render()
        self._fit()

    def _fit(self) -> None:
        if not self._pixmap_item.pixmap().isNull():
            self._view.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)

    def _render(self) -> None:
        if self._ct is None:
            return
        sl = reslice(self._ct, self._plane, self._slice)
        col_mm, row_mm = plane_pixel_size(self._spacing, self._plane)

        # CT → grayscale QImage via window/level.
        lo = self._wl_center - self._wl_width / 2.0
        hi = self._wl_center + self._wl_width / 2.0
        norm = np.clip((sl - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
        img8 = np.ascontiguousarray((norm * 255).astype(np.uint8))
        h, w = img8.shape
        # Construct from a bytes buffer then ``.copy()`` so the QImage owns its
        # data (independent of the transient numpy array).
        qimg = QImage(img8.tobytes(), w, h, w, QImage.Format.Format_Grayscale8).copy()
        self._pixmap_item.setPixmap(QPixmap.fromImage(qimg))
        # Physical aspect: scale each pixel to its mm size so anisotropic voxels
        # are not distorted. Child path items inherit this transform.
        self._pixmap_item.setTransform(QTransform().scale(col_mm, row_mm))
        self._scene.setSceneRect(self._pixmap_item.mapRectToScene(self._pixmap_item.boundingRect()))

        # Clear previous contour items (children of the pixmap item).
        for child in list(self._pixmap_item.childItems()):
            child.setParentItem(None)
            self._scene.removeItem(child)

        for ov in self._overlays:
            if not ov.visible:
                continue
            mslice = reslice(ov.mask, self._plane, self._slice)
            if not np.any(mslice):
                continue
            path = _mask_to_path(mslice)
            if path.isEmpty():
                continue
            item = QGraphicsPathItem(path, self._pixmap_item)
            width = self._thickness + (1 if ov.active else 0)
            pen = QPen(QColor(ov.color), width)
            pen.setCosmetic(True)  # constant device-pixel width regardless of zoom
            item.setPen(pen)
            item.setOpacity(self._opacity if ov.active or not _any_active(self._overlays) else
                            self._opacity * 0.55)


def _any_active(overlays: list[Overlay]) -> bool:
    return any(o.active and o.visible for o in overlays)


def _mask_to_path(mask_2d: np.ndarray) -> QPainterPath:
    """Build a QPainterPath outlining a 2D boolean mask via marching squares."""
    path = QPainterPath()
    for contour in measure.find_contours(mask_2d.astype(float), 0.5):
        # contour is (N, 2) in (row, col); QPainterPath uses (x=col, y=row).
        path.moveTo(float(contour[0, 1]), float(contour[0, 0]))
        for r, c in contour[1:]:
            path.lineTo(float(c), float(r))
    return path


def _default_window_level(volume: np.ndarray) -> tuple[float, float]:
    """A robust default window/level from the volume's 1st–99th percentiles."""
    finite = volume[np.isfinite(volume)]
    if finite.size == 0:
        return 40.0, 400.0
    lo = float(np.percentile(finite, 1))
    hi = float(np.percentile(finite, 99))
    if hi <= lo:
        hi = lo + 1.0
    return (lo + hi) / 2.0, max(1.0, hi - lo)
