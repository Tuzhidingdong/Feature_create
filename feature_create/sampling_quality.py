"""Read-only diagnostics comparing a sampled corpus with its eligible population."""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .sampling import (
    SAMPLE_ALGORITHM,
    SAMPLE_MANIFEST_VERSION,
    SamplingError,
    _read_csv,
    _row_value,
    _sha256_file,
    paper_identity_key,
)

REPORT_VERSION = "1.0"
DEFAULT_DIMENSIONS = ("year", "journal", "article_type", "preprocessing_status")


class SamplingQualityError(RuntimeError):
    """Raised when report inputs are inconsistent or cannot be read."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SamplingQualityError(f"Cannot read sampling manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SamplingQualityError("Sampling manifest must contain a JSON object.")
    return value


def _group_rows(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    try:
        for row in rows:
            grouped[paper_identity_key(row)].append(row)
    except SamplingError as exc:
        raise SamplingQualityError(str(exc)) from exc
    return dict(grouped)


def _category(rows: list[dict[str, str]], column: str) -> tuple[str, bool]:
    """Return one normalized paper-level category and whether source metadata conflict."""
    values = {
        " ".join(_row_value(row, column).split()).casefold()
        for row in rows
    }
    if column.casefold() == "preprocessing_status":
        values = {value.upper() for value in values}
    if len(values) > 1:
        return "(conflict)", True
    value = next(iter(values), "")
    return value if value else "(missing)", False


def _dimension_report(
    population: dict[str, list[dict[str, str]]],
    sample: dict[str, list[dict[str, str]]],
    column: str,
) -> dict[str, Any]:
    population_counts: Counter[str] = Counter()
    sample_counts: Counter[str] = Counter()
    conflict_papers = 0
    for rows in population.values():
        value, conflict = _category(rows, column)
        population_counts[value] += 1
        conflict_papers += int(conflict)
    for rows in sample.values():
        value, _ = _category(rows, column)
        sample_counts[value] += 1

    population_size = len(population)
    sample_size = len(sample)
    categories = sorted(
        population_counts,
        key=lambda value: (-population_counts[value], value),
    )
    details = []
    absolute_share_differences = []
    absent_categories = []
    absent_category_population_papers = 0
    for value in categories:
        population_count = population_counts[value]
        sample_count = sample_counts[value]
        population_share = population_count / population_size
        sample_share = sample_count / sample_size
        difference = sample_share - population_share
        absolute_share_differences.append(abs(difference))
        if sample_count == 0:
            absent_categories.append(value)
            absent_category_population_papers += population_count
        details.append({
            "category": value,
            "population_paper_count": population_count,
            "sample_paper_count": sample_count,
            "population_share": round(population_share, 8),
            "sample_share": round(sample_share, 8),
            "share_difference_percentage_points": round(difference * 100, 4),
            "representation_ratio": round(sample_share / population_share, 4) if population_share else None,
        })

    total_variation = sum(absolute_share_differences) / 2
    return {
        "column": column,
        "population_category_count": len(population_counts),
        "sample_category_count": len(sample_counts),
        "population_metadata_conflict_paper_count": conflict_papers,
        "total_variation_distance": round(total_variation, 8),
        "total_variation_distance_percent": round(total_variation * 100, 4),
        "max_absolute_share_difference_percentage_points": round(
            max(absolute_share_differences, default=0.0) * 100, 4
        ),
        "categories_absent_from_sample": absent_categories,
        "absent_category_population_paper_count": absent_category_population_papers,
        "categories": details,
    }


def build_sampling_quality_report(
    paired_csv: Path,
    sampling_manifest: Path,
    output_dir: Path,
    *,
    sample_csv: Path | None = None,
    dimensions: tuple[str, ...] | list[str] = DEFAULT_DIMENSIONS,
) -> dict[str, Any]:
    """Validate sampling artifacts and write descriptive distribution diagnostics.

    The comparison population is the set of papers marked eligible in the immutable
    sampling manifest. The sample must be the manifest's cumulative CSV, not its
    incremental-only CSV. No PDF, SI, or preprocessing package is opened.
    """
    paired_csv = paired_csv.expanduser().resolve()
    sampling_manifest = sampling_manifest.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    manifest = _load_json(sampling_manifest)

    if manifest.get("sampling_manifest_version") != SAMPLE_MANIFEST_VERSION:
        raise SamplingQualityError("Unsupported sampling manifest version.")
    if manifest.get("algorithm") != SAMPLE_ALGORITHM:
        raise SamplingQualityError("Sampling algorithm does not match this Feature_create version.")
    if not paired_csv.is_file():
        raise SamplingQualityError(f"Source paired CSV does not exist: {paired_csv}")
    if _sha256_file(paired_csv) != manifest.get("source_csv_sha256"):
        raise SamplingQualityError("Source CSV SHA-256 does not match the sampling manifest.")

    recorded_sample = Path(str(manifest.get("sampled_csv_path") or "")).expanduser()
    sample_csv = sample_csv.expanduser().resolve() if sample_csv else recorded_sample.resolve()
    if not sample_csv.is_file():
        raise SamplingQualityError(f"Cumulative sampled CSV does not exist: {sample_csv}")
    if _sha256_file(sample_csv) != manifest.get("sampled_csv_sha256"):
        raise SamplingQualityError(
            "Sample CSV SHA-256 does not match sampled_csv_sha256. "
            "Pass the cumulative sampled_research_corpus_paired.csv, not the incremental-only CSV."
        )

    source_fields, source_rows = _read_csv(paired_csv)
    _, sample_rows = _read_csv(sample_csv)
    source_groups = _group_rows(source_rows)
    sample_groups = _group_rows(sample_rows)

    decisions = manifest.get("paper_decisions")
    if not isinstance(decisions, list):
        raise SamplingQualityError("Manifest paper_decisions must be a list.")
    decision_by_key: dict[str, dict[str, Any]] = {}
    for item in decisions:
        if not isinstance(item, dict) or not isinstance(item.get("paper_key"), str):
            raise SamplingQualityError("Manifest contains a malformed paper decision.")
        key = item["paper_key"]
        if key in decision_by_key:
            raise SamplingQualityError(f"Manifest contains duplicate paper decision: {key}")
        decision_by_key[key] = item

    if set(decision_by_key) != set(source_groups):
        raise SamplingQualityError("Manifest paper decisions do not cover the source CSV paper identities.")
    for key, rows in source_groups.items():
        if decision_by_key[key].get("row_count") != len(rows):
            raise SamplingQualityError(f"Manifest source row count mismatch for paper {key}.")

    eligible_keys = {
        key for key, item in decision_by_key.items()
        if item.get("eligible") is True
    }
    selected_list = manifest.get("selected_paper_keys")
    if not isinstance(selected_list, list) or any(not isinstance(key, str) for key in selected_list):
        raise SamplingQualityError("Manifest selected_paper_keys is malformed.")
    selected_keys = set(selected_list)
    if len(selected_keys) != len(selected_list):
        raise SamplingQualityError("Manifest selected_paper_keys contains duplicates.")
    flagged_selected = {
        key for key, item in decision_by_key.items()
        if item.get("selected") is True
    }
    if selected_keys != flagged_selected or not selected_keys.issubset(eligible_keys):
        raise SamplingQualityError("Manifest selected keys, paper decisions, and eligible keys disagree.")
    if manifest.get("selected_paper_count") != len(selected_keys):
        raise SamplingQualityError("Manifest selected_paper_count is inconsistent.")
    if manifest.get("eligible_paper_count") != len(eligible_keys):
        raise SamplingQualityError("Manifest eligible_paper_count is inconsistent.")
    if set(sample_groups) != selected_keys:
        raise SamplingQualityError("Cumulative sample paper identities do not match selected_paper_keys.")
    for key in selected_keys:
        if len(sample_groups[key]) != len(source_groups[key]):
            raise SamplingQualityError(f"Sample row count does not preserve every row for paper {key}.")
    if manifest.get("selected_row_count") != len(sample_rows):
        raise SamplingQualityError("Manifest selected_row_count is inconsistent with the cumulative sample.")

    normalized_dimensions = tuple(dict.fromkeys(
        str(value).strip().casefold() for value in dimensions if str(value).strip()
    ))
    if not normalized_dimensions:
        raise SamplingQualityError("At least one report dimension must be selected.")
    field_lookup = {name.casefold(): name for name in source_fields}
    unknown = [name for name in normalized_dimensions if name not in field_lookup]
    if unknown:
        raise SamplingQualityError("Unknown report dimensions: " + ", ".join(unknown))

    eligible_population = {key: source_groups[key] for key in eligible_keys}
    sampled_population = {key: source_groups[key] for key in selected_keys}
    dimension_reports = [
        _dimension_report(eligible_population, sampled_population, field_lookup[name])
        for name in normalized_dimensions
    ]
    ineligible_count = len(source_groups) - len(eligible_population)
    requested_fraction = manifest.get("requested_fraction")
    report = {
        "report_version": REPORT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "report_type": "descriptive_sampling_distribution_diagnostics",
        "source": {
            "paired_csv_path": str(paired_csv),
            "paired_csv_sha256": manifest["source_csv_sha256"],
            "source_row_count": len(source_rows),
            "source_paper_count": len(source_groups),
        },
        "sample": {
            "cumulative_csv_path": str(sample_csv),
            "cumulative_csv_sha256": manifest["sampled_csv_sha256"],
            "sampling_manifest_path": str(sampling_manifest),
            "sampling_manifest_version": SAMPLE_MANIFEST_VERSION,
            "algorithm": SAMPLE_ALGORITHM,
            "seed": manifest.get("seed"),
            "requested_fraction": requested_fraction,
            "eligible_population_paper_count": len(eligible_population),
            "ineligible_paper_count": ineligible_count,
            "selected_paper_count": len(selected_keys),
            "actual_fraction_of_eligible_population": round(
                len(selected_keys) / len(eligible_population), 8
            ),
            "sampled_row_count": len(sample_rows),
        },
        "dimensions": dimension_reports,
        "interpretation": {
            "scope": "Paper-level metadata distributions among eligible papers only.",
            "limitations": [
                "This report describes observed sample-to-population distribution differences; it does not prove scientific or semantic representativeness.",
                "Rare categories may be absent from a small sample even when the overall distribution is close.",
                "Ineligible papers are excluded from both population and sample comparisons and are counted separately.",
                "The report does not change selection decisions, inspect PDF/SI content, or infer coverage of scientific findings.",
            ],
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "sampling_quality_report.json"
    markdown_path = output_dir / "sampling_quality_report.md"
    protected = {paired_csv, sample_csv, sampling_manifest}
    if json_path.resolve() in protected or markdown_path.resolve() in protected:
        raise SamplingQualityError("Report output paths must not overwrite source, sample, or manifest inputs.")
    markdown = render_sampling_quality_markdown(report)
    json_temp = json_path.with_name(json_path.name + ".tmp")
    markdown_temp = markdown_path.with_name(markdown_path.name + ".tmp")
    try:
        json_temp.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        markdown_temp.write_text(markdown, encoding="utf-8", newline="\n")
        os.replace(json_temp, json_path)
        os.replace(markdown_temp, markdown_path)
    except Exception:
        for path in (json_temp, markdown_temp):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    report["report_files"] = {
        "json": str(json_path),
        "markdown": str(markdown_path),
    }
    return report


def render_sampling_quality_markdown(report: dict[str, Any]) -> str:
    source = report["source"]
    sample = report["sample"]
    lines = [
        "# Sampling quality report",
        "",
        "> Descriptive diagnostics only. This report does not certify scientific representativeness.",
        "",
        "## Snapshot",
        "",
        f"- Eligible population: **{sample['eligible_population_paper_count']} papers**",
        f"- Selected cumulative sample: **{sample['selected_paper_count']} papers** "
        f"({sample['actual_fraction_of_eligible_population']:.2%} of eligible papers)",
        f"- Ineligible papers excluded from comparison: **{sample['ineligible_paper_count']}**",
        f"- Source CSV SHA-256: {source['paired_csv_sha256']}",
        f"- Sample CSV SHA-256: {sample['cumulative_csv_sha256']}",
        f"- Sampling seed: {sample.get('seed')}; requested fraction: {sample.get('requested_fraction')}",
        "",
    ]
    for dimension in report["dimensions"]:
        lines.extend([
            f"## {dimension['column']}",
            "",
            f"- Total variation distance: **{dimension['total_variation_distance_percent']:.2f}%**",
            f"- Largest absolute category-share difference: **{dimension['max_absolute_share_difference_percentage_points']:.2f} percentage points**",
            f"- Categories absent from sample: **{len(dimension['categories_absent_from_sample'])}** "
            f"(representing {dimension['absent_category_population_papers']} eligible papers)",
            f"- Papers with conflicting values for this dimension: **{dimension['population_metadata_conflict_paper_count']}**",
            "",
            "| Category | Population n | Sample n | Population % | Sample % | Difference (pp) | Representation ratio |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ])
        for item in dimension["categories"]:
            ratio = "—" if item["representation_ratio"] is None else f"{item['representation_ratio']:.3f}"
            category = item["category"].replace("|", "\\|")
            lines.append(
                f"| {category} | {item['population_paper_count']} | {item['sample_paper_count']} "
                f"| {item['population_share']:.2%} | {item['sample_share']:.2%} "
                f"| {item['share_difference_percentage_points']:+.2f} | {ratio} |"
            )
        lines.append("")
    lines.extend([
        "## Interpretation limits",
        "",
        *[f"- {note}" for note in report["interpretation"]["limitations"]],
        "",
    ])
    return "\n".join(lines)


__all__ = [
    "DEFAULT_DIMENSIONS",
    "SamplingQualityError",
    "build_sampling_quality_report",
    "render_sampling_quality_markdown",
]
