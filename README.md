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
- **Local Python:** deterministic parsing, chunking, scheduling, evidence validation, stable IDs, Registry persistence, checkpoints, audits, and TXT export.

## Zero paid model-token consumption requirement

The target is **zero paid inference cost for the OpenCode Worker calls**, not zero model tokens processed in the literal accounting sense and not zero model calls.

Workers must use two distinct models whose current OpenCode Go catalog pricing is verified as Free for input, output, and cached usage. The current preferred pair is:

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
- `docs/token_policy.md` — zero-paid-cost policy and provider/model guards.
- `docs/opencode_free_model_execution.md` — OpenCode Go invocation, model pinning, and verification.
- `worker_prompts/` — isolated Worker A, Worker B, and targeted-review prompts.
- `config/` — model and extraction policies.

## Status

Architecture/contract baseline: **v0.1**  
End-to-end implementation: **not yet declared complete**  
Production readiness: **not yet established**
