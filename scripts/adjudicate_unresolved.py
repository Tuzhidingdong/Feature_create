"""Prepare and apply auditable human decisions for unresolved context associations.

Human decisions are never accepted as blind status edits: cited text/table evidence is
re-checked against the immutable chem-literature-preprocessed source package, linked
record/unresolved snapshots must still match, and writes share the Registry lock.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None  # type: ignore[assignment]

try:
    from .validate_evidence import (
        load_manifest,
        verify_quote,
        verify_table_cell,
    )
except ImportError:  # Executed as: python scripts/adjudicate_unresolved.py
    from validate_evidence import (
        load_manifest,
        verify_quote,
        verify_table_cell,
    )


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts" / "manual-context-adjudication.schema.json"


RESOLVED = {"association_confirmed", "no_association"}
DECISIONS = RESOLVED | {"remain_unresolved"}
CONTEXT_ROLES = {
    "sample_identity",
    "condition",
    "time_or_cycle",
    "protocol",
    "statistical_scope",
    "other",
}
COUNTEREVIDENCE_BASES = {
    "explicit_other_sample_mapping",
    "explicit_scope_exclusion",
    "explicit_contradictory_identifier",
    "other_positive_counterevidence",
}


class AdjudicationError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


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
        raise AdjudicationError(f"Unable to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AdjudicationError(f"Expected a JSON object: {path}")
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
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AdjudicationError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
            if not isinstance(value, dict):
                raise AdjudicationError(f"JSONL row at {path}:{line_no} is not an object.")
            rows.append(value)
    return rows


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


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
                raise AdjudicationError(f"Timed out waiting for shared Registry lock: {path}")
            time.sleep(0.2)
    try:
        yield
    finally:
        os.close(descriptor)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def record_candidates(
    unresolved: dict[str, Any],
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    paper_id = str(unresolved.get("paper_id") or "")
    task_id = str(unresolved.get("worker_task_id") or "")
    pool = [
        row for row in records
        if str(row.get("paper_id") or "") == paper_id
        and str(row.get("worker_task_id") or "") == task_id
    ]
    candidate_record_id = unresolved.get("related_candidate_record_id")
    candidate_observation_id = unresolved.get("related_candidate_observation_id")
    if candidate_record_id:
        exact = [
            row for row in pool
            if str(row.get("origin_candidate_record_id") or "") == str(candidate_record_id)
        ]
        return exact, "candidate_record_id"
    if candidate_observation_id:
        exact = [
            row for row in pool
            if str(row.get("origin_candidate_observation_id") or "") == str(candidate_observation_id)
        ]
        if exact:
            return exact, "candidate_observation_id"
        # Backward compatibility for records ingested before the source
        # candidate observation ID was copied into each result record.
        return pool, "manual_selection_within_origin_task_legacy_record"
    return pool, "manual_selection_within_origin_task"


def candidate_card(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": record.get("record_id"),
        "raw_feature_name": record.get("raw_feature_name"),
        "raw_value": record.get("raw_value"),
        "numeric_value": record.get("numeric_value"),
        "reported_unit": record.get("reported_unit"),
        "registry_feature_id": record.get("registry_feature_id"),
        "source_id": record.get("source_id"),
        "source_type": record.get("source_type"),
        "source_location": record.get("source_location", []),
        "evidence_refs": record.get("evidence_refs", []),
        "sample_identity": record.get("sample_identity", []),
        "condition_facts": record.get("condition_facts", []),
        "time_cycle_facts": record.get("time_cycle_facts", []),
        "protocol_facts": record.get("protocol_facts", []),
        "other_context_facts": record.get("other_context_facts", []),
    }


def paper_source_text_hashes(
    manifest: dict[str, Any],
    paper_id: str,
    manifest_root: Path,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for source in manifest.get("sources", []):
        if str(source.get("paper_id") or "") != paper_id:
            continue
        relative = source.get("extracted_text_path")
        if not relative:
            continue
        path = (manifest_root / str(relative)).resolve()
        try:
            path.relative_to(manifest_root.resolve())
        except ValueError as exc:
            raise AdjudicationError(
                f"Prepared text path escapes prepared root for source {source.get('source_id')}."
            ) from exc
        if not path.is_file():
            raise AdjudicationError(f"Prepared source text is missing: {path}")
        result[str(source["source_id"])] = sha256_file(path)
    return result


def human_revision(unresolved: dict[str, Any]) -> int:
    history = unresolved.get("manual_review_history") or []
    return 1 + sum(
        1 for item in history
        if isinstance(item, dict) and item.get("actor") == "human"
    )


def prepare_templates(
    manifest_path: Path,
    registry_path: Path,
    records_path: Path,
    unresolved_path: Path,
    output_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    registry_path = registry_path.resolve()
    lock_path = registry_path.with_suffix(registry_path.suffix + ".lock")
    with exclusive_lock(lock_path):
        manifest, _sources, manifest_root = load_manifest(manifest_path)
        records = read_jsonl(records_path.resolve())
        unresolved_items = read_jsonl(unresolved_path.resolve())
        manifest_hash = sha256_file(manifest_path)
        templates = []
        skipped: list[dict[str, str]] = []
        for unresolved in sorted(
            unresolved_items, key=lambda row: str(row.get("unresolved_id") or "")
        ):
            if unresolved.get("item_type") != "missing_context":
                continue
            if str(unresolved.get("resolution_status") or "pending") in RESOLVED:
                continue
            unresolved_id = str(unresolved.get("unresolved_id") or "")
            if not unresolved_id:
                skipped.append({"unresolved_id": "", "reason": "missing_unresolved_id"})
                continue
            candidates, link_basis = record_candidates(unresolved, records)
            if not candidates:
                skipped.append({
                    "unresolved_id": unresolved_id,
                    "reason": "no_result_record_candidate_in_origin_task",
                })
                continue
            snapshot_map = {
                str(record["record_id"]): sha256_value(record)
                for record in candidates if record.get("record_id")
            }
            if not snapshot_map:
                skipped.append({"unresolved_id": unresolved_id, "reason": "candidate_records_missing_record_id"})
                continue
            selected_record_id = ""
            if len(candidates) == 1:
                selected_record_id = str(candidates[0]["record_id"])
            revision = human_revision(unresolved)
            paper_id = str(unresolved.get("paper_id") or "")
            source_hashes = paper_source_text_hashes(manifest, paper_id, manifest_root)
            templates.append({
                "template_version": "0.1",
                "decision_id": f"MANUAL-{unresolved_id}-R{revision}",
                "unresolved_id": unresolved_id,
                "paper_id": paper_id,
                "worker_task_id": unresolved.get("worker_task_id"),
                "item_type": unresolved.get("item_type"),
                "description": unresolved.get("description"),
                "current_resolution_status": unresolved.get("resolution_status") or "pending",
                "related_candidate_record_id": unresolved.get("related_candidate_record_id"),
                "related_candidate_observation_id": unresolved.get("related_candidate_observation_id"),
                "unresolved_evidence_refs": unresolved.get("evidence_refs", []),
                "cross_chunk_target": unresolved.get("cross_chunk_target"),
                "prior_resolution_history": unresolved.get("resolution_history", []),
                "prior_manual_review_history": unresolved.get("manual_review_history", []),
                "record_id": selected_record_id,
                "record_selection_required": len(candidates) != 1,
                "record_linkage_basis": link_basis,
                "candidate_records": [candidate_card(record) for record in candidates],
                "candidate_record_snapshot_sha256": snapshot_map,
                "unresolved_snapshot_sha256": sha256_value(unresolved),
                "source_manifest_sha256": manifest_hash,
                "paper_source_text_sha256": source_hashes,
                "decision": "remain_unresolved",
                "rationale": "",
                "counterevidence_basis": None,
                "review_evidence_refs": [],
                "context_facts": [],
                "instructions": (
                    "Choose exactly one record_id from candidate_records. Then set decision to "
                    "association_confirmed, no_association, or remain_unresolved; provide a factual "
                    "rationale. Confirm/no-association require verified current+target source quotes. "
                    "For association_confirmed add one or more context_facts with their own two-sided "
                    "evidence_refs. For no_association select a positive counterevidence_basis."
                ),
            })
        atomic_write_jsonl(output_path.resolve(), templates)
        report = {
            "adjudication_version": "0.1",
            "mode": "prepare",
            "prepared_at": utc_now(),
            "template_path": str(output_path.resolve()),
            "template_sha256": sha256_file(output_path.resolve()),
            "source_manifest_sha256": manifest_hash,
            "unresolved_item_count": len(unresolved_items),
            "template_count": len(templates),
            "skipped_count": len(skipped),
            "skipped": skipped,
            "status": "ready_for_human_review" if templates else "no_reviewable_items",
        }
        atomic_write_json(report_path.resolve(), report)
        return report


def validate_reference(
    reference: dict[str, Any],
    paper_id: str,
    sources: dict[str, dict[str, Any]],
    output_root: Path,
) -> tuple[bool, str]:
    side = reference.get("evidence_side")
    if side not in {"current", "target"}:
        return False, "evidence_side must be current or target."
    source_id = str(reference.get("source_id") or "")
    source = sources.get(source_id)
    if not source:
        return False, f"Unknown source_id: {source_id}"
    if str(source.get("paper_id") or "") != paper_id:
        return False, f"Source {source_id} belongs to another paper."
    locator = reference.get("locator")
    if not isinstance(locator, dict) or not locator:
        return False, f"Evidence reference for {source_id} has no locator."
    has_location = any(locator.get(key) is not None for key in (
        "page_number", "page", "table_id", "figure_id", "panel",
        "row_index_1based", "row_number", "row", "cell_address", "cell",
    ))
    if not has_location:
        return False, f"Evidence reference for {source_id} needs a page, figure, or table-cell locator."
    status = reference.get("evidence_status")
    quote = reference.get("quote_or_cell_value")
    if status not in {"verified_text", "verified_table_cell"}:
        return False, "Human association decisions require locally verifiable text/table-cell evidence."
    if not isinstance(quote, str) or not quote.strip():
        return False, f"Evidence quote is empty for source {source_id}."
    if status == "verified_table_cell":
        table_result, note = verify_table_cell(source, locator, quote)
        if table_result is not True:
            return False, f"Table evidence for {source_id} failed: {note}"
        return True, note
    ok, note = verify_quote(source, quote, output_root)
    if not ok:
        return False, f"Text evidence for {source_id} failed: {note}"
    return True, note


def validate_reference_set(
    references: Any,
    paper_id: str,
    sources: dict[str, dict[str, Any]],
    output_root: Path,
    require_both_sides: bool,
) -> tuple[bool, str, list[dict[str, Any]]]:
    if not isinstance(references, list):
        return False, "review_evidence_refs/context-fact evidence_refs must be an array.", []
    verified: list[dict[str, Any]] = []
    sides: set[str] = set()
    for index, reference in enumerate(references):
        if not isinstance(reference, dict):
            return False, f"Evidence reference {index} is not an object.", []
        ok, note = validate_reference(reference, paper_id, sources, output_root)
        if not ok:
            return False, f"Evidence reference {index}: {note}", []
        verified.append(reference)
        sides.add(str(reference["evidence_side"]))
    if require_both_sides and not {"current", "target"}.issubset(sides):
        return False, "Evidence is required from both current and target sides.", []
    return True, "verified", verified


def target_field(context_role: str) -> str:
    return {
        "sample_identity": "sample_identity",
        "condition": "condition_facts",
        "time_or_cycle": "time_cycle_facts",
        "protocol": "protocol_facts",
        "statistical_scope": "other_context_facts",
        "other": "other_context_facts",
    }[context_role]


def add_human_context_fact(
    record: dict[str, Any],
    fact: dict[str, Any],
    decision_id: str,
    unresolved_id: str,
) -> tuple[int, int]:
    if fact.get("context_role") not in CONTEXT_ROLES:
        raise AdjudicationError(f"Unsupported context_role: {fact.get('context_role')}")
    raw_label = str(fact.get("raw_label") or "").strip()
    raw_value = str(fact.get("raw_value") or "").strip()
    if not raw_label or not raw_value:
        raise AdjudicationError("Context facts require non-empty raw_label and raw_value.")
    field = target_field(str(fact["context_role"]))
    bucket = record.setdefault(field, [])
    new_refs = fact.get("evidence_refs") or []
    for existing in bucket:
        same_fact = (
            str(existing.get("raw_label") or "").strip().casefold() == raw_label.casefold()
            and str(existing.get("raw_value") or "").strip().casefold() == raw_value.casefold()
            and (existing.get("unit") or None) == (fact.get("unit") or None)
            and existing.get("context_role") == fact.get("context_role")
        )
        if not same_fact:
            continue
        refs = existing.setdefault("evidence_refs", [])
        existing_signatures = {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in refs}
        for reference in new_refs:
            signature = json.dumps(reference, sort_keys=True, ensure_ascii=False)
            if signature not in existing_signatures:
                refs.append(reference)
                existing_signatures.add(signature)
        existing.setdefault("human_adjudication_ids", [])
        if decision_id not in existing["human_adjudication_ids"]:
            existing["human_adjudication_ids"].append(decision_id)
        return 0, 1

    bucket.append({
        "raw_label": raw_label,
        "raw_value": raw_value,
        "unit": fact.get("unit"),
        "context_role": fact["context_role"],
        "evidence_refs": list(new_refs),
        "human_adjudication_ids": [decision_id],
        "origin_unresolved_id": unresolved_id,
    })
    return 1, 0


def validate_decision(
    item: dict[str, Any],
    template_map: dict[str, dict[str, Any]],
    record_map: dict[str, dict[str, Any]],
    unresolved_map: dict[str, dict[str, Any]],
    source_manifest_hash: str,
    manifest: dict[str, Any],
    output_root: Path,
) -> tuple[bool, str, dict[str, Any] | None]:
    decision_id = str(item.get("decision_id") or "")
    unresolved_id = str(item.get("unresolved_id") or "")
    template = template_map.get(unresolved_id)
    if not template:
        return False, f"No prepared template for unresolved_id {unresolved_id}.", None
    if item.get("template_version") != "0.1":
        return False, f"{decision_id}: template_version must be 0.1.", None
    if item.get("source_manifest_sha256") != source_manifest_hash:
        return False, f"{decision_id}: source manifest changed after template preparation.", None
    if item.get("source_manifest_sha256") != template.get("source_manifest_sha256"):
        return False, f"{decision_id}: source-manifest snapshot differs from prepared template.", None

    current_unresolved = unresolved_map.get(unresolved_id)
    if not current_unresolved:
        return False, f"{decision_id}: unresolved item no longer exists.", None
    if sha256_value(current_unresolved) != template.get("unresolved_snapshot_sha256"):
        return False, f"{decision_id}: unresolved item changed after template preparation; rebuild the template.", None
    if sha256_value(current_unresolved) != item.get("unresolved_snapshot_sha256"):
        return False, f"{decision_id}: unresolved snapshot was edited or is stale.", None

    record_id = str(item.get("record_id") or "")
    if record_id not in set(template.get("candidate_record_snapshot_sha256", {})):
        return False, f"{decision_id}: choose one record_id from candidate_records.", None
    record = record_map.get(record_id)
    if not record:
        return False, f"{decision_id}: selected result record no longer exists.", None
    if sha256_value(record) != template["candidate_record_snapshot_sha256"][record_id]:
        return False, f"{decision_id}: selected result record changed after template preparation.", None
    if sha256_value(record) != item.get("selected_record_snapshot_sha256", template["candidate_record_snapshot_sha256"][record_id]):
        return False, f"{decision_id}: selected record snapshot is stale or was edited.", None
    if (
        str(record.get("paper_id") or "") != str(current_unresolved.get("paper_id") or "")
        or str(record.get("worker_task_id") or "") != str(current_unresolved.get("worker_task_id") or "")
    ):
        return False, f"{decision_id}: selected record is outside the unresolved item's paper/origin task.", None

    decision = item.get("decision")
    rationale = str(item.get("rationale") or "").strip()
    if decision not in DECISIONS:
        return False, f"{decision_id}: decision must be one of {sorted(DECISIONS)}.", None
    if len(rationale) < 12:
        return False, f"{decision_id}: provide a specific rationale of at least 12 characters.", None

    sources = {
        str(source.get("source_id")): source
        for source in manifest.get("sources", [])
        if source.get("source_id")
    }
    review_refs = item.get("review_evidence_refs") or []
    if decision in RESOLVED:
        ok, note, verified_refs = validate_reference_set(
            review_refs, str(current_unresolved.get("paper_id") or ""),
            sources, output_root, require_both_sides=True,
        )
        if not ok:
            return False, f"{decision_id}: {note}", None
    else:
        if review_refs:
            ok, note, verified_refs = validate_reference_set(
                review_refs, str(current_unresolved.get("paper_id") or ""),
                sources, output_root, require_both_sides=False,
            )
            if not ok:
                return False, f"{decision_id}: {note}", None
        else:
            verified_refs = []

    facts = item.get("context_facts") or []
    if decision == "association_confirmed":
        if item.get("counterevidence_basis"):
            return False, f"{decision_id}: association_confirmed must not set counterevidence_basis.", None
        if not isinstance(facts, list) or not facts:
            return False, f"{decision_id}: association_confirmed requires context_facts.", None
        for index, fact in enumerate(facts):
            if not isinstance(fact, dict):
                return False, f"{decision_id}: context_facts/{index} must be an object.", None
            ok, note, fact_refs = validate_reference_set(
                fact.get("evidence_refs"), str(current_unresolved.get("paper_id") or ""),
                sources, output_root, require_both_sides=True,
            )
            if not ok:
                return False, f"{decision_id}: context_facts/{index}: {note}", None
            if fact.get("context_role") not in CONTEXT_ROLES:
                return False, f"{decision_id}: context_facts/{index} has an invalid context_role.", None
            if not str(fact.get("raw_label") or "").strip() or not str(fact.get("raw_value") or "").strip():
                return False, f"{decision_id}: context_facts/{index} requires raw_label/raw_value.", None
    elif facts:
        return False, f"{decision_id}: only association_confirmed may add context_facts.", None

    if decision == "no_association":
        basis = item.get("counterevidence_basis")
        if basis not in COUNTEREVIDENCE_BASES:
            return False, f"{decision_id}: no_association requires a positive counterevidence_basis.", None
        if basis == "other_positive_counterevidence" and len(rationale) < 30:
            return False, f"{decision_id}: explain other_positive_counterevidence in more detail.", None

    if decision == "remain_unresolved" and item.get("counterevidence_basis"):
        return False, f"{decision_id}: remain_unresolved must not set counterevidence_basis.", None

    expected_text_hashes = template.get("paper_source_text_sha256") or {}
    current_text_hashes = paper_source_text_hashes(manifest, str(current_unresolved.get("paper_id") or ""), output_root)
    if expected_text_hashes != current_text_hashes:
        return False, f"{decision_id}: prepared parsed-text inventory changed; rebuild the template.", None

    return True, "validated", {
        "decision_id": decision_id,
        "unresolved_id": unresolved_id,
        "record_id": record_id,
        "decision": decision,
        "rationale": rationale,
        "counterevidence_basis": item.get("counterevidence_basis"),
        "review_evidence_refs": verified_refs,
        "context_facts": facts,
        "template": template,
    }


def apply_one(
    record: dict[str, Any],
    unresolved: dict[str, Any],
    validated: dict[str, Any],
) -> dict[str, Any]:
    decision_id = validated["decision_id"]
    decision = validated["decision"]
    unresolved_id = validated["unresolved_id"]
    if any(
        item.get("decision_id") == decision_id
        for item in unresolved.get("manual_review_history", [])
        if isinstance(item, dict)
    ):
        raise AdjudicationError(f"Decision {decision_id} has already been applied.")

    added = 0
    enriched = 0
    if decision == "association_confirmed":
        for fact in validated["context_facts"]:
            count_added, count_enriched = add_human_context_fact(
                record, fact, decision_id, unresolved_id
            )
            added += count_added
            enriched += count_enriched

    reviewed_at = utc_now()
    history_item = {
        "decision_id": decision_id,
        "actor": "human",
        "decision": decision,
        "rationale": validated["rationale"],
        "counterevidence_basis": validated["counterevidence_basis"],
        "review_evidence_refs": validated["review_evidence_refs"],
        "context_facts_added": added,
        "context_facts_enriched": enriched,
        "reviewed_at": reviewed_at,
    }
    unresolved.setdefault("manual_review_history", []).append(history_item)
    record.setdefault("context_review_history", []).append({
        **history_item,
        "unresolved_id": unresolved_id,
    })

    if decision in RESOLVED:
        unresolved["resolution_status"] = decision
        unresolved["resolution_rationale"] = validated["rationale"]
        unresolved["resolution_evidence_refs"] = validated["review_evidence_refs"]
        unresolved["resolution_actor"] = "human"
        unresolved["resolution_decision_id"] = decision_id
        unresolved["resolved_at"] = reviewed_at
    else:
        unresolved["last_human_review_at"] = reviewed_at
        unresolved["last_human_review_decision"] = "remain_unresolved"

    linked_issue = next((
        issue for issue in record.get("unresolved_issues", [])
        if isinstance(issue, dict) and issue.get("unresolved_id") == unresolved_id
    ), None)
    if linked_issue is None:
        linked_issue = {
            "unresolved_id": unresolved_id,
            "item_type": unresolved.get("item_type"),
            "description": unresolved.get("description"),
            "evidence_refs": unresolved.get("evidence_refs", []),
            "cross_chunk_target": unresolved.get("cross_chunk_target"),
        }
        record.setdefault("unresolved_issues", []).append(linked_issue)
    linked_issue["resolution_status"] = unresolved.get("resolution_status", "pending")
    linked_issue["manual_review_history"] = unresolved["manual_review_history"]
    if decision in RESOLVED:
        linked_issue["resolution_rationale"] = validated["rationale"]
        linked_issue["resolution_evidence_refs"] = validated["review_evidence_refs"]
        linked_issue["resolution_actor"] = "human"
        linked_issue["resolution_decision_id"] = decision_id
        linked_issue["resolved_at"] = reviewed_at
    else:
        linked_issue["last_human_review_at"] = reviewed_at
        linked_issue["last_human_review_decision"] = "remain_unresolved"

    return {
        "decision_id": decision_id,
        "unresolved_id": unresolved_id,
        "record_id": record.get("record_id"),
        "decision": decision,
        "context_facts_added": added,
        "context_facts_enriched": enriched,
        "resolution_status_after": unresolved.get("resolution_status", "pending"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare or apply evidence-checked human adjudications for unresolved context links."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true", help="Create a human-editable decision template JSONL.")
    mode.add_argument("--apply", action="store_true", help="Validate and apply the completed decision JSONL.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True, help="Shared Registry lock target; Registry content is not modified.")
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--unresolved", type=Path, required=True)
    parser.add_argument("--output-template", type=Path, help="Required with --prepare.")
    parser.add_argument("--template", type=Path, help="Immutable prepared template; required with --apply.")
    parser.add_argument("--decisions", type=Path, help="Human-edited copy of the prepared template; required with --apply.")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.prepare and not args.output_template:
        parser.error("--prepare requires --output-template.")
    if args.apply and (not args.decisions or not args.template):
        parser.error("--apply requires both --template (kept unchanged) and --decisions (human-edited copy).")

    try:
        if args.prepare:
            report = prepare_templates(
                args.manifest, args.registry, args.records, args.unresolved,
                args.output_template, args.report,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0

        manifest_path = args.manifest.resolve()
        registry_path = args.registry.resolve()
        records_path = args.records.resolve()
        unresolved_path = args.unresolved.resolve()
        template_rows = read_jsonl(args.template.resolve())
        decisions = read_jsonl(args.decisions.resolve())
        if not template_rows:
            raise AdjudicationError("Prepared template contains no rows.")
        if not decisions:
            raise AdjudicationError("Decision file contains no rows.")
        if sha256_file(args.template.resolve()) == sha256_file(args.decisions.resolve()):
            raise AdjudicationError("Decision file is still identical to the prepared template; make a separate human-edited copy.")
        if not registry_path.is_file():
            raise AdjudicationError(f"Registry lock target does not exist: {registry_path}")
        if Draft202012Validator is None:
            raise AdjudicationError("Install jsonschema from requirements.txt.")
        schema = read_json(SCHEMA_PATH)
        validator = Draft202012Validator(schema)

        lock_path = registry_path.with_suffix(registry_path.suffix + ".lock")
        with exclusive_lock(lock_path):
            manifest, sources, output_root = load_manifest(manifest_path)
            manifest_hash = sha256_file(manifest_path)
            records_original = read_jsonl(records_path)
            unresolved_original = read_jsonl(unresolved_path)
            records_map = {str(row.get("record_id")): row for row in records_original}
            unresolved_map = {str(row.get("unresolved_id")): row for row in unresolved_original}

            templates_by_id = {
                str(item.get("unresolved_id")): item
                for item in template_rows
            }
            if len(templates_by_id) != len(template_rows):
                raise AdjudicationError("Prepared template contains duplicate unresolved IDs.")
            if {str(item.get("unresolved_id")) for item in decisions} != set(templates_by_id):
                raise AdjudicationError("Decision file must contain exactly the unresolved IDs in the prepared template.")
            errors: list[str] = []
            validated: list[dict[str, Any]] = []
            seen_unresolved: set[str] = set()
            seen_decision_ids: set[str] = set()
            for item in decisions:
                unresolved_id = str(item.get("unresolved_id") or "")
                decision_id = str(item.get("decision_id") or "")
                if unresolved_id in seen_unresolved:
                    errors.append(f"Duplicate unresolved_id in decision file: {unresolved_id}")
                    continue
                seen_unresolved.add(unresolved_id)
                if not decision_id or decision_id in seen_decision_ids:
                    errors.append(f"Missing or duplicate decision_id: {decision_id}")
                    continue
                seen_decision_ids.add(decision_id)
                prepared_template = templates_by_id.get(unresolved_id)
                if not prepared_template:
                    errors.append(f"No prepared template for unresolved_id {unresolved_id}.")
                    continue
                schema_errors = sorted(
                    validator.iter_errors(item),
                    key=lambda error: list(error.absolute_path),
                )
                if schema_errors:
                    errors.append(
                        f"{decision_id}: contract validation failed: "
                        + "; ".join(
                            f"{'/'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
                            for error in schema_errors
                        )
                    )
                    continue
                immutable_fields = (
                    "template_version", "decision_id", "paper_id", "worker_task_id",
                    "candidate_record_snapshot_sha256", "unresolved_snapshot_sha256",
                    "source_manifest_sha256", "paper_source_text_sha256",
                    "candidate_records", "related_candidate_record_id",
                    "related_candidate_observation_id", "unresolved_evidence_refs",
                    "cross_chunk_target", "prior_resolution_history",
                    "prior_manual_review_history",
                )
                if any(item.get(field) != prepared_template.get(field) for field in immutable_fields):
                    errors.append(f"{decision_id}: prepared template metadata was modified; restore it from the unchanged template file.")
                    continue
                ok, note, result = validate_decision(
                    item, templates_by_id, records_map, unresolved_map,
                    manifest_hash, manifest, output_root,
                )
                if not ok or result is None:
                    errors.append(note)
                else:
                    validated.append(result)

            if errors:
                report = {
                    "adjudication_version": "0.1",
                    "mode": "apply",
                    "applied_at": utc_now(),
                    "decision_count": len(decisions),
                    "validated_count": len(validated),
                    "applied_count": 0,
                    "status": "blocked",
                    "template_path": str(args.template.resolve()),
                    "template_sha256": sha256_file(args.template.resolve()),
                    "decision_path": str(args.decisions.resolve()),
                    "decision_sha256": sha256_file(args.decisions.resolve()),
                    "source_manifest_sha256": manifest_hash,
                    "errors": errors,
                }
                atomic_write_json(args.report.resolve(), report)
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 2

            records_working = copy.deepcopy(records_map)
            unresolved_working = copy.deepcopy(unresolved_map)
            applied = []
            for item in validated:
                record = records_working[item["record_id"]]
                unresolved = unresolved_working[item["unresolved_id"]]
                applied.append(apply_one(record, unresolved, item))

            # Keep stable JSONL order from the source files.
            new_records = [records_working[str(row.get("record_id"))] for row in records_original]
            new_unresolved = [unresolved_working[str(row.get("unresolved_id"))] for row in unresolved_original]
            atomic_write_jsonl(records_path, new_records)
            atomic_write_jsonl(unresolved_path, new_unresolved)

        report = {
            "adjudication_version": "0.1",
            "mode": "apply",
            "applied_at": utc_now(),
            "template_path": str(args.template.resolve()),
            "template_sha256": sha256_file(args.template.resolve()),
            "decision_path": str(args.decisions.resolve()),
            "decision_sha256": sha256_file(args.decisions.resolve()),
            "source_manifest_sha256": manifest_hash,
            "decision_count": len(decisions),
            "validated_count": len(validated),
            "applied_count": len(applied),
            "decisions": applied,
            "status": "applied",
        }
        atomic_write_json(args.report.resolve(), report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (AdjudicationError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
