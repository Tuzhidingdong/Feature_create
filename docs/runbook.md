# Feature_create Local Runbook (v0.1)

## 1. Preconditions

- Windows 10/11 or a supported Python environment.
- Python 3.10+.
- OpenCode CLI installed and authenticated for OpenCode Go.
- Current OpenCode Go plan/account allows access to the selected limited-time free models in the user's region.
- A local corpus folder containing articles and SI.
- Enough local disk space for extracted text, task bundles, raw worker outputs, and session exports.

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Check the current CLI version and model command syntax:

```powershell
opencode --version
opencode models --help
opencode run --help
opencode session export --help
```

The exact flags are checked against the installed CLI version by the user before the first real dispatch. The workflow uses current documented `opencode run --model`, `--agent`, `--file`, `--format json`, `--title`, and `--dir` options.

## 2. Prepare a corpus

Example corpus root: `E:\\Zinc_batters_datas`

```powershell
python scripts/prepare_corpus.py --input "E:\\Zinc_batters_datas" --output "E:\\Zinc_batters_datas\\runs\\Feature_create\\prepared"
```

Outputs:
- `source_manifest.json` — all detected source files and paper groups.
- `extracted_text/` — local extracted text for PDFs, Markdown, XML/TEI, HTML, CSV/TSV, and XLSX.
- `prepare_summary.json` — counts and parse failures.

The preparation step does not call any AI model. It does not OCR scanned PDFs. A PDF for which PyMuPDF extracts no usable text remains recorded as a source with an extraction status; the workflow must not report it as fully reviewed.

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

This is a deterministic first-pass evidence validator. It does not prove that the scientific interpretation is correct, fully verify row/column semantics for all table layouts, or establish complete context association. Those checks are expanded in later reconciliation work.

## 7. Inspect the run

Key files:
- `worker_runs/model_preflight.json`
- `worker_runs/run_state.json`
- `worker_runs/dispatch_summary.json`
- `worker_runs/evidence_validation.json`
- each task's `response.json`, `task_state.json`, `attempt_*/stdout.jsonl`, `stderr.txt`, and `session_export.json`

A missing usage/cost field is recorded as not exposed, not automatically treated as independently verified zero billing. Model pricing and actual route are separate audit layers.

## 8. Important limitations in v0.1

- Scanned PDFs are not OCR-processed.
- PDF page/paragraph/table locators are not yet enriched beyond page markers and model-provided locators.
- Table quote verification is text-occurrence checking, not yet cell-coordinate verification against structured table data.
- Figure digitization is not automated. Workers may flag visual-only series but must not fabricate precise values.
- Oversized paper bundles are blocked rather than chunked automatically.
- Cross-paper Registry reconciliation and final TXT export have not yet been implemented.
- The worker runner and preparation pipeline still require a local Windows smoke test against the installed OpenCode CLI version before production use.

Do not treat this version as production-ready until the local smoke test and a manually inspected pilot paper are successful.
