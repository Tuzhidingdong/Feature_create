"""Idempotently ingest validated Worker outputs into the global Feature Registry.

This script never merges semantic features automatically. It creates one
provisional canonical candidate per source observation, preserves all result
value records, and emits deterministic pairwise review candidates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from contextlib import contextmanager
from difflib import SequenceMatcher
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_SCHEMA = ROOT / "contracts" / "registry.schema.json"
WORKER_SCHEMA_PATHS = {
    "source_discovery": ROOT / "contracts" / "worker-a.schema.json",
    "feature_audit": ROOT / "contracts" / "worker-b.schema.json",
}
RESULT_ROLES = {"source_discovery", "feature_audit"}
STOP_WORDS = {
    "the", "of", "for", "in", "at", "to", "a", "an", "and", "or",
    "value", "values", "result", "results", "performance", "property",
    "properties", "measured", "measurement",
}


class RegistryIngestionError(RuntimeError):
    """Raised when source/registry ingestion cannot proceed safely."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, *parts: str, length: int = 18) -> str:
    material = "|".join(parts)
    return f"{prefix}-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:length].upper()}"


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryIngestionError(f"Unable to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RegistryIngestionError(f"Expected a JSON object in {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RegistryIngestionError(
                    f"Invalid JSONL at {path}:{line_number}: {exc}"
                ) from exc
            if not isinstance(value, dict):
                raise RegistryIngestionError(
                    f"JSONL row must be an object at {path}:{line_number}"
                )
            rows.append(value)
    return rows


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
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
    """Portable exclusive-create lock for Windows and POSIX filesystems."""
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"pid={os.getpid()} time={utc_now()}".encode("utf-8"))
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise RegistryIngestionError(f"Timed out waiting for Registry lock: {path}")
            time.sleep(0.2)
    try:
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def create_empty_registry() -> dict[str, Any]:
    return {
        "contract_version": "0.1",
        "registry_id": "FEATURE_CREATE_GLOBAL_REGISTRY_V1",
        "next_observation_sequence": 1,
        "next_feature_sequence": 1,
        "observations": [],
        "canonical_features": [],
        "decision_log": [],
    }


def validate_registry(registry: dict[str, Any]) -> None:
    if Draft202012Validator is None:
        raise RegistryIngestionError("Install jsonschema from requirements.txt.")
    schema = read_json(REGISTRY_SCHEMA)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(registry),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "$"
        raise RegistryIngestionError(
            f"Registry schema validation failed at {location}: {first.message}"
        )


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    text = text.replace("μ", "u").replace("µ", "u")
    text = re.sub(r"[^a-z0-9%]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize(value: str) -> set[str]:
    return {
        token for token in normalize_text(value).split()
        if token and token not in STOP_WORDS and len(token) > 1
    }


def similarity(left: str, right: str) -> tuple[float, float, float]:
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return 0.0, 0.0, 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    ta, tb = tokenize(a), tokenize(b)
    overlap = len(ta & tb) / max(1, len(ta | tb))
    containment = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return ratio, overlap, containment


def normalized_unit(value: str | None) -> str:
    if not value:
        return ""
    unit = unicodedata.normalize("NFKC", value).casefold()
    unit = unit.replace("μ", "u").replace("µ", "u")
    unit = re.sub(r"\s+", "", unit)
    return unit


def acronym_match(left: str, right: str) -> bool:
    a = normalize_text(left).replace(" ", "")
    b_tokens = tokenize(right)
    acronym = "".join(token[0] for token in normalize_text(right).split() if token)
    if len(a) < 2 or len(a) > 8:
        return False
    if a == acronym:
        return True
    # Also allow a short alias to match the initials of a longer canonical name.
    b = normalize_text(right).replace(" ", "")
    return bool(len(a) <= 5 and b.startswith(a) and len(b) >= len(a) + 3)


def is_evidence_reference_verified(
    reference: dict[str, Any],
    source_map: dict[str, dict[str, Any]],
    manifest_root: Path,
) -> tuple[bool, str]:
    source = source_map.get(reference.get("source_id"))
    if not source:
        return False, "unknown_source_id"
    status = reference.get("evidence_status")
    if status in {"verified_text", "verified_table_cell"}:
        quote = reference.get("quote_or_cell_value")
        text_path = source.get("extracted_text_path")
        if not isinstance(quote, str) or not quote.strip() or not text_path:
            return False, "missing_quote_or_source_text"
        local_path = (manifest_root / text_path).resolve()
        try:
            local_path.relative_to(manifest_root.resolve())
        except ValueError:
            return False, "source_text_path_outside_manifest_root"
        if not local_path.is_file():
            return False, "source_text_file_missing"
        content = local_path.read_text(encoding="utf-8", errors="replace")
        haystack = normalize_text(content)
        needle = normalize_text(quote)
        return (bool(needle) and needle in haystack, "quote_found" if needle and needle in haystack else "quote_not_found")
    if status == "verified_digitization":
        locator = reference.get("locator") or {}
        artifact = (
            locator.get("digitization_artifact")
            or locator.get("digitized_data_path")
        )
        if isinstance(artifact, str):
            candidate = Path(artifact)
            if not candidate.is_absolute():
                candidate = (manifest_root / candidate).resolve()
            try:
                candidate.relative_to(manifest_root.resolve())
            except ValueError:
                return False, "digitization_artifact_outside_manifest_root"
            return (candidate.is_file(), "digitization_artifact_found" if candidate.is_file() else "digitization_artifact_missing")
        if locator.get("digitization_id"):
            return True, "digitization_id_reference_present"
        return False, "digitization_artifact_not_specified"
    if status == "visual_only":
        return False, "visual_only_not_numeric_verification"
    return False, f"status_{status or 'not_reported'}"


def evidence_summary(
    references: list[dict[str, Any]],
    source_map: dict[str, dict[str, Any]],
    manifest_root: Path,
) -> dict[str, Any]:
    checks = []
    for reference in references:
        verified, reason = is_evidence_reference_verified(reference, source_map, manifest_root)
        checks.append({
            "source_id": reference.get("source_id"),
            "status_reported": reference.get("evidence_status"),
            "verified_by_local_check": verified,
            "check_reason": reason,
        })
    verified_refs = [item for item in checks if item["verified_by_local_check"]]
    status_priority = (
        "verified_table_cell",
        "verified_text",
        "verified_digitization",
        "visual_only",
    )
    reported_statuses = {
        item.get("evidence_status", "unverified")
        for item in references
    }
    best_status = next((status for status in status_priority if status in reported_statuses), "unverified")
    if verified_refs:
        effective_status = best_status
    elif "visual_only" in reported_statuses:
        effective_status = "visual_only"
    else:
        effective_status = "unverified"
    return {
        "status": effective_status,
        "verified_reference_count": len(verified_refs),
        "reference_count": len(references),
        "locally_verified": bool(verified_refs),
        "checks": checks,
    }


def evidence_refs_in_payload(payload: Any) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            refs = value.get("evidence_refs")
            if isinstance(refs, list):
                references.extend(ref for ref in refs if isinstance(ref, dict))
            for key, child in value.items():
                if key != "evidence_refs":
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return references


def initialize_registry(path: Path) -> dict[str, Any]:
    if path.is_file():
        registry = read_json(path)
    else:
        registry = create_empty_registry()
    validate_registry(registry)
    return registry


def append_decision(
    registry: dict[str, Any],
    event_type: str,
    subject_ids: list[str],
    decision: str,
    rationale: str,
    evidence_observation_ids: list[str],
    actor: str = "python_rule",
) -> None:
    event_id = stable_id(
        "EVT",
        event_type,
        *sorted(subject_ids),
        decision,
        rationale,
    )
    existing = {event.get("event_id") for event in registry["decision_log"]}
    if event_id in existing:
        return
    registry["decision_log"].append({
        "event_id": event_id,
        "event_type": event_type,
        "subject_ids": subject_ids,
        "decision": decision,
        "rationale": rationale,
        "evidence_observation_ids": evidence_observation_ids,
        "actor": actor,
        "contract_version": "0.1",
        "created_at": utc_now(),
    })


def add_observation(
    registry: dict[str, Any],
    paper_id: str,
    task: dict[str, Any],
    candidate: dict[str, Any],
    source_map: dict[str, dict[str, Any]],
    manifest_root: Path,
) -> tuple[str, str | None, dict[str, Any]]:
    role = task["worker_role"]
    candidate_id = str(candidate["candidate_observation_id"])
    source_id = str(candidate["source_id"])
    raw_name = str(candidate["raw_feature_name"]).strip()
    observation_id = stable_id(
        "OBS",
        task["task_id"],
        role,
        candidate_id,
        source_id,
        raw_name,
        str(candidate.get("raw_definition") or ""),
        str(candidate.get("reported_unit") or ""),
        str(candidate.get("reported_method") or ""),
        str(candidate.get("result_type") or ""),
        str(candidate.get("variable_role") or ""),
        json.dumps(evidence_refs if (evidence_refs := candidate.get("evidence_refs") or []) else [], sort_keys=True, ensure_ascii=False),
    )
    existing_observation = next(
        (item for item in registry["observations"] if item["observation_id"] == observation_id),
        None,
    )
    if existing_observation:
        return (
            observation_id,
            existing_observation.get("linked_registry_feature_id"),
            existing_observation,
        )

    summary = evidence_summary(evidence_refs, source_map, manifest_root)
    variable_role = candidate.get("variable_role", "uncertain")
    if variable_role in {"condition_only", "sample_descriptor"}:
        discovery_status = "excluded"
        registry_feature_id = None
    elif variable_role == "uncertain":
        discovery_status = "under_review"
        registry_feature_id = stable_id("RFEAT", observation_id)
    else:
        discovery_status = "evidence_verified" if summary["locally_verified"] else "candidate"
        registry_feature_id = stable_id("RFEAT", observation_id)

    observation = {
        "observation_id": observation_id,
        "paper_id": paper_id,
        "source_id": source_id,
        "raw_feature_name": raw_name,
        "raw_definition": candidate.get("raw_definition"),
        "reported_unit": candidate.get("reported_unit"),
        "reported_method": candidate.get("reported_method"),
        "result_type": candidate.get("result_type", "uncertain"),
        "variable_role": variable_role,
        "evidence_refs": evidence_refs,
        "origin_task_id": task["task_id"],
        "origin_worker": "source_discovery" if role == "source_discovery" else "feature_audit",
        "discovery_status": discovery_status,
        "linked_registry_feature_id": registry_feature_id,
        "created_at": utc_now(),
    }
    registry["observations"].append(observation)
    registry["next_observation_sequence"] += 1

    if registry_feature_id:
        existing_feature = next(
            (item for item in registry["canonical_features"] if item["registry_feature_id"] == registry_feature_id),
            None,
        )
        if not existing_feature:
            semantic_signature = {
                "physical_or_statistical_quantity": None,
                "measurand_or_operational_definition": candidate.get("raw_definition"),
                "formula": None,
                "numerator": None,
                "denominator": None,
                "normalization_basis": None,
                "statistic_or_aggregation": None,
                "temporal_or_cycle_scope": None,
                "unit_dimension": None,
                "method_scope": candidate.get("reported_method"),
            }
            registry["canonical_features"].append({
                "registry_feature_id": registry_feature_id,
                "canonical_name": raw_name,
                "definition": candidate.get("raw_definition"),
                "semantic_signature": semantic_signature,
                "aliases": [raw_name],
                "unit_dimensions": [],
                "measurement_methods": [candidate["reported_method"]] if candidate.get("reported_method") else [],
                "calculation_methods": [],
                "result_types": [candidate.get("result_type", "uncertain")],
                "observation_ids": [observation_id],
                "existing_feature_id": None,
                "feature_status": "provisional" if variable_role == "result_feature" else "under_review",
                "redirect_to": None,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            })
            registry["next_feature_sequence"] += 1
            append_decision(
                registry,
                "feature_created",
                [registry_feature_id, observation_id],
                "create_provisional_canonical_feature",
                "A source-backed raw Feature Observation was ingested. Semantic equivalence with other candidates has not yet been adjudicated.",
                [observation_id],
            )
        else:
            if observation_id not in existing_feature["observation_ids"]:
                existing_feature["observation_ids"].append(observation_id)
            existing_feature["updated_at"] = utc_now()

    if variable_role in {"condition_only", "sample_descriptor"}:
        append_decision(
            registry,
            "feature_excluded",
            [observation_id],
            "exclude_from_canonical_feature_registry",
            f"Worker classified this occurrence as {variable_role}; preserve it as an Observation/context candidate but do not create a result Feature.",
            [observation_id],
        )

    return observation_id, registry_feature_id, observation


def make_record(
    paper: dict[str, Any],
    task: dict[str, Any],
    candidate_record: dict[str, Any],
    observation_id: str,
    registry_feature_id: str | None,
    raw_feature_name: str,
    reported_method: str | None,
    source_map: dict[str, dict[str, Any]],
    manifest_root: Path,
) -> dict[str, Any]:
    refs = candidate_record.get("evidence_refs") or []
    evidence = evidence_summary(refs, source_map, manifest_root)
    value_status = candidate_record.get("value_extraction_status", "not_extractable")
    if value_status == "visual_only":
        extraction_status = "visual_only"
    elif value_status == "not_extractable":
        extraction_status = "not_extractable"
    elif evidence["locally_verified"]:
        extraction_status = "accepted"
    else:
        extraction_status = "value_unverified"

    if value_status == "visual_only" and candidate_record.get("numeric_value") is not None:
        extraction_status = "under_review"

    context_fingerprint = json.dumps(
        {
            "sample_identity": candidate_record.get("sample_identity", []),
            "condition_facts": candidate_record.get("condition_facts", []),
            "time_cycle_facts": candidate_record.get("time_cycle_facts", []),
            "protocol_facts": candidate_record.get("protocol_facts", []),
            "other_context_facts": candidate_record.get("other_context_facts", []),
            "evidence_refs": refs,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    record_id = stable_id(
        "REC",
        task["task_id"],
        str(candidate_record["candidate_record_id"]),
        str(candidate_record["source_id"]),
        str(candidate_record["raw_value"]),
        context_fingerprint,
    )
    source = source_map.get(candidate_record["source_id"], {})
    source_type = refs[0].get("source_type") if refs else None
    if not source_type:
        source_type = source.get("source_type")

    return {
        "record_id": record_id,
        "paper_id": paper["paper_id"],
        "doi": paper.get("doi"),
        "registry_feature_id": registry_feature_id,
        "observation_id": observation_id,
        "raw_feature_name": raw_feature_name,
        "raw_value": candidate_record.get("raw_value"),
        "numeric_value": candidate_record.get("numeric_value"),
        "reported_unit": candidate_record.get("reported_unit"),
        "result_type": candidate_record.get("result_type"),
        "value_extraction_status": value_status,
        "extraction_status": extraction_status,
        "statistic_label": candidate_record.get("statistic_label"),
        "replicate_count": candidate_record.get("replicate_count"),
        "sample_identity": candidate_record.get("sample_identity", []),
        "condition_facts": candidate_record.get("condition_facts", []),
        "time_cycle_facts": candidate_record.get("time_cycle_facts", []),
        "protocol_facts": candidate_record.get("protocol_facts", []),
        "other_context_facts": candidate_record.get("other_context_facts", []),
        "measurement_or_calculation_method": reported_method,
        "source_id": candidate_record["source_id"],
        "source_type": source_type,
        "source_location": [ref.get("locator", {}) for ref in refs],
        "evidence_refs": refs,
        "evidence_status": evidence["status"],
        "evidence_verification": evidence,
        "context_notes": candidate_record.get("context_notes"),
        "unresolved_issues": [],
        "worker_task_id": task["task_id"],
        "worker_model": task.get("model_ref"),
        "created_at": utc_now(),
    }


def write_upsert_jsonl(path: Path, rows_to_add: list[dict[str, Any]], id_key: str) -> int:
    existing_rows = read_jsonl(path)
    existing_ids = {row.get(id_key) for row in existing_rows}
    added = 0
    for row in rows_to_add:
        identifier = row.get(id_key)
        if identifier and identifier not in existing_ids:
            existing_rows.append(row)
            existing_ids.add(identifier)
            added += 1
    atomic_write_jsonl(path, existing_rows)
    return added


def build_feature_pair_queue(registry: dict[str, Any], output_path: Path) -> dict[str, int]:
    features = [
        item for item in registry["canonical_features"]
        if item.get("feature_status") not in {"retired_merged"}
    ]
    existing = read_jsonl(output_path)
    existing_by_pair = {item.get("candidate_pair_id"): item for item in existing}
    new_rows: list[dict[str, Any]] = []
    comparisons = 0

    observations_by_id = {
        item["observation_id"]: item for item in registry["observations"]
    }
    for index, left in enumerate(features):
        for right in features[index + 1:]:
            left_id = left["registry_feature_id"]
            right_id = right["registry_feature_id"]
            if left_id == right_id:
                continue
            comparisons += 1

            left_obs = [observations_by_id[oid] for oid in left.get("observation_ids", []) if oid in observations_by_id]
            right_obs = [observations_by_id[oid] for oid in right.get("observation_ids", []) if oid in observations_by_id]
            left_names = [left["canonical_name"], *left.get("aliases", [])]
            right_names = [right["canonical_name"], *right.get("aliases", [])]
            left_definitions = [left.get("definition") or ""]
            right_definitions = [right.get("definition") or ""]
            left_units = {
                normalized_unit(item.get("reported_unit"))
                for item in left_obs if item.get("reported_unit")
            }
            right_units = {
                normalized_unit(item.get("reported_unit"))
                for item in right_obs if item.get("reported_unit")
            }
            shared_unit = bool(left_units & right_units)

            best_ratio = 0.0
            best_overlap = 0.0
            best_containment = 0.0
            exact_name = False
            acronym = False
            for name_a in left_names:
                for name_b in right_names:
                    ratio, overlap, containment = similarity(str(name_a), str(name_b))
                    best_ratio = max(best_ratio, ratio)
                    best_overlap = max(best_overlap, overlap)
                    best_containment = max(best_containment, containment)
                    exact_name = exact_name or normalize_text(str(name_a)) == normalize_text(str(name_b))
                    acronym = acronym or acronym_match(str(name_a), str(name_b)) or acronym_match(str(name_b), str(name_a))
            definition_ratio, definition_overlap, definition_containment = (
                similarity(" ".join(left_definitions), " ".join(right_definitions))
                if any(left_definitions) and any(right_definitions)
                else (0.0, 0.0, 0.0)
            )
            best_overlap = max(best_overlap, definition_overlap)
            best_containment = max(best_containment, definition_containment)

            ratio = best_ratio
            overlap = best_overlap
            containment = best_containment
            reasons: list[str] = []
            if exact_name:
                reasons.append("exact_normalized_name")
            if ratio >= 0.58:
                reasons.append("name_sequence_similarity")
            if overlap >= 0.30:
                reasons.append("name_or_definition_token_overlap")
            if containment >= 0.65:
                reasons.append("token_containment")
            if acronym:
                reasons.append("possible_acronym_alias")
            if shared_unit:
                reasons.append("shared_reported_unit")
            if definition_ratio >= 0.55:
                reasons.append("definition_similarity")

            # Unit and acronym matching can rescue synonyms whose names differ.
            # The result is still only a review queue; no semantic merge occurs.
            if not reasons:
                continue

            pair_ids = sorted([left_id, right_id])
            pair_id = stable_id("PAIR", *pair_ids)
            if pair_id in existing_by_pair:
                continue

            all_papers = {
                item.get("paper_id")
                for item in left_obs + right_obs
                if item.get("paper_id")
            }
            new_rows.append({
                "candidate_pair_id": pair_id,
                "registry_feature_a_id": left_id,
                "registry_feature_b_id": right_id,
                "feature_a": {
                    "canonical_name": left.get("canonical_name"),
                    "definition": left.get("definition"),
                    "semantic_signature": left.get("semantic_signature"),
                    "aliases": left.get("aliases", []),
                    "units": sorted(left_units),
                    "observation_ids": left.get("observation_ids", []),
                },
                "feature_b": {
                    "canonical_name": right.get("canonical_name"),
                    "definition": right.get("definition"),
                    "semantic_signature": right.get("semantic_signature"),
                    "aliases": right.get("aliases", []),
                    "units": sorted(right_units),
                    "observation_ids": right.get("observation_ids", []),
                },
                "candidate_retrieval": {
                    "sequence_ratio": round(ratio, 4),
                    "token_overlap": round(overlap, 4),
                    "token_containment": round(containment, 4),
                    "shared_unit": shared_unit,
                    "reasons": reasons,
                },
                "paper_ids": sorted(all_papers),
                "cross_paper": len(all_papers) > 1,
                "relationship": None,
                "decision_status": "pending_semantic_review",
                "created_at": utc_now(),
            })

    if new_rows:
        atomic_write_jsonl(output_path, existing + new_rows)
    elif not output_path.exists():
        atomic_write_jsonl(output_path, [])
    return {
        "candidate_pairs_examined": comparisons,
        "candidate_pairs_added": len(new_rows),
        "candidate_pairs_total": len(existing) + len(new_rows),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest Worker A/B observations and results into the global Feature Registry."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--review-queue", type=Path, required=True)
    parser.add_argument("--run-report", type=Path, required=True)
    args = parser.parse_args(argv)

    manifest_path = args.manifest.resolve()
    manifest = read_json(manifest_path)
    manifest_root = manifest_path.parent
    source_map = {source["source_id"]: source for source in manifest.get("sources", [])}
    queue = read_json(args.queue.resolve())
    registry_path = args.registry.resolve()
    records_path = args.records.resolve()
    review_queue_path = args.review_queue.resolve()
    report_path = args.run_report.resolve()
    lock_path = registry_path.with_suffix(registry_path.suffix + ".lock")

    if Draft202012Validator is None:
        print("BLOCKED: install jsonschema from requirements.txt.", file=sys.stderr)
        return 2

    tasks_by_id = {task["task_id"]: task for task in queue.get("tasks", [])}
    validation_path = report_path.parent / "evidence_validation.json"
    if not validation_path.is_file():
        print(f"BLOCKED: evidence validation report not found: {validation_path}", file=sys.stderr)
        return 2
    validation = read_json(validation_path)
    validation_by_task = {
        item.get("task_id"): item for item in validation.get("tasks", [])
    }

    records_to_add: list[dict[str, Any]] = []
    omissions_to_add: list[dict[str, Any]] = []
    distinctions_to_add: list[dict[str, Any]] = []
    task_summaries: list[dict[str, Any]] = []
    added_observations = 0
    added_features = 0
    skipped_tasks: list[str] = []
    skipped_task_reasons: dict[str, str] = {}

    with exclusive_lock(lock_path):
        registry = initialize_registry(registry_path)
        existing_observation_ids = {item["observation_id"] for item in registry["observations"]}
        existing_feature_ids = {item["registry_feature_id"] for item in registry["canonical_features"]}

        for task_id, task in sorted(tasks_by_id.items()):
            if task.get("task_status") != "pending":
                continue
            task_dir = Path(task["output_dir"])
            task_state_path = task_dir / "task_state.json"
            response_path = task_dir / "response.json"
            if not task_state_path.is_file() or not response_path.is_file():
                skipped_tasks.append(task_id)
                continue
            task_state = read_json(task_state_path)
            if task_state.get("status") != "complete":
                skipped_tasks.append(task_id)
                skipped_task_reasons[task_id] = f"worker_status_{task_state.get('status', 'unknown')}"
                continue
            requested_model = task.get("model_ref")
            if task_state.get("requested_model") != requested_model:
                skipped_tasks.append(task_id)
                skipped_task_reasons[task_id] = "worker_requested_model_mismatch"
                continue
            observed_models = task_state.get("observed_models") or []
            if observed_models and observed_models != [requested_model]:
                skipped_tasks.append(task_id)
                skipped_task_reasons[task_id] = "worker_observed_model_mismatch"
                continue
            validation_item = validation_by_task.get(task_id, {})
            if validation_item.get("status") not in {"evidence_checked", "needs_review"}:
                skipped_tasks.append(task_id)
                skipped_task_reasons[task_id] = "missing_or_invalid_evidence_validation"
                continue

            payload = read_json(response_path)
            role = task["worker_role"]
            schema_path = WORKER_SCHEMA_PATHS[role]
            schema = read_json(schema_path)
            schema_errors = list(Draft202012Validator(schema).iter_errors(payload))
            if schema_errors:
                skipped_tasks.append(task_id)
                skipped_task_reasons[task_id] = "worker_payload_schema_invalid"
                continue
            if payload.get("task_id") != task_id or payload.get("paper_id") != task["paper_id"]:
                skipped_tasks.append(task_id)
                skipped_task_reasons[task_id] = "worker_payload_task_or_paper_id_mismatch"
                continue

            paper = next((item for item in manifest.get("papers", []) if item["paper_id"] == task["paper_id"]), None)
            if not paper:
                skipped_tasks.append(task_id)
                skipped_task_reasons[task_id] = "paper_not_found_in_source_manifest"
                continue

            assigned_source_ids = set(task.get("source_ids", []))
            payload_source_ids = {
                str(reference.get("source_id"))
                for reference in evidence_refs_in_payload(payload)
                if reference.get("source_id")
            }
            payload_source_ids.update(
                str(candidate.get("source_id"))
                for candidate in payload.get("feature_observations", [])
                if candidate.get("source_id")
            )
            payload_source_ids.update(
                str(record.get("source_id"))
                for record in payload.get("result_records", [])
                if record.get("source_id")
            )
            invalid_source_ids = [
                source_id for source_id in payload_source_ids
                if source_id not in assigned_source_ids
                or source_id not in source_map
                or source_map.get(source_id, {}).get("paper_id") != paper["paper_id"]
            ]
            if invalid_source_ids:
                skipped_tasks.append(task_id)
                skipped_task_reasons[task_id] = "evidence_source_outside_assigned_paper:" + ",".join(sorted(invalid_source_ids))
                continue

            candidate_to_persistent: dict[str, tuple[str, str | None, str]] = {}
            for candidate in payload.get("feature_observations", []):
                candidate_ref = candidate["candidate_observation_id"]
                observation_id, registry_feature_id, _ = add_observation(
                    registry,
                    paper_id=paper["paper_id"],
                    task=task,
                    candidate=candidate,
                    source_map=source_map,
                    manifest_root=manifest_root,
                )
                candidate_to_persistent[candidate_ref] = (
                    observation_id,
                    registry_feature_id,
                    candidate["raw_feature_name"],
                )
                if observation_id not in existing_observation_ids:
                    added_observations += 1
                    existing_observation_ids.add(observation_id)
                if registry_feature_id and registry_feature_id not in existing_feature_ids:
                    added_features += 1
                    existing_feature_ids.add(registry_feature_id)

            if role == "source_discovery":
                for result in payload.get("result_records", []):
                    mapping = candidate_to_persistent.get(result["candidate_observation_id"])
                    if not mapping:
                        continue
                    observation_id, registry_feature_id, raw_name = mapping
                    records_to_add.append(
                        make_record(
                            paper=paper,
                            task=task,
                            candidate_record=result,
                            observation_id=observation_id,
                            registry_feature_id=registry_feature_id,
                            raw_feature_name=raw_name,
                            source_map=source_map,
                            manifest_root=manifest_root,
                        )
                    )

            elif role == "feature_audit":
                for omission in payload.get("omission_candidates", []):
                    omission_id = stable_id("OMIT", task_id, omission["candidate_id"])
                    omissions_to_add.append({
                        "omission_id": omission_id,
                        "paper_id": paper["paper_id"],
                        "worker_task_id": task_id,
                        **omission,
                    })
                for distinction in payload.get("semantic_distinctions", []):
                    left = candidate_to_persistent.get(distinction["candidate_a_id"])
                    right = candidate_to_persistent.get(distinction["candidate_b_id"])
                    if not left or not right:
                        continue
                    distinctions_to_add.append({
                        "semantic_decision_id": stable_id(
                            "SEM", task_id, distinction["candidate_a_id"], distinction["candidate_b_id"]
                        ),
                        "paper_id": paper["paper_id"],
                        "worker_task_id": task_id,
                        "registry_feature_a_id": left[1],
                        "registry_feature_b_id": right[1],
                        "relationship": distinction["relationship"],
                        "rationale": distinction["rationale"],
                        "evidence_refs": distinction["evidence_refs"],
                        "decision_status": "model_recommendation_not_applied",
                    })

            task_summaries.append({
                "task_id": task_id,
                "paper_id": paper["paper_id"],
                "worker_role": role,
                "evidence_validation_status": validation_item.get("status"),
                "observations_seen": len(payload.get("feature_observations", [])),
                "result_records_seen": len(payload.get("result_records", [])),
                "omission_candidates_seen": len(payload.get("omission_candidates", [])),
                "semantic_distinctions_seen": len(payload.get("semantic_distinctions", [])),
                "status": "ingested_as_candidates",
            })

        records_added = write_upsert_jsonl(records_path, records_to_add, "record_id")
        omissions_added = write_upsert_jsonl(
            report_path.parent / "omission_candidates.jsonl",
            omissions_to_add,
            "omission_id",
        )
        distinctions_added = write_upsert_jsonl(
            report_path.parent / "semantic_distinctions.jsonl",
            distinctions_to_add,
            "semantic_decision_id",
        )
        review_summary = build_feature_pair_queue(registry, review_queue_path)
        validate_registry(registry)
        atomic_write_json(registry_path, registry)

    report = {
        "ingestion_version": "0.1",
        "completed_at": utc_now(),
        "registry_path": str(registry_path),
        "records_path": str(records_path),
        "review_queue_path": str(review_queue_path),
        "tasks_considered": len(tasks_by_id),
        "tasks_ingested": len(task_summaries),
        "tasks_skipped": len(skipped_tasks),
        "skipped_task_ids": skipped_tasks,
        "skipped_task_reasons": skipped_task_reasons,
        "new_observations": added_observations,
        "new_provisional_features": added_features,
        "new_result_records": records_added,
        "new_omission_candidates": omissions_added,
        "new_semantic_distinction_recommendations": distinctions_added,
        "registry_observation_count": len(registry["observations"]),
        "registry_feature_count": len(registry["canonical_features"]),
        "registry_decision_count": len(registry["decision_log"]),
        **review_summary,
        "task_summaries": task_summaries,
        "important_note": (
            "No semantic merge, split, or existing FEATURE_ID mapping was applied. "
            "All newly ingested canonical Features remain provisional pending reconciliation."
        ),
    }
    atomic_write_json(report_path, report)
    print(json.dumps({k: v for k, v in report.items() if k != "task_summaries"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
