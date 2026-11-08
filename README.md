# Feature_create

**Feature_create** is a research skill for discovering, extracting, reconciling, and documenting result features reported in scientific papers and their supplementary information.

The final user-facing deliverables are exactly:

- `result_feature_records.txt` — one record per reported result value, with its source evidence and sample/condition context.
- `result_feature_list.txt` — a deduplicated dictionary of result features, with definitions, aliases, units, methods, and comparability notes.

## Non-negotiable execution constraint

**OpenCode model-token consumption must remain exactly zero.**

The workflow does not invoke OpenCode models, OpenCode workers, `opencode run`, OpenCode model providers, or OpenCode fallback routes. Semantic tasks are performed by the Antigravity host model available to the running Skill. Deterministic work is performed locally by Python.

This means zero OpenCode tokens, not zero total model usage: Antigravity may consume its own model quota. If the Antigravity model or quota is unavailable, the workflow must pause safely and preserve its checkpoint. It must never silently switch to OpenCode.

## Current development baseline

The repository begins with the architecture and data-contract foundation:

- Global incremental Feature Registry.
- Independent source-centric and feature-centric worker contracts.
- Evidence-first reconciliation rules.
- Explicit zero-OpenCode-token execution policy.

The presence of these contracts does not imply that the end-to-end extraction implementation or validation suite is complete.

## Design principles

1. Discover features from source evidence; never constrain discovery to an existing schema.
2. Preserve raw reported names and definitions before normalization.
3. Keep result values separate from sample identity, conditions, time points, cycles, and protocols.
4. Never merge features on name similarity alone.
5. Keep uncertain candidates separate until sufficient evidence exists.
6. Treat figures as first-class sources; never convert visual estimates into exact values.
7. Use Python for IDs, validation, state transitions, audit logs, and deterministic export.
8. Preserve provenance and decisions so a run can be resumed and audited.

## Repository map

- `SKILL.md` — execution contract and agent instructions.
- `contracts/` — machine-readable schemas for Registry and worker payloads.
- `docs/architecture.md` — component boundaries and workflow.
- `docs/reconciliation.md` — evidence and semantic reconciliation rules.
- `docs/token_policy.md` — zero-OpenCode-token policy and failure behavior.

## Status

Architecture/contract baseline: **v0.1**  
End-to-end implementation: **not yet declared complete**  
Production readiness: **not yet established**
