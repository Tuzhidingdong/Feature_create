"""Deterministic, auditable paper-level sampling for large preprocessed corpora."""
from __future__ import annotations

import csv
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from fractions import Fraction
from pathlib import Path
from typing import Any

SAMPLE_MANIFEST_VERSION = "0.2"
SAMPLE_ALGORITHM = "sha256_rank_within_strata_dhondt_v2"
ALLOWED_PREPROCESSING_STATUSES = {
    "COMPLETE", "WARNING_SCANNED_PAGES", "PARTIAL_SI_UNSUPPORTED", "PARTIAL_NO_SI"
}
REQUIRED_COLUMNS = {
    "doi", "title", "year", "journal", "article_type",
    "local_pdf_path", "local_si_info", "preprocessing_path", "preprocessing_status",
}


class SamplingError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_doi(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    for prefix in (
        "https://doi.org/", "http://doi.org/",
        "https://dx.doi.org/", "http://dx.doi.org/",
    ):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    raw = raw.rstrip(".,;)]}>")
    return raw or None


def _row_value(row: dict[str, str], key: str) -> str:
    for name, value in row.items():
        if str(name).strip().casefold() == key.casefold():
            return str(value or "").strip()
    return ""


def paper_identity_key(row: dict[str, str]) -> str:
    """Return the shared DOI/metadata identity used by sampling and preparation."""
    doi = _normalized_doi(_row_value(row, "doi"))
    if doi:
        return f"doi:{doi}"
    title = " ".join(_row_value(row, "title").casefold().split())
    year = _row_value(row, "year").strip()
    journal = " ".join(_row_value(row, "journal").casefold().split())
    if not title:
        raise SamplingError("A paper row has neither DOI nor title; stable paper identity cannot be established.")
    fallback = json.dumps([title, year, journal], ensure_ascii=False, separators=(",", ":"))
    return "metadata:" + hashlib.sha256(fallback.encode("utf-8")).hexdigest()


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise SamplingError("Input paired CSV has no header row.")
            fieldnames = [str(name).strip() for name in reader.fieldnames]
            if len({name.casefold() for name in fieldnames}) != len(fieldnames):
                raise SamplingError("Input paired CSV contains duplicate column names (case-insensitive).")
            missing = sorted(REQUIRED_COLUMNS - {name.casefold() for name in fieldnames})
            if missing:
                raise SamplingError("Input is not a preprocessed chem-literature handoff; missing columns: " + ", ".join(missing))
            rows = []
            for line_number, raw in enumerate(reader, start=2):
                if None in raw and raw[None]:
                    raise SamplingError(f"Input CSV row {line_number} has more fields than the header.")
                rows.append({name: str(raw.get(name) or "") for name in reader.fieldnames})
    except OSError as exc:
        raise SamplingError(f"Unable to read paired CSV {path}: {exc}") from exc
    if not rows:
        raise SamplingError("Input paired CSV contains zero paper rows.")
    return fieldnames, rows


def _fraction(value: str | Decimal) -> Decimal:
    try:
        fraction = Decimal(str(value))
    except InvalidOperation as exc:
        raise SamplingError(f"Invalid sample fraction: {value!r}") from exc
    if not fraction.is_finite() or fraction <= 0 or fraction > 1:
        raise SamplingError("Sample fraction must be greater than 0 and at most 1.")
    return fraction


def _score(seed: str, paper_key: str) -> str:
    payload = f"{SAMPLE_ALGORITHM}\0{seed}\0{paper_key}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _allocate_quotas(
    groups: dict[str, list[dict[str, Any]]], target_count: int, total_count: int
) -> dict[str, int]:
    """Allocate seats with the house-monotone D'Hondt divisor method.

    Unlike largest-remainder allocation, increasing the target sample size cannot
    reduce a stratum's quota. This is required for cumulative sample expansion.
    """
    del total_count  # The stratum sizes already sum to the eligible total.
    quotas = {stratum: 0 for stratum in groups}
    ordered_strata = sorted(groups)
    for _ in range(target_count):
        available = [name for name in ordered_strata if quotas[name] < len(groups[name])]
        if not available:
            break
        winner = max(
            available,
            key=lambda name: (Fraction(len(groups[name]), quotas[name] + 1), name),
        )
        quotas[winner] += 1
    if sum(quotas.values()) != target_count:
        raise SamplingError("Stratum quota allocation did not reach the exact target sample count.")
    return quotas


