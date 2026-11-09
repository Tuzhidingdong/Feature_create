"""Bidirectional integrity checks for the global Feature Registry and result records.

The audit is deterministic and read-only. It never repairs IDs, moves Observations,
rewrites records, or infers semantic decisions.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any


ACTIVE_FEATURE_STATUSES = {"confirmed", "provisional", "under_review", "split_pending"}
VERIFIED_EVIDENCE_STATUSES = {"verified_text", "verified_table_cell", "verified_digitization", "visual_only"}


def _issue(code: str, severity: str, message: str, **context: Any) -> dict[str, Any]:
    item = {"code": code, "severity": severity, "message": message}
    if context:
        item["context"] = context
    return item


def _resolve_feature_id(feature_id: str, features: dict[str, dict[str, Any]]) -> tuple[str | None, str | None]:
    seen: set[str] = set()
    current = feature_id
    while current:
        if current in seen:
            return None, "redirect_cycle"
        seen.add(current)
        feature = features.get(current)
        if feature is None:
            return None, "unknown_feature_id"
        if feature.get("feature_status") != "retired_merged":
            return current, None
        current = str(feature.get("redirect_to") or "")
    return None, "missing_redirect_target"


def audit_integrity(
    registry: dict[str, Any],
    records: list[dict[str, Any]],
    source_manifest: dict[str, Any] | None = None,
    *,
    strict_observation_records: bool = False,
) -> dict[str, Any]:
    """Return a stable report of cross-object referential/provenance integrity."""
    issues: list[dict[str, Any]] = []
    observations_list = registry.get("observations") or []
    features_list = registry.get("canonical_features") or []
    events = registry.get("decision_log") or []

    def index_unique(rows: list[dict[str, Any]], key: str, kind: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for position, row in enumerate(rows):
            value = row.get(key)
            if not isinstance(value, str) or not value.strip():
                issues.append(_issue(f"missing_{kind}_id", "error", f"{kind} at index {position} has no {key}.", index=position))
                continue
            if value in result:
                issues.append(_issue(f"duplicate_{kind}_id", "error", f"Duplicate {kind} ID: {value}.", id=value))
                continue
            result[value] = row
        return result

    observations = index_unique(observations_list, "observation_id", "observation")
    features = index_unique(features_list, "registry_feature_id", "feature")
    record_by_id = index_unique(records, "record_id", "record")
    manifest_sources: set[str] | None = None
    if source_manifest is not None:
        manifest_sources = {
            str(row.get("source_id")) for row in source_manifest.get("sources", [])
            if row.get("source_id")
        }

    observation_to_records: dict[str, list[str]] = defaultdict(list)
    feature_to_records: dict[str, list[str]] = defaultdict(list)

    for observation_id, observation in observations.items():
        evidence = observation.get("evidence_refs") or []
        if not evidence:
            issues.append(_issue("observation_without_evidence", "error", f"Observation {observation_id} has no source evidence.", observation_id=observation_id))
        valid_evidence = 0
        for index, ref in enumerate(evidence):
            source_id = ref.get("source_id") if isinstance(ref, dict) else None
            locator = ref.get("locator") if isinstance(ref, dict) else None
            status = ref.get("evidence_status") if isinstance(ref, dict) else None
            if not source_id or not isinstance(locator, dict) or not locator:
                issues.append(_issue("invalid_observation_evidence_ref", "error", f"Observation {observation_id} has an incomplete evidence reference.", observation_id=observation_id, evidence_index=index))
                continue
            if observation.get("source_id") and str(source_id) != str(observation.get("source_id")):
                issues.append(_issue("observation_evidence_source_mismatch", "error", f"Observation {observation_id} evidence points to a different source than the Observation.", observation_id=observation_id, source_id=source_id))
                continue
            if manifest_sources is not None and str(source_id) not in manifest_sources:
                issues.append(_issue("unknown_evidence_source", "error", f"Observation {observation_id} references source {source_id}, which is absent from the source manifest.", observation_id=observation_id, source_id=source_id))
                continue
            if status in VERIFIED_EVIDENCE_STATUSES:
                valid_evidence += 1
        if observation.get("discovery_status") == "evidence_verified" and valid_evidence == 0:
            issues.append(_issue("observation_evidence_not_verified", "error", f"Evidence-verified Observation {observation_id} has no declared verified evidence reference.", observation_id=observation_id))

        linked = observation.get("linked_registry_feature_id")
        if observation.get("variable_role") == "result_feature" and observation.get("discovery_status") != "excluded":
            if not linked:
                issues.append(_issue("observation_without_feature", "error", f"Result Observation {observation_id} has no linked Registry Feature.", observation_id=observation_id))
            elif linked not in features:
                issues.append(_issue("observation_feature_missing", "error", f"Observation {observation_id} points to missing Feature {linked}.", observation_id=observation_id, feature_id=linked))
            elif observation_id not in (features[linked].get("observation_ids") or []):
                issues.append(_issue("feature_missing_observation_backlink", "error", f"Feature {linked} does not list Observation {observation_id}.", observation_id=observation_id, feature_id=linked))

    for feature_id, feature in features.items():
        status = feature.get("feature_status")
        linked_ids = feature.get("observation_ids") or []
        if status == "confirmed" and not linked_ids:
            issues.append(_issue("confirmed_feature_without_observation", "error", f"Confirmed Feature {feature_id} has no Observations.", feature_id=feature_id))
        valid_linked = 0
        verified_linked = 0
        for observation_id in linked_ids:
            observation = observations.get(observation_id)
            if observation is None:
                issues.append(_issue("feature_points_to_missing_observation", "error", f"Feature {feature_id} references missing Observation {observation_id}.", feature_id=feature_id, observation_id=observation_id))
                continue
            actual_feature_id = observation.get("linked_registry_feature_id")
            if actual_feature_id != feature_id:
                resolved_expected, expected_error = _resolve_feature_id(feature_id, features)
                resolved_actual, actual_error = _resolve_feature_id(str(actual_feature_id or ""), features)
                if expected_error or actual_error or resolved_expected != resolved_actual:
                    issues.append(_issue("observation_feature_backlink_mismatch", "error", f"Observation {observation_id} does not point back to Feature {feature_id} (or its merge survivor).", feature_id=feature_id, observation_id=observation_id, actual_feature_id=actual_feature_id))
            if observation.get("variable_role") == "result_feature" and observation.get("discovery_status") != "excluded":
                valid_linked += 1
                if observation.get("discovery_status") == "evidence_verified" and any(
                    isinstance(ref, dict) and ref.get("evidence_status") in VERIFIED_EVIDENCE_STATUSES
                    for ref in (observation.get("evidence_refs") or [])
                ):
                    verified_linked += 1
        if status == "confirmed" and valid_linked == 0:
            issues.append(_issue("confirmed_feature_without_valid_observation", "error", f"Confirmed Feature {feature_id} has no eligible result-feature Observation.", feature_id=feature_id))
        if status == "confirmed" and verified_linked == 0:
            issues.append(_issue("confirmed_feature_without_verified_source_evidence", "error", f"Confirmed Feature {feature_id} has no evidence-verified Observation with source evidence.", feature_id=feature_id))

    for record_id, record in record_by_id.items():
        observation_id = record.get("observation_id")
        observation = observations.get(str(observation_id)) if observation_id else None
        if observation is None:
            issues.append(_issue("record_without_valid_observation", "error", f"Result Record {record_id} has no valid Observation link.", record_id=record_id, observation_id=observation_id))
            continue
        observation_to_records[str(observation_id)].append(record_id)
        if not record.get("source_id"):
            issues.append(_issue("record_without_source_id", "error", f"Result Record {record_id} has no source_id.", record_id=record_id))
        elif observation.get("source_id") and str(record.get("source_id")) != str(observation.get("source_id")):
            issues.append(_issue("record_observation_source_mismatch", "error", f"Result Record {record_id} and Observation {observation_id} refer to different source IDs.", record_id=record_id, record_source_id=record.get("source_id"), observation_source_id=observation.get("source_id")))
        record_evidence = record.get("evidence_refs") or []
        if not record_evidence:
            issues.append(_issue("record_without_evidence", "error", f"Result Record {record_id} has no source evidence references.", record_id=record_id))
        for evidence_index, ref in enumerate(record_evidence):
            evidence_source = ref.get("source_id") if isinstance(ref, dict) else None
            locator = ref.get("locator") if isinstance(ref, dict) else None
            if not evidence_source or not isinstance(locator, dict) or not locator:
                issues.append(_issue("invalid_record_evidence_ref", "error", f"Result Record {record_id} has an incomplete evidence reference.", record_id=record_id, evidence_index=evidence_index))
                continue
            if record.get("source_id") and str(evidence_source) != str(record.get("source_id")):
                issues.append(_issue("record_evidence_source_mismatch", "error", f"Result Record {record_id} evidence points to a different source than the record.", record_id=record_id, source_id=evidence_source))
            if manifest_sources is not None and str(evidence_source) not in manifest_sources:
                issues.append(_issue("record_evidence_source_unknown", "error", f"Result Record {record_id} references unknown source {evidence_source}.", record_id=record_id, source_id=evidence_source))
        record_feature = record.get("registry_feature_id") or record.get("feature_id")
        observation_feature = observation.get("linked_registry_feature_id")
        if not record_feature:
            issues.append(_issue("record_without_feature_id", "error", f"Result Record {record_id} has no Registry Feature ID.", record_id=record_id))
        elif record_feature not in features:
            issues.append(_issue("record_feature_missing", "error", f"Result Record {record_id} references missing Feature {record_feature}.", record_id=record_id, feature_id=record_feature))
        elif observation_feature:
            resolved_record, record_error = _resolve_feature_id(str(record_feature), features)
            resolved_observation, observation_error = _resolve_feature_id(str(observation_feature), features)
            if record_error or observation_error or resolved_record != resolved_observation:
                issues.append(_issue("record_observation_feature_mismatch", "error", f"Result Record {record_id} Feature does not match its Observation's Feature after redirects.", record_id=record_id, record_feature_id=record_feature, observation_feature_id=observation_feature))
            else:
                feature_to_records[str(resolved_record)].append(record_id)

    for observation_id, observation in observations.items():
        if observation.get("variable_role") != "result_feature" or observation.get("discovery_status") == "excluded":
            continue
        if not observation_to_records.get(observation_id):
            severity = "error" if strict_observation_records else "warning"
            issues.append(_issue("observation_without_result_record", severity, f"Eligible Observation {observation_id} is not linked by any Result Record.", observation_id=observation_id))

    event_ids: set[str] = set()
    event_types_by_subject: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for index, event in enumerate(events):
        event_id = event.get("event_id")
        if not event_id or event_id in event_ids:
            issues.append(_issue("invalid_or_duplicate_decision_event_id", "error", f"Decision event at index {index} has a missing or duplicate event_id.", index=index, event_id=event_id))
        else:
            event_ids.add(str(event_id))
        subjects = event.get("subject_ids") or []
        if not subjects or not str(event.get("rationale") or "").strip() or not str(event.get("decision") or "").strip():
            issues.append(_issue("incomplete_decision_event", "error", f"Decision event {event_id or index} lacks subjects, decision, or rationale.", event_id=event_id))
        for subject in subjects:
            subject = str(subject)
            if subject not in observations and subject not in features:
                issues.append(_issue("decision_subject_missing", "error", f"Decision event {event_id or index} references unknown subject {subject}.", event_id=event_id, subject_id=subject))
                continue
            event_types_by_subject[(str(event.get("event_type")), subject)].append(event)
        evidence_observation_ids = event.get("evidence_observation_ids") or []
        if event.get("event_type") in {"feature_merged", "feature_split", "feature_excluded", "observation_reassigned"} and not evidence_observation_ids:
            issues.append(_issue("decision_event_without_evidence_chain", "error", f"Decision event {event_id or index} has no evidence Observation references.", event_id=event_id))
        if event.get("event_type") in {"feature_merged", "feature_split"}:
            feature_subjects = [subject for subject in subjects if str(subject) in features]
            if len(set(feature_subjects)) < 2:
                issues.append(_issue("incomplete_feature_transition_subjects", "error", f"Decision event {event_id or index} does not identify at least two Feature objects for its merge/split transition.", event_id=event_id))
        for observation_id in evidence_observation_ids:
            if observation_id not in observations:
                issues.append(_issue("decision_evidence_observation_missing", "error", f"Decision event {event_id or index} references missing evidence Observation {observation_id}.", event_id=event_id, observation_id=observation_id))

    for observation_id, observation in observations.items():
        if observation.get("discovery_status") == "excluded" and not event_types_by_subject.get(("feature_excluded", observation_id)):
            issues.append(_issue("excluded_observation_without_decision", "error", f"Excluded Observation {observation_id} has no feature_excluded decision event.", observation_id=observation_id))

    for feature_id, feature in features.items():
        if feature.get("feature_status") == "retired_merged":
            target = feature.get("redirect_to")
            resolved, error = _resolve_feature_id(feature_id, features)
            if not target or error or resolved == feature_id:
                issues.append(_issue("invalid_feature_merge_redirect", "error", f"Retired Feature {feature_id} has an invalid merge redirect.", feature_id=feature_id, redirect_to=target))
            if not event_types_by_subject.get(("feature_merged", feature_id)):
                issues.append(_issue("merged_feature_without_decision", "error", f"Retired Feature {feature_id} has no feature_merged decision event.", feature_id=feature_id))
        if feature.get("feature_status") == "split_pending" and not event_types_by_subject.get(("feature_split", feature_id)):
            issues.append(_issue("split_pending_without_decision", "error", f"Split-pending Feature {feature_id} has no feature_split decision event.", feature_id=feature_id))

    summary = {
        "observation_count": len(observations),
        "feature_count": len(features),
        "record_count": len(record_by_id),
        "decision_event_count": len(events),
        "observations_linked_to_records": len(observation_to_records),
        "records_linked_to_features": len(feature_to_records),
        "issue_count": len(issues),
        "error_count": sum(item["severity"] == "error" for item in issues),
        "warning_count": sum(item["severity"] == "warning" for item in issues),
    }
    status = "fail" if summary["error_count"] else "pass_with_warnings" if summary["warning_count"] else "pass"
    return {"audit_version": "1.0", "status": status, "summary": summary, "issues": issues}
