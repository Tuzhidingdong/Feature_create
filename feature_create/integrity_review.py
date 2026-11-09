"""Human review queue for global Feature/Observation/Record integrity findings.

This module is deliberately non-mutating with respect to the Registry, Observations,
Result Records, source files, and decision_log. Human decisions are appended to a
separate JSONL review ledger. Actual scientific corrections stay in the existing
reconciliation/evidence workflows and must be confirmed by a fresh integrity audit.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


TEMPLATE_VERSION = "1.0"
REVIEW_DECISIONS = {"repair_required", "accepted_warning", "defer"}
REVIEW_ACTIONS = {
    "feature_reconciliation",
    "record_review",
    "evidence_revalidation",
    "decision_history_review",
    "manual_review",
    "none",
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _primary_object(context: dict[str, Any]) -> dict[str, str]:
    for key, object_type in (
        ("record_id", "result_record"),
        ("observation_id", "observation"),
        ("feature_id", "feature"),
        ("event_id", "decision_event"),
        ("subject_id", "subject"),
    ):
        value = context.get(key)
        if value is not None and str(value).strip():
            return {"object_type": object_type, "object_id": str(value)}
    if "index" in context:
        return {"object_type": "registry_entry", "object_id": str(context["index"])}
    return {"object_type": "registry", "object_id": "global"}


def _finding_identity(issue: dict[str, Any]) -> tuple[str, str]:
    context = issue.get("context") if isinstance(issue.get("context"), dict) else {}
    primary = _primary_object(context)
    discriminator = {
        key: context[key]
        for key in ("index", "evidence_index", "subject_id", "event_id")
        if key in context
    }
    finding_id = "IRF-" + sha256_value({
        "code": issue.get("code"),
        "primary_object": primary,
        "discriminator": discriminator,
    })[:20].upper()
    fingerprint = sha256_value({
        "code": issue.get("code"),
        "severity": issue.get("severity"),
        "message": issue.get("message"),
        "context": context,
    })
    return finding_id, fingerprint


def _recommended_route(code: str) -> dict[str, str]:
    if "decision" in code or "redirect" in code or "split" in code or "excluded" in code or "transition" in code:
        return {
            "action": "decision_history_review",
            "workflow": "Compare the current Registry state with the historical decision_log. Do not fabricate a past event; record a new human decision only after evidence review and schema validation.",
            "entrypoint": "contracts/registry.schema.json + existing reconciliation workflow",
        }
    if "evidence" in code or "source" in code:
        return {
            "action": "evidence_revalidation",
            "workflow": "Re-check the upstream source package, source_id, locator, and quoted/table-cell evidence. Correct the originating evidence reference and run the evidence validator.",
            "entrypoint": "scripts/validate_evidence.py",
        }
    if code.startswith("record_") or code in {
        "invalid_or_duplicate_record_id",
        "missing_record_id",
        "observation_without_result_record",
    }:
        return {
            "action": "record_review",
            "workflow": "Review the source-backed Result Record and its Observation/Feature links. Re-extract or correct through the existing Record pipeline; do not hand-edit raw values.",
            "entrypoint": "scripts/audit_records.py / scripts/run_workers.py",
        }
    if "observation" in code or "feature" in code:
        return {
            "action": "feature_reconciliation",
            "workflow": "Inspect the Feature ↔ Observation identity decision. Use the existing reconciliation workflow for a justified merge/mapping/reassignment; preserve prior decisions and append the correcting event.",
            "entrypoint": "scripts/reconcile_features.py / scripts/apply_feature_reconciliation.py",
        }
    return {
        "action": "manual_review",
        "workflow": "Inspect the referenced Registry objects and evidence, then choose the narrowest existing workflow. Do not apply an automatic repair.",
        "entrypoint": "Manual review; rerun scripts/audit_global_integrity.py",
    }

def _project_observation(item: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "observation_id", "paper_id", "source_id", "raw_feature_name",
        "raw_definition", "reported_unit", "reported_method", "result_type",
        "variable_role", "discovery_status", "linked_registry_feature_id",
        "evidence_refs", "origin_task_id",
    )
    return {key: item[key] for key in fields if key in item}


def _project_feature(item: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "registry_feature_id", "canonical_name", "definition", "semantic_signature",
        "aliases", "feature_status", "redirect_to", "observation_ids",
        "existing_feature_id",
    )
    return {key: item[key] for key in fields if key in item}


def _project_record(item: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "record_id", "paper_id", "worker_task_id", "source_id", "observation_id",
        "registry_feature_id", "feature_id", "raw_feature_name", "value",
        "raw_value", "normalized_value", "reported_unit", "unit",
        "value_extraction_status", "evidence_refs",
    )
    return {key: item[key] for key in fields if key in item}


def _unique_rows(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {
        str(item[key]): item for item in rows
        if isinstance(item, dict) and item.get(key) is not None
    }


def _build_finding_context(
    issue: dict[str, Any],
    observations: dict[str, dict[str, Any]],
    features: dict[str, dict[str, Any]],
    records: dict[str, dict[str, Any]],
    events: list[dict[str, Any]],
    sources: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    context = issue.get("context") if isinstance(issue.get("context"), dict) else {}
    record_ids = {str(context["record_id"])} if context.get("record_id") else set()
    observation_ids = {str(context["observation_id"])} if context.get("observation_id") else set()
    feature_ids: set[str] = set()
    event_ids = {str(context["event_id"])} if context.get("event_id") else set()

    for key in ("feature_id", "actual_feature_id", "record_feature_id", "observation_feature_id", "redirect_to"):
        if context.get(key):
            feature_ids.add(str(context[key]))
    for key in ("subject_id",):
        value = context.get(key)
        if value in observations:
            observation_ids.add(str(value))
        elif value in features:
            feature_ids.add(str(value))

    selected_event_map: dict[str, dict[str, Any]] = {}
    while True:
        before = (
            len(record_ids), len(observation_ids), len(feature_ids),
            len(event_ids), len(selected_event_map),
        )
        for record_id in list(record_ids):
            record = records.get(record_id)
            if record:
                if record.get("observation_id"):
                    observation_ids.add(str(record["observation_id"]))
                feature_id = record.get("registry_feature_id") or record.get("feature_id")
                if feature_id:
                    feature_ids.add(str(feature_id))

        for feature_id in list(feature_ids):
            feature = features.get(feature_id)
            if feature:
                observation_ids.update(str(value) for value in feature.get("observation_ids", []) if value)
                if feature.get("redirect_to"):
                    feature_ids.add(str(feature["redirect_to"]))

        for observation_id, observation in observations.items():
            if str(observation.get("linked_registry_feature_id") or "") in feature_ids:
                observation_ids.add(observation_id)
        for observation_id in list(observation_ids):
            observation = observations.get(observation_id)
            if observation and observation.get("linked_registry_feature_id"):
                feature_ids.add(str(observation["linked_registry_feature_id"]))

        for record_id, record in records.items():
            if str(record.get("observation_id") or "") in observation_ids:
                record_ids.add(record_id)

        for event_index, event in enumerate(events):
            subjects = {str(value) for value in event.get("subject_ids", [])}
            evidence_ids = {str(value) for value in event.get("evidence_observation_ids", [])}
            event_id = str(event.get("event_id") or f"event_index_{event_index}")
            if (
                event_id in event_ids
                or subjects.intersection(feature_ids | observation_ids)
                or evidence_ids.intersection(observation_ids)
            ):
                selected_event_map[event_id] = event
                event_ids.add(event_id)
                for subject in subjects:
                    if subject in observations:
                        observation_ids.add(subject)
                    if subject in features:
                        feature_ids.add(subject)
                observation_ids.update(item for item in evidence_ids if item in observations)

        after = (
            len(record_ids), len(observation_ids), len(feature_ids),
            len(event_ids), len(selected_event_map),
        )
        if after == before:
            break

    selected_events = list(selected_event_map.values())

    projected_observations = [
        _project_observation(observations[item])
        for item in sorted(observation_ids) if item in observations
    ]
    projected_features = [
        _project_feature(features[item])
        for item in sorted(feature_ids) if item in features
    ]
    projected_records = [
        _project_record(records[item])
        for item in sorted(record_ids) if item in records
    ]

    evidence_refs: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    for item in projected_observations + projected_records:
        for ref in item.get("evidence_refs", []) or []:
            if not isinstance(ref, dict):
                continue
            key = sha256_value(ref)
            if key not in seen_refs:
                seen_refs.add(key)
                evidence_refs.append(ref)

    source_ids = {
        str(ref.get("source_id"))
        for ref in evidence_refs if ref.get("source_id")
    }
    source_ids.update(
        str(observations[item].get("source_id"))
        for item in observation_ids
        if item in observations and observations[item].get("source_id")
    )
    if context.get("source_id"):
        source_ids.add(str(context["source_id"]))

    source_context = []
    for source_id in sorted(source_ids):
        source = sources.get(source_id)
        if source:
            source_context.append({
                key: source[key] for key in (
                    "source_id", "paper_id", "source_type",
                    "extracted_text_path", "relative_path",
                ) if key in source
            })
        else:
            source_context.append({"source_id": source_id, "present_in_manifest": False})

    return {
        "objects": {
            "observations": projected_observations,
            "features": projected_features,
            "records": projected_records,
            "decision_events": selected_events,
        },
        "evidence_refs": evidence_refs,
        "sources": source_context,
        "related_ids": {
            "observation_ids": sorted(observation_ids),
            "feature_ids": sorted(feature_ids),
            "record_ids": sorted(record_ids),
            "event_ids": sorted(event_ids),
            "source_ids": sorted(source_ids),
        },
    }


def build_review_queue(
    audit_report: dict[str, Any],
    registry: dict[str, Any],
    records: list[dict[str, Any]],
    source_manifest: dict[str, Any] | None,
    input_fingerprints: dict[str, Any],
    review_ledger: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    observations = _unique_rows(registry.get("observations", []), "observation_id")
    features = _unique_rows(registry.get("canonical_features", []), "registry_feature_id")
    record_map = _unique_rows(records, "record_id")
    events = [item for item in registry.get("decision_log", []) if isinstance(item, dict)]
    sources = _unique_rows((source_manifest or {}).get("sources", []), "source_id")
    ledger_latest: dict[str, dict[str, Any]] = {}
    for item in review_ledger or []:
        if item.get("finding_id"):
            ledger_latest[str(item["finding_id"])] = item

    findings = []
    templates = []
    seen_finding_ids: set[str] = set()
    duplicate_counts: dict[str, int] = {}
    for issue in audit_report.get("issues", []):
        base_finding_id, fingerprint = _finding_identity(issue)
        occurrence = duplicate_counts.get(base_finding_id, 0) + 1
        duplicate_counts[base_finding_id] = occurrence
        finding_id = base_finding_id if occurrence == 1 else "IRF-" + sha256_value({
            "base": base_finding_id,
            "occurrence": occurrence,
            "fingerprint": fingerprint,
        })[:20].upper()
        if finding_id in seen_finding_ids:
            raise ValueError(f"Unable to allocate a unique finding ID for {base_finding_id}.")
        seen_finding_ids.add(finding_id)
        context = issue.get("context") if isinstance(issue.get("context"), dict) else {}
        primary = _primary_object(context)
        route = _recommended_route(str(issue.get("code") or ""))
        object_context = _build_finding_context(
            issue, observations, features, record_map, events, sources
        )
        previous = ledger_latest.get(finding_id)
        if previous is None:
            review_state = "pending"
            previous_decision = None
        elif previous.get("finding_fingerprint") != fingerprint:
            review_state = "changed_since_review"
            previous_decision = previous.get("review_decision")
        else:
            previous_decision = previous.get("review_decision")
            review_state = {
                "repair_required": "repair_pending",
                "accepted_warning": "accepted_warning",
                "defer": "deferred",
            }.get(str(previous_decision), "previously_reviewed")
        finding = {
            "finding_id": finding_id,
            "finding_fingerprint": fingerprint,
            "severity": issue.get("severity", "error"),
            "priority": "blocking" if issue.get("severity") == "error" else "review",
            "code": issue.get("code"),
            "message": issue.get("message"),
            "primary_object": primary,
            "issue_context": context,
            "recommended_route": route,
            "review_state": review_state,
            "previous_review_decision": previous_decision,
            "object_context": object_context,
        }
        findings.append(finding)
        templates.append({
            "template_version": TEMPLATE_VERSION,
            "audit_fingerprint": sha256_value({
                "audit_version": audit_report.get("audit_version"),
                "summary": audit_report.get("summary"),
                "issues": audit_report.get("issues", []),
            }),
            "finding_id": finding_id,
            "finding_fingerprint": fingerprint,
            "severity": issue.get("severity", "error"),
            "code": issue.get("code"),
            "primary_object": primary,
            "review_decision": "",
            "action": "",
            "rationale": "",
            "review_evidence_refs": [],
            "follow_up_reference": "",
        })

    current_ids = {item["finding_id"] for item in findings}
    cleared = []
    for finding_id, previous in ledger_latest.items():
        if finding_id in current_ids:
            continue
        if previous.get("review_decision") in {"repair_required", "accepted_warning", "defer"}:
            cleared.append({
                "finding_id": finding_id,
                "code": previous.get("code"),
                "previous_decision": previous.get("review_decision"),
                "previous_finding_fingerprint": previous.get("finding_fingerprint"),
                "clearance": "not_reported_by_current_audit",
                "note": "The finding is absent from this audit. This confirms current audit clearance, not which change caused it.",
            })

    audit_fingerprint = sha256_value({
        "audit_version": audit_report.get("audit_version"),
        "summary": audit_report.get("summary"),
        "issues": audit_report.get("issues", []),
    })
    queue_core = {
        "queue_version": TEMPLATE_VERSION,
        "audit_fingerprint": audit_fingerprint,
        "audit_status": audit_report.get("status"),
        "input_fingerprints": input_fingerprints,
        "finding_ids": [item["finding_id"] for item in findings],
        "known_source_ids": sorted(sources),
        "findings_sha256": sha256_value(findings),
    }
    queue = {
        **queue_core,
        "queue_fingerprint": sha256_value(queue_core),
        "generated_at": utc_now(),
        "summary": {
            "finding_count": len(findings),
            "blocking_count": sum(item["priority"] == "blocking" for item in findings),
            "review_count": sum(item["priority"] == "review" for item in findings),
            "pending_count": sum(item["review_state"] == "pending" for item in findings),
            "changed_since_review_count": sum(item["review_state"] == "changed_since_review" for item in findings),
            "cleared_since_previous_review_count": len(cleared),
        },
        "findings": findings,
        "cleared_since_previous_review": cleared,
        "safety": {
            "registry_modified": False,
            "records_modified": False,
            "source_files_modified": False,
            "decision_log_modified": False,
            "review_ledger_is_separate": True,
        },
    }
    return queue, templates


def validate_review_rows(
    queue: dict[str, Any],
    template_rows: list[dict[str, Any]],
    decision_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if queue.get("queue_version") != TEMPLATE_VERSION:
        raise ValueError("Unsupported integrity review queue version.")
    if len(template_rows) != len(decision_rows):
        raise ValueError("The edited decision JSONL must keep every template row; leave undecided rows blank.")
    if [item.get("finding_id") for item in template_rows] != [item.get("finding_id") for item in decision_rows]:
        raise ValueError("Decision rows must preserve the template order and finding_id values.")

    expected_audit_fingerprint = queue.get("audit_fingerprint")
    expected_finding_ids = queue.get("finding_ids") or []
    if [item.get("finding_id") for item in template_rows] != expected_finding_ids:
        raise ValueError("The immutable template does not match the queue finding inventory.")

    allowed_source_ids = set(queue.get("known_source_ids") or [])
    validated = []
    for original, edited in zip(template_rows, decision_rows):
        if original.get("audit_fingerprint") != expected_audit_fingerprint:
            raise ValueError(f"{original.get('finding_id')}: template audit fingerprint does not match the queue.")
        for key in (
            "template_version", "audit_fingerprint", "finding_id",
            "finding_fingerprint", "severity", "code", "primary_object",
        ):
            if original.get(key) != edited.get(key):
                raise ValueError(f"{original.get('finding_id')}: immutable field {key} was changed.")
        decision = str(edited.get("review_decision") or "").strip()
        if not decision:
            continue
        if decision not in REVIEW_DECISIONS:
            raise ValueError(f"{original['finding_id']}: invalid review_decision {decision!r}.")
        action = str(edited.get("action") or "none")
        if action not in REVIEW_ACTIONS:
            raise ValueError(f"{original['finding_id']}: invalid action {action!r}.")
        rationale = str(edited.get("rationale") or "").strip()
        if len(rationale) < 12:
            raise ValueError(f"{original['finding_id']}: rationale must contain at least 12 characters.")
        severity = str(original.get("severity") or "error")
        if decision == "accepted_warning":
            if severity != "warning":
                raise ValueError(f"{original['finding_id']}: hard errors cannot be accepted as warnings.")
            if action != "none":
                raise ValueError(f"{original['finding_id']}: accepted_warning must use action='none'.")
            refs = edited.get("review_evidence_refs") or []
            if not refs:
                raise ValueError(f"{original['finding_id']}: accepted_warning requires explicit review_evidence_refs.")
        elif decision == "repair_required":
            if action in {"", "none"}:
                raise ValueError(f"{original['finding_id']}: repair_required requires a concrete review action.")
        elif decision == "defer" and action != "none":
            raise ValueError(f"{original['finding_id']}: defer must use action='none'.")

        refs = edited.get("review_evidence_refs") or []
        if not isinstance(refs, list):
            raise ValueError(f"{original['finding_id']}: review_evidence_refs must be an array.")
        for ref_index, ref in enumerate(refs):
            if (
                not isinstance(ref, dict)
                or not str(ref.get("source_id") or "").strip()
                or not isinstance(ref.get("locator"), dict)
                or not ref.get("locator")
            ):
                raise ValueError(f"{original['finding_id']}: review_evidence_refs/{ref_index} needs source_id and a non-empty locator.")
            if allowed_source_ids and str(ref.get("source_id")) not in allowed_source_ids:
                raise ValueError(f"{original['finding_id']}: review_evidence_refs/{ref_index} references a source absent from the prepared manifest.")
        validated.append({
            "template_version": TEMPLATE_VERSION,
            "audit_fingerprint": original["audit_fingerprint"],
            "finding_id": original["finding_id"],
            "finding_fingerprint": original["finding_fingerprint"],
            "severity": severity,
            "code": original.get("code"),
            "primary_object": original.get("primary_object"),
            "review_decision": decision,
            "action": action,
            "rationale": rationale,
            "review_evidence_refs": refs,
            "follow_up_reference": str(edited.get("follow_up_reference") or "").strip() or None,
        })
    return validated


def make_ledger_entries(validated_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = []
    for row in validated_rows:
        decision_id = "IRV-" + sha256_value({
            "finding_id": row["finding_id"],
            "audit_fingerprint": row["audit_fingerprint"],
            "review_decision": row["review_decision"],
            "action": row["action"],
            "rationale": row["rationale"],
            "review_evidence_refs": row["review_evidence_refs"],
            "follow_up_reference": row["follow_up_reference"],
        })[:20].upper()
        entries.append({
            "decision_id": decision_id,
            **row,
            "actor": "human",
            "reviewed_at": utc_now(),
        })
    return entries