def _select_keys(
    candidates: list[dict[str, Any]], fraction: Decimal, min_papers: int,
) -> tuple[set[str], int, dict[str, list[dict[str, Any]]], dict[str, int]]:
    eligible = [item for item in candidates if item["eligible"]]
    if not eligible:
        raise SamplingError("No eligible preprocessed papers remain after checking preprocessing_path and preprocessing_status.")
    target_count = int(
        (Decimal(len(eligible)) * fraction).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    target_count = min(len(eligible), max(min_papers, target_count))
    strata: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in eligible:
        strata[item["stratum"]].append(item)
    quotas = _allocate_quotas(strata, target_count, len(eligible))
    selected_keys: set[str] = set()
    for stratum, members in strata.items():
        ranked = sorted(members, key=lambda item: (item["score"], item["paper_key"]))
        selected_keys.update(item["paper_key"] for item in ranked[:quotas[stratum]])
    return selected_keys, target_count, dict(strata), quotas


def _read_previous_manifest(
    path: Path,
    *,
    source_csv_hash: str,
    seed: str,
    stratify_by: tuple[str, ...],
    min_papers: int,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SamplingError(f"Cannot read previous sampling manifest {path}: {exc}") from exc
    if not isinstance(previous, dict):
        raise SamplingError("Previous sampling manifest must be a JSON object.")
    if previous.get("sampling_manifest_version") != SAMPLE_MANIFEST_VERSION:
        raise SamplingError("Previous sampling manifest version is unsupported.")
    if previous.get("algorithm") != SAMPLE_ALGORITHM:
        raise SamplingError("Previous sampling manifest uses a different sampling algorithm.")
    if previous.get("source_csv_sha256") != source_csv_hash:
        raise SamplingError("Previous sample was built from a different source CSV. Incremental expansion requires the exact same source corpus.")
    if previous.get("seed") != seed:
        raise SamplingError("The seed must remain unchanged when expanding a previous sample.")
    if previous.get("stratify_by") != list(stratify_by):
        raise SamplingError("Stratification columns must remain unchanged when expanding a previous sample.")
    try:
        previous_fraction = _fraction(str(previous.get("requested_fraction")))
        previous_min = int(previous.get("min_papers", 1))
    except (SamplingError, TypeError, ValueError) as exc:
        raise SamplingError("Previous sampling manifest contains invalid sample parameters.") from exc
    if previous_min < 1:
        raise SamplingError("Previous sampling manifest has an invalid min_papers value.")
    if previous_min != min_papers:
        raise SamplingError("The min_papers setting must remain unchanged when expanding a previous sample.")
    previous_keys = previous.get("selected_paper_keys")
    if not isinstance(previous_keys, list) or any(not isinstance(key, str) for key in previous_keys):
        raise SamplingError("Previous manifest selected_paper_keys is invalid.")
    if len(previous_keys) != len(set(previous_keys)):
        raise SamplingError("Previous manifest contains duplicate selected paper keys.")

    expected_previous_keys, expected_count, _, _ = _select_keys(candidates, previous_fraction, previous_min)
    if set(previous_keys) != expected_previous_keys:
        raise SamplingError("Previous manifest selected keys do not match the deterministic sample implied by its parameters.")
    if previous.get("selected_paper_count") != expected_count:
        raise SamplingError("Previous manifest selected_paper_count does not match its selection parameters.")
    previous_csv_path = Path(str(previous.get("sampled_csv_path") or "")).expanduser()
    if not previous_csv_path.is_file():
        raise SamplingError("The previous sampled CSV is missing; preserve the previous round's artifacts before expansion.")
    if _sha256_file(previous_csv_path) != previous.get("sampled_csv_sha256"):
        raise SamplingError("The previous sampled CSV no longer matches its recorded SHA-256.")
    previous_csv_hash = str(previous["sampled_csv_sha256"])
    return {
        "path": path.resolve(),
        "sha256": _sha256_file(path),
        "fraction": previous_fraction,
        "min_papers": previous_min,
        "selected_keys": expected_previous_keys,
        "selected_count": expected_count,
        "sampled_csv_sha256": previous_csv_hash,
    }


def create_corpus_sample(
    paired_csv: Path,
    output_dir: Path,
    *,
    fraction: str | Decimal = "0.1",
    seed: str = "feature-create-v1",
    stratify_by: tuple[str, ...] | list[str] = ("year",),
    min_papers: int = 1,
    previous_manifest: Path | None = None,
) -> dict[str, Any]:
    """Select a reproducible paper-level sample and publish CSV + provenance manifest.

    Sampling is based on stable paper identity, not row order. All CSV rows belonging
    to a selected DOI/title-year-journal identity are retained together.
    """
    paired_csv = paired_csv.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    previous_manifest = previous_manifest.expanduser().resolve() if previous_manifest else None
    if not paired_csv.is_file():
        raise SamplingError(f"Input paired CSV does not exist: {paired_csv}")
    fraction_value = _fraction(fraction)
    if not str(seed).strip():
        raise SamplingError("Sampling seed must not be empty.")
    if min_papers < 1:
        raise SamplingError("min_papers must be at least one so a valid non-empty corpus sample is produced.")

    fieldnames, rows = _read_csv(paired_csv)
    field_lookup = {name.casefold(): name for name in fieldnames}
    normalized_strata = tuple(dict.fromkeys(str(item).strip().casefold() for item in stratify_by if str(item).strip()))
    unknown = [name for name in normalized_strata if name not in field_lookup]
    if unknown:
        raise SamplingError("Unknown stratification columns: " + ", ".join(unknown))

    grouped_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped_rows[paper_identity_key(row)].append(row)

    candidates: list[dict[str, Any]] = []
    for paper_key, paper_rows in grouped_rows.items():
        first = paper_rows[0]
        statuses = sorted({_row_value(row, "preprocessing_status").upper() for row in paper_rows})
        package_paths = sorted({_row_value(row, "preprocessing_path") for row in paper_rows})
        missing_paths = any(not _row_value(row, "preprocessing_path") for row in paper_rows)
        unsupported = sorted(status for status in statuses if status not in ALLOWED_PREPROCESSING_STATUSES)
        reasons = []
        if missing_paths:
            reasons.append("missing_preprocessing_path")
        if unsupported:
            reasons.append("unsupported_preprocessing_status:" + ",".join(unsupported))
        if len(package_paths) > 1:
            reasons.append("conflicting_preprocessing_paths")
        if len(statuses) > 1:
            reasons.append("conflicting_preprocessing_statuses")
        for core_field in ("title", "year", "journal", "article_type"):
            core_values = {
                " ".join(_row_value(row, core_field).casefold().split())
                for row in paper_rows
            }
            if len(core_values) > 1:
                reasons.append(f"conflicting_paper_metadata:{core_field}")

        stratum_values = []
        for name in normalized_strata:
            values = sorted({
                _row_value(row, field_lookup[name]).strip() or "(missing)"
                for row in paper_rows
            }, key=lambda value: (value.casefold(), value))
            if len(values) > 1:
                reasons.append(f"conflicting_stratification_metadata:{name}")
                stratum_values.append("(conflict)")
            else:
                stratum_values.append(values[0] if values else "(missing)")
        stratum = json.dumps(stratum_values, ensure_ascii=False, separators=(",", ":")) if normalized_strata else "[]"
        score = _score(str(seed), paper_key)
        candidates.append({
            "paper_key": paper_key,
            "doi": _normalized_doi(_row_value(first, "doi")),
            "title": _row_value(first, "title"),
            "year": _row_value(first, "year"),
            "journal": _row_value(first, "journal"),
            "row_count": len(paper_rows),
            "eligible": not reasons,
            "exclusion_reasons": reasons,
            "stratum": stratum,
            "score": score,
        })

    source_csv_hash = _sha256_file(paired_csv)
    selected_keys, target_count, strata, quotas = _select_keys(candidates, fraction_value, min_papers)
    previous = None
    if previous_manifest is not None:
        previous = _read_previous_manifest(
            previous_manifest,
            source_csv_hash=source_csv_hash,
            seed=str(seed),
            stratify_by=normalized_strata,
            min_papers=min_papers,
            candidates=candidates,
        )
        if previous["fraction"] >= fraction_value:
            raise SamplingError("The new sample fraction must be greater than the previous sample fraction.")
        if not previous["selected_keys"].issubset(selected_keys):
            raise SamplingError(
                "The current sample is not a superset of the previous sample; "
                "check algorithm version, source CSV, seed, and strata."
            )

    eligible = [item for item in candidates if item["eligible"]]
    previous_keys = previous["selected_keys"] if previous else set()
    newly_selected_keys = selected_keys - previous_keys
    selected_rows = [row for row in rows if paper_identity_key(row) in selected_keys]
    newly_selected_rows = [row for row in rows if paper_identity_key(row) in newly_selected_keys]

    output_dir.mkdir(parents=True, exist_ok=True)
    sampled_csv = output_dir / "sampled_research_corpus_paired.csv"
    incremental_csv = output_dir / "newly_selected_research_corpus_paired.csv"
    manifest_path = output_dir / "sampling_manifest.json"
    if paired_csv in {sampled_csv, incremental_csv, manifest_path}:
        raise SamplingError("Sampling outputs must not overwrite the input paired CSV.")
    if any(path.exists() for path in (sampled_csv, incremental_csv, manifest_path)):
        raise SamplingError(
            f"Sampling outputs already exist in {output_dir}; use a new output directory "
            "to preserve the previous selection and its provenance."
        )

    temp_csv = sampled_csv.with_name(sampled_csv.name + ".tmp")
    temp_incremental = incremental_csv.with_name(incremental_csv.name + ".tmp")
    temp_manifest = manifest_path.with_name(manifest_path.name + ".tmp")
    try:
        with temp_csv.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
            writer.writeheader()
            writer.writerows(selected_rows)
        sampled_hash = _sha256_file(temp_csv)
        with temp_incremental.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
            writer.writeheader()
            writer.writerows(newly_selected_rows)
        incremental_hash = _sha256_file(temp_incremental)
        stratum_summary = []
        for stratum, members in sorted(strata.items()):
            stratum_summary.append({
                "stratum": stratum,
                "eligible_paper_count": len(members),
                "selected_paper_count": quotas[stratum],
                "newly_selected_paper_count": sum(
                    1 for item in members
                    if item["paper_key"] in newly_selected_keys
                ),
            })
        for item in candidates:
            item["selected"] = item["paper_key"] in selected_keys

        manifest = {
            "sampling_manifest_version": SAMPLE_MANIFEST_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "algorithm": SAMPLE_ALGORITHM,
            "seed": str(seed),
            "requested_fraction": format(fraction_value, "f").rstrip("0").rstrip(".") if "." in format(fraction_value, "f") else format(fraction_value, "f"),
            "min_papers": min_papers,
            "stratify_by": list(normalized_strata),
            "source_csv_path": str(paired_csv),
            "source_csv_sha256": _sha256_file(paired_csv),
            "source_row_count": len(rows),
            "source_paper_count": len(candidates),
            "eligible_paper_count": len(eligible),
            "ineligible_paper_count": len(candidates) - len(eligible),
            "target_selected_paper_count": target_count,
            "selected_paper_count": len(selected_keys),
            "selected_row_count": len(selected_rows),
            "sampled_csv_path": str(sampled_csv),
            "sampled_csv_sha256": sampled_hash,
            "selected_paper_keys": sorted(selected_keys),
            "previous_manifest_path": str(previous["path"]) if previous else None,
            "previous_manifest_sha256": previous["sha256"] if previous else None,
            "previous_sample_fraction": str(previous["fraction"]) if previous else None,
            "previous_selected_paper_count": previous["selected_count"] if previous else 0,
            "newly_selected_paper_count": len(newly_selected_keys),
            "newly_selected_row_count": len(newly_selected_rows),
            "newly_selected_paper_keys": sorted(newly_selected_keys),
            "incremental_csv_path": str(incremental_csv),
            "incremental_csv_sha256": incremental_hash,
            "stratum_summary": stratum_summary,
            "paper_decisions": sorted(candidates, key=lambda item: item["paper_key"]),
        }
        temp_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(temp_csv, sampled_csv)
        os.replace(temp_incremental, incremental_csv)
        # The manifest is the commit marker and is published only after both CSVs exist.
        os.replace(temp_manifest, manifest_path)
    except Exception:
        for temporary in (temp_csv, temp_incremental, temp_manifest):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    return {
        "status": "complete" if newly_selected_keys else "no_new_papers",
        "source_paper_count": len(candidates),
        "eligible_paper_count": len(eligible),
        "ineligible_paper_count": len(candidates) - len(eligible),
        "selected_paper_count": len(selected_keys),
        "selected_row_count": len(selected_rows),
        "requested_fraction": str(fraction_value.normalize()),
        "min_papers": min_papers,
        "seed": str(seed),
        "stratify_by": list(normalized_strata),
        "sampled_csv": str(sampled_csv),
        "incremental_csv": str(incremental_csv),
        "sampling_manifest": str(manifest_path),
        "sampled_csv_sha256": sampled_hash,
        "incremental_csv_sha256": incremental_hash,
        "previous_selected_paper_count": len(previous_keys),
        "newly_selected_paper_count": len(newly_selected_keys),
        "newly_selected_row_count": len(newly_selected_rows),
        "previous_manifest": str(previous_manifest) if previous_manifest else None,
    }
