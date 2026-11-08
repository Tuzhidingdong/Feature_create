"""Validate worker evidence references against the deterministic source manifest.

This is a first-pass provenance validator. It does not decide whether the
scientific interpretation is correct; it checks source identity, locator
presence, quoted text/cell existence, and unsafe figure-value claims.
"""

from __future__ import annotations

import argparse
import json
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


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = {
    "source_discovery": ROOT / "contracts" / "worker-a.schema.json",
    "feature_audit": ROOT / "contracts" / "worker-b.schema.json",
}


class EvidenceValidationError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceValidationError(f"Unable to read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceValidationError(f"Expected JSON object: {path}")
    return value


def normalize_for_search(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def load_manifest(manifest_path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]], Path]:
    manifest = read_json(manifest_path)
    output_root = manifest_path.resolve().parent
    sources = {item["source_id"]: item for item in manifest.get("sources", [])}
    return manifest, sources, output_root


def read_source_text(source: dict[str, Any], output_root: Path) -> str:
    relative = source.get("extracted_text_path")
    if not relative:
        return ""
    path = (output_root / relative).resolve()
    try:
        path.relative_to(output_root.resolve())
    except ValueError:
        return ""
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def validate_schema(payload: dict[str, Any], role: str) -> list[str]:
    if Draft202012Validator is None:
        raise EvidenceValidationError("Install jsonschema from requirements.txt before validation.")
    schema_path = SCHEMAS[role]
    schema = read_json(schema_path)
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda e: list(e.absolute_path))
    return [
        f"{'/'.join(str(p) for p in error.absolute_path) or '$'}: {error.message}"
        for error in errors
    ]


def evidence_refs_in_payload(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []

    def visit(value: Any, location: str) -> None:
        if isinstance(value, dict):
            refs = value.get("evidence_refs")
            if isinstance(refs, list):
                for index, ref in enumerate(refs):
                    if isinstance(ref, dict):
                        found.append((f"{location}/evidence_refs/{index}", ref))
            for key, child in value.items():
                if key != "evidence_refs":
                    visit(child, f"{location}/{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{location}/{index}")

    visit(payload, "$")
    return found


def verify_quote(
    source: dict[str, Any],
    quote: str,
    output_root: Path,
) -> tuple[bool, str]:
    text = read_source_text(source, output_root)
    if not text:
        return False, "No extracted text is available for this source; cannot verify quote locally."
    normalized_text = normalize_for_search(text)
    normalized_quote = normalize_for_search(quote)
    if not normalized_quote:
        return False, "Quoted evidence value is empty."
    if normalized_quote in normalized_text:
        return True, "quote/cell value found in extracted source text"
    return False, "Quoted text/cell value was not found verbatim after whitespace/Unicode normalization."


def verify_table_cell(
    source: dict[str, Any],
    locator: dict[str, Any],
    quote: str,
) -> tuple[bool | None, str]:
    """Verify a cited 1-based row/column against upstream evidence_index rows.

    None means the worker omitted machine-checkable cell coordinates.
    """
    table = source.get("table_data")
    if not isinstance(table, dict) or not isinstance(table.get("rows"), list):
        return None, "This source has no structured table rows in the upstream evidence index."

    row_value = (
        locator.get("row_index_1based")
        or locator.get("row_number")
        or locator.get("row")
    )
    column_value = (
        locator.get("column_index_1based")
        or locator.get("column_number")
        or locator.get("column")
    )

    def to_positive_index(value: Any) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool):
            return value if value > 0 else None
        if isinstance(value, str):
            match = re.search(r"\d+", value)
            if match:
                index = int(match.group(0))
                return index if index > 0 else None
        return None

    row_index = to_positive_index(row_value)
    column_index = to_positive_index(column_value)
    if row_index is None or column_index is None:
        cell_address = str(locator.get("cell_address") or locator.get("cell") or "").strip()
        match = re.fullmatch(r"([A-Za-z]+)([1-9]\d*)", cell_address)
        if match:
            letters, digits = match.groups()
            column_index = 0
            for char in letters.upper():
                column_index = column_index * 26 + (ord(char) - ord("A") + 1)
            row_index = int(digits)

    if row_index is None or column_index is None:
        return None, "Table-cell locator must include row_index_1based and column_index_1based (or an A1-style cell address)."

    rows = table["rows"]
    if row_index > len(rows) or column_index > len(rows[row_index - 1]):
        return False, (
            f"Table cell is out of range: row {row_index}, column {column_index}; "
            f"table has {len(rows)} rows."
        )
    actual = rows[row_index - 1][column_index - 1]
    if normalize_for_search(str(actual if actual is not None else "")) != normalize_for_search(quote):
        return False, (
            f"Claimed table cell does not match upstream rows at "
            f"row {row_index}, column {column_index}."
        )
    return True, f"exact upstream table cell verified at row {row_index}, column {column_index}"


