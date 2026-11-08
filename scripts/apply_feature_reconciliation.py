"""Apply Worker B semantic recommendations to the Global Feature Registry.

Merges are deliberately conservative. A model's SAME_FEATURE recommendation is
not enough by itself: Python checks evidence coverage, definitions, explicit
equivalence gates, raw unit compatibility, and conflicting semantic signatures.
"""

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
from typing import Any

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None  # type: ignore[assignment]

try:
    from .reconcile_features import build_feature_pair_queue
except ImportError:
    from reconcile_features import build_feature_pair_queue


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_SCHEMA = ROOT / "contracts" / "registry.schema.json"
DECISION_SCHEMA = ROOT / "contracts" / "feature-reconciliation-batch.schema.json"
EXECUTION_POLICY = ROOT / "config" / "execution_policy.json"


class ApplyDecisionError(RuntimeError):
    """Raised when a reconciliation decision cannot be applied safely."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplyDecisionError(f"Unable to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ApplyDecisionError(f"Expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ApplyDecisionError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
            if not isinstance(row, dict):
                raise ApplyDecisionError(f"JSONL row at {path}:{line_no} is not an object.")
            rows.append(row)
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


def stable_id(prefix: str, *parts: str, length: int = 18) -> str:
    material = "|".join(parts)
    return f"{prefix}-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:length].upper()}"


def normalize_unit(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.replace("μ", "u").replace("µ", "u")
    normalized = normalized.replace("·", "")
    normalized = re.sub(r"\s+", "", normalized)
    normalized = normalized.replace("**", "^")
    normalized = normalized.replace("−", "-")
    return normalized


def normalize_signature(value: Any) -> str:
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    normalized = normalized.replace("μ", "u").replace("µ", "u")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def validate_json_schema(value: dict[str, Any], schema_path: Path) -> list[str]:
    if Draft202012Validator is None:
        raise ApplyDecisionError("Install jsonschema from requirements.txt.")
    schema = read_json(schema_path)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda error: list(error.absolute_path),
    )
    return [
        f"{'/'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in errors
    ]


def append_event(
    registry: dict[str, Any],
    event_type: str,
    subject_ids: list[str],
    decision: str,
    rationale: str,
    evidence_observation_ids: list[str],
) -> None:
    event_id = stable_id(
        "EVT",
        event_type,
        *sorted(subject_ids),
        decision,
        rationale,
    )
    if any(item.get("event_id") == event_id for item in registry["decision_log"]):
        return
    registry["decision_log"].append({
        "event_id": event_id,
        "event_type": event_type,
        "subject_ids": subject_ids,
        "decision": decision,
        "rationale": rationale,
        "evidence_observation_ids": sorted(set(evidence_observation_ids)),
        "actor": "opencode_model_recommendation",
        "contract_version": "0.1",
        "created_at": utc_now(),
    })


def side_for_feature(pair: dict[str, Any], feature_id: str) -> dict[str, Any] | None:
    if pair.get("registry_feature_a_id") == feature_id:
        return pair.get("feature_a")
    if pair.get("registry_feature_b_id") == feature_id:
        return pair.get("feature_b")
    return None


def merge_gate(
    decision: dict[str, Any],
    pair: dict[str, Any],
    registry: dict[str, Any],
    observations: dict[str, dict[str, Any]],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    feature_a = next(
        (item for item in registry["canonical_features"]
         if item["registry_feature_id"] == pair["registry_feature_a_id"]),
        None,
    )
    feature_b = next(
        (item for item in registry["canonical_features"]
         if item["registry_feature_id"] == pair["registry_feature_b_id"]),
        None,
    )
    if not feature_a or not feature_b:
        return False, ["one or both Registry Features no longer exist"]
    if feature_a.get("feature_status") == "retired_merged" or feature_b.get("feature_status") == "retired_merged":
        return False, ["one or both candidate Features have already been merged"]

    assessment = decision["equivalence_assessment"]
    boolean_fields = (
        "quantity_equivalent",
        "definition_equivalent",
        "formula_and_normalization_compatible",
        "statistic_scope_compatible",
        "temporal_scope_compatible",
        "unit_dimension_compatible",
        "no_contradictory_evidence",
    )
    for field in boolean_fields:
        if assessment.get(field) is not True:
            reasons.append(f"equivalence assessment did not affirm {field}")

    if not (feature_a.get("definition") or "").strip():
        reasons.append("Feature A has no source-backed definition")
    if not (feature_b.get("definition") or "").strip():
        reasons.append("Feature B has no source-backed definition")

    obs_a = [
        observations[oid] for oid in feature_a.get("observation_ids", [])
        if oid in observations
    ]
    obs_b = [
        observations[oid] for oid in feature_b.get("observation_ids", [])
        if oid in observations
    ]
    if not obs_a or not obs_b:
        reasons.append("one or both Features have no linked Observations")

    verified_a = any(item.get("discovery_status") == "evidence_verified" for item in obs_a)
    verified_b = any(item.get("discovery_status") == "evidence_verified" for item in obs_b)
    if not verified_a:
        reasons.append("Feature A has no locally evidence-verified Observation")
    if not verified_b:
        reasons.append("Feature B has no locally evidence-verified Observation")

    units_a = {
        normalize_unit(str(item.get("reported_unit")))
        for item in obs_a if item.get("reported_unit")
    }
    units_b = {
        normalize_unit(str(item.get("reported_unit")))
        for item in obs_b if item.get("reported_unit")
    }
    if units_a and units_b and not units_a.intersection(units_b):
        reasons.append("reported unit strings do not match after conservative normalization")

    signature_a = feature_a.get("semantic_signature") or {}
    signature_b = feature_b.get("semantic_signature") or {}
    for field in (
        "physical_or_statistical_quantity",
        "formula",
        "numerator",
        "denominator",
        "normalization_basis",
        "statistic_or_aggregation",
        "temporal_or_cycle_scope",
        "unit_dimension",
    ):
        value_a = normalize_signature(signature_a.get(field))
        value_b = normalize_signature(signature_b.get(field))
        if value_a and value_b and value_a != value_b:
            reasons.append(f"conflicting Registry semantic signature field: {field}")

    existing_id_a = feature_a.get("existing_feature_id")
    existing_id_b = feature_b.get("existing_feature_id")
    if existing_id_a and existing_id_b and existing_id_a != existing_id_b:
        reasons.append("Features map to two different existing FEATURE_ID values")

    side_a_observations = set(feature_a.get("observation_ids", []))
    side_b_observations = set(feature_b.get("observation_ids", []))
    compared_ids = set(decision.get("compared_observation_ids", []))
    if not (compared_ids & side_a_observations):
        reasons.append("Worker did not compare any Observation from Feature A")
    if not (compared_ids & side_b_observations):
        reasons.append("Worker did not compare any Observation from Feature B")

    evidence_observation_ids = {
        item.get("observation_id") for item in decision.get("evidence_refs", [])
    }
    if not (evidence_observation_ids & side_a_observations):
        reasons.append("decision has no source evidence reference for Feature A")
    if not (evidence_observation_ids & side_b_observations):
        reasons.append("decision has no source evidence reference for Feature B")

    for evidence_ref in decision.get("evidence_refs", []):
        observation = observations.get(evidence_ref.get("observation_id"))
        if not observation:
            reasons.append("decision references an unknown Observation")
            continue
        matching = False
        for original in observation.get("evidence_refs", []):
            if (
                original.get("source_id") == evidence_ref.get("source_id")
                and original.get("locator") == evidence_ref.get("locator")
            ):
                matching = True
                break
        if not matching:
            reasons.append("decision evidence locator is not present in its referenced Observation")

    return not reasons, reasons


def choose_survivor(feature_a: dict[str, Any], feature_b: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    existing_a = bool(feature_a.get("existing_feature_id"))
    existing_b = bool(feature_b.get("existing_feature_id"))
    if existing_a != existing_b:
        return (feature_a, feature_b) if existing_a else (feature_b, feature_a)
    created_a = feature_a.get("created_at") or ""
    created_b = feature_b.get("created_at") or ""
    if created_a != created_b:
        return (feature_a, feature_b) if created_a < created_b else (feature_b, feature_a)
    return (
        (feature_a, feature_b)
        if feature_a["registry_feature_id"] < feature_b["registry_feature_id"]
        else (feature_b, feature_a)
    )


def apply_merge(
    registry: dict[str, Any],
    feature_a: dict[str, Any],
    feature_b: dict[str, Any],
    decision: dict[str, Any],
) -> tuple[str, str]:
    survivor, retired = choose_survivor(feature_a, feature_b)
    survivor_id = survivor["registry_feature_id"]
    retired_id = retired["registry_feature_id"]

    survivor["aliases"] = sorted(set(
        survivor.get("aliases", [])
        + retired.get("aliases", [])
        + [retired.get("canonical_name", "")]
    ) - {""}, key=str.casefold)
    survivor["observation_ids"] = sorted(set(
        survivor.get("observation_ids", []) + retired.get("observation_ids", [])
    ))
    survivor["unit_dimensions"] = sorted(set(
        survivor.get("unit_dimensions", []) + retired.get("unit_dimensions", [])
    ))
    survivor["measurement_methods"] = sorted(set(
        survivor.get("measurement_methods", []) + retired.get("measurement_methods", [])
    ))
    survivor["calculation_methods"] = sorted(set(
        survivor.get("calculation_methods", []) + retired.get("calculation_methods", [])
    ))
    survivor["result_types"] = sorted(set(
        survivor.get("result_types", []) + retired.get("result_types", [])
    ))
    if not survivor.get("definition") and retired.get("definition"):
        survivor["definition"] = retired["definition"]
    if not survivor.get("existing_feature_id") and retired.get("existing_feature_id"):
        survivor["existing_feature_id"] = retired["existing_feature_id"]
    survivor["feature_status"] = "confirmed"
    survivor["updated_at"] = utc_now()

    retired["feature_status"] = "retired_merged"
    retired["redirect_to"] = survivor_id
    retired["updated_at"] = utc_now()

    for observation in registry["observations"]:
        if observation.get("linked_registry_feature_id") == retired_id:
            observation["linked_registry_feature_id"] = survivor_id

    append_event(
        registry,
        "feature_merged",
        [survivor_id, retired_id],
        "merge_as_same_feature",
        decision["rationale"],
        decision.get("compared_observation_ids", []),
    )
    return survivor_id, retired_id


def update_pair_review_status(
    review_rows: list[dict[str, Any]],
    pair_id: str,
    status: str,
    decision: dict[str, Any],
    notes: list[str] | None = None,
) -> bool:
    for row in review_rows:
        if row.get("candidate_pair_id") == pair_id:
            row["relationship"] = decision.get("relationship")
            row["worker_recommended_action"] = decision.get("recommended_action")
            row["worker_rationale"] = decision.get("rationale")
            row["decision_status"] = status
            row["decision_applied_at"] = utc_now()
            row["decision_notes"] = notes or []
            return True
    return False


def invalidate_pairs_after_merge(
    review_rows: list[dict[str, Any]],
    retired_feature_id: str,
    current_pair_id: str,
    survivor_feature_id: str,
) -> None:
    for row in review_rows:
        if row.get("candidate_pair_id") == current_pair_id:
            continue
        if retired_feature_id not in {
            row.get("registry_feature_a_id"),
            row.get("registry_feature_b_id"),
        }:
            continue
        row["decision_status"] = "stale_after_merge"
        row["decision_applied_at"] = utc_now()
        row["decision_notes"] = [
            f"Feature {retired_feature_id} redirected to {survivor_feature_id}; regenerate this pair against the surviving Feature."
        ]


def refresh_feature_statuses(
    registry: dict[str, Any],
    review_rows: list[dict[str, Any]],
) -> None:
    unresolved_by_feature: dict[str, list[str]] = {}
    for row in review_rows:
        status = row.get("decision_status")
        if status in {"pending_semantic_review", "under_review", "blocked"}:
            for feature_id in (
                row.get("registry_feature_a_id"),
                row.get("registry_feature_b_id"),
            ):
                if feature_id:
                    unresolved_by_feature.setdefault(feature_id, []).append(
                        str(row.get("candidate_pair_id"))
                    )

    observations_by_id = {
        item["observation_id"]: item
        for item in registry.get("observations", [])
    }
    for feature in registry["canonical_features"]:
        if feature.get("feature_status") == "retired_merged":
            continue
        feature_id = feature["registry_feature_id"]
        if unresolved_by_feature.get(feature_id):
            feature["feature_status"] = "under_review"
        elif feature.get("feature_status") in {"provisional", "under_review"}:
            linked = [
                observations_by_id[oid]
                for oid in feature.get("observation_ids", [])
                if oid in observations_by_id
            ]
            has_verified_source = any(
                item.get("discovery_status") == "evidence_verified"
                for item in linked
            )
            feature["feature_status"] = (
                "confirmed" if has_verified_source else "under_review"
            )
        feature["updated_at"] = utc_now()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply Worker B semantic recommendations to the Global Feature Registry.")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--review-queue", type=Path, required=True)
    parser.add_argument("--reconciliation-queue", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)

    if Draft202012Validator is None:
        print("BLOCKED: install jsonschema from requirements.txt.", file=sys.stderr)
        return 2

    registry_path = args.registry.resolve()
    registry = read_json(registry_path)
    review_path = args.review_queue.resolve()
    review_rows = read_jsonl(review_path)
    pair_map = {row["candidate_pair_id"]: row for row in review_rows}
    observations = {row["observation_id"]: row for row in registry.get("observations", [])}
    tasks_queue = read_json(args.reconciliation_queue.resolve())
    run_dir = args.run_dir.resolve()
    execution_policy = read_json(EXECUTION_POLICY)
    expected_worker_b_model = execution_policy.get("workers", {}).get("worker_b", {}).get("model_ref")

    report = {
        "application_version": "0.1",
        "started_at": utc_now(),
        "registry_id": registry.get("registry_id"),
        "tasks_seen": len(tasks_queue.get("tasks", [])),
        "decisions_seen": 0,
        "merges_applied": 0,
        "pairs_kept_separate": 0,
        "pairs_under_review": 0,
        "tasks_skipped": [],
        "decisions": [],
    }

    for task in tasks_queue.get("tasks", []):
        if task.get("task_status") != "pending":
            report["tasks_skipped"].append({
                "task_id": task.get("task_id"),
                "reason": task.get("task_status"),
            })
            continue
        task_dir = Path(task["output_dir"])
        task_state_path = task_dir / "task_state.json"
        response_path = task_dir / "response.json"
        if not task_state_path.is_file() or not response_path.is_file():
            report["tasks_skipped"].append({
                "task_id": task.get("task_id"),
                "reason": "missing_task_state_or_response",
            })
            continue
        task_state = read_json(task_state_path)
        if task_state.get("status") != "complete":
            report["tasks_skipped"].append({
                "task_id": task.get("task_id"),
                "reason": f"worker_status_{task_state.get('status')}",
            })
            continue
        requested_model = task.get("model_ref")
        observed_models = task_state.get("observed_models") or []
        if requested_model != expected_worker_b_model:
            report["tasks_skipped"].append({
                "task_id": task.get("task_id"),
                "reason": "unexpected_worker_b_model",
            })
            continue
        if observed_models and observed_models != [requested_model]:
            report["tasks_skipped"].append({
                "task_id": task.get("task_id"),
                "reason": "observed_model_mismatch",
            })
            continue

        payload = read_json(response_path)
        schema_errors = validate_json_schema(payload, DECISION_SCHEMA)
        if schema_errors:
            report["tasks_skipped"].append({
                "task_id": task.get("task_id"),
                "reason": "schema_invalid",
                "errors": schema_errors[:10],
            })
            continue
        if payload.get("task_id") != task.get("task_id") or payload.get("registry_id") != registry.get("registry_id"):
            report["tasks_skipped"].append({
                "task_id": task.get("task_id"),
                "reason": "task_or_registry_id_mismatch",
            })
            continue

        for decision in payload.get("pair_decisions", []):
            report["decisions_seen"] += 1
            pair_id = decision["candidate_pair_id"]
            pair = pair_map.get(pair_id)
            if not pair:
                report["decisions"].append({
                    "candidate_pair_id": pair_id,
                    "status": "rejected_unknown_pair",
                })
                report["pairs_under_review"] += 1
                continue
            if (
                decision.get("registry_feature_a_id") != pair.get("registry_feature_a_id")
                or decision.get("registry_feature_b_id") != pair.get("registry_feature_b_id")
            ):
                report["decisions"].append({
                    "candidate_pair_id": pair_id,
                    "status": "rejected_feature_id_mismatch",
                })
                report["pairs_under_review"] += 1
                continue

            if pair.get("decision_status") in {"applied_merge", "resolved_keep_separate"}:
                report["decisions"].append({
                    "candidate_pair_id": pair_id,
                    "status": "already_resolved",
                })
                continue

            relationship = decision["relationship"]
            action = decision["recommended_action"]
            if relationship == "SAME_FEATURE" and action == "merge_candidates":
                allowed, reasons = merge_gate(decision, pair, registry, observations)
                if allowed:
                    feature_a = next(
                        item for item in registry["canonical_features"]
                        if item["registry_feature_id"] == pair["registry_feature_a_id"]
                    )
                    feature_b = next(
                        item for item in registry["canonical_features"]
                        if item["registry_feature_id"] == pair["registry_feature_b_id"]
                    )
                    survivor_id, retired_id = apply_merge(registry, feature_a, feature_b, decision)
                    invalidate_pairs_after_merge(
                        review_rows,
                        retired_feature_id=retired_id,
                        current_pair_id=pair_id,
                        survivor_feature_id=survivor_id,
                    )
                    update_pair_review_status(
                        review_rows, pair_id, "applied_merge", decision,
                        [f"survivor={survivor_id}", f"retired={retired_id}"],
                    )
                    report["merges_applied"] += 1
                    decision_status = "applied_merge"
                else:
                    update_pair_review_status(review_rows, pair_id, "under_review", decision, reasons)
                    report["pairs_under_review"] += 1
                    decision_status = "under_review"
            elif relationship in {"RELATED_BUT_DISTINCT", "SAME_FAMILY_DIFFERENT_STATISTIC"} and action == "keep_separate":
                update_pair_review_status(review_rows, pair_id, "resolved_keep_separate", decision)
                append_event(
                    registry,
                    "status_changed",
                    [pair["registry_feature_a_id"], pair["registry_feature_b_id"]],
                    "keep_separate",
                    decision["rationale"],
                    decision.get("compared_observation_ids", []),
                )
                report["pairs_kept_separate"] += 1
                decision_status = "resolved_keep_separate"
            else:
                update_pair_review_status(
                    review_rows,
                    pair_id,
                    "under_review",
                    decision,
                    ["recommendation did not meet an automatic resolution rule"],
                )
                report["pairs_under_review"] += 1
                decision_status = "under_review"

            report["decisions"].append({
                "candidate_pair_id": pair_id,
                "relationship": relationship,
                "recommended_action": action,
                "status": decision_status,
            })

    # A merge can invalidate other queued pairs involving the retired ID.
    # Persist resolved/stale decisions, then regenerate candidate pairs against
    # the surviving Registry identities without duplicating prior decisions.
    atomic_write_jsonl(review_path, review_rows)
    regenerated_pairs = build_feature_pair_queue(registry, review_path)
    review_rows = read_jsonl(review_path)
    refresh_feature_statuses(registry, review_rows)
    registry_errors = validate_json_schema(registry, REGISTRY_SCHEMA)
    if registry_errors:
        raise ApplyDecisionError("Registry validation failed: " + " | ".join(registry_errors[:10]))

    atomic_write_json(registry_path, registry)
    atomic_write_jsonl(review_path, review_rows)
    report["regenerated_pair_queue"] = regenerated_pairs
    report["finished_at"] = utc_now()
    report["registry_feature_count"] = len(registry["canonical_features"])
    report["confirmed_feature_count"] = sum(
        feature.get("feature_status") == "confirmed"
        for feature in registry["canonical_features"]
    )
    report["under_review_feature_count"] = sum(
        feature.get("feature_status") == "under_review"
        for feature in registry["canonical_features"]
    )
    report["registry_decision_count"] = len(registry["decision_log"])
    report["review_queue_pending_count"] = sum(
        row.get("decision_status") in {"pending_semantic_review", "under_review", "blocked"}
        for row in review_rows
    )
    report["important_note"] = (
        "Only decisions passing deterministic merge gates were applied. Other recommendations remain under review; "
        "the absence of a merge does not imply two Features are scientifically distinct."
    )
    report_path = args.report.resolve()
    atomic_write_json(report_path, report)
    print(json.dumps({key: value for key, value in report.items() if key != "decisions"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
