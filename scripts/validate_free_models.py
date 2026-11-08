"""Fail-closed preflight for the two free OpenCode Go worker models.

This script does not invoke a model. It validates:
- the checked-in free-model price snapshot is recent;
- Worker A and Worker B use different OpenCode Go model references;
- all listed token/cache prices are Free;
- both model references appear in the current `opencode models` catalog output.

The model-list output alone does not prove pricing. Pricing is tied to the
dated official-source snapshot in config/free_model_policy.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "config" / "free_model_policy.json"
DEFAULT_EXECUTION = ROOT / "config" / "execution_policy.json"

PRICE_FIELDS = (
    "input_price",
    "output_price",
    "cached_read_price",
    "cached_write_price",
)


class ModelPolicyError(RuntimeError):
    """Raised when the free-model preflight cannot establish safe routing."""


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


def validate_policy(
    free_policy: dict[str, Any],
    execution_policy: dict[str, Any],
    models_output: str,
    today: date | None = None,
    max_snapshot_age_days: int = 7,
) -> list[str]:
    """Return the two pinned free model references or raise a policy error."""
    today = today or date.today()

    snapshot_text = free_policy.get("verified_on")
    try:
        snapshot_date = date.fromisoformat(str(snapshot_text))
    except ValueError as exc:
        raise ModelPolicyError("free_model_policy.verified_on must be YYYY-MM-DD") from exc

    age_days = (today - snapshot_date).days
    if age_days < 0 or age_days > max_snapshot_age_days:
        raise ModelPolicyError(
            f"Free-price snapshot is {age_days} days old; refresh official pricing "
            f"and update the snapshot (maximum age: {max_snapshot_age_days} days)."
        )

    selection = free_policy.get("selection_rules", {})
    if selection.get("minimum_distinct_models") != 2:
        raise ModelPolicyError("Policy must require exactly two distinct worker models.")
    if selection.get("require_provider") != "opencode-go":
        raise ModelPolicyError("Workers must use the opencode-go provider.")
    for key in (
        "require_zero_input_price",
        "require_zero_output_price",
        "require_zero_cached_read_price",
        "require_zero_cached_write_price",
        "explicit_model_selection_for_every_worker_call",
        "no_paid_fallback",
        "no_antigravity_bulk_extraction_fallback",
        "pause_if_price_or_availability_is_uncertain",
    ):
        if selection.get(key) is not True:
            raise ModelPolicyError(f"Unsafe free-model policy: {key} must be true.")

    workers = execution_policy.get("workers", {})
    if workers.get("provider") != "opencode-go":
        raise ModelPolicyError("Execution policy provider must be opencode-go.")
    if workers.get("require_two_distinct_models") is not True:
        raise ModelPolicyError("Execution policy must require two distinct models.")
    if workers.get("paid_fallback_allowed") is not False:
        raise ModelPolicyError("Paid fallback must remain disabled.")
    if workers.get("cross_provider_fallback_allowed") is not False:
        raise ModelPolicyError("Cross-provider fallback must remain disabled.")

    pair = free_policy.get("preferred_pair")
    if not isinstance(pair, list) or len(pair) < 2:
        raise ModelPolicyError("At least two verified free model candidates are required.")

    configured = {
        "A": workers.get("worker_a", {}).get("model_ref"),
        "B": workers.get("worker_b", {}).get("model_ref"),
    }
    if not all(isinstance(v, str) and v for v in configured.values()):
        raise ModelPolicyError("Both Worker A and Worker B require explicit model_ref values.")
    if configured["A"] == configured["B"]:
        raise ModelPolicyError("Worker A and Worker B must use different models.")

    entries = {item.get("worker"): item for item in pair if isinstance(item, dict)}
    for worker in ("A", "B"):
        entry = entries.get(worker)
        model_ref = configured[worker]
        if not entry or entry.get("model_ref") != model_ref:
            raise ModelPolicyError(
                f"Worker {worker} model does not match the verified free-model policy."
            )
        if not model_ref.startswith("opencode-go/"):
            raise ModelPolicyError(f"Worker {worker} is not pinned to OpenCode Go: {model_ref}")
        for field in PRICE_FIELDS:
            if str(entry.get(field, "")).strip().casefold() != "free":
                raise ModelPolicyError(
                    f"Worker {worker} model has no verified Free value for {field}."
                )

        model_id = model_ref.split("/", 1)[1]
        if model_ref not in models_output and model_id not in models_output:
            raise ModelPolicyError(
                f"Worker {worker} model is absent from current 'opencode models' output: "
                f"{model_ref}"
            )

    return [configured["A"], configured["B"]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the free OpenCode Go model pair.")
    parser.add_argument(
        "--models-output",
        type=Path,
        required=True,
        help="File containing the output of the current 'opencode models' command.",
    )
    parser.add_argument("--free-policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--execution-policy", type=Path, default=DEFAULT_EXECUTION)
    parser.add_argument("--max-snapshot-age-days", type=int, default=7)
    args = parser.parse_args(argv)

    if args.max_snapshot_age_days < 0:
        parser.error("--max-snapshot-age-days must be non-negative")

    try:
        model_output = args.models_output.read_text(encoding="utf-8")
        pair = validate_policy(
            read_json(args.free_policy),
            read_json(args.execution_policy),
            model_output,
            max_snapshot_age_days=args.max_snapshot_age_days,
        )
    except (OSError, ModelPolicyError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2

    print("PASS: two distinct OpenCode Go models are present and match the current Free-price policy.")
    print(f"Worker A: {pair[0]}")
    print(f"Worker B: {pair[1]}")
    print("This check does not prove post-call billing; inspect actual model and cost metadata after each task.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
