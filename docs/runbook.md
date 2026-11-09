# Feature_create Local Runbook (v0.1)

## 1. Preconditions

- Windows 10/11 or a supported Python environment.
- Python 3.10+.
- OpenCode CLI installed and authenticated for OpenCode Go.
- Current OpenCode Go plan/account allows access to the selected limited-time free models in the user's region.
- A completed run of the merged [chem-literature `main` workflow](https://github.com/Tuzhidingdong/chem-literature/tree/main), with Stage 5 preprocessing enabled (the default).
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

Run [chem-literature's preprocessing stage](https://github.com/Tuzhidingdong/chem-literature/tree/main) first. The final `research_corpus_paired.csv` must include:
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

One independent Worker A task and one independent Worker B task are created for every deterministic chunk of an eligible paper. Each task has:
- stable task ID and exact pinned model reference;
- worker prompt and only the assigned upstream-parsed text/table evidence units;
- a per-chunk `chunk_evidence_index.json` locator slice, a compact paper-wide `paper_chunk_index.json` (including adjacent text-segment links and same-original-row table links across chunks), and the relevant QC report;
- pre-extracted figure/table image assets when available;
- stable source IDs, one `scope_id` per `(source_id, unit_id)` assignment, `unit_id`, `chunk_id`, and original page/table/row/column/figure/SI locators;
- raw PDF/SI hash inventory for provenance only.

The task builder must never attach raw PDF, ZIP, DOCX, or XLSX source files. It attaches only pre-extracted visual assets and bounded chunk/paper index plus QC files. The unchanged complete upstream evidence index remains in the prepared run for provenance. If a source package is partial, the bundle carries its warnings and unparsed materials; workers must mark the corresponding coverage incomplete.

Large papers are split automatically before dispatch. The bundle includes a coverage checklist with one row per source-unit pair. Worker A and Worker B must return one coverage item per `scope_id`; repeated `source_id` values are deliberately not collapsed. The audit aggregates all scope statuses before a source can be labelled fully reviewed. Text chunks preserve exact source character offsets and upstream page numbers; table chunks preserve original 1-based row/column indexes, including cell fragments for a single oversized row; related pre-extracted images are attached once. Context-only snippets and repeated table headers do not count as assigned coverage. The planner reduces the chunk budget iteratively; any irreducible atomic bundle above the hard limit is explicitly blocked rather than silently truncated. The paper-wide navigation index preserves the exact offset adjacency of text segments and connects every split table-row/cell unit that shares the same original `table_id + row index`; these links allow targeted cross-chunk review without asserting unsupported sample/condition/result relationships.

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

Default concurrency is four model calls (four chunk tasks across the corpus; each chunk has separate A/B tasks). It is configurable using `--max-workers`, but is capped at four by default. On 429/rate-limit errors, the scheduler lowers concurrency for later batches. Retries retain the same explicitly pinned model.

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

This validates schema shape, task/paper identity, source IDs, the task's exact `chunk_id` and assigned `unit_id`, non-empty original locators, quoted text within the exact assigned text range, table values against original row/column/cell or cell-fragment offsets, digitization artifact references, and the prohibition on exact numeric values for `visual_only` data. A reference to another chunk or an unassigned row/cell is rejected.

This is a deterministic first-pass evidence validator. It proves source/unit assignment and quoted text/cell provenance, but it does not prove that the scientific interpretation is correct or automatically resolve every long-range sample/condition/result relationship that requires content from distant chunks. Such cross-chunk links must remain explicitly unresolved until supported by the relevant evidence.

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

## 8. Targeted cross-chunk context review

Run this stage after Registry ingestion. It is selective: only Worker A missing_context issues with an explicit cross_chunk_target and a linked result record can create a second-pass task. Items without a deterministic remote unit, or whose target is not connected by the declared navigation relation, remain unresolved and are reported as skipped/blocked.

Build the context-review queue:

~~~powershell
python scripts/build_context_review_queue.py `
  --manifest "E:\\Zinc_batters_datas\\runs\\Feature_create\\prepared\\source_manifest.json" `
  --queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\dispatch\\task_queue.json" `
  --unresolved "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs\\unresolved_extraction_items.jsonl" `
  --records "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs\\result_records.jsonl" `
  --output "E:\\Zinc_batters_datas\\runs\\Feature_create\\context_review_dispatch" `
  --max-context-chars 60000 `
  --max-attachments 8
~~~

The builder retrieves only:
- the exact current evidence unit(s) referenced by the result's unresolved item;
- the specific remote target unit(s) nominated by Worker A and verified against the paper navigation index;
- the existing result record's identifying fields and already-extracted context, for reference only.

It does not attach the entire paper, read other workers' outputs, or parse raw PDFs/SI. The default cap is eight target units per review. The existing Worker B model from config/execution_policy.json is reused; no third model is introduced. Already reviewed/resolved unresolved items are skipped by default. For an inconclusive item that merits another pass, add --retry-inconclusive to the queue-builder command; association_confirmed and no_association items remain closed. Each explicit retry increments review_attempt and receives a new task ID, even when the evidence is unchanged, so a completed prior session cannot be silently reused. A task ID also fingerprints the exact question, current/target unit content and locators, visual-asset hashes, model ID, and dedicated prompt hash; changed inputs create a new task as well. The task JSON also records the input, prompt, bundle, attachment, and evidence-manifest hashes.

Run the free-model preflight without inference:

~~~powershell
python scripts/run_workers.py `
  --queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\context_review_dispatch\\context_review_task_queue.json" `
  --run-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\context_review_runs" `
  --dry-run
~~~

Dispatch only after the regular current free-model price gate passes:

~~~powershell
python scripts/run_workers.py `
  --queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\context_review_dispatch\\context_review_task_queue.json" `
  --run-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\context_review_runs"
~~~

Validate both CURRENT and TARGET citations against their assigned unit, original source offsets/table cells, and exact quotes:

~~~powershell
python scripts/validate_evidence.py `
  --manifest "E:\\Zinc_batters_datas\\runs\\Feature_create\\prepared\\source_manifest.json" `
  --queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\context_review_dispatch\\context_review_task_queue.json" `
  --run-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\context_review_runs"
~~~

Apply only the context decision after evidence validation passes:

~~~powershell
python scripts/apply_context_reviews.py `
  --queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\context_review_dispatch\\context_review_task_queue.json" `
  --run-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\context_review_runs" `
  --registry "E:\\Zinc_batters_datas\\runs\\Feature_create\\global_feature_registry.json" `
  --records "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs\\result_records.jsonl" `
  --unresolved "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs\\unresolved_extraction_items.jsonl" `
  --report "E:\\Zinc_batters_datas\\runs\\Feature_create\\context_review_runs\\application_report.json"
~~~

A confirmed fact is appended only to its matching context category (sample_identity, condition_facts, time_cycle_facts, protocol_facts, or other_context_facts). When the same fact already exists from Worker A, the applier enriches that single fact with the additional verified evidence references rather than duplicating it. The applier protects the original result value, numeric value, Feature/Observation IDs, raw feature name, unit, result type, method, source ID, first-pass evidence references, and other result metadata. It also checks that the current response.json SHA-256 still matches the exact response file hash recorded by evidence_validation.json. The validation report additionally binds the source manifest, each assigned preprocessed text snapshot, context evidence manifest, exact bundle, Worker B prompt, and all visual attachments by SHA-256; the applier rechecks every one of them and the attachment inventory before changing records. A changed or unregistered input is not applied. The applier takes the same Registry lock as reconcile_features.py and reloads both JSONL files only after acquiring it, preventing a stale read from overwriting concurrent ingestion. Each queued task also snapshots the linked Result Record and unresolved item; if either was changed by another review or manual edit after queue construction, the old decision is rejected and the queue must be rebuilt against the new state. no_association and conflicting_evidence require verified text/table evidence on both sides; no_association specifically requires positive counter-evidence (for example, an explicit mapping to another sample/series or an explicit scope exclusion), not merely the absence of a matching statement. If either side cannot be verified or the target simply omits the necessary link, Worker B must return insufficient_evidence. no_association closes that specific question without adding a fact. insufficient_evidence and conflicting_evidence remain open for human review. Every decision and its two-sided citations are retained in the unresolved item and the linked result record's context-review history.

## 9. Build global cross-paper Feature review tasks

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

## 10. Apply semantic decisions conservatively

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

## 11. Import the existing Feature Schema

Use the existing authoritative feature dictionary as a mapping reference. For the current local corpus, the known source path is:

```powershell
python scripts/import_existing_schema.py `
  --input "E:\\Zinc_batters_datas\\preprocessed\\result_feature_list.txt" `
  --output "E:\\Zinc_batters_datas\\runs\\Feature_create\\existing_feature_schema.json"
```

The importer supports TSV, JSON, and common key/value text blocks. It preserves existing FEATURE_ID values and records the source file hash. If the local legacy file uses an unsupported layout, the importer stops and reports the parse issue; do not manually fabricate a schema.

## 12. Build Worker B existing-ID mapping tasks

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

## 13. Human adjudication for unresolved context associations

Use the deterministic human-review path for Worker B results that remain `insufficient_evidence`/`conflicting_evidence`, items without a usable remote target, or other unresolved sample/condition links. Do not edit the records or unresolved JSONL files by hand.

First build a review template. This reads the existing prepared evidence package and creates a separate template containing candidate result records, full snapshot hashes, and the SHA-256 inventory of parsed source text:

```powershell
python scripts/adjudicate_unresolved.py `
  --prepare `
  --manifest "E:\\Zinc_batters_datas\\runs\\Feature_create\\prepared\\source_manifest.json" `
  --registry "E:\\Zinc_batters_datas\\runs\\Feature_create\\global_feature_registry.json" `
  --records "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs\\result_records.jsonl" `
  --unresolved "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs\\unresolved_extraction_items.jsonl" `
  --output-template "E:\\Zinc_batters_datas\\runs\\Feature_create\\manual_context_template.jsonl" `
  --report "E:\\Zinc_batters_datas\\runs\\Feature_create\\manual_context_prepare_report.json"
```

Keep `manual_context_template.jsonl` unchanged and copy it to a separate file for editing:

```powershell
Copy-Item `
  "E:\\Zinc_batters_datas\\runs\\Feature_create\\manual_context_template.jsonl" `
  "E:\\Zinc_batters_datas\\runs\\Feature_create\\manual_context_decisions.jsonl"
```

Edit only the decision fields in the copy:
- Select exactly one `record_id` from `candidate_records`. If one Observation has multiple result-value records, the template deliberately leaves `record_id` blank and requires an explicit human choice.
- Set `decision` to `association_confirmed`, `no_association`, or `remain_unresolved`; provide a specific rationale of at least 12 characters.
- For `association_confirmed`, add one or more `context_facts`. Each fact needs a context role, raw label/value, and its own exact `current` + `target` evidence references.
- For `no_association`, leave `context_facts` empty and choose a positive `counterevidence_basis`: `explicit_other_sample_mapping`, `explicit_scope_exclusion`, `explicit_contradictory_identifier`, or `other_positive_counterevidence`. This cannot be justified by a missing mention alone.
- For `remain_unresolved`, leave `context_facts` empty. The issue remains open and continues to block normal formal export.

A text evidence reference must include `evidence_side` (`current` or `target`), `source_id`, a page/figure/table locator, the exact `quote_or_cell_value`, and `evidence_status: verified_text`. For a structured table cell use `verified_table_cell` and original row/column indexes. The tool rechecks every quote/cell against the preprocessed source manifest and extracted text; merely copying the Worker B's status is not sufficient. It does not parse PDFs/SI or accept unverified/visual-only citations as proof.

Apply the edited decisions while retaining the unchanged template:

```powershell
python scripts/adjudicate_unresolved.py `
  --apply `
  --manifest "E:\\Zinc_batters_datas\\runs\\Feature_create\\prepared\\source_manifest.json" `
  --registry "E:\\Zinc_batters_datas\\runs\\Feature_create\\global_feature_registry.json" `
  --records "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs\\result_records.jsonl" `
  --unresolved "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs\\unresolved_extraction_items.jsonl" `
  --template "E:\\Zinc_batters_datas\\runs\\Feature_create\\manual_context_template.jsonl" `
  --decisions "E:\\Zinc_batters_datas\\runs\\Feature_create\\manual_context_decisions.jsonl" `
  --report "E:\\Zinc_batters_datas\\runs\\Feature_create\\manual_context_application_report.json"
```

The applier uses the same Registry lock as ingestion/context application. It checks the unchanged template, selected record and unresolved snapshots, the source-manifest hash, every extracted text hash for the paper, and every cited quote/table cell. A stale or invalid decision blocks the entire batch without changing either JSONL file. A confirmed fact is merged into its existing context entry when identical; a no-association decision closes only that question; a retained-unresolved decision appends human history but preserves its open resolution state. The original result value, unit, Feature/Observation IDs and primary evidence are protected. Human actions are recorded in both the unresolved item's `manual_review_history` and the result record's `context_review_history`.

The completed-decision contract is `contracts/manual-context-adjudication.schema.json`. Rebuild the template whenever any source/record/unresolved state changes; never edit its snapshot fields to force a stale decision through.

## 14. Export the two formal TXT files


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

Both are UTF-8 TSV files with fixed headers; nested context and evidence fields are compact JSON cells. The records file includes `CONTEXT_ASSOCIATION_STATUS` and `OPEN_CONTEXT_UNRESOLVED_ITEMS` for every result record. A record is `not_required` when it has no linked missing-context issue, `resolved` only when every linked issue is explicitly `association_confirmed` or `no_association`, and otherwise carries a pending/inconclusive/mixed/invalid status plus the open unresolved IDs and their current states.

By default, export is blocked while any linked context association remains pending, insufficient, or conflicting. Unknown resolution states always block. After the context review has been completed, run the command above normally. Only when an intentionally provisional package is needed, add `--allow-unresolved-context`; unresolved items remain visible in the record rows, the separate audit JSON is marked `exported_with_unresolved_context`, and the package must not be treated as a final resolved deliverable. This override does not bypass unknown states, Feature reconciliation, or other export checks.

The exporter also blocks if any active Feature remains provisional/under review, any result record lacks a valid Feature ID, or any candidate pair remains unresolved. It writes a separate internal audit JSON beside (not inside) the formal output directory. The formal output directory contains only the two TXT files.

## 15. Generate the final deterministic audit report

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
  --context-review-queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\context_review_dispatch\\context_review_task_queue.json" `
  --run-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs" `
  --output "E:\\Zinc_batters_datas\\runs\\Feature_create\\final_audit.json"
```

The audit distinguishes sources fully reviewed by both independent Workers from partial/one-pass coverage, and reports main-article vs SI coverage separately. It also counts records by extraction/evidence status, explicit unresolved context items, active/retired Features, existing-ID mappings, pending semantic pairs, Worker B context-review decisions, human context-adjudication decisions/facts/retained-open questions, and model usage/cost metadata when exposed. Missing usage or cost metadata is labelled unknown, never silently converted to zero.

## 16. Inspect the run

Key files:
- `worker_runs/model_preflight.json`
- `worker_runs/run_state.json`
- `worker_runs/dispatch_summary.json`
- `worker_runs/evidence_validation.json`
- each task's `response.json`, `task_state.json`, `attempt_*/stdout.jsonl`, `stderr.txt`, and `session_export.json`

A missing usage/cost field is recorded as not exposed, not automatically treated as independently verified zero billing. Model pricing and actual route are separate audit layers.

## 17. Important limitations in v0.1

## 18. Global bidirectional Feature / Record integrity audit

Run this after reconciliation and human adjudication, and before final export:

```powershell
python scripts/audit_global_integrity.py `
  --registry "E:\\Zinc_batters_datas\\runs\\Feature_create\\global_feature_registry.json" `
  --records "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs\\result_records.jsonl" `
  --manifest "E:\\Zinc_batters_datas\\runs\\Feature_create\\prepared\\source_manifest.json" `
  --report "E:\\Zinc_batters_datas\\runs\\Feature_create\\global_integrity_audit.json"
```

The audit is read-only and must not repair the Registry. It verifies every Record's Observation and Feature links; Feature ↔ Observation backlinks; Observation and Record source evidence with non-empty locators; source-ID existence when a manifest is supplied; and decision chains for excluded Observations, merged Features, split-pending Features, and merge/split/reassignment events.

Every eligible Observation is checked for at least one Result Record. Such omissions are warnings by default because a reported visual-only variable may have no exportable value. Use `--strict-observation-records` when all eligible observations must materialize as records. Exit code 2 indicates hard integrity errors. Do not bypass them or modify raw identity fields to silence the report. Correct the originating reconciliation/adjudication decision, preserve the old event, append the correcting event, and rerun the audit.

The final `export_txt.py` command invokes the same integrity engine. It blocks both TXT files for hard errors and records the integrity summary/findings in the separate internal export audit JSON. Pass `--source-manifest` to the exporter for source existence checks. See [global_integrity_audit.md](global_integrity_audit.md) for issue-code semantics.


- Scanned PDFs are not OCR-processed.
- Upstream page, text-block, figure bounding-box, table ID, row/cell, and SI-reference coordinates are preserved in the attached evidence index; the validator checks exact structured-table cells when a machine-readable row/column locator is supplied.
- Figure panel/bounding-box interpretation is not automatically validated. The image and upstream coordinates support review, but workers must not claim automated figure digitization or exact values without digitization evidence.
- Figure digitization is not automated. Workers may flag visual-only series but must not fabricate precise values.
- Scanned pages remain un-OCRed unless chem-literature has already supplied OCR-derived text and QC evidence; Feature_create does not perform OCR.
- Deterministic chunking preserves text ranges and table row/cell locators, but does not automatically infer a long-range context relationship between separate chunks. Unresolved cross-chunk associations are retained, not guessed.
- The cross-paper Registry review/merge mechanism, existing FEATURE_ID mapping, and final TXT exporter are implemented but still require a manually inspected pilot and validation against the user's actual legacy schema file.
- The worker runner and preparation pipeline still require a local Windows smoke test against the installed OpenCode CLI version before production use.

Do not treat this version as production-ready until the local smoke test and a manually inspected pilot paper are successful.


## 19. Integrity finding review and remediation

After the global audit, build the human review queue with `scripts/build_integrity_review_queue.py`. It enriches each issue with related Features, Observations, Result Records, source references, and decision history. Copy and edit its JSONL decision template, then validate and append the human triage to a separate `integrity_review_decision_ledger.jsonl` ledger with `scripts/apply_integrity_review_decisions.py`.

The apply step is snapshot-bound: it refuses decisions if Registry, Records, or Manifest bytes have changed since queue preparation. It does not edit scientific data or the Registry decision log. `repair_required` is not a repair; complete the correction through the existing reconciliation / evidence workflow, then rebuild the queue with `--review-ledger` to verify that the finding is absent. Hard errors cannot be reclassified as accepted warnings, and accepted warnings never bypass the final export gate. See [integrity_review_workflow.md](integrity_review_workflow.md) for the full PowerShell procedure.
