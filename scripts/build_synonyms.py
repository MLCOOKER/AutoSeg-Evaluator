"""Build the TG-263 synonym dictionary from the official nomenclature CSV.

Reads ``TG263_Nomenclature_Worksheet_20170815 5.csv`` (path given on the
command line) and emits four files into ``scripts/build/``:

* ``synonyms.json``                — proposed ``{canonical: [variants]}`` dict
* ``synonyms_audit.md``            — per-canonical breakdown for human review
* ``synonyms_conflicts.md``        — conflicts / skipped variants / anomalies
* ``tg263_resolution_sample.md``   — resolution of real ROI names found by
                                     scanning a sample-data folder

The build outputs are **not** committed automatically to
``src/autoseg_evaluator/resources/`` — they sit in ``scripts/build/`` so the
user can audit them before the final JSON is promoted.

Four expansion rules are applied (see brainstorm in the PR description):

* **Rule 0** — Keep only ``Target Type = Anatomic`` rows.
* **Rule 1** — Always accept the TG-263 Primary + Reverse-order names.
* **Rule 2** — Laterality expansion is applied **only** when the canonical
  is one half of a proven ``_L`` / ``_R`` pair (kills VB_L = "Lumbar" cases).
* **Rule 3** — Category-prefix collapse (``Bone_Mandible`` → also accepts
  ``Mandible``) is applied **only** when stripping creates no collision
  with another canonical's naked form.
* **Rule 4** — Bare-suffix laterality (``ParotidL`` / ``ParotidR``) is
  generated from Rule-2 paired stems only.

Run from the repository root::

    python scripts/build_synonyms.py \
        "C:/.../SAMPLE DATA/TG263_Nomenclature_Worksheet_20170815 5.csv" \
        --sample-data "C:/.../SAMPLE DATA"
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

try:
    import pydicom  # noqa: F401 — only needed for sample-data resolution

    _HAS_PYDICOM = True
except ImportError:
    _HAS_PYDICOM = False


# ---- Rule configuration --------------------------------------------------

ANATOMIC_TARGET_TYPE = "anatomic"

# Category prefixes for Rule 3. The trailing underscore is required.
CATEGORY_PREFIXES: tuple[str, ...] = (
    "Bone_",
    "Glnd_",
    "Musc_",
    "LN_",
    "Joint_",
    "Cartlg_",
    "Spc_",
    "Sinus_",
    "A_",
    "V_",
    "Lobe_",
    "CN_",
    "Lig_",
    "Proc_",
    "Cavity_",
    "Canal_",
    "BileDuct_",
    "Bowel_",
    "Valve_",
    "Tongue_",
    "VB_",
)


# ---- CSV ingest ----------------------------------------------------------


class CSVRow:
    """One TG-263 row, normalised for our purposes."""

    __slots__ = ("target_type", "major", "minor", "group", "primary", "reverse", "description")

    def __init__(self, raw: dict[str, str]) -> None:
        # The header row contains stray trailing spaces in some columns
        # (e.g. "Anatomic " with a trailing space). Strip keys + values to
        # normalise.
        clean = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        self.target_type = clean.get("Target Type", "").lower()
        self.major = clean.get("Major Category", "")
        self.minor = clean.get("Minor Category", "")
        self.group = clean.get("Anatomic Group", "")
        self.primary = clean.get("TG263-Primary Name", "")
        self.reverse = clean.get("TG-263-Reverse Order Name", "")
        self.description = clean.get("Description", "")

    def is_anatomic(self) -> bool:
        return self.target_type == ANATOMIC_TARGET_TYPE


def read_csv(path: Path) -> list[CSVRow]:
    """Read the TG-263 worksheet. utf-8-sig handles the BOM in the file."""
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [CSVRow(row) for row in reader if (row.get("TG263-Primary Name") or "").strip()]


# ---- Expansion engine ----------------------------------------------------


_LATERALITY_SUFFIX_RE = re.compile(r"_(L|R)$")


def _laterality_split(canonical: str) -> tuple[str, str] | None:
    """Return ``(stem, side)`` if ``canonical`` looks like ``Stem_L`` or ``Stem_R``."""
    m = _LATERALITY_SUFFIX_RE.search(canonical)
    if not m:
        return None
    return canonical[: m.start()], m.group(1)


def _category_strip(canonical: str) -> tuple[str, str] | None:
    """Return ``(prefix, naked)`` if ``canonical`` starts with a known category prefix."""
    for prefix in CATEGORY_PREFIXES:
        if canonical.startswith(prefix) and len(canonical) > len(prefix):
            return prefix, canonical[len(prefix) :]
    return None


# Words whose presence in a description disqualifies it as a synonym candidate.
# These signal aggregates, derivatives, sentences-with-fillers, or vague labels —
# i.e. anything that wouldn't be a sensible alias for a vendor's ROI name.
_DESC_FILLER_WORDS: frozenset[str] = frozenset(
    {
        "of",
        "by",
        "with",
        "for",
        "to",
        "on",
        "and",
        "or",
        "the",
        "a",
        "an",
        "in",
        "at",
        "as",
        "is",
    }
)
_DESC_BLOCKLIST: frozenset[str] = frozenset(
    {
        "set",
        "pair",
        "both",
        "non",
        "adjacent",
        "minus",
        "plus",
        "prv",
        "ptv",
        "ctv",
        "gtv",
        "itv",
        "expansion",
        "originally",
        "occupied",
        "whole",
        "excluding",
        "generated",
        "tissue",
        "structure",
        "evaluation",
        "boost",
        "region",
        "any",
        "all",
        "only",
        "external",
        "internal",  # too generic on their own
    }
)


def _clean_description(description: str) -> str | None:
    """Return the description verbatim if it passes the conservative filter.

    Filter rules:
    - 1 to 4 tokens after splitting on whitespace
    - No digits, parens, slashes, colons, commas, ampersands, equals, pipes
    - Every token must be alphabetic (hyphens allowed)
    - No filler word (``of``, ``the`` …) or blocklisted word (``set``, ``PRV`` …)
    - Doesn't contain both ``Left`` and ``Right``
    """
    desc = (description or "").strip()
    if not desc:
        return None
    if re.search(r"[(){}\[\]/:;,|&=\d]", desc):
        return None
    tokens = desc.split()
    if not (1 <= len(tokens) <= 4):
        return None
    for tok in tokens:
        if not tok.replace("-", "").isalpha():
            return None
    lowered = {t.lower() for t in tokens}
    if lowered & _DESC_FILLER_WORDS:
        return None
    if lowered & _DESC_BLOCKLIST:
        return None
    if "left" in lowered and "right" in lowered:
        return None
    return desc


def _description_to_variants(description: str) -> list[str]:
    """From a cleaned description, generate variants honouring trailing laterality.

    If the description ends in ``Left``/``Right``, the leading tokens form a
    "stem" that we run through :func:`_laterality_variants` to produce every
    underscore / space / no-separator spelling. Otherwise, the description
    is returned as-is.
    """
    tokens = description.split()
    if not tokens:
        return []
    last_lower = tokens[-1].lower()
    if last_lower in ("left", "right") and len(tokens) > 1:
        side = "L" if last_lower == "left" else "R"
        stem = " ".join(tokens[:-1])
        return _laterality_variants(stem, side)
    return [description]


def _laterality_variants(stem: str, side: str) -> list[str]:
    """Generate every reasonable spelling variant for a lateralised ``stem``.

    ``side`` is ``'L'`` or ``'R'``. Output preserves the original casing of
    ``stem``; the normaliser in :mod:`autoseg_evaluator.core.matching` is
    case-insensitive, so we don't bother lowercasing here.
    """
    if side == "L":
        words = ("L", "Lt", "LT", "Left", "LEFT")
    else:
        words = ("R", "Rt", "RT", "Right", "RIGHT")
    variants: list[str] = []
    for w in words:
        # Suffix forms: Stem_L, Stem_Lt, Stem_Left, etc.
        variants.append(f"{stem}_{w}")
        variants.append(f"{stem} {w}")
        # Prefix forms: L_Stem, Lt_Stem, Left_Stem, etc.
        variants.append(f"{w}_{stem}")
        variants.append(f"{w} {stem}")
        # No-separator forms (Rule 4): StemL, StemLeft, LeftStem
        variants.append(f"{stem}{w}")
        variants.append(f"{w}{stem}")
    return variants


def build_synonyms(
    rows: list[CSVRow],
) -> tuple[
    dict[str, list[str]],
    dict[str, dict[str, list[str]]],
    dict[str, list[str]],
]:
    """Apply Rules 0-4 and build the synonym dictionary.

    Returns
    -------
    synonyms
        ``{canonical: [variant, …]}`` mapping ready for serialisation.
    sources
        ``{canonical: {"primary": […], "reverse": […], "laterality": […],
                       "naked": […]}}`` — feeds the audit log.
    conflicts
        ``{kind: [message, …]}`` — feeds the conflicts log.
    """
    # Rule 0 — filter to anatomic rows
    anatomic = [r for r in rows if r.is_anatomic()]
    canonical_set = {r.primary for r in anatomic if r.primary}

    # Detect laterality pairs (Rule 2 gate)
    paired_stems: set[str] = set()
    for canonical in canonical_set:
        split = _laterality_split(canonical)
        if not split:
            continue
        stem, side = split
        twin = f"{stem}_R" if side == "L" else f"{stem}_L"
        if twin in canonical_set:
            paired_stems.add(stem)

    # Detect prefix-collapse conflicts (Rule 3 gate)
    # Build naked → [canonicals that would collapse to it]
    naked_owners: dict[str, list[str]] = defaultdict(list)
    for canonical in canonical_set:
        stripped = _category_strip(canonical)
        if stripped:
            _prefix, naked = stripped
            naked_owners[naked].append(canonical)

    sources: dict[str, dict[str, list[str]]] = {}
    conflicts: dict[str, list[str]] = defaultdict(list)
    variants_per_canonical: dict[str, set[str]] = defaultdict(set)

    # Pass 1 — Rule 1: Primary + Reverse for every anatomic row
    for r in anatomic:
        canonical = r.primary
        src = sources.setdefault(
            canonical,
            {
                "primary": [],
                "reverse": [],
                "laterality": [],
                "naked": [],
                "description": [],
                "group": [r.group],
            },
        )
        src["primary"].append(canonical)
        variants_per_canonical[canonical].add(canonical)
        if r.reverse and r.reverse != canonical:
            src["reverse"].append(r.reverse)
            variants_per_canonical[canonical].add(r.reverse)

    # Pass 2 — Rule 2: laterality expansion (only for paired stems)
    for canonical in list(variants_per_canonical.keys()):
        split = _laterality_split(canonical)
        if not split:
            continue
        stem, side = split
        if stem not in paired_stems:
            conflicts["laterality_skipped_unpaired"].append(
                f"{canonical}: no `{stem}_{'R' if side == 'L' else 'L'}` counterpart in TG-263 "
                "— laterality expansion skipped."
            )
            continue
        for v in _laterality_variants(stem, side):
            variants_per_canonical[canonical].add(v)
            sources[canonical]["laterality"].append(v)

    # Pass 3 — Rule 3: prefix-collapse naked variants (only when conflict-free)
    for canonical in list(variants_per_canonical.keys()):
        stripped = _category_strip(canonical)
        if not stripped:
            continue
        _prefix, naked = stripped
        owners = naked_owners.get(naked, [])
        if len(owners) > 1:
            conflicts["naked_conflict"].append(
                f"`{naked}` would collide between canonicals: {', '.join(sorted(owners))} "
                "— naked variant skipped for all of them."
            )
            continue
        # Single owner → safe to add naked form as a variant
        variants_per_canonical[canonical].add(naked)
        sources[canonical]["naked"].append(naked)
        # AND if the naked form ends in _L/_R and the stem is itself paired,
        # expand laterality on the naked form too (e.g. Glnd_Parotid_L → Parotid_L
        # → also Parotid_Left, L_Parotid, ParotidL, etc.)
        naked_split = _laterality_split(naked)
        if naked_split:
            stem, side = naked_split
            twin_naked = f"{stem}_R" if side == "L" else f"{stem}_L"
            # The "twin" of the naked form should also have a unique owner
            twin_owners = naked_owners.get(twin_naked, [])
            if len(twin_owners) == 1:
                for v in _laterality_variants(stem, side):
                    variants_per_canonical[canonical].add(v)
                    sources[canonical]["laterality"].append(v)

    # Pass 4 — description mining. Each anatomic row contributes its (filtered)
    # description as a variant of its canonical. Same description appearing for
    # multiple canonicals is resolved by the bilateral-wins rule below, or
    # skipped entirely as a true collision.
    proposed_desc: dict[str, list[tuple[str, str]]] = defaultdict(list)
    # ^ key: normalised variant; value: list of (canonical, raw_variant_text)
    for r in anatomic:
        canonical = r.primary
        cleaned = _clean_description(r.description)
        if not cleaned:
            continue
        for v in _description_to_variants(cleaned):
            proposed_desc[_normalise(v)].append((canonical, v))

    for _normalised, proposals in proposed_desc.items():
        # `proposals` can list the same canonical multiple times if its
        # laterality expansion produced several raw spellings that all
        # normalise to the same key (e.g. "Brain Stem_L" and "Brain Stem L"
        # both → "brainsteml"). True collisions require *distinct* canonicals.
        unique_canonicals = {c for c, _ in proposals}
        if len(unique_canonicals) == 1:
            canonical = next(iter(unique_canonicals))
            # Pick the first raw form for this canonical as the recorded variant
            raw = next(r for c, r in proposals if c == canonical)
            variants_per_canonical[canonical].add(raw)
            sources[canonical]["description"].append(raw)
            continue
        # Multiple distinct canonicals — try the bilateral-wins disambiguation.
        per_canonical_raw: dict[str, str] = {}
        for c, r in proposals:
            per_canonical_raw.setdefault(c, r)
        bilateral_canonical: str | None = None
        stems: set[str] = set()
        for c in unique_canonicals:
            split = _laterality_split(c)
            if split:
                stems.add(split[0])
            else:
                stems.add(c)
                if bilateral_canonical is None:
                    bilateral_canonical = c
        if len(stems) == 1 and bilateral_canonical is not None:
            # Same stem with an L/R/bilateral spread — give it to the bilateral
            raw = per_canonical_raw[bilateral_canonical]
            variants_per_canonical[bilateral_canonical].add(raw)
            sources[bilateral_canonical]["description"].append(raw)
            conflicts["description_collision_bilateral_won"].append(
                f"`{raw}` proposed by {', '.join(sorted(unique_canonicals))} — "
                f"assigned to bilateral `{bilateral_canonical}`."
            )
        else:
            conflicts["description_collision_skipped"].append(
                f"`{next(iter(per_canonical_raw.values()))}` proposed by "
                f"multiple canonicals: {', '.join(sorted(unique_canonicals))} "
                "— skipped for all."
            )

    # Pass 5 — propagate bilateral description variants to L/R siblings. A
    # canonical like `OpticNrv` has the description "Optic nerve"; its
    # paired siblings `OpticNrv_L`/`OpticNrv_R` get the lateralised forms of
    # that description ("OpticNerve_L", "L_OpticNerve", "OpticNerveLeft", …).
    for canonical in list(sources.keys()):
        descs = list(dict.fromkeys(sources[canonical]["description"]))  # de-dupe, preserve order
        if not descs:
            continue
        if _laterality_split(canonical):
            continue
        twin_l = f"{canonical}_L"
        twin_r = f"{canonical}_R"
        if twin_l not in sources or twin_r not in sources:
            continue
        for desc in descs:
            for side, twin in (("L", twin_l), ("R", twin_r)):
                for v in _laterality_variants(desc, side):
                    variants_per_canonical[twin].add(v)
                    sources[twin]["laterality"].append(v)

    # Materialise final synonyms dict (drop the canonical itself from variants;
    # the matcher always considers identity separately).
    synonyms: dict[str, list[str]] = {}
    for canonical, variant_set in variants_per_canonical.items():
        variants = sorted(v for v in variant_set if v != canonical)
        synonyms[canonical] = variants

    return synonyms, sources, dict(conflicts)


# ---- Audit writers -------------------------------------------------------


def write_synonyms_json(synonyms: dict[str, list[str]], out_path: Path) -> None:
    payload: dict[str, object] = {
        "_comment": (
            "TG-263-derived organ synonym dictionary. Generated by "
            "scripts/build_synonyms.py from the official TG-263 worksheet. "
            "Each key is the canonical primary name; values are recognised "
            "variants. Edit freely or override by placing a synonyms.json "
            "next to the .exe."
        )
    }
    for canonical in sorted(synonyms.keys()):
        payload[canonical] = synonyms[canonical]
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")


def write_audit(
    synonyms: dict[str, list[str]],
    sources: dict[str, dict[str, list[str]]],
    out_path: Path,
) -> None:
    # Group canonicals by anatomic group for easier human scanning.
    by_group: dict[str, list[str]] = defaultdict(list)
    for canonical, src in sources.items():
        group = (src.get("group") or ["Other"])[0] or "Other"
        by_group[group].append(canonical)

    lines: list[str] = []
    lines.append("# TG-263 Synonyms — Audit Log")
    lines.append("")
    lines.append(
        f"Generated from the TG-263 worksheet — **{len(synonyms)}** canonicals, "
        f"**{sum(len(v) for v in synonyms.values())}** variants total."
    )
    lines.append("")
    lines.append(
        "For each canonical, the bullets list which expansion rule produced "
        "each variant family. Use this to spot-check that nothing absurd was "
        "generated."
    )
    lines.append("")

    for group in sorted(by_group.keys()):
        lines.append(f"## {group}")
        lines.append("")
        for canonical in sorted(by_group[group]):
            src = sources[canonical]
            variants = synonyms[canonical]
            lines.append(f"### `{canonical}`")
            if src["reverse"]:
                lines.append(f"- **Reverse-order:** {', '.join(f'`{x}`' for x in src['reverse'])}")
            if src["naked"]:
                lines.append(
                    f"- **Category-collapse:** {', '.join(f'`{x}`' for x in src['naked'])}"
                )
            if src.get("description"):
                descs = list(dict.fromkeys(src["description"]))
                lines.append(f"- **From description:** {', '.join(f'`{x}`' for x in descs)}")
            if src["laterality"]:
                # Compress for readability — show first 8 and a count
                lat = src["laterality"]
                shown = ", ".join(f"`{x}`" for x in sorted(set(lat))[:8])
                if len(set(lat)) > 8:
                    shown += f", … ({len(set(lat))} total)"
                lines.append(f"- **Laterality expansion:** {shown}")
            if not variants:
                lines.append("- *(no variants — primary name only)*")
            lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def write_conflicts(conflicts: dict[str, list[str]], out_path: Path) -> None:
    lines: list[str] = []
    lines.append("# TG-263 Synonyms — Conflicts & Skipped Expansions")
    lines.append("")
    lines.append(
        "Cases where the generator deliberately refused to add a variant, plus "
        "data-quality anomalies that warrant a human look."
    )
    lines.append("")

    sections = (
        ("naked_conflict", "Rule 3 conflicts (naked variant skipped to avoid ambiguity)"),
        (
            "laterality_skipped_unpaired",
            "Rule 2 skipped — canonical ends in `_L`/`_R` but no twin in TG-263",
        ),
        (
            "description_collision_bilateral_won",
            "Description appears for multiple lateralised forms — assigned to the bilateral canonical",
        ),
        (
            "description_collision_skipped",
            "Description proposed by genuinely unrelated canonicals — skipped for all",
        ),
    )
    for key, heading in sections:
        # Same conflict often gets logged once per colliding canonical that
        # tripped it — collapse to unique entries for readability.
        items = sorted(set(conflicts.get(key, [])))
        lines.append(f"## {heading}")
        lines.append("")
        if not items:
            lines.append("*(none)*")
        else:
            for item in items:
                lines.append(f"- {item}")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---- Sample-data resolution ---------------------------------------------


def _flatten_for_lookup(synonyms: dict[str, list[str]]) -> dict[str, str]:
    """Mirror :func:`autoseg_evaluator.data.synonyms.flatten_synonyms`.

    The on-disk format is ``{canonical: [variants]}``. For matcher use we
    flatten to ``{normalised_variant: canonical}`` with the same
    lower+strip-``_-``+strip-whitespace normalisation the matcher applies.
    """
    flat: dict[str, str] = {}
    for canonical, variants in synonyms.items():
        canon_norm = _normalise(canonical)
        if canon_norm:
            flat[canon_norm] = canonical
        for variant in variants:
            v_norm = _normalise(variant)
            if v_norm:
                flat[v_norm] = canonical
    return flat


def _normalise(name: str) -> str:
    return (name or "").lower().replace(" ", "").replace("_", "").replace("-", "").strip()


def scan_sample_rois(sample_dir: Path) -> dict[str, list[tuple[str, str]]]:
    """Return ``{roi_name: [(patient_folder, rtss_filename), …]}`` for every
    unique ROI name found in RTSTRUCT files under ``sample_dir``.

    Returns an empty dict (with a stderr note) when pydicom is unavailable.
    """
    if not _HAS_PYDICOM:
        print(
            "scan_sample_rois: pydicom not importable, skipping real-data audit.",
            file=sys.stderr,
        )
        return {}
    import pydicom  # local import — only needed when actually scanning

    found: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for path in sample_dir.rglob("*.dcm"):
        # Cheap skip: filename starting with CT./MR./PT. won't be RTSTRUCT
        name = path.name
        if name.startswith(("CT.", "MR.", "PT.", "CT ")):
            continue
        try:
            ds = pydicom.dcmread(str(path), force=True, stop_before_pixels=True)
        except Exception:  # noqa: BLE001 — corrupt files skipped
            continue
        if getattr(ds, "Modality", "") != "RTSTRUCT":
            continue
        rois = getattr(ds, "StructureSetROISequence", [])
        for roi in rois:
            roi_name = str(getattr(roi, "ROIName", "")).strip()
            if roi_name:
                found[roi_name].append((path.parent.name, path.name))
    return found


def write_sample_resolution(
    rois: dict[str, list[tuple[str, str]]],
    synonyms: dict[str, list[str]],
    out_path: Path,
) -> None:
    flat = _flatten_for_lookup(synonyms)

    resolved: list[tuple[str, str, list[tuple[str, str]]]] = []
    unresolved: list[tuple[str, list[tuple[str, str]]]] = []
    for roi_name in sorted(rois.keys(), key=str.lower):
        normalised = _normalise(roi_name)
        canon = flat.get(normalised)
        if canon:
            resolved.append((roi_name, canon, rois[roi_name]))
        else:
            unresolved.append((roi_name, rois[roi_name]))

    lines: list[str] = []
    lines.append("# TG-263 Synonyms — Sample Data Resolution")
    lines.append("")
    lines.append(
        f"Scanned **{sum(len(v) for v in rois.values())}** ROI occurrences across "
        f"**{len(rois)}** unique names found in the SAMPLE DATA RTSTRUCTs."
    )
    lines.append("")
    coverage = (len(resolved) / len(rois) * 100) if rois else 0.0
    lines.append(
        f"- **Resolved to TG-263 canonical:** {len(resolved)} / {len(rois)} ({coverage:.1f}%)"
    )
    lines.append(f"- **Unresolved (fuzzy fallback):** {len(unresolved)} / {len(rois)}")
    lines.append("")
    lines.append("## Resolved")
    lines.append("")
    lines.append("| Raw ROI name | Canonical | Seen in |")
    lines.append("|---|---|---|")
    for raw, canon, where in resolved:
        sources = ", ".join(sorted({f"{folder}/{rtss}" for folder, rtss in where}))
        lines.append(f"| `{raw}` | **`{canon}`** | {sources} |")
    lines.append("")
    lines.append("## Unresolved (would fall through to fuzzy matching)")
    lines.append("")
    if not unresolved:
        lines.append("*(none — full coverage)*")
    else:
        lines.append("| Raw ROI name | Seen in |")
        lines.append("|---|---|")
        for raw, where in unresolved:
            sources = ", ".join(sorted({f"{folder}/{rtss}" for folder, rtss in where}))
            lines.append(f"| `{raw}` | {sources} |")
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---- CLI -----------------------------------------------------------------


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the TG-263 synonyms dictionary.")
    parser.add_argument("csv", type=Path, help="Path to the TG-263 nomenclature CSV.")
    parser.add_argument(
        "--sample-data",
        type=Path,
        default=None,
        help="Optional path to a SAMPLE DATA folder to scan for real-world ROI names.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "build",
        help="Directory to write build outputs into (default: scripts/build/).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.csv.exists():
        parser.error(f"CSV not found: {args.csv}")

    args.out.mkdir(parents=True, exist_ok=True)

    rows = read_csv(args.csv)
    synonyms, sources, conflicts = build_synonyms(rows)

    write_synonyms_json(synonyms, args.out / "synonyms.json")
    write_audit(synonyms, sources, args.out / "synonyms_audit.md")
    write_conflicts(conflicts, args.out / "synonyms_conflicts.md")

    if args.sample_data:
        rois = scan_sample_rois(args.sample_data)
        write_sample_resolution(rois, synonyms, args.out / "tg263_resolution_sample.md")

    n_variants = sum(len(v) for v in synonyms.values())
    n_conflicts = sum(len(v) for v in conflicts.values())
    print(f"Built {len(synonyms)} canonicals, {n_variants} variants.")
    print(f"{n_conflicts} conflicts/anomalies logged to synonyms_conflicts.md.")
    print(f"Outputs: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
