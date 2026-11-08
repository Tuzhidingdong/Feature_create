"""Fail-closed preflight for the configured two free OpenCode Go workers.

Production mode refreshes OpenCode's provider catalog and asks for verbose model
metadata. The CLI output must identify each exact provider/model and expose
three Free token-price fields (input, output, cached read). Cached-write is
accepted only when explicitly Free/$0.00 or not listed/dash.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "config" / "free_model_policy.json"
DEFAULT_EXECUTION = ROOT / "config" / "execution_policy.json"
ZERO_PRICE_FIELDS = ("input_price", "output_price", "cached_read_price")


class ModelPolicyError(RuntimeError):
    """Raised when the configured worker pair cannot be verified as free."""


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ModelPolicyError(f"Required policy file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ModelPolicyError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ModelPolicyError(f"Policy root must be an object: {path}")
    return value


def _has_zero_price(value: Any) -> bool:
    text = str(value or "").strip().casefold()
    return text == "free" or text in {"$0", "$0.0", "$0.00", "0", "0.0", "0.00"}


def _cached_write_is_nonpositive(value: Any) -> bool:
    text = str(value or "").strip().casefold()
    return text in {"", "-", "—", "n/a", "na", "not_listed", "not applicable"} or _has_zero_price(text)


def _model_block(output: str, model_ref: str) -> str:
    """Find the verbose model row/block for an exact provider/model identifier."""
    lines = output.splitlines()
    positions = [index for index, line in enumerate(lines) if model_ref in line]
    if not positions:
        return ""
    index = positions[0]
    # Verbose CLI output is table-oriented. Keep the exact row plus a small
    # continuation window for terminals that wrap metadata onto following lines.
    block = [lines[index]]
    for line in lines[index + 1 : index + 4]:
        if re.search(r"\b[a-z0-9_.-]+/[a-z0-9_.-]+\b", line) and "opencode-go/" in line:
            break
        block.append(line)
    return "\n".join(block)


def _validate_verbose_price_block(block: str, model_ref: str) -> None:
    if not block:
        raise ModelPolicyError(
            f"Exact model reference not found in refreshed verbose catalog: {model_ref}"
        )
    # Current OpenCode Go verbose output is expected to expose metadata in the
    # selected model row. Require at least three explicit zero/free price values.
    free_tokens = re.findall(r"\bFree\b", block, flags=re.IGNORECASE)
    zero_tokens = re.findall(r"\$0(?:\.0+)?\b", block)
    if len(free_tokens) + len(zero_tokens) < 3:
        raise ModelPolicyError(
            f"Verbose catalog does not prove input/output/cached-read are all Free "
            f"for {model_ref}. Block was: {block!r}"
        )
    positive_prices = re.findall(r"\$([0-9]+(?:\.[0-9]+)?)", block)
    if any(float(price) > 0 for price in positive_prices):
        raise ModelPolicyError(
            f"Positive price found in the selected model block for {model_ref}: {block!r}"
        )
    # The Go catalog currently shows '-' for cached-write for these free models.
    # If a future catalog exposes a positive cached-write price, it is rejected
    # by the positive-price test above.


def validate_policy(
    free_policy: dict[str, Any],
    execution_policy: dict[str, Any],
    models_output: str,
    today: date | None = None,
    max_snapshot_age_days: int = 7,
) -> list[str]:
    """Validate configuration and live verbose model rows; return [A, B]."""
    today = today or date.today()

    snapshot_text = free_policy.get("verified_on")
    try:
        snapshot_date = date.fromisoformat(str(snapshot_text))
    except ValueError as exc:
        raise ModelPolicyError("free_model_policy.verified_on must be YYYY-MM-DD") from exc

    age_days = (today - snapshot_date).days
    if age_days < 0 or age_days > max_snapshot_age_days:
        raise ModelPolicyError(
            f"Free-price policy snapshot is {age_days} days old; refresh the official "
            f"price snapshot (maximum age: {max_snapshot_age_days} days)."
        )

    selection = free_policy.get("selection_rules", {})
    if selection.get("minimum_distinct_models") != 2:
        raise ModelPolicyError("Policy must require two distinct models.")
    if selection.get("require_provider") != "opencode-go":
        raise ModelPolicyError("Workers must use provider opencode-go.")
    for key in (
        "require_zero_input_price",
        "require_zero_output_price",
        "require_zero_cached_read_price",
        "explicit_model_selection_for_every_worker_call",
        "no_paid_fallback",
        "no_antigravity_bulk_extraction_fallback",
        "pause_if_price_or_availability_is_uncertain",
    ):
        if selection.get(key) is not True:
            raise ModelPolicyError(f"Unsafe model policy: {key} must be true.")
    if selection.get("cached_write_price_must_not_be_positive") is not True:
        raise ModelPolicyError("Cached-write price must be checked for any positive value.")

    workers = execution_policy.get("workers", {})
    if workers.get("provider") != "opencode-go":
        raise ModelPolicyError("Execution policy provider must be opencode-go.")
    if workers.get("require_two_distinct_models") is not True:
        raise ModelPolicyError("Execution policy must require two distinct models.")
    if workers.get("paid_fallback_allowed") is not False:
        raise ModelPolicyError("Paid fallback must remain disabled.")
    if workers.get("cross_provider_fallback_allowed") is not False:
        raise ModelPolicyError("Cross-provider fallback must remain disabled.")
    if workers.get("pause_when_free_pair_unavailable") is not True:
        raise ModelPolicyError("Workflow must pause when the free model pair is unavailable.")

    pair = free_policy.get("preferred_pair")
    if not isinstance(pair, list):
        raise ModelPolicyError("Free model policy must contain preferred_pair.")
    entries = {item.get("worker"): item for item in pair if isinstance(item, dict)}
    configured = {
        "A": workers.get("worker_a", {}).get("model_ref"),
        "B": workers.get("worker_b", {}).get("model_ref"),
    }
    if not all(isinstance(value, str) and value for value in configured.values()):
        raise ModelPolicyError("Both workers must have explicit model_ref values.")
    if configured["A"] == configured["B"]:
        raise ModelPolicyError("Worker A and Worker B must use different models.")

    for worker in ("A", "B"):
        model_ref = configured[worker]
        entry = entries.get(worker)
        if not entry or entry.get("model_ref") != model_ref:
            raise ModelPolicyError(f"Worker {worker} does not match the free-model policy.")
        if not model_ref.startswith("opencode-go/"):
            raise ModelPolicyError(f"Worker {worker} is not using OpenCode Go: {model_ref}")
        for field in ZERO_PRICE_FIELDS:
            if not _has_zero_price(entry.get(field)):
                raise ModelPolicyError(
                    f"Policy snapshot does not mark {field} Free for Worker {worker}."
                )
        if not _cached_write_is_nonpositive(entry.get("cached_write_price")):
            raise ModelPolicyError(
                f"Cached-write is not explicitly non-positive for Worker {worker}."
            )
        _validate_verbose_price_block(_model_block(models_output, model_ref), model_ref)

    return [configured["A"], configured["B"]]


def fetch_live_catalog(opencode_bin: str, timeout_seconds: int = 120) -> tuple[str, str]:
    command = [opencode_bin, "models", "opencode-go", "--refresh", "--verbose"]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ModelPolicyError(f"Unable to refresh OpenCode model catalog: {exc}") from exc
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode != 0:
        raise ModelPolicyError(
            f"OpenCode models refresh failed (exit {result.returncode}): {combined[-4000:]}"
        )
    if not result.stdout.strip():
        raise ModelPolicyError("OpenCode models refresh returned empty stdout.")
    return result.stdout, " ".join(command)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify two distinct currently free OpenCode Go models.")
    parser.add_argument("--opencode-bin", default="opencode")
    parser.add_argument("--models-output", type=Path, help="Offline test mode: use a saved verbose catalog output.")
    parser.add_argument("--free-policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--execution-policy", type=Path, default=DEFAULT_EXECUTION)
    parser.add_argument("--max-snapshot-age-days", type=int, default=7)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--json-output", type=Path, help="Write the successful verification manifest.")
    args = parser.parse_args(argv)

    try:
        if args.models_output:
            model_output = args.models_output.read_text(encoding="utf-8")
            catalog_command = f"offline:{args.models_output}"
        else:
            model_output, catalog_command = fetch_live_catalog(args.opencode_bin, args.timeout_seconds)
        free_policy = read_json(args.free_policy)
        execution_policy = read_json(args.execution_policy)
        pair = validate_policy(
            free_policy,
            execution_policy,
            model_output,
            max_snapshot_age_days=args.max_snapshot_age_days,
        )
        verification = {
            "status": "verified_free_pair",
            "verified_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "policy_snapshot_date": free_policy["verified_on"],
            "catalog_command": catalog_command,
            "models": {"worker_a": pair[0], "worker_b": pair[1]},
            "price_check": "live_verbose_catalog_zero_input_output_cached_read; cached_write_nonpositive",
            "paid_fallback_allowed": False,
        }
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(json.dumps(verification, indent=2), encoding="utf-8", newline="\n")
        print(json.dumps(verification, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ModelPolicyError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
