"""Apply verified existing-schema FEATURE_ID mappings to confirmed Registry Features."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager
import time
from typing import Any

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_SCHEMA = ROOT / "contracts" / "registry.schema.json"
MAPPING_SCHEMA = ROOT / "contracts" / "existing-feature-mapping-batch.schema.json"
EXECUTION_POLICY = ROOT / "config" / "execution_policy.json"


class MappingApplyError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MappingApplyError(f"Unable to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MappingApplyError(f"Expected JSON object: {path}")
    return value


def validate_schema(value: dict[str, Any], path: Path) -> list[str]:
    if Draft202012Validator is None:
        raise MappingApplyError("Install jsonschema from requirements.txt.")
    schema = read_json(path)
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda error: list(error.absolute_path))
    return [
        f"{'/'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in errors
    ]


def stable_id(prefix: str, *parts: str, length: int = 18) -> str:
    source = "|".join(parts)
    return f"{prefix}-{hashlib.sha256(source.encode('utf-8')).hexdigest()[:length].upper()}"


def normalize_unit(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.replace("μ", "u").replace("µ", "u").replace("·", "")
    normalized = normalized.replace("−", "-")
    return re.sub(r"\s+", "", normalized)


def append_event(
    registry: dict[str, Any],
    feature_id: str,
    event_type: str,
    decision: str,
    rationale: str,
    observation_ids: list[str],
    schema_sha: str,
) -> None:
    event_id = stable_id("EVT", event_type, feature_id, decision, schema_sha)
    if any(item.get("event_id") == event_id for item in registry["decision_log"]):
        return
    registry["decision_log"].append({
        "event_id": event_id,
        "event_type": event_type,
        "subject_ids": [feature_id],
        "decision": decision,
        "rationale": rationale,
        "evidence_observation_ids": sorted(set(observation_ids)),
        "actor": "opencode_model_recommendation",
        "contract_version": "0.1",
        "created_at": utc_now(),
    })


def mapping_gate(
    decision: dict[str, Any],
    feature: dict[str, Any],
    old_feature: dict[str, Any],
    observations: dict[str, dict[str, Any]],
) -> tuple[bool, list[str]]:
    problems: list[str] = []
    assessment = decision["mapping_assessment"]
    required = (
        "quantity_equivalent",
        "definition_equivalent",
        "formula_and_normalization_compatible",
        "statistic_scope_compatible",
        "temporal_scope_compatible",
        "unit_dimension_compatible",
        "no_contradictory_evidence",
    )
    for key in required:
        if assessment.get(key) is not True:
            problems.append(f"Worker did not affirm {key}.")

    if feature.get("feature_status") != "confirmed":
        problems.append("Registry Feature is not confirmed.")
    if not (feature.get("definition") or "").strip():
        problems.append("Registry Feature has no definition.")
    if not (old_feature.get("DEFINITION") or "").strip():
        problems.append("Existing schema Feature has no definition.")

    source_observations = [
        observations[oid] for oid in feature.get("observation_ids", [])
        if oid in observations
    ]
    if not source_observations:
        problems.append("Registry Feature has no source Observations.")
    if not any(item.get("discovery_status") == "evidence_verified" for item in source_observations):
        problems.append("Registry Feature has no locally evidence-verified source Observation.")

    units_current = {
        normalize_unit(str(item.get("reported_unit")))
        for item in source_observations if item.get("reported_unit")
    }
    units_existing = {
        normalize_unit(value)
        for value in old_feature.get("UNITS_AND_DIMENSION", {}).get("reported_units", [])
        if value
    }
    if units_current and units_existing and not units_current.intersection(units_existing):
        problems.append("Reported units do not match after conservative normalization.")

    compared = set(decision.get("compared_observation_ids", []))
    if not (compared & set(feature.get("observation_ids", []))):
        problems.append("Mapping decision cites no Observation linked to the Registry Feature.")

    existing_id = decision.get("existing_feature_id")
    if not existing_id or existing_id != old_feature.get("FEATURE_ID"):
        problems.append("Selected existing FEATURE_ID does not match the supplied schema entry.")

    return not problems, problems


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


@contextmanager
def registry_lock(path: Path, timeout_seconds: float = 30.0):
    """Protect Registry read-modify-write from concurrent ingestion/mapping jobs."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    deadline = time.monotonic() + timeout_seconds
    descriptor = None
    while descriptor is None:
        try:
            descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"pid={os.getpid()} time={utc_now()}".encode("utf-8"))
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise MappingApplyError(f"Timed out waiting for Registry lock: {lock_path}")
            time.sleep(0.2)
    try:
        yield
    finally:
        os.close(descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _main_unlocked(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply Worker B recommendations mapping Registry Features to existing FEATURE_IDs.")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--existing-schema", type=Path, required=True)
    parser.add_argument("--mapping-queue", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)

    registry_path = args.registry.resolve()
    schema_path = args.existing_schema.resolve()
    registry = read_json(registry_path)
    existing_schema = read_json(schema_path)
    queue = read_json(args.mapping_queue.resolve())
    if existing_schema.get("contract_version") != "0.1":
        raise MappingApplyError("Existing schema must be normalized with import_existing_schema.py.")
    schema_sha = existing_schema.get("source_sha256")
    if queue.get("existing_schema_sha256") != schema_sha:
        raise MappingApplyError("Mapping queue refers to a different existing schema version.")
    old_by_id = {
        item["FEATURE_ID"]: item
        for item in existing_schema.get("features", [])
    }
    feature_by_id = {
        item["registry_feature_id"]: item
        for item in registry.get("canonical_features", [])
    }
    observations = {
        item["observation_id"]: item
        for item in registry.get("observations", [])
    }
    execution = read_json(EXECUTION_POLICY)
    expected_model = execution.get("workers", {}).get("worker_b", {}).get("model_ref")

    report = {
        "mapping_application_version": "0.1",
        "started_at": utc_now(),
        "registry_id": registry.get("registry_id"),
        "existing_schema_sha256": schema_sha,
        "tasks_seen": len(queue.get("tasks", [])),
        "mapping_decisions_seen": 0,
        "mappings_applied": 0,
        "new_features_kept": 0,
        "mapping_reviews": 0,
        "tasks_skipped": [],
        "decisions": [],
    }

    assigned_ids = set()
    for feature in feature_by_id.values():
        if feature.get("existing_feature_id"):
            assigned_ids.add(feature["existing_feature_id"])

    for task in queue.get("tasks", []):
        if task.get("task_status") != "pending":
            report["tasks_skipped"].append({
                "task_id": task.get("task_id"),
                "reason": task.get("task_status"),
            })
            continue
        task_dir = Path(task["output_dir"])
        state_path = task_dir / "task_state.json"
        response_path = task_dir / "response.json"
        if not state_path.is_file() or not response_path.is_file():
            report["tasks_skipped"].append({
                "task_id": task.get("task_id"),
                "reason": "missing_task_state_or_response",
            })
            continue
        state = read_json(state_path)
        if state.get("status") != "complete":
            report["tasks_skipped"].append({
                "task_id": task.get("task_id"),
                "reason": f"worker_status_{state.get('status')}",
            })
            continue
        requested_model = task.get("model_ref")
        observed_models = state.get("observed_models") or []
        if requested_model != expected_model or (observed_models and observed_models != [requested_model]):
            report["tasks_skipped"].append({
                "task_id": task.get("task_id"),
                "reason": "worker_model_mismatch",
            })
            continue

        payload = read_json(response_path)
        schema_errors = validate_schema(payload, MAPPING_SCHEMA)
        if schema_errors:
            report["tasks_skipped"].append({
                "task_id": task.get("task_id"),
                "reason": "schema_invalid",
                "errors": schema_errors[:10],
            })
            continue
        if (
            payload.get("task_id") != task.get("task_id")
            or payload.get("registry_id") != registry.get("registry_id")
            or payload.get("existing_schema_sha256") != schema_sha
        ):
            report["tasks_skipped"].append({
                "task_id": task.get("task_id"),
                "reason": "task_registry_or_schema_version_mismatch",
            })
            continue
        returned = {item.get("registry_feature_id") for item in payload.get("mapping_decisions", [])}
        expected = set(task.get("registry_feature_ids", []))
        if returned != expected:
            report["tasks_skipped"].append({
                "task_id": task.get("task_id"),
                "reason": "mapping_feature_set_mismatch",
            })
            continue

        for decision in payload.get("mapping_decisions", []):
            report["mapping_decisions_seen"] += 1
            feature_id = decision["registry_feature_id"]
            feature = feature_by_id.get(feature_id)
            if not feature:
                report["mapping_reviews"] += 1
                report["decisions"].append({
                    "registry_feature_id": feature_id,
                    "status": "unknown_registry_feature",
                })
                continue
            if feature.get("existing_feature_id"):
                report["decisions"].append({
                    "registry_feature_id": feature_id,
                    "status": "already_mapped",
                    "existing_feature_id": feature["existing_feature_id"],
                })
                continue

            status = decision["mapping_status"]
            if status == "MAP_TO_EXISTING":
                old_id = decision.get("existing_feature_id")
                old_feature = old_by_id.get(old_id)
                if not old_feature:
                    report["mapping_reviews"] += 1
                    report["decisions"].append({
                        "registry_feature_id": feature_id,
                        "status": "unknown_existing_feature_id",
                        "existing_feature_id": old_id,
                    })
                    continue
                allowed, reasons = mapping_gate(decision, feature, old_feature, observations)
                if old_id in assigned_ids:
                    allowed = False
                    reasons.append(
                        "Existing FEATURE_ID is already assigned to another active Registry Feature; merge/review first."
                    )
                if allowed:
                    feature["existing_feature_id"] = old_id
                    assigned_ids.add(old_id)
                    append_event(
                        registry,
                        feature_id,
                        "feature_mapped",
                        f"map_to_existing_feature_id:{old_id}",
                        decision["rationale"],
                        decision.get("compared_observation_ids", []),
                        schema_sha,
                    )
                    report["mappings_applied"] += 1
                    applied_status = "mapped"
                else:
                    report["mapping_reviews"] += 1
                    applied_status = "under_review"
                report["decisions"].append({
                    "registry_feature_id": feature_id,
                    "existing_feature_id": old_id,
                    "status": applied_status,
                    "reasons": reasons,
                })
            elif status == "NO_MATCH":
                append_event(
                    registry,
                    feature_id,
                    "feature_unmapped",
                    "no_semantically_equivalent_existing_feature",
                    decision["rationale"],
                    decision.get("compared_observation_ids", []),
                    schema_sha,
                )
                report["new_features_kept"] += 1
                report["decisions"].append({
                    "registry_feature_id": feature_id,
                    "status": "kept_new_registry_id",
                    "rationale": decision["rationale"],
                })
            else:
                report["mapping_reviews"] += 1
                append_event(
                    registry,
                    feature_id,
                    "feature_unmapped",
                    f"existing_schema_mapping_{status.casefold()}",
                    decision["rationale"],
                    decision.get("compared_observation_ids", []),
                    schema_sha,
                )
                report["decisions"].append({
                    "registry_feature_id": feature_id,
                    "status": "mapping_review_required",
                    "mapping_status": status,
                    "rationale": decision["rationale"],
                })

    registry_errors = validate_schema(registry, REGISTRY_SCHEMA)
    if registry_errors:
        raise MappingApplyError("Registry schema validation failed: " + " | ".join(registry_errors[:10]))

    atomic_write_json(registry_path, registry)
    report["finished_at"] = utc_now()
    report["registry_feature_count"] = len(registry.get("canonical_features", []))
    report["mapped_existing_feature_count"] = sum(
        bool(feature.get("existing_feature_id"))
        for feature in registry.get("canonical_features", [])
        if feature.get("feature_status") != "retired_merged"
    )
    report["new_registry_id_feature_count"] = sum(
        not feature.get("existing_feature_id")
        for feature in registry.get("canonical_features", [])
        if feature.get("feature_status") == "confirmed"
    )
    report["registry_decision_count"] = len(registry.get("decision_log", []))
    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps({key: value for key, value in report.items() if key != "decisions"}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--registry", type=Path, required=True)
    args, _unknown = parser.parse_known_args(argv)
    registry_path = args.registry.expanduser().resolve()
    with registry_lock(registry_path):
        return _main_unlocked(argv)


def main(argv: list[str] | None = None) -> int:
    import sys

    arguments = list(argv) if argv is not None else sys.argv[1:]
    if "-h" in arguments or "--help" in arguments:
        return _main_unlocked(arguments)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--registry", type=Path, required=True)
    args, _unknown = parser.parse_known_args(arguments)
    registry_path = args.registry.expanduser().resolve()
    with registry_lock(registry_path):
        return _main_unlocked(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
