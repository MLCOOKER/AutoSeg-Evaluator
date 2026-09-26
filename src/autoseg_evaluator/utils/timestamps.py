"""Timestamps for results: when a row was computed, when a contour was scored."""

from __future__ import annotations

from datetime import datetime


def now_local() -> str:
    """The current local time with its UTC offset: ``2026-09-26 14:03:22+08:00``.

    Local rather than UTC because the reader is the person who ran the
    computation or gave the score, on their own clock; the offset keeps it
    unambiguous. Seconds only — a row is not produced faster than that matters.
    """
    return datetime.now().astimezone().isoformat(sep=" ", timespec="seconds")


__all__ = ["now_local"]
