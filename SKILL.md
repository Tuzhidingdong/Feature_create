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
- chem-literature performs all PDF/SI preprocessing. Feature_create Python validates and indexes its preprocessed text, tables, figure assets, evidence index, QC status, and raw-source hashes; it does not parse PDFs or SI.

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

Worker A reviews one assigned evidence chunk at a time. It discovers raw result features, extracts individual result values, dynamically associates sample/context facts, and cites precise chunk/unit and original source locations.

### OpenCode Worker B — Feature-centric Discovery and Audit

Worker B independently reviews the same assigned chunk without seeing Worker A's first-pass output. It independently discovers the feature inventory, checks assigned figures/tables/SI for omissions, detects semantic distinctions, and submits evidence-backed omission candidates.

The two workers must run on different verified free models. The task contracts in `worker_prompts/` and `contracts/` are normative.

### Local deterministic Python layer

Python owns:
- source inventory and hashes; validation/indexing of upstream-preprocessed content; chunking of parsed text and task queue construction;
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

1. Require `research_corpus_paired.csv` from the merged [chem-literature main branch](https://github.com/Tuzhidingdong/chem-literature), not a harvesting-only CSV.
2. Require `preprocessing_path` and `preprocessing_status` on every selected CSV row.
3. Resolve `preprocessing_path`, `local_pdf_path`, and `local_si_info` against the upstream workflow working directory (CSV directory by default, or an explicit corpus root).
4. Treat CSV rows as the complete and authoritative paper list. Do not scan for extra papers, repeat article screening, or substitute unlisted files.
5. Validate the per-paper `01_preprocessing/` package, package version 1.1.0, `evidence_index.json`, `PREPROCESSING_QC_REPORT.md`, parsed text, table metadata, and referenced image assets.
6. Hash-check the CSV-listed raw MAIN/SI files against upstream inventory records. This is integrity verification only; raw files must not be parsed, unpacked, rendered, or attached to workers.
7. Require CSV `preprocessing_status` to match `evidence_index.processing_summary.status`. Block missing/corrupt packages and hash conflicts. Preserve scanned-page and unsupported-SI states as explicit warnings.
8. Record upstream CSV and evidence-index paths/hashes, row count, source IDs, package statuses, QC warnings, raw-source hashes, and asset hashes.
9. Read the supplied Feature Schema as a mapping reference only.
10. Refresh/check the available OpenCode model catalog.
11. Confirm Worker A and Worker B use two distinct exact OpenCode Go model references.
12. Verify both models are currently Free for input, output, and cached reads using the refreshed verbose model catalog. The published cached-write field may be a dash/not listed; reject any positive cached-write price.
13. Check deterministic task titles and ensure no helper-model default can select a paid model.
14. If cost/availability checks are inconclusive, stop before dispatching semantic work.

### Phase 1 — Index upstream preprocessing packages

1. Read only the rows in `research_corpus_paired.csv`.
2. Follow each row's `preprocessing_path` to its `01_preprocessing/` package. Keep `local_pdf_path` and `local_si_info` only as raw-source provenance and hash-integrity inputs.
3. Keep upstream DOI/title/year/journal/article type and all original CSV row metadata.
4. Read `main_analysis.md` and `si_analysis.md` exactly as created upstream.
5. Index structured tables from `evidence_index.json`, preserving table ID, document type, page number, bounding box, caption, and every row/cell.
6. Index figure captions and all upstream `media_assets`, preserving asset IDs, figure/table IDs, page number, bounding boxes, captions, extraction methods, dimensions, and hashes.
7. Preserve the complete page/block index, scan flags, SI references, supplementary-file inventory, processing summary, and QC warnings. Copy the complete upstream evidence index unchanged into the Feature_create run as the provenance authority; derive per-chunk locator slices from it without modifying the original.
8. Do not run PyMuPDF, OCR, GROBID, archive extraction, DOCX/XLSX parsing, page rendering, or any other document-preprocessing step in Feature_create.
9. Record missing, corrupt, inaccessible, scanned, or partially parsed upstream components. Do not infer successful review from file presence. Pair/hash failures block the paper; valid partial packages proceed only with explicit coverage warnings.

The internal `source_manifest.json` is a logical-source index derived from the CSV and upstream packages, not a new document parser. Each worker task receives only its assigned parsed text/table evidence units, related pre-extracted images, the `chunk_evidence_index.json` locator slice, a compact paper-wide `paper_chunk_index.json`, and the relevant QC report. The complete original upstream index remains in the prepared run and is not redundantly attached to every chunk. Raw PDF/SI files remain outside worker attachments.

#### Deterministic chunking and cross-chunk provenance

- Text is split at upstream page headings and paragraph/newline boundaries. Every source character is assigned once; page number and original source-text offsets remain attached to each unit.
- Tables are split by original 1-based source rows. A single oversized row is split into cell fragments while preserving original table ID, row, column, and character offsets. Multi-row headers remain available as context.
- An upstream image asset is assigned once to its related figure/table evidence unit when the evidence index identifies that relationship. Orphaned assets remain explicit units.
- Every unit has a stable `unit_id`; every task has a stable `chunk_id`. Every `(source_id, unit_id)` assignment also receives a stable `scope_id`, and the task bundle includes a coverage checklist. Worker evidence locators must cite `unit_id` and `chunk_id`, plus the original upstream locator. Context-only excerpts and repeated header rows are not counted as assigned coverage.
- `paper_chunk_index.json` exposes `unit_navigation` for every unit, `cross_chunk_text_boundaries` for adjacent text segments assigned to different chunks, `same_original_table_row_groups` for rows/cell fragments spanning chunks, and candidate MAIN-to-SI links. Candidate links are navigation aids, not confirmed semantic associations.
- If a sample/condition/result relationship crosses chunks, the worker must cite only what the assigned chunk supports and record the missing cross-chunk link as unresolved. It must never fill the gap by proximity or guesswork.
- Worker A and Worker B must each return exactly one coverage entry for every assigned `scope_id`, preserving the corresponding `source_id`. Repeated `source_id` values with different units require separate entries. A source is counted fully reviewed only when every one of its scopes across every chunk has the worker's successful status. A completed task/chunk cannot mark other scopes of that source complete.

### Phase 2 — Independent discovery and extraction

For each bounded evidence chunk (Worker A and Worker B receive independent copies of the same chunk):

- Worker A returns raw feature observations, individual result-value candidates, context facts, evidence references, coverage, and unresolved items.
- Worker B independently returns feature observations, coverage audits, omission candidates, semantic distinctions, and unresolved items.
- Neither worker receives the other's first-pass output.
- Existing schema entries may be used only as a post-discovery mapping reference; they must never become the discovery boundary.
- Run the two tasks with different free model IDs.

Do not collapse multiple samples, conditions, time points, cycles, or series into one result record. Keep each separately represented.

### Phase 3 — Deterministic validation

Python validates:
- payload structure and required fields;
- source, chunk, and assigned unit identity;
- quoted text within the exact assigned text range, not merely elsewhere in the same source;
- table values against exact original row/column/cell or oversized-cell character fragment, and confirmation that the cited row/cell belongs to the assigned unit;
- numeric parseability without changing the original reported string;
- units and context association;
- duplicate candidate records;
- actual model identity and cost metadata where exposed;
- unsupported or contradictory claims.

A model agreement is not evidence. Every accepted result value must have a traceable source. Values that cannot be reliably read from figures must not be represented as exact numbers.

### Phase 4 — Global Registry ingestion

1. Preserve raw observations from Worker A and Worker B.
2. Ingest each source observation into the persistent Registry with a stable Python ID.
3. Ingest Worker A's individual result-value records separately, including dynamic sample/context facts.
4. Preserve Worker B's omission candidates and within-paper semantic distinctions as review items.
5. Build candidate Feature pairs using permissive lexical, alias, acronym, unit, and definition signals. These signals only retrieve candidates; they never merge them.
6. Keep all new Canonical Features provisional until the global review stage resolves the candidate pairs.

### Phase 4A — Targeted cross-chunk context review

After Worker A/B first-pass outputs have been validated and ingested, resolve only those value-specific missing-context items that identify a concrete remote target unit.

1. Worker A may nominate at most eight target units in an unresolved item's `cross_chunk_target`. Each target must carry its exact `chunk_id`, `unit_id`, and `source_id`, plus a context role, relation basis, and short rationale.
2. Python verifies every nomination against the paper-wide `unit_navigation` index. For `adjacent_text_boundary` and `same_original_table_row`, it also verifies the exact cross-chunk navigation relation. A target from another paper, the current chunk, or an unindexed unit is blocked.
3. Build a minimal context-review queue containing only the current result evidence, the selected target evidence, and the existing result record's identifying/context fields. Do not attach the full paper or any Worker A/B output.
4. Route the task to the already-configured Worker B free model. Worker B receives a dedicated context-review prompt and must return exactly one of: `association_confirmed`, `no_association`, `insufficient_evidence`, or `conflicting_evidence`.
5. A confirmed context fact must cite both CURRENT and TARGET evidence, using exact assigned unit/chunk IDs and locally verifiable text or table-cell values. Target descriptors are navigation requests, never evidence.
6. `no_association` and `conflicting_evidence` also require verified text/table evidence from both sides. If either side cannot be verified, Worker B must return `insufficient_evidence`; lack of a citation is not proof that no association exists.
7. Run the evidence validator against the two-sided evidence manifest. Reject a fact if its quote/cell is outside the assigned range, its side/source/chunk/unit identity is wrong, or the exact content cannot be verified. The validation report records the SHA-256 of the exact worker response file; the applier refuses to apply a response that changed after validation.
8. The context applier may append only sample identity, condition, time/cycle, protocol, statistical-scope, or other context facts. It must not change the original result value, numeric value, Feature/Observation IDs, raw feature name, unit, result type, method, source ID, or first-pass evidence references.
9. When an identical context fact already exists, merge newly verified evidence references into that fact rather than duplicate the sample/condition entry. Repeated application of the same task is idempotent.
10. `no_association` closes that one investigated question without adding a fact. `insufficient_evidence` and `conflicting_evidence` remain unresolved for human review. All decisions and citations are retained in the unresolved item and linked result record history.

This stage reuses Worker B; it is not a third worker or a repeated full-paper discovery pass. The context queue skips already reviewed/resolved items by default; only inconclusive items may be explicitly retried with the retry-inconclusive switch. Confirmed/no-association items stay closed. Each explicit retry increments review_attempt and gets a fresh task ID, preventing an earlier completed session from being mistaken for a new review. Task IDs are also fingerprinted from the unresolved question, exact current/target content and locators, visual assets, model ID, and dedicated prompt; changed inputs cannot silently reuse a stale completed response. Response SHA-256 must still match the evidence-validation report at apply time. The source manifest, every assigned preprocessed text snapshot, two-sided evidence manifest, bundle, prompt, and every visual attachment are also SHA-256-bound at validation and rechecked before apply; an unregistered attachment or changed input blocks the update. The linked Result Record and unresolved item are separately snapshot-hashed at queue construction; if either changes before apply, the old context decision is rejected and must be reviewed against a rebuilt queue. A no_association decision requires positive counter-evidence and cannot be based only on a missing mention. Items without a linked result record and exact remote target remain in the unresolved queue.

### Phase 5 — Global cross-paper reconciliation

1. Build bounded pair-review tasks with source-backed Observation excerpts and relevant visual evidence.
2. Use Worker B's second free OpenCode Go model for cross-paper semantic comparison. This is the same Worker B role, not an additional model worker.
3. Compare physical/statistical quantity, definition, formula, numerator/denominator, normalization basis, statistic, temporal/cycle scope, dimensions, methods, and source contradictions.
4. Require an explicit equivalence assessment for each dimension. Missing information cannot be treated as affirmative equivalence.
5. Python applies a merge only when the decision is SAME_FEATURE and all deterministic evidence/compatibility gates pass.
6. Preserve separate features for RELATED_BUT_DISTINCT and SAME_FAMILY_DIFFERENT_STATISTIC decisions.
7. Keep insufficient/conflicting evidence under review; do not force a merge.
8. After a merge, invalidate and regenerate any other pair that references the retired ID.
9. Preserve all merge/split/redirect decisions in the append-only Registry history.

Worker B supplies semantic recommendations. Python validates source provenance, feature IDs, evidence coverage, and explicit merge gates, then performs the state transition. The Supervisor only coordinates this work.

### Phase 6 — Existing FEATURE_ID mapping

1. Import the user's existing Feature Schema into the normalized schema contract, preserving every existing ID and the source-file SHA-256.
2. Treat this schema strictly as a mapping reference; never use it to limit discovery or to define what is/is not a result Feature.
3. Build bounded mapping tasks for each confirmed Registry Feature. Include the full legacy candidate list in the review bundle so lexical pre-filtering cannot silently remove a valid synonym.
4. Use Worker B's already configured free OpenCode Go model to compare definitions, quantity, formula, normalization basis, statistic, temporal scope, and units.
5. Apply an old FEATURE_ID only when exactly one candidate is selected and every explicit compatibility gate passes, source evidence exists, and the old ID is not already assigned to another active Feature.
6. When there is no match, preserve the new Registry Feature ID. When evidence is insufficient or candidates conflict, do not force a mapping.
7. Record the source schema hash and every mapping/no-mapping decision in the Registry decision log.

### Phase 7 — Final global audit and export

Audit cross-paper consistency, feature coverage, evidence integrity, record completeness, unresolved cases, and paid-cost compliance.

Export exactly two formal TXT files:
- `result_feature_records.txt`
- `result_feature_list.txt`

The records file may contain paper identifiers, raw names, mapped feature IDs, reported values and units, result types, sample/context facts, protocols, evidence locators, extraction states, and unresolved questions.

The Feature-list file contains one entry per canonical result feature, with ID, canonical name, aliases, definition, result type, units/dimension, measurement/calculation methods, and standardization/comparability notes. It must not contain concrete values, Record_IDs, samples, conditions, paper coverage, or conclusions.

### Phase 8 — Report

Report counts calculated from local audit data:
- sources found, accessible, reviewed, partially reviewed, and inaccessible;
- papers and SI packages actually reviewed;
- result records extracted and evidence-verified;
- canonical features confirmed;
- unresolved records and feature candidates;
- unresolved context items, targeted cross-chunk context decisions, and unresolved context links remaining;
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
