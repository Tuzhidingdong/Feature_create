# Feature_create

**Feature_create** is a research skill for discovering, extracting, reconciling, and documenting result features reported in scientific papers and their supplementary information.

The final formal deliverables are exactly:

- `result_feature_records.txt` — one record per reported result value, with source evidence and sample/condition context.
- `result_feature_list.txt` — a deduplicated dictionary of result features, with definitions, aliases, units, methods, and comparability notes.

## Model execution architecture

The intended architecture is:

- **Antigravity Supervisor:** thin orchestration and monitoring layer. It prepares/dispatches tasks, inspects compact progress cards, handles exceptions, and coordinates reconciliation. It must not read every paper in full or perform the bulk extraction itself.
- **OpenCode Worker A:** source-centric discovery and value extraction, pinned to a verified free OpenCode Go model.
- **OpenCode Worker B:** independent feature-centric discovery and omission audit, pinned to a second, distinct verified free OpenCode Go model.
- **Local Python:** deterministic indexing of upstream parsed outputs, task scheduling, provenance/evidence validation, stable IDs, Registry persistence, checkpoints, audits, and TXT export.

## Zero paid model-token consumption requirement

The target is **zero paid inference cost for the OpenCode Worker calls**, not zero model tokens processed in the literal accounting sense and not zero model calls.

Workers must use two distinct models whose current OpenCode Go catalog pricing is verified as Free for input, output, and cached reads; cached-write must be unpriced/dash or explicitly zero. The current preferred pair is:

- `opencode-go/crouton-alpha-free`
- `opencode-go/beam-free`

These are temporary/limited-time free offerings in the current published catalog. The pair must be checked at runtime. Do not assume a model remains free forever.

Every worker invocation must explicitly select its model. Paid fallback is forbidden. If either model is unavailable, no longer free, or cannot be verified as free, pause the task instead of selecting a paid model. The project must also account for any implicit OpenCode model calls (for example, title generation via `small_model`): use deterministic titles and pin any configured helper model to a verified free model.

The OpenCode Go subscription itself is a separate fixed subscription charge. A free model's zero per-token price does not make the subscription free.

Antigravity may be used as the Supervisor control plane, but not as the paper-reading/extraction worker. Its role should stay thin to avoid consuming the more expensive host-model quota on bulk scientific interpretation.

## Design principles

1. Discover features from source evidence; never constrain discovery to an existing schema.
2. Preserve raw reported names and definitions before normalization.
3. Keep result values separate from sample identity, conditions, time points, cycles, and protocols.
4. Never merge features on name similarity alone.
5. Keep uncertain candidates separate until sufficient evidence exists.
6. Treat figures as first-class sources; never convert visual estimates into exact values.
7. Use Python for IDs, validation, state transitions, audit logs, and deterministic export.
8. Preserve provenance and decisions so a run can be resumed and audited.
9. Keep Worker A and Worker B first-pass outputs independent.
10. Never silently switch a worker to a paid model.

## Repository map

- `SKILL.md` — execution contract and agent instructions.
- `contracts/` — machine-readable schemas for Registry and worker payloads.
- `feature_create/sampling.py` + `scripts/sample_corpus.py` — optional deterministic paper-level sampling before evidence indexing and worker dispatch.
- `contracts/sampling-manifest.schema.json` — auditable sample-selection manifest contract.
- `docs/architecture.md` — component boundaries and workflow.
- `docs/upstream_input_contract.md` — the formal chem-literature CSV/file-path boundary.
- `docs/token_policy.md` — zero-paid-cost policy and provider/model guards.
- `docs/opencode_free_model_execution.md` — OpenCode Go invocation, model pinning, and verification.
- `scripts/adjudicate_unresolved.py` + `contracts/manual-context-adjudication.schema.json` — two-step, evidence-checked human resolution for context links that Worker B cannot close.
- `worker_prompts/` — isolated Worker A, Worker B, and targeted-review prompts.
- `config/` — model and extraction policies.

## Status

Architecture/contract baseline: **v0.1**  
End-to-end implementation: **not yet declared complete**  
Production readiness: **not yet established**


## Local execution quick start (Windows PowerShell)

Install Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

### Required upstream input

