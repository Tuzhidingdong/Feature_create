---
name: Feature_create
description: Select representative scientific result features from preprocessed papers using author-discussion evidence, lightweight semantic deduplication, and deterministic validation.
---

# Feature_create

## Mission

Select a **representative, scientifically meaningful set of Result Features** from the supplied preprocessed papers and supplementary information.

Feature_create is deliberately narrow:
- discover candidate outcome/result concepts;
- give strong weight to what authors substantively discuss in the main text;
- remove synonyms and redundant concepts while preserving genuinely different quantities;
- select a manageable set representing important result dimensions without requiring exhaustive coverage;
- export selected Feature definitions and their selection rationale.

Feature_create does **not** extract individual reported values, sample identities, experimental conditions, protocols, or value-to-condition associations. A separate downstream project fills those fields for the selected Features.

The formal scientific deliverable is `result_feature_list.txt`. Internal manifests, candidate observations, evidence references, and selection decisions support this deliverable; they are not additional scientific datasets.

A representative set is the goal, not complete coverage. Do not force a fixed number or percentage of Features, and do not select every item that appears in a figure, table, or SI.

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

### OpenCode Worker A — Result Feature Discovery

Worker A identifies scientifically meaningful result concepts and records author-emphasis labels supported by exact main-text passages. It does not extract result values or conditions.

### OpenCode Worker B — Independent Feature Review

Worker B independently checks for overlooked important concepts, especially results substantively discussed in the main text but easy to miss when attention is limited to figures/tables. It does not duplicate value extraction; its review is an accuracy check on the Feature inventory.

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

