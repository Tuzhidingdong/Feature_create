"""Run independent Worker A/B tasks using explicitly pinned free OpenCode Go models.

The runner:
- refreshes and verifies the current OpenCode Go model catalog before inference;
- pins model and small_model to the same verified-free model for each task;
- uses a tool-restricted, subagent-disabled agent;
- attaches the worker prompt, one consolidated source bundle, and visual assets;
- stores raw stdout/stderr, session export, structured response, and accounting;
- resumes completed tasks without repeating them;
- never falls back to another model/provider.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    from jsonschema import Draft202012Validator
except ImportError:  # handled as an explicit preflight error
    Draft202012Validator = None  # type: ignore[assignment]

try:
    from .validate_free_models import (
        ModelPolicyError,
        fetch_live_catalog,
        validate_policy,
    )
except ImportError:  # direct execution as python scripts/run_workers.py
    from validate_free_models import (
        ModelPolicyError,
        fetch_live_catalog,
        validate_policy,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXECUTION_POLICY = ROOT / "config" / "execution_policy.json"
SCHEMA_PATHS = {
    "source_discovery": ROOT / "contracts" / "worker-a.schema.json",
    "feature_audit": ROOT / "contracts" / "worker-b.schema.json",
}
WORKER_AGENTS = {
    "source_discovery": "feature_create_worker_a",
    "feature_audit": "feature_create_worker_b",
}
TOOL_DENY_MAP = {
    name: False
    for name in (
        "bash", "read", "edit", "write", "glob", "grep", "list",
        "task", "todowrite", "webfetch", "websearch", "lsp", "skill",
    )
}


class WorkerRunnerError(RuntimeError):
    """Raised for configuration, task, or execution errors."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerRunnerError(f"Unable to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkerRunnerError(f"Expected JSON object in {path}")
    return value


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    os.replace(temp, path)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def merge_event_text(existing: str, incoming: str) -> str:
    """Handle either cumulative text-part events or incremental text chunks."""
    if not incoming:
        return existing
    if not existing:
        return incoming
    if incoming.startswith(existing):
        return incoming
    if existing.startswith(incoming):
        return existing
    return existing + incoming


def _model_ref_from_object(value: dict[str, Any]) -> str | None:
    provider = value.get("providerID") or value.get("providerId") or value.get("provider_id")
    model_id = value.get("modelID") or value.get("modelId") or value.get("model_id")
    if isinstance(provider, str) and isinstance(model_id, str):
        return f"{provider}/{model_id}"
    model = value.get("model")
    if isinstance(model, dict):
        provider = model.get("providerID") or model.get("providerId") or model.get("provider_id")
        model_id = model.get("id") or model.get("modelID") or model.get("modelId")
        if isinstance(provider, str) and isinstance(model_id, str):
            return f"{provider}/{model_id}"
    if isinstance(model, str) and "/" in model:
        return model
    return None


def _walk_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_objects(child)


def parse_jsonl_events(text: str) -> dict[str, Any]:
    session_ids: set[str] = set()
    model_refs: set[str] = set()
    text_by_message: dict[str, str] = {}
    step_costs: list[float] = []
    token_totals = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
    usage_seen = False
    tool_events = 0
    parse_errors: list[str] = []

    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            parse_errors.append(f"line {line_no}: invalid JSON event")
            continue
        if not isinstance(event, dict):
            continue
        for obj in _walk_objects(event):
            sid = obj.get("sessionID") or obj.get("sessionId")
            if isinstance(sid, str):
                session_ids.add(sid)
            model_ref = _model_ref_from_object(obj)
            if model_ref:
                model_refs.add(model_ref)

        event_type = str(event.get("type", ""))
        if event_type == "tool_use":
            tool_events += 1
        part = event.get("part")
        if not isinstance(part, dict):
            part = {}
        if event_type == "text":
            message_id = str(part.get("messageID") or part.get("messageId") or "default")
            text_by_message[message_id] = merge_event_text(
                text_by_message.get(message_id, ""), str(part.get("text") or "")
            )
        if event_type == "step_finish" or part.get("type") == "step-finish":
            cost = part.get("cost")
            if isinstance(cost, (int, float)):
                step_costs.append(float(cost))
            tokens = part.get("tokens")
            if isinstance(tokens, dict):
                usage_seen = True
                token_totals["input"] += int(tokens.get("input") or 0)
                token_totals["output"] += int(tokens.get("output") or 0)
                token_totals["reasoning"] += int(tokens.get("reasoning") or 0)
                cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
                token_totals["cache_read"] += int(cache.get("read") or 0)
                token_totals["cache_write"] += int(cache.get("write") or 0)

    return {
        "session_ids": sorted(session_ids),
        "model_refs": sorted(model_refs),
        "assistant_text": "\n".join(value for value in text_by_message.values() if value),
        "cost_reported": bool(step_costs),
        "reported_cost": sum(step_costs) if step_costs else None,
        "usage_reported": usage_seen,
        "tokens": token_totals if usage_seen else None,
        "tool_event_count": tool_events,
        "jsonl_parse_errors": parse_errors,
    }


