"""Optional, read-only diminishing-returns diagnostics for sequential Feature discovery batches.

This report uses the current reconciled Registry identity, so merges/splits can
retroactively change the historical batch attribution. It never changes or gates
the extraction workflow.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from scripts.reconcile_features import exclusive_lock, registry_lock_path


REPORT_VERSION = "1.0"


class FeatureSaturationError(RuntimeError):
    """Raised when batch manifests or Registry data cannot be compared safely."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FeatureSaturationError(f"Cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FeatureSaturationError(f"Expected a JSON object: {path}")
    return value


def _load_batches(
    batch_manifests: Sequence[tuple[str, Path]],
) -> tuple[list[dict[str, Any]], set[str], list[str]]:
    if not batch_manifests:
        raise FeatureSaturationError("Provide at least one ordered --batch LABEL=source_manifest.json input.")
    labels: set[str] = set()
    assigned_papers: set[str] = set()
    batches: list[dict[str, Any]] = []
    sampling_metadata: list[dict[str, Any]] = []
    warnings: list[str] = []

    for index, (raw_label, raw_path) in enumerate(batch_manifests, start=1):
        label = str(raw_label).strip()
        path = raw_path.expanduser().resolve()
        if not label:
            raise FeatureSaturationError(f"Batch {index} has an empty label.")
        if label in labels:
            raise FeatureSaturationError(f"Duplicate batch label: {label}")
        labels.add(label)
        if not path.is_file():
            raise FeatureSaturationError(f"Batch source manifest does not exist: {path}")
        manifest = read_json(path)
        papers = manifest.get("papers")
        if not isinstance(papers, list) or not papers:
            raise FeatureSaturationError(f"Batch manifest has no papers array or contains zero papers: {path}")
        paper_ids: list[str] = []
        paper_by_id: dict[str, dict[str, Any]] = {}
        for paper in papers:
            if not isinstance(paper, dict) or not isinstance(paper.get("paper_id"), str) or not paper["paper_id"]:
                raise FeatureSaturationError(f"Batch manifest contains a paper without a valid paper_id: {path}")
            paper_id = paper["paper_id"]
            if paper_id in paper_by_id:
                raise FeatureSaturationError(f"Batch manifest repeats paper_id {paper_id}: {path}")
            paper_by_id[paper_id] = paper
            paper_ids.append(paper_id)

        if manifest.get("paper_count") is not None and manifest["paper_count"] != len(paper_ids):
            raise FeatureSaturationError(f"Batch manifest paper_count does not match its papers array: {path}")
        overlap = assigned_papers.intersection(paper_ids)
        if overlap:
            examples = ", ".join(sorted(overlap)[:5])
            raise FeatureSaturationError(
                f"Batch paper overlap detected at {label}; use the incremental prepared manifest, "
                f"not a cumulative round. Repeated paper IDs: {examples}"
            )
        assigned_papers.update(paper_ids)

        sampling = manifest.get("sampling")
        if sampling is not None:
            if not isinstance(sampling, dict):
                raise FeatureSaturationError(f"Batch manifest sampling provenance is malformed: {path}")
            if sampling.get("applied_paper_count") != len(paper_ids):
                raise FeatureSaturationError(
                    f"Batch {label} sampling applied_paper_count does not match its prepared paper count."
                )
            sampling_metadata.append({
                "label": label,
                "source_csv_sha256": sampling.get("source_csv_sha256"),
                "seed": sampling.get("seed"),
                "algorithm": sampling.get("algorithm"),
                "applied_view": sampling.get("applied_view"),
            })

        ready_count = sum(
            1 for paper in papers
            if paper.get("input_status") in {"ready", "ready_with_warnings"}
        )
        blocked_count = sum(
            1 for paper in papers
            if str(paper.get("input_status") or "").startswith("blocked")
        )
        batches.append({
            "index": index,
            "label": label,
            "manifest_path": str(path),
            "manifest_sha256": sha256_file(path),
            "paper_count": len(paper_ids),
            "paper_ids": paper_ids,
            "paper_by_id": paper_by_id,
            "ready_paper_count": ready_count,
            "blocked_paper_count": blocked_count,
            "sampling": sampling,
        })

    if len(sampling_metadata) == len(batches) and sampling_metadata:
        for field in ("source_csv_sha256", "seed", "algorithm"):
            values = {item.get(field) for item in sampling_metadata}
            if len(values) > 1:
                raise FeatureSaturationError(
                    f"Sampled batch manifests disagree on {field}; they do not belong to one comparable sampling lineage."
                )
        if sampling_metadata[0].get("applied_view") not in {"cumulative", "incremental"}:
            raise FeatureSaturationError("The first sampled batch has an unsupported applied_view.")
        for item in sampling_metadata[1:]:
            if item.get("applied_view") != "incremental":
                warnings.append(
                    f"Batch {item['label']} is marked {item.get('applied_view')!r}, not incremental; "
                    "verify that the run contains only newly added papers."
                )
    elif sampling_metadata and len(sampling_metadata) != len(batches):
        warnings.append(
            "Some batch manifests include sampling provenance and others do not; "
            "confirm that the supplied batches form the intended sequence."
        )

    return batches, assigned_papers, warnings


