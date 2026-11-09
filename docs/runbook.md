# Feature_create — focused runbook

## Scope

Feature_create has one scientific job: **select a manageable, representative set of Result Features from papers already preprocessed by chem-literature**.

It does not extract per-paper result values, identify samples, or fill experimental conditions and protocols. Those tasks belong to the downstream project.

The strongest selection signal is substantive author discussion in the main-text Abstract, Results, Discussion, and Conclusion. Figures, tables, captions, and SI are supporting sources. A figure-only metric may still be kept when it contributes an important distinct result dimension. Do not enumerate every plotted series or impose a fixed Feature count.

## 1. Input

Run [chem-literature](https://github.com/Tuzhidingdong/chem-literature) first. Supply its final `research_corpus_paired.csv` and each paper's existing `01_preprocessing/` package.

Feature_create reads the upstream parsed text, evidence index, tables, figures, and QC report. It does not parse, render, OCR, or unpack original PDFs/SI.

## 2. Optional corpus sampling

For a large corpus, the user may choose to sample unique papers before preparation. Sampling is optional; full-corpus processing remains supported.

```powershell
python scripts/sample_corpus.py `
  --paired-csv "E:\\Zinc_batters_datas\\chem-literature\\research_corpus_paired.csv" `
  --output-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\sampling\\round-10" `
  --fraction 0.1 `
  --seed "feature-create-v1" `
  --stratify-by year
```

The sampler keeps all rows belonging to each selected paper. It creates cumulative and incremental CSVs plus a manifest. A larger sample can be expanded from the previous manifest. Sampling Quality and Feature Discovery Saturation reports are optional diagnostics; neither is a required gate nor a scientific coverage guarantee.

## 3. Prepare and review the papers

Prepare the full CSV or the chosen sampled CSV using `scripts/prepare_corpus.py`, then create bounded tasks using `scripts/build_task_queue.py`.

Use the verified free OpenCode Go Worker A/B models configured for the run. Worker A discovers candidate Features and records why the authors appear to emphasize them. Worker B independently checks for omitted important Features and confusing near-duplicates. Do not use paid fallback.

Run workers using `scripts/run_workers.py`, then run `scripts/validate_evidence.py`. These stages verify task coverage and source citations; they do not extract numeric values or associate values with conditions.

## 4. Build the candidate Feature Registry

Run `scripts/reconcile_features.py` to ingest candidate Feature observations and create a cross-paper semantic review queue. The legacy worker schema still contains a `result_records` field; Worker A must leave it empty. No Result Records are created by the active workflow.

Use `scripts/build_reconciliation_queue.py` to prepare likely duplicate pairs. Worker B compares the definitions and supporting evidence. Apply decisions with `scripts/apply_feature_reconciliation.py`. Merge only genuinely equivalent outcome concepts; keep related-but-distinct quantities separate. Ambiguous pairs remain under review.

An existing Feature Schema may optionally be imported and used to preserve compatible IDs. It must never limit discovery or selection.

## 5. Select the representative Feature set

Use `worker_prompts/feature_selection.md` with the reconciled Registry and `contracts/feature-selection.schema.json`.

For every active canonical Feature, produce one decision:
- `select`: an important, distinct result concept that belongs in the representative set;
- `exclude`: redundant, incidental, not a result outcome, or not sufficiently useful;
- `review`: meaning or evidence is unclear.

Prioritize Features substantively discussed in the main text, especially those highlighted in conclusions, used in mechanistic interpretation, or compared across systems or literature. Use recurrence as supporting evidence, not as the sole criterion. Preserve important low-frequency Features when they add otherwise-missing coverage. A fixed percentage or target count is not required.

Save the decision object as `feature_selection_decisions.json`. Every decision must cite one or more Observation IDs linked to that Feature and include a concise rationale. For every selected Feature, assign one primary category using the small controlled list in `worker_prompts/feature_selection.md`; include a short `category_rationale`. Use `unclassified` rather than forcing a doubtful category.

## 6. Export the one formal deliverable

```powershell
python scripts/export_feature_list.py `
  --registry "E:\\Zinc_batters_datas\\runs\\Feature_create\\global_feature_registry.json" `
  --decisions "E:\\Zinc_batters_datas\\runs\\Feature_create\\feature_selection_decisions.json" `
  --output "E:\\Zinc_batters_datas\\runs\\Feature_create\\output\\result_feature_list.txt"
```

The exporter validates that decisions cover every active Feature exactly once, checks that supporting Observation IDs belong to the cited Feature, and writes only selected Feature definitions. Selected Features are grouped in a fixed category order, with each entry showing its category code and rationale.

The formal scientific output is `result_feature_list.txt`. The downstream project fills values and the conditions that produced them.

## Design boundary

Keep this project focused. Do not add value/condition extraction, context-association review, comprehensive paper-level knowledge graphs, or scientific hypothesis evaluation to its required workflow. Sampling and diagnostic reports remain optional.
