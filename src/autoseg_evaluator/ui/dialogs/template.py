"""Auto-match Template dialog.

The user specifies:

* a list of organ names to set as ground truth (one per patient),
* a way to identify the GT RTSS file within each patient — either by a
  substring of its ``Manufacturer`` tag, or by a substring of its
  filename, or both,
* a string-similarity threshold below which test matches are flagged.

The template is persisted in ``settings.json`` under ``last_template`` so
the user resumes with their previous configuration on the next launch.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)


class TemplateDialog(QDialog):
    """Define the auto-match template and similarity threshold."""

    def __init__(
        self,
        existing: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Define Auto-Match Template")
        self.resize(620, 480)
        self._existing = existing or {}
        self._result: dict[str, Any] = dict(self._existing)
        self._build_ui()
        self._populate()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        info = QLabel(
            "Define the set of ground-truth organs to find in each patient and "
            "the criterion used to identify the GT RTSS file. The auto-match "
            "step uses these inputs together with your replacement rules to "
            "populate drawers across every loaded patient."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Middle row: organs (left, tall) + GT identifier + threshold (right, stacked)
        middle = QHBoxLayout()
        middle.setSpacing(8)

        organ_box = QGroupBox("Ground-truth organs (one per line)")
        organ_layout = QVBoxLayout(organ_box)
        self._organs_edit = QPlainTextEdit()
        self._organs_edit.setPlaceholderText("Prostate\nBladder\nRectum")
        organ_layout.addWidget(self._organs_edit)
        middle.addWidget(organ_box, stretch=1)

        right_col = QVBoxLayout()
        right_col.setSpacing(8)

        id_box = QGroupBox("Identify the GT RTSS file by…")
        id_form = QFormLayout(id_box)
        self._mfr_edit = QLineEdit()
        self._mfr_edit.setPlaceholderText("e.g. Varian Medical Systems")
        id_form.addRow("Manufacturer contains:", self._mfr_edit)
        self._filename_edit = QLineEdit()
        self._filename_edit.setPlaceholderText("e.g. manual")
        id_form.addRow("Filename contains:", self._filename_edit)
        hint = QLabel(
            "<i>An RTSS file matches if its Manufacturer tag OR its filename "
            "contains the corresponding substring (case-insensitive). Leave "
            "blank to ignore that criterion.</i>"
        )
        hint.setWordWrap(True)
        id_form.addRow(hint)
        right_col.addWidget(id_box)

        thr_box = QGroupBox("Test-match similarity threshold")
        thr_form = QFormLayout(thr_box)
        self._threshold_spin = QDoubleSpinBox()
        self._threshold_spin.setRange(0.0, 1.0)
        self._threshold_spin.setSingleStep(0.05)
        self._threshold_spin.setDecimals(2)
        thr_form.addRow("Below this score → ⚠ flag:", self._threshold_spin)
        right_col.addWidget(thr_box)

        right_col.addStretch(1)
        middle.addLayout(right_col, stretch=1)
        layout.addLayout(middle, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate(self) -> None:
        organs = self._existing.get("organs", []) or []
        self._organs_edit.setPlainText("\n".join(str(o) for o in organs))
        self._mfr_edit.setText(str(self._existing.get("gt_manufacturer", "")))
        self._filename_edit.setText(str(self._existing.get("gt_filename", "")))
        try:
            thr = float(self._existing.get("similarity_threshold", 0.6))
        except (TypeError, ValueError):
            thr = 0.6
        self._threshold_spin.setValue(max(0.0, min(1.0, thr)))

    def _on_accept(self) -> None:
        organs = [
            line.strip()
            for line in self._organs_edit.toPlainText().splitlines()
            if line.strip()
        ]
        self._result = {
            "organs": organs,
            "gt_manufacturer": self._mfr_edit.text().strip(),
            "gt_filename": self._filename_edit.text().strip(),
            "similarity_threshold": float(self._threshold_spin.value()),
        }
        self.accept()

    def template(self) -> dict[str, Any]:
        """Return the template dict (meaningful only after the dialog was accepted)."""
        return dict(self._result)