1. Require the complete `research_corpus_paired.csv` from the merged [chem-literature main branch](https://github.com/Tuzhidingdong/chem-literature), not a harvesting-only CSV. An optional sample may be created from this file before preparation.
2. For the selected input CSV (full or sampled), require `preprocessing_path` and `preprocessing_status` on every row.
3. Resolve `preprocessing_path`, `local_pdf_path`, and `local_si_info` against the upstream workflow working directory (CSV directory by default, or an explicit corpus root).
4. Treat the CSV supplied to the current run as its complete and authoritative paper list. Do not scan for extra papers, repeat article screening, or substitute unlisted files.
5. Validate the per-paper `01_preprocessing/` package, package version 1.1.0, `evidence_index.json`, `PREPROCESSING_QC_REPORT.md`, parsed text, table metadata, and referenced image assets.
6. Hash-check the CSV-listed raw MAIN/SI files against upstream inventory records. This is integrity verification only; raw files must not be parsed, unpacked, rendered, or attached to workers.
7. Require CSV `preprocessing_status` to match `evidence_index.processing_summary.status`. Block missing/corrupt packages and hash conflicts. Preserve scanned-page and unsupported-SI states as explicit warnings.
8. Record upstream CSV and evidence-index paths/hashes, row count, source IDs, package statuses, QC warnings, raw-source hashes, and asset hashes. When sampling was used, also verify and record the sampling manifest, seed, fraction, strata, selected-paper count, and sampled-CSV hash.
9. Read the supplied Feature Schema as a mapping reference only.
10. Refresh/check the available OpenCode model catalog.
11. Confirm Worker A and Worker B use two distinct exact OpenCode Go model references.
12. Verify both models are currently Free for input, output, and cached reads using the refreshed verbose model catalog. The published cached-write field may be a dash/not listed; reject any positive cached-write price.
13. Check deterministic task titles and ensure no helper-model default can select a paid model.
14. If cost/availability checks are inconclusive, stop before dispatching semantic work.

### Phase 0A — Optional deterministic paper-level sampling

For a very large chem-literature corpus, the user may choose to build the Feature tree from a reproducible subset instead of every preprocessed paper. This is an explicit reduction of the run's paper scope, not a new literature-screening or feature-selection step.

1. Run `scripts/sample_corpus.py` against the complete `research_corpus_paired.csv` before `prepare_corpus.py`.
2. Default to `--fraction 0.1` (10% of eligible unique papers) and `--stratify-by year`. The default seed is `feature-create-v1`; record any user-supplied seed.
3. Treat a normalized DOI as the paper identity. If DOI is missing, use normalized title + year + journal. Keep every CSV row that belongs to a selected paper.
4. Exclude rows with missing `preprocessing_path` or unsupported/failed preprocessing status before calculating the sample size. Report every excluded paper and reason in `sampling_manifest.json`.
5. Use the D'Hondt divisor method across configured strata, with deterministic per-paper SHA-256 ranking. The target count must be exact after half-up rounding, and the method must be house-monotone: increasing the fraction cannot reduce a stratum quota. The identical input bytes, fraction, seed, and stratification columns must reproduce the same selected identities.
6. Inspect the cumulative `sampled_research_corpus_paired.csv`, the `newly_selected_research_corpus_paired.csv`, and `sampling_manifest.json`. Do not edit these files.
7. When expanding an existing sample (for example, 10% to 20%), pass `--previous-manifest` and keep the exact source CSV bytes, seed, strata, and `min_papers` unchanged. The new fraction must be greater. The parent manifest and previous cumulative CSV must exist and match their hashes. The new CSV is cumulative; the incremental CSV contains only papers not previously selected.
8. Prepare a later round from its incremental CSV and the same round's manifest in a fresh prepared output directory. Reuse the persistent Registry and Result Record files during the subsequent reconciliation stage; do not dispatch the cumulative CSV again for an incremental run.
9. Pass the manifest to `prepare_corpus.py --sampling-manifest`. Preparation identifies cumulative vs incremental view, verifies the matching CSV hash and paper keys, and records the sampling lineage in `source_manifest.json`.
10. If the user chooses full-corpus processing, skip the sampler and omit `--sampling-manifest`. Never silently sample or silently expand a sample to the full corpus.

Sampling operates only on CSV metadata; it does not open raw PDFs, SI files, or preprocessed package contents. If increasing the fraction rounds to the same sample count, report `no_new_papers` and do not build/dispatch an empty incremental queue. Once a paper is selected, its entire upstream-preprocessed MAIN/SI text, tables, captions, evidence index, and related visual assets remain available to the normal downstream pipeline. It is not permissible to take 10% of the pages, tables, or evidence units from a selected paper.

Before preparation/queue construction, the Supervisor may run scripts/report_sampling_quality.py using the exact source CSV and that round's sampling manifest. Compare the **cumulative** sample against the eligible population at unique-paper level (default dimensions: year, journal, article_type, preprocessing_status). Inspect share differences, total variation distance, absent categories, and metadata conflicts. Treat this as descriptive metadata diagnostics only; it does not certify scientific/semantic coverage and must not modify the immutable manifest or sample. For a later incremental round, pass that round's cumulative sample, never its incremental-only CSV. See docs/sampling_quality.md.


### Phase 1 — Index upstream preprocessing packages

1. Read only the rows in the CSV selected for this run: the complete `research_corpus_paired.csv`, a hash-verified cumulative `sampled_research_corpus_paired.csv`, or a hash-verified `newly_selected_research_corpus_paired.csv` for an incremental expansion round.
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

### Phase 2 — Candidate Feature discovery

For each assigned evidence chunk:
- Worker A identifies candidate result/outcome concepts, definitions, source evidence, and the strongest author-emphasis signal.
- Worker B independently checks for missed important concepts and semantic distinctions.
- Main-text Abstract, Results, Discussion, and Conclusion are the primary importance evidence. Figures/tables/SI are supporting discovery sources.
- Worker outputs must not contain reported values or sample/condition/protocol records. Worker A keeps the legacy `result_records` field empty for contract compatibility.
- Existing schema entries are mapping references only; they must not restrict discovery.

### Phase 3 — Deterministic validation

Python validates worker payload structure, assigned source/chunk/unit identity, exact text quotes, original table/figure locators, evidence supporting each candidate's name/meaning, main-text evidence for substantive author-emphasis labels, candidate IDs, and result-vs-condition classification.

Numerical-value parsing, figure digitization, sample/condition association, and Result Record validation are not part of the active Feature_create workflow.

### Phase 4 — Candidate Registry ingestion

1. Preserve source Feature observations from both workers with author-emphasis labels.
2. Assign stable Observation and provisional Feature IDs in Python.
3. Keep source references and selection rationale with each Observation.
4. Build semantic candidate pairs using name/alias/definition signals; these retrieve candidates but never automatically merge them.
5. Do not create or populate per-paper Result Records. The downstream project owns value and condition extraction.

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

### Phase 7 — Representative Feature selection and export

After cross-paper reconciliation, make one global selection pass over active canonical Features. Use `worker_prompts/feature_selection.md` and `contracts/feature-selection.schema.json`.

Selection priorities:
1. **Author emphasis:** principal conclusions, mechanistic interpretation, comparisons, and substantive Results/Discussion passages.
2. **Scientific value and distinctiveness:** whether the Feature expresses an important outcome or distinct research dimension.
3. **Cross-paper recurrence:** supporting evidence, but not a substitute for scientific importance.
4. **Coverage contribution:** retain low-frequency Features representing an important otherwise-missing dimension.
5. **Redundancy:** avoid synonyms, near-duplicates, trivial variants, and unnecessary fine-grained splits.

A figure/table-only Feature may be selected when it adds a distinct scientific dimension. Frequency alone must never determine selection. Use `review` when evidence is ambiguous; do not force a yes/no decision.

Every decision cites supporting Observation IDs and gives a concise reason. The Supervisor reviews the shortlist and borderline decisions, not every underlying value.

Export only Features marked `select`:

```powershell
python scripts/export_feature_list.py `
  --registry "E:\\Zinc_batters_datas\\runs\\Feature_create\\global_feature_registry.json" `
  --decisions "E:\\Zinc_batters_datas\\runs\\Feature_create\\feature_selection_decisions.json" `
  --output "E:\\Zinc_batters_datas\\runs\\Feature_create\\output\\result_feature_list.txt"
```

The export contains Feature definitions and Feature-level metadata only. It must not contain reported values, paper-specific samples, experimental conditions, or protocols.

### Phase 8 — Report

Report concise counts: papers/chunks reviewed, candidate Features found, selected/excluded/review-needed Features, author-emphasis distribution, source-coverage warnings, worker model IDs, and usage/cost information where exposed. Never let a model invent counts.

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


## Optional post-run diagnostic: Feature discovery marginal yield

This is a separate, opt-in utility, not part of the Required workflow. Never invoke it automatically, require it for export, or use its output to block or cancel a later batch.

After multiple batches have been ingested into the same persistent Registry and the current semantic reconciliation has been applied, a user may explicitly run scripts/report_feature_saturation.py with the current Registry and one prepared source_manifest.json per batch in chronological order. For later sampled rounds, use each round's incremental prepared manifest, not its cumulative sample manifest.

The report compares newly observed current canonical result Features per 100 papers, uses the current Registry's merged identities, and records scope/coverage warnings. It does not produce a saturated/not-saturated verdict. Low marginal yield is not proof of complete discovery; inspect worker coverage, evidence validation, unresolved items, and pending reconciliation. The utility is read-only and does not dispatch workers or mutate Registry/Records. See docs/feature_discovery_saturation.md.
