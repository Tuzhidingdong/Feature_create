# Integrity finding review and remediation workflow

## Purpose and safety boundary

The global integrity audit remains deterministic and read-only. This workflow turns its findings into a human-facing remediation queue with the related Feature, Observation, Result Record, source evidence references, and decision-log history attached.

It deliberately does **not**:
- edit or delete Observations or Result Records;
- rewrite canonical Feature IDs or redirects;
- append events to the Registry `decision_log`;
- claim that a review decision has repaired a finding;
- invoke a model or dispatch a Worker.

Human triage decisions are stored in a separate append-only `integrity_review_decision_ledger.jsonl` ledger. Actual scientific changes must use the existing evidence/reconciliation workflows. A fresh integrity audit is the only way to confirm that a finding is no longer present.

## 1. Build the queue

Run from the repository root in PowerShell. The builder runs the audit itself, so the queue and its report always describe the same Registry/Records/Manifest snapshot.

```
$run = "E:\\Zinc_batters_datas\\runs\\Feature_create"
$review = Join-Path $run "integrity_review"

python scripts/build_integrity_review_queue.py`
  --registry (Join-Path $run "global_feature_registry.json")`
  --records (Join-Path $run "worker_runs\\result_records.jsonl")`
  --manifest (Join-Path $run "prepared\\source_manifest.json")`
  --review-ledger (Join-Path $review "integrity_review_decision_ledger.jsonl")`
  --output-dir $review
```

Outputs:

- `global_integrity_audit.json`: the exact audit used to build this queue.
- `integrity_review_queue.json`: findings enriched with connected objects, evidence locators, source-manifest context, and relevant decision events.
- `integrity_review_decisions.template.jsonl`: immutable finding identity/snapshot fields plus blank human decision fields.
- `integrity_review_bundle.json`: a commit manifest containing SHA-256 hashes for the three artifacts above. The apply command verifies this manifest before accepting decisions.

The builder acquires the same `global_feature_registry.json.lock` used by Registry/Record mutation stages while it reads Registry and Records; it also locks the optional review ledger. All artifacts are staged and flushed before replacement, and the bundle manifest is published last. If publication fails part-way through, the old or missing bundle will not match the mixed artifact files, so apply fails closed.

If any queue, template, edited-decision, bundle, or prior apply-report file already exists, rebuilding is refused. After deciding to start a new review round, use both `--force --archive-existing`; the previous review files are moved into a timestamped archive directory. The append-only decision ledger is deliberately preserved outside that archive.

Use `--strict-observation-records` only when every eligible result-feature Observation is expected to have a Result Record. If the audit was built in strict mode, that mode is captured in the queue snapshot.

## 2. Prepare and edit human decisions

Copy the generated template; do not edit the original:

```
Copy-Item`
  (Join-Path $review "integrity_review_decisions.template.jsonl")`
  (Join-Path $review "integrity_review_decisions.jsonl")
```

Edit the copied JSONL file. Keep **every row, its order, and all immutable fields** unchanged. Only fill the decision fields:

| Field | Allowed values / purpose |
|---|---|
| `review_decision` | `repair_required`, `accepted_warning`, or `defer`; leave empty when not reviewed |
| `action` | `feature_reconciliation`, `record_review`, `evidence_revalidation`, `decision_history_review`, `manual_review`, or `none` |
| `rationale` | Specific human explanation, at least 12 characters |
| `review_evidence_refs` | Optional references with a valid `source_id` and non-empty `locator` |
| `follow_up_reference` | Optional issue, task, or local review reference |

Rules:

- A hard error can only be marked `repair_required` or `defer`; it cannot be accepted as a warning.
- `repair_required` must select a concrete action. It means work is required, not that the issue is fixed.
- `accepted_warning` is allowed only for warnings and requires explicit review evidence. It records a human rationale; it does not suppress the warning or bypass the export gate.
- `defer` records why the item remains open.
- Keep the quoted source text and exact locator grounded in the prepared source package. Run the existing evidence validator before using a new quote as proof.

## 3. Validate and append the review ledger

```
python scripts/apply_integrity_review_decisions.py`
  --queue (Join-Path $review "integrity_review_queue.json")`
  --template (Join-Path $review "integrity_review_decisions.template.jsonl")`
  --decisions (Join-Path $review "integrity_review_decisions.jsonl")`
  --registry (Join-Path $run "global_feature_registry.json")`
  --records (Join-Path $run "worker_runs\\result_records.jsonl")`
  --manifest (Join-Path $run "prepared\\source_manifest.json")`
  --bundle (Join-Path $review "integrity_review_bundle.json")`
  --ledger (Join-Path $review "integrity_review_decision_ledger.jsonl")`
  --report (Join-Path $review "integrity_review_apply_report.json")
```

Apply validates the bundle manifest and every prepared artifact hash, the queue and finding fingerprints, the complete template row inventory, and the JSON Schema for each submitted decision. It acquires the canonical Registry lock first and the review-ledger lock second; while both are held it re-checks Registry/Records/Manifest hashes and the edited decision-file hash immediately before appending. This closes the snapshot-check/append race with the existing Registry/Record mutation stages. Repeated application of the same decision is idempotent. A conflicting second decision for the same finding and audit snapshot is rejected.

The apply step appends only to the separate review ledger. The Registry, Records, source files, and Registry `decision_log` remain untouched.

## 4. Perform the actual correction through existing workflows

Use the route attached to each queue item:

- `feature_reconciliation`: review canonical identity and Observation ownership with `reconcile_features.py` and the guarded `apply_feature_reconciliation.py`.
- `record_review`: inspect source-backed Record extraction and links with `audit_records.py` / the existing Worker A run.
- `evidence_revalidation`: re-check source package, locator, and quote/cell evidence with `validate_evidence.py`.
- `decision_history_review`: inspect the Registry schema and existing transition history; never fabricate an old event or delete a prior decision.
- `manual_review`: use a narrowly scoped human correction when no existing deterministic action covers the finding.

Do not patch raw Observation fields or Result Record values just to clear a finding. When a correction changes canonical links or state, preserve the old decision and append a new, evidence-backed event using the existing Registry workflow.

## 5. Re-audit and confirm clearance

After making corrections, rebuild the queue and pass the review ledger:

```
python scripts/build_integrity_review_queue.py`
  --registry (Join-Path $run "global_feature_registry.json")`
  --records (Join-Path $run "worker_runs\\result_records.jsonl")`
  --manifest (Join-Path $run "prepared\\source_manifest.json")`
  --review-ledger (Join-Path $review "integrity_review_decision_ledger.jsonl")`
  --output-dir $review`
  --force --archive-existing
```

Findings still present are marked `repair_pending`, `deferred`, `accepted_warning`, or `changed_since_review`. A previously reviewed finding absent from the current audit appears under `cleared_since_previous_review` with `not_reported_by_current_audit`. This confirms only that the latest audit no longer reports that finding; it does not attribute causality to a particular edit.

The `--force --archive-existing` combination archives the prior queue, audit, bundle, template, edited decisions, and apply report before publishing the new round. The separate ledger remains append-only and carries the review history across queue rebuilds.

## 6. Export gate

The queue is a review aid, not a waiver mechanism. The exporter continues to run the global integrity engine and blocks formal TXT output when hard integrity errors remain. Re-run the audit and evidence checks after all corrections; do not change severity or accept a warning to make export pass.
