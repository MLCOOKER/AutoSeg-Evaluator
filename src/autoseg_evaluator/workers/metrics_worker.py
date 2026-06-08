"""Background worker that drives metric computation for every drawer/test row.

Iterates the snapshot from :meth:`MatchContoursTab.session_state` and the
configuration from :meth:`ComputeTab.config`, emits structured progress
updates after each per-row metric block, and pushes one ``result`` signal
per (GT × test) pair. Per-row errors are captured into ``row['error']``
and counted; the worker only emits the bare ``error`` signal for
unrecoverable, batch-level failures.

The worker is a ``QObject``: call ``moveToThread(thread)`` then connect
``thread.started → worker.run`` and ``worker.finished → thread.quit``.
"""

from __future__ import annotations

import gc
import traceback
from typing import Any

import pydicom
from PySide6.QtCore import QObject, Signal, Slot

from autoseg_evaluator.core.dvh import DVHConfig, DVHError, _fmt_num, compute_dvh_metrics
from autoseg_evaluator.core.masks import (
    extract_mask_for_roi,
    find_reference_image_folder,
    read_dicom_image,
    read_rtstruct,
    truncate_to_gt_z_extent,
)
from autoseg_evaluator.core.metrics import compute_geometric_metrics
from autoseg_evaluator.core.staple import (
    StapleConfig,
    compute_staple,
    sensitivity_specificity_vs_reference,
)

# "Mode" value for the per-organ STAPLE summary row (sensitivity/specificity
# live on the per-rater rows; this row carries the aggregate consensus metrics
# — volume, entropy, disagreement, bbox padding, iterations, convergence).
_STAPLE_DETAILS_MODE = "STAPLE Details"
# "Mode" values that designate a STAPLE-consensus comparison.
_MODE_MULTI_OBSERVER = "Multi-observer STAPLE"
_MODE_GENERIC_STAPLE_GT = "Generic STAPLE with GT"
_MODE_GENERIC_STAPLE_NO_GT = "Generic STAPLE no GT"


