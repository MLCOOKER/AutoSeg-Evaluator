"""Tab 3 — Compute.

Provides:

* a geometric-metric checkbox group (Dice, HD100/95, MSD, Surface Dice, APL)
  plus Surface-Dice τ and APL τ tolerance spinboxes,
* a dosimetric-metric group (Dmean/Dmax/Dmin checkboxes, user-defined
  D@volume% list, V@dose(Gy) list, RTDOSE auto-detection note),
* a "Compute All" button that emits a fully-populated configuration dict,
* a detailed live progress panel with cancel button.

Step 7 lands the UI only — the actual worker that consumes the config and
emits progress arrives in step 8.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from autoseg_evaluator.ui.widgets.progress_panel import ProgressPanel


class _NoScrollSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox that ignores mouse-wheel events.

    Without this, the spinbox eats wheel events even when the user is
    scrolling the surrounding tab — silently mutating STAPLE parameters
    they didn't intend to change. Click-to-focus + arrow keys + typing
    still work normally.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Prevent the spinbox from grabbing focus on scroll-by — only on
        # explicit click or keyboard tab navigation.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        # Let the wheel event propagate to the parent scroll area instead
        # of being consumed by the spinbox.
        event.ignore()


# STAPLE parameter defaults — aligned with MICCAI consensus-contour pipelines
# (Asman & Landman 2011; Iglesias & Sabuncu 2015; Bakas BraTS 2018). Surfaced
# in the UI labels so the user can recover them and used by the Reset button.
_STAPLE_DEFAULTS = {
    "max_iterations": 100,
    "confidence_weight": 1.0,
    "target_fg_ratio_min": 0.10,
    "target_fg_ratio_max": 0.50,
}


_GEOMETRIC_METRICS: tuple[tuple[str, str, str], ...] = (
    (
        "dice",
        "Dice",
        "Volumetric overlap: 2 · |A ∩ B| / (|A| + |B|). 0–1; 1 = identical. "
        "Biased toward larger structures (Rusanov 2025; Dice 1945).",
    ),
    (
        "hausdorff100",
        "Hausdorff (100%)",
        "Maximum closest-point surface distance between GT and test, "
        "symmetric (mm). Very sensitive to single outlier voxels.",
    ),
    (
        "hausdorff95",
        "Hausdorff (95%)",
        "95th-percentile of the closest-point surface distances (mm). "
        "Robust to isolated outliers; the standard reporting form.",
    ),
    (
        "mean_surface_distance",
        "Mean Surface Distance",
        "Symmetric mean of per-surface-element closest-point distances (mm). "
        "Returns NaN if either direction is empty (v1 convention).",
    ),
    (
        "surface_dice",
        "Surface Dice",
        "Fraction of each surface within tolerance τ mm of the other; 0–1. "
        "Reflects the clinically acceptable editing tolerance (Nikolov 2018).",
    ),
    (
        "apl_mean",
        "Mean APL",
        "Mean of per-slice Added Path Length across slices that contain "
        "either contour (mm). PlatiPy convention; NaN when no slices contribute.",
    ),
    (
        "apl_total",
        "Total APL",
        "Sum of per-slice Added Path Length over the whole volume (mm). "
        "Approximates manual-editing effort (Vaassen 2020).",
    ),
    (
        "volume",
        "Volume (cc) + diff/ratio",
        "Absolute GT and test volumes in cubic centimetres, plus signed "
        "difference (test − GT) and ratio (test / GT). Catches systematic "
        "over- or under-segmentation.",
    ),
    (
        "com_offset",
        "Centre-of-mass offset",
        "Euclidean distance between GT and test centroids in patient "
        "coordinates (mm), with signed Δx / Δy / Δz components. Detects "
        "positional shifts that high-overlap metrics can hide.",
    ),
)


class ComputeTab(QWidget):
    """Configuration + progress display for batch metric computation."""

    # Emitted when the user clicks Compute All. Payload is a config dict the
    # worker can act on directly.
    computeRequested = Signal(dict)
    cancelRequested = Signal()
    # Settings persistence: emitted whenever the user changes any control.
    metricConfigChanged = Signal(dict)

    def __init__(
        self,
        settings: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings: dict[str, Any] = settings if settings is not None else {}
        self._library: Any | None = None  # populated when Tab 1 loads a folder

        self._geom_checks: dict[str, QCheckBox] = {}
        self._dose_checks: dict[str, QCheckBox] = {}

        self._build_ui()
        self._load_from_settings()

    # ---- Public API -------------------------------------------------------

    def set_library(self, library: Any) -> None:
        """Receive the loaded library (used by step 8 to discover RTDOSEs)."""
        self._library = library
        self._refresh_dose_summary()

    def set_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        self._load_from_settings()

    def config(self) -> dict[str, Any]:
        """Snapshot of the currently-selected metric configuration."""
        return {
            "geometric": {key: cb.isChecked() for key, cb in self._geom_checks.items()},
            "tolerances": {
                "surface_dice_tau_mm": float(self._sd_tau_spin.value()),
                "apl_tolerance_mm": float(self._apl_tau_spin.value()),
            },
            "dvh": {
                "include_dmean": self._dose_checks["dmean"].isChecked(),
                "include_dmax": self._dose_checks["dmax"].isChecked(),
                "include_dmin": self._dose_checks["dmin"].isChecked(),
                "d_at_volumes_pct": _parse_number_list(self._d_pct_edit.text()),
                "v_at_doses_gy": _parse_number_list(self._v_gy_edit.text()),
            },
            "staple": {
                "max_iterations": int(self._staple_max_iter_spin.value()),
                "confidence_weight": float(self._staple_conf_spin.value()),
                "target_fg_ratio_min": float(self._staple_fg_min_spin.value()),
                "target_fg_ratio_max": float(self._staple_fg_max_spin.value()),
            },
        }

    def progress_panel(self) -> ProgressPanel:
        return self._progress

    # ---- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # Two-column config row
        config_row = QHBoxLayout()
        config_row.setSpacing(8)
        config_row.addWidget(self._build_geometric_group(), stretch=1)
        config_row.addWidget(self._build_dosimetric_group(), stretch=1)
        outer.addLayout(config_row)

        # STAPLE settings (only meaningful when ≥1 drawer has STAPLE mode on)
        outer.addWidget(self._build_staple_group())

        # Run button
        run_row = QHBoxLayout()
        run_row.addStretch(1)
        self._validation_label = QLabel("", self)
        self._validation_label.setStyleSheet("color: #d96b00;")
        run_row.addWidget(self._validation_label, stretch=1)
        self._compute_btn = QPushButton("Compute All", self)
        self._compute_btn.clicked.connect(self._on_compute_clicked)
        run_row.addWidget(self._compute_btn)
        outer.addLayout(run_row)

        outer.addWidget(_h_divider())

        # Progress panel (hidden until Compute clicked)
        self._progress = ProgressPanel(self)
        self._progress.cancelRequested.connect(self.cancelRequested)
        outer.addWidget(self._progress)

        outer.addStretch(1)

    def _build_geometric_group(self) -> QGroupBox:
        box = QGroupBox("Geometric metrics", self)
        layout = QVBoxLayout(box)
        layout.setSpacing(4)

        for key, label, description in _GEOMETRIC_METRICS:
            cb = QCheckBox(label, box)
            cb.toggled.connect(self._emit_config_changed)
            cb.setToolTip(description)
            self._geom_checks[key] = cb
            layout.addWidget(cb)

        layout.addSpacing(8)

        tol_form = QFormLayout()
        tol_form.setContentsMargins(0, 0, 0, 0)
        tol_form.setSpacing(6)
        self._sd_tau_spin = _NoScrollSpinBox(box)
        self._sd_tau_spin.setRange(0.0, 100.0)
        self._sd_tau_spin.setSingleStep(0.1)
        self._sd_tau_spin.setDecimals(2)
        self._sd_tau_spin.setSuffix(" mm")
        self._sd_tau_spin.valueChanged.connect(self._emit_config_changed)
        tol_form.addRow("Surface Dice τ:", self._sd_tau_spin)

        self._apl_tau_spin = _NoScrollSpinBox(box)
        self._apl_tau_spin.setRange(0.0, 100.0)
        self._apl_tau_spin.setSingleStep(0.1)
        self._apl_tau_spin.setDecimals(2)
        self._apl_tau_spin.setSuffix(" mm")
        self._apl_tau_spin.valueChanged.connect(self._emit_config_changed)
        tol_form.addRow("APL tolerance:", self._apl_tau_spin)
        layout.addLayout(tol_form)

        layout.addStretch(1)
        return box

    def _build_dosimetric_group(self) -> QGroupBox:
        box = QGroupBox("Dosimetric metrics (DVH)", self)
        layout = QVBoxLayout(box)
        layout.setSpacing(6)

        # RT Dose source info — auto-detected per patient.
        self._dose_info = QLabel(
            "<i>RT Dose: auto-detected per patient from the loaded folder "
            "(prefers <code>DoseSummationType = PLAN</code>).</i>",
            box,
        )
        self._dose_info.setTextFormat(Qt.TextFormat.RichText)
        self._dose_info.setWordWrap(True)
        layout.addWidget(self._dose_info)

        # Built-in DVH point checkboxes — descriptions in tooltips only.
        dvh_builtin = (
            ("dmean", "Dmean", "Mean dose absorbed by the structure (Gy)."),
            ("dmax", "Dmax", "Maximum dose to any voxel in the structure (Gy)."),
            ("dmin", "Dmin", "Minimum dose to any voxel in the structure (Gy)."),
        )
        builtin_row = QHBoxLayout()
        for key, label, description in dvh_builtin:
            cb = QCheckBox(label, box)
            cb.toggled.connect(self._emit_config_changed)
            cb.setToolTip(description)
            self._dose_checks[key] = cb
            builtin_row.addWidget(cb)
        builtin_row.addStretch(1)
        layout.addLayout(builtin_row)

        # D@volume% and V@dose(Gy) inputs
        d_v_form = QFormLayout()
        d_v_form.setContentsMargins(0, 0, 0, 0)
        d_v_form.setSpacing(6)
        self._d_pct_edit = QLineEdit(box)
        self._d_pct_edit.setPlaceholderText("95, 50, 5, 2")
        self._d_pct_edit.setToolTip(
            "DV%: dose received by the hottest V% of the structure (Gy). "
            "D95 = dose to the hottest 95%, often used as a CTV coverage metric."
        )
        self._d_pct_edit.editingFinished.connect(self._emit_config_changed)
        d_v_form.addRow("D at volume (%):", self._d_pct_edit)

        self._v_gy_edit = QLineEdit(box)
        self._v_gy_edit.setPlaceholderText("20, 30, 40")
        self._v_gy_edit.setToolTip(
            "VxGy: absolute volume (cc) receiving at least x Gy. "
            "V20Gy lung is a classic pneumonitis-risk metric."
        )
        self._v_gy_edit.editingFinished.connect(self._emit_config_changed)
        d_v_form.addRow("V at dose (Gy):", self._v_gy_edit)
        layout.addLayout(d_v_form)

        layout.addWidget(
            QLabel(
                "<span style='color:#666; font-size: 9pt'>"
                "D<sub>X</sub>%: dose to the hottest X% of the structure (Gy). "
                "V<sub>X</sub>Gy: volume (cc) receiving ≥ X Gy. "
                "Examples: D95, D50, D5, D2 — V20Gy, V30Gy, V40Gy."
                "</span>",
                box,
            )
        )

        # Dose-file summary populates once a library is loaded
        self._dose_summary_label = QLabel("<i>No data loaded.</i>", box)
        self._dose_summary_label.setStyleSheet("color: #666;")
        self._dose_summary_label.setWordWrap(True)
        layout.addWidget(self._dose_summary_label)

        layout.addStretch(1)
        return box

    def _build_staple_group(self) -> QGroupBox:
        box = QGroupBox("STAPLE consensus parameters", self)
        box.setToolTip(
            "Only relevant when one or more drawers have STAPLE consensus mode "
            "enabled. STAPLE (Warfield, Zou & Wells 2002) estimates a probabilistic "
            "ground truth from N rater segmentations via expectation-maximisation."
        )
        layout = QFormLayout(box)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        self._staple_max_iter_spin = _NoScrollSpinBox(box)
        self._staple_max_iter_spin.setDecimals(0)
        self._staple_max_iter_spin.setRange(1, 500)
        self._staple_max_iter_spin.setSingleStep(5)
        self._staple_max_iter_spin.setValue(_STAPLE_DEFAULTS["max_iterations"])
        self._staple_max_iter_spin.setToolTip(
            "Maximum EM iterations. STAPLE typically converges in 10–30 for clinical "
            "OARs; values below ~5 risk premature termination."
        )
        self._staple_max_iter_spin.valueChanged.connect(self._emit_config_changed)
        layout.addRow(
            f"Max iterations (default {_STAPLE_DEFAULTS['max_iterations']}):",
            self._staple_max_iter_spin,
        )

        self._staple_conf_spin = _NoScrollSpinBox(box)
        self._staple_conf_spin.setDecimals(2)
        self._staple_conf_spin.setRange(0.1, 5.0)
        self._staple_conf_spin.setSingleStep(0.1)
        self._staple_conf_spin.setValue(_STAPLE_DEFAULTS["confidence_weight"])
        self._staple_conf_spin.setToolTip(
            "Multiplier on STAPLE's auto-estimated foreground prior. The ITK "
            "docstring recommends leaving this at 1.0 unless you have a specific "
            "reason to bias the EM toward foreground (>1) or background (<1)."
        )
        self._staple_conf_spin.valueChanged.connect(self._emit_config_changed)
        layout.addRow(
            f"Confidence weight (default {_STAPLE_DEFAULTS['confidence_weight']:.1f}):",
            self._staple_conf_spin,
        )

        # Adaptive bbox padding — keeps foreground/total ratio inside this band.
        # Replaces the old fixed-voxel padding that under-sized for small
        # structures (specificity becomes uninformative) and over-tightened for
        # large ones. Values from Iglesias & Sabuncu 2015; Asman & Landman 2011.
        self._staple_fg_min_spin = _NoScrollSpinBox(box)
        self._staple_fg_min_spin.setDecimals(2)
        self._staple_fg_min_spin.setRange(0.01, 0.99)
        self._staple_fg_min_spin.setSingleStep(0.05)
        self._staple_fg_min_spin.setValue(_STAPLE_DEFAULTS["target_fg_ratio_min"])
        self._staple_fg_min_spin.setToolTip(
            "Lower bound of the adaptive bbox foreground ratio band. The padder "
            "grows the union bbox until foreground/total falls below the upper "
            "bound; it stops at this lower bound to avoid over-cropping. "
            "Published clinical pipelines use 0.10–0.50."
        )
        self._staple_fg_min_spin.valueChanged.connect(self._emit_config_changed)
        layout.addRow(
            f"Adaptive bbox FG ratio min (default {_STAPLE_DEFAULTS['target_fg_ratio_min']:.2f}):",
            self._staple_fg_min_spin,
        )

        self._staple_fg_max_spin = _NoScrollSpinBox(box)
        self._staple_fg_max_spin.setDecimals(2)
        self._staple_fg_max_spin.setRange(0.05, 0.99)
        self._staple_fg_max_spin.setSingleStep(0.05)
        self._staple_fg_max_spin.setValue(_STAPLE_DEFAULTS["target_fg_ratio_max"])
        self._staple_fg_max_spin.setToolTip(
            "Upper bound of the adaptive bbox foreground ratio band. The padder "
            "grows the union bbox until foreground/total drops to (or below) "
            "this value, keeping per-rater specificity informative even for "
            "small structures."
        )
        self._staple_fg_max_spin.valueChanged.connect(self._emit_config_changed)
        layout.addRow(
            f"Adaptive bbox FG ratio max (default {_STAPLE_DEFAULTS['target_fg_ratio_max']:.2f}):",
            self._staple_fg_max_spin,
        )

        reset_btn = QPushButton("Reset to defaults", box)
        reset_btn.setToolTip(
            "Restore STAPLE parameters to the values most consensus-contour "
            "studies cite (max_iterations=30, confidence_weight=1.0, "
            "bbox_padding_voxels=5)."
        )
        reset_btn.clicked.connect(self._reset_staple_defaults)
        layout.addRow("", reset_btn)

        return box

    def _reset_staple_defaults(self) -> None:
        """Restore the STAPLE spinboxes to their published MICCAI defaults."""
        self._staple_max_iter_spin.setValue(_STAPLE_DEFAULTS["max_iterations"])
        self._staple_conf_spin.setValue(_STAPLE_DEFAULTS["confidence_weight"])
        self._staple_fg_min_spin.setValue(_STAPLE_DEFAULTS["target_fg_ratio_min"])
        self._staple_fg_max_spin.setValue(_STAPLE_DEFAULTS["target_fg_ratio_max"])

    # ---- Settings round-trip ---------------------------------------------

    def _load_from_settings(self) -> None:
        # Geometric metric flags
        defaults_geom = {
            "dice": True,
            "hausdorff100": True,
            "hausdorff95": True,
            "mean_surface_distance": True,
            "surface_dice": True,
            "apl_mean": False,
            "apl_total": False,
            "volume": True,
            "com_offset": True,
        }
        # Use settings if any are explicitly stored under "compute_geometric"
        stored_geom = (self._settings.get("compute_geometric") or {}) if self._settings else {}
        for key, cb in self._geom_checks.items():
            cb.blockSignals(True)
            cb.setChecked(bool(stored_geom.get(key, defaults_geom[key])))
            cb.blockSignals(False)

        # Tolerances
        tol = (self._settings.get("tolerances") or {}) if self._settings else {}
        self._sd_tau_spin.blockSignals(True)
        self._sd_tau_spin.setValue(float(tol.get("surface_dice_tau_mm", 3.0)))
        self._sd_tau_spin.blockSignals(False)
        self._apl_tau_spin.blockSignals(True)
        self._apl_tau_spin.setValue(float(tol.get("apl_tolerance_mm", 3.0)))
        self._apl_tau_spin.blockSignals(False)

        # DVH config
        dvh = (self._settings.get("dvh") or {}) if self._settings else {}
        self._dose_checks["dmean"].blockSignals(True)
        self._dose_checks["dmean"].setChecked(bool(dvh.get("include_dmean", True)))
        self._dose_checks["dmean"].blockSignals(False)
        self._dose_checks["dmax"].blockSignals(True)
        self._dose_checks["dmax"].setChecked(bool(dvh.get("include_dmax", True)))
        self._dose_checks["dmax"].blockSignals(False)
        self._dose_checks["dmin"].blockSignals(True)
        self._dose_checks["dmin"].setChecked(bool(dvh.get("include_dmin", False)))
        self._dose_checks["dmin"].blockSignals(False)
        self._d_pct_edit.blockSignals(True)
        self._d_pct_edit.setText(
            ", ".join(str(v) for v in dvh.get("d_at_volumes_pct", [95, 50, 5, 2]))
        )
        self._d_pct_edit.blockSignals(False)
        self._v_gy_edit.blockSignals(True)
        self._v_gy_edit.setText(", ".join(str(v) for v in dvh.get("v_at_doses_gy", [20, 30, 40])))
        self._v_gy_edit.blockSignals(False)

        # STAPLE config
        staple = (self._settings.get("staple") or {}) if self._settings else {}
        self._staple_max_iter_spin.blockSignals(True)
        self._staple_max_iter_spin.setValue(
            int(staple.get("max_iterations", _STAPLE_DEFAULTS["max_iterations"]))
        )
        self._staple_max_iter_spin.blockSignals(False)
        self._staple_conf_spin.blockSignals(True)
        self._staple_conf_spin.setValue(
            float(staple.get("confidence_weight", _STAPLE_DEFAULTS["confidence_weight"]))
        )
        self._staple_conf_spin.blockSignals(False)
        self._staple_fg_min_spin.blockSignals(True)
        self._staple_fg_min_spin.setValue(
            float(staple.get("target_fg_ratio_min", _STAPLE_DEFAULTS["target_fg_ratio_min"]))
        )
        self._staple_fg_min_spin.blockSignals(False)
        self._staple_fg_max_spin.blockSignals(True)
        self._staple_fg_max_spin.setValue(
            float(staple.get("target_fg_ratio_max", _STAPLE_DEFAULTS["target_fg_ratio_max"]))
        )
        self._staple_fg_max_spin.blockSignals(False)

    def _emit_config_changed(self) -> None:
        try:
            cfg = self.config()
        except ValueError as exc:
            self._validation_label.setText(str(exc))
            return
        self._validation_label.setText("")
        self.metricConfigChanged.emit(cfg)

    # ---- Compute button --------------------------------------------------

    def _on_compute_clicked(self) -> None:
        try:
            cfg = self.config()
        except ValueError as exc:
            self._validation_label.setText(str(exc))
            return
        self._validation_label.setText("")
        # The actual worker hookup arrives in step 8. For now, just emit the
        # config so external code (or tests) can observe.
        self.computeRequested.emit(cfg)
        # Show the progress panel armed at "Idle" — step 8 will call begin()
        # against it with a real step count.
        self._progress.setVisible(True)

    def _refresh_dose_summary(self) -> None:
        if self._library is None:
            self._dose_summary_label.setText("<i>No data loaded.</i>")
            return
        n_dose = sum(
            len(ctx.rtdoses)
            for patient in self._library.patients.values()
            for ctx in patient.contexts
        )
        n_patients = len(self._library.patients)
        patients_with_dose = sum(
            1
            for patient in self._library.patients.values()
            if any(ctx.rtdoses for ctx in patient.contexts)
        )
        self._dose_summary_label.setText(
            f"<span style='color:#666'>{n_dose} RTDOSE file(s) detected across "
            f"{patients_with_dose}/{n_patients} patient(s).</span>"
        )


# ---- Helpers --------------------------------------------------------------


def _parse_number_list(text: str) -> list[float]:
    """Parse a comma/space-separated list of numbers. Empty → empty list."""
    out: list[float] = []
    if not text or not text.strip():
        return out
    raw = text.replace(";", ",")
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.append(float(token))
        except ValueError as exc:
            raise ValueError(
                f"Cannot parse '{token}' as a number — check your D% / V Gy inputs."
            ) from exc
    return out


def _h_divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return line
