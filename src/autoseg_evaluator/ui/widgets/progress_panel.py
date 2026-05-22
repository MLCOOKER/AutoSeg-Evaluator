"""Detailed progress panel for metric computation.

Displays:

* a 0..100% progress bar,
* a "Current task" group: patient, drawer, GT file, current test (n of m),
  current metric (n of m),
* a "Progress" group: drawers completed, elapsed time, remaining ETA,
  error count,
* a Cancel button.

The panel is fed structured updates via :meth:`update_progress` so that
the worker can push richer state than a bare percent. Hidden by default;
:meth:`begin` makes it visible and resets all fields.
"""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ProgressPanel(QWidget):
    """Live progress UI for batch metric computation."""

    cancelRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._start_time: float | None = None
        self._build_ui()
        self.reset()
        self.setVisible(False)

    # ---- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self._bar = QProgressBar(self)
        self._bar.setTextVisible(True)
        outer.addWidget(self._bar)

        cols = QHBoxLayout()
        cols.setSpacing(8)

        current = QGroupBox("Current task", self)
        form_a = QFormLayout(current)
        form_a.setContentsMargins(8, 4, 8, 4)
        form_a.setSpacing(4)
        self._lbl_patient = QLabel("—")
        self._lbl_drawer = QLabel("—")
        self._lbl_gt = QLabel("—")
        self._lbl_test = QLabel("—")
        self._lbl_metric = QLabel("—")
        form_a.addRow("Patient:", self._lbl_patient)
        form_a.addRow("Drawer:", self._lbl_drawer)
        form_a.addRow("GT:", self._lbl_gt)
        form_a.addRow("Test:", self._lbl_test)
        form_a.addRow("Metric:", self._lbl_metric)
        cols.addWidget(current, stretch=1)

        progress_box = QGroupBox("Progress", self)
        form_b = QFormLayout(progress_box)
        form_b.setContentsMargins(8, 4, 8, 4)
        form_b.setSpacing(4)
        self._lbl_done = QLabel("0 / 0")
        self._lbl_elapsed = QLabel("0:00:00")
        self._lbl_eta = QLabel("—")
        self._lbl_errors = QLabel("0")
        form_b.addRow("Drawers complete:", self._lbl_done)
        form_b.addRow("Elapsed:", self._lbl_elapsed)
        form_b.addRow("Remaining (est.):", self._lbl_eta)
        form_b.addRow("Errors:", self._lbl_errors)
        cols.addWidget(progress_box, stretch=1)

        outer.addLayout(cols)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._cancel_btn = QPushButton("Cancel", self)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(self._cancel_btn)
        outer.addLayout(btn_row)

    # ---- Lifecycle --------------------------------------------------------

    def reset(self) -> None:
        self._start_time = None
        self._bar.setRange(0, 1)
        self._bar.setValue(0)
        self._bar.setFormat("Idle")
        self._lbl_patient.setText("—")
        self._lbl_drawer.setText("—")
        self._lbl_gt.setText("—")
        self._lbl_test.setText("—")
        self._lbl_metric.setText("—")
        self._lbl_done.setText("0 / 0")
        self._lbl_elapsed.setText("0:00:00")
        self._lbl_eta.setText("—")
        self._lbl_errors.setText("0")
        self._cancel_btn.setEnabled(True)
        self._cancel_btn.setText("Cancel")

    def begin(self, total_steps: int) -> None:
        """Show the panel, reset state, and arm the progress bar for ``total_steps``."""
        self.reset()
        self._start_time = time.monotonic()
        self._bar.setRange(0, max(1, total_steps))
        self._bar.setValue(0)
        self._bar.setFormat("%v / %m  (%p%)")
        self.setVisible(True)

    def update_progress(self, current: int, total: int, state: dict[str, Any]) -> None:
        """Push a structured progress update.

        ``state`` may include any of: ``patient``, ``drawer``, ``gt``,
        ``test``, ``metric``, ``drawers_done`` (int), ``drawers_total`` (int),
        ``errors`` (int).
        """
        self._bar.setRange(0, max(1, total))
        self._bar.setValue(min(current, total))

        # Current task labels
        if "patient" in state:
            self._lbl_patient.setText(str(state["patient"]))
        if "drawer" in state:
            self._lbl_drawer.setText(str(state["drawer"]))
        if "gt" in state:
            self._lbl_gt.setText(str(state["gt"]))
        if "test" in state:
            self._lbl_test.setText(str(state["test"]))
        if "metric" in state:
            self._lbl_metric.setText(str(state["metric"]))

        # Progress numbers
        if "drawers_done" in state or "drawers_total" in state:
            done = state.get("drawers_done", 0)
            tot = state.get("drawers_total", 0)
            self._lbl_done.setText(f"{done} / {tot}")
        if "errors" in state:
            self._lbl_errors.setText(str(state["errors"]))

        # Elapsed + ETA
        if self._start_time is not None:
            elapsed = time.monotonic() - self._start_time
            self._lbl_elapsed.setText(_format_hms(elapsed))
            if current > 0 and current < total:
                rate = current / elapsed if elapsed > 0 else 0.0
                eta = (total - current) / rate if rate > 0 else 0.0
                self._lbl_eta.setText("~" + _format_hms(eta))
            elif current >= total:
                self._lbl_eta.setText("0:00:00")

    def finish(self, *, cancelled: bool = False, errors: int | None = None) -> None:
        """Mark the run finished. Leaves the panel visible so the user sees the totals."""
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setText("Close" if not cancelled else "Cancelled")
        if errors is not None:
            self._lbl_errors.setText(str(errors))
        if cancelled:
            self._bar.setFormat("Cancelled — %v / %m")
        else:
            self._bar.setValue(self._bar.maximum())
            self._bar.setFormat("Done — %v / %m")
        # Final elapsed snapshot
        if self._start_time is not None:
            self._lbl_elapsed.setText(_format_hms(time.monotonic() - self._start_time))

    def hide_panel(self) -> None:
        self.setVisible(False)

    # ---- Internals --------------------------------------------------------

    def _on_cancel_clicked(self) -> None:
        if self._cancel_btn.text() == "Close":
            self.hide_panel()
            return
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setText("Cancelling…")
        self.cancelRequested.emit()


def _format_hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"
