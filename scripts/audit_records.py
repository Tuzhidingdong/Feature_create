"""Compute source-review, extraction, context, Feature, and model-cost audit metrics.

All counts are derived from the prepared source manifest, task outputs, evidence
validation, Registry, record JSONL, and review queues. The script never asks a
model to self-report corpus totals.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditError(RuntimeError):
    pass


def read_json(path: Path, required: bool = True) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise AuditError(f"Required input file does not exist: {path}")
        return {}
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"Unable to read JSON {path}: {exc}") from exc
    if not isinstance(result, dict):
        raise AuditError(f"Expected JSON object in {path}")
    return result


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AuditError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            if isinstance(value, dict):
                rows.append(value)
    return rows


def task_state(task: dict[str, Any]) -> dict[str, Any]:
    task_dir = Path(task.get("output_dir", ""))
    path = task_dir / "task_state.json"
    return read_json(path, required=False)


def coverage_for_task(task: dict[str, Any]) -> dict[str, str]:
    """Return scope-level states for new queues, with source-level legacy fallback."""
    path = Path(task.get("output_dir", "")) / "response.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    role = task.get("worker_role")
    rows = payload.get("coverage", []) if role == "source_discovery" else payload.get("coverage_audit", [])
    expected = {
        str(scope.get("scope_id")): str(scope.get("source_id"))
        for scope in task.get("source_scopes", [])
        if scope.get("scope_id") and scope.get("source_id")
    }
    counts: Counter[str] = Counter()
    result: dict[str, str] = {}
    for row in rows:
        source_id = row.get("source_id")
        status = row.get("status")
        if not source_id or status is None:
            continue
        scope_id = row.get("scope_id")
        if expected:
            if not scope_id or expected.get(str(scope_id)) != str(source_id):
                continue
            key = str(scope_id)
        else:
            # Legacy queue: one coverage state per source in the chunk.
            key = str(source_id)
        counts[key] += 1
        result[key] = str(status)
    for key, count in counts.items():
        if count > 1:
            result[key] = "duplicate_coverage_entry"
    return result


def aggregate_chunk_coverage(
    assignments: dict[str, list[str]],
    success_status: str,
) -> dict[str, str]:
    """A source is complete only when every assigned chunk for that worker is complete."""
    result: dict[str, str] = {}
    partial_statuses = {"partially_reviewed", "uncertain", "needs_targeted_review", "duplicate_coverage_entry"}
    for source_id, statuses in assignments.items():
        if statuses and all(status == success_status for status in statuses):
            result[source_id] = success_status
        elif any(status in partial_statuses or status == success_status for status in statuses):
            result[source_id] = "partially_reviewed"
        else:
            result[source_id] = "not_reviewed"
    return result


def context_issue_count(record: dict[str, Any]) -> int:
    explicit = record.get("unresolved_issues") or []
    count = 0
    for issue in explicit:
        if isinstance(issue, dict):
            issue_type = str(issue.get("type") or issue.get("item_type") or "").casefold()
            description = str(issue.get("description") or "").casefold()
            if (
                "context" in issue_type
                or "sample" in issue_type
                or "condition" in issue_type
                or "context" in description
                or "not reported" in description
                or "unknown" in description
            ):
                count += 1
        elif isinstance(issue, str) and any(
            token in issue.casefold()
            for token in ("context", "sample identity", "condition", "not reported", "unknown")
        ):
            count += 1
    context_notes = record.get("context_notes")
    if isinstance(context_notes, str) and context_notes.strip():
        count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a deterministic Feature_create run audit.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--review-queue", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--reconciliation-queue", type=Path)
    parser.add_argument("--mapping-queue", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    manifest = read_json(args.manifest.resolve())
    queue = read_json(args.queue.resolve())
    registry = read_json(args.registry.resolve())
    sources = manifest.get("sources", [])
    papers = manifest.get("papers", [])
    records = read_jsonl(args.records.resolve())
    review_rows = read_jsonl(args.review_queue.resolve())
    tasks = queue.get("tasks", [])
    reconciliation_queue = (
        read_json(args.reconciliation_queue.resolve(), required=False)
        if args.reconciliation_queue else {}
    )
    mapping_queue = read_json(args.mapping_queue.resolve(), required=False) if args.mapping_queue else {}
    reconciliation_tasks = reconciliation_queue.get("tasks", [])
    mapping_tasks = mapping_queue.get("tasks", [])
    all_tasks = list(tasks) + list(reconciliation_tasks) + list(mapping_tasks)

    tasks_by_role: dict[str, list[dict[str, Any]]] = {}
    for task in all_tasks:
        tasks_by_role.setdefault(str(task.get("worker_role")), []).append(task)

    source_a_status: dict[str, str] = {}
    source_b_status: dict[str, str] = {}
    coverage_assignments: dict[str, dict[str, list[str]]] = {
        "source_discovery": {},
        "feature_audit": {},
    }
    task_status_counts: dict[str, Counter[str]] = {}
    model_refs: dict[str, set[str]] = {}
    requested_model_refs: dict[str, set[str]] = {}
    model_mismatch_tasks: dict[str, list[str]] = {}
    model_unknown_tasks: dict[str, list[str]] = {}
    costs: dict[str, list[float]] = {}
    unknown_cost_tasks: dict[str, list[str]] = {}
    usage: dict[str, list[dict[str, int]]] = {}
    chunk_role_complete: dict[str, dict[str, bool]] = {}
    chunk_to_paper: dict[str, str] = {}

    for task in all_tasks:
        role = task.get("worker_role")
        state = task_state(task)
        status = str(
            state.get("status")
            or (task.get("task_status") if task.get("task_status") != "pending" else "not_started")
        )
        task_status_counts.setdefault(str(role), Counter())[status] += 1
        coverage = coverage_for_task(task) if status == "complete" else {}
        if role in {"source_discovery", "feature_audit"}:
            required_status = "fully_reviewed" if role == "source_discovery" else "reviewed"
            expected_scopes = task.get("source_scopes") or []
            if expected_scopes:
                for scope in expected_scopes:
                    source_id = str(scope.get("source_id") or "")
                    scope_id = str(scope.get("scope_id") or "")
                    if not source_id or not scope_id:
                        continue
                    coverage_status = coverage.get(scope_id, "missing_chunk_coverage")
                    coverage_assignments[str(role)].setdefault(source_id, []).append(coverage_status)
                current_chunk_complete = bool(
                    status == "complete"
                    and expected_scopes
                    and all(
                        coverage.get(str(scope.get("scope_id"))) == required_status
                        for scope in expected_scopes
                    )
                )
            else:
                # Backward compatibility for queues created before scope-level coverage.
                expected_sources = task.get("source_ids") or list(coverage)
                for source_id in expected_sources:
                    coverage_status = coverage.get(source_id, "missing_chunk_coverage")
                    coverage_assignments[str(role)].setdefault(str(source_id), []).append(coverage_status)
                current_chunk_complete = bool(
                    status == "complete"
                    and expected_sources
                    and all(coverage.get(source_id) == required_status for source_id in expected_sources)
                )
        else:
            current_chunk_complete = False

        chunk_id = task.get("chunk_id")
        if chunk_id and role in {"source_discovery", "feature_audit"}:
            chunk_to_paper[str(chunk_id)] = str(task.get("paper_id") or "")
            chunk_role_complete.setdefault(str(chunk_id), {})[str(role)] = current_chunk_complete

        role_key = str(role)
        requested_model = task.get("model_ref")
        if requested_model:
            requested_model_refs.setdefault(role_key, set()).add(str(requested_model))
        observed_models = state.get("observed_models") or []
        model_refs.setdefault(role_key, set()).update(observed_models)
        if observed_models and requested_model and observed_models != [requested_model]:
            model_mismatch_tasks.setdefault(role_key, []).append(str(task.get("task_id")))
        elif not observed_models and status == "complete":
            model_unknown_tasks.setdefault(role_key, []).append(str(task.get("task_id")))
        if isinstance(state.get("reported_cost"), (int, float)):
            costs.setdefault(role_key, []).append(float(state["reported_cost"]))
        else:
            unknown_cost_tasks.setdefault(role_key, []).append(str(task.get("task_id")))
        if isinstance(state.get("tokens"), dict):
            usage.setdefault(str(role), []).append(state["tokens"])

    source_a_status = aggregate_chunk_coverage(
        coverage_assignments["source_discovery"], "fully_reviewed"
    )
    source_b_status = aggregate_chunk_coverage(
        coverage_assignments["feature_audit"], "reviewed"
    )
    source_by_id = {source["source_id"]: source for source in sources}
    a_full = {sid for sid, status in source_a_status.items() if status == "fully_reviewed"}
    b_full = {sid for sid, status in source_b_status.items() if status == "reviewed"}
    fully_reviewed_both = a_full & b_full
    sources_with_any_review = {
        sid for sid in set(source_a_status) | set(source_b_status)
        if source_a_status.get(sid) != "not_reviewed"
        or source_b_status.get(sid) != "not_reviewed"
    }
    partially_reviewed = {
        sid for sid in sources_with_any_review
        if source_a_status.get(sid) != "fully_reviewed"
        or source_b_status.get(sid) != "reviewed"
    }
    sources_never_reviewed = set(source_by_id) - sources_with_any_review

    chunk_ids = sorted(chunk_to_paper)
    chunks_full_both = {
        chunk_id for chunk_id in chunk_ids
        if chunk_role_complete.get(chunk_id, {}).get("source_discovery", False)
        and chunk_role_complete.get(chunk_id, {}).get("feature_audit", False)
    }
    chunk_counts_by_paper: dict[str, dict[str, int]] = {}
    for chunk_id in chunk_ids:
        paper_id = chunk_to_paper.get(chunk_id) or "unknown"
        item = chunk_counts_by_paper.setdefault(
            paper_id, {"planned_chunks": 0, "fully_reviewed_by_both": 0, "partial_or_missing": 0}
        )
        item["planned_chunks"] += 1
        if chunk_id in chunks_full_both:
            item["fully_reviewed_by_both"] += 1
        else:
            item["partial_or_missing"] += 1

    inaccessible = {
        sid for sid, source in source_by_id.items()
        if str(source.get("parse_status", "")).startswith(("dependency_missing", "parse_error"))
        or source.get("parse_status") in {"unsupported", "not_accessible"}
    }
    inaccessible_si = {
        sid for sid in inaccessible
        if source_by_id[sid].get("source_role") == "si"
    }
    fully_reviewed_main = {
        sid for sid in fully_reviewed_both
        if source_by_id.get(sid, {}).get("source_role") == "main"
    }
    fully_reviewed_si = {
        sid for sid in fully_reviewed_both
        if source_by_id.get(sid, {}).get("source_role") == "si"
    }

    paper_by_id = {paper["paper_id"]: paper for paper in papers}
    paper_fully_reviewed = 0
    paper_partial_reviewed = 0
    for paper in papers:
        source_ids = set(paper.get("source_ids", []))
        if source_ids and source_ids.issubset(fully_reviewed_both):
            paper_fully_reviewed += 1
        elif source_ids & sources_with_any_review:
            paper_partial_reviewed += 1

    record_status_counts = Counter(str(item.get("extraction_status", "not_set")) for item in records)
    result_type_counts = Counter(str(item.get("result_type", "not_set")) for item in records)
    evidence_status_counts = Counter(str(item.get("evidence_status", "not_set")) for item in records)
    feature_status_counts = Counter(
        str(item.get("feature_status", "not_set"))
        for item in registry.get("canonical_features", [])
        if item.get("feature_status") != "retired_merged"
    )
    active_features = [
        item for item in registry.get("canonical_features", [])
        if item.get("feature_status") != "retired_merged"
    ]
    mapped_features = sum(1 for item in active_features if item.get("existing_feature_id"))
    new_id_features = sum(1 for item in active_features if not item.get("existing_feature_id"))

    explicit_context_unresolved = sum(context_issue_count(record) for record in records)
    records_with_context_notes = sum(
        1 for record in records
        if isinstance(record.get("context_notes"), str) and record["context_notes"].strip()
    )
    pending_pairs = [
        row for row in review_rows
        if row.get("decision_status") in {"pending_semantic_review", "under_review", "blocked"}
    ]
    mapping_queue = read_json(args.mapping_queue.resolve(), required=False) if args.mapping_queue else {}
    mapping_task_states = []
    for task in mapping_queue.get("tasks", []):
        state = task_state(task)
        mapping_task_states.append(str(state.get("status", "not_started")))

    worker_usage: dict[str, Any] = {}
    for role in sorted(set(task_status_counts) | set(model_refs) | set(requested_model_refs)):
        token_rows = usage.get(role, [])
        summed_tokens = {
            key: sum(int(row.get(key) or 0) for row in token_rows)
            for key in ("input", "output", "reasoning", "cache_read", "cache_write")
        } if token_rows else None
        cost_rows = costs.get(role, [])
        worker_usage[role] = {
            "task_status_counts": dict(task_status_counts.get(role, {})),
            "requested_model_ids": sorted(requested_model_refs.get(role, set())),
            "observed_model_ids": sorted(model_refs.get(role, set())),
            "model_mismatch_task_ids": sorted(model_mismatch_tasks.get(role, [])),
            "model_not_exposed_task_ids": sorted(model_unknown_tasks.get(role, [])),
            "reported_cost_sum": sum(cost_rows) if cost_rows else None,
            "reported_cost_task_count": len(cost_rows),
            "cost_not_exposed_task_ids": sorted(unknown_cost_tasks.get(role, [])),
            "paid_cost_detected": any(value > 0 for value in cost_rows),
            "token_usage_sum": summed_tokens,
            "usage_task_count": len(token_rows),
        }

    report = {
        "audit_version": "0.1",
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "corpus_fingerprint": manifest.get("corpus_fingerprint"),
        "source_inventory": {
            "upstream_source_kind": manifest.get("source_kind"),
            "upstream_artifact": manifest.get("upstream_artifact"),
            "upstream_artifact_sha256": manifest.get("upstream_artifact_sha256"),
            "upstream_csv_row_count": manifest.get("upstream_row_count"),
            "source_count": len(sources),
            "paper_count": len(papers),
            "main_source_count": sum(1 for source in sources if source.get("source_role") == "main"),
            "si_source_count": sum(1 for source in sources if source.get("source_role") == "si"),
            "visual_asset_count": sum(1 for source in sources if source.get("is_visual_asset")),
            "parse_failure_count": len(inaccessible),
            "inaccessible_si_count": len(inaccessible_si),
            "unsupported_or_unparsed_source_ids": sorted(inaccessible),
        },
        "review_coverage": {
            "main_sources_fully_reviewed_by_both": len(fully_reviewed_main),
            "si_sources_fully_reviewed_by_both": len(fully_reviewed_si),
            "sources_fully_reviewed_by_worker_a": len(a_full),
            "sources_fully_reviewed_by_worker_b": len(b_full),
            "sources_fully_reviewed_by_both": len(fully_reviewed_both),
            "sources_partially_reviewed_or_one_pass_missing": len(partially_reviewed),
            "sources_not_reviewed": len(sources_never_reviewed),
            "papers_fully_reviewed_by_both": paper_fully_reviewed,
            "papers_partially_reviewed": paper_partial_reviewed,
            "inaccessible_source_ids": sorted(inaccessible),
            "source_ids_not_reviewed": sorted(sources_never_reviewed),
        },
        "result_extraction": {
            "result_record_count": len(records),
            "extraction_status_counts": dict(record_status_counts),
            "result_type_counts": dict(result_type_counts),
            "evidence_status_counts": dict(evidence_status_counts),
            "records_with_context_notes": records_with_context_notes,
            "explicit_unresolved_context_items": explicit_context_unresolved,
        },
        "feature_registry": {
            "active_canonical_feature_count": len(active_features),
            "feature_status_counts": dict(feature_status_counts),
            "mapped_existing_feature_count": mapped_features,
            "new_registry_id_feature_count": new_id_features,
            "retired_merged_feature_count": sum(
                1 for item in registry.get("canonical_features", [])
                if item.get("feature_status") == "retired_merged"
            ),
            "candidate_pair_count": len(review_rows),
            "pending_or_under_review_pair_count": len(pending_pairs),
        },
        "worker_task_totals": {
            "initial_discovery_task_count": len(tasks),
            "cross_paper_reconciliation_task_count": len(reconciliation_tasks),
            "existing_schema_mapping_task_count": len(mapping_tasks),
            "all_worker_task_count": len(all_tasks),
            "planned_paper_chunk_count": len(chunk_ids),
            "chunks_fully_reviewed_by_both": len(chunks_full_both),
            "chunks_partial_or_missing": len(chunk_ids) - len(chunks_full_both),
        },
        "paper_chunk_coverage": chunk_counts_by_paper,
        "existing_schema_mapping": {
            "mapping_task_count": len(mapping_queue.get("tasks", [])),
            "mapping_task_status_counts": dict(Counter(mapping_task_states)),
        } if args.mapping_queue else None,
        "model_usage_and_cost": worker_usage,
        "interpretation": {
            "fully_reviewed_requires_both_independent_workers": True,
            "missing_usage_or_cost_metadata_is_unknown_not_zero": True,
            "counts_are_derived_from_local_files_not_model_self_reports": True,
        },
    }
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
