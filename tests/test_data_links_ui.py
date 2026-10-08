"""Tests for the Tab 1 data-link gate and the Review Data Links dialog.

The design decision these lock in: an ambiguous link is settled by the user in
Load Data *before* any computation, rather than being guessed at compute time
or flagged afterwards on a results row. So there are two things to prove —
the dialog can express the answer, and Compute refuses to start until it has.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from autoseg_evaluator.data.linkage import (  # noqa: E402
    KIND_DOSE,
    KIND_SERIES,
    collect_link_issues,
    override_key,
)
from autoseg_evaluator.ui.dialogs.data_links import DataLinksDialog  # noqa: E402
from autoseg_evaluator.ui.tabs.compute import ComputeTab  # noqa: E402
from tests.test_linkage import _build_reirradiation, _scan  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _ambiguous_library(tmp: str):
    """A two-course cohort under one FoR and one study — nothing auto-resolves."""
    root = Path(tmp)
    uids = _build_reirradiation(root, explicit=False, same_study=True)
    return _scan(root), uids


def _clean_library(tmp: str):
    root = Path(tmp)
    uids = _build_reirradiation(root, explicit=True)
    return _scan(root), uids


# ---- Dialog ---------------------------------------------------------------


def test_dialog_lists_every_structure_set(qapp):
    with tempfile.TemporaryDirectory() as tmp:
        lib, _ = _ambiguous_library(tmp)
        dialog = DataLinksDialog(lib, {})
        assert dialog._table.rowCount() == 2
        dialog.deleteLater()


def test_dialog_offers_every_candidate_plus_automatic(qapp):
    """Each combo carries an 'automatic' row followed by the real candidates."""
    with tempfile.TemporaryDirectory() as tmp:
        lib, _ = _ambiguous_library(tmp)
        dialog = DataLinksDialog(lib, {})
        series_combos = [c for _p, _s, kind, c in dialog._combos if kind == KIND_SERIES]
        assert series_combos
        for combo in series_combos:
            # 1 automatic row + 2 candidate CT series.
            assert combo.count() == 3
            assert combo.itemData(0) == ""
            assert combo.currentIndex() == 0
        dialog.deleteLater()


def test_choosing_a_candidate_produces_an_override(qapp):
    with tempfile.TemporaryDirectory() as tmp:
        lib, (rtss_1, _d1, _rtss_2, _d2) = _ambiguous_library(tmp)
        dialog = DataLinksDialog(lib, {})
        patient_id = next(iter(lib.patients))

        target = None
        for pid, sop, kind, combo in dialog._combos:
            if sop == rtss_1 and kind == KIND_SERIES:
                combo.setCurrentIndex(1)
                target = (pid, sop, combo.currentData())
                break
        assert target is not None

        overrides = dialog.overrides()
        assert overrides[override_key(patient_id, rtss_1, KIND_SERIES)] == target[2]
        dialog.deleteLater()


def test_dialog_preselects_existing_overrides(qapp):
    with tempfile.TemporaryDirectory() as tmp:
        lib, (rtss_1, _d1, _r2, _d2) = _ambiguous_library(tmp)
        patient_id = next(iter(lib.patients))
        series_uids = [
            s.series_instance_uid for c in lib.patients[patient_id].contexts for s in c.image_series
        ]
        existing = {override_key(patient_id, rtss_1, KIND_SERIES): series_uids[1]}

        dialog = DataLinksDialog(lib, existing)
        for _pid, sop, kind, combo in dialog._combos:
            if sop == rtss_1 and kind == KIND_SERIES:
                assert combo.currentData() == series_uids[1]
        assert dialog.overrides() == existing
        dialog.deleteLater()


def test_dialog_does_not_mutate_the_library(qapp):
    """Choices only take effect when the caller applies them, not on selection.

    The status line re-resolves against a temporary view to keep its count
    live; that must not leak into the library if the user cancels.
    """
    with tempfile.TemporaryDirectory() as tmp:
        lib, _ = _ambiguous_library(tmp)
        lib.link_overrides = {"sentinel": "value"}
        dialog = DataLinksDialog(lib, {})
        for _pid, _sop, kind, combo in dialog._combos:
            if kind == KIND_SERIES:
                combo.setCurrentIndex(1)
        assert lib.link_overrides == {"sentinel": "value"}
        dialog.deleteLater()


def test_reset_clears_every_override(qapp):
    with tempfile.TemporaryDirectory() as tmp:
        lib, _ = _ambiguous_library(tmp)
        dialog = DataLinksDialog(lib, {})
        for _pid, _sop, kind, combo in dialog._combos:
            if kind == KIND_SERIES:
                combo.setCurrentIndex(1)
        assert dialog.overrides()
        dialog._on_reset()
        assert dialog.overrides() == {}
        dialog.deleteLater()


def test_dialog_answers_clear_the_blocking_issues(qapp):
    """End to end: what the dialog returns is what unblocks the gate."""
    with tempfile.TemporaryDirectory() as tmp:
        lib, _ = _ambiguous_library(tmp)
        assert collect_link_issues(lib)

        dialog = DataLinksDialog(lib, {})
        for _pid, _sop, _kind, combo in dialog._combos:
            if combo.count() > 1:
                combo.setCurrentIndex(1)
        lib.link_overrides = dialog.overrides()
        assert collect_link_issues(lib) == []
        dialog.deleteLater()


# ---- Compute gate ---------------------------------------------------------


def _dvh_off(cfg):
    cfg["dvh"] = {
        "include_dmean": False,
        "include_dmax": False,
        "include_dmin": False,
        "d_at_volumes_pct": [],
        "d_at_volumes_cc": [],
        "v_at_doses_gy": [],
    }
    return cfg


def _dvh_on(cfg):
    cfg = _dvh_off(cfg)
    cfg["dvh"]["include_dmean"] = True
    return cfg


def test_compute_is_blocked_while_links_are_ambiguous(qapp):
    with tempfile.TemporaryDirectory() as tmp:
        lib, _ = _ambiguous_library(tmp)
        tab = ComputeTab()
        tab.set_library(lib)
        blocker = tab._link_blocker(_dvh_on(tab.config()))
        assert blocker
        assert "Review Data Links" in blocker
        tab.deleteLater()


def test_compute_is_allowed_once_links_are_settled(qapp):
    with tempfile.TemporaryDirectory() as tmp:
        lib, _ = _ambiguous_library(tmp)
        tab = ComputeTab()
        tab.set_library(lib)
        assert tab._link_blocker(_dvh_on(tab.config()))

        dialog = DataLinksDialog(lib, {})
        for _pid, _sop, _kind, combo in dialog._combos:
            if combo.count() > 1:
                combo.setCurrentIndex(1)
        lib.link_overrides = dialog.overrides()
        dialog.deleteLater()

        assert tab._link_blocker(_dvh_on(tab.config())) == ""
        tab.deleteLater()


def test_dose_ambiguity_does_not_block_when_no_dose_metric_is_requested(qapp):
    """Turning DVH off should not force someone to answer a dose question."""
    with tempfile.TemporaryDirectory() as tmp:
        lib, _ = _ambiguous_library(tmp)
        patient_id = next(iter(lib.patients))
        # Settle the series links only, leaving both dose links ambiguous.
        series_uids = [
            s.series_instance_uid for c in lib.patients[patient_id].contexts for s in c.image_series
        ]
        overrides = {}
        for ctx in lib.patients[patient_id].contexts:
            for rtss in ctx.rtstructs:
                overrides[override_key(patient_id, rtss.sop_instance_uid, KIND_SERIES)] = (
                    series_uids[0]
                )
        lib.link_overrides = overrides

        tab = ComputeTab()
        tab.set_library(lib)
        assert tab._link_blocker(_dvh_off(tab.config())) == ""
        assert tab._link_blocker(_dvh_on(tab.config()))
        assert {i.kind for i in collect_link_issues(lib)} == {KIND_DOSE}
        tab.deleteLater()


def test_clean_cohort_never_blocks(qapp):
    with tempfile.TemporaryDirectory() as tmp:
        lib, _ = _clean_library(tmp)
        tab = ComputeTab()
        tab.set_library(lib)
        assert tab._link_blocker(_dvh_on(tab.config())) == ""
        tab.deleteLater()


def test_compute_gate_is_inert_without_a_library(qapp):
    tab = ComputeTab()
    assert tab._link_blocker(_dvh_on(tab.config())) == ""
    tab.deleteLater()
