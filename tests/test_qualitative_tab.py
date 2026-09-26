"""Tests for the Qualitative Assessment tab — scoring flow, multi-rater, session."""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from autoseg_evaluator.ui.tabs.qualitative import QualitativeTab  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    # The tab pops information/question dialogs on rater-complete and unlock;
    # make them non-blocking + auto-confirm so tests run headless.
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )


def _drawers():
    return [
        {
            "organ_name": "Brainstem",
            "patients": [
                {
                    "patient_id": "P1",
                    "gt": {
                        "rtstruct_sop_uid": "gt1",
                        "source_label": "Manual",
                        "roi_number": 1,
                        "roi_name": "Brainstem",
                    },
                    "tests": [
                        {
                            "rtstruct_sop_uid": "a1",
                            "source_label": "VendorA",
                            "organ_name": "Brainstem",
                            "roi_number": 5,
                        },
                        {
                            "rtstruct_sop_uid": "b1",
                            "source_label": "VendorB",
                            "organ_name": "Brainstem",
                            "roi_number": 9,
                        },
                    ],
                }
            ],
        }
    ]


def _add_rater(tab, name, *, mode="blinded", include_gt=False, randomize=False, seed=0):
    """Add a configured grader directly (bypasses the modal config dialog)."""
    tab._rater_config[name] = {
        "mode": mode,
        "include_gt": include_gt,
        "randomize": randomize,
        "seed": seed,
    }
    tab._rater_list.addItem(name)


def _make_tab(qapp, raters=("Alice",)):
    tab = QualitativeTab()
    tab.set_drawers_provider(_drawers)  # library stays None → CT load no-ops, scoring still works
    for r in raters:
        _add_rater(tab, r)
    return tab


def test_start_locks_and_shows_assessment(qapp):
    tab = _make_tab(qapp)
    locks = []
    tab.assessmentLockChanged.connect(locks.append)
    tab._on_start()
    assert tab._started is True
    assert tab._stack.currentIndex() == 1  # assessment panel
    assert locks == [True]
    # Stack = 2 tests (GT excluded by default)
    assert len(tab._rater_order["Alice"]) == 2


def test_scoring_emits_payload_and_advances(qapp):
    tab = _make_tab(qapp)
    scored = []
    tab.qualitativeScored.connect(scored.append)
    tab._on_start()
    tab._apply_score(4)
    assert len(scored) == 1
    p = scored[0]
    assert p["rater"] == "Alice"
    assert p["score"] == 4
    assert p["blinded"] is True
    assert p["source_label"] == "VendorA"
    assert tab._rater_pos["Alice"] == 1  # advanced to second item


def test_back_returns_to_previous_and_keeps_score(qapp):
    tab = _make_tab(qapp)
    tab._on_start()
    tab._apply_score(5)  # item 0 scored, now at item 1
    assert tab._rater_pos["Alice"] == 1
    tab._go_back()
    assert tab._rater_pos["Alice"] == 0
    assert tab._rater_scores["Alice"][tab._rater_order["Alice"][0]] == 5


def test_multi_rater_progresses_then_unlocks(qapp):
    tab = _make_tab(qapp, raters=("Alice", "Bob"))
    scored = []
    locks = []
    tab.qualitativeScored.connect(scored.append)
    tab.assessmentLockChanged.connect(locks.append)
    tab._on_start()
    # Alice scores both items → switches to Bob
    tab._apply_score(4)
    tab._apply_score(3)
    assert tab._active_rater == "Bob"
    # Bob scores both → assessment complete, tabs unlocked, back to setup
    tab._apply_score(2)
    tab._apply_score(1)
    assert len(scored) == 4
    assert {p["rater"] for p in scored} == {"Alice", "Bob"}
    assert locks[0] is True and locks[-1] is False
    assert tab._stack.currentIndex() == 0


def test_resume_highlighted_rater_preserves_other_progress(qapp):
    tab = _make_tab(qapp, raters=("Alice", "Bob"))
    tab._on_start()  # active = Alice (first)
    tab._apply_score(4)  # Alice rates item 0
    assert tab._active_rater == "Alice"
    tab._on_unlock()  # exit to setup (question auto-confirmed)
    assert tab._stack.currentIndex() == 0
    # Highlight Bob and start again → Bob's run begins, Alice's score kept.
    tab._rater_list.setCurrentRow(1)
    tab._on_start()
    assert tab._active_rater == "Bob"
    assert tab._rater_pos["Bob"] == 0
    assert len(tab._rater_scores["Alice"]) == 1  # preserved, not wiped
    assert tab._stack.currentIndex() == 1


