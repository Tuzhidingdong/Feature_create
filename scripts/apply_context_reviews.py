"""Apply validated Worker B context-only decisions to result records.

This script may add sample/condition/time/protocol context facts and resolve the
linked unresolved item. It must never alter a result value, Feature identity,
definition, method, unit, or original evidence reference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class ContextApplicationError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContextApplicationError(f"Unable to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContextApplicationError(f"Expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContextApplicationError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
            if not isinstance(item, dict):
                raise ContextApplicationError(f"Expected JSON object at {path}:{line_no}")
            rows.append(item)
    return rows


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    os.replace(temp, path)


def atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


@contextmanager
def exclusive_lock(path: Path, timeout_seconds: float = 30.0) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    descriptor = None
    while descriptor is None:
        try:
            descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"pid={os.getpid()} time={utc_now()}".encode("utf-8"))
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ContextApplicationError(f"Timed out waiting for context review lock: {path}")
            time.sleep(0.2)
    try:
        yield
    finally:
        os.close(descriptor)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_context_artifact_hashes(
    task: dict[str, Any],
    validation: dict[str, Any],
    validation_item: dict[str, Any],
) -> str | None:
    """Re-check every immutable input that the evidence validator hashed."""
    observed = validation_item.get("artifact_sha256")
    if not isinstance(observed, dict):
        return "validated_context_input_hashes_missing"

    validation_manifest_path = Path(str(validation.get("manifest_path") or "")).resolve()
    task_manifest_path = Path(str(task.get("source_manifest_path") or "")).resolve()
    if not validation.get("manifest_sha256") or validation_manifest_path != task_manifest_path:
        return "source_manifest_identity_missing_or_mismatched"

    checks = [
        ("source_manifest", task_manifest_path, task.get("source_manifest_sha256")),
        (
            "context_evidence_manifest",
            Path(str(task.get("context_evidence_manifest_path") or "")).resolve(),
            task.get("context_evidence_manifest_sha256"),
        ),
        ("bundle", Path(str(task.get("bundle_path") or "")).resolve(), task.get("bundle_sha256")),
        ("prompt", Path(str(task.get("prompt_path") or "")).resolve(), task.get("prompt_sha256")),
    ]
    if observed.get("source_manifest") != validation.get("manifest_sha256"):
        return "source_manifest_hash_changed_after_validation"

    for label, path, task_expected in checks:
        if not path.is_file():
            return f"context_artifact_missing:{label}"
        actual = sha256_file(path)
        if not task_expected or actual != task_expected:
            return f"context_artifact_changed_after_queue_build:{label}"
        if observed.get(label) != actual:
            return f"context_artifact_changed_after_evidence_validation:{label}"

    expected_attachments = task.get("attachment_sha256") or {}
    observed_attachments = observed.get("attachments")
    if not isinstance(observed_attachments, dict):
        return "validated_visual_attachment_hashes_missing"
    for attachment, expected_hash in expected_attachments.items():
        path = Path(str(attachment)).resolve()
        if not path.is_file():
            return "context_visual_attachment_missing"
        actual = sha256_file(path)
        if actual != expected_hash:
            return "context_visual_attachment_changed_after_queue_build"
        if observed_attachments.get(str(path)) != actual:
            return "context_visual_attachment_changed_after_evidence_validation"
    if set(observed_attachments) != {str(Path(item).resolve()) for item in expected_attachments}:
        return "context_visual_attachment_inventory_changed"
    return None


def _target_field(context_role: str) -> str:
    return {
        "sample_identity": "sample_identity",
        "condition": "condition_facts",
        "time_or_cycle": "time_cycle_facts",
        "protocol": "protocol_facts",
        "statistical_scope": "other_context_facts",
        "other": "other_context_facts",
    }[context_role]


def _context_fact_for_record(
    fact: dict[str, Any],
    task: dict[str, Any],
) -> dict[str, Any]:
    return {
        "raw_label": fact["raw_label"],
        "raw_value": fact["raw_value"],
        "unit": fact.get("unit"),
        "context_role": fact["context_role"],
        "evidence_refs": fact["evidence_refs"],
        "cross_chunk_review_task_id": task["task_id"],
        "origin_unresolved_id": task["unresolved_id"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply validated targeted cross-chunk context decisions without changing result values."
    )
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--unresolved", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)

    queue = read_json(args.queue.resolve())
    run_dir = args.run_dir.resolve()
    validation_path = run_dir / "evidence_validation.json"
    if not validation_path.is_file():
        print(f"BLOCKED: evidence validation report not found: {validation_path}", file=sys.stderr)
        return 2
    validation = read_json(validation_path)
    validation_by_task = {
        item.get("task_id"): item for item in validation.get("tasks", [])
    }

    records_path = args.records.resolve()
    unresolved_path = args.unresolved.resolve()
    records_by_id = {item.get("record_id"): item for item in read_jsonl(records_path)}
    unresolved_items = read_jsonl(unresolved_path)
    unresolved_by_id = {item.get("unresolved_id"): item for item in unresolved_items}

    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    lock_path = records_path.with_suffix(records_path.suffix + ".context.lock")

    with exclusive_lock(lock_path):
        for task in queue.get("tasks", []):
            task_id = str(task.get("task_id") or "")
            if task.get("task_status") != "pending":
                skipped.append({"task_id": task_id, "reason": str(task.get("task_status"))})
                continue
            output_dir = Path(str(task.get("output_dir") or "")).resolve()
            state_path = output_dir / "task_state.json"
            response_path = output_dir / "response.json"
            if not state_path.is_file() or not response_path.is_file():
                skipped.append({"task_id": task_id, "reason": "missing_worker_state_or_response"})
                continue
            state = read_json(state_path)
            if state.get("status") != "complete":
                skipped.append({"task_id": task_id, "reason": f"worker_status_{state.get('status', 'unknown')}"})
                continue
            if state.get("requested_model") != task.get("model_ref"):
                skipped.append({"task_id": task_id, "reason": "requested_model_mismatch"})
                continue
            observed = state.get("observed_models") or []
            if observed and observed != [task.get("model_ref")]:
                skipped.append({"task_id": task_id, "reason": "observed_model_mismatch"})
                continue

            validation_item = validation_by_task.get(task_id, {})
            if validation_item.get("status") != "evidence_checked":
                skipped.append({"task_id": task_id, "reason": "evidence_validation_not_passed"})
                continue
            expected_response_sha256 = validation_item.get("response_sha256")
            actual_response_sha256 = hashlib.sha256(response_path.read_bytes()).hexdigest()
            if not expected_response_sha256 or actual_response_sha256 != expected_response_sha256:
                skipped.append({"task_id": task_id, "reason": "response_file_changed_after_evidence_validation"})
                continue
            artifact_problem = verify_context_artifact_hashes(task, validation, validation_item)
            if artifact_problem:
                skipped.append({"task_id": task_id, "reason": artifact_problem})
                continue
            payload = read_json(response_path)
            if (
                payload.get("task_id") != task_id
                or payload.get("paper_id") != task.get("paper_id")
                or payload.get("unresolved_id") != task.get("unresolved_id")
                or payload.get("record_id") != task.get("record_id")
            ):
                skipped.append({"task_id": task_id, "reason": "response_identity_mismatch"})
                continue

            record = records_by_id.get(task.get("record_id"))
            unresolved = unresolved_by_id.get(task.get("unresolved_id"))
            if not record or not unresolved:
                skipped.append({"task_id": task_id, "reason": "linked_record_or_unresolved_item_missing"})
                continue
            if record.get("paper_id") != task.get("paper_id") or unresolved.get("paper_id") != task.get("paper_id"):
                skipped.append({"task_id": task_id, "reason": "paper_id_mismatch"})
                continue
            if record.get("worker_task_id") != task.get("origin_worker_task_id"):
                skipped.append({"task_id": task_id, "reason": "origin_worker_task_mismatch"})
                continue

            decision = payload.get("decision")
            if decision == "association_confirmed":
                facts = payload.get("context_facts") or []
                if not facts:
                    skipped.append({"task_id": task_id, "reason": "confirmed_decision_has_no_context_facts"})
                    continue
                original_value_snapshot = {
                    key: record.get(key)
                    for key in (
                        "record_id", "paper_id", "doi", "raw_value", "numeric_value",
                        "raw_feature_name", "registry_feature_id", "observation_id",
                        "reported_unit", "result_type", "value_extraction_status",
                        "extraction_status", "statistic_label", "replicate_count",
                        "measurement_or_calculation_method", "source_id", "source_type",
                        "source_location", "evidence_refs", "evidence_status",
                        "evidence_verification", "context_notes", "worker_task_id",
                        "worker_model", "created_at",
                    )
                }
                added_fact_count = 0
                enriched_fact_count = 0
                for fact in facts:
                    target_field = _target_field(str(fact["context_role"]))
                    target_rows = record.setdefault(target_field, [])
                    normalized = _context_fact_for_record(fact, task)
                    existing_fact = next(
                        (
                            item for item in target_rows
                            if isinstance(item, dict)
                            and item.get("raw_label") == normalized["raw_label"]
                            and item.get("raw_value") == normalized["raw_value"]
                            and item.get("unit") == normalized.get("unit")
                            and item.get("context_role") == normalized["context_role"]
                        ),
                        None,
                    )
                    if existing_fact is None:
                        target_rows.append(normalized)
                        added_fact_count += 1
                        continue

                    # Same fact already exists from first-pass extraction: keep one
                    # row and merge the newly verified cross-chunk provenance.
                    existing_refs = existing_fact.setdefault("evidence_refs", [])
                    seen_refs = {_canonical(ref) for ref in existing_refs if isinstance(ref, dict)}
                    refs_added = 0
                    for ref in normalized["evidence_refs"]:
                        ref_key = _canonical(ref)
                        if ref_key not in seen_refs:
                            existing_refs.append(ref)
                            seen_refs.add(ref_key)
                            refs_added += 1
                    task_ids = existing_fact.setdefault("cross_chunk_review_task_ids", [])
                    if task["task_id"] not in task_ids:
                        task_ids.append(task["task_id"])
                    unresolved_ids = existing_fact.setdefault("origin_unresolved_ids", [])
                    if task["unresolved_id"] not in unresolved_ids:
                        unresolved_ids.append(task["unresolved_id"])
                    if refs_added:
                        enriched_fact_count += 1

                # Assert this stage cannot alter the result's identity or numerical value.
                for key, original in original_value_snapshot.items():
                    if record.get(key) != original:
                        raise ContextApplicationError(
                            f"Context review unexpectedly modified protected result field {key!r} on record {record.get('record_id')}."
                        )
                unresolved["resolution_status"] = "association_confirmed"
                unresolved["resolution_task_id"] = task_id
                unresolved["resolution_rationale"] = payload.get("rationale")
                unresolved["resolution_evidence_refs"] = payload.get("review_evidence_refs", [])
                unresolved["resolved_at"] = utc_now()
            elif decision == "no_association":
                unresolved["resolution_status"] = "no_association"
                unresolved["resolution_task_id"] = task_id
                unresolved["resolution_rationale"] = payload.get("rationale")
                unresolved["resolution_evidence_refs"] = payload.get("review_evidence_refs", [])
                unresolved["resolved_at"] = utc_now()
            elif decision in {"insufficient_evidence", "conflicting_evidence"}:
                unresolved["resolution_status"] = decision
                unresolved["resolution_task_id"] = task_id
                unresolved["resolution_rationale"] = payload.get("rationale")
                unresolved["resolution_evidence_refs"] = payload.get("review_evidence_refs", [])
                unresolved["last_reviewed_at"] = utc_now()
            else:
                skipped.append({"task_id": task_id, "reason": f"unsupported_decision_{decision}"})
                continue

            unresolved_history = unresolved.setdefault("resolution_history", [])
            if not any(
                item.get("task_id") == task_id
                for item in unresolved_history if isinstance(item, dict)
            ):
                unresolved_history.append({
                    "task_id": task_id,
                    "decision": decision,
                    "rationale": payload.get("rationale"),
                    "review_evidence_refs": payload.get("review_evidence_refs", []),
                    "reviewed_at": utc_now(),
                })

            history = record.setdefault("context_review_history", [])
            if not any(item.get("task_id") == task_id for item in history if isinstance(item, dict)):
                history.append({
                    "task_id": task_id,
                    "unresolved_id": task["unresolved_id"],
                    "decision": decision,
                    "rationale": payload.get("rationale"),
                    "review_evidence_refs": payload.get("review_evidence_refs", []),
                    "context_facts_added": added_fact_count if decision == "association_confirmed" else 0,
                    "context_facts_enriched": enriched_fact_count if decision == "association_confirmed" else 0,
                    "reviewed_at": utc_now(),
                })

            for issue in record.get("unresolved_issues", []):
                if isinstance(issue, dict) and issue.get("unresolved_id") == task.get("unresolved_id"):
                    issue["resolution_status"] = unresolved.get("resolution_status")
                    issue["resolution_task_id"] = task_id
                    issue["resolution_rationale"] = payload.get("rationale")
                    issue["resolution_evidence_refs"] = payload.get("review_evidence_refs", [])
                    issue["resolved_at"] = unresolved.get("resolved_at")
                    issue["last_reviewed_at"] = unresolved.get("last_reviewed_at")

            applied.append({
                "task_id": task_id,
                "record_id": record.get("record_id"),
                "unresolved_id": unresolved.get("unresolved_id"),
                "decision": decision,
                "context_facts_added": added_fact_count if decision == "association_confirmed" else 0,
                "context_facts_enriched": enriched_fact_count if decision == "association_confirmed" else 0,
            })

        atomic_write_jsonl(records_path, list(records_by_id.values()))
        atomic_write_jsonl(unresolved_path, list(unresolved_by_id.values()))

    report = {
        "application_version": "0.1",
        "applied_at": utc_now(),
        "records_path": str(records_path),
        "unresolved_path": str(unresolved_path),
        "tasks_considered": len(queue.get("tasks", [])),
        "tasks_applied": len(applied),
        "tasks_skipped": len(skipped),
        "decisions": {
            decision: sum(1 for item in applied if item["decision"] == decision)
            for decision in (
                "association_confirmed", "no_association",
                "insufficient_evidence", "conflicting_evidence",
            )
        },
        "context_facts_added": sum(item["context_facts_added"] for item in applied),
        "context_facts_enriched": sum(item["context_facts_enriched"] for item in applied),
        "applied_tasks": applied,
        "skipped_tasks": skipped,
        "protected_result_fields": [
            "record_id", "paper_id", "doi", "raw_value", "numeric_value",
            "raw_feature_name", "registry_feature_id", "observation_id",
            "reported_unit", "result_type", "value_extraction_status",
            "extraction_status", "statistic_label", "replicate_count",
            "measurement_or_calculation_method", "source_id", "source_type",
            "source_location", "evidence_refs", "evidence_status",
            "evidence_verification", "context_notes", "worker_task_id",
            "worker_model", "created_at",
        ],
    }
    atomic_write_json(args.report.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