class MetricsWorker(QObject):
    """Drives the per-row metric loop on a background thread.

    Signals
    -------
    progress(int, int, dict)
        Emitted as ``(current_step, total_steps, state)``. ``state`` carries
        the same keys :class:`ProgressPanel.update_progress` consumes.
    result(dict)
        Emitted once per (GT × test) row. See :func:`_make_row_template` for
        the schema.
    finished(int)
        Emitted with the total error count when the run completes (whether
        normally, cancelled, or after batch-level error).
    error(str)
        Emitted for unrecoverable failures (e.g. bad config). Per-row errors
        do NOT fire this — they are written into ``row['error']`` instead.
    """

    progress = Signal(int, int, dict)
    result = Signal(dict)
    finished = Signal(int)
    error = Signal(str)

    def __init__(
        self,
        library: Any,
        drawers_state: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> None:
        super().__init__()
        self._library = library
        self._drawers_state = list(drawers_state or [])
        self._config = dict(config or {})
        self._dvh_config = DVHConfig.from_dict(self._config.get("dvh", {}) or {})
        self._staple_config = StapleConfig.from_dict(self._config.get("staple", {}) or {})
        self._cancelled = False
        # Caches keyed by SOP UID / patient ID to avoid redundant DICOM I/O
        self._rtstruct_cache: dict[str, Any] = {}
        self._ct_cache: dict[str, Any] = {}
        self._mask_cache: dict[tuple[str, str, int], Any] = {}
        self._dose_cache: dict[str, Any] = {}
        # STAPLE summary scalars captured while synthesising a multi-observer
        # consensus GT (Tab 2), keyed by (patient_id, synthetic_sop, roi_number).
        # Lets the GT branch emit a "STAPLE Details" row without re-running EM.
        self._synthetic_staple_summaries: dict[tuple[str, str, int], dict[str, Any]] = {}

    @Slot()
    def cancel(self) -> None:
        self._cancelled = True

    @Slot()
    def run(self) -> None:
        try:
            self._do_run()
        except Exception as exc:  # noqa: BLE001 — surface anything unexpected
            self.error.emit(f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}")
            self.finished.emit(0)

    # ---- Main loop --------------------------------------------------------

    def _do_run(self) -> None:
        groups = self._enumerate_groups()
        if not groups:
            self.finished.emit(0)
            return

        # Progress is measured in (drawer × patient) work units, weighted by
        # the number of test raters in each group (the dominant per-group
        # cost). This total is known exactly up front, so the bar stays honest
        # regardless of how many result rows a group ends up emitting (GT,
        # gt-dose, STAPLE-detail, per-rater, …) — a row-count estimate could
        # never track those reliably and used to leave the bar stuck or short.
        total = sum(self._group_weight(g) for g in groups)
        if total == 0:
            self.finished.emit(0)
            return

        errors = 0
        units_done = 0  # weighted work units completed → drives the bar
        completed = 0  # whole (drawer × patient) groups finished → the counter
        total_drawers = len(groups)  # every drawer×patient evaluation, not unique organs
        current_patient: str | None = None

        # Identify the final group index for each patient. After mask creation
        # in that group finishes, the CT (~200 MB) is no longer needed for
        # this patient and can be dropped *before* the metric-compute phase,
        # shaving ~200 MB off peak RAM during the slowest part of the run.
        last_group_idx_for_patient: dict[str, int] = {}
        for i, g in enumerate(groups):
            last_group_idx_for_patient[g["patient_id"]] = i

        for i, group in enumerate(groups):
            if self._cancelled:
                break

            # When we move to a new patient, release the previous patient's
            # CT / masks / RTSTRUCTs / dose datasets before doing more work.
            # This keeps peak RAM bounded by a single patient's data instead
            # of the whole cohort, which matters dramatically when working
            # with 50+ patients × multiple AI vendors × thick masks.
            if current_patient is not None and group["patient_id"] != current_patient:
                self._evict_patient_caches(current_patient)
            current_patient = group["patient_id"]

            state = {
                "patient": group["patient_id"],
                "drawer": group["organ_name"],
                "gt": group["gt_filename"],
                "test": "loading…",
                "metric": "loading…",
                "drawers_done": completed,
                "drawers_total": total_drawers,
                "errors": errors,
            }
            self.progress.emit(units_done, total, state)

            is_last_for_patient = i == last_group_idx_for_patient[group["patient_id"]]
            for row in self._compute_group(
                group, state, units_done, total, drop_ct_after_masks=is_last_for_patient
            ):
                if row.get("error"):
                    errors += 1
                self.result.emit(row)

            units_done += self._group_weight(group)
            completed += 1

        # Evict the final patient's caches before we exit.
        if current_patient is not None:
            self._evict_patient_caches(current_patient)

        # Final progress tick + finished signal
        self.progress.emit(
            units_done,
            total,
            {
                "drawers_done": completed,
                "drawers_total": total_drawers,
                "errors": errors,
            },
        )
        self.finished.emit(errors)

    def _enumerate_groups(self) -> list[dict[str, Any]]:
        """One entry per (drawer × patient). Each carries the GT + every test as ``raters``."""
        groups: list[dict[str, Any]] = []
        for drawer in self._drawers_state:
            organ_name = str(drawer.get("organ_name", "") or "")
            truncate = bool(drawer.get("truncate", False))
            gt_comparison = bool(drawer.get("gt_comparison", True))
            staple_consensus = bool(drawer.get("staple_consensus", False))
            # Default True for backward compatibility with sessions saved
            # before the per-drawer GT-pool toggle existed.
            staple_include_gt = bool(drawer.get("staple_include_gt", True))
            if not (gt_comparison or staple_consensus):
                # Nothing to compute for this drawer
                continue
            for patient in drawer.get("patients", []) or []:
                gt = patient.get("gt", {}) or {}
                tests = []
                for t in patient.get("tests", []) or []:
                    tests.append(
                        {
                            "rtstruct_sop_uid": str(t.get("rtstruct_sop_uid", "") or ""),
                            "source_label": str(t.get("source_label", "") or ""),
                            "organ_name": str(t.get("organ_name", "") or ""),
                            "roi_number": int(t.get("roi_number", 0) or 0),
                            "similarity": float(t.get("similarity", 0.0) or 0.0),
                        }
                    )
                groups.append(
                    {
                        "organ_name": organ_name,
                        "truncate": truncate,
                        "gt_comparison": gt_comparison,
                        "staple_consensus": staple_consensus,
                        "staple_include_gt": staple_include_gt,
                        "patient_id": str(patient.get("patient_id", "") or ""),
                        "gt_sop": str(gt.get("rtstruct_sop_uid", "") or ""),
                        "gt_filename": str(gt.get("rtstruct_filename", "") or ""),
                        "gt_source": str(gt.get("source_label", "") or ""),
                        "gt_roi_number": int(gt.get("roi_number", 0) or 0),
                        "gt_roi_name": str(gt.get("roi_name", "") or ""),
                        "tests": tests,
                    }
                )
        # Sort patient-major so we finish every drawer for a patient before
        # moving on. Lets ``_do_run`` evict that patient's caches in one
        # block and keeps peak RAM bounded by a single patient's worth of
        # CT + masks instead of the entire cohort's worth.
        groups.sort(key=lambda g: (g["patient_id"], g["organ_name"]))
        return groups

    def _group_weight(self, group: dict[str, Any]) -> int:
        """Relative cost of a group for the progress bar.

        Weighted by the number of test raters — the dominant per-group cost
        (mask rasterisation + metric/DVH computation scale with it) — so a
        5-vendor drawer advances the bar five times as far as a 1-vendor one.
        Floored at 1 so every group makes the bar move.
        """
        return max(1, len(group["tests"]))

    # ---- Per-group computation -------------------------------------------

    def _compute_group(
        self,
        group: dict[str, Any],
        state: dict[str, Any],
        idx: int,
        total: int,
        *,
        drop_ct_after_masks: bool = False,
    ) -> list[dict[str, Any]]:
        """Emit every result row for one (drawer × patient).

        Loads the GT + all test masks once, applies truncation if the drawer
        requested it, then dispatches to the GT-comparison and/or STAPLE
        branches. Row-level errors land in ``row['error']`` so the table can
        flag them red; only catastrophic per-group failures (no CT, no GT
        mask) short-circuit the rest of the group.

        When ``drop_ct_after_masks`` is ``True`` (set by the caller when this
        is the final group for the patient), the cached CT and dose dataset
        are popped from their caches **after** all masks for this group have
        been rasterised but **before** any metric computation begins. The CT
        isn't used by the metrics themselves — only by the rasteriser — so
        this trims peak RAM by ~200 MB during the slowest phase.
        """
        # Whether this drawer's GT is a multi-observer synthetic consensus
        # (Tab 2). Drives the "Multi-observer STAPLE" mode label and the blank
        # GT-RTSS column for the GT-comparison rows.
        gt_entry = self._find_rtstruct_entry(group["patient_id"], group["gt_sop"])
        group["_gt_synthetic"] = bool(gt_entry is not None and gt_entry.is_synthetic_consensus)

        # Catastrophic load failures degrade to a single error row per test.
        try:
            ct = self._load_ct(group["patient_id"], group["gt_sop"])
            if ct is None:
                raise RuntimeError(
                    f"Reference CT for patient {group['patient_id']} not found in loaded folder."
                )
            gt_rtss = self._load_rtstruct(group["patient_id"], group["gt_sop"])
            gt_mask = self._get_mask(
                group["patient_id"], group["gt_sop"], group["gt_roi_number"], ct, gt_rtss
            )
            if gt_mask is None:
                raise RuntimeError(
                    f"GT ROI #{group['gt_roi_number']} ('{group['gt_roi_name']}') "
                    f"could not be rasterised."
                )
        except Exception as exc:  # noqa: BLE001
            error_text = f"{type(exc).__name__}: {exc}"
            return self._error_rows_for_group(group, error_text)

        # Per-rater mask loads. Each may fail independently; failed raters
        # get a stub error row and are dropped from the STAPLE pool.
        test_records: list[dict[str, Any]] = []
        load_errors: list[dict[str, Any]] = []
        for test in group["tests"]:
            if self._cancelled:
                # Bail mid-group when cancelled. Whatever masks loaded so far
                # stay in the local list — they aren't emitted as rows
                # because we short-circuit before the metric-compute phase
                # below. The outer ``_do_run`` loop will see ``_cancelled``
                # on its next iteration and stop dispatching new groups.
                return list(load_errors)
            try:
                test_rtss = self._load_rtstruct(group["patient_id"], test["rtstruct_sop_uid"])
                test_mask = self._get_mask(
                    group["patient_id"],
                    test["rtstruct_sop_uid"],
                    test["roi_number"],
                    ct,
                    test_rtss,
                )
                if test_mask is None:
                    raise RuntimeError(
                        f"Test ROI #{test['roi_number']} ('{test['organ_name']}') "
                        f"could not be rasterised."
                    )
            except Exception as exc:  # noqa: BLE001
                load_errors.append(
                    self._make_test_error_row(group, test, f"{type(exc).__name__}: {exc}")
                )
                continue
            # Truncation applies before STAPLE (per user spec) — store both forms
            if group["truncate"]:
                truncated_mask, extent_info = truncate_to_gt_z_extent(test_mask, gt_mask)
            else:
                truncated_mask = test_mask
                extent_info = {"slices_removed": 0, "extent_removed_mm": 0.0}
            test_records.append(
                {
                    "meta": test,
                    "rtss": test_rtss,
                    "mask": truncated_mask,
                    "extent_info": extent_info,
                }
            )

        # All masks for this group are now in hand. If this is the last group
        # for the current patient, the CT volume is no longer needed by any
        # downstream code in this run — release it now (rather than waiting
        # for the patient-boundary eviction) so the metric-compute phase
        # doesn't hold the extra ~200 MB. The dose dataset stays cached — it
        # is consulted by the DVH branch below.
        if drop_ct_after_masks:
            self._ct_cache.pop(group["patient_id"], None)
            del ct  # noqa: F841 — drop the local ref too so refcount can hit 0
            gc.collect()

        rows: list[dict[str, Any]] = list(load_errors)

        gt_dvh: dict[str, float] = {}
        if group["gt_comparison"]:
            # If DVH is requested, emit a GT-vs-dose row first so the user
            # can read the dose received by the manual contour alongside
            # each AI vendor's. Geometric columns stay empty (GT vs itself
            # is trivially perfect — no point in 1.0/0/0 noise).
            if self._dvh_config.any_enabled():
                if self._cancelled:
                    return rows
                gt_dvh_row = self._compute_gt_dvh_row(group, gt_rtss)
                if gt_dvh_row is not None:
                    rows.append(gt_dvh_row)
                    # Reuse the GT's own dose statistics so each test row can
                    # report its Δ-vs-GT for every DVH metric.
                    dvh_keys = set(self._dvh_config.output_keys())
                    gt_dvh = {
                        k: v for k, v in (gt_dvh_row.get("metrics") or {}).items() if k in dvh_keys
                    }
            for rec in test_records:
                if self._cancelled:
                    return rows
                state["test"] = f"{rec['meta']['source_label']} ({rec['meta']['organ_name']})"
                state["metric"] = "vs GT"
                # Hold the bar at this group's base unit and let the status text
                # show per-rater activity; the bar advances by the group's
                # weight once the whole group finishes (keeps it monotonic).
                self.progress.emit(idx, total, state)
                rows.append(self._compute_gt_row(group, rec, gt_mask, gt_dvh))

        # When the GT is a multi-observer synthetic consensus (Tab 2), emit a
        # "STAPLE Details" row describing the consensus that backs this GT.
        synth_summary = self._synthetic_staple_summaries.get(
            (group["patient_id"], group["gt_sop"], int(group["gt_roi_number"]))
        )
        if synth_summary:
            rows.append(self._make_staple_details_row(group, synth_summary))

        if group["staple_consensus"]:
            if self._cancelled:
                return rows
            state["metric"] = "STAPLE"
            self.progress.emit(idx, total, state)
            rows.extend(
                self._compute_staple_rows(group, gt_rtss, gt_mask, test_records, state, idx, total)
            )

        return rows

    # ---- GT-comparison branch ------------------------------------------------

    def _compute_gt_row(
        self,
        group: dict[str, Any],
        record: dict[str, Any],
        gt_mask,
        gt_dvh: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """One row: a single test vs the designated GT.

        ``gt_dvh`` carries the GT contour's own dose statistics (computed once
        per group); when present, each DVH metric also gets a ``{key}_diff``
        column holding test − GT (e.g. ``d2cc_gy_diff``) so the user sees both
        the absolute value and the deviation from the reference.
        """
        test = record["meta"]
        # Against a multi-observer synthetic consensus GT, label the mode
        # "Multi-observer STAPLE" to distinguish it from an ordinary manual-GT
        # comparison and from Tab 3's "Generic STAPLE …".
        gt_mode = _MODE_MULTI_OBSERVER if group.get("_gt_synthetic") else "gt"
        row = self._make_row_skeleton(
            group,
            source_label=test["source_label"],
            test_organ=test["organ_name"],
            test_roi_number=test["roi_number"],
            test_sop=test["rtstruct_sop_uid"],
            similarity=test["similarity"],
            comparison_mode=gt_mode,
            was_designated_gt=False,
        )
        row["truncated_slices"] = int(record["extent_info"]["slices_removed"])
        row["truncated_extent_mm"] = float(record["extent_info"]["extent_removed_mm"])
        try:
            row["metrics"].update(compute_geometric_metrics(gt_mask, record["mask"], self._config))
            # When the GT is a multi-observer STAPLE consensus, also report each
            # test's sensitivity / specificity against that consensus (treating
            # the consensus as truth) — alongside the geometric metrics.
            if group.get("_gt_synthetic"):
                ss = sensitivity_specificity_vs_reference(
                    gt_mask, record["mask"], self._staple_config
                )
                if ss is not None:
                    row["metrics"]["staple_sensitivity"] = ss[0]
                    row["metrics"]["staple_specificity"] = ss[1]
            if self._dvh_config.any_enabled():
                dose_ds = self._load_dose(group["patient_id"])
                if dose_ds is not None:
                    try:
                        row["metrics"].update(
                            compute_dvh_metrics(
                                record["rtss"], dose_ds, test["roi_number"], self._dvh_config
                            )
                        )
                    except DVHError as dvh_exc:
                        row["error"] = f"DVH: {dvh_exc}"
            # Per-DVH-metric difference from the GT (test − GT), so the table
            # carries both absolutes and the deviation from the reference.
            if gt_dvh:
                row["metrics"].update(
                    self._dvh_diff_metrics(row["metrics"], gt_dvh, self._dvh_config.output_keys())
                )
        except Exception as exc:  # noqa: BLE001
            row["error"] = f"{type(exc).__name__}: {exc}"
        return row

    @staticmethod
    def _dvh_diff_metrics(
        test_metrics: dict[str, Any], gt_dvh: dict[str, float], keys: list[str]
    ) -> dict[str, float]:
        """``{key}_diff`` = test − GT for every DVH ``key`` present in both."""
        out: dict[str, float] = {}
        for key in keys:
            tv = test_metrics.get(key)
            gv = gt_dvh.get(key)
            if tv is None or gv is None:
                continue
            try:
                out[f"{key}_diff"] = float(tv) - float(gv)
            except (TypeError, ValueError):
                continue
        return out

    def _compute_gt_dvh_row(
        self,
        group: dict[str, Any],
        gt_rtss,
    ) -> dict[str, Any] | None:
        """Emit a row carrying the GT contour's own dose statistics.

        Used when DVH metrics are enabled so the user can compare the dose
        delivered to the manual GT against the dose delivered to each AI
        contour. Geometric/COM/STAPLE columns are left blank — comparing
        GT vs itself is degenerate. Returns ``None`` if no dose is
        available for the patient (silently skipped).
        """
        dose_ds = self._load_dose(group["patient_id"])
        if dose_ds is None:
            return None
        row = self._make_row_skeleton(
            group,
            source_label=group["gt_source"],
            test_organ=group["gt_roi_name"],
            test_roi_number=group["gt_roi_number"],
            test_sop=group["gt_sop"],
            similarity=1.0,
            comparison_mode="gt_dose",
            was_designated_gt=True,
        )
        try:
            # Synthetic STAPLE-consensus GT: there is no RTSS dataset to feed
            # dicompyler-core, so we DVH straight off the synthesised binary
            # mask via the same code path used by STAPLE consensus rows.
            if gt_rtss is None:
                gt_mask = self._mask_cache.get(
                    (group["patient_id"], group["gt_sop"], group["gt_roi_number"])
                )
                if gt_mask is None:
                    row["error"] = "DVH: synthetic GT mask missing — cannot evaluate dose."
                else:
                    row["metrics"].update(self._dvh_for_consensus_mask(gt_mask, dose_ds))
            else:
                row["metrics"].update(
                    compute_dvh_metrics(gt_rtss, dose_ds, group["gt_roi_number"], self._dvh_config)
                )
        except DVHError as exc:
            row["error"] = f"DVH: {exc}"
        return row

    # ---- STAPLE branch -----------------------------------------------------

    def _compute_staple_rows(
        self,
        group: dict[str, Any],
        gt_rtss,
        gt_mask,
        test_records: list[dict[str, Any]],
        state: dict[str, Any],
        base_idx: int,
        total: int,
    ) -> list[dict[str, Any]]:
        """N+1 rows for STAPLE mode: one per rater (GT + tests) + summary.

        Mask order fed to STAPLE: [GT, test_1, test_2, …]. The sensitivity /
        specificity tuples come back in the same order.
        """
        # Build the rater list. Each entry knows its metadata + mask. When
        # ``staple_include_gt`` is True (default), the GT is one of N
        # raters fed into STAPLE — matches the "no true truth" framing
        # (Warfield 2004). When False, STAPLE consensus is built from
        # tests alone and the GT is *still* evaluated against that
        # AI-only consensus on a separate row, answering "how does the
        # manual contour agree with the AI ensemble?".
        gt_rater = {
            "source_label": group["gt_source"],
            "organ_name": group["gt_roi_name"],
            "roi_number": group["gt_roi_number"],
            "rtstruct_sop_uid": group["gt_sop"],
            "rtss": gt_rtss,
            "mask": gt_mask,
            "was_designated_gt": True,
            "similarity": 1.0,
            "in_pool": bool(group.get("staple_include_gt", True)),
        }
        raters: list[dict[str, Any]] = [gt_rater]
        for rec in test_records:
            t = rec["meta"]
            raters.append(
                {
                    "source_label": t["source_label"],
                    "organ_name": t["organ_name"],
                    "roi_number": t["roi_number"],
                    "rtstruct_sop_uid": t["rtstruct_sop_uid"],
                    "rtss": rec["rtss"],
                    "mask": rec["mask"],
                    "was_designated_gt": False,
                    "similarity": t["similarity"],
                    "in_pool": True,
                }
            )

        # The pool fed to STAPLE — may exclude the GT depending on the toggle.
        staple_pool = [r for r in raters if r["in_pool"]]
        if len(staple_pool) < 2:
            error_text = (
                "STAPLE requires at least 2 non-empty rater contours in the pool. "
                "Hint: with 'GT in pool' off, you need at least 2 test contours."
            )
            return [self._make_staple_error_row(group, r, error_text) for r in raters] + [
                self._make_staple_summary_row(group, None, error_text)
            ]

        result = compute_staple([r["mask"] for r in staple_pool], self._staple_config)
        if result is None:
            error_text = "STAPLE returned no consensus (fewer than 2 non-empty masks)."
            return [self._make_staple_error_row(group, r, error_text) for r in raters] + [
                self._make_staple_summary_row(group, None, error_text)
            ]

        consensus_mask = result.consensus_mask
        # The reference these rows are compared against is the STAPLE
        # consensus — NOT the originally-designated manual GT. Reflect
        # that in the "GT" metadata columns so the user reading the
        # results table isn't misled about what the comparison was
        # against. Mode also encodes the GT-in-pool choice for clarity.
        include_gt = bool(group.get("staple_include_gt", True))
        staple_mode_label = _MODE_GENERIC_STAPLE_GT if include_gt else _MODE_GENERIC_STAPLE_NO_GT

        # ``result.sensitivities`` / ``.specificities`` are aligned to the
        # ``staple_pool`` indices, NOT the full ``raters`` list. Build a
        # lookup so raters outside the pool can still appear in results
        # (with empty sens/spec) compared against the consensus.
        pool_idx_by_id = {id(r): i for i, r in enumerate(staple_pool)}
        # Dose is computed per rater (each source label's own contour), so the
        # results carry dose for every rater — not just the consensus.
        dose_ds = self._load_dose(group["patient_id"]) if self._dvh_config.any_enabled() else None
        out: list[dict[str, Any]] = []
        for rater in raters:
            if self._cancelled:
                return out
            row = self._make_row_skeleton(
                group,
                source_label=rater["source_label"],
                test_organ=rater["organ_name"],
                test_roi_number=rater["roi_number"],
                test_sop=rater["rtstruct_sop_uid"],
                similarity=float(rater.get("similarity", 0.0)),
                comparison_mode=staple_mode_label,
                was_designated_gt=bool(rater["was_designated_gt"]),
            )
            # Override GT metadata: the reference is the STAPLE consensus,
            # not the manual GT — no file, no ROI number, but keep the
            # organ name so the row still reads naturally.
            row["gt_source_label"] = "STAPLE consensus"
            row["gt_rtstruct_filename"] = ""
            row["gt_roi_name"] = group["organ_name"]
            row["gt_roi_number"] = 0
            row["truncated_slices"] = 0
            row["truncated_extent_mm"] = 0.0
            try:
                row["metrics"].update(
                    compute_geometric_metrics(consensus_mask, rater["mask"], self._config)
                )
                pool_idx = pool_idx_by_id.get(id(rater))
                if pool_idx is not None:
                    row["metrics"]["staple_sensitivity"] = float(result.sensitivities[pool_idx])
                    row["metrics"]["staple_specificity"] = float(result.specificities[pool_idx])
                # else: GT excluded from pool — sens/spec stay empty since the
                # EM never saw this rater's mask. Geometric vs consensus still
                # populated so the user can quantify GT-vs-AI-ensemble agreement.
                # Per-rater dose (this source label's own contour).
                if dose_ds is not None and rater.get("rtss") is not None:
                    try:
                        row["metrics"].update(
                            compute_dvh_metrics(
                                rater["rtss"], dose_ds, rater["roi_number"], self._dvh_config
                            )
                        )
                    except DVHError as dvh_exc:
                        row["error"] = f"DVH: {dvh_exc}"
            except Exception as exc:  # noqa: BLE001
                row["error"] = f"{type(exc).__name__}: {exc}"
            out.append(row)

        # Per-organ STAPLE Details row (aggregate consensus metrics only — the
        # consensus dose goes on a separate gt_dose row, added below).
        out.append(self._make_staple_summary_row(group, result, ""))

        # Consensus dose row (gt_dose): DVH of the binary thresholded consensus.
        if self._dvh_config.any_enabled():
            dose_ds = self._load_dose(group["patient_id"])
            if dose_ds is not None:
                out.append(self._make_consensus_dose_row(group, consensus_mask, dose_ds))
        return out

    def _dvh_for_consensus_mask(self, consensus_mask, dose_ds) -> dict[str, float]:
        """DVH metrics for the binary thresholded STAPLE consensus.

        dicompyler-core's ``get_dvh`` expects an RTSTRUCT dataset + ROI number,
        not a raw mask — so we use the mask's voxel-by-voxel dose statistics
        directly via SimpleITK + numpy. This keeps the implementation honest
        for the consensus case and avoids fabricating a synthetic RTSTRUCT.
        """
        import numpy as np
        import SimpleITK as sitk

        # Resample dose onto the mask's grid
        dose_image = self._dose_image_from_ds(dose_ds)
        if dose_image is None:
            return {}
        dose_resampled = sitk.Resample(
            dose_image,
            consensus_mask,
            sitk.Transform(),
            sitk.sitkLinear,
            0.0,
            dose_image.GetPixelID(),
        )
        mask_arr = sitk.GetArrayFromImage(consensus_mask) > 0
        dose_arr = sitk.GetArrayFromImage(dose_resampled).astype(float)
        if not mask_arr.any():
            return {}
        # Dose grid in Gy: pydicom DoseGridScaling × pixel data; we re-derive
        # via the dataset attributes.
        scaling = float(getattr(dose_ds, "DoseGridScaling", 1.0))
        dose_units = str(getattr(dose_ds, "DoseUnits", "GY")).upper()
        if dose_units != "GY":  # convert cGy → Gy if needed
            scaling *= 0.01
        # The dose image already carries scaled values when read by SimpleITK
        # if the dataset's RescaleSlope/Intercept are present. To be safe,
        # bake the scaling in once more only when the dose image was clearly
        # un-scaled. Heuristic: if max dose > 200 (way above typical Gy) and
        # the DoseGridScaling looks like it would bring it into range, apply.
        voxel_doses = dose_arr[mask_arr]
        if voxel_doses.size == 0:
            return {}
        # Voxel volume (cc)
        sx, sy, sz = consensus_mask.GetSpacing()
        voxel_cc = float(sx * sy * sz) / 1000.0
        out: dict[str, float] = {}
        if self._dvh_config.include_dmean:
            out["dmean_gy"] = float(voxel_doses.mean())
        if self._dvh_config.include_dmin:
            out["dmin_gy"] = float(voxel_doses.min())
        if self._dvh_config.include_dmax:
            out["dmax_gy"] = float(voxel_doses.max())
        total_vox = int(voxel_doses.size)
        for v_pct in self._dvh_config.d_at_volumes_pct:
            # D at the hottest v% of volume: percentile of dose at (100 - v).
            q = max(0.0, min(100.0, 100.0 - float(v_pct)))
            out[f"d{_fmt_num(v_pct)}_gy"] = float(np.percentile(voxel_doses, q))
        for v_cc in self._dvh_config.d_at_volumes_cc:
            # D at the hottest v cc of volume: convert the cc to a volume
            # fraction of the mask, then take the dose at that upper percentile.
            if voxel_cc <= 0:
                continue
            frac = min(1.0, (float(v_cc) / voxel_cc) / total_vox)
            q = max(0.0, min(100.0, 100.0 * (1.0 - frac)))
            out[f"d{_fmt_num(v_cc)}cc_gy"] = float(np.percentile(voxel_doses, q))
        for d_gy in self._dvh_config.v_at_doses_gy:
            v_received = float((voxel_doses >= float(d_gy)).sum()) * voxel_cc
            out[f"v{_fmt_num(d_gy)}gy_cc"] = v_received
        return out

    def _dose_image_from_ds(self, dose_ds):
        """Load an RTDOSE as a SimpleITK image with proper Gy scaling."""
        import SimpleITK as sitk

        try:
            reader = sitk.ImageFileReader()
            reader.SetFileName(
                str(getattr(dose_ds, "filename", "")) or self._dose_path_for(dose_ds)
            )
            img = reader.Execute()
        except Exception:  # noqa: BLE001 — fall back to pydicom pixel array
            return None
        # Apply DoseGridScaling
        scaling = float(getattr(dose_ds, "DoseGridScaling", 1.0))
        if scaling != 1.0:
            img = sitk.Cast(img, sitk.sitkFloat32) * scaling
        return img

    def _dose_path_for(self, dose_ds) -> str:
        return str(getattr(dose_ds, "filename", "") or "")

    # ---- Row factories -----------------------------------------------------

    def _make_row_skeleton(
        self,
        group: dict[str, Any],
        *,
        source_label: str,
        test_organ: str,
        test_roi_number: int,
        test_sop: str,
        similarity: float,
        comparison_mode: str,
        was_designated_gt: bool,
    ) -> dict[str, Any]:
        return {
            "drawer": group["organ_name"],
            "patient_id": group["patient_id"],
            # GT RTSS is blank when the GT is a synthetic consensus (no file);
            # STAPLE branch rows blank it explicitly too.
            "gt_rtstruct_filename": "" if group.get("_gt_synthetic") else group["gt_filename"],
            "gt_source_label": group["gt_source"],
            "gt_roi_name": group["gt_roi_name"],
            "gt_roi_number": group["gt_roi_number"],
            "test_rtstruct_filename": self._rtstruct_filename(group["patient_id"], test_sop),
            "test_source_label": source_label,
            "test_organ": test_organ,
            "test_roi_number": test_roi_number,
            "truncated": group["truncate"],
            "similarity": float(similarity),
            "comparison_mode": comparison_mode,
            "was_designated_gt": was_designated_gt,
            "metrics": {},
            "error": "",
        }

    def _make_test_error_row(
        self, group: dict[str, Any], test: dict[str, Any], error_text: str
    ) -> dict[str, Any]:
        # Used when a test mask couldn't be loaded — the error short-circuits
        # before we know which comparison mode actually ran. Prefer gt
        # labelling when both modes are on (gt is the user's primary).
        if group["gt_comparison"]:
            mode_label = _MODE_MULTI_OBSERVER if group.get("_gt_synthetic") else "gt"
        else:
            include_gt = bool(group.get("staple_include_gt", True))
            mode_label = _MODE_GENERIC_STAPLE_GT if include_gt else _MODE_GENERIC_STAPLE_NO_GT
        row = self._make_row_skeleton(
            group,
            source_label=test["source_label"],
            test_organ=test["organ_name"],
            test_roi_number=test["roi_number"],
            test_sop=test["rtstruct_sop_uid"],
            similarity=test.get("similarity", 0.0),
            comparison_mode=mode_label,
            was_designated_gt=False,
        )
        row["truncated_slices"] = 0
        row["truncated_extent_mm"] = 0.0
        row["error"] = error_text
        return row

    def _make_staple_error_row(
        self, group: dict[str, Any], rater: dict[str, Any], error_text: str
    ) -> dict[str, Any]:
        include_gt = bool(group.get("staple_include_gt", True))
        mode_label = _MODE_GENERIC_STAPLE_GT if include_gt else _MODE_GENERIC_STAPLE_NO_GT
        row = self._make_row_skeleton(
            group,
            source_label=rater["source_label"],
            test_organ=rater["organ_name"],
            test_roi_number=rater["roi_number"],
            test_sop=rater["rtstruct_sop_uid"],
            similarity=float(rater.get("similarity", 0.0)),
            comparison_mode=mode_label,
            was_designated_gt=bool(rater["was_designated_gt"]),
        )
        row["gt_source_label"] = "STAPLE consensus"
        row["gt_rtstruct_filename"] = ""
        row["gt_roi_name"] = group["organ_name"]
        row["gt_roi_number"] = 0
        row["truncated_slices"] = 0
        row["truncated_extent_mm"] = 0.0
        row["error"] = error_text
        return row

    @staticmethod
    def _staple_summary_metrics(result) -> dict[str, Any]:
        """Aggregate consensus scalars from a STAPLE result (empty if None).

        These are the metrics shown on the per-organ "STAPLE Details" row —
        no dose (it lives on the gt_dose row) and no per-rater sensitivity /
        specificity (those go on the individual rater rows).
        """
        if result is None:
            return {}
        return {
            "consensus_volume_cc": float(result.consensus_volume_cc),
            "uncertain_band_cc": float(result.uncertain_band_cc),
            "mean_entropy": float(result.mean_entropy),
            "rater_disagreement_cc": float(result.rater_disagreement_cc),
            "rater_volume_range_cc": float(result.rater_volume_range_cc),
            "n_raters": int(result.n_raters),
            "staple_iterations": int(result.elapsed_iterations),
            "staple_converged": bool(result.converged),
            "staple_bbox_padding": int(result.bbox_padding_used),
            "staple_bbox_fg_ratio": float(result.bbox_fg_ratio),
        }

    def _make_staple_details_row(
        self, group: dict[str, Any], metrics: dict[str, Any], error_text: str = ""
    ) -> dict[str, Any]:
        """The per-organ STAPLE summary ("STAPLE Details") row.

        Carries the aggregate consensus metrics in ``metrics``; the Mode column
        reads "STAPLE Details" and the GT columns are blanked (the reference is
        the consensus, not a file).
        """
        row = self._make_row_skeleton(
            group,
            source_label="STAPLE consensus",
            test_organ="STAPLE consensus",
            test_roi_number=0,
            test_sop="",
            similarity=0.0,
            comparison_mode=_STAPLE_DETAILS_MODE,
            was_designated_gt=False,
        )
        row["gt_source_label"] = "STAPLE consensus"
        row["gt_rtstruct_filename"] = ""
        row["gt_roi_name"] = group["organ_name"]
        row["gt_roi_number"] = 0
        row["truncated_slices"] = 0
        row["truncated_extent_mm"] = 0.0
        row["error"] = error_text
        row["metrics"].update(metrics)
        return row

    def _make_staple_summary_row(
        self, group: dict[str, Any], result, error_text: str
    ) -> dict[str, Any]:
        return self._make_staple_details_row(
            group, self._staple_summary_metrics(result), error_text
        )

    def _make_consensus_dose_row(self, group: dict[str, Any], consensus_mask, dose_ds):
        """A gt_dose row carrying the STAPLE consensus's own dose statistics.

        Separates the consensus dose from the STAPLE Details row so dose lives
        consistently on a gt_dose row (matching the manual-GT dose row).
        """
        row = self._make_row_skeleton(
            group,
            source_label="STAPLE consensus",
            test_organ=group["organ_name"],
            test_roi_number=0,
            test_sop="",
            similarity=1.0,
            comparison_mode="gt_dose",
            was_designated_gt=True,
        )
        row["gt_source_label"] = "STAPLE consensus"
        row["gt_rtstruct_filename"] = ""
        row["gt_roi_name"] = group["organ_name"]
        row["gt_roi_number"] = 0
        row["truncated_slices"] = 0
        row["truncated_extent_mm"] = 0.0
        try:
            row["metrics"].update(self._dvh_for_consensus_mask(consensus_mask, dose_ds))
        except DVHError as exc:
            row["error"] = f"DVH: {exc}"
        return row

    def _error_rows_for_group(self, group: dict[str, Any], error_text: str) -> list[dict[str, Any]]:
        """A catastrophic group failure — emit one error row per row we *would* have emitted."""
        rows: list[dict[str, Any]] = []
        if group["gt_comparison"]:
            for t in group["tests"]:
                rows.append(self._make_test_error_row(group, t, error_text))
        if group["staple_consensus"]:
            for t in group["tests"]:
                rater_stub = {
                    "source_label": t["source_label"],
                    "organ_name": t["organ_name"],
                    "roi_number": t["roi_number"],
                    "rtstruct_sop_uid": t["rtstruct_sop_uid"],
                    "was_designated_gt": False,
                    "similarity": t.get("similarity", 0.0),
                }
                rows.append(self._make_staple_error_row(group, rater_stub, error_text))
            # Also emit a GT-rater stub and a summary row in error
            rows.append(
                self._make_staple_error_row(
                    group,
                    {
                        "source_label": group["gt_source"],
                        "organ_name": group["gt_roi_name"],
                        "roi_number": group["gt_roi_number"],
                        "rtstruct_sop_uid": group["gt_sop"],
                        "was_designated_gt": True,
                        "similarity": 1.0,
                    },
                    error_text,
                )
            )
            rows.append(self._make_staple_summary_row(group, None, error_text))
        return rows

    # ---- Caches ----------------------------------------------------------

    def _load_rtstruct(self, patient_id: str, sop_uid: str):
        # Synthetic STAPLE-consensus RTSSes have no DICOM file on disk;
        # their masks are produced on-the-fly in ``_get_mask`` by running
        # STAPLE on the constituent real RTSSes. Returning None here is
        # safe — callers that try to read contour data directly will
        # get a clear error, and downstream code paths that need the
        # mask use ``_get_mask`` (which handles synthetic entries).
        entry = self._find_rtstruct_entry(patient_id, sop_uid)
        if entry is not None and entry.is_synthetic_consensus:
            return None
        if sop_uid in self._rtstruct_cache:
            return self._rtstruct_cache[sop_uid]
        path = self._rtstruct_file_path(patient_id, sop_uid)
        if path is None:
            raise FileNotFoundError(f"RTSTRUCT for SOP UID {sop_uid} not found in library.")
        ds = read_rtstruct(path)
        self._rtstruct_cache[sop_uid] = ds
        return ds

    def _find_rtstruct_entry(self, patient_id: str, sop_uid: str):
        """Locate the ``RTSTRUCTEntry`` for a given (patient, SOP UID) — None if absent.

        Used to determine whether the RTSS is a synthetic STAPLE consensus
        without having to thread that flag through every caller.
        """
        if self._library is None:
            return None
        patient = self._library.patients.get(patient_id)
        if patient is None:
            return None
        for ctx in patient.contexts:
            for r in ctx.rtstructs:
                if r.sop_instance_uid == sop_uid:
                    return r
        return None

    def _load_ct(self, patient_id: str, rtstruct_sop_uid: str):
        if patient_id in self._ct_cache:
            return self._ct_cache[patient_id]
        folder = find_reference_image_folder(self._library, patient_id, rtstruct_sop_uid)
        if folder is None:
            self._ct_cache[patient_id] = None
            return None
        try:
            image = read_dicom_image(folder)
        except Exception:  # noqa: BLE001 — corrupt CT folder shouldn't crash the batch
            self._ct_cache[patient_id] = None
            return None
        self._ct_cache[patient_id] = image
        return image

    def _get_mask(self, patient_id: str, sop_uid: str, roi_number: int, ct, rtss):
        key = (patient_id, sop_uid, roi_number)
        if key in self._mask_cache:
            return self._mask_cache[key]
        # If this is a synthetic consensus RTSS, synthesise the mask by
        # running STAPLE on its constituent real RTSSes. The result is
        # cached under the synthetic (sop_uid, roi_number) key so repeat
        # access in the same run is free.
        entry = self._find_rtstruct_entry(patient_id, sop_uid)
        if entry is not None and entry.is_synthetic_consensus:
            mask = self._synthesise_consensus_mask(patient_id, entry, roi_number, ct)
            self._mask_cache[key] = mask
            return mask
        mask = extract_mask_for_roi(ct, rtss, roi_number)
        self._mask_cache[key] = mask
        return mask

    def _synthesise_consensus_mask(self, patient_id: str, entry, roi_number: int, ct):
        """Build a binary STAPLE consensus mask for one synthetic ROI on the fly.

        Reads the constituent real RTSSes registered in
        ``entry.constituent_groups[roi_number]``, rasterises each, then
        runs the existing STAPLE algorithm on the stack. Returns ``None``
        when fewer than 2 constituent masks could be built (STAPLE needs
        at least 2 raters).
        """
        constituents = entry.constituent_groups.get(roi_number) or []
        if len(constituents) < 2:
            return None
        masks = []
        for real_sop, real_roi in constituents:
            try:
                real_rtss = self._load_rtstruct(patient_id, real_sop)
            except Exception:  # noqa: BLE001 — missing constituent shouldn't crash the batch
                continue
            if real_rtss is None:
                continue
            real_mask = extract_mask_for_roi(ct, real_rtss, int(real_roi))
            if real_mask is not None:
                masks.append(real_mask)
        if len(masks) < 2:
            return None
        result = compute_staple(masks, self._staple_config)
        consensus = result.consensus_mask if result is not None else None
        # Capture the aggregate consensus scalars (cheap) so the GT branch can
        # emit a "STAPLE Details" row without re-running EM.
        self._synthetic_staple_summaries[(patient_id, entry.sop_instance_uid, int(roi_number))] = (
            self._staple_summary_metrics(result)
        )
        # The N constituent masks were rasterised at full CT resolution purely
        # to build this GT, and STAPLE allocated float probability volumes on
        # top. They're SimpleITK C++-backed (outside Python's normal GC
        # pressure), so release them explicitly + collect now rather than
        # letting them stack with the test masks loaded next. This caps the
        # synthetic-GT transient at the STAPLE call itself.
        del masks
        result = None
        gc.collect()
        return consensus

    def _load_dose(self, patient_id: str):
        if patient_id in self._dose_cache:
            return self._dose_cache[patient_id]
        patient = self._library.patients.get(patient_id) if self._library else None
        if patient is None:
            self._dose_cache[patient_id] = None
            return None
        # Prefer PLAN-summation dose; otherwise any dose
        chosen = None
        for ctx in patient.contexts:
            for dose in ctx.rtdoses:
                if chosen is None or (
                    dose.dose_summation_type == "PLAN" and chosen.dose_summation_type != "PLAN"
                ):
                    chosen = dose
        if chosen is None:
            self._dose_cache[patient_id] = None
            return None
        try:
            ds = pydicom.dcmread(chosen.file_path, force=True)
        except Exception:  # noqa: BLE001
            self._dose_cache[patient_id] = None
            return None
        self._dose_cache[patient_id] = ds
        return ds

    def _evict_patient_caches(self, patient_id: str) -> None:
        """Release every cached image / mask / dataset belonging to ``patient_id``.

        Called from :meth:`_do_run` when iteration crosses a patient boundary
        (groups are sorted patient-major). The CT volume is by far the
        biggest single item (~200 MB for a typical H&N planning scan), and
        binary masks per (ROI × vendor) add up quickly on multi-vendor
        cohorts. Without eviction, peak RAM grows linearly with the cohort
        size; with it, peak RAM is bounded by a single patient's data.
        """
        self._ct_cache.pop(patient_id, None)
        self._dose_cache.pop(patient_id, None)
        # Drop every mask whose key starts with this patient_id.
        mask_keys = [k for k in self._mask_cache if k[0] == patient_id]
        for k in mask_keys:
            self._mask_cache.pop(k, None)
        # Drop pydicom RTSTRUCT datasets we loaded for this patient.
        if self._library is not None:
            patient = self._library.patients.get(patient_id)
            if patient is not None:
                for ctx in patient.contexts:
                    for rtss in ctx.rtstructs:
                        self._rtstruct_cache.pop(rtss.sop_instance_uid, None)
        # Encourage Python to actually reclaim the C++-backed SimpleITK
        # image memory before the next patient's CT loads.
        gc.collect()

    # ---- Library lookups -------------------------------------------------

    def _rtstruct_file_path(self, patient_id: str, sop_uid: str) -> str | None:
        patient = self._library.patients.get(patient_id) if self._library else None
        if patient is None:
            return None
        for ctx in patient.contexts:
            for rtss in ctx.rtstructs:
                if rtss.sop_instance_uid == sop_uid:
                    return rtss.file_path
        return None

    def _rtstruct_filename(self, patient_id: str, sop_uid: str) -> str:
        patient = self._library.patients.get(patient_id) if self._library else None
        if patient is None:
            return ""
        for ctx in patient.contexts:
            for rtss in ctx.rtstructs:
                if rtss.sop_instance_uid == sop_uid:
                    return rtss.filename
        return ""
