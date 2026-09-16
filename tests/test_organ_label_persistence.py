"""Organ labels belong to a session, not to the application's settings.

They describe one cohort's naming rather than a preference, so writing them the
moment a dialog closes would both leak them into unrelated folders and record
work the user never chose to keep. Drawers and matches already behave this way;
labels now match them.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from autoseg_evaluator.data.session import (  # noqa: E402
    build_session_dict,
    load_session_file,
    save_session,
)
from autoseg_evaluator.ui.main_window import MainWindow  # noqa: E402

LABELS = {"SubmanG_R": "glnd_submand", "Chiasm": "opticchiasm"}


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_labels_start_empty(qapp):
    win = MainWindow({})
    assert win._organ_assignments == {}
    win.close()


def test_labels_never_reach_the_settings_file(qapp):
    """The specific complaint: they were being saved silently."""
    settings: dict = {}
    win = MainWindow(settings)
    win._organ_assignments = dict(LABELS)
    assert "organ_assignments" not in settings
    win.close()


def test_labels_are_written_into_a_saved_session(qapp, tmp_path):
    win = MainWindow({})
    win._organ_assignments = dict(LABELS)

    payload = build_session_dict(
        folder="/x",
        drawers_state=[],
        replacement_rules=[],
        template={},
        organ_assignments=dict(win._organ_assignments),
    )
    path = tmp_path / "s.session.json"
    save_session(path, payload)
    assert load_session_file(path)["organ_assignments"] == LABELS
    win.close()


def test_loading_a_session_defers_labels_until_the_scan_completes(qapp, tmp_path):
    """A session load triggers a rescan; labels must land after the library does.

    Applying them to an empty library would drop them on the floor, because the
    index is rebuilt from what the scan found.
    """
    win = MainWindow({})
    win._pending_organ_assignments = dict(LABELS)
    win._library = None

    # No library yet — the pending labels stay pending.
    win._rebuild_organ_index()
    assert win._organ_assignments == {}
    assert win._pending_organ_assignments == LABELS
    win.close()


def test_a_session_without_labels_leaves_them_empty(tmp_path):
    payload = build_session_dict(folder="/x", drawers_state=[], replacement_rules=[], template={})
    path = tmp_path / "s.session.json"
    save_session(path, payload)
    assert load_session_file(path)["organ_assignments"] == {}


def test_a_pre_v6_session_has_no_labels(tmp_path):
    path = tmp_path / "old.session.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 5,
                "folder": "/x",
                "replacement_rules": [],
                "last_template": {},
                "drawers": [],
                "consensus_groups": [],
                "qualitative": {},
            }
        ),
        encoding="utf-8",
    )
    assert load_session_file(path).get("organ_assignments", {}) == {}
