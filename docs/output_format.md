# Result Feature list output contract

## Scope

The only formal scientific deliverable of Feature_create is `result_feature_list.txt`.

It is a curated dictionary of selected Result Features, not a table of per-paper observations. Reported values, samples, experimental conditions, and protocols are populated by a separate downstream project.

## Entry fields

| Field | Meaning |
|---|---|
| `FEATURE_ID` | Existing compatible Feature ID when supplied; otherwise stable Registry Feature ID |
| `CANONICAL_NAME` | Preferred scientific name |
| `DEFINITION` | Concise scientific/operational definition |
| `ALIASES` | Confirmed equivalent names and abbreviations |
| `RESULT_TYPES` | Measured, calculated, derived, simulated, or other supported type |
| `UNIT_DIMENSIONS` | General dimension metadata when established, not paper-specific values |
| `AUTHOR_EMPHASIS` | Strongest author-attention signal among cited supporting Observations |
| `SELECTION_RATIONALE` | Why this Feature is representative and worth carrying downstream |

## Selection evidence

Each decision cites one or more Observation IDs belonging to the selected canonical Feature. Supporting Observations retain source evidence. Main-text discussion in the Abstract, Results, Discussion, or Conclusion is a strong positive signal. A figure/table-only Feature may still be selected when it adds a distinct scientific dimension.

## Exclusions

Do not include reported numerical/categorical values, paper-specific sample identities, experimental conditions, time points, protocols, per-paper rankings, or every plotted variable just because it appears in a figure.

Do not equate text frequency with scientific importance. Preserve meaningful distinctions and avoid duplicate aliases as separate Features.

## Deterministic export

`scripts/export_feature_list.py` validates the Registry and selection-decision contract, requires one decision for every active canonical Feature, checks that cited Observation IDs belong to that Feature, and writes selected entries in deterministic name/ID order. It does not modify the Registry.
