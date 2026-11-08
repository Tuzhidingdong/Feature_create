"""Build existing FEATURE_ID mapping tasks for Worker B.

Every confirmed Registry Feature is compared against the imported existing schema.
The imported schema is a mapping reference only and never constrains discovery.
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
PROMPT = ROOT / "worker_prompts" / "existing_feature_mapping.md"
EXECUTION_POLICY = ROOT / "config" / "execution_policy.json"


class MappingQueueError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MappingQueueError(f"Unable to read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MappingQueueError(f"Expected JSON object: {path}")
    return value


def stable_task_id(registry_id: str, feature_ids: list[str], schema_sha: str, input_sha: str) -> str:
    raw = "|".join([registry_id, schema_sha, *sorted(feature_ids), input_sha, "0.1"])
    return "FC-M-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:14].upper()


def safe_text(root: Path, relative: str | None) -> str:
    if not relative:
        return ""
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return ""
    if not candidate.is_file():
        return ""
    return candidate.read_text(encoding="utf-8", errors="replace")


def quote_excerpt(text: str, quote: str | None, radius: int = 500) -> str:
    if not text:
        return "[No extracted source text available.]"
    if not quote:
        return text[:radius * 2]
    normalized_text = " ".join(text.split())
    normalized_quote = " ".join(str(quote).split())
    index = normalized_text.casefold().find(normalized_quote.casefold())
    if index < 0:
        return f"[Quoted text not found verbatim.] {quote}"
    return normalized_text[max(0, index - radius):index + len(normalized_quote) + radius]


def build_feature_context(
    feature: dict[str, Any],
    registry_observations: dict[str, dict[str, Any]],
    schema_features: list[dict[str, Any]],
    source_map: dict[str, dict[str, Any]],
    manifest_root: Path,
    max_observations: int,
) -> tuple[dict[str, Any], list[str]]:
    observations = [
        registry_observations[oid]
        for oid in feature.get("observation_ids", [])
        if oid in registry_observations
    ]
    if len(observations) > max_observations:
        return {
            "registry_feature_id": feature["registry_feature_id"],
            "blocked_reason": "too_many_observations_for_mapping_task",
            "observation_count": len(observations),
        }, []

    evidence_items = []
    attachments: list[str] = []
    for observation in observations:
        refs = []
        for reference in observation.get("evidence_refs", []):
            source = source_map.get(reference.get("source_id"))
            if not source:
                continue
            excerpt = quote_excerpt(
                safe_text(manifest_root, source.get("extracted_text_path")),
                reference.get("quote_or_cell_value"),
            )
            refs.append({
                "source_id": reference.get("source_id"),
                "source_type": reference.get("source_type"),
                "locator": reference.get("locator"),
                "quote_or_cell_value": reference.get("quote_or_cell_value"),
                "evidence_status": reference.get("evidence_status"),
                "source_excerpt": excerpt,
            })
            # Only attach original PDFs/figure pages when the observation's
            # evidence is explicitly visual. Text evidence is already excerpted.
            source_type = str(reference.get("source_type", ""))
            if source_type in {"main_figure", "main_figure_caption", "si_figure", "si_figure_caption"}:
                original = Path(source.get("absolute_path", ""))
                if original.is_file() and original.suffix.casefold() == ".pdf":
                    attachments.append(str(original.resolve()))
                for visual in source.get("visual_pages", []):
                    visual_file = (manifest_root / "visual_assets" / visual.get("relative_path", "")).resolve()
                    try:
                        visual_file.relative_to(manifest_root.resolve())
                    except ValueError:
                        continue
                    if visual_file.is_file():
                        attachments.append(str(visual_file))

        evidence_items.append({
            "observation_id": observation["observation_id"],
            "paper_id": observation.get("paper_id"),
            "raw_feature_name": observation.get("raw_feature_name"),
            "raw_definition": observation.get("raw_definition"),
            "reported_unit": observation.get("reported_unit"),
            "reported_method": observation.get("reported_method"),
            "result_type": observation.get("result_type"),
            "discovery_status": observation.get("discovery_status"),
            "evidence": refs,
        })

    return {
        "registry_feature": {
            "registry_feature_id": feature["registry_feature_id"],
            "canonical_name": feature.get("canonical_name"),
            "definition": feature.get("definition"),
            "semantic_signature": feature.get("semantic_signature"),
            "aliases": feature.get("aliases", []),
            "unit_dimensions": feature.get("unit_dimensions", []),
            "measurement_methods": feature.get("measurement_methods", []),
            "calculation_methods": feature.get("calculation_methods", []),
            "result_types": feature.get("result_types", []),
        },
        "source_observations": evidence_items,
        # Include the full imported schema. Do not pre-filter candidates solely
        # by lexical similarity: semantic synonyms may have dissimilar labels.
        "existing_schema_candidates": schema_features,
    }, attachments


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Worker B mapping tasks against an imported existing FEATURE_ID schema.")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--existing-schema", type=Path, required=True, help="Normalized existing_feature_schema.json.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-features-per-task", type=int, default=2)
    parser.add_argument("--max-observations-per-feature", type=int, default=40)
    parser.add_argument("--max-bundle-chars", type=int, default=500_000)
    parser.add_argument("--max-command-chars", type=int, default=7600)
    args = parser.parse_args(argv)
    if min(args.max_features_per_task, args.max_observations_per_feature, args.max_bundle_chars) < 1:
        parser.error("Task/bundle limits must be positive.")

    registry_path = args.registry.expanduser().resolve()
    schema_path = args.existing_schema.expanduser().resolve()
    manifest_path = args.manifest.expanduser().resolve()
    registry = read_json(registry_path)
    existing_schema = read_json(schema_path)
    manifest = read_json(manifest_path)
    if existing_schema.get("contract_version") != "0.1":
        raise MappingQueueError("Existing schema is not normalized; run import_existing_schema.py first.")
    schema_sha = existing_schema.get("source_sha256")
    if not isinstance(schema_sha, str) or len(schema_sha) != 64:
        raise MappingQueueError("Imported schema is missing source_sha256.")
    schema_features = existing_schema.get("features", [])
    if not schema_features:
        raise MappingQueueError("Imported existing schema contains no features.")
    if not PROMPT.is_file():
        raise MappingQueueError(f"Worker B mapping prompt not found: {PROMPT}")

    execution = read_json(EXECUTION_POLICY)
    model_ref = execution.get("workers", {}).get("worker_b", {}).get("model_ref")
    if not isinstance(model_ref, str) or not model_ref.startswith("opencode-go/"):
        raise MappingQueueError("Worker B must be configured with an OpenCode Go model.")

    observations = {item["observation_id"]: item for item in registry.get("observations", [])}
    source_map = {item["source_id"]: item for item in manifest.get("sources", [])}
    active = [
        feature for feature in registry.get("canonical_features", [])
        if feature.get("feature_status") != "retired_merged"
    ]
    confirmed = [
        feature for feature in active
        if feature.get("feature_status") == "confirmed"
        and not feature.get("existing_feature_id")
    ]
    output_root = args.output.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    tasks: list[dict[str, Any]] = []
    for start in range(0, len(confirmed), args.max_features_per_task):
        batch = confirmed[start:start + args.max_features_per_task]
        mapping_inputs = []
        attachments: list[str] = []
        blocked_features = []
        for feature in batch:
            context, visual_files = build_feature_context(
                feature, observations, schema_features, source_map,
                manifest_path.parent, args.max_observations_per_feature,
            )
            if context.get("blocked_reason"):
                blocked_features.append(context)
                continue
            mapping_inputs.append(context)
            attachments.extend(visual_files)

        bundle = {
            "contract_version": "0.1",
            "task_type": "existing_schema_mapping",
            "registry_id": registry["registry_id"],
            "existing_schema_sha256": schema_sha,
            "mapping_candidates": mapping_inputs,
            "blocked_features": blocked_features,
            "instructions": [
                "Return exactly one mapping decision for every mapping_candidates entry.",
                "Do not create or modify any FEATURE_ID.",
                "If no existing schema entry is semantically identical, return NO_MATCH.",
            ],
        }
        bundle_text = json.dumps(bundle, ensure_ascii=False, indent=2)
        input_sha = hashlib.sha256(bundle_text.encode("utf-8")).hexdigest()
        feature_ids = [item["registry_feature"]["registry_feature_id"] for item in mapping_inputs]
        task_id = stable_task_id(registry["registry_id"], feature_ids, schema_sha, input_sha)
        task_dir = output_root / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = task_dir / "task_input.json"
        bundle_path.write_text(bundle_text, encoding="utf-8", newline="\n")
        prompt_path = PROMPT.resolve()

        command = [
            "opencode", "--pure", "run",
            "--model", model_ref,
            "--agent", "feature_create_worker_b",
            "--title", task_id,
            "--format", "json",
            "--dir", str(task_dir.resolve()),
        ]
        for attachment in [str(prompt_path), str(bundle_path), *sorted(set(attachments))]:
            command.extend(["--file", str(Path(attachment).resolve())])
        command.append(f"Map the supplied Registry Features against the existing schema for task {task_id}.")
        command_chars = len(subprocess.list2cmdline(command))

        if not feature_ids:
            status = "blocked_no_features"
        elif len(bundle_text) > args.max_bundle_chars:
            status = "blocked_oversize"
        elif command_chars > args.max_command_chars:
            status = "blocked_command_line"
        else:
            status = "pending"

        task = {
            "task_id": task_id,
            "contract_version": "0.1",
            "task_type": "existing_schema_mapping",
            "worker_role": "existing_schema_mapping",
            "registry_id": registry["registry_id"],
            "existing_schema_sha256": schema_sha,
            "registry_feature_ids": feature_ids,
            "model_ref": model_ref,
            "prompt_path": str(prompt_path),
            "bundle_path": str(bundle_path.resolve()),
            "attachment_paths": sorted(set(attachments)),
            "output_dir": str(task_dir.resolve()),
            "feature_count": len(batch),
            "reviewable_feature_count": len(feature_ids),
            "blocked_features": blocked_features,
            "bundle_chars": len(bundle_text),
            "command_length_estimate": command_chars,
            "task_status": status,
            "input_fingerprint": input_sha,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        task_path = task_dir / "task.json"
        task_path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        task["task_json_path"] = str(task_path.resolve())
        tasks.append(task)

    queue = {
        "queue_version": "0.1",
        "task_type": "existing_schema_mapping",
        "registry_id": registry["registry_id"],
        "existing_schema_sha256": schema_sha,
        "registry_path": str(registry_path),
        "existing_schema_path": str(schema_path),
        "manifest_path": str(manifest_path),
        "task_count": len(tasks),
        "pending_count": sum(item["task_status"] == "pending" for item in tasks),
        "blocked_count": sum(item["task_status"] != "pending" for item in tasks),
        "tasks": tasks,
    }
    queue_path = output_root / "existing_schema_mapping_queue.json"
    queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    summary = {
        "confirmed_registry_features_without_existing_id": len(confirmed),
        "existing_schema_feature_count": len(schema_features),
        "task_count": len(tasks),
        "pending_count": queue["pending_count"],
        "blocked_count": queue["blocked_count"],
        "queue": str(queue_path),
    }
    (output_root / "mapping_queue_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
