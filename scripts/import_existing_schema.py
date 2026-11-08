"""Import an existing result_feature_list schema without semantic alteration."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts" / "existing-feature-schema.schema.json"


class SchemaImportError(RuntimeError):
    """Raised when the legacy feature list cannot be parsed safely."""


KEY_ALIASES = {
    "FEATURE_ID": {"feature_id", "feature id", "id"},
    "CANONICAL_NAME": {"canonical_name", "canonical name", "feature_name", "feature name", "name"},
    "ALIASES": {"aliases", "alias", "synonyms", "abbreviations"},
    "DEFINITION": {"definition", "feature definition", "description"},
    "RESULT_TYPE": {"result_type", "result type", "type"},
    "UNITS_AND_DIMENSION": {"units_and_dimension", "units and dimension", "units/dimension", "unit and dimension"},

    "MEASUREMENT_METHODS": {"measurement_methods", "measurement methods", "measurement_method", "measurement method"},
    "CALCULATION_METHODS": {"calculation_methods", "calculation methods", "calculation_method", "calculation method"},
    "STANDARDIZATION_NOTES": {"standardization_notes", "standardization notes", "standardization"},
    "COMPARABILITY_NOTES": {"comparability_notes", "comparability notes", "comparability"},
}


def norm_key(value: str) -> str:
    return re.sub(r"[\s_\-]+", " ", value.strip().casefold())


def split_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [item.strip().strip('"') for item in re.split(r"[;,\n|]+", text) if item.strip()]


def parse_units(value: Any) -> dict[str, list[str]]:
    if isinstance(value, dict):
        return {
            "reported_units": split_values(value.get("reported_units", value.get("units", []))),
            "dimensions": split_values(value.get("dimensions", value.get("unit_dimensions", []))),
        }
    if value is None:
        return {"reported_units": [], "dimensions": []}
    text = str(value).strip()
    if not text:
        return {"reported_units": [], "dimensions": []}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parse_units(parsed)
        if isinstance(parsed, list):
            return {"reported_units": split_values(parsed), "dimensions": []}
    except json.JSONDecodeError:
        pass
    return {"reported_units": split_values(text), "dimensions": []}


def canonicalize_row(raw: dict[str, Any]) -> dict[str, Any]:
    lookup = {norm_key(str(key)): value for key, value in raw.items() if key is not None}
    aliases_to_field = {
        norm_key(alias): field
        for field, aliases in KEY_ALIASES.items()
        for alias in aliases
    }
    mapped: dict[str, Any] = {}
    for key, value in lookup.items():
        field = aliases_to_field.get(key)
        if field and field not in mapped:
            mapped[field] = value

    feature_id = str(mapped.get("FEATURE_ID", "")).strip()
    canonical_name = str(mapped.get("CANONICAL_NAME", "")).strip()
    if not feature_id or not canonical_name:
        raise SchemaImportError(
            f"Feature row must include FEATURE_ID and CANONICAL_NAME; received keys: {list(raw)}"
        )

    raw_fields = {
        str(key): "" if value is None else str(value)
        for key, value in raw.items()
    }
    definition = mapped.get("DEFINITION")
    combined_unit_info = parse_units(mapped.get("UNITS_AND_DIMENSION"))
    separate_units: list[str] = []
    separate_dimensions: list[str] = []
    for key, value in lookup.items():
        if key in {"unit", "units", "reported unit", "reported units", "unit reported"}:
            separate_units.extend(split_values(value))
        elif key in {"dimension", "dimensions", "unit dimension", "unit dimensions"}:
            separate_dimensions.extend(split_values(value))
    combined_unit_info["reported_units"] = sorted(
        set(combined_unit_info["reported_units"] + separate_units),
        key=str.casefold,
    )
    combined_unit_info["dimensions"] = sorted(
        set(combined_unit_info["dimensions"] + separate_dimensions),
        key=str.casefold,
    )
    return {
        "FEATURE_ID": feature_id,
        "CANONICAL_NAME": canonical_name,
        "ALIASES": sorted(set(split_values(mapped.get("ALIASES"))), key=str.casefold),
        "DEFINITION": str(definition).strip() if definition not in (None, "") else None,
        "RESULT_TYPE": sorted(set(split_values(mapped.get("RESULT_TYPE")))),
        "UNITS_AND_DIMENSION": combined_unit_info,
        "MEASUREMENT_METHODS": sorted(set(split_values(mapped.get("MEASUREMENT_METHODS")))),
        "CALCULATION_METHODS": sorted(set(split_values(mapped.get("CALCULATION_METHODS")))),
        "STANDARDIZATION_NOTES": (
            str(mapped["STANDARDIZATION_NOTES"]).strip()
            if mapped.get("STANDARDIZATION_NOTES") not in (None, "")
            else None
        ),
        "COMPARABILITY_NOTES": (
            str(mapped["COMPARABILITY_NOTES"]).strip()
            if mapped.get("COMPARABILITY_NOTES") not in (None, "")
            else None
        ),
        "RAW_FIELDS": raw_fields,
    }


def parse_tsv(text: str) -> list[dict[str, Any]]:
    rows = list(csv.DictReader(text.splitlines(), delimiter="\t"))
    if not rows:
        raise SchemaImportError("TSV has a header but no Feature rows.")
    return [canonicalize_row(row) for row in rows]


def parse_json_schema(text: str) -> list[dict[str, Any]]:
    value = json.loads(text)
    if isinstance(value, dict):
        features = value.get("features", value.get("items", value.get("data")))
    else:
        features = value
    if not isinstance(features, list):
        raise SchemaImportError("JSON schema must be a Feature array or contain a features/items/data array.")
    return [canonicalize_row(item) for item in features]


def parse_text_blocks(text: str) -> list[dict[str, Any]]:
    explicit_blocks = re.split(
        r"(?im)^\s*(?:\[FEATURE\]|\*{2,}\s*FEATURE\b.*|\-{3,}\s*FEATURE\b.*)\s*$",
        text,
    )
    aliases_to_field = {
        norm_key(alias): field
        for field, aliases in KEY_ALIASES.items()
        for alias in aliases
    }
    parsed_blocks: list[dict[str, Any]] = []
    for block in explicit_blocks:
        current: dict[str, Any] = {}
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "//", "[/")):
                continue
            match = re.match(r"^\s*[*\-]?\s*([^:=]+?)\s*[:=]\s*(.*?)\s*$", stripped)
            if not match:
                continue
            key = norm_key(match.group(1))
            field = aliases_to_field.get(key)
            if not field:
                continue
            if field == "FEATURE_ID" and current.get("FEATURE_ID"):
                parsed_blocks.append(canonicalize_row(current))
                current = {}
            current[field] = match.group(2).strip()
        if current:
            parsed_blocks.append(canonicalize_row(current))
    if not parsed_blocks:
        raise SchemaImportError(
            "No Feature blocks were recognized. Expected key/value blocks containing FEATURE_ID and CANONICAL_NAME."
        )
    return parsed_blocks


def validate_imported(value: dict[str, Any]) -> None:
    if Draft202012Validator is None:
        raise SchemaImportError("Install jsonschema from requirements.txt.")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "$"
        raise SchemaImportError(f"Normalized schema failed validation at {location}: {first.message}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import an existing Feature dictionary as a mapping reference.")
    parser.add_argument("--input", type=Path, required=True, help="Existing result_feature_list.txt, TSV, or JSON.")
    parser.add_argument("--output", type=Path, required=True, help="Normalized existing_feature_schema.json.")
    parser.add_argument("--format", choices=["auto", "tsv", "json", "text_blocks"], default="auto")
    args = parser.parse_args(argv)

    source = args.input.expanduser().resolve()
    try:
        raw_bytes = source.read_bytes()
        text = raw_bytes.decode("utf-8-sig")
        selected = args.format
        if selected == "auto":
            stripped = text.lstrip()
            if stripped.startswith("{") or stripped.startswith("["):
                selected = "json"
            elif text.splitlines() and "\t" in text.splitlines()[0]:
                selected = "tsv"
            else:
                selected = "text_blocks"

        if selected == "json":
            features = parse_json_schema(text)
        elif selected == "tsv":
            features = parse_tsv(text)
        else:
            features = parse_text_blocks(text)

        ids = [feature["FEATURE_ID"] for feature in features]
        if len(set(ids)) != len(ids):
            duplicates = sorted({value for value in ids if ids.count(value) > 1})
            raise SchemaImportError(f"Duplicate FEATURE_ID values: {duplicates}")
        sha = hashlib.sha256(raw_bytes).hexdigest()
        result = {
            "contract_version": "0.1",
            "source_file": str(source),
            "source_sha256": sha,
            "source_format": selected,
            "features": features,
        }
        validate_imported(result)
        destination = args.output.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        print(json.dumps({
            "status": "imported",
            "source_file": str(source),
            "source_format": selected,
            "feature_count": len(features),
            "output": str(destination),
            "sha256": sha,
        }, ensure_ascii=False, indent=2))
        return 0
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, SchemaImportError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
