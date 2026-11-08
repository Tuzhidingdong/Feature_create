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


def validate_payload(
    payload: dict[str, Any],
    role: str,
    task: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    output_root: Path,
) -> dict[str, Any]:
    errors = validate_schema(payload, role)
    warnings: list[dict[str, str]] = []
    verified_refs = 0
    unverified_refs = 0
    reference_count = 0

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

        locator = ref.get("locator")
        if not isinstance(locator, dict) or not locator:
            errors.append(f"{location}: evidence locator is empty.")
            continue

        status = ref.get("evidence_status", "unverified")
        quote = ref.get("quote_or_cell_value")
        if status in {"verified_text", "verified_table_cell"}:
            if not isinstance(quote, str) or not quote.strip():
                errors.append(f"{location}: verified evidence must include quote_or_cell_value.")
                continue
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
        "schema_valid": not any("schema" in error.lower() for error in errors),
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
