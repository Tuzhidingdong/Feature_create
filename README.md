# Feature_create

**Feature_create selects a representative set of scientific Result Features from preprocessed papers.** It is deliberately single-purpose: discover candidate outcome concepts, identify what authors consider important, remove redundant concepts, and export a manageable Feature list.

It does **not** extract reported values or the sample identities, experimental conditions, and protocols that produced them. A separate downstream project fills those fields for the selected Features.

## What makes a Feature worth selecting?

The main-text Abstract, Results, Discussion, and Conclusion are important. Substantive author discussion is a strong signal:
- highlighted as a principal finding;
- used to support a mechanistic interpretation;
- compared across systems/materials or with prior work;
- explicitly discussed as an important result.

Tables, figures, captions, and SI are supporting sources. A figure-only result may still be selected when it adds a distinct scientific dimension, but mere presence in a plot is not enough. Cross-paper recurrence and coverage contribution help; frequency alone does not decide selection.

The goal is representative coverage, not exhaustive enumeration. There is no fixed percentage or Feature-count target.

## Core workflow

1. Consume parsed text, tables, figure metadata/assets, and QC information produced by [chem-literature](https://github.com/Tuzhidingdong/chem-literature). Do not re-parse PDFs or SI here.
2. Discover candidate result concepts and cite exact source evidence, especially author-discussion passages.
3. Independently check for missed important concepts and distinguish genuinely different quantities.
4. Reconcile synonyms and semantic duplicates across papers.
5. Make one global selection pass using `worker_prompts/feature_selection.md`.
6. Export only selected Features with `scripts/export_feature_list.py`.

The formal scientific output is **`result_feature_list.txt`**. Selection decisions remain a small, reviewable JSON artifact. No per-paper values or conditions are exported by this project.

## Execution and cost

Antigravity is a thin coordinator; OpenCode workers perform bounded source review. Use verified free OpenCode Go models and never fall back to paid inference. Python performs indexing, schema checks, evidence validation, stable IDs, deduplication safeguards, and deterministic export.

## Optional corpus sampling

For thousands of preprocessed papers, the deterministic paper-level sampler can start with a smaller corpus. Sampling is optional; full-corpus processing remains supported. Sampling-quality and Feature-discovery marginal-yield reports are also optional diagnostics, not required gates.

See `SKILL.md`, `worker_prompts/`, `contracts/`, `docs/architecture.md`, and `docs/output_format.md`.

## Optional 10% sampling example (Windows PowerShell)

```powershell
python scripts/sample_corpus.py `
  --paired-csv "E:\\Zinc_batters_datas\\chem-literature\\research_corpus_paired.csv" `
  --output-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\sampling\\round-10" `
  --fraction 0.1 `
  --seed "feature-create-v1" `
  --stratify-by year
```

Review the sample and manifest. 10% is an optional cost-control choice, not a scientific coverage guarantee.
