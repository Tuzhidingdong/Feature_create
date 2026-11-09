"""Optional diagnostics for marginal Feature discovery across sequential paper batches."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from feature_create.discovery_saturation import (
    FeatureSaturationError,
    build_feature_saturation_report,
)


def parse_batch(value: str) -> tuple[str, Path]:
    label, separator, path = value.partition("=")
    if not separator or not label.strip() or not path.strip():
        raise argparse.ArgumentTypeError(
            "Each --batch must use LABEL=path/to/source_manifest.json."
        )
    return label.strip(), Path(path.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "OPTIONAL: compare newly observed canonical result Features across ordered batches. "
            "This command is read-only and never blocks extraction or later batches."
        )
    )
    parser.add_argument("--registry", type=Path, required=True, help="Current global_feature_registry.json.")
    parser.add_argument(
        "--batch",
        action="append",
        type=parse_batch,
        required=True,
        metavar="LABEL=SOURCE_MANIFEST",
        help="Prepared source_manifest.json for one batch. Repeat in chronological order; use incremental manifests for later sampled rounds.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--allow-unassigned",
        action="store_true",
        help="Allow Registry observations from paper IDs not represented by supplied batch manifests; the report will mark its scope incomplete.",
    )
    args = parser.parse_args(argv)

    try:
        report = build_feature_saturation_report(
            args.registry,
            args.batch,
            args.output_dir,
            allow_unassigned=args.allow_unassigned,
        )
        print(json.dumps({
            "status": "complete",
            "optional": True,
            "saturation_verdict": None,
            "batch_count": report["inputs"]["batch_count"],
            "paper_count": report["summary"]["supplied_batch_paper_count"],
            "unique_current_canonical_result_feature_count": report["summary"]["unique_current_canonical_result_feature_count"],
            "trend_comparison": report["summary"]["trend_comparison"],
            "warnings": report["warnings"],
            "report_files": report["report_files"],
        }, ensure_ascii=False, indent=2))
        return 0
    except (FeatureSaturationError, OSError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
