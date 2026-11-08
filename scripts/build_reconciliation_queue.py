"""Build cross-paper Feature reconciliation tasks for Worker B.

The script batches candidate feature pairs from the Global Registry review
queue, includes source-backed Observation evidence, and pins every task to the
configured Worker B free OpenCode Go model.
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
PROMPT = ROOT / "worker_prompts" / "feature_reconciliation.md"
EXECUTION_POLICY = ROOT / "config" / "execution_policy.json"


class ReconciliationQueueError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReconciliationQueueError(f"Unable to read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReconciliationQueueError(f"Expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ReconciliationQueueError(f"Review queue file not found: {path}")
    result = []
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReconciliationQueueError(f"Invalid JSONL at {path}:{number}: {exc}") from exc
            if isinstance(value, dict):
                result.append(value)
    return result


def stable_task_id(registry_id: str, pair_ids: list[str], input_hash: str) -> str:
    material = "|".join([registry_id, *sorted(pair_ids), input_hash, "0.1"])
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:14].upper()
    return f"FC-R-{digest}"


def safe_relative_file(root: Path, relative: str | None) -> Path | None:
    if not relative:
        return None
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def excerpt_for_reference(
    reference: dict[str, Any],
    source: dict[str, Any],
    manifest_root: Path,
    radius: int,
) -> str:
    text_path = safe_relative_file(manifest_root, source.get("extracted_text_path"))
    if not text_path:
        return "[No locally extracted text available for this source.]"
    text = text_path.read_text(encoding="utf-8", errors="replace")
    quote = reference.get("quote_or_cell_value")
    if not isinstance(quote, str) or not quote.strip():
        locator = reference.get("locator") or {}
        return (
            "[No verbatim quote was supplied. Use locator and original attachment if available.]\n"
            f"Locator: {json.dumps(locator, ensure_ascii=False)}"
        )
    # Unicode/whitespace-insensitive lookup; keep returned excerpt in original text.
    import unicodedata
    import re

    def compact(value: str) -> str:
        return re.sub(
            r"\s+", " ",
            unicodedata.normalize("NFKC", value).casefold().replace("μ", "u").replace("µ", "u")
        ).strip()

    needle = compact(quote)
    haystack = compact(text)
    location = haystack.find(needle)
    if location < 0:
        return (
            "[Quoted text was not found verbatim in extracted text; inspect original file.]\n"
            f"Quoted text: {quote}"
        )
    # Map the normalized offset approximately back to the source string by
    # using a whitespace-normalized source representation. The exact quote and
    # source locator are included separately for traceability.
    normalized_source = " ".join(text.split())
    normalized_position = normalized_source.casefold().find(needle)
    if normalized_position < 0:
        normalized_position = max(0, min(location, len(normalized_source)))
    start = max(0, normalized_position - radius)
    end = min(len(normalized_source), normalized_position + len(quote) + radius)
    return normalized_source[start:end]


def build_pair_context(
    pair: dict[str, Any],
    observations: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    manifest_root: Path,
    max_observations: int,
    radius: int,
) -> tuple[dict[str, Any], list[str], int]:
    all_observation_ids = []
    for feature_key in ("feature_a", "feature_b"):
        all_observation_ids.extend(pair.get(feature_key, {}).get("observation_ids", []))
    # Retain all observations by default. An explicit cap converts an oversized
    # pair to blocked status instead of silently pretending it was exhaustive.
    if len(all_observation_ids) > max_observations:
        return {
            "candidate_pair_id": pair["candidate_pair_id"],
            "blocked_reason": "observation_count_exceeds_limit",
            "observation_count": len(all_observation_ids),
            "allowed_observation_count": max_observations,
        }, [], len(all_observation_ids)

    context: dict[str, Any] = {
        "candidate_pair_id": pair["candidate_pair_id"],
        "registry_feature_a_id": pair["registry_feature_a_id"],
        "registry_feature_b_id": pair["registry_feature_b_id"],
        "feature_a": pair["feature_a"],
        "feature_b": pair["feature_b"],
        "retrieval_signals": pair.get("candidate_retrieval", {}),
        "cross_paper": pair.get("cross_paper", False),
        "paper_ids": pair.get("paper_ids", []),
        "observation_evidence": {"feature_a": [], "feature_b": []},
    }
    attachments: list[str] = []
    unresolved_sources = []
    for feature_key, dest_key in (("feature_a", "feature_a"), ("feature_b", "feature_b")):
        for observation_id in pair.get(feature_key, {}).get("observation_ids", []):
            observation = observations.get(observation_id)
            if not observation:
                unresolved_sources.append(f"Missing Observation ID: {observation_id}")
                continue
            refs = observation.get("evidence_refs", [])
            excerpts = []
            for reference in refs:
                source = sources.get(reference.get("source_id"))
                if not source:
                    unresolved_sources.append(
                        f"Observation {observation_id}: missing source {reference.get('source_id')}"
                    )
                    continue
                excerpt = excerpt_for_reference(reference, source, manifest_root, radius)
                excerpts.append({
                    "source_id": reference.get("source_id"),
                    "source_type": reference.get("source_type"),
                    "locator": reference.get("locator"),
                    "quote_or_cell_value": reference.get("quote_or_cell_value"),
                    "evidence_status": reference.get("evidence_status"),
                    "source_excerpt": excerpt,
                })
                original = Path(source.get("absolute_path", ""))
                if original.is_file() and original.suffix.casefold() in {
                    ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"
                }:
                    attachments.append(str(original.resolve()))
                for visual_page in source.get("visual_pages", []):
                    visual_file = safe_relative_file(
                        manifest_root / "visual_assets",
                        visual_page.get("relative_path"),
                    )
                    if visual_file:
                        attachments.append(str(visual_file))
            context["observation_evidence"][dest_key].append({
                "observation_id": observation_id,
                "paper_id": observation.get("paper_id"),
                "source_id": observation.get("source_id"),
                "raw_feature_name": observation.get("raw_feature_name"),
                "raw_definition": observation.get("raw_definition"),
                "reported_unit": observation.get("reported_unit"),
                "reported_method": observation.get("reported_method"),
                "result_type": observation.get("result_type"),
                "variable_role": observation.get("variable_role"),
                "discovery_status": observation.get("discovery_status"),
                "evidence": excerpts,
            })
    if unresolved_sources:
        context["unresolved_source_references"] = unresolved_sources
    return context, sorted(set(attachments)), len(all_observation_ids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build Worker B global semantic reconciliation tasks from the candidate pair queue."
    )
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--review-queue", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-pairs-per-task", type=int, default=8)
    parser.add_argument("--max-observations-per-pair", type=int, default=40)
    parser.add_argument("--max-bundle-chars", type=int, default=500_000)
    parser.add_argument("--max-command-chars", type=int, default=7600)
    parser.add_argument("--excerpt-radius", type=int, default=700)
    args = parser.parse_args(argv)
    if min(args.max_pairs_per_task, args.max_observations_per_pair, args.max_bundle_chars) < 1:
        parser.error("Pair, observation, and bundle limits must be positive.")

    registry_path = args.registry.expanduser().resolve()
    manifest_path = args.manifest.expanduser().resolve()
    manifest_root = manifest_path.parent
    registry = read_json(registry_path)
    manifest = read_json(manifest_path)
    pairs = [
        pair for pair in read_jsonl(args.review_queue.expanduser().resolve())
        if pair.get("decision_status") in {"pending_semantic_review", "pending"}
    ]
    observations = {item["observation_id"]: item for item in registry.get("observations", [])}
    sources = {item["source_id"]: item for item in manifest.get("sources", [])}
    execution_policy = read_json(EXECUTION_POLICY)
    model_ref = execution_policy.get("workers", {}).get("worker_b", {}).get("model_ref")
    if not isinstance(model_ref, str) or not model_ref.startswith("opencode-go/"):
        raise ReconciliationQueueError("Worker B must be pinned to an OpenCode Go model.")
    if not PROMPT.is_file():
        raise ReconciliationQueueError(f"Worker B reconciliation prompt not found: {PROMPT}")

    output_root = args.output.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    tasks = []
    for start in range(0, len(pairs), args.max_pairs_per_task):
        batch = pairs[start:start + args.max_pairs_per_task]
        contexts = []
        attachments: list[str] = []
        blocked_pairs: list[dict[str, Any]] = []
        for pair in batch:
            context, pair_attachments, observation_count = build_pair_context(
                pair,
                observations,
                sources,
                manifest_root,
                args.max_observations_per_pair,
                args.excerpt_radius,
            )
            if context.get("blocked_reason"):
                blocked_pairs.append(context)
                continue
            contexts.append(context)
            attachments.extend(pair_attachments)

        bundle = {
            "contract_version": "0.1",
            "task_type": "semantic_reconciliation",
            "registry_id": registry.get("registry_id"),
            "reconciliation_pairs": contexts,
            "blocked_pairs": blocked_pairs,
            "instructions": [
                "Return exactly one decision for every reconciliation_pairs entry.",
                "Do not decide or infer a result for blocked_pairs.",
                "Source evidence excerpts are provided per Observation; do not assume missing evidence.",
            ],
        }
        bundle_text = json.dumps(bundle, ensure_ascii=False, indent=2)
        input_hash = hashlib.sha256(bundle_text.encode("utf-8")).hexdigest()
        pair_ids = [pair["candidate_pair_id"] for pair in batch]
        task_id = stable_task_id(registry["registry_id"], pair_ids, input_hash)
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
        command.append(f"Reconcile the supplied Feature pair batch for task {task_id}.")
        command_chars = len(subprocess.list2cmdline(command))
        if not contexts:
            task_status = "blocked_no_reviewable_pairs"
        elif len(bundle_text) > args.max_bundle_chars:
            task_status = "blocked_oversize"
        elif command_chars > args.max_command_chars:
            task_status = "blocked_command_line"
        else:
            task_status = "pending"

        task = {
            "task_id": task_id,
            "contract_version": "0.1",
            "task_type": "semantic_reconciliation",
            "worker_role": "semantic_reconciliation",
            "registry_id": registry["registry_id"],
            "model_ref": model_ref,
            "prompt_path": str(prompt_path),
            "bundle_path": str(bundle_path.resolve()),
            "attachment_paths": sorted(set(attachments)),
            "output_dir": str(task_dir.resolve()),
            "candidate_pair_ids": pair_ids,
            "pair_count": len(batch),
            "reviewable_pair_count": len(contexts),
            "blocked_pairs": blocked_pairs,
            "bundle_chars": len(bundle_text),
            "command_length_estimate": command_chars,
            "task_status": task_status,
            "input_fingerprint": input_hash,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        task_path = task_dir / "task.json"
        task_path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        task["task_json_path"] = str(task_path.resolve())
        tasks.append(task)

    queue = {
        "queue_version": "0.1",
        "task_type": "semantic_reconciliation",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "registry_path": str(registry_path),
        "manifest_path": str(manifest_path),
        "registry_id": registry.get("registry_id"),
        "task_count": len(tasks),
        "pending_count": sum(task["task_status"] == "pending" for task in tasks),
        "blocked_count": sum(task["task_status"] != "pending" for task in tasks),
        "tasks": tasks,
    }
    queue_path = output_root / "reconciliation_task_queue.json"
    queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    summary = {
        "pair_count": len(pairs),
        "task_count": len(tasks),
        "pending_count": queue["pending_count"],
        "blocked_count": queue["blocked_count"],
        "queue": str(queue_path),
    }
    (output_root / "reconciliation_queue_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
