"""Tests for cross-platform window-geometry clamping (fits any screen)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from autoseg_evaluator.ui.main_window import _clamp_to_available  # noqa: E402


def test_caps_size_to_screen_and_moves_onscreen():
    # 1400×900 default on a 1280×800 laptop → capped and pulled fully on-screen.
    w, h, x, y = _clamp_to_available(1400, 900, 100, 100, (0, 0, 1280, 800))
    assert (w, h) == (1280, 800)
    assert (x, y) == (0, 0)


def test_leaves_fitting_window_unchanged():
    assert _clamp_to_available(1000, 700, 50, 40, (0, 0, 1440, 900)) == (1000, 700, 50, 40)


def test_shifts_offscreen_window_back_within_bounds():
    w, h, x, y = _clamp_to_available(800, 600, 5000, 5000, (0, 0, 1440, 900))
    assert (w, h) == (800, 600)
    assert x + w <= 1440 and y + h <= 900
    assert (x, y) == (640, 300)


def test_respects_work_area_origin_macos_menu_bar():
    # macOS work area starts below the menu bar (y = 25); a huge saved window
    # from another monitor is capped and pinned to the work-area top-left.
    w, h, x, y = _clamp_to_available(2000, 2000, -100, -100, (0, 25, 1440, 875))
    assert (w, h) == (1440, 875)
    assert (x, y) == (0, 25)
