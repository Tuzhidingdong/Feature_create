"""Build isolated Worker A/B tasks from a prepared source manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROMPTS = {
    "source_discovery": ROOT / "worker_prompts" / "worker_a_source_discovery.md",
    "feature_audit": ROOT / "worker_prompts" / "worker_b_feature_audit.md",
}
MODEL_CONFIG = ROOT / "config" / "execution_policy.json"


class TaskQueueError(RuntimeError):
    pass


def stable_task_id(paper_id: str, worker_role: str, fingerprint: str) -> str:
    digest = hashlib.sha256(
        f"{paper_id}|{worker_role}|{fingerprint}|0.1".encode("utf-8")
    ).hexdigest()[:12].upper()
    prefix = "A" if worker_role == "source_discovery" else "B"
    return f"FC-{prefix}-{digest}"


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskQueueError(f"Unable to read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TaskQueueError(f"Expected JSON object: {path}")
    return value


def source_text(output_root: Path, source: dict[str, Any]) -> str:
    text_path = source.get("extracted_text_path")
    if not text_path:
        return ""
    candidate = (output_root / text_path).resolve()
    try:
        candidate.relative_to(output_root.resolve())
    except ValueError as exc:
        raise TaskQueueError(f"Extracted text path escapes run directory: {candidate}") from exc
    if not candidate.is_file():
        return ""
    return candidate.read_text(encoding="utf-8", errors="replace")


def build_bundle(
    paper: dict[str, Any],
    source_records: list[dict[str, Any]],
    source_output_root: Path,
) -> tuple[str, list[str], int]:
    lines = [
        "# Feature_create paper task input",
        "",
        f"- Paper ID: {paper['paper_id']}",
        f"- DOI: {paper.get('doi') or 'not found in local source metadata'}",
        f"- Source count: {len(source_records)}",
        "- Discovery boundary: all variables actually reported by the attached sources; existing schemas are not a discovery boundary.",
        "",
        "## Source manifest",
        "",
        "| Source ID | Role | Type | Relative path | Parse status |",
        "|---|---|---|---|---|",
    ]
    attachments: list[str] = []
    all_chars = 0

    for source in source_records:
        rel = source["relative_path"].replace("|", "\\|")
        lines.append(
            f"| {source['source_id']} | {source['source_role']} | "
            f"{source['source_type']} | {rel} | {source['parse_status']} |"
        )
        source_path = Path(source["absolute_path"])
        if source_path.is_file() and source_path.suffix.casefold() in {
            ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"
        }:
            attachments.append(str(source_path.resolve()))

    lines.extend([
        "",
        "## Review requirements",
        "",
        "- Review every listed source that is attached or represented by extracted text.",
        "- Preserve source IDs exactly; they are required for provenance.",
        "- Do not claim a source was reviewed if it was only listed.",
        "- Each accepted result value must remain linked to the exact source and locator.",
        "- Keep sample identity, conditions, time/cycle facts, protocols, and result values separate.",
        "- Figure images are attached individually where available. Original PDFs are also attached where present.",
        "- If an attachment could not be read, report it in coverage and unresolved items.",
        "",
        "## Extracted source text",
        "",
    ])

    for source in source_records:
        text = source_text(source_output_root, source)
        if not text.strip():
            lines.extend([
                f"### SOURCE {source['source_id']} — {source['relative_path']}",
                "",
                f"[No extracted text available; parse_status={source['parse_status']}. Inspect the original attachment if present.]",
                "",
            ])
            continue
        all_chars += len(text)
        lines.extend([
            f"### SOURCE {source['source_id']} — {source['relative_path']}",
            "",
            f"Source role: {source['source_role']}; source type: {source['source_type']}.",
            "",
            text,
            "",
        ])

    return "\n".join(lines), sorted(set(attachments)), all_chars


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build independent OpenCode Worker A/B task queue.")
    parser.add_argument("--manifest", type=Path, required=True, help="source_manifest.json from prepare_corpus.py")
    parser.add_argument("--output", type=Path, required=True, help="Run directory for worker tasks.")
    parser.add_argument("--max-bundle-chars", type=int, default=2_000_000)
    parser.add_argument("--max-command-chars", type=int, default=7600)
    args = parser.parse_args(argv)
    if args.max_bundle_chars < 1 or args.max_command_chars < 1000:
        parser.error("max-bundle-chars must be positive and max-command-chars must be at least 1000.")

    manifest = load_json(args.manifest.resolve())
    output_root = args.output.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    config = load_json(MODEL_CONFIG)
    workers = config.get("workers", {})
    model_refs = {
        "source_discovery": workers.get("worker_a", {}).get("model_ref"),
        "feature_audit": workers.get("worker_b", {}).get("model_ref"),
    }
    if not all(model_refs.values()) or model_refs["source_discovery"] == model_refs["feature_audit"]:
        raise TaskQueueError("Worker A and B must have distinct, explicitly configured model references.")

    sources_by_id = {source["source_id"]: source for source in manifest.get("sources", [])}
    tasks: list[dict[str, Any]] = []
    papers = manifest.get("papers", [])
    manifest_root = args.manifest.expanduser().resolve().parent

    for paper in papers:
        paper_sources = [sources_by_id[sid] for sid in paper.get("source_ids", []) if sid in sources_by_id]
        if not paper_sources:
            continue
        paper_sources.sort(key=lambda s: (s["source_role"] != "main", s["relative_path"].casefold()))
        bundle, attachments, extracted_chars = build_bundle(paper, paper_sources, manifest_root)
        paper_hash = hashlib.sha256(
            "|".join(sorted(s["sha256"] for s in paper_sources)).encode("utf-8")
        ).hexdigest()
        paper_dir = output_root / "tasks" / hashlib.sha1(paper["paper_id"].encode("utf-8")).hexdigest()[:10]
        paper_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = paper_dir / "task_input.md"
        bundle_path.write_text(bundle, encoding="utf-8", newline="\n")

        for role in ("source_discovery", "feature_audit"):
            prompt_path = PROMPTS[role]
            if not prompt_path.is_file():
                raise TaskQueueError(f"Worker prompt file is missing: {prompt_path}")
            task_id = stable_task_id(paper["paper_id"], role, paper_hash)
            task_dir = paper_dir / ("worker_a" if role == "source_discovery" else "worker_b")
            task_dir.mkdir(parents=True, exist_ok=True)
            model_ref = model_refs[role]
            agent_name = "feature_create_worker_a" if role == "source_discovery" else "feature_create_worker_b"
            command_preview = [
                "opencode", "--pure", "run",
                "--model", model_ref,
                "--agent", agent_name,
                "--title", task_id,
                "--format", "json",
                "--dir", str(task_dir.resolve()),
            ]
            for attachment_path in [str(prompt_path.resolve()), str(bundle_path.resolve()), *attachments]:
                command_preview.extend(["--file", attachment_path])
            command_preview.append(f"Execute Feature_create task {task_id}. Return the contract JSON.")
            command_chars = len(subprocess.list2cmdline(command_preview))
            if extracted_chars > args.max_bundle_chars:
                task_status = "blocked_oversize"
            elif command_chars > args.max_command_chars:
                task_status = "blocked_command_line"
            else:
                task_status = "pending"
            worker_task = {
                "task_id": task_id,
                "contract_version": "0.1",
                "paper_id": paper["paper_id"],
                "doi": paper.get("doi"),
                "worker_role": role,
                "model_ref": model_ref,
                "prompt_path": str(prompt_path.resolve()),
                "bundle_path": str(bundle_path.resolve()),
                "attachment_paths": attachments,
                "output_dir": str(task_dir.resolve()),
                "task_status": task_status,
                "extracted_text_chars": extracted_chars,
                "source_ids": [source["source_id"] for source in paper_sources],
                "source_count": len(paper_sources),
                "attachment_count": len(attachments),
                "command_length_estimate": command_chars,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "input_fingerprint": paper_hash,
                "oversize_limit_chars": args.max_bundle_chars,
                "command_length_limit": args.max_command_chars,
            }
            task_path = task_dir / "task.json"
            task_path.write_text(
                json.dumps(worker_task, ensure_ascii=False, indent=2),
                encoding="utf-8",
                newline="\n",
            )
            worker_task["task_json_path"] = str(task_path.resolve())
            tasks.append(worker_task)

    queue = {
        "queue_version": "0.1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest_path": str(args.manifest.resolve()),
        "corpus_fingerprint": manifest.get("corpus_fingerprint"),
        "task_count": len(tasks),
        "pending_count": sum(t["task_status"] == "pending" for t in tasks),
        "blocked_count": sum(t["task_status"] != "pending" for t in tasks),
        "tasks": tasks,
    }
    queue_path = output_root / "task_queue.json"
    queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    summary = {
        "paper_count": len(papers),
        "task_count": len(tasks),
        "pending_count": queue["pending_count"],
        "blocked_count": queue["blocked_count"],
        "queue": str(queue_path),
    }
    (output_root / "queue_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
