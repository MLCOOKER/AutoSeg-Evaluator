"""Tests for session save/load round-tripping."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication

from autoseg_evaluator.data.metadata import MetadataLibrary  # noqa: E402
from autoseg_evaluator.data.session import (  # noqa: E402
    SCHEMA_VERSION,
    build_session_dict,
    load_session_file,
    save_session,
)
from autoseg_evaluator.ui.tabs.match_contours import MatchContoursTab  # noqa: E402
from autoseg_evaluator.ui.widgets.loaded_contours_tree import (  # noqa: E402
    ROLE_NODE_KIND,
    ROLE_PATIENT_ID,
    ROLE_ROI_NAME,
)
from test_metadata import _write_ct_slice, _write_rtstruct  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def populated_library(tmp_path):
    """Two patients, manual + AI RTSS each."""
    from pydicom.uid import generate_uid

    folder = tmp_path
    organs = ["Prostate", "Bladder", "Rectum"]
    for pid in ("HN1", "HN2"):
        study_uid = generate_uid()
        for_uid = generate_uid()
        _write_ct_slice(
            folder, patient_id=pid, study_uid=study_uid, series_uid=generate_uid(), for_uid=for_uid
        )
        _write_rtstruct(
            folder,
            patient_id=pid,
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="Varian",
            organs=organs,
            filename=f"manual_{pid}.dcm",
        )
        _write_rtstruct(
            folder,
            patient_id=pid,
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="Limbus",
            organs=organs,
            filename=f"limbus_{pid}.dcm",
        )
    lib = MetadataLibrary()
    lib.scan_folder(str(folder))
    return lib, folder


def _make_tab(qapp, library):
    tab = MatchContoursTab(settings={"tolerances": {"similarity_threshold": 0.6}})
    tab.set_library(library)
    return tab


def _select_organs(tree, predicate):
    tree.clearSelection()

    def walk(item):
        if item.data(0, ROLE_NODE_KIND) == "organ" and predicate(item):
            item.setSelected(True)
        for i in range(item.childCount()):
            walk(item.child(i))

    for i in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(i))


# ---- Session file format -------------------------------------------------


def test_build_session_dict_contains_required_fields():
    data = build_session_dict(
        folder="/x/y",
        drawers_state=[
            {"organ_name": "Prostate", "truncate": False, "expanded": True, "patients": []}
        ],
        replacement_rules=[{"find": "a", "replace": "b"}],
        template={"organs": ["Prostate"], "gt_manufacturer": "Varian"},
    )
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["folder"] == "/x/y"
    assert data["replacement_rules"] == [{"find": "a", "replace": "b"}]
    assert data["last_template"]["gt_manufacturer"] == "Varian"
    assert data["drawers"][0]["organ_name"] == "Prostate"
    assert "saved_at" in data


def test_save_and_load_session_round_trip(tmp_path):
    path = tmp_path / "test.session.json"
    payload = build_session_dict(
        folder="/some/folder",
        drawers_state=[],
        replacement_rules=[],
        template={},
    )
    save_session(path, payload)
    assert path.exists()
    reloaded = load_session_file(path)
    assert reloaded == payload


def test_load_session_rejects_future_schema(tmp_path):
    path = tmp_path / "future.session.json"
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION + 99}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema version"):
        load_session_file(path)


def test_session_dict_round_trips_qualitative_block(tmp_path):
    qualitative = {
        "mode": "blinded",
        "raters": ["Alice", "Bob"],
        "include_gt": True,
        "randomize": True,
        "seed": 1234,
        "active_rater": "Alice",
        "per_rater": {
            "Alice": {"order": ["P1|Organ|sop|5"], "scores": {"P1|Organ|sop|5": 4}, "index": 1},
            "Bob": {"order": ["P1|Organ|sop|5"], "scores": {}, "index": 0},
        },
    }
    payload = build_session_dict(
        folder="/x", drawers_state=[], replacement_rules=[], template={}, qualitative=qualitative
    )
    assert payload["schema_version"] == 5
    path = tmp_path / "q.session.json"
    save_session(path, payload)
    assert load_session_file(path)["qualitative"] == qualitative


def test_pre_v4_session_without_qualitative_loads(tmp_path):
    """A v3 session (no qualitative key) loads cleanly — the tab just starts empty."""
    path = tmp_path / "v3.session.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "folder": "/x",
                "drawers": [],
                "replacement_rules": [],
                "last_template": {},
                "consensus_groups": [],
            }
        ),
        encoding="utf-8",
    )
    data = load_session_file(path)
    assert data.get("qualitative", {}) == {}


# ---- session_state snapshot + apply_session_state -----------------------


def test_session_state_captures_current_drawers(qapp, populated_library):
    lib, _folder = populated_library
    tab = _make_tab(qapp, lib)
    _select_organs(
        tab._tree,
        lambda it: (
            str(it.data(0, ROLE_PATIENT_ID) or "") == "HN1"
            and str(it.data(0, ROLE_ROI_NAME) or "") == "Prostate"
            and "manual_" in (it.parent().text(0) if it.parent() else "")
        ),
    )
    tab._on_set_ground_truth_clicked()
    state = tab.session_state()
    assert len(state) == 1
    drawer = state[0]
    assert drawer["organ_name"] == "Prostate"
    assert drawer["patients"][0]["patient_id"] == "HN1"
    assert drawer["patients"][0]["gt"]["roi_name"] == "Prostate"
    # Auto-match should have populated at least one test row from Limbus
    assert len(drawer["patients"][0]["tests"]) >= 1


def test_apply_session_state_restores_drawers(qapp, populated_library):
    lib, _folder = populated_library
    # Phase 1: set up drawers and snapshot
    tab1 = _make_tab(qapp, lib)
    _select_organs(
        tab1._tree,
        lambda it: (
            str(it.data(0, ROLE_ROI_NAME) or "") == "Prostate"
            and "manual_" in (it.parent().text(0) if it.parent() else "")
        ),
    )
    tab1._on_set_ground_truth_clicked()
    snapshot = tab1.session_state()
    assert len(snapshot) == 1
    assert len(snapshot[0]["patients"]) == 2  # HN1 + HN2

    # Phase 2: fresh tab, apply the snapshot, verify state matches
    tab2 = _make_tab(qapp, lib)
    applied, missing, warnings = tab2.apply_session_state(snapshot)
    assert missing == 0
    assert warnings == []
    assert applied == 2
    assert "Prostate" in tab2._drawers
    restored = tab2._drawers["Prostate"]
    assert set(restored.patient_ids()) == {"HN1", "HN2"}
    # Tests should have been restored as well
    sub_hn1 = restored.patient_subsection("HN1")
    assert len(sub_hn1.tests) == len(snapshot[0]["patients"][0]["tests"])


def test_apply_session_warns_when_sop_uid_missing(qapp, populated_library):
    """A session referencing a SOP UID not present in the library reports missing/warning."""
    lib, _folder = populated_library
    fake_state = [
        {
            "organ_name": "Prostate",
            "truncate": False,
            "expanded": False,
            "patients": [
                {
                    "patient_id": "HN1",
                    "gt": {
                        "rtstruct_sop_uid": "this-sop-does-not-exist",
                        "rtstruct_filename": "ghost.dcm",
                        "source_label": "Ghost",
                        "roi_number": 1,
                        "roi_name": "Prostate",
                    },
                    "tests": [],
                }
            ],
        }
    ]
    tab = _make_tab(qapp, lib)
    applied, missing, warnings = tab.apply_session_state(fake_state)
    assert applied == 0
    assert missing == 1
    assert warnings  # at least one warning
    assert any("not present" in w for w in warnings)
    # The drawer was created but holds no patients
    assert "Prostate" in tab._drawers
    assert tab._drawers["Prostate"].patient_count() == 0


def test_apply_session_resets_previous_drawers(qapp, populated_library):
    """Loading a session should wipe any in-progress drawers before restoring."""
    lib, _folder = populated_library
    tab = _make_tab(qapp, lib)
    # Manually create a drawer that will NOT exist in the restored state
    _select_organs(
        tab._tree,
        lambda it: (
            str(it.data(0, ROLE_ROI_NAME) or "") == "Bladder"
            and "manual_" in (it.parent().text(0) if it.parent() else "")
        ),
    )
    tab._on_set_ground_truth_clicked()
    assert "Bladder" in tab._drawers
    # Now apply an empty session — Bladder should disappear
    tab.apply_session_state([])
    assert tab._drawers == {}


def test_session_dict_round_trips_link_overrides(tmp_path):
    """Schema v5: the answers given in Review Data Links travel with the session."""
    overrides = {
        "P1|1.2.3.4|series": "1.2.3.9",
        "P1|1.2.3.4|dose": "1.2.3.7",
    }
    payload = build_session_dict(
        folder="/x",
        drawers_state=[],
        replacement_rules=[],
        template={},
        link_overrides=overrides,
    )
    assert payload["schema_version"] == 5
    assert payload["link_overrides"] == overrides

    path = tmp_path / "s.session.json"
    save_session(path, payload)
    assert load_session_file(path)["link_overrides"] == overrides


def test_session_without_link_overrides_still_loads():
    """A v4 session predates the key; absent simply means fully automatic."""
    payload = build_session_dict(folder="/x", drawers_state=[], replacement_rules=[], template={})
    assert payload["link_overrides"] == {}


def test_v4_session_file_loads_under_v5(tmp_path):
    """Older sessions on disk must keep loading after the schema bump."""
    path = tmp_path / "old.session.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 4,
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
    data = load_session_file(path)
    assert data["schema_version"] == 4
    assert data.get("link_overrides", {}) == {}