def _resolve_feature_id(
    feature_id: str,
    feature_by_id: dict[str, dict[str, Any]],
) -> str:
    seen: set[str] = set()
    current = feature_id
    while True:
        if current in seen:
            raise FeatureSaturationError(f"Registry Feature redirect cycle detected at {current}.")
        seen.add(current)
        feature = feature_by_id.get(current)
        if feature is None:
            raise FeatureSaturationError(f"Observation points to missing Registry Feature ID: {current}")
        if feature.get("feature_status") != "retired_merged":
            return current
        redirect = feature.get("redirect_to")
        if not isinstance(redirect, str) or not redirect:
            raise FeatureSaturationError(f"Retired Feature {current} has no redirect_to target.")
        current = redirect


def _registry_index(registry: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    observations = registry.get("observations")
    features = registry.get("canonical_features")
    if not isinstance(observations, list) or not isinstance(features, list):
        raise FeatureSaturationError("Registry must contain observations and canonical_features arrays.")
    observation_by_id: dict[str, dict[str, Any]] = {}
    feature_by_id: dict[str, dict[str, Any]] = {}
    for item in observations:
        if not isinstance(item, dict) or not isinstance(item.get("observation_id"), str):
            raise FeatureSaturationError("Registry contains a malformed Observation.")
        identifier = item["observation_id"]
        if identifier in observation_by_id:
            raise FeatureSaturationError(f"Registry contains duplicate Observation ID: {identifier}")
        observation_by_id[identifier] = item
    for item in features:
        if not isinstance(item, dict) or not isinstance(item.get("registry_feature_id"), str):
            raise FeatureSaturationError("Registry contains a malformed canonical Feature.")
        identifier = item["registry_feature_id"]
        if identifier in feature_by_id:
            raise FeatureSaturationError(f"Registry contains duplicate canonical Feature ID: {identifier}")
        feature_by_id[identifier] = item

    for observation in observations:
        linked = observation.get("linked_registry_feature_id")
        if linked:
            resolved = _resolve_feature_id(str(linked), feature_by_id)
            canonical = feature_by_id[resolved]
            if observation["observation_id"] not in canonical.get("observation_ids", []):
                raise FeatureSaturationError(
                    f"Observation {observation['observation_id']} is not listed in its canonical Feature "
                    f"{resolved} observation_ids backlink."
                )
    return observation_by_id, feature_by_id


def _build_batch_metrics(
    batches: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    feature_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], set[str]]:
    observations_by_paper: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        paper_id = str(observation.get("paper_id") or "")
        observations_by_paper.setdefault(paper_id, []).append(observation)

    seen_feature_ids: set[str] = set()
    cumulative_papers = 0
    cumulative_observation_count = 0
    cumulative_result_observation_count = 0
    metrics: list[dict[str, Any]] = []

    for batch in batches:
        paper_ids = set(batch["paper_ids"])
        batch_observations = [
            observation
            for paper_id in paper_ids
            for observation in observations_by_paper.get(paper_id, [])
        ]
        result_observations = [
            item for item in batch_observations
            if item.get("variable_role") == "result_feature"
            and item.get("discovery_status") != "excluded"
        ]
        uncertain_observations = [
            item for item in batch_observations
            if item.get("variable_role") == "uncertain"
            and item.get("discovery_status") != "excluded"
        ]

        current_feature_ids: set[str] = set()
        status_counts: Counter[str] = Counter()
        verified_observation_count = 0
        for observation in result_observations:
            if observation.get("discovery_status") == "evidence_verified":
                verified_observation_count += 1
            linked = observation.get("linked_registry_feature_id")
            if not linked:
                continue
            resolved = _resolve_feature_id(str(linked), feature_by_id)
            current_feature_ids.add(resolved)

        new_feature_ids = current_feature_ids - seen_feature_ids
        for feature_id in new_feature_ids:
            status_counts[str(feature_by_id[feature_id].get("feature_status") or "unknown")] += 1
        seen_feature_ids.update(current_feature_ids)

        batch_paper_observation_ids = {
            str(item.get("paper_id"))
            for item in batch_observations
        }
        papers_with_result_observations = {
            str(item.get("paper_id"))
            for item in result_observations
        }
        cumulative_papers += len(paper_ids)
        cumulative_observation_count += len(batch_observations)
        cumulative_result_observation_count += len(result_observations)
        new_rate = len(new_feature_ids) / len(paper_ids) * 100 if paper_ids else 0.0

        metrics.append({
            "batch_index": batch["index"],
            "batch_label": batch["label"],
            "paper_count": len(paper_ids),
            "ready_paper_count": batch["ready_paper_count"],
            "blocked_paper_count": batch["blocked_paper_count"],
            "papers_with_any_registry_observation": len(batch_paper_observation_ids),
            "papers_with_result_feature_observation": len(papers_with_result_observations),
            "registry_observation_count": len(batch_observations),
            "result_feature_observation_count": len(result_observations),
            "evidence_verified_result_observation_count": verified_observation_count,
            "uncertain_observation_count": len(uncertain_observations),
            "unique_current_canonical_result_features_observed": len(current_feature_ids),
            "new_current_canonical_result_features": len(new_feature_ids),
            "new_canonical_features_per_100_papers": round(new_rate, 4),
            "new_feature_status_counts": dict(sorted(status_counts.items())),
            "new_feature_ids": sorted(new_feature_ids),
            "cumulative_paper_count": cumulative_papers,
            "cumulative_registry_observation_count": cumulative_observation_count,
            "cumulative_result_feature_observation_count": cumulative_result_observation_count,
            "cumulative_unique_current_canonical_result_features": len(seen_feature_ids),
            "cumulative_new_current_canonical_result_features": sum(
                item["new_current_canonical_result_features"] for item in metrics
            ) + len(new_feature_ids),
        })
    return metrics, seen_feature_ids


