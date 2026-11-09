from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover
    Draft202012Validator = None

from scripts.reconcile_features import validate_registry

ROOT = Path(__file__).resolve().parents[1]
SELECTION_SCHEMA = ROOT / "contracts" / "feature-selection.schema.json"


class FeatureExportError(ValueError):
    """Raised when selection decisions cannot be safely exported."""


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FeatureExportError(f"Cannot read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FeatureExportError(f"Expected a JSON object in {path}.")
    return value


def validate_selection(registry: dict[str, Any], decisions: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if Draft202012Validator is None:
        raise FeatureExportError("Install jsonschema from requirements.txt.")
    errors = sorted(
        Draft202012Validator(read_json(SELECTION_SCHEMA)).iter_errors(decisions),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "$"
        raise FeatureExportError(f"Selection schema error at {location}: {first.message}")
    if decisions["registry_id"] != registry["registry_id"]:
        raise FeatureExportError("Selection decisions refer to a different Registry.")

    active = {
        item["registry_feature_id"]: item
        for item in registry["canonical_features"]
        if item.get("feature_status") != "retired_merged"
    }
    mapping: dict[str, dict[str, Any]] = {}
    for item in decisions["decisions"]:
        feature_id = item["registry_feature_id"]
        if feature_id in mapping:
            raise FeatureExportError(f"Duplicate decision for {feature_id}.")
        if feature_id not in active:
            raise FeatureExportError(f"Unknown or retired Feature ID: {feature_id}.")
        support = set(item["supporting_observation_ids"])
        valid = set(active[feature_id].get("observation_ids", []))
        if not support or not support.issubset(valid):
            raise FeatureExportError(f"Supporting Observation IDs do not belong to {feature_id}.")
        mapping[feature_id] = item

    missing = sorted(set(active) - set(mapping))
    if missing:
        raise FeatureExportError(
            "A decision is required for every active Feature; missing: " + ", ".join(missing[:10])
        )
    if not any(item["decision"] == "select" for item in mapping.values()):
        raise FeatureExportError("No Features were selected; refusing to write an empty Feature list.")
    return mapping


def render_feature_list(registry: dict[str, Any], decisions: dict[str, dict[str, Any]]) -> tuple[str, dict[str, int]]:
    observations = {item["observation_id"]: item for item in registry["observations"]}
    selected = [
        feature for feature in registry["canonical_features"]
        if feature.get("feature_status") != "retired_merged"
        and decisions[feature["registry_feature_id"]]["decision"] == "select"
    ]
    selected.sort(key=lambda item: (item.get("canonical_name", "").casefold(), item["registry_feature_id"]))
    output = [
        "RESULT FEATURE LIST",
        "Scope: representative Feature definitions only; values and experimental conditions are handled downstream.",
        "",
    ]
    for feature in selected:
        decision = decisions[feature["registry_feature_id"]]
        supporting = [observations[key] for key in decision["supporting_observation_ids"]]
        emphasis = sorted({item.get("author_emphasis", "not_assessable") for item in supporting})
        feature_id = feature.get("existing_feature_id") or feature["registry_feature_id"]
        aliases = sorted(set(feature.get("aliases", [])), key=str.casefold)
        result_types = sorted(set(feature.get("result_types", [])))
        unit_dimensions = sorted(set(feature.get("unit_dimensions", [])))
        output.extend([
            f"FEATURE_ID: {feature_id}",
            f"CANONICAL_NAME: {feature.get('canonical_name', '')}",
            f"DEFINITION: {feature.get('definition') or 'Not stated in supplied sources.'}",
            f"ALIASES: {'; '.join(aliases) if aliases else 'None'}",
            f"RESULT_TYPES: {'; '.join(result_types) if result_types else 'Not specified'}",
            f"UNIT_DIMENSIONS: {'; '.join(unit_dimensions) if unit_dimensions else 'Not standardized'}",
            f"AUTHOR_EMPHASIS: {'; '.join(emphasis) if emphasis else 'not_assessable'}",
            f"SELECTION_RATIONALE: {decision['selection_rationale']}",
            "",
        ])
    return "\n".join(output).rstrip() + "\n", {
        "selected": len(selected),
        "excluded": sum(item["decision"] == "exclude" for item in decisions.values()),
        "review": sum(item["decision"] == "review" for item in decisions.values()),
    }


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export only selected representative Result Features.")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        registry = read_json(args.registry)
        validate_registry(registry)
        decisions = read_json(args.decisions)
        mapping = validate_selection(registry, decisions)
        content, summary = render_feature_list(registry, mapping)
        write_atomic(args.output, content)
    except (FeatureExportError, ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"output": str(args.output), **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
