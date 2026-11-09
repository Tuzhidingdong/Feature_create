"""Run a read-only bidirectional integrity audit across Registry and Result Records."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from feature_create.integrity import audit_integrity


class AuditInputError(RuntimeError):
    pass


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditInputError(f"Unable to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AuditInputError(f"Expected a JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise AuditInputError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
                if not isinstance(value, dict):
                    raise AuditInputError(f"Expected object at {path}:{line_number}")
                rows.append(value)
    except OSError as exc:
        raise AuditInputError(f"Unable to read JSONL {path}: {exc}") from exc
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, help="Optional source_manifest.json for source-ID existence checks.")
    parser.add_argument("--strict-observation-records", action="store_true", help="Treat eligible Observations without a Result Record as errors rather than warnings.")
    parser.add_argument("--report", type=Path, help="Optional path for the machine-readable audit JSON.")
    args = parser.parse_args(argv)

    registry = read_object(args.registry.resolve())
    records = read_jsonl(args.records.resolve())
    manifest = read_object(args.manifest.resolve()) if args.manifest else None
    report = audit_integrity(registry, records, manifest, strict_observation_records=args.strict_observation_records)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        path = args.report.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8", newline="\n")
    print(rendered)
    return 2 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