def calculate_feature_saturation_report(
    registry: dict[str, Any],
    registry_sha256: str,
    batches: list[dict[str, Any]],
    assigned_papers: set[str],
    batch_warnings: list[str],
    *,
    allow_unassigned: bool = False,
) -> dict[str, Any]:
    batch_paper_union: set[str] = set()
    for batch in batches:
        paper_ids = batch.get("paper_ids")
        if not isinstance(paper_ids, list) or any(not isinstance(item, str) or not item for item in paper_ids):
            raise FeatureSaturationError(f"Batch {batch.get('label', '<unknown>')} has invalid paper_ids.")
        if len(paper_ids) != len(set(paper_ids)):
            raise FeatureSaturationError(f"Batch {batch.get('label', '<unknown>')} contains duplicate paper IDs.")
        overlap = batch_paper_union.intersection(paper_ids)
        if overlap:
            examples = ", ".join(sorted(overlap)[:5])
            raise FeatureSaturationError(f"Batch paper overlap detected in core calculation: {examples}")
        batch_paper_union.update(paper_ids)
    if batch_paper_union != assigned_papers:
        raise FeatureSaturationError("The assigned_papers index does not match the union of ordered batch paper IDs.")

    observations = registry.get("observations", [])
    observation_by_id, feature_by_id = _registry_index(registry)
    registry_papers = {
        str(item.get("paper_id"))
        for item in observations
        if item.get("paper_id")
    }
    unassigned_papers = registry_papers - assigned_papers
    if unassigned_papers and not allow_unassigned:
        examples = ", ".join(sorted(unassigned_papers)[:5])
        raise FeatureSaturationError(
            f"Registry contains {len(unassigned_papers)} paper(s) absent from the supplied batch manifests "
            f"(examples: {examples}). Supply the missing batch manifests or use --allow-unassigned to "
            "produce an explicitly incomplete report."
        )

    metrics, all_feature_ids = _build_batch_metrics(
        batches, observations, feature_by_id
    )
    warnings = list(batch_warnings)
    if unassigned_papers:
        warnings.append(
            f"{len(unassigned_papers)} Registry paper(s) are outside the supplied batches; "
            "all cumulative and marginal counts below cover only supplied batches."
        )
    if any(item["blocked_paper_count"] for item in batches):
        warnings.append(
            "One or more source-manifest papers were blocked during preparation; "
            "paper counts include them, but they may not have contributed any observations."
        )
    if any(item["papers_with_result_feature_observation"] < item["paper_count"] for item in metrics):
        warnings.append(
            "Some papers have no ingested result-feature Observation. A low yield may reflect incomplete extraction, "
            "evidence gaps, or genuinely few reported features; this report cannot distinguish those causes."
        )
    if len(metrics) < 3:
        warnings.append(
            "Fewer than three batches were supplied. The output is a descriptive batch comparison, "
            "not evidence of a stable diminishing-returns trend."
        )
    if any(item.get("feature_status") in {"provisional", "under_review", "split_pending"} for item in feature_by_id.values()):
        warnings.append(
            "The current Registry still contains provisional, under-review, or split-pending Features. "
            "Further semantic reconciliation may change canonical Feature identities and historical novelty counts."
        )

    trend = None
    if len(metrics) >= 2:
        last = metrics[-1]
        preceding_papers = sum(item["paper_count"] for item in metrics[:-1])
        preceding_new_features = sum(item["new_current_canonical_result_features"] for item in metrics[:-1])
        preceding_rate = preceding_new_features / preceding_papers * 100 if preceding_papers else 0.0
        last_rate = last["new_canonical_features_per_100_papers"]
        trend = {
            "last_batch_label": last["batch_label"],
            "last_batch_new_features_per_100_papers": last_rate,
            "preceding_batches_pooled_new_features_per_100_papers": round(preceding_rate, 4),
            "last_batch_to_preceding_rate_ratio": (
                round(last_rate / preceding_rate, 4) if preceding_rate > 0 else None
            ),
            "relative_change_percent": (
                round((last_rate / preceding_rate - 1) * 100, 2) if preceding_rate > 0 else None
            ),
            "interpretation": "descriptive_only_no_automatic_saturation_decision",
        }

    return {
        "report_version": REPORT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "report_type": "optional_feature_discovery_diminishing_returns_diagnostics",
        "workflow_policy": {
            "default_enabled": False,
            "required_for_extraction": False,
            "blocks_later_batches": False,
            "automatically_recommends_stop": False,
            "modifies_registry": False,
        },
        "inputs": {
            "registry_id": registry.get("registry_id"),
            "registry_sha256": registry_sha256,
            "batch_count": len(batches),
            "batches": [
                {
                    "index": item["index"],
                    "label": item["label"],
                    "manifest_path": item["manifest_path"],
                    "manifest_sha256": item["manifest_sha256"],
                    "paper_count": item["paper_count"],
                    "ready_paper_count": item["ready_paper_count"],
                    "blocked_paper_count": item["blocked_paper_count"],
                    "sampling": (
                        {
                            "applied_view": item["sampling"].get("applied_view"),
                            "requested_fraction": item["sampling"].get("requested_fraction"),
                            "source_csv_sha256": item["sampling"].get("source_csv_sha256"),
                            "seed": item["sampling"].get("seed"),
                        }
                        if item["sampling"] else None
                    ),
                }
                for item in batches
            ],
            "unassigned_registry_paper_count": len(unassigned_papers),
        },
        "summary": {
            "supplied_batch_paper_count": sum(item["paper_count"] for item in batches),
            "registry_observation_count_in_supplied_batches": sum(item["registry_observation_count"] for item in metrics),
            "result_feature_observation_count_in_supplied_batches": sum(item["result_feature_observation_count"] for item in metrics),
            "unique_current_canonical_result_feature_count": len(all_feature_ids),
            "cumulative_new_current_canonical_result_feature_count": sum(
                item["new_current_canonical_result_features"] for item in metrics
            ),
            "trend_comparison": trend,
            "saturation_verdict": None,
            "saturation_verdict_note": (
                "No automatic saturated/not-saturated verdict is generated. Review marginal yield together with "
                "worker coverage, unresolved evidence, rare-feature expectations, and semantic reconciliation state."
            ),
        },
        "batches": metrics,
        "warnings": warnings,
        "limitations": [
            "This diagnostic is optional and is not part of the required extraction workflow.",
            "Batch order is the order of the --batch arguments; chronology is not inferred from timestamps.",
            "Feature identity is resolved using the current Registry. Later merges/splits can retroactively change which batch first contains a canonical Feature.",
            "The report measures newly observed current canonical result Features per paper, not completeness of scientific knowledge.",
            "A low marginal yield does not prove saturation; incomplete worker coverage, inaccessible sources, unresolved evidence, and semantic reconciliation can all change the result.",
            "Rare or domain-specific Features may remain undiscovered even after marginal yield decreases.",
            "No registry, records, source manifest, sampling manifest, or worker output is modified.",
        ],
    }


