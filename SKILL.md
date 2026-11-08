---
name: Feature_create
description: Discover and extract result features and reported values from scientific papers and supplementary information using two independent free OpenCode Go workers, a thin Antigravity Supervisor, and deterministic local validation.
---

# Feature_create

## Mission

Discover the broadest defensible set of result features actually reported in the supplied scientific papers and supplementary information (SI), extract their reported values with source evidence and dynamically associated context, reconcile feature identities across papers, and export exactly two formal TXT files:

1. `result_feature_records.txt`
2. `result_feature_list.txt`

Do not perform feature selection, hypothesis testing, causal inference, interaction analysis, performance ranking, or machine-learning modelling. This Skill performs feature discovery, evidence-backed extraction, normalization, and auditing only.

## Core model architecture

Use **Antigravity Supervisor + two OpenCode Worker models + deterministic local Python**.

- Antigravity is the thin Supervisor/control plane. It plans, dispatches, monitors, and coordinates; it must not perform bulk paper reading or result extraction.
- Worker A runs through OpenCode using a verified free OpenCode Go model and performs source-centric discovery/extraction.
- Worker B runs through OpenCode using a second, distinct verified free OpenCode Go model and independently performs feature-centric discovery/coverage audit.
- Python performs parsing, provenance, task management, evidence validation, stable IDs, Registry operations, auditing, and export.

The intended current model pair is:
- Worker A: `opencode-go/crouton-alpha-free`
- Worker B: `opencode-go/beam-free`

These are current preferred candidates, not immutable guarantees. Their free status and availability must be re-verified before every run. A third model can only be substituted if its exact current OpenCode Go input/output/cache prices are independently verified as Free and the run manifest records the change.

## Zero-paid-cost requirement

The requirement is **zero paid model-token cost for all OpenCode worker calls**. This does not mean zero tokens are processed; free models still process tokens. It means their current published inference prices must be Free and the run must not route to paid models.

- Every worker call must specify its exact model reference explicitly.
- Worker A and Worker B must use different model IDs.
- Paid fallback is forbidden.
- If a free model is missing, region-restricted, rate-limited, or not currently verifiable as free, pause the task and checkpoint. Do not use a paid OpenCode model or shift extraction to Antigravity.
- Use deterministic session titles so automatic title-generation calls do not invoke an unintended helper model.
- If `small_model` or another helper route can be used by the installed OpenCode version, pin it to a currently verified free model or disable the helper behavior.
- Record actual model IDs and usage/cost metadata exposed by OpenCode.
- OpenCode Go subscription fees are separate from free-model inference prices.

Antigravity Supervisor usage is also separate. Keep its control-plane work compact; do not send complete papers to it for extraction.

## Execution roles

### Antigravity Supervisor

The Supervisor:
- inspects corpus inventory and run status;
- verifies that the configured model pair is distinct and currently free;
- dispatches Worker A/B tasks;
- inspects compact progress cards rather than long transcripts;
- handles task failures and targeted review;
- coordinates deterministic Python validation/reconciliation/export;
- reports incomplete sources and unresolved issues.

Do not perform Worker A or Worker B's literature interpretation in the Supervisor.

### OpenCode Worker A — Source-centric Discovery

Worker A reviews each assigned paper and all available SI. It discovers raw result features, extracts individual result values, dynamically associates sample/context facts, and cites precise source locations.

### OpenCode Worker B — Feature-centric Discovery and Audit

Worker B independently reviews the same source bundle without seeing Worker A's first-pass output. It independently discovers the feature inventory, checks figures/tables/SI for omissions, detects semantic distinctions, and submits evidence-backed omission candidates.

The two workers must run on different verified free models. The task contracts in `worker_prompts/` and `contracts/` are normative.

### Local deterministic Python layer

Python owns:
- source inventory, hashes, parsing, chunking, and task queue construction;
- model-pair and free-price preflight checks;
- stable ID allocation;
- Registry persistence and decision logs;
- schema validation;
- evidence-location and table-cell verification;
- duplicate detection and consistency checks;
- checkpoints, locks, retries, and idempotent resume;
- model/cost accounting where the CLI exposes it;
- final TXT serialization and audit counts.

Python must never silently change the provider/model to continue a failed task.

## Required workflow

### Phase 0 — Preflight and cost gate

1. Identify the corpus root and all supplied papers and SI files.
2. Read the supplied Feature Schema as a mapping reference only.
3. Record source inventory, hashes, availability, and parsing status.
4. Refresh/check the available OpenCode model catalog.
5. Confirm Worker A and Worker B use two distinct exact OpenCode Go model references.
6. Verify both models are currently Free for input, output, cached reads, and cached writes using current model/catalog pricing metadata or a fresh official price snapshot.
7. Check that deterministic task titles are supplied and no helper-model default can select a paid model.
8. If any cost/availability check is inconclusive, stop before dispatching semantic work.

