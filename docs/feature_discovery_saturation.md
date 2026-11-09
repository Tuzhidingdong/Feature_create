# Optional Feature Discovery Saturation Diagnostics

## Status and workflow policy

This is an **optional, manually invoked diagnostic**. It is not a required phase of Feature_create, is not run automatically after a batch, and does not block preparation, ingestion, reconciliation, export, or later batches.

It does not decide that the corpus is saturated. It reports the marginal number of newly observed canonical result Features as more paper batches are added. The user decides whether another batch is worthwhile.

## When it is useful

Use it after at least two batches have been ingested into the same persistent Global Feature Registry and the relevant semantic reconciliation has been applied. Three or more batches provide a more useful descriptive trend.

It can help answer questions such as:

- How many current canonical result Features were first observed in each batch?
- Is the number of newly observed Features per 100 papers declining?
- How many papers in each batch have no ingested result-feature Observation?
- Are provisional, under-review, or split-pending Features still present?

A falling marginal yield is only a signal to inspect. It does not establish that rare or domain-specific Features have been found.

## Inputs and batch order

The tool reads:
1. The current persistent global_feature_registry.json.
2. One prepared source_manifest.json for each batch, supplied in chronological order.

For an incremental sampling workflow, the first entry can be the first-round prepared manifest (for example, the 10% batch). Later entries must be manifests prepared from the newly-selected incremental CSV for each later round (for example, round-20-incremental and round-30-incremental). Do not pass a later cumulative sample as a new batch: the tool rejects overlapping paper IDs.

The batch order is the order of the repeated --batch arguments. The tool does not infer chronology from timestamps. Keep the same Registry and Result Record paths across batches, as required by the normal incremental workflow.

The report resolves each Observation's linked Registry Feature ID through the **current** Registry. Thus, semantic merges already applied to the Registry are reflected in historical batch counts: two provisional candidates later confirmed as the same Feature are not counted as two distinct canonical Features. A later merge or split can retroactively change which batch first contains a current canonical Feature.

## Run

Example after the 10%, 20%, and 30% batches have been prepared and reconciled:

~~~powershell
python scripts/report_feature_saturation.py --registry "E:\Zinc_batters_datas\runs\Feature_create\global_feature_registry.json" --batch "round-10=E:\Zinc_batters_datas\runs\Feature_create\prepared\round-10\source_manifest.json" --batch "round-20-incremental=E:\Zinc_batters_datas\runs\Feature_create\prepared\round-20-incremental\source_manifest.json" --batch "round-30-incremental=E:\Zinc_batters_datas\runs\Feature_create\prepared\round-30-incremental\source_manifest.json" --output-dir "E:\Zinc_batters_datas\runs\Feature_create\diagnostics\feature-saturation"
~~~

The output directory contains:

- feature_saturation_report.json — machine-readable counts, rates, current Feature IDs, input hashes, warnings, and limitations;
- feature_saturation_report.md — a human-readable batch comparison.

A single batch can be reported to establish a baseline, but no across-batch trend can be calculated. The tool recommends using at least three batches for trend interpretation; this is guidance, not a workflow requirement.

## Reported metrics

For each batch:

- prepared paper count, ready/blocked paper counts;
- papers with any Registry Observation and papers with a result-feature Observation;
- all Registry Observations and result-feature Observations;
- evidence-verified result-feature Observations;
- uncertain-role Observations;
- unique current canonical result Features observed in that batch;
- new current canonical result Features first observed in that batch;
- new canonical Features per 100 papers;
- current Feature-status counts for the newly observed Features;
- cumulative papers, Observations, and unique current canonical result Features.

The last batch's marginal rate is compared with the pooled marginal rate of all preceding batches. This is a descriptive ratio and percentage change, not a pass/fail threshold.

Condition-only and sample-descriptor Observations do not count as result Features. Uncertain-role Observations are reported separately and do not inflate the result-Feature yield.

## Integrity and scope checks

The report:

- acquires the same Registry lock used by reconciliation/export while reading the Registry;
- fingerprints the Registry and each prepared batch manifest;
- rejects duplicate paper IDs within a batch or overlapping paper IDs across batches;
- verifies Observation-to-Feature links, redirect chains, and Feature observation backlinks;
- fails by default when the Registry contains papers absent from all supplied batch manifests.

If the Registry contains unrelated papers outside the selected sequence and the user explicitly wants a partial-scope report, add --allow-unassigned. The report will mark the number of unassigned Registry papers and warn that cumulative values cover only supplied batches. Do not use this switch when the intention is to claim a corpus-wide trend.

The diagnostic is read-only. It does not update the Registry, Result Records, source manifests, sampling manifests, worker outputs, or any selection decision.

## Interpretation limits

A low marginal yield can arise from incomplete extraction, missing/inaccessible sources, evidence-validation gaps, or unresolved semantic reconciliation—not only from genuine discovery saturation. The source manifest's ready/blocked status is not a complete audit of Worker A/B coverage. Inspect the normal task queue, worker run, evidence validation, unresolved-item, and reconciliation reports before drawing conclusions.

The current Registry may still contain provisional, under-review, or split-pending Features. Further reconciliation can change the canonical identity counts. Rare scientific Features may remain undiscovered even when the overall marginal yield is low.

No automatic saturation verdict is produced. No later batch is skipped, cancelled, or blocked by this report.
