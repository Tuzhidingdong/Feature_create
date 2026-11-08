# Feature_create Local Runbook (v0.1)

## 1. Preconditions

- Windows 10/11 or a supported Python environment.
- Python 3.10+.
- OpenCode CLI installed and authenticated for OpenCode Go.
- Current OpenCode Go plan/account allows access to the selected limited-time free models in the user's region.
- A completed [chem-literature](https://github.com/Tuzhidingdong/chem-literature) run.
- The resulting `research_corpus_paired.csv` and the PDF/SI files referenced by its `local_pdf_path` and `local_si_info` columns.
- Enough local disk space for extracted text, expanded SI ZIP members, visual pages, task bundles, raw worker outputs, and session exports.

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Check the current CLI version and model command syntax:

```powershell
opencode --version
opencode models --help
opencode run --help
opencode export --help
```

The exact flags are checked against the installed CLI version by the user before the first real dispatch. The workflow uses current documented `opencode run --model`, `--agent`, `--file`, `--format json`, `--title`, and `--dir` options.

## 2. Prepare the chem-literature paired corpus

Run `chem-literature` first. Its final curated artifact is:

`research_corpus_paired.csv`

The CSV contains the selected original-research papers and the local file paths in:
- `local_pdf_path` — full-text PDF path;
- `local_si_info` — supporting-information file path (currently one path per row).

The paired CSV is authoritative. Feature_create must not recursively scan `papers_pdf/` or `papers_si/`, add unlisted files, re-filter articles, or attempt to repair a missing pair by finding a similarly named file.

The current chem-literature workflow writes its CSV and `papers_pdf/` / `papers_si/` paths relative to the directory from which `run_workflow.py` is executed. For example, when it is run from:

`E:\\Zinc_batters_datas\\chem-literature`

use:

```powershell
python scripts/prepare_corpus.py `
  --paired-csv "E:\\Zinc_batters_datas\\chem-literature\\research_corpus_paired.csv" `
  --corpus-root "E:\\Zinc_batters_datas\\chem-literature" `
  --output "E:\\Zinc_batters_datas\\runs\\Feature_create\\prepared"
```

`--corpus-root` is the base directory against which relative CSV paths are resolved. It defaults to the directory containing the CSV, but should be explicit when the upstream workflow was run from another working directory.

The preparation step:
- reads only rows present in `research_corpus_paired.csv`;
- verifies the physical existence of every referenced main PDF and SI file;
- records missing files and invalid PDF magic as blocked pair-integrity items;
- extracts text from main PDFs and supported SI files;
- safely expands SI ZIP archives into the Feature_create run directory, without modifying the upstream archive;
- parses PDF, DOCX, XLSX, CSV/TSV, HTML, XML/TEI, and plain-text SI files;
- renders PDF pages with Figure/Table/Scheme captions for visual inspection;
- records the upstream CSV path and SHA-256 in the derived internal manifest.

Outputs:
- `source_manifest.json` — internal manifest derived only from the curated CSV and its referenced files;
- `extracted_text/` — extracted text for each listed source;
- `si_archives/` — safe copies of supported members from listed SI ZIP archives;
- `visual_assets/` — rendered figure/table pages from listed PDFs;
- `prepare_summary.json` — row, paper, source, and file-pair integrity counts.

A separate `research_corpus_paired_manifest.json` is not required; the current upstream workflow generates the CSV. The internal Feature_create manifest is derived downstream from that CSV.

A listed file that is missing or invalid remains visible in the manifest and blocks that paper's Worker A/B tasks. It is never replaced by an unlisted file. A scanned PDF with no extractable text remains recorded as partially/unparsed evidence; this step does not perform OCR.

## 3. Build isolated Worker tasks

```powershell
python scripts/build_task_queue.py --manifest "E:\\Zinc_batters_datas\\runs\\Feature_create\\prepared\\source_manifest.json" --output "E:\\Zinc_batters_datas\\runs\\Feature_create\\dispatch"
```

One independent Worker A and Worker B task is created per paper. The first-pass outputs are isolated. Each task has:
- stable task ID;
- exact pinned model reference;
- worker prompt;
- consolidated source text bundle;
- original PDF and image attachments when present;
- source ID and file provenance.

If the bundle exceeds the configured maximum, it is marked blocked rather than silently truncated. A later task splitter will allow oversized papers to be processed in bounded chunks without losing source coverage.

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
- PDF page/paragraph/table locators are not yet enriched beyond page markers and model-provided locators.
- Table quote verification is text-occurrence checking, not yet cell-coordinate verification against structured table data.
- Figure digitization is not automated. Workers may flag visual-only series but must not fabricate precise values.
- Oversized paper bundles are blocked rather than chunked automatically.
- The cross-paper Registry review/merge mechanism, existing FEATURE_ID mapping, and final TXT exporter are implemented but still require a manually inspected pilot and validation against the user's actual legacy schema file.
- The worker runner and preparation pipeline still require a local Windows smoke test against the installed OpenCode CLI version before production use.

Do not treat this version as production-ready until the local smoke test and a manually inspected pilot paper are successful.
