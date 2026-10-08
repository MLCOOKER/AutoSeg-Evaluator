"""Refresh a view when it can be seen, and no more often than it can afford.

The Results and Report tabs used to rebuild after every result row and every
Likert score, whether anyone could see them or not. Each rebuild reads the
whole table, so the work grew with the square of the number of rows: measured,
about half an hour of the window's time over a 1,000-row computation, and
seconds of delay on every Likert click. A hidden tab need not be current until
it is shown, and a visible one only to within a second or so.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QWidget


class DeferredRefresh(QObject):
    """Gather refresh requests for ``widget`` into as few refreshes as will do.

    A request while the widget is hidden only marks it out of date; it
    refreshes when next shown, before it is painted, so it never shows stale
    rows. The widget reports that through :meth:`shown`, from its
    ``showEvent``. A request while it is visible runs after
    ``min_interval_ms``, along with every other request made in the meantime.
    The wait grows with the refresh's own cost, to ``load_factor`` times its
    last duration, so however large the table grows, refreshing takes at most
    about a quarter of the window's time and the window stays responsive.
    """

    def __init__(
        self,
        widget: QWidget,
        refresh: Callable[[], None],
        *,
        min_interval_ms: int = 1000,
        load_factor: float = 3.0,
    ) -> None:
        # A child of the widget, found through parent() rather than held, so it
        # goes with the widget and never outlives it.
        super().__init__(widget)
        self._refresh = refresh
        self._min_interval_ms = int(min_interval_ms)
        self._load_factor = float(load_factor)
        self._interval_ms = self._min_interval_ms
        self._pending = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)

    @property
    def pending(self) -> bool:
        """Whether a request is waiting to be refreshed."""
        return self._pending

    def request(self) -> None:
        """Ask for a refresh: soon if the widget is visible, else when shown."""
        self._pending = True
        if self._visible() and not self._timer.isActive():
            self._timer.start(self._interval_ms)

    def shown(self) -> None:
        """The widget has just been shown: refresh it now if anything is owed."""
        if self._pending:
            self._run()

    def settled(self) -> None:
        """The widget has just refreshed by other means, so nothing is owed."""
        self._pending = False
        self._timer.stop()

    def _visible(self) -> bool:
        widget = self.parent()
        return isinstance(widget, QWidget) and widget.isVisible()

    def _on_timeout(self) -> None:
        # Hidden since the request: it stays pending and refreshes when shown.
        if self._pending and self._visible():
            self._run()

    def _run(self) -> None:
        self._pending = False
        self._timer.stop()
        started = time.perf_counter()
        self._refresh()
        elapsed_ms = 1000.0 * (time.perf_counter() - started)
        self._interval_ms = max(self._min_interval_ms, int(self._load_factor * elapsed_ms))