def test_session_round_trip_restores_progress(qapp):
    tab = _make_tab(qapp, raters=("Alice", "Bob"))
    tab._on_start()
    tab._apply_score(4)  # Alice rates item 0
    state = tab.session_state()
    assert state["started"] is True
    assert state["per_rater"]["Alice"]["scores"]

    tab2 = _make_tab(qapp, raters=())  # raters come from the session
    tab2.apply_session_state(state)
    assert tab2._started is True
    # The rater list widget is repopulated, so every rater is visible/selectable.
    listed = [tab2._rater_list.item(i).text() for i in range(tab2._rater_list.count())]
    assert listed == ["Alice", "Bob"]
    assert tab2._active_rater == "Alice"
    assert tab2._selected_rater() == "Alice"  # active rater highlighted
    assert tab2._rater_scores["Alice"] == state["per_rater"]["Alice"]["scores"]
    # Lands on the setup panel (unlocked) so any rater can be resumed.
    assert tab2._stack.currentIndex() == 0
    # Resuming Bob from the list keeps Alice's restored score.
    tab2._rater_list.setCurrentRow(1)
    tab2._on_start()
    assert tab2._active_rater == "Bob"
    assert tab2._stack.currentIndex() == 1
    assert len(tab2._rater_scores["Alice"]) == 1


def test_session_restore_not_started_repopulates_graders_and_configs(qapp):
    tab = _make_tab(qapp, raters=())
    state = {
        "raters": ["Alice", "Bob"],
        "rater_config": {
            "Alice": {"mode": "transparent", "include_gt": True, "randomize": True, "seed": 1},
            "Bob": {"mode": "blinded", "include_gt": False, "randomize": False, "seed": 2},
        },
        "active_rater": None,
        "started": False,
        "per_rater": {},
    }
    tab.apply_session_state(state)
    listed = [tab._rater_list.item(i).text() for i in range(tab._rater_list.count())]
    assert listed == ["Alice", "Bob"]
    assert tab._rater_config["Alice"]["mode"] == "transparent"
    assert tab._rater_config["Alice"]["include_gt"] is True
    assert tab._rater_config["Bob"]["mode"] == "blinded"
    assert tab._started is False
    assert tab._stack.currentIndex() == 0


def test_per_grader_config_drives_stack_and_blinded(qapp):
    tab = QualitativeTab()
    tab.set_drawers_provider(_drawers)
    _add_rater(tab, "Alice", mode="blinded", include_gt=False)
    _add_rater(tab, "Bob", mode="transparent", include_gt=True)
    scored = []
    tab.qualitativeScored.connect(scored.append)
    # Start Alice (blinded, no GT → 2 tests).
    tab._select_rater_in_list("Alice")
    tab._on_start()
    assert len(tab._rater_order["Alice"]) == 2
    tab._apply_score(4)
    assert scored[-1]["blinded"] is True
    # Switch to Bob (transparent, +GT → GT + 2 tests = 3).
    tab._on_unlock()
    tab._select_rater_in_list("Bob")
    tab._on_start()
    assert len(tab._rater_order["Bob"]) == 3
    tab._apply_score(5)
    assert scored[-1]["blinded"] is False


def test_restore_re_emits_saved_scores_for_results(qapp):
    # First grade something, snapshot, then restore into a fresh tab and confirm
    # the saved scores are re-emitted (so the Results tab can repopulate).
    tab = _make_tab(qapp, raters=("Alice",))
    tab._on_start()
    tab._apply_score(4)
    state = tab.session_state()

    tab2 = QualitativeTab()
    tab2.set_drawers_provider(_drawers)
    emitted = []
    tab2.qualitativeScored.connect(emitted.append)
    tab2.apply_session_state(state)
    assert len(emitted) == 1
    assert emitted[0]["rater"] == "Alice"
    assert emitted[0]["score"] == 4


# ---- Scores over several sessions -------------------------------------------


def test_a_score_carries_its_structure_set_and_when_it_was_given(qapp):
    tab = _make_tab(qapp)
    emitted = []
    tab.qualitativeScored.connect(emitted.append)
    tab._on_start()
    tab._apply_score(4)
    assert emitted[-1]["rtstruct_sop_uid"] in ("a1", "b1")
    assert emitted[-1]["scored_at"]
    state = tab.session_state()
    item_id = next(iter(state["per_rater"]["Alice"]["scores"]))
    assert state["per_rater"]["Alice"]["scored_at"][item_id] == emitted[-1]["scored_at"]


def test_scores_for_contours_no_longer_in_the_drawers_are_kept_not_dropped(qapp):
    """A restore used to drop them silently, and the next save lost them.

    Renaming a drawer between sessions changes every item's identity, which is
    the easiest way to lose a day's rating.
    """
    tab = _make_tab(qapp)
    tab._on_start()
    tab._apply_score(4)
    tab._apply_score(2)
    state = tab.session_state()

    renamed = _drawers()
    renamed[0]["organ_name"] = "Brain Stem"
    tab2 = QualitativeTab()
    tab2.set_drawers_provider(lambda: renamed)
    emitted = []
    tab2.qualitativeScored.connect(emitted.append)
    tab2.apply_session_state(state)

    assert tab2.orphaned_score_count() == 2
    # Still sent to the results, as rows of their own...
    assert sorted(e["score"] for e in emitted) == [2, 4]
    assert {e["drawer"] for e in emitted} == {"Brainstem"}
    # ...and saved again, so a later session can still use them.
    again = tab2.session_state()
    assert again["per_rater"]["Alice"]["scores"] == state["per_rater"]["Alice"]["scores"]
    assert again["per_rater"]["Alice"]["scored_at"] == state["per_rater"]["Alice"]["scored_at"]


