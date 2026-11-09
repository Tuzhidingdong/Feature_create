"""Deterministically export the two formal Feature_create TXT deliverables.

The files are UTF-8 TSV (tab-separated text) with JSON-encoded nested context
columns. Export is blocked when Registry reconciliation remains unresolved,
so an ambiguous Feature dictionary is never silently presented as final.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_SCHEMA = ROOT / "contracts" / "registry.schema.json"

RECORD_HEADERS = [
    "Record_ID",
    "Paper_ID",
    "DOI",
    "FEATURE_ID",
    "RAW_FEATURE_NAME",
    "CANONICAL_NAME",
    "RAW_VALUE",
    "NUMERIC_VALUE",
    "REPORTED_UNIT",
    "RESULT_TYPE",
    "SAMPLE_IDENTITY",
    "CONDITION_FACTS",
    "TIME_CYCLE_FACTS",
    "PROTOCOL_FACTS",
    "OTHER_CONTEXT_FACTS",
    "CONTEXT_ASSOCIATION_STATUS",
    "OPEN_CONTEXT_UNRESOLVED_ITEMS",
    "MEASUREMENT_OR_CALCULATION_METHOD",
    "SOURCE_TYPE",
    "SOURCE_LOCATION",
    "EVIDENCE_REFS",
    "EVIDENCE_STATUS",
    "EXTRACTION_STATUS",
    "UNRESOLVED_ISSUES",
]

FEATURE_HEADERS = [
    "FEATURE_ID",
    "CANONICAL_NAME",
    "ALIASES",
    "DEFINITION",
    "RESULT_TYPE",
    "UNITS_AND_DIMENSION",
    "MEASUREMENT_METHODS",
    "CALCULATION_METHODS",
    "STANDARDIZATION_NOTES",
    "COMPARABILITY_NOTES",
]


class ExportError(RuntimeError):
    """Raised when the Registry is not ready for final two-file export."""


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"Unable to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ExportError(f"Expected a JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ExportError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ExportError(f"JSONL row is not an object at {path}:{line_number}")
            rows.append(value)
    return rows


def json_cell(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def atomic_write_tsv(path: Path, headers: list[str], rows: Iterable[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(
            handle,
            delimiter="\t",
            quotechar='"',
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\n",
        )
        writer.writerow(headers)
        for row in rows:
            writer.writerow(["" if value is None else value for value in row])
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def validate_registry(registry: dict[str, Any]) -> list[str]:
    if Draft202012Validator is None:
        raise ExportError("Install jsonschema from requirements.txt.")
    schema = read_json(REGISTRY_SCHEMA)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(registry),
        key=lambda error: list(error.absolute_path),
    )
    return [
        f"{'/'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in errors
    ]


def resolve_feature_id(feature_id: str | None, feature_by_id: dict[str, dict[str, Any]]) -> str | None:
    if not feature_id:
        return None
    seen: set[str] = set()
    current = feature_id
    while True:
        if current in seen:
            raise ExportError(f"Registry redirect cycle detected at {current}")
        seen.add(current)
        feature = feature_by_id.get(current)
        if not feature:
            raise ExportError(f"Result record points to missing Registry Feature ID: {current}")
        if feature.get("feature_status") != "retired_merged":
            return current
        redirect = feature.get("redirect_to")
        if not redirect:
            raise ExportError(f"Retired Feature {current} has no redirect_to target.")
        current = redirect



CONTEXT_RESOLVED_STATUSES = {"association_confirmed", "no_association"}
CONTEXT_PENDING_STATUSES = {"pending", "under_review", "blocked"}
CONTEXT_INCONCLUSIVE_STATUSES = {"insufficient_evidence", "conflicting_evidence"}


def is_context_association_issue(issue: Any) -> bool:
    """Identify unresolved items that can affect record-to-context attribution."""
    if isinstance(issue, dict):
        kind = str(issue.get("item_type") or issue.get("type") or "").casefold()
        if kind == "missing_context":
            return True
        if any(token in kind for token in (
            "context", "sample_identity", "condition", "time_cycle",
            "time_or_cycle", "protocol_context", "statistical_scope",
        )):
            return True
        if not kind:
            description = str(issue.get("description") or "").casefold()
            return any(token in description for token in (
                "missing context", "sample identity", "condition association",
                "time/cycle association", "protocol association",
                "statistical scope association",
            ))
        return False
    if isinstance(issue, str):
        lowered = issue.casefold()
        return any(token in lowered for token in (
            "missing context", "sample identity", "condition association",
            "time/cycle association", "protocol association",
            "statistical scope association",
        ))
    return False


def context_association_summary(record: dict[str, Any]) -> dict[str, Any]:
    """Return a fail-closed summary of all context-association gaps linked to one record."""
    linked = [
        issue for issue in (record.get("unresolved_issues") or [])
        if is_context_association_issue(issue)
    ]
    if not linked:
        return {
            "status": "not_required",
            "open_items": [],
            "issue_count": 0,
            "invalid_status_count": 0,
        }

    open_items: list[dict[str, Any]] = []
    statuses: list[str] = []
    invalid_status_count = 0
    for issue in linked:
        if isinstance(issue, dict):
            status = str(issue.get("resolution_status") or "pending").casefold()
            item = {
                "unresolved_id": issue.get("unresolved_id"),
                "item_type": issue.get("item_type") or issue.get("type") or "missing_context",
                "description": issue.get("description"),
                "resolution_status": status,
            }
        else:
            status = "pending"
            item = {
                "unresolved_id": None,
                "item_type": "legacy_missing_context",
                "description": str(issue),
                "resolution_status": status,
            }
        statuses.append(status)
        if status not in CONTEXT_RESOLVED_STATUSES:
            open_items.append(item)
            if status not in (CONTEXT_PENDING_STATUSES | CONTEXT_INCONCLUSIVE_STATUSES):
                invalid_status_count += 1

    if invalid_status_count:
        status = "invalid_status"
    elif not open_items:
        status = "resolved"
    elif any(value in CONTEXT_RESOLVED_STATUSES for value in statuses):
        status = "mixed"
    elif all(value in CONTEXT_PENDING_STATUSES for value in statuses):
        status = "pending"
    elif all(value in CONTEXT_INCONCLUSIVE_STATUSES for value in statuses):
        status = "inconclusive"
    else:
        status = "mixed"

    return {
        "status": status,
        "open_items": open_items,
        "issue_count": len(linked),
        "invalid_status_count": invalid_status_count,
    }


def feature_methods(
    feature: dict[str, Any],
    observations: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str]]:
    measurement = set(feature.get("measurement_methods", []))
    calculation = set(feature.get("calculation_methods", []))
    for observation_id in feature.get("observation_ids", []):
        observation = observations.get(observation_id)
        if not observation:
            continue
        method = observation.get("reported_method")
        if not method:
            continue
        result_type = observation.get("result_type")
        if result_type in {"calculated", "derived", "simulated", "model_output"}:
            calculation.add(method)
        elif result_type == "measured":
            measurement.add(method)
        else:
            # Preserve method without claiming it is exclusively a measurement
            # or calculation method; include it in both only when source type
            # is explicitly identified above.
            pass
    return sorted(measurement, key=str.casefold), sorted(calculation, key=str.casefold)


def build_feature_rows(
    registry: dict[str, Any],
    existing_schema: dict[str, Any] | None = None,
) -> tuple[list[list[Any]], dict[str, dict[str, Any]], list[str]]:
    observations = {item["observation_id"]: item for item in registry.get("observations", [])}
    existing_by_id = {
        item["FEATURE_ID"]: item
        for item in (existing_schema or {}).get("features", [])
    }
    feature_by_id = {
        item["registry_feature_id"]: item
        for item in registry.get("canonical_features", [])
    }
    active_features = [
        item for item in registry.get("canonical_features", [])
        if item.get("feature_status") != "retired_merged"
    ]
    rows: list[list[Any]] = []
    errors: list[str] = []
    canonical_names: dict[str, str] = {}

    for feature in sorted(active_features, key=lambda item: (
        item.get("canonical_name", "").casefold(),
        item.get("registry_feature_id", ""),
    )):
        status = feature.get("feature_status")
        if status != "confirmed":
            errors.append(
                f"Feature {feature.get('registry_feature_id')} is not confirmed (status={status})."
            )
            continue

        linked_observations = [
            observations[oid]
            for oid in feature.get("observation_ids", [])
            if oid in observations
        ]
        units = sorted(
            {
                str(item.get("reported_unit"))
                for item in linked_observations
                if item.get("reported_unit")
            },
            key=str.casefold,
        )
        measurement_methods, calculation_methods = feature_methods(feature, observations)
        dimensions = sorted(set(feature.get("unit_dimensions", [])), key=str.casefold)
        result_types = set(feature.get("result_types", []))
        feature_id = feature.get("existing_feature_id") or feature["registry_feature_id"]
        legacy = existing_by_id.get(feature.get("existing_feature_id")) if feature.get("existing_feature_id") else None
        if feature.get("existing_feature_id") and legacy is None:
            errors.append(
                f"Feature {feature['registry_feature_id']} maps to existing FEATURE_ID "
                f"{feature['existing_feature_id']} but that ID is absent from the supplied schema."
            )
            continue

        aliases = set(feature.get("aliases", []))
        if legacy:
            aliases.update(legacy.get("ALIASES", []))
            if feature.get("canonical_name") != legacy.get("CANONICAL_NAME"):
                aliases.add(feature.get("canonical_name", ""))
            result_types.update(legacy.get("RESULT_TYPE", []))
            units.extend(legacy.get("UNITS_AND_DIMENSION", {}).get("reported_units", []))
            dimensions.extend(legacy.get("UNITS_AND_DIMENSION", {}).get("dimensions", []))
            measurement_methods = sorted(
                set(measurement_methods) | set(legacy.get("MEASUREMENT_METHODS", [])),
                key=str.casefold,
            )
            calculation_methods = sorted(
                set(calculation_methods) | set(legacy.get("CALCULATION_METHODS", [])),
                key=str.casefold,
            )
        aliases.discard("")
        unit_object = {
            "reported_units": sorted(set(units), key=str.casefold),
            "dimensions": sorted(set(dimensions), key=str.casefold),
        }
        definition = (
            legacy.get("DEFINITION") if legacy and legacy.get("DEFINITION")
            else feature.get("definition") or ""
        )
        canonical_name = (
            legacy.get("CANONICAL_NAME") if legacy else feature.get("canonical_name")
        )
        canonical_names[feature["registry_feature_id"]] = canonical_name
        result_types_list = sorted(result_types)
        standardization_notes = (
            legacy.get("STANDARDIZATION_NOTES") if legacy and legacy.get("STANDARDIZATION_NOTES")
            else "Canonical name and aliases were reconciled across source observations."
        )
        generated_standardization = (
            " Source-reported unit spellings are retained; extraction does not silently convert values."
        )
        standardization_notes = (standardization_notes or "") + generated_standardization
        comparability_notes = (
            legacy.get("COMPARABILITY_NOTES") if legacy and legacy.get("COMPARABILITY_NOTES")
            else ""
        )
        generated_comparability = (
            " Compare individual records only after checking sample identity, experimental protocol, "
            "statistic, temporal/cycle scope, result type, method, and reported unit. "
            "Method/result-type differences remain visible at record level."
        )
        comparability_notes = (comparability_notes + " " if comparability_notes else "") + generated_comparability
        rows.append([
            feature_id,
            canonical_name,
            json_cell(sorted(aliases, key=str.casefold)),
            definition,
            json_cell(result_types_list),
            json_cell(unit_object),
            json_cell(measurement_methods),
            json_cell(calculation_methods),
            standardization_notes.strip(),
            comparability_notes.strip(),
        ])

    return rows, feature_by_id, canonical_names, errors


def build_record_rows(
    records: list[dict[str, Any]],
    feature_by_id: dict[str, dict[str, Any]],
    canonical_names: dict[str, str] | None = None,
 ) -> tuple[list[list[Any]], list[str], dict[str, Any]]:
    rows: list[list[Any]] = []
    errors: list[str] = []
    seen_record_ids: set[str] = set()
    context_summary = {
        "status_counts": {},
        "records_with_open_context": 0,
        "open_context_issue_count": 0,
        "invalid_context_status_count": 0,
    }

    for record in records:
        record_id = record.get("record_id")
        if not record_id:
            errors.append("A result record has no record_id.")
            continue
        if record_id in seen_record_ids:
            errors.append(f"Duplicate Record_ID: {record_id}")
            continue
        seen_record_ids.add(record_id)

        try:
            canonical_id = resolve_feature_id(record.get("registry_feature_id"), feature_by_id)
        except ExportError as exc:
            errors.append(str(exc))
            continue
        if not canonical_id:
            errors.append(
                f"Record {record_id} has no result Feature ID; manual classification is required."
            )
            continue
        feature = feature_by_id[canonical_id]
        if feature.get("feature_status") != "confirmed":
            errors.append(
                f"Record {record_id} maps to Feature {canonical_id} with status "
                f"{feature.get('feature_status')}."
            )
            continue

        raw_value_status = record.get("value_extraction_status")
        if raw_value_status == "visual_only" and record.get("numeric_value") is not None:
            errors.append(
                f"Record {record_id} is visual_only but contains numeric_value."
            )
            continue

        context_summary_for_record = context_association_summary(record)
        context_status = context_summary_for_record["status"]
        open_context_items = context_summary_for_record["open_items"]
        context_summary["status_counts"][context_status] = (
            context_summary["status_counts"].get(context_status, 0) + 1
        )
        if open_context_items:
            context_summary["records_with_open_context"] += 1
            context_summary["open_context_issue_count"] += len(open_context_items)
        context_summary["invalid_context_status_count"] += (
            context_summary_for_record["invalid_status_count"]
        )

        feature_id = feature.get("existing_feature_id") or canonical_id
        source_location = {
            "source_id": record.get("source_id"),
            "source_type": record.get("source_type"),
            "locators": record.get("source_location", []),
        }
        rows.append([
            record_id,
            record.get("paper_id"),
            record.get("doi"),
            feature_id,
            record.get("raw_feature_name"),
            (canonical_names or {}).get(canonical_id, feature.get("canonical_name")),
            record.get("raw_value"),
            "" if record.get("numeric_value") is None else str(record.get("numeric_value")),
            record.get("reported_unit"),
            record.get("result_type"),
            json_cell(record.get("sample_identity", [])),
            json_cell(record.get("condition_facts", [])),
            json_cell(record.get("time_cycle_facts", [])),
            json_cell(record.get("protocol_facts", [])),
            json_cell(record.get("other_context_facts", [])),
            context_status,
            json_cell(open_context_items),
            record.get("measurement_or_calculation_method"),
            record.get("source_type"),
            json_cell(source_location),
            json_cell(record.get("evidence_refs", [])),
            record.get("evidence_status"),
            record.get("extraction_status"),
            json_cell(record.get("unresolved_issues", [])),
        ])

    rows.sort(key=lambda row: (
        str(row[1] or ""),
        str(row[3] or ""),
        str(row[0] or ""),
    ))
    return rows, errors, context_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export exactly the two formal Feature_create TXT files.")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--review-queue", type=Path, required=True)
    parser.add_argument("--existing-schema", type=Path, help="Optional normalized existing_feature_schema.json for legacy name/definition/alias preservation.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--audit-report", type=Path, help="Optional separate internal audit JSON path.")
    parser.add_argument(
        "--allow-unresolved-context",
        action="store_true",
        help="Explicitly export a provisional package while keeping every open context association flagged in the records.",
    )
    args = parser.parse_args(argv)

    registry_path = args.registry.resolve()
    registry = read_json(registry_path)
    schema_errors = validate_registry(registry)
    review_rows = read_jsonl(args.review_queue.resolve())
    records = read_jsonl(args.records.resolve())
    existing_schema = read_json(args.existing_schema.resolve()) if args.existing_schema else None
    if existing_schema and existing_schema.get("contract_version") != "0.1":
        raise ExportError("Existing schema must be normalized with import_existing_schema.py.")

    active_unresolved_pairs = [
        item for item in review_rows
        if item.get("decision_status") in {
            "pending_semantic_review", "under_review", "blocked",
        }
    ]
    feature_rows, feature_by_id, canonical_names, feature_errors = build_feature_rows(registry, existing_schema)
    record_rows, record_errors, context_summary = build_record_rows(
        records, feature_by_id, canonical_names
    )

    errors = list(schema_errors) + feature_errors + record_errors
    if existing_schema is None and any(
        feature.get("existing_feature_id")
        for feature in registry.get("canonical_features", [])
        if feature.get("feature_status") != "retired_merged"
    ):
        errors.append(
            "Registry contains existing FEATURE_ID mappings but --existing-schema was not supplied."
        )
    exported_feature_ids = [row[0] for row in feature_rows]
    if len(set(exported_feature_ids)) != len(exported_feature_ids):
        duplicates = sorted({value for value in exported_feature_ids if exported_feature_ids.count(value) > 1})
        errors.append("Duplicate FEATURE_ID in active Feature dictionary: " + ", ".join(duplicates))
    if active_unresolved_pairs:
        errors.append(
            f"{len(active_unresolved_pairs)} candidate Feature pair(s) remain pending or under review."
        )
    if context_summary["invalid_context_status_count"]:
        errors.append(
            f"{context_summary['invalid_context_status_count']} context association item(s) have an unknown resolution status; export is blocked even with --allow-unresolved-context."
        )
    if context_summary["open_context_issue_count"] and not args.allow_unresolved_context:
        errors.append(
            f"{context_summary['open_context_issue_count']} context association item(s) remain unresolved across {context_summary['records_with_open_context']} result record(s). Complete the review or explicitly use --allow-unresolved-context for a clearly flagged provisional export."
        )

    report = {
        "export_version": "0.1",
        "registry_id": registry.get("registry_id"),
        "record_count": len(record_rows),
        "canonical_feature_count": len(feature_rows),
        "active_registry_feature_count": sum(
            feature.get("feature_status") != "retired_merged"
            for feature in registry.get("canonical_features", [])
        ),
        "retired_merged_feature_count": sum(
            feature.get("feature_status") == "retired_merged"
            for feature in registry.get("canonical_features", [])
        ),
        "unresolved_pair_count": len(active_unresolved_pairs),
        "record_error_count": len(record_errors),
        "feature_error_count": len(feature_errors),
        "schema_error_count": len(schema_errors),
        "context_association_status_counts": context_summary["status_counts"],
        "records_with_open_context": context_summary["records_with_open_context"],
        "open_context_issue_count": context_summary["open_context_issue_count"],
        "invalid_context_status_count": context_summary["invalid_context_status_count"],
        "allow_unresolved_context": bool(args.allow_unresolved_context),
        "warnings": (
            ["Formal TXT files contain explicitly flagged unresolved context associations; treat this package as provisional."]
            if context_summary["open_context_issue_count"] and args.allow_unresolved_context
            else []
        ),
        "errors": errors,
        "status": (
            "blocked" if errors
            else "exported_with_unresolved_context" if context_summary["open_context_issue_count"]
            else "ready"
        ),
    }

    if errors:
        output_dir = args.output_dir.expanduser().resolve()
        audit_path = (
            args.audit_report.resolve()
            if args.audit_report
            else output_dir.parent / f"{output_dir.name}_export_audit.json"
        )
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    records_path = output_dir / "result_feature_records.txt"
    features_path = output_dir / "result_feature_list.txt"
    atomic_write_tsv(records_path, RECORD_HEADERS, record_rows)
    atomic_write_tsv(features_path, FEATURE_HEADERS, feature_rows)

    report.update({
        "status": (
            "exported_with_unresolved_context"
            if context_summary["open_context_issue_count"]
            else "exported"
        ),
        "result_feature_records": str(records_path),
        "result_feature_list": str(features_path),
        "formal_output_files": [records_path.name, features_path.name],
    })
    if args.audit_report:
        audit_path = args.audit_report.resolve()
    else:
        audit_path = output_dir.parent / f"{output_dir.name}_export_audit.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