def extract_export_details(export_text: str) -> dict[str, Any]:
    try:
        document = json.loads(export_text)
    except json.JSONDecodeError:
        return {
            "assistant_text": "",
            "model_refs": [],
            "cost_reported": False,
            "reported_cost": None,
            "usage_reported": False,
            "tokens": None,
        }

    messages: list[dict[str, Any]] = []
    model_refs: set[str] = set()
    assistant_info_costs: list[float] = []
    step_costs: list[float] = []
    info_tokens = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
    step_tokens = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
    info_usage_seen = False
    step_usage_seen = False

    for obj in _walk_objects(document):
        model_ref = _model_ref_from_object(obj)
        if model_ref:
            model_refs.add(model_ref)

        info = obj.get("info")
        if isinstance(info, dict) and info.get("role") == "assistant":
            messages.append(obj)
            model_ref = _model_ref_from_object(info)
            if model_ref:
                model_refs.add(model_ref)
            cost = info.get("cost")
            if isinstance(cost, (int, float)):
                assistant_info_costs.append(float(cost))
            tokens = info.get("tokens")
            if isinstance(tokens, dict):
                info_usage_seen = True
                info_tokens["input"] += int(tokens.get("input") or 0)
                info_tokens["output"] += int(tokens.get("output") or 0)
                info_tokens["reasoning"] += int(tokens.get("reasoning") or 0)
                cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
                info_tokens["cache_read"] += int(cache.get("read") or 0)
                info_tokens["cache_write"] += int(cache.get("write") or 0)

        if obj.get("type") == "step-finish":
            cost = obj.get("cost")
            if isinstance(cost, (int, float)):
                step_costs.append(float(cost))
            tokens = obj.get("tokens")
            if isinstance(tokens, dict):
                step_usage_seen = True
                step_tokens["input"] += int(tokens.get("input") or 0)
                step_tokens["output"] += int(tokens.get("output") or 0)
                step_tokens["reasoning"] += int(tokens.get("reasoning") or 0)
                cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
                step_tokens["cache_read"] += int(cache.get("read") or 0)
                step_tokens["cache_write"] += int(cache.get("write") or 0)

    assistant_texts: list[str] = []
    for message in messages:
        for part in message.get("parts", []):
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                assistant_texts.append(part["text"])

    # Prefer message-level totals when present; do not add step totals to them.
    cost_values = assistant_info_costs if assistant_info_costs else step_costs
    if info_usage_seen:
        tokens_result = info_tokens
    elif step_usage_seen:
        tokens_result = step_tokens
    else:
        tokens_result = None

    return {
        "assistant_text": "\n".join(assistant_texts),
        "model_refs": sorted(model_refs),
        "cost_reported": bool(cost_values),
        "reported_cost": sum(cost_values) if cost_values else None,
        "usage_reported": info_usage_seen or step_usage_seen,
        "tokens": tokens_result,
    }