def test_scores_survive_a_restore_whose_drawers_are_all_missing(qapp):
    tab = _make_tab(qapp)
    tab._on_start()
    tab._apply_score(5)
    state = tab.session_state()

    tab2 = QualitativeTab()
    tab2.set_drawers_provider(lambda: [])
    tab2.apply_session_state(state)

    assert tab2.orphaned_score_count() == 1
    assert (
        tab2.session_state()["per_rater"]["Alice"]["scores"]
        == (state["per_rater"]["Alice"]["scores"])
    )


def test_a_held_score_rejoins_its_grader_once_its_contour_is_back(qapp):
    tab = _make_tab(qapp)
    tab._on_start()
    tab._apply_score(3)
    state = tab.session_state()

    drawers: list = []
    tab2 = QualitativeTab()
    tab2.set_drawers_provider(lambda: drawers)
    tab2.apply_session_state(state)
    assert tab2.orphaned_score_count() == 1

    drawers.extend(_drawers())  # matching restored the drawer
    tab2._on_start()
    assert tab2.orphaned_score_count() == 0
    assert list(tab2._rater_scores["Alice"].values()) == [3]


def test_reset_forgets_every_grader_and_score(qapp):
    tab = _make_tab(qapp, raters=("Alice", "Bob"))
    tab._on_start()
    tab._apply_score(4)
    tab._on_unlock()
    tab.reset()
    assert tab._current_raters() == []
    assert not tab.has_scores()
    assert tab.session_state()["per_rater"] == {}


# ---- Which contour is being graded ---------------------------------------------


def _started(qapp, monkeypatch, mode, include_gt=False):
    tab = QualitativeTab()
    tab.set_drawers_provider(_drawers)
    _add_rater(tab, "Alice", mode=mode, include_gt=include_gt)
    # No CT in these tests: the status line is what is under test.
    monkeypatch.setattr(tab, "_render_item", lambda item: None)
    tab._on_start()
    return tab


def test_transparent_mode_names_the_contour_being_graded(qapp, monkeypatch):
    """Every outline is labelled already; the status line says which one the
    score is for, so it cannot be given to the wrong vendor unnoticed."""
    tab = _started(qapp, monkeypatch, "transparent")
    text = tab._status_label.text()
    assert "Rating: VendorA — Brainstem" in text
    assert "Patient P1 · Brainstem" in text
    tab._apply_score(4)
    assert "Rating: VendorB — Brainstem" in tab._status_label.text()


def test_transparent_mode_names_the_ground_truth_as_such(qapp, monkeypatch):
    tab = _started(qapp, monkeypatch, "transparent", include_gt=True)
    assert "Rating: ground truth — Brainstem" in tab._status_label.text()


def test_blinded_mode_names_no_source(qapp, monkeypatch):
    tab = _started(qapp, monkeypatch, "blinded")
    assert tab._status_label.text() == "Patient P1 · Brainstem"


def test_a_source_name_is_shown_as_text_not_markup(qapp, monkeypatch):
    drawers = _drawers()
    drawers[0]["patients"][0]["tests"][0]["source_label"] = "A<b>&B"
    tab = QualitativeTab()
    tab.set_drawers_provider(lambda: drawers)
    _add_rater(tab, "Alice", mode="transparent")
    monkeypatch.setattr(tab, "_render_item", lambda item: None)
    tab._on_start()
    assert "A&lt;b&gt;&amp;B" in tab._status_label.text()


def test_the_graded_contour_is_bold_in_the_side_panel(qapp):
    import numpy as np

    from autoseg_evaluator.ui.widgets.multiplanar_viewer import Overlay

    tab = QualitativeTab()
    mask = np.zeros((2, 4, 4), dtype=bool)
    tab._rebuild_source_panel(
        [
            Overlay(label="VendorA — Brainstem", color="#1f77b4", mask=mask, active=False),
            Overlay(label="VendorB — Brainstem", color="#ff7f0e", mask=mask, active=True),
        ]
    )
    boxes = [
        tab._source_panel_lay.itemAt(i).widget()
        for i in range(tab._source_panel_lay.count())
        if tab._source_panel_lay.itemAt(i).widget() is not None
    ]
    assert [b.text() for b in boxes] == [
        "VendorA — Brainstem",
        "VendorB — Brainstem  (rating)",
    ]
    assert "font-weight: bold" not in boxes[0].styleSheet()
    assert "font-weight: bold" in boxes[1].styleSheet()
