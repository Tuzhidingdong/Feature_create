"""Build an evidence-linked human review queue from the global integrity audit."""
from __future__ import annotations

import argparse
import json
import hashlib
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from feature_create.integrity import audit_integrity
from feature_create.integrity_review import build_review_queue


class QueueInputError(RuntimeError):
    pass


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QueueInputError(f"Unable to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise QueueInputError(f"Expected a JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, help="Optional source_manifest.json.")
    parser.add_argument("--review-ledger", type=Path, help="Optional prior append-only integrity review ledger.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--strict-observation-records", action="store_true")
    parser.add_argument("--force", action="store_true", help="Allow replacing an existing editable decision template.")
    args = parser.parse_args(argv)

    try:
        registry_path = args.registry.expanduser().resolve()
        records_path = args.records.expanduser().resolve()
        manifest_path = args.manifest.expanduser().resolve() if args.manifest else None
        registry = read_object(registry_path)
        records = read_jsonl(records_path)
        manifest = read_object(manifest_path) if manifest_path else None
        ledger = read_jsonl(args.review_ledger.expanduser().resolve()) if args.review_ledger else []

        fingerprints = {
            "registry_sha256": sha256_file(registry_path),
            "records_sha256": sha256_file(records_path),
            "manifest_sha256": sha256_file(manifest_path) if manifest_path else None,
            "strict_observation_records": bool(args.strict_observation_records),
        }
        audit_report = audit_integrity(
            registry, records, manifest,
            strict_observation_records=args.strict_observation_records,
        )
        queue, template_rows = build_review_queue(
            audit_report, registry, records, manifest, fingerprints, ledger,
        )

        output_dir = args.output_dir.expanduser().resolve()
        template_path = output_dir / "integrity_review_decisions.template.jsonl"
        if template_path.exists() and not args.force:
            raise QueueInputError(
                f"Editable template already exists: {template_path}. "
                "Copy it to a decisions file before rebuilding, or pass --force to replace it."
            )

        write_json(output_dir / "global_integrity_audit.json", audit_report)
        write_json(output_dir / "integrity_review_queue.json", queue)
        write_jsonl(template_path, template_rows)

        print(json.dumps({
            "audit_status": audit_report["status"],
            "audit_summary": audit_report["summary"],
            "queue_summary": queue["summary"],
            "outputs": {
                "audit_report": str(output_dir / "global_integrity_audit.json"),
                "review_queue": str(output_dir / "integrity_review_queue.json"),
                "decision_template": str(template_path),
            },
            "note": "The Registry, Result Records, source files, and Registry decision_log were not modified.",
        }, ensure_ascii=False, indent=2))
        return 0
    except (QueueInputError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
