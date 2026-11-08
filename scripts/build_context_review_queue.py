"""Build narrowly scoped Worker B tasks for unresolved cross-chunk context links.

The builder never scans/re-parses PDFs. It reads the prepared source manifest,
the original Worker A task queue, ingested unresolved items, and result records.
Only current and explicitly nominated target evidence units are included.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = ROOT / "worker_prompts" / "worker_b_cross_chunk_context.md"
MODEL_CONFIG = ROOT / "config" / "execution_policy.json"
MAX_TARGET_UNITS = 8


class ContextQueueError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContextQueueError(f"Unable to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContextQueueError(f"Expected JSON object: {path}")
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
                raise ContextQueueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
            if not isinstance(item, dict):
                raise ContextQueueError(f"Expected JSON object at {path}:{line_no}")
            rows.append(item)
    return rows


def stable_id(prefix: str, *parts: str, length: int = 16) -> str:
    material = "|".join(parts)
    return f"{prefix}-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:length].upper()}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_source_text(source: dict[str, Any], root: Path) -> str:
    relative = source.get("extracted_text_path")
    if not relative:
        return ""
    path = (root / str(relative)).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return ""
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _row_indices(locator: dict[str, Any]) -> list[int]:
    values = locator.get("row_indices_1based") or []
    if locator.get("row_index_1based") is not None:
        values = [*values, locator["row_index_1based"]]
    if locator.get("row_index_start_1based") is not None and locator.get("row_index_end_1based") is not None:
        values = [
            *values,
            *range(
                int(locator["row_index_start_1based"]),
                int(locator["row_index_end_1based"]) + 1,
            ),
        ]
    return sorted({int(value) for value in values})


def render_unit(
    unit: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    root: Path,
) -> tuple[str, list[str]]:
    source_ids = [str(value) for value in unit.get("source_ids", [])]
    primary_id = str(unit.get("source_id") or (source_ids[0] if source_ids else ""))
    source = sources.get(primary_id, {})
    locator = unit.get("locator") or {}
    attachments = []

    for source_id in source_ids:
        asset = sources.get(source_id, {})
        attachment = asset.get("attachment_path")
        if asset.get("is_visual_asset") and attachment:
            path = Path(str(attachment)).resolve()
            if path.is_file():
                attachments.append(str(path))

    header = [
        f"UNIT_ID: {unit.get('unit_id')}",
        f"CHUNK_ID: {unit.get('chunk_id')}",
        f"SOURCE_ID: {primary_id}",
        f"SOURCE_IDS: {', '.join(source_ids)}",
        f"SOURCE_TYPE: {unit.get('source_type')}",
        f"UNIT_KIND: {unit.get('unit_kind')}",
        f"DOC_TYPE: {unit.get('doc_type')}",
        f"PAGE: {unit.get('page_number')}",
        f"ORIGINAL_LOCATOR_JSON: {json.dumps(locator, ensure_ascii=False, sort_keys=True)}",
        "",
    ]

    if unit.get("unit_kind") == "text":
        text = safe_source_text(source, root)
        start = locator.get("source_text_offset_start")
        end = locator.get("source_text_offset_end")
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start or end > len(text):
            return "\n".join(header) + "[BLOCKED: exact text range could not be resolved.]\n", attachments
        content = text[start:end]
        return "\n".join(header) + "ASSIGNED TEXT RANGE:\n" + content + "\n", attachments

    if unit.get("table_id") or locator.get("table_id"):
        table = source.get("table_data") or {}
        rows = table.get("rows") or []
        table_id = str(unit.get("table_id") or locator.get("table_id") or table.get("table_id") or "")
        selected_rows = _row_indices(locator)
        cell_fragments = locator.get("cell_fragments") or []
        lines = [
            *header,
            f"TABLE_ID: {table_id}",
            f"TABLE_CAPTION: {table.get('caption') or source.get('caption') or ''}",
            "ORIGINAL UPSTREAM TABLE ROWS/CELL FRAGMENTS:",
        ]
        if cell_fragments:
            for cell in cell_fragments:
                row_index = int(cell["row_index_1based"])
                column_index = int(cell["column_index_1based"])
                if not (1 <= row_index <= len(rows) and 1 <= column_index <= len(rows[row_index - 1])):
                    continue
                value = rows[row_index - 1][column_index - 1]
                cell_text = "" if value is None else str(value)
                start = int(cell.get("cell_text_offset_start", 0))
                end = int(cell.get("cell_text_offset_end", len(cell_text)))
                lines.append(json.dumps({
                    "row_index_1based": row_index,
                    "column_index_1based": column_index,
                    "cell_text_offset_start": start,
                    "cell_text_offset_end": end,
                    "cell_text_segment_index_1based": cell.get("cell_text_segment_index_1based"),
                    "cell_text_segment_count": cell.get("cell_text_segment_count"),
                    "cell_text_fragment": cell_text[start:end],
                }, ensure_ascii=False))
        else:
            for row_index in selected_rows:
                if 1 <= row_index <= len(rows):
                    lines.append(json.dumps({
                        "row_index_1based": row_index,
                        "cells": rows[row_index - 1],
                    }, ensure_ascii=False))
        return "\n".join(lines) + "\n", attachments

    text = safe_source_text(source, root)
    if text:
        return "\n".join(header) + "ASSIGNED SOURCE TEXT:\n" + text + "\n", attachments
    return "\n".join(header) + "[No text content is available; inspect only the attached extracted visual asset, if present.]\n", attachments


def _is_valid_relation(
    relation_basis: str,
    current_unit_ids: set[str],
    target_unit_ids: set[str],
    paper_index: dict[str, Any],
) -> bool:
    pairs = {
        (left, right)
        for boundary in paper_index.get("cross_chunk_text_boundaries", [])
        for left, right in [(
            str((boundary.get("from_segment") or {}).get("unit_id") or ""),
            str((boundary.get("to_segment") or {}).get("unit_id") or ""),
        )]
    }
    if relation_basis == "adjacent_text_boundary":
        return any(
            (left in current_unit_ids and right in target_unit_ids)
            or (right in current_unit_ids and left in target_unit_ids)
            for left, right in pairs
        )
    if relation_basis == "same_original_table_row":
        for group in paper_index.get("same_original_table_row_groups", []):
            ids = {str(item.get("unit_id")) for item in group.get("units", [])}
            if ids & current_unit_ids and ids & target_unit_ids:
                return True
        return False
    if relation_basis == "main_si_reference":
        navigation = {
            str(item.get("unit_id")): item
            for item in paper_index.get("unit_navigation", [])
            if item.get("unit_id")
        }
        current_main_pages = {
            int(navigation[unit_id]["page_number"])
            for unit_id in current_unit_ids
            if unit_id in navigation
            and str(navigation[unit_id].get("doc_type") or "").upper() == "MAIN"
            and navigation[unit_id].get("page_number") is not None
        }
        target_evidence_ids = set()
        for unit_id in target_unit_ids:
            item = navigation.get(unit_id, {})
            if item.get("table_id"):
                target_evidence_ids.add(str(item["table_id"]).casefold())
            if item.get("figure_id"):
                target_evidence_ids.add(str(item["figure_id"]).casefold())
        return any(
            str(reference.get("source_doc_type") or "").upper() == "MAIN"
            and int(reference.get("source_page_number") or 0) in current_main_pages
            and any(
                str(candidate.get("target_id") or "").casefold() in target_evidence_ids
                for candidate in reference.get("target_candidates", [])
            )
            for reference in paper_index.get("cross_source_references", [])
        )
    # A worker-located target is an explicitly nominated navigation lead, not a
    # verified association. Worker B independently evaluates the actual content.
    return relation_basis == "worker_located"


def _load_unit_from_chunk_index(
    task: dict[str, Any],
    unit_id: str,
) -> dict[str, Any] | None:
    path_value = task.get("chunk_evidence_index_path")
    if not path_value:
        return None
    path = Path(str(path_value)).resolve()
    if not path.is_file():
        return None
    index = read_json(path)
    return next(
        (item for item in index.get("assigned_units", []) if item.get("unit_id") == unit_id),
        None,
    )


def build_context_task(
    unresolved: dict[str, Any],
    record: dict[str, Any],
    current_task: dict[str, Any],
    target_descriptors: list[dict[str, Any]],
    target_units: list[dict[str, Any]],
    current_units: list[dict[str, Any]],
    paper_index: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    root: Path,
    output_root: Path,
    model_ref: str,
    max_chars: int,
    max_attachments: int,
    max_command_chars: int,
    review_attempt: int = 0,
) -> dict[str, Any]:
    unresolved_id = str(unresolved["unresolved_id"])
    current_chunk_id = str(current_task.get("chunk_id") or "")
    target_chunk_ids = sorted({str(item["chunk_id"]) for item in target_units})
    all_units: list[dict[str, Any]] = []
    for unit in [*current_units, *target_units]:
        unit_copy = dict(unit)
        unit_copy["review_side"] = "current" if unit_copy.get("chunk_id") == current_chunk_id else "target"
        all_units.append(unit_copy)
    unit_ids = [str(item["unit_id"]) for item in all_units]
    if len(unit_ids) != len(set(unit_ids)):
        raise ContextQueueError("A unit was assigned to both current and target sides.")
    source_ids = sorted({
        str(source_id)
        for unit in all_units
        for source_id in unit.get("source_ids", [])
    })

    rendered = []
    attachments = []
    for unit in all_units:
        text, unit_attachments = render_unit(unit, sources, root)
        rendered.append((unit, text))
        attachments.extend(unit_attachments)

    current_refs = unresolved.get("evidence_refs") or []
    attachment_paths = sorted(set(attachments))
    record_context = {
        "record_id": record.get("record_id"),
        "raw_feature_name": record.get("raw_feature_name"),
        "raw_value": record.get("raw_value"),
        "numeric_value": record.get("numeric_value"),
        "reported_unit": record.get("reported_unit"),
        "result_type": record.get("result_type"),
        "statistic_label": record.get("statistic_label"),
        "replicate_count": record.get("replicate_count"),
        "sample_identity_already_extracted": record.get("sample_identity", []),
        "condition_facts_already_extracted": record.get("condition_facts", []),
        "time_cycle_facts_already_extracted": record.get("time_cycle_facts", []),
        "protocol_facts_already_extracted": record.get("protocol_facts", []),
        "other_context_facts_already_extracted": record.get("other_context_facts", []),
    }
    attachment_hashes = {
        attachment: sha256_file(Path(attachment))
        for attachment in attachment_paths if Path(attachment).is_file()
    }
    prompt_sha256 = hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest()
    task_fingerprint = hashlib.sha256(json.dumps({
        "unresolved_id": unresolved_id,
        "paper_id": unresolved.get("paper_id"),
        "item_type": unresolved.get("item_type"),
        "description": unresolved.get("description"),
        "cross_chunk_target": unresolved.get("cross_chunk_target"),
        "current_evidence_refs": current_refs,
        "record_context": record_context,
        "current_task_id": current_task.get("task_id"),
        "current_chunk_id": current_chunk_id,
        "target_descriptors": target_descriptors,
        "assigned_units": [
            {
                "unit_id": unit["unit_id"],
                "chunk_id": unit["chunk_id"],
                "review_side": unit["review_side"],
                "locator": unit.get("locator", {}),
                "content_sha256": hashlib.sha256(
                    unit_text.encode("utf-8")
                ).hexdigest(),
            }
            for unit, unit_text in rendered
        ],
        "attachment_hashes": attachment_hashes,
        "model_ref": model_ref,
        "prompt_sha256": prompt_sha256,
        "review_attempt": review_attempt,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    task_id = stable_id("FC-CX", unresolved_id, task_fingerprint, length=14)

    lines = [
        "# Targeted cross-chunk context review task",
        "",
        f"- Task ID: {task_id}",
        f"- Paper ID: {unresolved.get('paper_id')}",
        f"- Unresolved ID: {unresolved_id}",
        f"- Record ID: {record.get('record_id')}",
        f"- Original Worker A task: {current_task.get('task_id')}",
        f"- Current chunk: {current_chunk_id}",
        f"- Target chunk(s): {', '.join(target_chunk_ids)}",
        f"- Context role requested: {(unresolved.get('cross_chunk_target') or {}).get('context_role')}",
        f"- Relation basis nominated by Worker A: {(unresolved.get('cross_chunk_target') or {}).get('relation_basis')}",
        f"- Review attempt: {review_attempt}",
        "",
        "## Unresolved question",
        "",
        str(unresolved.get("description") or ""),
        "",
        "### Worker A target rationale (navigation lead only)",
        "",
        str((unresolved.get("cross_chunk_target") or {}).get("rationale") or ""),
        "",
        "## Existing result record — reference only, do not modify its result fields",
        "",
        json.dumps(record_context, ensure_ascii=False, indent=2),
        "",
        "## Current-side evidence",
        "",
        json.dumps(current_refs, ensure_ascii=False, indent=2),
        "",
        "## Target unit descriptors",
        "",
        json.dumps(target_descriptors, ensure_ascii=False, indent=2),
        "",
        "## Assigned evidence content",
        "",
    ]
    for unit, unit_text in rendered:
        lines.extend([
            f"### {str(unit.get('review_side')).upper()} UNIT {unit['unit_id']} — CHUNK {unit.get('chunk_id')}",
            "",
            unit_text,
            "",
        ])
    bundle = "\n".join(lines)
    if len(bundle) > max_chars:
        raise ContextQueueError(
            f"Rendered cross-chunk context bundle exceeds limit ({len(bundle)} > {max_chars})."
        )
    if len(attachment_paths) > max_attachments:
        raise ContextQueueError(
            f"Cross-chunk context task requires {len(attachment_paths)} visual attachments, over limit {max_attachments}."
        )

    task_dir = output_root / "tasks" / hashlib.sha1(str(unresolved.get("paper_id")).encode("utf-8")).hexdigest()[:10] / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = task_dir / "task_input.md"
    bundle_path.write_text(bundle, encoding="utf-8", newline="\n")

    assigned_units = []
    for unit in all_units:
        assigned_units.append({
            "unit_id": unit["unit_id"],
            "chunk_id": unit["chunk_id"],
            "source_id": unit.get("source_id"),
            "source_ids": unit.get("source_ids", []),
            "source_type": unit.get("source_type"),
            "component": unit.get("component"),
            "unit_kind": unit.get("unit_kind"),
            "doc_type": unit.get("doc_type"),
            "page_number": unit.get("page_number"),
            "table_id": unit.get("table_id"),
            "figure_id": unit.get("figure_id"),
            "locator": unit.get("locator", {}),
            "review_side": unit.get("review_side"),
        })
    manifest_path = task_dir / "context_evidence_manifest.json"
    manifest_path.write_text(json.dumps({
        "manifest_type": "feature_create_cross_chunk_context_evidence",
        "unresolved_id": unresolved_id,
        "record_id": record.get("record_id"),
        "assigned_units": assigned_units,
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

    prompt = PROMPT_PATH
    if not prompt.is_file():
        raise ContextQueueError(f"Worker B cross-chunk prompt missing: {prompt}")
    task_json = {
        "task_id": task_id,
        "contract_version": "0.1",
        "task_type": "cross_chunk_context",
        "paper_id": unresolved.get("paper_id"),
        "doi": current_task.get("doi"),
        "worker_role": "cross_chunk_context",
        "model_ref": model_ref,
        "input_fingerprint": task_fingerprint,
        "review_attempt": review_attempt,
        "prompt_sha256": prompt_sha256,
        "bundle_sha256": hashlib.sha256(bundle.encode("utf-8")).hexdigest(),
        "attachment_sha256": attachment_hashes,
        "context_evidence_manifest_sha256": sha256_file(manifest_path),
        "prompt_path": str(prompt.resolve()),
        "bundle_path": str(bundle_path.resolve()),
        "attachment_paths": [str(manifest_path.resolve()), *attachment_paths],
        "output_dir": str((task_dir / "worker_b").resolve()),
        "task_status": "pending",
        "unresolved_id": unresolved_id,
        "record_id": record.get("record_id"),
        "origin_worker_task_id": current_task.get("task_id"),
        "current_chunk_id": current_chunk_id,
        "target_chunk_ids": target_chunk_ids,
        "current_unit_ids": [unit["unit_id"] for unit in current_units],
        "target_unit_ids": [unit["unit_id"] for unit in target_units],
        "assigned_unit_ids": unit_ids,
        "evidence_unit_map": assigned_units,
        "source_ids": source_ids,
        "context_evidence_manifest_path": str(manifest_path.resolve()),
        "attachment_count": len(attachment_paths) + 1,
        "bundle_char_count": len(bundle),
        "max_bundle_chars": max_chars,
        "max_attachments": max_attachments,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    command_preview = [
        "opencode", "--pure", "run", "--model", model_ref,
        "--agent", "feature_create_worker_b", "--title", task_id,
        "--format", "json", "--dir", str(task_json["output_dir"]),
    ]
    for file_path in [str(prompt.resolve()), str(bundle_path.resolve()), *task_json["attachment_paths"]]:
        command_preview.extend(["--file", file_path])
    command_preview.append(f"Execute targeted cross-chunk context review task {task_id}.")
    command_chars = len(subprocess.list2cmdline(command_preview))
    task_json["command_length_estimate"] = command_chars
    if command_chars > max_command_chars:
        task_json["task_status"] = "blocked_command_line"
    task_json["task_json_path"] = str((task_dir / "task.json").resolve())
    task_json_path = Path(task_json["task_json_path"])
    task_json_path.write_text(json.dumps(task_json, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return task_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build targeted Worker B cross-chunk context review tasks from ingested unresolved items."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True, help="Original Worker A/B task_queue.json.")
    parser.add_argument("--unresolved", type=Path, required=True, help="unresolved_extraction_items.jsonl from Registry ingestion.")
    parser.add_argument("--records", type=Path, required=True, help="result_records.jsonl from Registry ingestion.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-context-chars", type=int, default=60000)
    parser.add_argument("--max-attachments", type=int, default=8)
    parser.add_argument("--max-command-chars", type=int, default=7600)
    parser.add_argument(
        "--retry-inconclusive",
        action="store_true",
        help="Rebuild tasks previously marked insufficient_evidence or conflicting_evidence.",
    )
    args = parser.parse_args(argv)

    if args.max_context_chars < 5000 or args.max_attachments < 1 or args.max_command_chars < 1000:
        parser.error("max-context-chars must be >= 5000; attachment and command limits must be positive.")

    manifest_path = args.manifest.expanduser().resolve()
    manifest_root = manifest_path.parent
    manifest = read_json(manifest_path)
    source_map = {item["source_id"]: item for item in manifest.get("sources", [])}
    queue = read_json(args.queue.expanduser().resolve())
    unresolved_items = read_jsonl(args.unresolved.expanduser().resolve())
    records = read_jsonl(args.records.expanduser().resolve())
    config = read_json(MODEL_CONFIG)
    model_ref = config.get("workers", {}).get("worker_b", {}).get("model_ref")
    if not model_ref:
        raise ContextQueueError("Worker B model_ref is missing from execution_policy.json.")

    primary_tasks = {
        str(task["task_id"]): task
        for task in queue.get("tasks", [])
        if task.get("worker_role") == "source_discovery"
    }
    records_by_origin = {
        (str(item.get("worker_task_id")), str(item.get("origin_candidate_record_id"))): item
        for item in records
        if item.get("origin_candidate_record_id")
    }
    target_tasks_by_chunk = {
        (str(task.get("paper_id")), str(task.get("chunk_id"))): task
        for task in queue.get("tasks", [])
        if task.get("worker_role") == "source_discovery" and task.get("chunk_id")
    }

    output_root = args.output.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    tasks = []
    skipped = []
    blocked = []

    for unresolved in sorted(unresolved_items, key=lambda item: str(item.get("unresolved_id", ""))):
        if unresolved.get("item_type") != "missing_context":
            continue
        resolution_status = str(unresolved.get("resolution_status") or "pending")
        final_statuses = {"association_confirmed", "no_association"}
        inconclusive_statuses = {"insufficient_evidence", "conflicting_evidence"}
        if resolution_status in final_statuses or (
            resolution_status in inconclusive_statuses and not args.retry_inconclusive
        ) or resolution_status not in ({"pending"} | inconclusive_statuses | final_statuses):
            skipped.append({
                "unresolved_id": unresolved.get("unresolved_id"),
                "reason": f"already_reviewed_status:{resolution_status}",
            })
            continue
        target_spec = unresolved.get("cross_chunk_target") or {}
        if not target_spec:
            skipped.append({
                "unresolved_id": unresolved.get("unresolved_id"),
                "reason": "no_explicit_cross_chunk_target",
            })
            continue
        current_task = primary_tasks.get(str(unresolved.get("worker_task_id")))
        if not current_task:
            blocked.append({
                "unresolved_id": unresolved.get("unresolved_id"),
                "reason": "origin_worker_a_task_not_found",
            })
            continue
        record = records_by_origin.get((
            str(unresolved.get("worker_task_id")),
            str(unresolved.get("related_candidate_record_id") or ""),
        ))
        if not record:
            blocked.append({
                "unresolved_id": unresolved.get("unresolved_id"),
                "reason": "linked_result_record_not_found_by_origin_candidate_id",
            })
            continue
        if record.get("paper_id") != unresolved.get("paper_id"):
            blocked.append({
                "unresolved_id": unresolved.get("unresolved_id"),
                "reason": "linked_result_record_paper_mismatch",
            })
            continue

        index_path = Path(str(current_task.get("paper_chunk_index_path") or "")).resolve()
        if not index_path.is_file():
            blocked.append({
                "unresolved_id": unresolved.get("unresolved_id"),
                "reason": "paper_chunk_index_missing",
            })
            continue
        paper_index = read_json(index_path)
        nav_by_id = {
            str(item.get("unit_id")): item
            for item in paper_index.get("unit_navigation", [])
            if item.get("unit_id")
        }
        current_chunk_id = str(current_task.get("chunk_id") or "")
        current_unit_ids = {
            str((ref.get("locator") or {}).get("unit_id"))
            for ref in unresolved.get("evidence_refs", [])
            if (ref.get("locator") or {}).get("unit_id")
        }
        if not current_unit_ids:
            current_unit_ids = {
                str((ref.get("locator") or {}).get("unit_id"))
                for ref in record.get("evidence_refs", [])
                if (ref.get("locator") or {}).get("unit_id")
            }
        if not current_unit_ids:
            blocked.append({
                "unresolved_id": unresolved.get("unresolved_id"),
                "reason": "current_side_evidence_has_no_unit_ids",
            })
            continue

        target_units = []
        target_descriptors = []
        invalid_target = None
        raw_targets = target_spec.get("target_units") or []
        if not 1 <= len(raw_targets) <= MAX_TARGET_UNITS:
            invalid_target = f"target_units_count_must_be_1_to_{MAX_TARGET_UNITS}"
        else:
            for target in raw_targets:
                target_unit_id = str(target.get("unit_id") or "")
                nav = nav_by_id.get(target_unit_id)
                if not nav:
                    invalid_target = f"target_unit_not_in_paper_navigation_index:{target_unit_id}"
                    break
                if str(target.get("chunk_id")) != str(nav.get("chunk_id")):
                    invalid_target = f"target_chunk_does_not_match_unit:{target_unit_id}"
                    break
                if str(target.get("source_id")) not in set(nav.get("source_ids") or []):
                    invalid_target = f"target_source_does_not_match_unit:{target_unit_id}"
                    break
                if str(nav.get("chunk_id")) == current_chunk_id:
                    invalid_target = f"target_unit_is_not_in_another_chunk:{target_unit_id}"
                    break
                if nav.get("unit_id") in current_unit_ids:
                    invalid_target = f"target_unit_duplicates_current_unit:{target_unit_id}"
                    break
                target_task = target_tasks_by_chunk.get((
                    str(unresolved.get("paper_id")), str(nav.get("chunk_id"))
                ))
                if not target_task:
                    invalid_target = f"target_chunk_task_not_found:{nav.get('chunk_id')}"
                    break
                unit = _load_unit_from_chunk_index(target_task, target_unit_id)
                if not unit:
                    invalid_target = f"target_unit_missing_from_chunk_evidence_index:{target_unit_id}"
                    break
                unit["chunk_id"] = str(nav["chunk_id"])
                unit["source_id"] = str(nav.get("source_id") or target.get("source_id"))
                target_units.append(unit)
                target_descriptors.append({
                    "chunk_id": nav.get("chunk_id"),
                    "unit_id": target_unit_id,
                    "source_id": nav.get("source_id"),
                    "unit_kind": nav.get("unit_kind"),
                    "doc_type": nav.get("doc_type"),
                    "page_number": nav.get("page_number"),
                    "table_id": nav.get("table_id"),
                    "figure_id": nav.get("figure_id"),
                    "locator": nav.get("locator", {}),
                })

        if invalid_target:
            blocked.append({
                "unresolved_id": unresolved.get("unresolved_id"),
                "reason": invalid_target,
            })
            continue

        current_index_path = Path(str(current_task.get("chunk_evidence_index_path") or "")).resolve()
        if not current_index_path.is_file():
            blocked.append({
                "unresolved_id": unresolved.get("unresolved_id"),
                "reason": "current_chunk_evidence_index_missing",
            })
            continue
        current_index = read_json(current_index_path)
        current_index_units = {
            str(item.get("unit_id")): item
            for item in current_index.get("assigned_units", [])
            if item.get("unit_id")
        }
        current_units = []
        for unit_id in sorted(current_unit_ids):
            unit = current_index_units.get(unit_id)
            if not unit:
                blocked.append({
                    "unresolved_id": unresolved.get("unresolved_id"),
                    "reason": f"current_unit_not_assigned_to_origin_task:{unit_id}",
                })
                current_units = []
                break
            unit = dict(unit)
            unit["chunk_id"] = current_chunk_id
            current_units.append(unit)
        if not current_units:
            continue

        if not _is_valid_relation(
            str(target_spec.get("relation_basis") or ""),
            current_unit_ids,
            {str(item["unit_id"]) for item in target_units},
            paper_index,
        ):
            blocked.append({
                "unresolved_id": unresolved.get("unresolved_id"),
                "reason": "nominated_target_is_not_connected_by_declared_navigation_relation",
            })
            continue

        review_history = unresolved.get("resolution_history") or []
        review_attempt = len(review_history)
        if args.retry_inconclusive and resolution_status in inconclusive_statuses:
            review_attempt += 1
            if review_attempt < 1:
                review_attempt = 1

        try:
            task = build_context_task(
                unresolved=unresolved,
                record=record,
                current_task=current_task,
                target_descriptors=target_descriptors,
                target_units=target_units,
                current_units=current_units,
                paper_index=paper_index,
                sources=source_map,
                root=manifest_root,
                output_root=output_root,
                model_ref=str(model_ref),
                max_chars=args.max_context_chars,
                max_attachments=args.max_attachments,
                max_command_chars=args.max_command_chars,
                review_attempt=review_attempt,
            )
            tasks.append(task)
        except ContextQueueError as exc:
            blocked.append({
                "unresolved_id": unresolved.get("unresolved_id"),
                "reason": str(exc),
            })

    queue_result = {
        "queue_version": "0.1",
        "queue_type": "cross_chunk_context_review",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest_path": str(manifest_path),
        "source_queue_path": str(args.queue.expanduser().resolve()),
        "task_count": len(tasks),
        "pending_count": sum(task["task_status"] == "pending" for task in tasks),
        "blocked_count": sum(task["task_status"] != "pending" for task in tasks),
        "skipped_unresolved_count": len(skipped),
        "blocked_unresolved_count": len(blocked),
        "retry_inconclusive_enabled": args.retry_inconclusive,
        "skipped_unresolved": skipped,
        "blocked_unresolved": blocked,
        "tasks": tasks,
    }
    queue_path = output_root / "context_review_task_queue.json"
    queue_path.write_text(json.dumps(queue_result, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    summary = {
        "task_count": queue_result["task_count"],
        "pending_count": queue_result["pending_count"],
        "blocked_count": queue_result["blocked_count"],
        "skipped_unresolved_count": len(skipped),
        "blocked_unresolved_count": len(blocked),
        "queue": str(queue_path),
    }
    (output_root / "context_review_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
