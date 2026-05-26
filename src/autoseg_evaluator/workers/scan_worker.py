"""Background DICOM folder scanner — runs MetadataLibrary.scan_folder in a QThread."""

from __future__ import annotations

import traceback
from collections.abc import Mapping

from PySide6.QtCore import QObject, Signal, Slot

from autoseg_evaluator.data.metadata import MetadataLibrary


class ScanWorker(QObject):
    """QObject worker — move to a QThread, then call ``run()``.

    Signals
    -------
    progress(int, int, str)
        ``(current_index, total_files, current_filename)`` — emitted periodically
        during the scan so the UI can update a progress display.
    finished(object)
        Emitted with the populated ``MetadataLibrary`` on successful completion.
    error(str)
        Emitted with a human-readable error message if the scan raises unexpectedly.
    """

    progress = Signal(int, int, str)
    finished = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        folder_path: str,
        custom_overrides: Mapping[str, str] | None = None,
        progress_throttle: int = 25,
    ) -> None:
        super().__init__()
        self._folder_path = folder_path
        self._custom_overrides = dict(custom_overrides or {})
        self._progress_throttle = max(1, int(progress_throttle))
        self._cancelled = False

    @Slot()
    def cancel(self) -> None:
        """Request that the running scan stop at the next safe point."""
        self._cancelled = True

    @Slot()
    def run(self) -> None:
        try:
            library = MetadataLibrary()

            def _progress_cb(idx: int, total: int, path: str) -> None:
                # Throttle UI updates: emit on every Nth file and on the final tick
                if idx % self._progress_throttle == 0 or idx >= total:
                    self.progress.emit(idx, total, path)

            library.scan_folder(
                self._folder_path,
                progress_callback=_progress_cb,
                custom_overrides=self._custom_overrides,
                cancel_check=lambda: self._cancelled,
            )
            if not self._cancelled:
                self.finished.emit(library)
        except Exception as exc:  # noqa: BLE001 — surface anything unexpected to the UI
            self.error.emit(f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}")