def extract_json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^\`\`\`(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*\`\`\`$", "", value)
    value = value.strip()
    if not value.startswith("{"):
        raise WorkerRunnerError("Worker response must contain only one JSON object; leading text is not allowed.")
    decoder = json.JSONDecoder()
    try:
        parsed, end = decoder.raw_decode(value)
    except json.JSONDecodeError as exc:
        raise WorkerRunnerError(f"Worker response contains invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise WorkerRunnerError("Worker response JSON root must be an object.")
    if value[end:].strip():
        raise WorkerRunnerError("Worker response contains trailing text after its JSON object.")
    return parsed


def validate_worker_payload(payload: dict[str, Any], task: dict[str, Any]) -> list[str]:
    if Draft202012Validator is None:
        raise WorkerRunnerError("Missing dependency jsonschema. Install requirements.txt first.")
    schema_path = SCHEMA_PATHS[task["worker_role"]]
    schema = read_json(schema_path)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    problems = [
        f"{'/'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in errors
    ]
    if payload.get("task_id") != task["task_id"]:
        problems.append("task_id does not match the dispatched task.")
    if payload.get("paper_id") != task["paper_id"]:
        problems.append("paper_id does not match the dispatched task.")

    if task["worker_role"] == "source_discovery":
        observations = {
            item.get("candidate_observation_id")
            for item in payload.get("feature_observations", [])
        }
        for index, record in enumerate(payload.get("result_records", [])):
            if record.get("candidate_observation_id") not in observations:
                problems.append(
                    f"result_records/{index}: candidate_observation_id is not in feature_observations."
                )
    return problems


def build_runtime_config(model_ref: str, worker_role: str) -> str:
    agent_name = WORKER_AGENTS[worker_role]
    agent_prompt = (
        "Use only the attached worker instructions and task source bundle. "
        "Do not call tools, subagents, shells, web services, or any other model. "
        "Return exactly one JSON object matching the task contract. "
        "Do not include Markdown fences or explanatory text."
    )
    config = {
        "$schema": "https://opencode.ai/config.json",
        "model": model_ref,
        "small_model": model_ref,
        "subagent_depth": 0,
        "default_agent": agent_name,
        "agent": {
            agent_name: {
                "description": "Read-only Feature_create extraction worker; no tools or subagents.",
                "mode": "primary",
                "model": model_ref,
                "prompt": agent_prompt,
                "tools": TOOL_DENY_MAP,
                "permission": {"*": "deny"},
            }
        },
        "tools": TOOL_DENY_MAP,
    }
    return json.dumps(config, separators=(",", ":"), ensure_ascii=False)


def build_command(
    opencode_bin: str,
    task: dict[str, Any],
    task_dir: Path,
) -> list[str]:
    model_ref = str(task["model_ref"])
    role = str(task["worker_role"])
    args = [
        opencode_bin,
        "--pure",
        "run",
        "--model", model_ref,
        "--agent", WORKER_AGENTS[role],
        "--title", str(task["task_id"]),
        "--format", "json",
        "--dir", str(task_dir),
    ]
    files = [task["prompt_path"], task["bundle_path"], *task.get("attachment_paths", [])]
    seen: set[str] = set()
    for raw_path in files:
        path = str(Path(raw_path).resolve())
        if path in seen:
            continue
        seen.add(path)
        if not Path(path).is_file():
            raise WorkerRunnerError(f"Task attachment does not exist: {path}")
        args.extend(["--file", path])
    args.append(
        f"Execute Feature_create task {task['task_id']}. "
        "Read every attached source item relevant to this task and return exactly one JSON object."
    )
    command_chars = len(subprocess.list2cmdline(args)) if os.name == "nt" else sum(len(x) + 1 for x in args)
    if os.name == "nt" and command_chars > 28000:
        raise WorkerRunnerError(
            f"Windows command line would be too long ({command_chars} chars); split the task bundle."
        )
    return args


def run_process(
    command: Sequence[str],
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkerRunnerError(f"OpenCode task timed out after {timeout_seconds} seconds.") from exc
    except OSError as exc:
        raise WorkerRunnerError(f"Unable to start OpenCode CLI: {exc}") from exc


def attempt_worker_task(
    task: dict[str, Any],
    opencode_bin: str,
    timeout_seconds: int,
    max_attempts: int,
    retry_backoff_seconds: float,
    max_cli_output_chars: int,
) -> dict[str, Any]:
    output_dir = Path(task["output_dir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    model_ref = str(task["model_ref"])
    role = str(task["worker_role"])
    run_meta: dict[str, Any] = {
        "task_id": task["task_id"],
        "paper_id": task["paper_id"],
        "worker_role": role,
        "requested_model": model_ref,
        "started_at": utc_now(),
        "attempts": [],
        "model_verification": "explicitly_pinned",
    }

    for attempt in range(1, max_attempts + 1):
        attempt_dir = output_dir / f"attempt_{attempt:02d}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        try:
            command = build_command(opencode_bin, task, attempt_dir)
            env = os.environ.copy()
            env["OPENCODE_CONFIG_CONTENT"] = build_runtime_config(model_ref, role)
            env["OPENCODE_DISABLE_DEFAULT_PLUGINS"] = "1"
            result = run_process(command, attempt_dir, env, timeout_seconds)

            stdout_path = attempt_dir / "stdout.jsonl"
            stderr_path = attempt_dir / "stderr.txt"
            stdout_path.write_text(result.stdout or "", encoding="utf-8", newline="\n")
            stderr_path.write_text(result.stderr or "", encoding="utf-8", newline="\n")

            events = parse_jsonl_events(result.stdout or "")
            session_id = events["session_ids"][-1] if events["session_ids"] else None
            export_path = attempt_dir / "session_export.json"
            export_info: dict[str, Any] = {
                "assistant_text": "",
                "model_refs": [],
                "cost_reported": False,
                "reported_cost": None,
                "usage_reported": False,
                "tokens": None,
            }
            export_return_code = None
            if session_id:
                export_result = run_process(
                    [opencode_bin, "export", session_id, "--sanitize"],
                    attempt_dir,
                    env,
                    timeout_seconds=min(timeout_seconds, 180),
                )
                export_return_code = export_result.returncode
                export_path.write_text(export_result.stdout or "", encoding="utf-8", newline="\n")
                if export_result.returncode == 0 and export_result.stdout.strip():
                    export_info = extract_export_details(export_result.stdout)

            observed_models = sorted(set(events["model_refs"]) | set(export_info["model_refs"]))
            if any(ref != model_ref for ref in observed_models):
                run_meta["status"] = "quarantined_model_mismatch"
                run_meta["observed_models"] = observed_models
                run_meta["error"] = "Actual model metadata differs from the explicitly pinned free model."
                run_meta["finished_at"] = utc_now()
                run_meta["attempts"].append({
                    "attempt": attempt,
                    "exit_code": result.returncode,
                    "session_id": session_id,
                    "status": run_meta["status"],
                })
                return run_meta

            costs = []
            if events["cost_reported"]:
                costs.append(float(events["reported_cost"]))
            if export_info["cost_reported"]:
                costs.append(float(export_info["reported_cost"]))
            # Prefer the session export as the stored session-level total. The
            # JSONL step_finish can be missing or truncated in some CLI versions.
            reported_cost = (
                export_info["reported_cost"]
                if export_info["cost_reported"]
                else events["reported_cost"]
            )
            if reported_cost is not None and float(reported_cost) > 0:
                run_meta["status"] = "quarantined_nonzero_cost"
                run_meta["reported_cost"] = float(reported_cost)
                run_meta["observed_models"] = observed_models
                run_meta["finished_at"] = utc_now()
                run_meta["error"] = "OpenCode reported non-zero model cost for a task expected to use a free model."
                run_meta["attempts"].append({
                    "attempt": attempt,
                    "exit_code": result.returncode,
                    "session_id": session_id,
                    "status": run_meta["status"],
                })
                return run_meta

            assistant_text = export_info["assistant_text"] or events["assistant_text"]
            if len(assistant_text) > max_cli_output_chars:
                raise WorkerRunnerError(
                    f"Worker response exceeds configured output size limit ({len(assistant_text)} chars)."
                )
            if events["tool_event_count"] > 0:
                raise WorkerRunnerError(
                    f"Worker emitted {events['tool_event_count']} tool-use event(s) despite the read-only policy."
                )
            if result.returncode != 0:
                raise WorkerRunnerError(
                    f"OpenCode exited with code {result.returncode}: {(result.stderr or result.stdout)[-2500:]}"
                )
            if not assistant_text.strip():
                raise WorkerRunnerError(
                    "No assistant text recovered from JSONL or session export. "
                    "The CLI may have omitted events; inspect raw files before retrying."
                )

            payload = extract_json_object(assistant_text)
            validation_errors = validate_worker_payload(payload, task)
            if validation_errors:
                raise WorkerRunnerError("Worker payload failed validation: " + " | ".join(validation_errors[:12]))

            response_path = output_dir / "response.json"
            response_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
                newline="\n",
            )

            tokens = export_info["tokens"] if export_info["usage_reported"] else events["tokens"]
            cost_status = (
                "reported_zero"
                if reported_cost is not None and float(reported_cost) == 0
                else "not_exposed_free_model_price_verified"
            )
            run_meta.update({
                "status": "complete",
                "finished_at": utc_now(),
                "attempt_count": attempt,
                "session_id": session_id,
                "observed_models": observed_models,
                "actual_model_status": "verified" if model_ref in observed_models else "not_exposed_cli_model_explicitly_pinned",
                "reported_cost": reported_cost,
                "cost_status": cost_status,
                "tokens": tokens,
                "usage_status": "reported" if tokens is not None else "not_exposed",
                "session_export_return_code": export_return_code,
                "response_path": str(response_path),
                "raw_stdout_path": str(stdout_path),
                "raw_stderr_path": str(stderr_path),
                "jsonl_parse_errors": events["jsonl_parse_errors"],
                "session_export_path": str(export_path) if export_path.exists() else None,
            })
            run_meta["attempts"].append({
                "attempt": attempt,
                "exit_code": result.returncode,
                "session_id": session_id,
                "status": "complete",
            })
            return run_meta

        except WorkerRunnerError as exc:
            error_text = str(exc)
            run_meta["attempts"].append({
                "attempt": attempt,
                "status": "failed",
                "error": error_text,
            })
            if any(token in error_text.casefold() for token in ("non-zero model cost", "model mismatch")):
                break
            if attempt < max_attempts:
                time.sleep(retry_backoff_seconds * (2 ** (attempt - 1)))
            else:
                run_meta["status"] = "failed"
                run_meta["error"] = error_text
                run_meta["finished_at"] = utc_now()
        except Exception as exc:  # noqa: BLE001 - retain unexpected failure for checkpoint
            run_meta["attempts"].append({
                "attempt": attempt,
                "status": "failed_unexpected",
                "error": f"{type(exc).__name__}: {exc}",
            })
            run_meta["status"] = "failed"
            run_meta["error"] = f"{type(exc).__name__}: {exc}"
            run_meta["finished_at"] = utc_now()
            break

    if "status" not in run_meta:
        run_meta["status"] = "failed"
        run_meta["finished_at"] = utc_now()
    return run_meta


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dispatch Feature_create Worker A/B through free OpenCode Go models.")
    parser.add_argument("--queue", type=Path, required=True, help="task_queue.json generated by build_task_queue.py")
    parser.add_argument("--run-dir", type=Path, required=True, help="Persistent run/checkpoint directory.")
    parser.add_argument("--opencode-bin", default="opencode")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--retry-backoff-seconds", type=float, default=10.0)
    parser.add_argument("--max-cli-output-chars", type=int, default=20_000_000)
    parser.add_argument("--dry-run", action="store_true", help="Verify preflight and print tasks without inference calls.")
    args = parser.parse_args(argv)

    if args.max_workers < 1 or args.max_attempts < 1 or args.timeout_seconds < 1:
        parser.error("max-workers, max-attempts and timeout-seconds must be positive.")
    if Draft202012Validator is None:
        print("BLOCKED: install jsonschema from requirements.txt before running workers.", file=sys.stderr)
        return 2

    run_dir = args.run_dir.expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    queue = read_json(args.queue.expanduser().resolve())
    tasks = queue.get("tasks")
    if not isinstance(tasks, list):
        print("BLOCKED: task queue does not contain a tasks array.", file=sys.stderr)
        return 2

    # The model catalog is refreshed before any inference request.
    try:
        execution_policy = read_json(DEFAULT_EXECUTION_POLICY)
        free_policy = read_json(ROOT / "config" / "free_model_policy.json")
        catalog_output, catalog_command = fetch_live_catalog(args.opencode_bin)
        model_pair = validate_policy(free_policy, execution_policy, catalog_output)
        preflight = {
            "status": "verified_free_pair",
            "verified_at": utc_now(),
            "catalog_command": catalog_command,
            "models": {"worker_a": model_pair[0], "worker_b": model_pair[1]},
            "output_sha256": sha256_text(catalog_output),
            "price_evidence": "opencode models opencode-go --refresh --verbose",
        }
    except (OSError, ModelPolicyError, WorkerRunnerError) as exc:
        atomic_write_json(run_dir / "preflight_blocked.json", {
            "status": "blocked_no_verified_free_pair",
            "time": utc_now(),
            "error": str(exc),
        })
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2

    atomic_write_json(run_dir / "model_preflight.json", preflight)
    allowed_models = {model_pair[0], model_pair[1]}

    eligible: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for task in tasks:
        if task.get("task_status") != "pending":
            blocked.append({**task, "status": task.get("task_status", "blocked")})
            continue
        if task.get("model_ref") not in allowed_models:
            blocked.append({**task, "status": "blocked_unapproved_model"})
            continue
        expected_role_model = (
            model_pair[0] if task.get("worker_role") == "source_discovery" else model_pair[1]
        )
        if task.get("model_ref") != expected_role_model:
            blocked.append({**task, "status": "blocked_wrong_worker_model"})
            continue
        eligible.append(task)

    queue_hash = sha256_text(json.dumps(
        [{"task_id": t["task_id"], "input_fingerprint": t.get("input_fingerprint"), "model_ref": t["model_ref"]} for t in eligible],
        sort_keys=True,
    ))
    state_path = run_dir / "run_state.json"
    if state_path.exists():
        state = read_json(state_path)
        if state.get("queue_fingerprint") != queue_hash:
            print(
                "BLOCKED: existing run_state.json belongs to a different task queue. "
                "Use a new run directory or archive the prior run state.",
                file=sys.stderr,
            )
            return 2
    else:
        state = {
            "state_version": "0.1",
            "created_at": utc_now(),
            "queue_fingerprint": queue_hash,
            "preflight": preflight,
            "tasks": {},
        }

    for task in eligible:
        previous = state["tasks"].get(task["task_id"])
        if previous and previous.get("status") == "complete":
            continue
        if previous and previous.get("status") in {"quarantined_model_mismatch", "quarantined_nonzero_cost"}:
            continue
        state["tasks"][task["task_id"]] = {
            "status": "pending",
            "worker_role": task["worker_role"],
            "model_ref": task["model_ref"],
        }
    state_lock = threading.Lock()
    atomic_write_json(state_path, state)

    if args.dry_run:
        report = {
            "status": "dry_run",
            "eligible_tasks": len(eligible),
            "blocked_tasks": len(blocked),
            "max_workers": min(args.max_workers, 4),
            "models": preflight["models"],
            "commands_not_executed": True,
        }
        atomic_write_json(run_dir / "dispatch_summary.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    pending = [
        task for task in eligible
        if state["tasks"].get(task["task_id"], {}).get("status") != "complete"
        and state["tasks"].get(task["task_id"], {}).get("status") not in {
            "quarantined_model_mismatch", "quarantined_nonzero_cost"
        }
    ]

    def run_one(task: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        task_state = attempt_worker_task(
            task=task,
            opencode_bin=args.opencode_bin,
            timeout_seconds=args.timeout_seconds,
            max_attempts=args.max_attempts,
            retry_backoff_seconds=args.retry_backoff_seconds,
            max_cli_output_chars=args.max_cli_output_chars,
        )
        atomic_write_json(Path(task["output_dir"]) / "task_state.json", task_state)
        with state_lock:
            state["tasks"][task["task_id"]] = task_state
            state["updated_at"] = utc_now()
            atomic_write_json(state_path, state)
        return task["task_id"], task_state

    outcomes: dict[str, Any] = {}
    worker_count = min(args.max_workers, 4)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(run_one, task) for task in pending]
        for future in as_completed(futures):
            task_id, task_state = future.result()
            outcomes[task_id] = task_state

    statuses = [value.get("status", "unknown") for value in state["tasks"].values()]
    summary = {
        "status": "complete" if statuses and all(s == "complete" for s in statuses) and not blocked else "partial",
        "finished_at": utc_now(),
        "eligible_tasks": len(eligible),
        "executed_tasks": len(outcomes),
        "already_complete_tasks": sum(1 for t in eligible if t["task_id"] not in outcomes and state["tasks"].get(t["task_id"], {}).get("status") == "complete"),
        "blocked_tasks": len(blocked),
        "task_status_counts": {status: statuses.count(status) for status in sorted(set(statuses))},
        "max_workers_used": worker_count,
        "models": preflight["models"],
        "paid_fallback_allowed": False,
        "run_state_path": str(state_path),
    }
    atomic_write_json(run_dir / "dispatch_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
