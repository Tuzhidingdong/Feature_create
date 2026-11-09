# Global Feature / Observation / Result Record Integrity Audit

## Purpose

This is a deterministic, read-only, corpus-wide audit of the persisted Registry and `result_records.jsonl`. It complements (and does not replace) model review, human adjudication, evidence validation, and the final TXT export gate.

The audit checks both directions of every identity relationship:

1. Result Record → Observation → canonical Feature.
2. Canonical Feature → Observation(s) → source evidence.
3. Observation → Result Record(s), including source and Feature consistency.
4. Decision history → the objects and evidence that justify exclusion, merge, split, and reassignment.

It never repairs, reassigns, merges, splits, or deletes objects. Findings must be resolved through the existing reconciliation/adjudication workflow, preserving append-only decision history.

## Run

From the repository root (PowerShell):

```powershell
python scripts/audit_global_integrity.py `
  --registry "E:\\Zinc_batters_datas\\runs\\Feature_create\\registry\\global_feature_registry.json" `
  --records "E:\\Zinc_batters_datas\\runs\\Feature_create\\worker_runs\\result_records.jsonl" `
  --manifest "E:\\Zinc_batters_datas\\runs\\Feature_create\\prepared\\source_manifest.json" `
  --report "E:\\Zinc_batters_datas\\runs\\Feature_create\\global_integrity_audit.json"
```

Use `--strict-observation-records` when the run has reached the stage where every eligible result-feature Observation is expected to have at least one Result Record. By default, an eligible Observation without a Record is a warning, not an error: a source may establish that a variable exists without yielding a separately exportable value (for example, a visual-only series).

Exit codes: `0` = pass or warnings only; `2` = one or more integrity errors. The JSON report contains stable issue codes, severity, object IDs, summary counts, and the audit version.

## Hard failures

- Missing or duplicate Record, Observation, or canonical Feature IDs.
- A Record with no valid Observation or Feature ID.
- A Record's Feature disagrees with its Observation's Feature after resolving merge redirects.
- Record and Observation source IDs disagree.
- Confirmed Feature with no eligible result-feature Observation or no evidence-verified Observation.
- Missing, malformed, mismatched, or unknown source evidence references (when a source manifest is supplied).
- Feature ↔ Observation links that are missing or asymmetric.
- Excluded Observation without a `feature_excluded` decision event.
- Retired merged Feature without a valid redirect and `feature_merged` event.
- Split-pending Feature without a `feature_split` event.
- Merge/split/reassignment/exclusion events without evidence Observation references, or merge/split events that fail to identify at least two Feature subjects.
- Decision events with missing/duplicate IDs, unknown subjects, missing rationale/decision, or dangling evidence Observation IDs.

## Warnings and strict mode

An eligible, non-excluded result-feature Observation that has no Result Record is reported as `observation_without_result_record`. It is a warning by default because a Feature can be evidenced without a numeric/text value that qualifies as a Result Record. When a dataset is expected to be fully materialized into records, use `--strict-observation-records` to make these omissions blocking.

The audit does not treat `visual_only` evidence as numeric verification. It does accept it as evidence that a result variable/Feature was reported; the separate evidence-validation and export rules continue to control whether a numeric value can be exported.

## Export integration

`scripts/export_txt.py` runs the same integrity engine before writing either formal TXT file. Any hard integrity error blocks both outputs. The optional `--source-manifest` enables source-ID existence checks, and `--strict-observation-records` enables strict Observation-to-Record completeness at export time. The separate internal export audit JSON records the integrity summary and all findings; it is not added to the formal output directory.

## Non-goals

- No automatic repair or best-effort reassignment.
- No semantic equivalence inference or model calls.
- No conversion of warnings into confirmed completeness.
- No replacement for the Registry JSON Schema, evidence quote/hash checks, model review, human adjudication, or export-specific value/context gates.
