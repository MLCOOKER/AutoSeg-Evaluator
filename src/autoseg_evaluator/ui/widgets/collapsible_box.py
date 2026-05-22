"""Generic animated accordion widget.

A ``CollapsibleBox`` has a header row (with a disclosure triangle) and a
content area that smoothly animates open/closed when the header is clicked.
The header is a plain widget supplied by the caller, so callers can build
arbitrarily rich headers (icons, action buttons, status badges, etc.).
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLayout,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class CollapsibleBox(QWidget):
    """A drawer with a clickable header and an animated, hideable content area.

    Signals
    -------
    toggled(bool)
        Emitted whenever the drawer's expanded state changes. The bool
        argument is ``True`` for expanded, ``False`` for collapsed.
    """

    toggled = Signal(bool)

    ANIMATION_DURATION_MS = 160

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self._header_container = QFrame(self)
        self._header_container.setObjectName("CollapsibleBoxHeader")
        self._header_container.setFrameShape(QFrame.Shape.StyledPanel)

        header_layout = QHBoxLayout(self._header_container)
        header_layout.setContentsMargins(6, 4, 6, 4)
        header_layout.setSpacing(6)
        self._header_layout = header_layout

        self._toggle_btn = QToolButton(self._header_container)
        self._toggle_btn.setArrowType(Qt.ArrowType.RightArrow)
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setStyleSheet("QToolButton { border: none; padding: 0; }")
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.toggled.connect(self._on_toggled)
        header_layout.addWidget(self._toggle_btn)

        self._header_widget: QWidget | None = None  # set via setHeaderWidget

        self._content_area = QFrame(self)
        self._content_area.setObjectName("CollapsibleBoxContent")
        self._content_area.setFrameShape(QFrame.Shape.NoFrame)
        self._content_area.setMaximumHeight(0)
        self._content_area.setMinimumHeight(0)
        self._content_area.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

        self._animation = QPropertyAnimation(self._content_area, b"maximumHeight", self)
        self._animation.setDuration(self.ANIMATION_DURATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._header_container)
        outer.addWidget(self._content_area)

    # ---- Header / content setters ----------------------------------------

    def setHeaderWidget(self, widget: QWidget) -> None:
        """Place ``widget`` as the header (besides the disclosure arrow)."""
        if self._header_widget is not None:
            self._header_layout.removeWidget(self._header_widget)
            self._header_widget.deleteLater()
        self._header_widget = widget
        self._header_layout.addWidget(widget, stretch=1)

    def headerWidget(self) -> QWidget | None:
        return self._header_widget

    def setContentLayout(self, layout: QLayout) -> None:
        """Install ``layout`` inside the content area.

        Any previous layout is detached and scheduled for deletion.
        """
        old = self._content_area.layout()
        if old is not None:
            # Re-parent the old layout onto a throwaway widget so Qt cleans it up cleanly.
            sink = QWidget()
            sink.setLayout(old)
            sink.deleteLater()
        self._content_area.setLayout(layout)
        if self.isExpanded():
            # Re-measure for the new content if currently open.
            self._content_area.setMaximumHeight(self._content_height())

    def contentArea(self) -> QFrame:
        """Direct access to the content frame (useful for stylesheet tweaks)."""
        return self._content_area

    def headerContainer(self) -> QFrame:
        """Direct access to the outer header frame (so subclasses can style it)."""
        return self._header_container

    # ---- State -----------------------------------------------------------

    def isExpanded(self) -> bool:
        return self._toggle_btn.isChecked()

    def setExpanded(self, expanded: bool) -> None:
        if expanded != self._toggle_btn.isChecked():
            self._toggle_btn.setChecked(expanded)

    def expand(self) -> None:
        self.setExpanded(True)

    def collapse(self) -> None:
        self.setExpanded(False)

    # ---- Internals -------------------------------------------------------

    def _content_height(self) -> int:
        """Best-effort measurement of how tall the content wants to be."""
        layout = self._content_area.layout()
        if layout is None:
            return 0
        # sizeHint on the layout returns the natural size including margins.
        return layout.totalSizeHint().height()

    def _on_toggled(self, checked: bool) -> None:
        self._toggle_btn.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )
        start = self._content_area.maximumHeight()
        end = self._content_height() if checked else 0
        self._animation.stop()
        self._animation.setStartValue(start)
        self._animation.setEndValue(end)
        self._animation.start()
        self.toggled.emit(checked)

