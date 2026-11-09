# Sampling Quality Diagnostics

## Purpose

This report checks whether the selected paper sample has a similar **metadata distribution** to the eligible source population. It is an advisory diagnostic after sampling, not a second selection algorithm, scientific quality score, or certification of representativeness.

The comparison is made at the unique-paper level. Multiple CSV rows for one DOI/title-year-journal identity count as one paper. The population is the set of papers marked eligible in sampling_manifest.json; papers excluded by preprocessing-path/status or metadata-conflict rules are counted separately and are not included in either distribution.

The default dimensions are year, journal, article_type, and preprocessing_status. Other source CSV columns can be selected with --dimensions.

## Run

After creating the sample with scripts/sample_corpus.py, run:

~~~powershell
python scripts/report_sampling_quality.py --paired-csv "E:\Zinc_batters_datas\chem-literature\research_corpus_paired.csv" --sampling-manifest "E:\Zinc_batters_datas\runs\Feature_create\sampling\round-10\sampling_manifest.json" --output-dir "E:\Zinc_batters_datas\runs\Feature_create\sampling\round-10\quality"
~~~

By default, the tool reads the cumulative CSV path recorded in the manifest. To specify it explicitly, add --sample-csv and the path to sampled_research_corpus_paired.csv.

A report for selected dimensions only:

~~~powershell
python scripts/report_sampling_quality.py --paired-csv "E:\Zinc_batters_datas\chem-literature\research_corpus_paired.csv" --sampling-manifest "E:\Zinc_batters_datas\runs\Feature_create\sampling\round-10\sampling_manifest.json" --output-dir "E:\Zinc_batters_datas\runs\Feature_create\sampling\round-10\quality-journal" --dimensions year,journal,article_type
~~~

The command writes sampling_quality_report.json (machine-readable counts, shares, metrics, hashes, and limitations) and sampling_quality_report.md (human-readable tables and interpretation notes).

## Integrity checks

Before generating a report, the tool verifies:

1. The exact source CSV SHA-256 matches source_csv_sha256 in the sampling manifest.
2. The supplied sample SHA-256 matches sampled_csv_sha256; the default is the manifest's cumulative sampled_csv_path.
3. Manifest paper decisions cover every unique source-paper identity and record the correct source row count.
4. Selected keys agree with both the manifest's selected_paper_keys and each paper decision's selected flag; selected papers must be eligible.
5. The cumulative sample contains exactly the selected paper identities and retains every original CSV row for each selected paper.

The incremental-only CSV is intentionally rejected. It contains only the newly added papers from an expansion round, so comparing it directly with the entire eligible population would produce a misleading distribution report. Use the cumulative sample for that round.

The quality report does not modify sampling_manifest.json, either sample CSV, selection scores, or selected-paper decisions. It reads CSV metadata only; it does not open PDFs, SI files, or preprocessing packages.

## Metrics

For every category in each dimension, the report gives population count/share, sample count/share, share difference in percentage points, and the representation ratio (sample share divided by population share).

It also reports:

- **Total variation distance (TVD):** one half of the sum of absolute category-share differences. A value of 0 means the observed category proportions are identical; larger values mean greater distributional difference for that dimension.
- **Maximum absolute share difference:** the largest category-level difference, in percentage points.
- **Categories absent from the sample:** population categories with zero selected papers, plus the number of eligible population papers in those categories.
- **Metadata conflicts:** eligible papers with more than one normalized value for the requested dimension. Such papers are grouped under (conflict) rather than silently assigned to one value.
- **Missing metadata:** empty values are retained as the (missing) category instead of being dropped.

The tool deliberately does not apply a universal pass/fail threshold. A low TVD for year or journal does not establish that rare result features, unusual methods, or under-represented scientific subfields are covered. Conversely, an absent rare category in a small sample is a prompt for review, not proof that the overall sample is unusable.

## Suggested use in the staged workflow

1. Generate the first 10% sample.
2. Generate and inspect the quality report before building the first Feature_create task queue.
3. Review large share differences and categories absent from the sample. Decide manually whether the initial sample is adequate for the intended feature-tree discovery.
4. When expanding to 20% or 30%, generate a new report from that round's **cumulative** sample and the same immutable source CSV.
5. Continue to use only the incremental CSV for incremental preparation and dispatch. Keep the cumulative CSV for quality comparison and lineage verification.

The quality report is metadata-level evidence about the sample composition. It is not a replacement for later source-coverage, feature-discovery, or evidence-integrity audits.