### Phase 1 — Source preparation

1. Parse available PDF, XML/TEI, Markdown, HTML, CSV, XLSX, and SI sources using deterministic local tools where possible.
2. Preserve source/page/section/table/row/column/figure locators.
3. Index all figures, captions, tables, and SI materials.
4. Keep source text and table data traceable to original files.
5. Record inaccessible, corrupt, or only partially parsed sources; do not count them as fully reviewed.

Figures are first-class evidence. Provide relevant figure images to the relevant OpenCode worker when visual interpretation is required.

### Phase 2 — Independent discovery and extraction

For each paper/SI bundle:

- Worker A returns raw feature observations, individual result-value candidates, context facts, evidence references, coverage, and unresolved items.
- Worker B independently returns feature observations, coverage audits, omission candidates, semantic distinctions, and unresolved items.
- Neither worker receives the other's first-pass output.
- Existing schema entries may be used only as a post-discovery mapping reference; they must never become the discovery boundary.
- Run the two tasks with different free model IDs.

Do not collapse multiple samples, conditions, time points, cycles, or series into one result record. Keep each separately represented.

### Phase 3 — Deterministic validation

Python validates:
- payload structure and required fields;
- source and locator existence;
- quoted text against the source;
- table values against exact row/column/cell;
- numeric parseability without changing the original reported string;
- units and context association;
- duplicate candidate records;
- actual model identity and cost metadata where exposed;
- unsupported or contradictory claims.

A model agreement is not evidence. Every accepted result value must have a traceable source. Values that cannot be reliably read from figures must not be represented as exact numbers.

### Phase 4 — Global Registry reconciliation

1. Preserve raw observations.
2. Compare them with existing Registry candidates.
3. Use name and unit similarity only to retrieve candidates, never as sufficient evidence to merge.
4. Evaluate physical meaning, definition, formula, normalization basis, statistic, temporal scope, dimension, and method.
5. Merge only when semantic equivalence is supported.
6. Keep uncertain candidates separate and mark them for review.
7. Map confirmed canonical features to the existing Feature Schema only when definitions are compatible.
8. Allocate new IDs through Python for confirmed new features.
9. Record every create, merge, split, mapping, and status decision in an append-only decision log.

Use the two OpenCode free workers for semantic comparisons that need model judgement. The Supervisor coordinates, while Python applies and audits state transitions.

### Phase 5 — Final global audit and export

Audit cross-paper consistency, feature coverage, evidence integrity, record completeness, unresolved cases, and paid-cost compliance.

Export exactly two formal TXT files:
- `result_feature_records.txt`
- `result_feature_list.txt`

The records file may contain paper identifiers, raw names, mapped feature IDs, reported values and units, result types, sample/context facts, protocols, evidence locators, extraction states, and unresolved questions.

The Feature-list file contains one entry per canonical result feature, with ID, canonical name, aliases, definition, result type, units/dimension, measurement/calculation methods, and standardization/comparability notes. It must not contain concrete values, Record_IDs, samples, conditions, paper coverage, or conclusions.

### Phase 6 — Report

Report counts calculated from local audit data:
- sources found, accessible, reviewed, partially reviewed, and inaccessible;
- papers and SI packages actually reviewed;
- result records extracted and evidence-verified;
- canonical features confirmed;
- unresolved records and feature candidates;
- unresolved context items;
- figure-only and unreadable sources;
- exact Worker A/B model IDs;
- free-price verification timestamp/source;
- usage/cost metadata where exposed, and explicit unknowns otherwise.

Never let a model invent these counts.

## Registry and worker contracts

The machine-readable contracts in `contracts/` are normative. When a payload does not validate, reject or quarantine it and create a repair task. Do not silently coerce malformed data into accepted records.

The Global Incremental Feature Registry is authoritative for stable internal feature identity. Workers submit observations and candidates; they never allocate or mutate persistent Registry IDs directly.

## Stop conditions

Stop or pause when:
- either configured worker model is not currently verifiable as free;
- the two selected workers resolve to the same model;
- a model is unavailable or region-restricted;
- OpenCode indicates or reports a paid route/cost;
- the only way to continue would be a paid fallback or bulk extraction by Antigravity;
- source integrity cannot be established;
- a schema migration would invalidate existing IDs or records.

Preserve partial work and report exactly what remains incomplete.