def render_feature_saturation_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Optional Feature Discovery Saturation Diagnostics",
        "",
        "> Descriptive only. This report does not declare the corpus saturated and does not control whether another batch is run.",
        "",
        "## Snapshot",
        "",
        f"- Batches: **{report['inputs']['batch_count']}**",
        f"- Papers across supplied batches: **{summary['supplied_batch_paper_count']}**",
        f"- Unique current canonical result Features: **{summary['unique_current_canonical_result_feature_count']}**",
        f"- Registry observations in supplied batches: **{summary['registry_observation_count_in_supplied_batches']}**",
        f"- Registry SHA-256: {report['inputs']['registry_sha256']}",
        "",
        "## Batch-by-batch marginal discovery",
        "",
        "| Batch | Papers | Papers with result observations | Result observations | New canonical Features | New Features / 100 papers | Cumulative Features |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report["batches"]:
        lines.append(
            f"| {item['batch_label']} | {item['paper_count']} | "
            f"{item['papers_with_result_feature_observation']} | "
            f"{item['result_feature_observation_count']} | "
            f"{item['new_current_canonical_result_features']} | "
            f"{item['new_canonical_features_per_100_papers']:.2f} | "
            f"{item['cumulative_unique_current_canonical_result_features']} |"
        )
    trend = summary.get("trend_comparison")
    lines.extend(["", "## Marginal-yield comparison", ""])
    if trend is None:
        lines.append("At least two batches are required for a rate comparison. No saturation determination is made.")
    else:
        ratio = trend["last_batch_to_preceding_rate_ratio"]
        relative = trend["relative_change_percent"]
        lines.extend([
            f"- Last batch: **{trend['last_batch_label']}**, "
            f"{trend['last_batch_new_features_per_100_papers']:.2f} new canonical Features per 100 papers.",
            f"- Earlier batches pooled: **{trend['preceding_batches_pooled_new_features_per_100_papers']:.2f}** per 100 papers.",
            f"- Last/earlier yield ratio: **{'not computable' if ratio is None else f'{ratio:.3f}'}**.",
            f"- Relative change: **{'not computable' if relative is None else f'{relative:+.1f}%'}**.",
            "",
            "This is a descriptive comparison only. It is not an automatic stopping rule.",
        ])
    lines.extend(["", "## Warnings", ""])
    lines.extend([f"- {item}" for item in report["warnings"]] or ["- None reported."])
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.append("")
    return "\n".join(lines)


