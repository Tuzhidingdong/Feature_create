---
name: Feature_create
description: Discover and extract every result feature and reported result value from scientific papers and supplementary information, maintaining an evidence-backed global Feature Registry and exporting two stable TXT deliverables.
---

# Feature_create

## Mission

Discover the broadest defensible set of result features actually reported in the supplied scientific papers and supplementary information (SI), extract their reported values with source evidence and dynamically associated context, reconcile feature identities across papers, and export exactly two formal TXT files:

1. `result_feature_records.txt`
2. `result_feature_list.txt`

Do not perform feature selection, hypothesis testing, causal inference, interaction analysis, performance ranking, or machine-learning modelling. This Skill performs feature discovery, evidence-backed extraction, normalization, and auditing only.

## Absolute execution constraint: zero OpenCode model tokens

The entire workflow must consume **zero OpenCode model tokens**.

- Do not call `opencode run`.
- Do not start or dispatch OpenCode workers.
- Do not invoke OpenCode model providers, model sessions, or OpenCode fallback routes.
- Do not route work to an OpenCode model when the Antigravity model is unavailable.
- Do not claim that a model is free merely because a model identifier contains words such as `free` or `alpha`.

Use the Antigravity host model available to the current Skill execution for semantic reasoning. Use local Python for deterministic processing, parsing, validation, indexing, state management, and export. Antigravity model use may consume Antigravity quota; zero OpenCode tokens does not mean zero total model usage.

If the current Antigravity model is unavailable, rate-limited, or cannot accept the required task:
1. stop dispatching semantic tasks;
2. persist completed outputs and the checkpoint;
3. mark pending tasks as paused;
4. report the blocker;
5. resume only when an eligible Antigravity model is available.

Never bypass this policy to finish a task.

## Execution roles

### Antigravity Supervisor

The Supervisor owns planning and coordination. It should:
- inspect the corpus inventory and existing run state;
- request deterministic preparation and validation from local scripts;
- dispatch bounded semantic tasks through the Antigravity host model;
- keep source-centric and feature-centric first-pass tasks independent;
- inspect compact progress summaries rather than repeatedly loading large intermediate files;
- resolve or request targeted reviews only where the evidence requires them;
- report incomplete sources and unresolved issues honestly.

The Supervisor must not invent evidence, assign persistent IDs manually, edit the Registry by free-form text, or silently change the model route.

### Local deterministic Python layer

Python owns:
- source inventory, hashes, parsing, chunking, and task queue construction;
- stable ID allocation;
- Registry persistence and decision logs;
- schema validation;
- evidence-location and table-cell verification;
- duplicate detection and consistency checks;
- checkpoints, locks, retries, and idempotent resume;
- final TXT serialization and audit counts.

Python must not use a generative model as an undisclosed fallback.

### Antigravity semantic workers

Two logical perspectives are required:

- **Worker A — source-centric discovery:** comprehensive extraction of reported result features and individual result values from a paper and its SI.
- **Worker B — feature-centric audit:** independent discovery and coverage review, focusing on omitted result variables, result series, semantic distinctions, and SI coverage.

These are task contracts, not a requirement to use two different model providers. Use native Antigravity subtask/agent capabilities only when actually available. Otherwise execute bounded tasks sequentially through the current Antigravity model. Do not simulate parallel workers by invoking OpenCode.

First-pass A and B tasks must not read each other's outputs. Their outputs may be compared only after both first-pass tasks are complete.

## Required workflow

### Phase 0 — Preflight and policy gate

1. Identify the corpus root and all supplied papers and SI files.
2. Read any supplied authoritative feature schema as a mapping reference only.
3. Record source inventory, file hashes, availability, and parsing status.
4. Verify that the execution route is the Antigravity host model plus local deterministic processing.
5. Establish a run directory and resumable checkpoint.
6. Refuse to start semantic work if doing so would require an OpenCode model call.

### Phase 1 — Source preparation

