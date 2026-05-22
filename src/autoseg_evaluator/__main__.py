"""Module entry point — `python -m autoseg_evaluator`."""

from __future__ import annotations

import sys

from autoseg_evaluator.app import main

if __name__ == "__main__":
    sys.exit(main())
