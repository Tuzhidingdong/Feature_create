"""Build an evidence-linked human review queue from the global integrity audit."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from feature_create.integrity import audit_integrity
from feature_create.integrity_review import build_review_queue, sha256_value
from scripts.reconcile_features import RegistryIngestionError, exclusive_lock, registry_lock_path


class QueueInputError(RuntimeError):
    pass


ARTIFACT_NAMES = (
    "global_integrity_audit.json",
    "integrity_review_queue.json",
    "integrity_review_decisions.template.jsonl",
    "integrity_review_bundle.json",
)
WORKFLOW_STATE_NAMES = (
    *ARTIFACT_NAMES,
    "integrity_review_decisions.jsonl",
    "integrity_review_apply_report.json",
)


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QueueInputError(f"Unable to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise QueueInputError(f"Expected a JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise QueueInputError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
                if not isinstance(value, dict):
                    raise QueueInputError(f"Expected an object at {path}:{line_number}.")
                rows.append(value)
    except OSError as exc:
        raise QueueInputError(f"Unable to read JSONL {path}: {exc}") from exc
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    ).encode("utf-8")


def archive_existing_workflow(output_dir: Path) -> Path | None:
    existing = [output_dir / name for name in WORKFLOW_STATE_NAMES if (output_dir / name).exists()]
    if not existing:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_dir = output_dir / f"integrity_review_archive_{stamp}_{os.getpid()}"
    suffix = 1
    while archive_dir.exists():
        archive_dir = output_dir / f"integrity_review_archive_{stamp}_{os.getpid()}_{suffix}"
        suffix += 1
    archive_dir.mkdir(parents=True, exist_ok=False)
    moved: list[tuple[Path, Path]] = []
    try:
        for source in existing:
            target = archive_dir / source.name
            os.replace(source, target)
            moved.append((source, target))
    except OSError:
        # Restore every item already moved; never leave a half-archived active workflow.
        for source, target in reversed(moved):
            if target.exists():
                os.replace(target, source)
        try:
            archive_dir.rmdir()
        except OSError:
            pass
        raise
    return archive_dir


def commit_artifacts(output_dir: Path, payloads: dict[str, bytes]) -> None:
    """Stage all files, replace payloads, and publish the bundle manifest last.

    The manifest is the commit marker. If any replace fails part-way through, the
    previous marker will no longer match the mixed artifact set and apply will fail
    closed instead of consuming a partially published review bundle.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    staged: dict[str, Path] = {}
    try:
        for name, payload in payloads.items():
            target = output_dir / name
            temporary = output_dir / f".{name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            staged[name] = temporary
        for name in (
            "global_integrity_audit.json",
            "integrity_review_queue.json",
            "integrity_review_decisions.template.jsonl",
            "integrity_review_bundle.json",
        ):
            os.replace(staged[name], output_dir / name)
    finally:
        for temporary in staged.values():
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, help="Optional source_manifest.json.")
    parser.add_argument("--review-ledger", type=Path, help="Optional prior append-only integrity review ledger.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--strict-observation-records", action="store_true")
    parser.add_argument("--force", action="store_true", help="Allow rebuilding an existing review bundle.")
    parser.add_argument(
        "--archive-existing",
        action="store_true",
        help="Move the previous queue/template/edited decisions/apply report into a timestamped archive before rebuilding.",
    )
    args = parser.parse_args(argv)

    try:
        if args.archive_existing and not args.force:
            raise QueueInputError("--archive-existing requires --force.")
        registry_path = args.registry.expanduser().resolve()
        records_path = args.records.expanduser().resolve()
        manifest_path = args.manifest.expanduser().resolve() if args.manifest else None
        ledger_path = args.review_ledger.expanduser().resolve() if args.review_ledger else None
        output_dir = args.output_dir.expanduser().resolve()

        existing_state = [output_dir / name for name in WORKFLOW_STATE_NAMES if (output_dir / name).exists()]
        if existing_state and not args.force:
            names = ", ".join(path.name for path in existing_state)
            raise QueueInputError(
                f"Existing review workflow files detected ({names}). "
                "Use --force --archive-existing only after deciding to archive and rebuild this review round."
            )
        if existing_state and not args.archive_existing:
            raise QueueInputError(
                "Refusing to overwrite an existing review round. Pass both --force and --archive-existing "
                "to preserve the previous queue, editable decisions, and apply report in an archive."
            )

        # All mutation stages that can update Registry/Records use this exact lock.
        # The ledger is acquired second everywhere to keep lock ordering deterministic.
        with ExitStack() as locks:
            locks.enter_context(exclusive_lock(registry_lock_path(registry_path)))
            if ledger_path is not None:
                ledger_lock_path = ledger_path.with_name(ledger_path.name + ".lock")
                locks.enter_context(exclusive_lock(ledger_lock_path))

            registry = read_object(registry_path)
            records = read_jsonl(records_path)
            manifest_before = sha256_file(manifest_path) if manifest_path else None
            manifest = read_object(manifest_path) if manifest_path else None
            manifest_after = sha256_file(manifest_path) if manifest_path else None
            if manifest_before != manifest_after:
                raise QueueInputError("Source manifest changed while being read; retry queue preparation.")
            ledger = read_jsonl(ledger_path) if ledger_path else []

            fingerprints = {
                "registry_sha256": sha256_file(registry_path),
                "records_sha256": sha256_file(records_path),
                "manifest_sha256": manifest_after,
                "strict_observation_records": bool(args.strict_observation_records),
            }
            audit_report = audit_integrity(
                registry, records, manifest,
                strict_observation_records=args.strict_observation_records,
            )
            queue, template_rows = build_review_queue(
                audit_report, registry, records, manifest, fingerprints, ledger,
            )

        payloads = {
            "global_integrity_audit.json": json_bytes(audit_report),
            "integrity_review_queue.json": json_bytes(queue),
            "integrity_review_decisions.template.jsonl": jsonl_bytes(template_rows),
        }
        artifact_hashes = {
            name: hashlib.sha256(payload).hexdigest()
            for name, payload in payloads.items()
        }
        bundle_core = {
            "bundle_version": "1.0",
            "queue_fingerprint": queue.get("queue_fingerprint"),
            "artifact_sha256": artifact_hashes,
        }
        bundle = {
            **bundle_core,
            "bundle_fingerprint": sha256_value(bundle_core),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "commit_protocol": "bundle_manifest_published_last",
        }
        payloads["integrity_review_bundle.json"] = json_bytes(bundle)

        output_dir.mkdir(parents=True, exist_ok=True)
        archive_dir = archive_existing_workflow(output_dir) if args.archive_existing else None
        commit_artifacts(output_dir, payloads)

        print(json.dumps({
            "audit_status": audit_report["status"],
            "audit_summary": audit_report["summary"],
            "queue_summary": queue["summary"],
            "bundle_fingerprint": bundle["bundle_fingerprint"],
            "archive_dir": str(archive_dir) if archive_dir else None,
            "outputs": {
                "audit_report": str(output_dir / "global_integrity_audit.json"),
                "review_queue": str(output_dir / "integrity_review_queue.json"),
                "decision_template": str(output_dir / "integrity_review_decisions.template.jsonl"),
                "bundle_manifest": str(output_dir / "integrity_review_bundle.json"),
            },
            "note": "The Registry, Result Records, source files, and Registry decision_log were not modified.",
        }, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, QueueInputError, RegistryIngestionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
