"""Compare a cumulative sample's metadata distribution with its eligible corpus."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from feature_create.sampling_quality import (
    DEFAULT_DIMENSIONS,
    SamplingQualityError,
    build_sampling_quality_report,
)
from feature_create.sampling import SamplingError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paired-csv", type=Path, required=True, help="Exact source research_corpus_paired.csv used for sampling.")
    parser.add_argument("--sampling-manifest", type=Path, required=True, help="sampling_manifest.json created by sample_corpus.py.")
    parser.add_argument("--sample-csv", type=Path, help="Optional cumulative sampled CSV; defaults to sampled_csv_path in the manifest.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--dimensions",
        default=",".join(DEFAULT_DIMENSIONS),
        help="Comma-separated paper metadata columns; defaults to year,journal,article_type,preprocessing_status.",
    )
    args = parser.parse_args(argv)
    dimensions = tuple(item.strip() for item in args.dimensions.split(",") if item.strip())
    try:
        report = build_sampling_quality_report(
            args.paired_csv,
            args.sampling_manifest,
            args.output_dir,
            sample_csv=args.sample_csv,
            dimensions=dimensions,
        )
        print(json.dumps({
            "status": "complete",
            "eligible_population_paper_count": report["sample"]["eligible_population_paper_count"],
            "selected_paper_count": report["sample"]["selected_paper_count"],
            "actual_fraction": report["sample"]["actual_fraction_of_eligible_population"],
            "report_files": report["report_files"],
        }, ensure_ascii=False, indent=2))
        return 0
    except (SamplingQualityError, SamplingError, OSError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
