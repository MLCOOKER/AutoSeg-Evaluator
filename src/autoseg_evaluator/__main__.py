"""Module entry point — `python -m autoseg_evaluator`."""

from __future__ import annotations

import sys


def main() -> int:
    """Start the application, or say why it cannot.

    Also the ``autoseg-evaluator`` console script's entry point.
    """
    try:
        from autoseg_evaluator.app import main as run
    except Exception as exc:  # noqa: BLE001 - reported, then the app exits
        # Before Qt is running: PySide6, or something it needs, did not load.
        from autoseg_evaluator.startup import report_failure

        report_failure(exc)
        return 1
    return run()


if __name__ == "__main__":
    sys.exit(main())
