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
- `docs/architecture.md` — component boundaries and workflow.
- `docs/upstream_input_contract.md` — the formal chem-literature CSV/file-path boundary.
- `docs/token_policy.md` — zero-paid-cost policy and provider/model guards.
- `docs/opencode_free_model_execution.md` — OpenCode Go invocation, model pinning, and verification.
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

Run the [chem-literature `main` branch](https://github.com/Tuzhidingdong/chem-literature/tree/main) first. Its final `research_corpus_paired.csv` must contain:
- the curated paper rows and original `local_pdf_path` / `local_si_info` provenance;
- `preprocessing_path`, `preprocessing_status`, and `preprocessing_qc_report`;
- one `01_preprocessing/` package per paper, including `evidence_index.json`, `PREPROCESSING_QC_REPORT.md`, `main_analysis.md`, `si_analysis.md`, extracted tables, and image assets.

Feature_create consumes this CSV and the already-generated preprocessing packages. It does not parse PDFs, unpack SI archives, re-extract tables, or render PDF pages. It hashes the CSV-listed raw files only to confirm that they still match the upstream package; raw PDFs/SI are provenance and are never attached to Worker A/B. The CSV remains the complete paper-selection boundary. The preprocessing columns and package paths are now part of the merged chem-literature `main` workflow; a separate feature branch is no longer required.

Example paths below assume chem-literature was run from `E:\\Zinc_batters_datas\\chem-literature`.

Index the upstream preprocessing packages:

```powershell
python scripts/prepare_corpus.py `
  --paired-csv "E:\\Zinc_batters_datas\\chem-literature\\research_corpus_paired.csv" `
  --corpus-root "E:\\Zinc_batters_datas\\chem-literature" `
  --output "E:\\Zinc_batters_datas\\runs\\Feature_create\\prepared"
```

This creates an internal `source_manifest.json`, an `extracted_text/` staging area containing exact copies of upstream-parsed text/table content, and `upstream_evidence_indexes/` containing exact copies of each `evidence_index.json`. No PDF/SI parsing is performed by Feature_create. The downstream task builder then splits long parsed sources at upstream page boundaries and large tables at original row boundaries. Every segment receives a stable `scope_id`, while evidence continues to cite the original `source_id`, page, table ID, and 1-based row/column. Worker coverage is reported per scope and aggregated only after every chunk is complete.

Build the isolated Worker A/B queue:

```powershell
python scripts/build_task_queue.py `
  --manifest "E:\\Zinc_batters_datas\\runs\\Feature_create\\prepared\\source_manifest.json" `
  --output "E:\\Zinc_batters_datas\\runs\\Feature_create\\dispatch"
```

First perform model-price preflight and command planning without inference:

```powershell
python scripts/run_workers.py `
  --queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\dispatch\\task_queue.json" `
  --run-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs" `
  --dry-run
```

Only after the dry run passes, dispatch the two free workers:

```powershell
python scripts/run_workers.py `
  --queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\dispatch\\task_queue.json" `
  --run-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs"
```

Validate result-value evidence:

```powershell
python scripts/validate_evidence.py `
  --manifest "E:\\Zinc_batters_datas\\runs\\Feature_create\\prepared\\source_manifest.json" `
  --queue "E:\\Zinc_batters_datas\\runs\\Feature_create\\dispatch\\task_queue.json" `
  --run-dir "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs"
```

The first run should be performed on a small paper subset to validate the local OpenCode CLI version, model availability, task bundle size, and JSON event/export format. Do not start a large corpus run until the local smoke test succeeds.

More detail: [docs/runbook.md](docs/runbook.md).