def load_assigned_chunk_units(task: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Load the exact unit locator slice assigned to a chunked task."""
    path_value = task.get("chunk_evidence_index_path")
    if not task.get("chunk_id"):
        return {}
    if not path_value:
        raise EvidenceValidationError("Chunked task is missing chunk_evidence_index_path.")
    path = Path(str(path_value)).resolve()
    if not path.is_file():
        raise EvidenceValidationError(f"Chunk evidence index is missing: {path}")
    index = read_json(path)
    if index.get("chunk_id") != task.get("chunk_id"):
        raise EvidenceValidationError("Chunk evidence index ID does not match task.json.")
    assigned = index.get("assigned_units")
    if not isinstance(assigned, list):
        raise EvidenceValidationError("Chunk evidence index has no assigned_units array.")
    result = {}
    for item in assigned:
        if isinstance(item, dict) and item.get("unit_id"):
            result[str(item["unit_id"])] = item
    expected = set(task.get("unit_ids") or [])
    if expected and expected != set(result):
        raise EvidenceValidationError("Chunk task unit_ids do not match chunk_evidence_index assigned_units.")
    return result


def verify_quote_in_assigned_text_unit(
    source: dict[str, Any],
    quote: str,
    unit: dict[str, Any],
    output_root: Path,
) -> tuple[bool, str]:
    text = read_source_text(source, output_root)
    locator = unit.get("locator") or {}
    start = locator.get("source_text_offset_start")
    end = locator.get("source_text_offset_end")
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start or end > len(text):
        return False, "Assigned text unit has invalid source offsets."
    normalized_quote = normalize_for_search(quote)
    normalized_assigned_text = normalize_for_search(text[start:end])
    if normalized_quote and normalized_quote in normalized_assigned_text:
        return True, "quote found within the exact assigned text-unit range"
    return False, "Quoted text is not present within the assigned text-unit range."


def verify_table_cell_is_assigned(
    unit: dict[str, Any],
    row_index: int,
    column_index: int,
    locator: dict[str, Any],
) -> tuple[bool, str]:
    unit_locator = unit.get("locator") or {}
    cell_fragments = unit_locator.get("cell_fragments") or []
    if cell_fragments:
        matches = [
            cell for cell in cell_fragments
            if cell.get("row_index_1based") == row_index
            and cell.get("column_index_1based") == column_index
        ]
        if not matches:
            return False, "The cited table cell is not part of this assigned cell-fragment unit."
        start = locator.get("cell_text_offset_start")
        end = locator.get("cell_text_offset_end")
        if start is not None or end is not None:
            if not any(cell.get("cell_text_offset_start") == start and cell.get("cell_text_offset_end") == end for cell in matches):
                return False, "The cited cell text range does not match the assigned cell fragment."
        return True, "table cell belongs to the assigned original row/column fragment"

    allowed_rows = unit_locator.get("row_indices_1based") or []
    if row_index not in allowed_rows:
        return False, "The cited original table row is not assigned to this chunk."
    return True, "table row belongs to this assigned table unit"


def validate_payload(
    payload: dict[str, Any],
    role: str,
    task: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    output_root: Path,
) -> dict[str, Any]:
    schema_errors = validate_schema(payload, role)
    errors = list(schema_errors)
    warnings: list[dict[str, str]] = []
    verified_refs = 0
    unverified_refs = 0
    reference_count = 0
    try:
        assigned_units = load_assigned_chunk_units(task)
    except EvidenceValidationError as exc:
        assigned_units = {}
        errors.append(str(exc))
    is_chunked_task = bool(task.get("chunk_id"))

    if payload.get("task_id") != task.get("task_id"):
        errors.append("Payload task_id does not match task.json.")
    if payload.get("paper_id") != task.get("paper_id"):
        errors.append("Payload paper_id does not match task.json.")

    for location, ref in evidence_refs_in_payload(payload):
        reference_count += 1
        source_id = ref.get("source_id")
        source = sources.get(source_id)
        if not source:
            errors.append(f"{location}: unknown source_id {source_id!r}.")
            continue
        assigned_source_ids = set(task.get("source_ids", []))
        if source_id not in assigned_source_ids:
            errors.append(
                f"{location}: source_id {source_id!r} was not assigned to this worker task."
            )
        if source.get("paper_id") != task.get("paper_id"):
            errors.append(
                f"{location}: source_id {source_id!r} belongs to a different paper."
            )

        locator = ref.get("locator")
        if not isinstance(locator, dict) or not locator:
            errors.append(f"{location}: evidence locator is empty.")
            continue

        assigned_unit = None
        if is_chunked_task:
            unit_id = locator.get("unit_id")
            cited_chunk_id = locator.get("chunk_id")
            if cited_chunk_id != task.get("chunk_id"):
                errors.append(
                    f"{location}: locator chunk_id must equal assigned chunk {task.get('chunk_id')!r}."
                )
            assigned_unit = assigned_units.get(str(unit_id)) if unit_id else None
            if not unit_id or assigned_unit is None:
                errors.append(
                    f"{location}: locator unit_id must identify one evidence unit assigned to this task."
                )
            elif source_id not in set(assigned_unit.get("source_ids") or []):
                errors.append(
                    f"{location}: source_id {source_id!r} is not linked to cited assigned unit {unit_id!r}."
                )

        status = ref.get("evidence_status", "unverified")
        quote = ref.get("quote_or_cell_value")
        if status in {"verified_text", "verified_table_cell"}:
            if not isinstance(quote, str) or not quote.strip():
                errors.append(f"{location}: verified evidence must include quote_or_cell_value.")
                continue
            if status == "verified_table_cell" and source.get("source_type") in {"main_table", "si_table"}:
                cell_locator = ref.get("locator") or {}
                cell_ok, cell_note = verify_table_cell(source, cell_locator, quote)
                if cell_ok is True and assigned_unit is not None:
                    row_value = cell_locator.get("row_index_1based") or cell_locator.get("row_number") or cell_locator.get("row")
                    column_value = cell_locator.get("column_index_1based") or cell_locator.get("column_number") or cell_locator.get("column")
                    row_index = int(row_value) if str(row_value).isdigit() else None
                    column_index = int(column_value) if str(column_value).isdigit() else None
                    if row_index is None or column_index is None:
                        cell_ok, cell_note = False, "Chunked table-cell evidence requires numeric original row and column indexes."
                    else:
                        assigned_ok, assigned_note = verify_table_cell_is_assigned(
                            assigned_unit, row_index, column_index, cell_locator
                        )
                        if not assigned_ok:
                            cell_ok, cell_note = False, assigned_note
                        elif cell_locator.get("cell_text_offset_start") is not None:
                            table_rows = (source.get("table_data") or {}).get("rows") or []
                            actual_cell = table_rows[row_index - 1][column_index - 1]
                            actual_text = "" if actual_cell is None else str(actual_cell)
                            start = int(cell_locator["cell_text_offset_start"])
                            end = int(cell_locator.get("cell_text_offset_end", len(actual_text)))
                            if start < 0 or end > len(actual_text) or end < start:
                                cell_ok, cell_note = False, "Cell fragment offsets are outside the original cell value."
                            elif normalize_for_search(actual_text[start:end]) != normalize_for_search(quote):
                                cell_ok, cell_note = False, "Quoted table-cell fragment does not match the original cell text range."
                elif cell_ok is True and is_chunked_task:
                    cell_ok, cell_note = False, "Chunked table-cell evidence has no resolvable assigned unit."
                if cell_ok is True:
                    verified_refs += 1
                else:
                    errors.append(f"{location}: claimed {status} but {cell_note}")
            else:
                if is_chunked_task and assigned_unit and assigned_unit.get("unit_kind") == "text":
                    ok, note = verify_quote_in_assigned_text_unit(source, quote, assigned_unit, output_root)
                else:
                    ok, note = verify_quote(source, quote, output_root)
                if ok:
                    verified_refs += 1
                else:
                    errors.append(f"{location}: claimed {status} but {note}")
        elif status == "verified_digitization":
            artifact = (
                locator.get("digitization_artifact")
                or locator.get("digitized_data_path")
                or locator.get("digitization_id")
            )
            if not artifact:
                errors.append(
                    f"{location}: verified_digitization requires a digitization artifact/path/id in locator."
                )
            else:
                verified_refs += 1
        else:
            unverified_refs += 1
            warnings.append({
                "location": location,
                "warning": f"Evidence status is {status}; this reference is not counted as verified.",
            })

    if role == "source_discovery":
        observations = {
            item.get("candidate_observation_id")
            for item in payload.get("feature_observations", [])
        }
        for index, record in enumerate(payload.get("result_records", [])):
            record_source = record.get("source_id")
            if record_source not in sources:
                errors.append(f"result_records/{index}: unknown source_id {record_source!r}.")
            elif record_source not in set(task.get("source_ids", [])):
                errors.append(f"result_records/{index}: source_id was not assigned to this worker task.")
            elif sources[record_source].get("paper_id") != task.get("paper_id"):
                errors.append(f"result_records/{index}: source_id belongs to a different paper.")
            if record.get("candidate_observation_id") not in observations:
                errors.append(f"result_records/{index}: missing matching feature observation.")
            if record.get("value_extraction_status") == "visual_only" and record.get("numeric_value") is not None:
                errors.append(
                    f"result_records/{index}: visual_only values cannot carry an exact numeric_value."
                )
            if record.get("value_extraction_status") in {"exact_text", "exact_table_cell", "digitized"}:
                if not record.get("evidence_refs"):
                    errors.append(f"result_records/{index}: extractable value has no evidence reference.")

    return {
        "task_id": task.get("task_id"),
        "paper_id": task.get("paper_id"),
        "worker_role": role,
        "schema_valid": not schema_errors,
        "reference_count": reference_count,
        "verified_reference_count": verified_refs,
        "unverified_reference_count": unverified_refs,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "status": "evidence_checked" if not errors else "needs_review",
        "errors": errors,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Worker A/B source references against source_manifest.json.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    manifest, sources, output_root = load_manifest(args.manifest.resolve())
    queue = read_json(args.queue.resolve())
    run_dir = args.run_dir.resolve()
    results = []

    for task in queue.get("tasks", []):
        output_dir = Path(task["output_dir"])
        response_path = output_dir / "response.json"
        task_json_path = Path(task.get("task_json_path", ""))
        if not response_path.is_file():
            results.append({
                "task_id": task.get("task_id"),
                "worker_role": task.get("worker_role"),
                "status": "missing_worker_response",
                "errors": [f"Response file not found: {response_path}"],
                "error_count": 1,
            })
            continue
        payload = read_json(response_path)
        role = task["worker_role"]
        result = validate_payload(payload, role, task, sources, output_root)
        result["response_path"] = str(response_path)
        results.append(result)

    summary = {
        "validation_version": "0.1",
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_path": str(args.manifest.resolve()),
        "task_count": len(results),
        "passed_count": sum(1 for item in results if item.get("status") == "evidence_checked"),
        "needs_review_count": sum(1 for item in results if item.get("status") != "evidence_checked"),
        "total_errors": sum(int(item.get("error_count", 0)) for item in results),
        "total_verified_references": sum(int(item.get("verified_reference_count", 0)) for item in results),
        "total_unverified_references": sum(int(item.get("unverified_reference_count", 0)) for item in results),
        "tasks": results,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / "evidence_validation.json"
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps({
        "status": "complete" if summary["needs_review_count"] == 0 else "partial",
        "task_count": summary["task_count"],
        "passed_count": summary["passed_count"],
        "needs_review_count": summary["needs_review_count"],
        "total_errors": summary["total_errors"],
        "report": str(output_path),
    }, ensure_ascii=False, indent=2))
    return 0 if summary["needs_review_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
