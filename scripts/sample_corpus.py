"""Create a deterministic, auditable paper-level sample from chem-literature's paired CSV."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from feature_create.sampling import SamplingError, create_corpus_sample


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paired-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fraction", default="0.1", help="Selected paper fraction from 0 (exclusive) to 1 (inclusive); default 0.1.")
    parser.add_argument("--seed", default="feature-create-v1", help="Fixed seed; identical inputs/options produce the same selected papers.")
    parser.add_argument("--stratify-by", default="year", help="Comma-separated CSV columns for proportional stratification; use 'none' for global sampling.")
    parser.add_argument("--min-papers", type=int, default=1, help="Minimum selected paper count when eligible papers exist; default 1.")
    parser.add_argument("--previous-manifest", type=Path, help="Optional previous sampling_manifest.json. Requires the same source CSV, seed and strata, and a larger new fraction; emits only newly selected papers in a separate CSV.")
    args = parser.parse_args(argv)

    stratify_by = () if args.stratify_by.strip().casefold() in {"", "none", "global"} else tuple(
        item.strip() for item in args.stratify_by.split(",") if item.strip()
    )
    try:
        summary = create_corpus_sample(
            args.paired_csv, args.output_dir,
            fraction=args.fraction, seed=args.seed,
            stratify_by=stratify_by, min_papers=args.min_papers,
            previous_manifest=args.previous_manifest,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (SamplingError, OSError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
