"""DICOM metadata library with FrameOfReferenceUID-based linking.

The metadata layer groups loaded DICOM files by *imaging context* — a set of
files sharing one ``FrameOfReferenceUID``. This is the DICOM-correct way to
link RTSTRUCT and RTDOSE files to their reference CT, and is robust to AI
vendors that issue their generated structures inside a fresh
``StudyInstanceUID`` while preserving the FoR.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import pydicom
from pydicom.errors import InvalidDicomError

from autoseg_evaluator.core.source_labels import (
    ORIGIN_FILENAME,
    SourceLabel,
    derive_source_label,
)


# ---- Dataclasses ----------------------------------------------------------


@dataclass(frozen=True)
class OrganEntry:
    roi_number: int
    roi_name: str


@dataclass
class ImageSeriesEntry:
    """A single CT/MR/PT series. ``files`` are full paths in original DICOM order."""

    series_instance_uid: str
    modality: str
    frame_of_reference_uid: str
    files: list[str] = field(default_factory=list)

    @property
    def folder(self) -> str:
        return os.path.dirname(self.files[0]) if self.files else ""


@dataclass
class RTSTRUCTEntry:
    sop_instance_uid: str
    file_path: str
    manufacturer: str  # raw DICOM tag, may be empty
    source_label: str  # derived via cascade (or custom override)
    source_origin: str  # where the label came from
    frame_of_reference_uid: str
    study_instance_uid: str
    organs: list[OrganEntry] = field(default_factory=list)

    # ---- Synthetic STAPLE-consensus support ---------------------------------
    # When True, this entry has no real DICOM file backing it. The masks are
    # produced at compute-time by running STAPLE over ``constituent_groups``
    # (one group per ROI in ``organs``). Each group lists the (SOPInstanceUID,
    # ROINumber) pairs from REAL RTSSes that contribute to that synthetic ROI.
    # ``file_path`` is empty, ``source_label`` is set to "STAPLE Consensus",
    # and downstream code that reads contour data must respect this flag.
    is_synthetic_consensus: bool = False
    constituent_groups: dict[int, list[tuple[str, int]]] = field(default_factory=dict)
    """``{synthetic_roi_number: [(real_sop_uid, real_roi_number), …]}``"""

    @property
    def filename(self) -> str:
        if self.is_synthetic_consensus:
            return "(STAPLE consensus)"
        return os.path.basename(self.file_path)


@dataclass
class RTDOSEEntry:
    sop_instance_uid: str
    file_path: str
    frame_of_reference_uid: str
    study_instance_uid: str
    dose_summation_type: str  # "PLAN", "BEAM", "FRACTION", "MULTI_PLAN", or ""

    @property
    def filename(self) -> str:
        return os.path.basename(self.file_path)


@dataclass
class ImagingContext:
    """A group of files sharing one FrameOfReferenceUID — one anatomical context."""

    frame_of_reference_uid: str
    study_instance_uids: set[str] = field(default_factory=set)
    study_dates: set[str] = field(default_factory=set)  # YYYYMMDD strings
    image_series: list[ImageSeriesEntry] = field(default_factory=list)
    rtstructs: list[RTSTRUCTEntry] = field(default_factory=list)
    rtdoses: list[RTDOSEEntry] = field(default_factory=list)

    def display_date(self) -> str:
        """Human-readable date range (or empty)."""
        if not self.study_dates:
            return ""
        dates = sorted(d for d in self.study_dates if d)
        if not dates:
            return ""
        formatted = [_format_dicom_date(d) for d in dates]
        if len(formatted) == 1:
            return formatted[0]
        return f"{formatted[0]} … {formatted[-1]}"

    def organ_count(self) -> int:
        return sum(len(r.organs) for r in self.rtstructs)

    def has_reference_image(self) -> bool:
        return bool(self.image_series)


@dataclass
class PatientEntry:
    patient_id: str
    contexts: list[ImagingContext] = field(default_factory=list)
    merged_aliases: set[str] = field(default_factory=set)
    """PatientIDs absorbed into this entry by anonymisation-alias merging."""

    def total_rtstructs(self) -> int:
        return sum(len(c.rtstructs) for c in self.contexts)

    def total_organs(self) -> int:
        return sum(c.organ_count() for c in self.contexts)

    def total_image_files(self) -> int:
        return sum(len(s.files) for c in self.contexts for s in c.image_series)

    def has_image_series(self) -> bool:
        return any(c.image_series for c in self.contexts)


@dataclass
class ScanIssue:
    severity: str  # "warning" or "error"
    message: str


# ---- Library --------------------------------------------------------------


_IMAGE_MODALITIES = {"CT", "MR", "PT"}


class MetadataLibrary:
    """In-memory catalogue of loaded DICOM data, grouped by Patient → ImagingContext."""

    def __init__(self) -> None:
        self.patients: dict[str, PatientEntry] = {}
        self.root_folder: str = ""
        self.issues: list[ScanIssue] = []
        self._total_files_scanned: int = 0
        self._total_files_failed: int = 0

    # ---- Scanning ---------------------------------------------------------

    def scan_folder(
        self,
        folder_path: str,
        *,
        progress_callback: Callable[[int, int, str], None] | None = None,
        custom_overrides: Mapping[str, str] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        """Walk ``folder_path`` recursively and ingest all DICOM files found.

        Parameters
        ----------
        progress_callback:
            Called as ``cb(current_index, total_files, current_path)`` for UI updates.
        custom_overrides:
            SOPInstanceUID → user-defined source label (overrides the cascade).
        cancel_check:
            Optional zero-arg callable; if it returns True the scan stops early.
        """
        self.patients.clear()
        self.issues.clear()
        self._total_files_scanned = 0
        self._total_files_failed = 0
        self.root_folder = folder_path

        all_files: list[str] = []
        for root, _dirs, files in os.walk(folder_path):
            for name in files:
                all_files.append(os.path.join(root, name))
        total = len(all_files)
        if total == 0:
            self.issues.append(ScanIssue("warning", "No files found in the selected folder."))
            return

        for idx, file_path in enumerate(all_files):
            if cancel_check is not None and cancel_check():
                return
            if progress_callback is not None:
                progress_callback(idx, total, file_path)
            self._ingest_file(file_path, custom_overrides=custom_overrides)
        if progress_callback is not None:
            progress_callback(total, total, "")

        # Merge anonymisation aliases BEFORE issue derivation — the merge
        # turns "orphan RTSTRUCT" warnings into resolved links.
        self._merge_anonymisation_aliases()
        self._derive_issues()

    def _ingest_file(self, file_path: str, custom_overrides: Mapping[str, str] | None) -> None:
        try:
            ds = pydicom.dcmread(file_path, stop_before_pixels=True, force=False)
        except (InvalidDicomError, OSError, AttributeError, Exception):
            self._total_files_failed += 1
            return

        modality = _get_str(ds, "Modality")
        patient_id = _get_str(ds, "PatientID") or "UnknownPatient"
        study_uid = _get_str(ds, "StudyInstanceUID")
        if not study_uid:
            self._total_files_failed += 1
            return
        study_date = _get_str(ds, "StudyDate")

        # Resolve FrameOfReferenceUID based on modality (RTSTRUCT stores it differently).
        for_uid = _resolve_frame_of_reference_uid(ds, modality)
        if not for_uid:
            self.issues.append(
                ScanIssue(
                    "warning",
                    f"File has no FrameOfReferenceUID and was skipped: {os.path.basename(file_path)}",
                )
            )
            self._total_files_failed += 1
            return

        patient = self.patients.setdefault(patient_id, PatientEntry(patient_id=patient_id))
        context = self._get_or_create_context(patient, for_uid)
        context.study_instance_uids.add(study_uid)
        if study_date:
            context.study_dates.add(study_date)

        if modality in _IMAGE_MODALITIES:
            self._add_image_file(context, ds, file_path, modality, for_uid)
        elif modality == "RTSTRUCT":
            self._add_rtstruct(context, ds, file_path, for_uid, study_uid, custom_overrides)
        elif modality == "RTDOSE":
            self._add_rtdose(context, ds, file_path, for_uid, study_uid)
        else:
            # Other modalities (RTPLAN, RTIMAGE, etc.) are ignored for now.
            pass

        self._total_files_scanned += 1

    @staticmethod
    def _get_or_create_context(patient: PatientEntry, for_uid: str) -> ImagingContext:
        for ctx in patient.contexts:
            if ctx.frame_of_reference_uid == for_uid:
                return ctx
        ctx = ImagingContext(frame_of_reference_uid=for_uid)
        patient.contexts.append(ctx)
        return ctx

    @staticmethod
    def _add_image_file(
        context: ImagingContext,
        ds: Any,
        file_path: str,
        modality: str,
        for_uid: str,
    ) -> None:
        series_uid = _get_str(ds, "SeriesInstanceUID")
        if not series_uid:
            return
        for entry in context.image_series:
            if entry.series_instance_uid == series_uid:
                entry.files.append(file_path)
                return
        context.image_series.append(
            ImageSeriesEntry(
                series_instance_uid=series_uid,
                modality=modality,
                frame_of_reference_uid=for_uid,
                files=[file_path],
            )
        )

    @staticmethod
    def _add_rtstruct(
        context: ImagingContext,
        ds: Any,
        file_path: str,
        for_uid: str,
        study_uid: str,
        custom_overrides: Mapping[str, str] | None,
    ) -> None:
        sop_uid = _get_str(ds, "SOPInstanceUID")
        source = derive_source_label(ds, file_path, custom_overrides=custom_overrides)
        organs = _extract_organs(ds)
        context.rtstructs.append(
            RTSTRUCTEntry(
                sop_instance_uid=sop_uid,
                file_path=file_path,
                manufacturer=_get_str(ds, "Manufacturer"),
                source_label=source.label,
                source_origin=source.origin,
                frame_of_reference_uid=for_uid,
                study_instance_uid=study_uid,
                organs=organs,
            )
        )

    @staticmethod
    def _add_rtdose(
        context: ImagingContext,
        ds: Any,
        file_path: str,
        for_uid: str,
        study_uid: str,
    ) -> None:
        sop_uid = _get_str(ds, "SOPInstanceUID")
        context.rtdoses.append(
            RTDOSEEntry(
                sop_instance_uid=sop_uid,
                file_path=file_path,
                frame_of_reference_uid=for_uid,
                study_instance_uid=study_uid,
                dose_summation_type=_get_str(ds, "DoseSummationType"),
            )
        )

    # ---- Anonymisation-alias merge ---------------------------------------

    def _merge_anonymisation_aliases(self) -> None:
        """Merge PatientEntries that share a FrameOfReferenceUID.

        Some vendors anonymise PatientID/PatientName on export but preserve
        the FoR UID (and StudyInstanceUID). This presents as one real patient
        plus one or more "alias" patients in the scan output. Since FoR UIDs
        are globally unique, sharing one is a near-certain indicator that the
        files describe the same anatomy of the same person.

        Canonical PatientID is the one carrying the actual image series.
        Tie-breakers: more files, then alphabetically first.
        """
        if len(self.patients) <= 1:
            return

        # Build FoR UID → set of PatientIDs that contain it.
        for_to_patients: dict[str, set[str]] = {}
        for pid, patient in self.patients.items():
            for ctx in patient.contexts:
                for_to_patients.setdefault(ctx.frame_of_reference_uid, set()).add(pid)

        # Reduce overlapping FoR groups into transitive merge groups.
        merge_groups: list[set[str]] = []
        for pids in for_to_patients.values():
            if len(pids) < 2:
                continue
            self._union_into_groups(merge_groups, pids)

        for group in merge_groups:
            canonical = self._pick_canonical_patient(group)
            aliases = group - {canonical}
            for alias in aliases:
                self._absorb_patient(alias, canonical)
            self.patients[canonical].merged_aliases.update(aliases)
            self.issues.append(
                ScanIssue(
                    "warning",
                    f"Anonymisation mismatch: {', '.join(sorted(aliases))} "
                    f"shares FrameOfReferenceUID with '{canonical}'. "
                    f"Files merged into '{canonical}'.",
                )
            )

    @staticmethod
    def _union_into_groups(groups: list[set[str]], new_pids: set[str]) -> None:
        """Merge ``new_pids`` into ``groups``, joining any groups it overlaps."""
        overlapping = [g for g in groups if g & new_pids]
        if not overlapping:
            groups.append(set(new_pids))
            return
        merged = set(new_pids)
        for g in overlapping:
            merged |= g
            groups.remove(g)
        groups.append(merged)

    def _pick_canonical_patient(self, candidates: set[str]) -> str:
        """Choose the canonical PatientID for a merge group."""

        def sort_key(pid: str) -> tuple[bool, int, str]:
            p = self.patients[pid]
            # First priority: patient HAS image series  → False sorts before True,
            # so we negate ("has images" → False to sort first).
            return (not p.has_image_series(), -p.total_image_files(), pid)

        return min(candidates, key=sort_key)

    def _absorb_patient(self, alias_pid: str, canonical_pid: str) -> None:
        """Move all contexts from ``alias_pid`` into ``canonical_pid``."""
        alias = self.patients.pop(alias_pid)
        canonical = self.patients[canonical_pid]
        canonical.merged_aliases.update(alias.merged_aliases)
        for alias_ctx in alias.contexts:
            target = next(
                (
                    c
                    for c in canonical.contexts
                    if c.frame_of_reference_uid == alias_ctx.frame_of_reference_uid
                ),
                None,
            )
            if target is None:
                canonical.contexts.append(alias_ctx)
                continue
            target.study_instance_uids |= alias_ctx.study_instance_uids
            target.study_dates |= alias_ctx.study_dates
            target.image_series.extend(alias_ctx.image_series)
            target.rtstructs.extend(alias_ctx.rtstructs)
            target.rtdoses.extend(alias_ctx.rtdoses)

    # ---- Derived facts ----------------------------------------------------

    def _derive_issues(self) -> None:
        """Populate self.issues with cohort-level problems."""
        for patient in self.patients.values():
            for ctx in patient.contexts:
                if ctx.rtstructs and not ctx.has_reference_image():
                    self.issues.append(
                        ScanIssue(
                            "warning",
                            f"Patient {patient.patient_id}: RTSTRUCT(s) in context "
                            f"{_short_uid(ctx.frame_of_reference_uid)} have no matching CT/MR/PT "
                            f"image series in the loaded folder.",
                        )
                    )
                if ctx.rtdoses and not ctx.has_reference_image():
                    self.issues.append(
                        ScanIssue(
                            "warning",
                            f"Patient {patient.patient_id}: RTDOSE(s) in context "
                            f"{_short_uid(ctx.frame_of_reference_uid)} have no matching image series.",
                        )
                    )
            if not patient.contexts:
                self.issues.append(
                    ScanIssue("warning", f"Patient {patient.patient_id} has no usable contexts.")
                )

    # ---- Summary statistics ----------------------------------------------

    def summary(self) -> dict[str, Any]:
        n_patients = len(self.patients)
        n_contexts = sum(len(p.contexts) for p in self.patients.values())
        n_rtss = sum(p.total_rtstructs() for p in self.patients.values())
        n_organs = sum(p.total_organs() for p in self.patients.values())
        unique_organs = {
            o.roi_name.lower()
            for p in self.patients.values()
            for c in p.contexts
            for r in c.rtstructs
            for o in r.organs
        }
        n_rtdose = sum(len(c.rtdoses) for p in self.patients.values() for c in p.contexts)
        sources = {
            r.source_label
            for p in self.patients.values()
            for c in p.contexts
            for r in c.rtstructs
            if r.source_label
        }
        return {
            "patients": n_patients,
            "contexts": n_contexts,
            "rtstructs": n_rtss,
            "rtdoses": n_rtdose,
            "total_organs": n_organs,
            "unique_organs": len(unique_organs),
            "sources": sorted(sources),
            "files_scanned": self._total_files_scanned,
            "files_failed": self._total_files_failed,
        }

    def all_rtstructs(self) -> list[RTSTRUCTEntry]:
        return [r for p in self.patients.values() for c in p.contexts for r in c.rtstructs]

    # ---- Synthetic STAPLE-consensus RTSS support --------------------------

    def register_synthetic_consensus(
        self,
        patient_id: str,
        for_uid: str,
        entry: "RTSTRUCTEntry",
    ) -> bool:
        """Append a synthetic consensus RTSS to the patient's matching context.

        The synthetic entry must be marked ``is_synthetic_consensus=True``.
        Returns True on success, False if the patient or matching context
        couldn't be found.

        Re-registering a synthetic entry with a SOPInstanceUID that already
        exists in that context is treated as an update (replace in place).
        """
        if not entry.is_synthetic_consensus:
            return False
        patient = self.patients.get(patient_id)
        if patient is None:
            return False
        for ctx in patient.contexts:
            if ctx.frame_of_reference_uid != for_uid:
                continue
            for i, existing in enumerate(ctx.rtstructs):
                if existing.sop_instance_uid == entry.sop_instance_uid:
                    ctx.rtstructs[i] = entry
                    return True
            ctx.rtstructs.append(entry)
            return True
        return False

    def unregister_synthetic_consensus(self, sop_uid: str) -> bool:
        """Drop a synthetic consensus RTSS by SOPInstanceUID. Returns True if removed."""
        for patient in self.patients.values():
            for ctx in patient.contexts:
                for i, r in enumerate(ctx.rtstructs):
                    if r.is_synthetic_consensus and r.sop_instance_uid == sop_uid:
                        ctx.rtstructs.pop(i)
                        return True
        return False

    def clear_synthetic_consensus(self) -> None:
        """Remove every synthetic consensus RTSS — used before re-applying a new set."""
        for patient in self.patients.values():
            for ctx in patient.contexts:
                ctx.rtstructs = [r for r in ctx.rtstructs if not r.is_synthetic_consensus]

    def synthetic_consensus_entries(self) -> list[tuple[str, str, "RTSTRUCTEntry"]]:
        """Return ``(patient_id, for_uid, entry)`` for every synthetic RTSS in the library."""
        out: list[tuple[str, str, RTSTRUCTEntry]] = []
        for pid, patient in self.patients.items():
            for ctx in patient.contexts:
                for r in ctx.rtstructs:
                    if r.is_synthetic_consensus:
                        out.append((pid, ctx.frame_of_reference_uid, r))
        return out


# ---- Helpers --------------------------------------------------------------


def _get_str(obj: Any, attr: str) -> str:
    value = getattr(obj, attr, "") if obj is not None else ""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " ".join(str(v) for v in value if v is not None)
    return str(value).strip()


def _resolve_frame_of_reference_uid(ds: Any, modality: str) -> str:
    """Extract the FrameOfReferenceUID, accounting for modality-specific storage."""
    if modality == "RTSTRUCT":
        # RTSTRUCT stores FoR inside ReferencedFrameOfReferenceSequence
        seq = getattr(ds, "ReferencedFrameOfReferenceSequence", None)
        if seq:
            for item in seq:
                uid = _get_str(item, "FrameOfReferenceUID")
                if uid:
                    return uid
        # Some files (rare) also carry a top-level tag
    return _get_str(ds, "FrameOfReferenceUID")


def _extract_organs(ds: Any) -> list[OrganEntry]:
    seq = getattr(ds, "StructureSetROISequence", None)
    if not seq:
        return []
    organs: list[OrganEntry] = []
    for roi in seq:
        roi_name = _get_str(roi, "ROIName") or "UnknownOrgan"
        try:
            roi_number = int(getattr(roi, "ROINumber", 0))
        except (TypeError, ValueError):
            roi_number = 0
        organs.append(OrganEntry(roi_number=roi_number, roi_name=roi_name))
    return organs


def _format_dicom_date(yyyymmdd: str) -> str:
    """Format a DICOM date (YYYYMMDD) as YYYY-MM-DD; return input unchanged on failure."""
    if len(yyyymmdd) == 8 and yyyymmdd.isdigit():
        return f"{yyyymmdd[0:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
    return yyyymmdd


def _short_uid(uid: str) -> str:
    """Truncate a UID for display in issue messages."""
    if len(uid) <= 16:
        return uid
    return f"{uid[:8]}…{uid[-6:]}"


__all__ = [
    "OrganEntry",
    "ImageSeriesEntry",
    "RTSTRUCTEntry",
    "RTDOSEEntry",
    "ImagingContext",
    "PatientEntry",
    "ScanIssue",
    "MetadataLibrary",
    "ORIGIN_FILENAME",
    "SourceLabel",
]