Run the merged [chem-literature main branch](https://github.com/Tuzhidingdong/chem-literature) first. Its final `research_corpus_paired.csv` must contain:
- the curated paper rows and original `local_pdf_path` / `local_si_info` provenance;
- `preprocessing_path`, `preprocessing_status`, and `preprocessing_qc_report`;
- one `01_preprocessing/` package per paper, including `evidence_index.json`, `PREPROCESSING_QC_REPORT.md`, `main_analysis.md`, `si_analysis.md`, extracted tables, and image assets.

Feature_create consumes this CSV and the already-generated preprocessing packages. It does not parse PDFs, unpack SI archives, re-extract tables, or render PDF pages. It hashes the CSV-listed raw files only to confirm that they still match the upstream package; raw PDFs/SI are provenance and are never attached to Worker A/B. The CSV remains the complete paper-selection boundary.


For a corpus with thousands of preprocessed papers, optionally sample 10% before indexing. The sampler selects unique papers (not CSV rows), defaults to proportional stratification by year, and preserves every row belonging to each selected paper. It reads CSV metadata only; PDFs, SI, and preprocessing packages are not opened at this stage.

~~~powershell
python scripts/sample_corpus.py `
  --paired-csv "E:\Zinc_batters_datas\chem-literature\research_corpus_paired.csv" `
  --output-dir "E:\Zinc_batters_datas\runs\Feature_create\sampling\round-10" `
  --fraction 0.1 `
  --seed "feature-create-v1" `
  --stratify-by year
~~~

The output contains:
- `sampled_research_corpus_paired.csv` — cumulative selected papers;
- `newly_selected_research_corpus_paired.csv` — only papers newly selected in this round (the same as the cumulative file for the first round);
- `sampling_manifest.json` — source/sample hashes, paper-level decisions and scores, exclusions, quotas, and lineage.

Stratum quotas use the D'Hondt divisor method. For an unchanged source CSV, increasing the sample fraction cannot reduce any stratum's quota, so larger samples are guaranteed to contain the earlier sample. To expand 10% to 20%, preserve the first round's artifacts and run:

~~~powershell
python scripts/sample_corpus.py `
  --paired-csv "E:\Zinc_batters_datas\chem-literature\research_corpus_paired.csv" `
  --output-dir "E:\Zinc_batters_datas\runs\Feature_create\sampling\round-20" `
  --fraction 0.2 `
  --seed "feature-create-v1" `
  --stratify-by year `
  --previous-manifest "E:\Zinc_batters_datas\runs\Feature_create\sampling\round-10\sampling_manifest.json"
~~~

The second round emits a cumulative 20% CSV plus an incremental CSV containing only the additional papers. Expansion requires the exact same source CSV bytes, seed, strata, and `min_papers`; the new fraction must be greater. The previous manifest and previous cumulative CSV must still exist and match their recorded hashes. If rounding produces no new whole papers, the command reports `no_new_papers` and the empty incremental CSV must not be dispatched.

Before preparation, inspect the cumulative sample's metadata distribution against the full eligible population:

~~~powershell
python scripts/report_sampling_quality.py --paired-csv "E:\Zinc_batters_datas\chem-literature\research_corpus_paired.csv" --sampling-manifest "E:\Zinc_batters_datas\runs\Feature_create\sampling\round-10\sampling_manifest.json" --output-dir "E:\Zinc_batters_datas\runs\Feature_create\sampling\round-10\quality"
~~~

This read-only paper-level report compares year, journal, article type, and preprocessing status by default. It records category-share differences, TVD, missing categories, and metadata conflicts; it does not modify the sample or certify scientific representativeness. For later rounds, report against that round's cumulative CSV, not its incremental-only CSV. For details, see [docs/sampling_quality.md](docs/sampling_quality.md).


Prepare the first-round or incremental-only CSV with the matching manifest. This example prepares the new papers from round 20 in a fresh output directory:

~~~powershell
python scripts/prepare_corpus.py `
  --paired-csv "E:\Zinc_batters_datas\runs\Feature_create\sampling\round-20\newly_selected_research_corpus_paired.csv" `
  --sampling-manifest "E:\Zinc_batters_datas\runs\Feature_create\sampling\round-20\sampling_manifest.json" `
  --corpus-root "E:\Zinc_batters_datas\chem-literature" `
  --output "E:\Zinc_batters_datas\runs\Feature_create\prepared\round-20-incremental"
~~~

Preparation identifies whether the supplied CSV is the cumulative or incremental view, verifies the corresponding hash and paper keys, and records both cumulative and applied-run provenance. For incremental expansion, keep the same persistent Registry and Result Record paths in the later reconciliation stage; create a fresh prepared/dispatch directory for the new batch. Do not dispatch the cumulative CSV again, or previous papers will be scheduled again. To process the full corpus, skip sampling and use the original CSV without `--sampling-manifest`.

This creates `source_manifest.json`, exact staging copies of upstream-parsed text/table content, and exact copies of each selected paper's upstream `evidence_index.json`. No PDF/SI parsing is performed by Feature_create.
Build the bounded Worker A/B chunk queue:

```powershell
python scripts/build_task_queue.py `
  --manifest "E:\Zinc_batters_datas\runs\Feature_create\prepared\round-20-incremental\source_manifest.json" `
  --output "E:\Zinc_batters_datas\runs\Feature_create\dispatch\round-20-incremental" `
  --max-bundle-chars 120000 `
  --max-attachments-per-chunk 8
```

The task builder does not create one unbounded whole-paper prompt. It creates as many deterministic chunks as needed:
- parsed text is divided on upstream page headings and paragraph/newline boundaries; exact character offsets and page numbers are retained;
- structured tables are divided by original 1-based row numbers; a single oversized row is split into original row/column cell fragments with exact character offsets;
- a pre-extracted figure/table image is assigned once to its related evidence unit when the upstream index provides that relationship;
- adjacent snippets and repeated table-prefix rows are marked context-only and do not count as assigned coverage;
- `paper_chunk_index.json` exposes a compact `unit_navigation` inventory, `cross_chunk_text_boundaries` for adjacent text ranges split across chunks, and `same_original_table_row_groups` for original table rows/cells split across chunks; each task receives only its `chunk_evidence_index.json` locator slice and assigned content;
- oversized atomic bundles that still exceed the configured hard limit are blocked explicitly, never truncated silently.

The queue produces one independent Worker A and Worker B task per chunk. Each task contains a coverage checklist with one stable `scope_id` for every `(source_id, unit_id)` assignment, including linked visual assets. Each worker must return one coverage item per scope; when a source spans several units/chunks, coverage is aggregated across every scope before that source can be counted as fully reviewed.
First perform model-price preflight and command planning without inference:

```powershell
python scripts/run_workers.py `
  --queue "E:\Zinc_batters_datas\runs\Feature_create\dispatch\round-20-incremental\task_queue.json" `
  --run-dir "E:\Zinc_batters_datas\runs\Feature_create\worker_runs\round-20-incremental" `
  --dry-run
```

Only after the dry run passes, dispatch the two free workers:

```powershell
python scripts/run_workers.py `
  --queue "E:\Zinc_batters_datas\runs\Feature_create\dispatch\round-20-incremental\task_queue.json" `
  --run-dir "E:\Zinc_batters_datas\runs\Feature_create\worker_runs"
```

Validate result-value evidence:

```powershell
python scripts/validate_evidence.py `
  --manifest "E:\Zinc_batters_datas\runs\Feature_create\prepared\round-20-incremental\source_manifest.json" `
  --queue "E:\Zinc_batters_datas\runs\Feature_create\dispatch\round-20-incremental\task_queue.json" `
  --run-dir "E:\Zinc_batters_datas\runs\Feature_create\worker_runs"
```

The first run should be performed on a small paper subset to validate the local OpenCode CLI version, model availability, task bundle size, and JSON event/export format. Do not start a large corpus run until the local smoke test succeeds.

## Optional Feature discovery saturation diagnostics

This is a **manually invoked, optional diagnostic**, not a required workflow phase. It is not run automatically after a batch and never blocks later extraction batches or determines that sampling should stop.

After multiple batches have been ingested into the same persistent Registry and semantically reconciled, the report compares the number of newly observed current canonical result Features per 100 papers. It uses the current Registry so already-applied Feature merges are not double-counted. Batch manifests must be supplied in chronological order; for later sample expansions, use the incremental prepared manifest, not the cumulative one.

~~~powershell
python scripts/report_feature_saturation.py --registry "E:\Zinc_batters_datas\runs\Feature_create\global_feature_registry.json" --batch "round-10=E:\Zinc_batters_datas\runs\Feature_create\prepared\round-10\source_manifest.json" --batch "round-20-incremental=E:\Zinc_batters_datas\runs\Feature_create\prepared\round-20-incremental\source_manifest.json" --output-dir "E:\Zinc_batters_datas\runs\Feature_create\diagnostics\feature-saturation"
~~~

The JSON and Markdown reports show batch-level marginal yield, cumulative canonical Feature counts, evidence-verified observations, uncertain observations, and scope warnings. No automatic saturated/not-saturated verdict is generated. A single batch can establish a baseline; at least three batches are more useful for a descriptive trend. A low yield may reflect incomplete extraction or unresolved reconciliation rather than genuine discovery saturation. This utility is not run unless explicitly invoked. See [docs/feature_discovery_saturation.md](docs/feature_discovery_saturation.md).

## Targeted cross-chunk context review

After first-pass Worker A/B results are ingested into the Registry, unresolved value-specific context links can be reviewed selectively with the existing Worker B model:

~~~powershell
python scripts/build_context_review_queue.py --manifest "E:\Zinc_batters_datas\runs\Feature_create\prepared\round-20-incremental\source_manifest.json" --queue "E:\Zinc_batters_datas\runs\Feature_create\dispatch\round-20-incremental\task_queue.json" --unresolved "E:\Zinc_batters_datas\runs\Feature_create\worker_runs\unresolved_extraction_items.jsonl" --records "E:\Zinc_batters_datas\runs\Feature_create\worker_runs\result_records.jsonl" --output "E:\Zinc_batters_datas\runs\Feature_create\context_review_dispatch"
~~~

The queue includes only unresolved items with an explicit remote target unit and a linked result record. It supplies the current result evidence and nominated target evidence, not the full paper. Dispatch it through the normal free-model gate, then run validate_evidence.py on the context queue before applying decisions with apply_context_reviews.py. A confirmed association may add context facts only; original result values and Feature identity are protected. Validation binds the exact source manifest, evidence manifest, prompt, bundle and visual attachments, and apply rechecks those hashes. No-association requires positive counter-evidence, not merely a missing match; insufficient/conflicting evidence remains unresolved. Resolved items are not redispatched; a repeat pass on inconclusive items requires the explicit --retry-inconclusive switch.

More detail: [docs/runbook.md](docs/runbook.md).

## Global bidirectional integrity audit

Before treating the two TXT files as final, audit the Registry ↔ Observation ↔ Result Record graph. This read-only audit checks both directions of the links, source evidence, merge redirects, eligible Record-to-Observation links, and evidence coverage of exclusion/split/merge decision history. The final exporter also invokes it under the shared Registry lock; hard integrity errors block both formal TXT outputs, and its internal report records input and output SHA-256 fingerprints.

```powershell
python scripts/audit_global_integrity.py `
  --registry "E:\Zinc_batters_datas\runs\Feature_create\global_feature_registry.json" `
  --records "E:\Zinc_batters_datas\runs\Feature_create\worker_runs\result_records.jsonl" `
  --manifest "E:\Zinc_batters_datas\runs\Feature_create\prepared\round-20-incremental\source_manifest.json" `
  --report "E:\Zinc_batters_datas\runs\Feature_create\global_integrity_audit.json"
```

Eligible Observations without a Result Record are warnings by default because a source may identify a variable without supplying an exportable value. Add `--strict-observation-records` when complete Observation-to-Record materialization is required. See [docs/global_integrity_audit.md](docs/global_integrity_audit.md).



## Integrity review and remediation queue

Build a source-linked human review queue from the global audit, then record triage separately from the Registry. Queue artifacts are snapshot-bound by a bundle manifest, and human decisions are tied to the complete queue fingerprint. The builder and apply step coordinate through the shared Registry lock; incomplete or stale bundles fail closed. The queue never changes scientific records or decision history; corrections must use the existing evidence/reconciliation workflows and be confirmed by a new audit. See [docs/integrity_review_workflow.md](docs/integrity_review_workflow.md) for the prepare → review → append-only ledger → correct → re-audit process.