def build_feature_saturation_report(
    registry_path: Path,
    batch_manifests: Sequence[tuple[str, Path]],
    output_dir: Path,
    *,
    allow_unassigned: bool = False,
) -> dict[str, Any]:
    """Build a read-only report from the current Registry and ordered prepared batch manifests."""
    registry_path = registry_path.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if not registry_path.is_file():
        raise FeatureSaturationError(f"Registry file does not exist: {registry_path}")
    batches, assigned_papers, batch_warnings = _load_batches(batch_manifests)

    # Share the exact writer lock used by reconciliation and export so a partially
    # written Registry snapshot cannot be observed during this read-only report.
    with exclusive_lock(registry_lock_path(registry_path)):
        registry_sha256 = sha256_file(registry_path)
        registry = read_json(registry_path)
        if not isinstance(registry.get("registry_id"), str):
            raise FeatureSaturationError("Registry has no valid registry_id.")
        report = calculate_feature_saturation_report(
            registry,
            registry_sha256,
            batches,
            assigned_papers,
            batch_warnings,
            allow_unassigned=allow_unassigned,
        )

    json_path = output_dir / "feature_saturation_report.json"
    markdown_path = output_dir / "feature_saturation_report.md"
    protected_paths = {registry_path, *(path.expanduser().resolve() for _, path in batch_manifests)}
    if json_path.resolve() in protected_paths or markdown_path.resolve() in protected_paths:
        raise FeatureSaturationError("Report outputs must not overwrite the Registry or any batch manifest.")
    output_dir.mkdir(parents=True, exist_ok=True)
    json_temp = json_path.with_name(json_path.name + ".tmp")
    markdown_temp = markdown_path.with_name(markdown_path.name + ".tmp")
    markdown = render_feature_saturation_markdown(report)
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

    report["report_files"] = {"json": str(json_path), "markdown": str(markdown_path)}
    return report


__all__ = [
    "FeatureSaturationError",
    "build_feature_saturation_report",
    "calculate_feature_saturation_report",
    "render_feature_saturation_markdown",
]
