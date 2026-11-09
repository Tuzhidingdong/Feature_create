"""Validate human integrity-review decisions and append them to a separate ledger."""
from __future__ import annotations

import argparse
import json
import hashlib
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts" / "integrity-review-decision.schema.json"
sys.path.insert(0, str(ROOT))

from feature_create.integrity_review import (
    make_ledger_entries,
    sha256_value,
    validate_review_rows,
)


class ReviewApplyError(RuntimeError):
    pass


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewApplyError(f"Unable to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewApplyError(f"Expected a JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReviewApplyError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ReviewApplyError(f"Expected an object at {path}:{line_number}.")
            rows.append(value)
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def exclusive_lock(path: Path, timeout_seconds: float = 30.0):
    class Lock:
        def __enter__(self):
            self.path = path
            self.path.parent.mkdir(parents=True, exist_ok=True)
            deadline = time.monotonic() + timeout_seconds
            self.fd = None
            while self.fd is None:
                try:
                    self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(self.fd, f"pid={os.getpid()}".encode("utf-8"))
                except FileExistsError:
                    if time.monotonic() >= deadline:
                        raise ReviewApplyError(f"Timed out waiting for review-ledger lock: {self.path}")
                    time.sleep(0.2)
            return self

        def __exit__(self, exc_type, exc, tb):
            if self.fd is not None:
                os.close(self.fd)
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
    return Lock()


def validate_decision_contracts(rows: list[dict[str, Any]]) -> None:
    if Draft202012Validator is None:
        raise ReviewApplyError("Install jsonschema from requirements.txt before applying human review decisions.")
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewApplyError(f"Unable to load integrity review decision schema: {exc}") from exc
    validator = Draft202012Validator(schema)
    for index, row in enumerate(rows, 1):
        if not str(row.get("review_decision") or "").strip():
            continue
        errors = sorted(validator.iter_errors(row), key=lambda error: list(error.absolute_path))
        if errors:
            error = errors[0]
            location = "/".join(str(part) for part in error.absolute_path) or "$"
            raise ReviewApplyError(f"Decision row {index} violates integrity-review schema at {location}: {error.message}")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
        handle.flush()
        os.fsync(handle.fileno())
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True, help="Immutable JSONL template generated with the queue.")
    parser.add_argument("--decisions", type=Path, required=True, help="Human-edited copy of the template.")
    parser.add_argument("--registry", type=Path, required=True, help="Used only to verify the queue input snapshot.")
    parser.add_argument("--records", type=Path, required=True, help="Used only to verify the queue input snapshot.")
    parser.add_argument("--manifest", type=Path, help="Required when the queue was built with a source manifest.")
    parser.add_argument("--ledger", type=Path, required=True, help="Separate append-only review ledger; never the Registry decision_log.")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        queue_path = args.queue.expanduser().resolve()
        queue = read_object(queue_path)
        template_rows = read_jsonl(args.template.expanduser().resolve())
        decision_rows = read_jsonl(args.decisions.expanduser().resolve())

        expected = queue.get("input_fingerprints") or {}
        queue_core = {
            "queue_version": queue.get("queue_version"),
            "audit_fingerprint": queue.get("audit_fingerprint"),
            "audit_status": queue.get("audit_status"),
            "input_fingerprints": expected,
            "finding_ids": queue.get("finding_ids") or [],
            "known_source_ids": queue.get("known_source_ids") or [],
            "findings_sha256": sha256_value(queue.get("findings") or []),
        }
        if sha256_value(queue_core) != queue.get("queue_fingerprint"):
            raise ReviewApplyError("Queue fingerprint is invalid; rebuild the review queue.")
        actual_manifest_path = args.manifest.expanduser().resolve() if args.manifest else None
        actual = {
            "registry_sha256": sha256_file(args.registry.expanduser().resolve()),
            "records_sha256": sha256_file(args.records.expanduser().resolve()),
            "manifest_sha256": sha256_file(actual_manifest_path) if actual_manifest_path else None,
            "strict_observation_records": expected.get("strict_observation_records"),
        }
        if actual != expected:
            raise ReviewApplyError(
                "The Registry, Result Records, or manifest changed after queue preparation. "
                "Rebuild the audit queue and review the new template before applying decisions."
            )

        findings = {item.get("finding_id"): item for item in queue.get("findings", [])}
        for row in template_rows:
            current = findings.get(row.get("finding_id"))
            if not current or (
                current.get("finding_fingerprint") != row.get("finding_fingerprint")
                or current.get("severity") != row.get("severity")
                or current.get("code") != row.get("code")
            ):
                raise ReviewApplyError(f"Template finding no longer matches queue: {row.get('finding_id')}.")

        validate_decision_contracts(decision_rows)
        validated = validate_review_rows(queue, template_rows, decision_rows)
        new_entries = make_ledger_entries(validated)
        ledger_path = args.ledger.expanduser().resolve()
        lock_path = ledger_path.with_name(ledger_path.name + ".lock")
        with exclusive_lock(lock_path):
            existing = read_jsonl(ledger_path)
            existing_ids = {str(item.get("decision_id")) for item in existing}
            existing_snapshots = {
                (str(item.get("finding_id")), str(item.get("audit_fingerprint")))
                for item in existing
            }
            to_append = []
            for entry in new_entries:
                if entry["decision_id"] in existing_ids:
                    continue
                snapshot_key = (entry["finding_id"], entry["audit_fingerprint"])
                if snapshot_key in existing_snapshots:
                    raise ReviewApplyError(
                        f"{entry['finding_id']}: a decision already exists for this exact audit snapshot."
                    )
                to_append.append(entry)
                existing_ids.add(entry["decision_id"])
                existing_snapshots.add(snapshot_key)
            appended = append_jsonl(ledger_path, to_append) if to_append else 0

        decision_counts: dict[str, int] = {}
        action_counts: dict[str, int] = {}
        for entry in new_entries:
            decision = str(entry.get("review_decision") or "")
            action = str(entry.get("action") or "")
            decision_counts[decision] = decision_counts.get(decision, 0) + 1
            action_counts[action] = action_counts.get(action, 0) + 1

        report = {
            "status": "applied",
            "queue_fingerprint": queue.get("queue_fingerprint"),
            "decision_rows_validated": len(validated),
            "ledger_entries_appended": appended,
            "decision_counts": decision_counts,
            "action_counts": action_counts,
            "ledger_path": str(ledger_path),
            "registry_modified": False,
            "records_modified": False,
            "source_files_modified": False,
            "registry_decision_log_modified": False,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "important_note": (
                "This records human triage only. It does not repair or suppress an audit finding. "
                "After a correction through the existing workflow, rebuild the queue to confirm clearance."
            ),
        }
        report_path = args.report.expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, ReviewApplyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
