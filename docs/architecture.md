# Architecture — Feature_create v0.1

## 1. System boundary

Feature_create is a local-first, evidence-preserving literature extraction workflow. It has three execution layers:

1. **Antigravity Supervisor and semantic workers** — semantic interpretation of scientific language, variables, context, and figures.
2. **Local deterministic Python orchestrator** — all file processing, task state, stable identifiers, schema validation, evidence checks, Registry transitions, and serialization.
3. **Source corpus** — original papers, SI, preprocessed text, tables, figures, and existing schemas.

OpenCode is outside the execution graph. No OpenCode model call, worker, session, or fallback is permitted.

## 2. Data flow

```text
Corpus inventory
      |
      v
Local source preparation and provenance indexing
      |
      +---------------------------+
      |                           |
      v                           v
Worker A: source-centric     Worker B: feature-centric
independent first pass       independent first pass
      |                           |
      +-------------+-------------+
                    |
                    v
         Deterministic evidence validation
                    |
                    v
      Raw Feature Observation store
                    |
                    v
       Global incremental Feature Registry
                    |
                    v
       Semantic reconciliation and audit
                    |
                    v
        Deterministic two-file TXT export
```

## 3. Why two independent perspectives

A source-centric pass is best suited to exhaustive value extraction: every result statement, table cell, series, sample, test condition, and SI result is considered in its source context.

A feature-centric pass is best suited to coverage detection: it independently asks which result variables exist, which series or SI results may have been missed, and which similarly named variables are semantically distinct.

The passes are independent at first. Their outputs are reconciled only after both have completed. Worker B does not need to repeat every value already extracted by Worker A; it submits evidence-backed omission candidates when it finds missed material.

If Antigravity's native task/agent facilities do not support actual independent concurrent execution, the Supervisor runs the bounded tasks sequentially. Concurrency is an optimization, not a correctness requirement.

## 4. Separation of responsibilities

| Responsibility | Owner |
|---|---|
| Raw feature discovery | Antigravity semantic worker |
| Result value/context interpretation | Antigravity semantic worker |
| Stable IDs and sequence allocation | Python |
| Source hashes and locator verification | Python |
| Exact table cell verification | Python |
| Semantic equivalence recommendation | Antigravity, with evidence |
| Final merge/split execution | Python, gated by reconciliation policy |
| Registry history and decision log | Python |
| Final TXT formatting | Python |
| Counts and audit metrics | Python |

The model may recommend a decision. It cannot directly commit a Registry mutation or claim that unverified evidence is verified.

## 5. Registry lifecycle

A raw discovery is an immutable Observation. An Observation may initially be linked to a provisional Canonical Feature. Later reconciliation may:
- confirm the candidate;
- link it to an existing Canonical Feature;
- remain under review;
- be split from an incorrectly broad candidate;
- be excluded as a condition or non-result item.

Canonical Feature IDs are stable internal identities. Merge/split history is retained. A merge creates a redirect from the retired ID to the surviving ID; a split creates new canonical identities and reassigns observations through an explicit decision event. No historical record is silently erased.

## 6. Context model

Do not predefine a narrow universal list of experimental condition names. Store context as evidence-backed facts with:
- raw label;
- raw value;
- unit, if present;
- context role (sample identity, condition, time/cycle, protocol, or other);
- evidence locator.

This permits domains and papers to report different conditions without losing information. A downstream normalized vocabulary can be layered on later.

## 7. Evidence model

Every accepted result value references one or more evidence locators. The locator should identify the source file and the most precise available position: page, section, paragraph, table, row, column, cell, figure, panel, caption, or digitization artifact.

Visual-only interpretation is not an exact numeric extraction. A plotted trend may support feature discovery and series identification without supporting precise result values.

## 8. Incremental execution

Each source and task has a deterministic identity derived from the source hash, task type, contract version, and relevant configuration. Completed tasks are skipped on resume only when their inputs and contract versions still match.

The Registry uses append-only decision events and atomic writes/locks. A crash must not leave a half-written canonical state or duplicate IDs.

## 9. Deliverable boundary

Internal run files may include JSON/JSONL, evidence maps, checkpoints, metrics, and review queues. Formal output remains exactly:
- `result_feature_records.txt`
- `result_feature_list.txt`

The feature dictionary must remain independent of which papers happen to have reported values in the current corpus.
