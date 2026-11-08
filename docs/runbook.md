# Feature_create Local Runbook (v0.1)

## 1. Preconditions

- Windows 10/11 or a supported Python environment.
- Python 3.10+.
- OpenCode CLI installed and authenticated for OpenCode Go.
- Current OpenCode Go plan/account allows access to the selected limited-time free models in the user's region.
- A completed [chem-literature `main` workflow](https://github.com/Tuzhidingdong/chem-literature/tree/main) run.
- `research_corpus_paired.csv` contains `preprocessing_path`, `preprocessing_status`, and the referenced `01_preprocessing/` packages.
- Enough local disk space for task bundles, pre-extracted image attachments, worker outputs, and session exports. Feature_create does not create another parsed PDF/SI corpus.

Install dependencies:

~~~powershell
python -m pip install -r requirements.txt
~~~

Check the current CLI version and model command syntax:

~~~powershell
opencode --version
opencode models --help
opencode run --help
opencode export --help
~~~

The workflow uses `opencode run --model`, `--agent`, `--file`, `--format json`, `--title`, and `--dir`.

## 2. Prepare the upstream-preprocessed corpus

Run the merged [chem-literature `main` workflow](https://github.com/Tuzhidingdong/chem-literature/tree/main) first. Stage 5 preprocessing is enabled by default; make sure it was not skipped. The final `research_corpus_paired.csv` must include:
- `local_pdf_path` and `local_si_info` for raw-source provenance and hash verification;
- `preprocessing_path` pointing to that paper's `01_preprocessing/`;
- `preprocessing_status` matching `evidence_index.json`;
- `preprocessing_qc_report` (recommended).

Each package must include:
- `evidence_index.json` and `PREPROCESSING_QC_REPORT.md`;
- `main_analysis.md` and `si_analysis.md`;
- `extracted_tables/` and `extracted_figures/` assets;
- page, table, figure, SI-reference, coordinate, and hash metadata in the evidence index.

The CSV is the complete paper-selection authority. Feature_create does not scan the upstream folders, add unlisted files, repeat article screening, or substitute missing PDFs/SI.

The current chem-literature workflow writes relative paths against the directory from which `run_workflow.py` was executed. For example, when it is run from:

`E:\\Zinc_batters_datas\\chem-literature`

index the upstream packages:

~~~powershell
python scripts/prepare_corpus.py `
  --paired-csv "E:\\Zinc_batters_datas\\chem-literature\\research_corpus_paired.csv" `
  --corpus-root "E:\\Zinc_batters_datas\\chem-literature" `
  --output "E:\\Zinc_batters_datas\\runs\\Feature_create\\prepared"
~~~

The preparation adapter:
- reads only rows from `research_corpus_paired.csv`;
- resolves each row's `preprocessing_path`;
- validates package version, CSV/package DOI, preprocessing status, QC report, and evidence index;
- hashes CSV-listed raw MAIN/SI files to verify they still match upstream inventory records;
- validates extracted image hashes and referenced table exports;
- preserves page blocks, table rows/cells, figure IDs/bounding boxes, SI references, scan flags, QC warnings, and unsupported-SI status;
- copies the evidence index byte-for-byte into the Feature_create run.

It does **not** parse PDFs, render pages, extract tables, unpack SI archives, or parse DOCX/XLSX. Raw MAIN/SI files are not attached to workers.

Outputs:
- `source_manifest.json` — internal logical-source index derived from the paired CSV and upstream packages;
- `extracted_text/` — exact staging copies of upstream MAIN/SI text and structured table/caption text for evidence checking;
- `upstream_evidence_indexes/` — exact copies of upstream `evidence_index.json` files;
- `prepare_summary.json` — ready/blocked paper and source counts.

`COMPLETE` packages may proceed normally. `WARNING_SCANNED_PAGES`, `PARTIAL_SI_UNSUPPORTED`, and `PARTIAL_NO_SI` are preserved as warnings and must remain visible to both workers. A missing/corrupt package, unsupported package version, raw-source hash mismatch, or missing MAIN parsed text blocks the paper instead of falling back to raw-document parsing.

## 3. Build isolated Worker tasks

~~~powershell
python scripts/build_task_queue.py --manifest "E:\\Zinc_batters_datas\\runs\\Feature_create\\prepared\\source_manifest.json" --output "E:\\Zinc_batters_datas\\runs\\Feature_create\\dispatch"
~~~

One independent Worker A and Worker B task pair is created for each bounded chunk of an eligible paper. A paper may therefore produce multiple task pairs. The default bundle limit is 120,000 characters, the Windows command limit is 7,600 characters, and the default attachment limit is 12 (`--max-bundle-chars`, `--max-command-chars`, and `--max-attachments` can override them). Each chunk has:
- stable task ID and exact pinned model reference;
- worker prompt and consolidated upstream-parsed text/table bundle;
- exact upstream `evidence_index.json` and QC report attachments;
- pre-extracted figure/table image assets when available;
- stable source IDs and original page/table/figure/SI locators;
- raw PDF/SI hash inventory for provenance only.

The task builder must never attach raw PDF, ZIP, DOCX, or XLSX source files. It attaches only pre-extracted visual assets and the authoritative upstream evidence index/QC report. Each assigned page/text segment/table-row range/asset has a stable `scope_id`; its original `source_id`, page number, figure/table ID, bounding box, and original table row/column positions remain unchanged. Every chunk also stores the exact scope text locally for downstream quote verification. If a source package is partial, the bundle carries its warnings and unparsed materials; workers must mark the corresponding scopes incomplete.

Long upstream-parsed MAIN/SI text is split first at chem-literature's explicit `## Page N (MAIN|SI)` boundaries. A single oversized page is split at paragraph/line/whitespace boundaries without dropping or duplicating characters. Structured tables are split into bounded groups of original rows; later groups repeat the original first row as header context while retaining original 1-based row numbers. Chunk packing respects bundle size, attachment count, and command-line limits. No content is silently truncated. An individual row or source asset that cannot fit safely is retained in a blocked task rather than omitted. Coverage is recorded per `scope_id` and aggregated by original `source_id` only after every scope for that source has been fully reviewed by that worker.

## 4. Verify the free model pair before dispatch

The worker runner refreshes the current OpenCode Go model catalog using:

```powershell
opencode models opencode-go --refresh --verbose
```

It then verifies the two exact provider/model IDs against the dated price policy:
- Worker A: `opencode-go/crouton-alpha-free`
- Worker B: `opencode-go/beam-free`

Input, output, and cached-read must be Free/$0. Cached-write must be absent/dash or explicitly zero; any positive listed price blocks execution. The free-price snapshot is currently dated 2026-11-08 and is accepted for at most seven days. Refresh the official price snapshot and update `config/free_model_policy.json` when it becomes stale.

First use dry-run:

```powershell
python scripts/run_workers.py --queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\dispatch\\task_queue.json" --run-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs" --dry-run
```

Dry-run verifies the model pair and task-to-model assignments but does not make inference calls.

## 5. Dispatch workers

```powershell
python scripts/run_workers.py --queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\dispatch\\task_queue.json" --run-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs"
```

Default concurrency is four model calls (two papers × two workers). It is configurable using `--max-workers`, but is capped at four by default. On 429/rate-limit errors, the scheduler lowers concurrency for later batches. Retries retain the same explicitly pinned model.

The runner:
- pins both `model` and `small_model` to the worker's selected free model;
- uses a tool-disabled primary agent and no subagents;
- supplies a deterministic session title;
- closes child-process stdin to avoid waiting on an inherited terminal/pipe;
- captures raw JSONL and stderr;
- exports the session to recover complete assistant text and usage metadata when available;
- quarantines model mismatch and non-zero reported cost;
- validates worker JSON against the relevant JSON Schema;
- checkpoints each task and resumes completed tasks.

The runner does not delete OpenCode sessions automatically. This preserves the evidence needed for usage and failure audits.

## 6. Validate evidence

```powershell
python scripts/validate_evidence.py --manifest "E:\\Zinc_batters_datas\\runs\\Feature_create\\prepared\\source_manifest.json" --queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\dispatch\\task_queue.json" --run-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs"
```

This validates schema shape, task/paper identity, source IDs, non-empty evidence locators, quoted text/table values against locally extracted text, digitization artifact references, and the prohibition on exact numeric values for `visual_only` data.

This is a deterministic first-pass evidence validator. It does not prove that the scientific interpretation is correct, fully verify row/column semantics for all table layouts, or establish complete context association.

## 7. Ingest observations and result records

After the evidence report exists, ingest Worker A/B candidates into the persistent Registry:

```powershell
python scripts/reconcile_features.py `
  --manifest "E:\\Zinc_batters_datas\\runs\\Feature_create\\prepared\\source_manifest.json" `
  --queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\dispatch\\task_queue.json" `
  --registry "E:\\Zinc_batters_datas\\runs\\Feature_create\\global_feature_registry.json" `
  --records "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs\\result_records.jsonl" `
  --review-queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs\\feature_pair_review_queue.jsonl" `
  --run-report "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs\\registry_ingestion.json"
```

This step creates immutable raw Feature Observations and provisional canonical Feature candidates. Worker A result values are stored as per-value JSONL records. Worker B omissions and semantic distinctions are stored separately. Python does not automatically merge similar feature names.

## 8. Build global cross-paper Feature review tasks

```powershell
python scripts/build_reconciliation_queue.py `
  --manifest "E:\\Zinc_batters_datas\\runs\\Feature_create\\prepared\\source_manifest.json" `
  --registry "E:\\Zinc_batters_datas\\runs\\Feature_create\\global_feature_registry.json" `
  --review-queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs\\feature_pair_review_queue.jsonl" `
  --output "E:\\Zinc_batters_datas\\runs\\Feature_create\\reconciliation_dispatch"
```

Worker B reviews each candidate pair using source-backed definitions, units, methods, formulas, normalization bases, statistics, temporal scope, and evidence excerpts. It is the same Worker B model and is not a third model worker. Its first-pass per-paper audit remains independent from Worker A.

Run a no-inference preflight:

```powershell
python scripts/run_workers.py `
  --queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\reconciliation_dispatch\\reconciliation_task_queue.json" `
  --run-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\reconciliation_runs" `
  --dry-run
```

Then dispatch after the free-model price gate passes:

```powershell
python scripts/run_workers.py `
  --queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\reconciliation_dispatch\\reconciliation_task_queue.json" `
  --run-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\reconciliation_runs"
```

## 9. Apply semantic decisions conservatively

```powershell
python scripts/apply_feature_reconciliation.py `
  --registry "E:\\Zinc_batters_datas\\runs\\Feature_create\\global_feature_registry.json" `
  --review-queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs\\feature_pair_review_queue.jsonl" `
  --reconciliation-queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\reconciliation_dispatch\\reconciliation_task_queue.json" `
  --run-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\reconciliation_runs" `
  --report "E:\\Zinc_batters_datas\\runs\\Feature_create\\reconciliation_runs\\application_report.json"
```

Python applies a merge only when the Worker B recommendation is SAME_FEATURE and every explicit equivalence flag passes, both features have locally verified source evidence, definitions exist, units are conservatively compatible, existing IDs do not conflict, and the decision cites observations from both sides. Otherwise, the pair remains under review or is explicitly resolved as distinct.

After a merge, pairs that still reference the retired Feature ID are invalidated and regenerated against the surviving Feature ID. Raw observations and decision history are retained.

## 10. Import the existing Feature Schema

Use the existing authoritative feature dictionary as a mapping reference. For the current local corpus, the known source path is:

```powershell
python scripts/import_existing_schema.py `
  --input "E:\\Zinc_batters_datas\\preprocessed\\result_feature_list.txt" `
  --output "E:\\Zinc_batters_datas\\runs\\Feature_create\\existing_feature_schema.json"
```

The importer supports TSV, JSON, and common key/value text blocks. It preserves existing FEATURE_ID values and records the source file hash. If the local legacy file uses an unsupported layout, the importer stops and reports the parse issue; do not manually fabricate a schema.

## 11. Build Worker B existing-ID mapping tasks

```powershell
python scripts/build_schema_mapping_queue.py `
  --registry "E:\\Zinc_batters_datas\\runs\\Feature_create\\global_feature_registry.json" `
  --existing-schema "E:\\Zinc_batters_datas\\runs\\Feature_create\\existing_feature_schema.json" `
  --manifest "E:\\Zinc_batters_datas\\runs\\Feature_create\\prepared\\source_manifest.json" `
  --output "E:\\Zinc_batters_datas\\runs\\Feature_create\\schema_mapping_dispatch"
```

Worker B compares every confirmed Registry Feature with the imported legacy Feature candidates. The legacy schema is not used to filter discovery. The mapping decisions are separate from Feature-to-Feature merges.

Run the no-inference preflight:

```powershell
python scripts/run_workers.py `
  --queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\schema_mapping_dispatch\\existing_schema_mapping_queue.json" `
  --run-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\schema_mapping_runs" `
  --dry-run
```

Then dispatch using the same verified-free Worker B model:

```powershell
python scripts/run_workers.py `
  --queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\schema_mapping_dispatch\\existing_schema_mapping_queue.json" `
  --run-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\schema_mapping_runs"
```

Apply mapping decisions:

```powershell
python scripts/apply_existing_feature_mapping.py `
  --registry "E:\\Zinc_batters_datas\\runs\\Feature_create\\global_feature_registry.json" `
  --existing-schema "E:\\Zinc_batters_datas\\runs\\Feature_create\\existing_feature_schema.json" `
  --mapping-queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\schema_mapping_dispatch\\existing_schema_mapping_queue.json" `
  --run-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\schema_mapping_runs" `
  --report "E:\\Zinc_batters_datas\\runs\\Feature_create\\schema_mapping_runs\\mapping_report.json"
```

A mapping is applied only when Worker B selects one exact old FEATURE_ID, all seven compatibility checks are affirmative, definitions and source evidence exist, unit strings are conservatively compatible, and that old ID is not already assigned to another active Registry Feature. Otherwise the Registry Feature keeps its new Registry ID.

## 12. Export the two formal TXT files

Only after all active Features are confirmed and all candidate pairs are resolved:

```powershell
python scripts/export_txt.py `
  --registry "E:\\Zinc_batters_datas\\runs\\Feature_create\\global_feature_registry.json" `
  --records "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs\\result_records.jsonl" `
  --review-queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs\\feature_pair_review_queue.jsonl" `
  --existing-schema "E:\\Zinc_batters_datas\\runs\\Feature_create\\existing_feature_schema.json" `
  --output-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\final_output"
```

The exporter creates exactly two formal TXT files:
- `result_feature_records.txt`
- `result_feature_list.txt`

Both are UTF-8 TSV files with fixed headers; nested context and evidence fields are compact JSON cells. The exporter blocks if any active Feature remains provisional/under review, any result record lacks a valid Feature ID, or any candidate pair remains unresolved. It writes a separate internal audit JSON beside (not inside) the formal output directory. The formal output directory contains only the two TXT files.

## 13. Generate the final deterministic audit report

After mapping and reconciliation, calculate the corpus/source/extraction totals directly from local manifests and task outputs:

```powershell
python scripts/audit_records.py `
  --manifest "E:\\Zinc_batters_datas\\runs\\Feature_create\\prepared\\source_manifest.json" `
  --queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\dispatch\\task_queue.json" `
  --registry "E:\\Zinc_batters_datas\\runs\\Feature_create\\global_feature_registry.json" `
  --records "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs\\result_records.jsonl" `
  --review-queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs\\feature_pair_review_queue.jsonl" `
  --reconciliation-queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\reconciliation_dispatch\\reconciliation_task_queue.json" `
  --mapping-queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\schema_mapping_dispatch\\existing_schema_mapping_queue.json" `
  --run-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs" `
  --output "E:\\Zinc_batters_datas\\runs\\Feature_create\\final_audit.json"
```

The audit distinguishes sources fully reviewed by both independent Workers from partial/one-pass coverage, and reports main-article vs SI coverage separately. It also counts records by extraction/evidence status, explicit unresolved context items, active/retired Features, existing-ID mappings, pending semantic pairs, and model usage/cost metadata when exposed. Missing usage or cost metadata is labelled unknown, never silently converted to zero.

## 14. Inspect the run

Key files:
- `worker_runs/model_preflight.json`
- `worker_runs/run_state.json`
- `worker_runs/dispatch_summary.json`
- `worker_runs/evidence_validation.json`
- each task's `response.json`, `task_state.json`, `attempt_*/stdout.jsonl`, `stderr.txt`, and `session_export.json`

A missing usage/cost field is recorded as not exposed, not automatically treated as independently verified zero billing. Model pricing and actual route are separate audit layers.

## 15. Important limitations in v0.1

- Scanned PDFs are not OCR-processed.
- Upstream page, text-block, figure bounding-box, table ID, row/cell, and SI-reference coordinates are preserved in the attached evidence index; the validator checks exact structured-table cells when a machine-readable row/column locator is supplied.
- Figure panel/bounding-box interpretation is not automatically validated. The image and upstream coordinates support review, but workers must not claim automated figure digitization or exact values without digitization evidence.
- Figure digitization is not automated. Workers may flag visual-only series but must not fabricate precise values.
- Very large sources are split into page/text-segment and table-row scopes. An individual source unit that still exceeds a hard size/attachment/command limit is blocked, never truncated or discarded.
- The cross-paper Registry review/merge mechanism, existing FEATURE_ID mapping, and final TXT exporter are implemented but still require a manually inspected pilot and validation against the user's actual legacy schema file.
- The worker runner and preparation pipeline still require a local Windows smoke test against the installed OpenCode CLI version before production use.

Do not treat this version as production-ready until the local smoke test and a manually inspected pilot paper are successful.