1. Parse available PDF, XML/TEI, Markdown, HTML, CSV, XLSX, and SI sources using deterministic local tools where possible.
2. Preserve source/page/section/table/row/column/figure locators.
3. Index all figures, captions, tables, and SI materials.
4. Keep source text and table data traceable to original files.
5. Record inaccessible, corrupt, or only partially parsed sources; do not count them as fully reviewed.

Figures are first-class evidence. Provide relevant figure images to the Antigravity model when visual interpretation is required. Do not assume that a text conversion captures all plotted series or values.

### Phase 2 — Independent discovery and extraction

For each paper/SI bundle:

- Worker A returns raw feature observations, individual result-value candidates, context facts, evidence references, coverage, and unresolved items.
- Worker B independently returns feature observations, coverage audits, omission candidates, semantic distinctions, and unresolved items.
- Neither worker receives the other's first-pass output.
- Existing schema entries may be supplied only after or separately from first-pass discovery; they must never become the discovery boundary.

Do not collapse multiple samples, conditions, time points, cycles, or series into one result record. Keep them as separately represented values and context facts.

### Phase 3 — Deterministic validation

Python validates:
- payload structure and required fields;
- source and locator existence;
- quoted text against the source;
- table values against the exact row/column/cell;
- numeric parseability without changing the original reported string;
- units and context association;
- duplicate candidate records;
- unsupported or contradictory claims.

A model agreement is not evidence. Every accepted result value must have a traceable source. Values that cannot be reliably read from figures must not be represented as exact numbers.

### Phase 4 — Global Registry reconciliation

1. Preserve raw observations.
2. Compare them with existing Registry candidates.
3. Use name and unit similarity only to retrieve candidates, never as sufficient evidence to merge.
4. Evaluate physical meaning, definition, formula, normalization basis, statistic, temporal scope, dimension, and method.
5. Merge only when semantic equivalence is supported.
6. Keep uncertain candidates separate and mark them for review.
7. Map confirmed canonical features to the existing feature schema only when definitions are compatible.
8. Allocate new IDs through Python for confirmed new features.
9. Record every create, merge, split, mapping, and status decision in an append-only decision log.

### Phase 5 — Final global audit and export

Audit cross-paper consistency, feature coverage, evidence integrity, record completeness, and unresolved cases.

Export exactly two formal TXT files:
- `result_feature_records.txt`
- `result_feature_list.txt`

Do not add sample-specific values, paper coverage statistics, Record_IDs, conditions, or conclusions to the feature dictionary. Do not put concrete result values in the feature dictionary.

The records file may contain paper identifiers, raw names, mapped feature IDs, reported values and units, result types, sample/context facts, protocols, evidence locators, extraction states, and unresolved questions, consistent with the established output contract.

The feature-list file contains one entry per canonical result feature, with ID, canonical name, aliases, definition, result type, units/dimension, measurement/calculation methods, and standardization/comparability notes.

### Phase 6 — Report

Report counts calculated from the local audit database:
- sources found, accessible, reviewed, partially reviewed, and inaccessible;
- papers and SI packages actually reviewed;
- result records extracted and evidence-verified;
- canonical features confirmed;
- unresolved records and feature candidates;
- unresolved context items;
- figure-only and unreadable sources.

Never let a model invent these counts.

## Registry and worker contracts

The machine-readable contracts in `contracts/` are normative. When a payload does not validate, reject or quarantine it and create a repair task. Do not silently coerce malformed data into accepted records.

The Global Incremental Feature Registry is authoritative for stable internal feature identity. Workers submit observations and candidates; they never allocate or mutate persistent Registry IDs directly.

## Stop conditions

Stop or pause when:
- the only available semantic route would consume OpenCode model tokens;
- Antigravity model access is unavailable;
- source integrity cannot be established;
- a required input is missing and cannot be found locally;
- a schema migration would invalidate existing IDs or records;
- evidence is insufficient to make a safe merge or numeric extraction.

Preserve partial work and report exactly what remains incomplete.
