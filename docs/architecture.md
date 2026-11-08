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
Antigravity Supervisor (thin coordination only)
      |
      +--------------------------------------+
      |                                      |
      v                                      v
OpenCode Worker A                       OpenCode Worker B
verified free model A                   verified free model B
source-centric extraction               feature-centric audit
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

Worker A reviews the assigned upstream-preprocessed paper/SI bundle (parsed text, structured tables, and extracted visual assets) and extracts:
- all raw result-feature observations;
- individual reported result values;
- sample identities and dynamically associated context;
- text/table/figure/SI evidence references;
- source coverage and unresolved items.

### Worker B — feature-centric discovery and audit

Worker B independently reviews the same upstream-preprocessed paper/SI bundle and discovers the result-feature inventory. It focuses on:
- variables that appear only in SI, tables, figures, or secondary analysis;
- omissions and additional result series;
- same-name/different-definition cases;
- different-name/equivalent-definition candidates;
- differences in statistic, formula, normalization, or temporal scope.

Worker B need not duplicate every value Worker A extracted. It should produce evidence-backed omission candidates and targeted record candidates when it detects missed values.

The first-pass outputs must remain isolated: neither Worker sees the other's output before its own first-pass review is complete.

A paper may generate multiple Worker A/B task pairs. Every page segment, table-row range, and source asset receives a stable `scope_id`; original `source_id`, page, table ID, figure ID, bounding box, and 1-based table row/column coordinates remain unchanged. A source is fully reviewed only after all of its scope IDs have completed coverage for that worker. Same-page context snippets and repeated table-prefix rows help preserve local relationships but do not expand the assigned coverage scope.

## 5. Model pair and run isolation

The two workers must use different exact model references. The current preferred pair is:
- `opencode-go/crouton-alpha-free`;
- `opencode-go/beam-free`.

They are limited-time offers, so the pair is configurable and must be verified before each run. Model names must not be inferred from display labels alone; use the exact model references exposed by the current OpenCode installation.

Every paper/task invocation explicitly pins its model. Use deterministic titles and avoid implicit helper-model calls. A task must not fall back to an OpenCode paid model or to the Antigravity model when the selected free worker fails.

## 6. Parallelism and quota protection

Keep concurrency configurable. The intended initial operating point is:
- up to 2 papers active at once;
- Worker A and Worker B per paper;
- at most 4 active inference calls under this arrangement.

The orchestrator must also implement bounded retries, backoff, and a global rate limiter. Do not assume unlimited request allowances mean unlimited safe concurrency. HTTP 429 or service errors should reduce concurrency and preserve tasks for retry; never switch to a paid model.

## 7. Deterministic processing

chem-literature owns all scientific-document preprocessing: PDF/SI text extraction, page/layout indexing, table extraction, figure/table image extraction, SI parsing, evidence-index generation, and preprocessing QC.

Feature_create Python owns:
- ingestion of the upstream chem-literature preprocessed paired CSV (not independent corpus discovery);
- verification and hashing of the CSV-listed raw MAIN/SI files without parsing them;
- validation/indexing of upstream evidence_index, parsed text, structured tables, media assets, and QC;
- provenance indexing, page-aware text segmentation, original-row-preserving table chunking, bounded task packing, and task queue construction;
- scope-level coverage validation and aggregation across all chunks before a source/paper can be considered fully reviewed;
- exact staged-scope text hashing and quote verification so evidence cannot be borrowed from another chunk;
- stable ID allocation;
- Registry persistence and decision logs;
- schema validation;
- evidence-location and table-cell verification against the upstream page/table index;
- duplicate detection and consistency checks;
- checkpoints, locks, retries, and idempotent resume;
- final TXT serialization and audit counts.

A model may identify or interpret a scientific feature, value, or context. It cannot allocate persistent Registry IDs, claim unverified evidence is verified, or directly commit Registry mutations.

## 8. Registry lifecycle

A raw discovery is an immutable Observation. An Observation may initially be linked to a provisional Canonical Feature. Later reconciliation may confirm the candidate, link it to an existing Canonical Feature, leave it under review, split it from an incorrectly broad candidate, or exclude it as a condition/non-result item.

Canonical Feature IDs are stable internal identities. Merge/split history is retained. A merge creates a redirect from the retired ID to the surviving ID; a split creates new canonical identities and reassigns observations through an explicit decision event. No historical decision is silently erased.

## 9. Context model

Do not predefine a narrow universal list of experimental condition names. Store context as evidence-backed facts with:
- raw label;
- raw value;
- unit, if present;
- context role (sample identity, condition, time/cycle, protocol, statistical scope, or other);
- evidence locator.

## 10. Evidence model

Every accepted result value references one or more evidence locators. The locator should identify the source file and the most precise available position: page, section, paragraph, table, row, column, cell, figure, panel, caption, or digitization artifact.

Visual-only interpretation is not an exact numeric extraction. A plotted trend may support feature discovery and series identification without supporting precise result values.

## 11. Incremental execution

Each source and task has a deterministic identity derived from source hash, task type, contract version, and relevant configuration. Completed tasks are skipped on resume only when inputs and contract versions still match.

The Registry uses append-only decision events and atomic writes/locks. A crash must not leave half-written canonical state or duplicate IDs.

## 12. Deliverable boundary

Internal run files may include JSON/JSONL, evidence maps, checkpoints, metrics, and review queues. Formal output remains exactly:
- `result_feature_records.txt`
- `result_feature_list.txt`

The Feature dictionary must remain independent of which papers happen to have reported values in the current corpus.
