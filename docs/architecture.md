# Architecture — Feature_create v0.1

## 1. System boundary

Feature_create is an evidence-preserving literature extraction workflow with three execution layers:

1. **Antigravity Supervisor** — thin control-plane coordinator; does not perform bulk paper reading or extraction.
2. **OpenCode Worker A and Worker B** — two independent semantic workers, each pinned to a different currently verified free OpenCode Go model.
3. **Local deterministic Python orchestrator** — all file processing, task state, stable identifiers, schema validation, evidence checks, Registry transitions, and serialization.

The main semantic workload is routed to the two free OpenCode models. The system must never substitute a paid model when a free model is unavailable.

## 2. Data flow

```text
chem-literature paired research_corpus_paired.csv
      |
      v
Upstream chem-literature preprocessing package
(evidence_index + parsed text + tables + media assets + QC)
      |
      v
Package/hash validation and logical-source indexing
(no PDF/SI parsing in Feature_create)
      |
      v
Deterministic source-aware chunk planner
(page/text ranges + original table rows/cells + image links)
      |
      v
Antigravity Supervisor (thin coordination only)
      |
      +--------------------------------------+
      |                                      |
      v                                      v
OpenCode Worker A                       OpenCode Worker B
verified free model A                   verified free model B
chunk-scoped extraction                 independent chunk-scoped audit
      |                                      |
      +------------------+-------------------+
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

## 3. Supervisor responsibilities

The Antigravity Supervisor:
- inspects the chem-literature paired CSV-derived inventory and compact task status;
- ensures the two free worker model references are distinct and verified;
- dispatches tasks through OpenCode's non-interactive CLI or the established local worker-manager interface;
- checks progress/status cards rather than repeatedly reading long worker transcripts;
- coordinates retries only on the same pinned free model;
- creates targeted review tasks for unresolved issues;
- starts deterministic validation/reconciliation/export scripts;
- reports blockers and incomplete sources.

It must not read the entire corpus and perform the same scientific extraction itself. It is a controller, not Worker C.

## 4. Worker responsibilities

### Worker A — source-centric discovery

Worker A reviews one assigned bounded evidence chunk from the upstream-preprocessed paper/SI package (parsed text ranges, original table rows/cell fragments, and related extracted visual assets) and extracts:
- all raw result-feature observations;
- individual reported result values;
- sample identities and dynamically associated context;
- text/table/figure/SI evidence references;
- source coverage and unresolved items.

### Worker B — feature-centric discovery and audit

Worker B independently reviews the same assigned chunk as Worker A (without seeing A's first-pass output) and discovers the result-feature inventory. It focuses on:
- variables that appear only in SI, tables, figures, or secondary analysis;
- omissions and additional result series;
- same-name/different-definition cases;
- different-name/equivalent-definition candidates;
- differences in statistic, formula, normalization, or temporal scope.

Worker B need not duplicate every value Worker A extracted. It should produce evidence-backed omission candidates and targeted record candidates when it detects missed values.

The first-pass outputs must remain isolated: neither Worker sees the other's output before its own first-pass review is complete.

## 5. Deterministic evidence chunking

The unit of semantic work is a bounded chunk, not an entire paper. Python creates stable evidence units from the already-parsed upstream package:

- Text units split on upstream page headings and then paragraph/newline boundaries. Each unit retains exact source-text character offsets, document type, and original page number. Units are sorted by original source offsets before grouping.
- Tables normally group complete original rows. If one row is too large, it is split into cell fragments retaining the original table ID, 1-based row and column indexes, cell offsets, and header-context references.
- Pre-extracted figure/table assets are attached once to the matching evidence unit when the upstream index provides the relation; otherwise they remain explicit orphan visual units.
- A task bundle contains only its assigned units. A separate paper-wide navigation index identifies all chunks and candidate MAIN-to-SI references; a per-chunk evidence index carries the exact assigned page/table/figure locators and the upstream index SHA-256.
- Adjacent text and repeated table-header rows are explicitly context-only. They can help interpret the assigned evidence but do not count as additional assigned coverage.
- Every evidence reference must cite `chunk_id` and `unit_id` in addition to the original upstream locator. Deterministic validation rejects references to unassigned chunks/units, text quotes outside the assigned text range, or table rows/cells outside the assigned unit.
- Every `(source_id, unit_id)` assignment receives a deterministic `scope_id`. A chunk's coverage checklist contains one row per scope, including linked image assets that share a parent evidence unit. Each worker must report every assigned scope exactly once; the validator checks the exact scope/source pairing, and the audit aggregates scope states across all chunks before reporting whole-source coverage.
- The paper-wide index explicitly links adjacent text segments by source offsets and original table-row units by `table_id + row index`, even when their units fall in different chunks. This is a provenance/navigation relation, not an automatic scientific inference that a distant sample or condition belongs to a result.
- The planner iteratively reduces chunk size when the rendered bundle exceeds its hard character limit. If an indivisible asset/metadata item still exceeds limits, the task is explicitly blocked and reported; content is never silently truncated.

Cross-chunk relationships are preserved through stable unit/chunk identities, original source locators, same-row table identities, paper-wide navigation, and explicit unresolved items. A worker must not infer a sample/condition/result link from proximity. Full-source coverage is aggregated only after all its `(source_id, unit_id)` scopes are successful for the relevant Worker; no source-level status may overwrite the status of another unit.

## 6. Targeted cross-chunk context review

Long-range result context is handled as a selective second pass after first-pass evidence validation and Registry ingestion. This stage is not a paper-wide reread and does not add another worker model.

1. Worker A may attach a cross_chunk_target nomination to a value-specific missing_context unresolved item. The nomination contains the requested context role, a relation basis, a rationale, and exact remote chunk_id/unit_id/source_id descriptors.
2. The context queue builder resolves each descriptor against the paper-wide unit_navigation inventory, checks source/chunk identity, confirms it is in another chunk, and checks the declared adjacency/same-row relation where applicable.
3. It builds one small evidence packet from the current-side unit(s), the nominated target unit(s), and the linked result record's current context fields. It does not attach the full paper or read either first-pass Worker output.
4. The task is routed to the configured Worker B model using a dedicated context-review schema/prompt. The four decisions are association_confirmed, no_association, insufficient_evidence, and conflicting_evidence.
5. Every confirmed context fact requires verified evidence from both sides. Evidence references must point to assigned unit IDs and their actual owning chunk IDs. Text quotes are checked against exact source offsets; structured table cells are checked against original row/column and cell offsets.
6. The apply step may append only sample identity, condition, time/cycle, protocol, statistical-scope, or other context facts. It protects the raw/numeric result value, Feature/Observation IDs, raw feature name, unit, result type, method, source ID, and first-pass evidence references.
7. no_association closes the investigated question without adding a fact. Insufficient or conflicting evidence stays unresolved for human review. The decision and two-sided citations are recorded in both the unresolved item and linked result record history.
8. The run audit reports context review task states, decision counts, and the number of unresolved context items remaining.

Only value-specific unresolved items with a linked result record and a verifiable remote target are eligible. Feature-level ambiguity without a result record, unindexed targets, inaccessible sources, and non-context unresolved issues remain in the ordinary unresolved queue. The navigation index is not evidence; only the target unit's assigned content can support a context fact.

## 7. Model pair and run isolation

The two workers must use different exact model references. The current preferred pair is:
- `opencode-go/crouton-alpha-free`;
- `opencode-go/beam-free`.

They are limited-time offers, so the pair is configurable and must be verified before each run. Model names must not be inferred from display labels alone; use the exact model references exposed by the current OpenCode installation.

Every paper/task invocation explicitly pins its model. Use deterministic titles and avoid implicit helper-model calls. A task must not fall back to an OpenCode paid model or to the Antigravity model when the selected free worker fails.

## 8. Parallelism and quota protection

Keep concurrency configurable. The intended initial operating point is:
- up to 2 papers active at once;
- Worker A and Worker B per paper;
- at most 4 active inference calls under this arrangement.

The orchestrator must also implement bounded retries, backoff, and a global rate limiter. Do not assume unlimited request allowances mean unlimited safe concurrency. HTTP 429 or service errors should reduce concurrency and preserve tasks for retry; never switch to a paid model.

## 9. Deterministic processing

chem-literature owns all scientific-document preprocessing: PDF/SI text extraction, page/layout indexing, table extraction, figure/table image extraction, SI parsing, evidence-index generation, and preprocessing QC.

Feature_create Python owns:
- ingestion of the upstream chem-literature preprocessed paired CSV (not independent corpus discovery);
- verification and hashing of the CSV-listed raw MAIN/SI files without parsing them;
- validation/indexing of upstream evidence_index, parsed text, structured tables, media assets, and QC;
- provenance indexing, deterministic chunking of upstream-parsed content, and chunk-scoped task queue construction;
- exact assigned-unit/chunk validation for every worker evidence reference;
- stable ID allocation;
- Registry persistence and decision logs;
- schema validation;
- evidence-location and table-cell verification against the upstream page/table index;
- duplicate detection and consistency checks;
- checkpoints, locks, retries, and idempotent resume;
- final TXT serialization and audit counts.

A model may identify or interpret a scientific feature, value, or context. It cannot allocate persistent Registry IDs, claim unverified evidence is verified, or directly commit Registry mutations.

## 10. Registry lifecycle

A raw discovery is an immutable Observation. An Observation may initially be linked to a provisional Canonical Feature. Later reconciliation may confirm the candidate, link it to an existing Canonical Feature, leave it under review, split it from an incorrectly broad candidate, or exclude it as a condition/non-result item.

Canonical Feature IDs are stable internal identities. Merge/split history is retained. A merge creates a redirect from the retired ID to the surviving ID; a split creates new canonical identities and reassigns observations through an explicit decision event. No historical decision is silently erased.

## 11. Context model

Do not predefine a narrow universal list of experimental condition names. Store context as evidence-backed facts with:
- raw label;
- raw value;
- unit, if present;
- context role (sample identity, condition, time/cycle, protocol, statistical scope, or other);
- evidence locator.

## 12. Evidence model

Every accepted result value references one or more evidence locators. The locator should identify the source file and the most precise available position: page, section, paragraph, table, row, column, cell, figure, panel, caption, or digitization artifact.

Visual-only interpretation is not an exact numeric extraction. A plotted trend may support feature discovery and series identification without supporting precise result values.

## 13. Incremental execution

Each source and task has a deterministic identity derived from source hash, task type, contract version, and relevant configuration. Completed tasks are skipped on resume only when inputs and contract versions still match.

The Registry uses append-only decision events and atomic writes/locks. A crash must not leave half-written canonical state or duplicate IDs.

## 14. Deliverable boundary

Internal run files may include JSON/JSONL, evidence maps, checkpoints, metrics, and review queues. Formal output remains exactly:
- `result_feature_records.txt`
- `result_feature_list.txt`

The Feature dictionary must remain independent of which papers happen to have reported values in the current corpus.
