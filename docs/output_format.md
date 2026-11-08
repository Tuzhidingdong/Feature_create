# Formal TXT Output Contract

## 1. Scope

Every completed corpus run exports exactly two formal deliverables:

1. `result_feature_records.txt`
2. `result_feature_list.txt`

Internal JSON/JSONL files, Registry snapshots, checkpoints, evidence maps, logs, and audit reports are implementation data. They are not additional formal deliverables.

UTF-8 encoding is mandatory. Output ordering must be deterministic. The exporter must not rely on model-generated prose formatting.

## 2. result_feature_records.txt

This file contains individual reported result values. Each record represents one reported scalar value, one reported range, or one explicitly reported categorical/textual result. A time series, cycle series, sample series, or condition series must not be collapsed into a single record when its values can be represented separately.

### Required semantic fields

| Field | Meaning |
|---|---|
| `Record_ID` | Stable ID assigned by Python |
| `Paper_ID/DOI` | Stable paper identifier and DOI when available |
| `FEATURE_ID` | Canonical Feature ID; may be provisional when mapping remains unresolved |
| `RAW_FEATURE_NAME` | Exact variable label used by the source |
| `CANONICAL_NAME` | Canonical Feature name linked by the Registry |
| `RAW_VALUE` | Exact reported value string, preserving inequalities, ranges, symbols, and significant figures |
| `NUMERIC_VALUE` | Parsed numeric value only when the source reports an unambiguous single numeric value; otherwise null/not applicable |
| `REPORTED_UNIT` | Unit as reported by the source |
| `RESULT_TYPE` | measured, calculated, derived, simulated, model_output, not_stated, or uncertain |
| `SAMPLE_IDENTITY` | Evidence-backed sample/composition/variant identity facts |
| `CONDITION_FACTS` | Evidence-backed experimental condition facts |
| `TIME_CYCLE_FACTS` | Time, cycle, sequence, stage, and duration facts |
| `PROTOCOL_FACTS` | Evidence-backed preparation, testing, measurement, and analysis protocol facts |
| `MEASUREMENT_OR_CALCULATION_METHOD` | Method explicitly associated with the result |
| `SOURCE_TYPE` | Main text, table, figure, SI, dataset, or other |
| `SOURCE_LOCATION` | Precise locator: page/section/paragraph, table/row/column/cell, figure/panel/caption, or dataset sheet/range |
| `EVIDENCE_STATUS` | unverified, verified_text, verified_table_cell, verified_digitization, visual_only, unreadable, or not_applicable |
| `EXTRACTION_STATUS` | accepted, partial_context, value_unverified, visual_only, not_extractable, or under_review |
| `UNRESOLVED_ISSUES` | Explicit missing context, ambiguous association, or unresolved interpretation; empty only when none remain |

### Context representation

Sample identity, conditions, time/cycle facts, and protocol facts are separate groups. Each fact retains:
- source-reported label;
- source-reported value;
- unit when present;
- its own evidence locator.

Do not flatten all context into a single free-text field. Do not infer missing facts from common practice, another paper, or an assumed protocol. Use an explicit missing/unknown state when the output contract requires the field but the source does not report it.

### Numeric and figure rules

- Preserve the exact source string in `RAW_VALUE`.
- Populate `NUMERIC_VALUE` only when an unambiguous number is directly reported or reproducibly digitized.
- Do not convert a visual estimate into an exact number.
- A figure may support feature/series discovery even when its numeric values remain visual-only.
- A range remains a range; do not replace it with a midpoint.
- Preserve the source unit separately from any optional normalized unit.

## 3. result_feature_list.txt

This file contains one entry per canonical result Feature in the global Registry. It is a dictionary, not a list of observed values or papers.

### Required semantic fields

| Field | Meaning |
|---|---|
| `FEATURE_ID` | Stable ID; preserve an existing ID when semantically compatible |
| `CANONICAL_NAME` | Preferred result variable name |
| `ALIASES` | Confirmed equivalent raw names and abbreviations |
| `DEFINITION` | Operational/physical/statistical definition |
| `RESULT_TYPE` | Applicable result-type category or categories |
| `UNITS_AND_DIMENSION` | Reported/recognized units and physical dimension |
| `MEASUREMENT_METHODS` | Recognized methods used to measure the Feature |
| `CALCULATION_METHODS` | Recognized formulas or calculation procedures |
| `STANDARDIZATION_NOTES` | Naming, notation, unit, and normalization conventions |
| `COMPARABILITY_NOTES` | Important differences in definitions, statistics, methods, and scope that affect comparison |

### Strict exclusions

The Feature dictionary must not contain:
- concrete result values;
- Record_IDs;
- paper-specific sample identities;
- individual experimental conditions;
- per-paper source coverage or frequency statistics;
- paper-specific conclusions;
- rankings, feature selection, causal claims, or ML interpretations.

A method may be listed as a general method for the Feature only when it is supported by one or more source observations. Paper-specific method/value associations belong in the records file.

## 4. Exact TXT serialization (v0.1)

Both files are UTF-8 TSV (tab-separated text) despite the `.txt` extension. This keeps them plain-text and easy to read while allowing reliable parsing. Fields containing nested arrays/objects are serialized as compact UTF-8 JSON inside one TSV cell. The exporter uses standard CSV quoting with a tab delimiter and LF line endings. Embedded tabs, quotes, and newlines are therefore escaped by the TSV writer rather than by model-generated formatting.

### result_feature_records.txt — exact column order

```text
Record_ID
Paper_ID
DOI
FEATURE_ID
RAW_FEATURE_NAME
CANONICAL_NAME
RAW_VALUE
NUMERIC_VALUE
REPORTED_UNIT
RESULT_TYPE
SAMPLE_IDENTITY
CONDITION_FACTS
TIME_CYCLE_FACTS
PROTOCOL_FACTS
OTHER_CONTEXT_FACTS
MEASUREMENT_OR_CALCULATION_METHOD
SOURCE_TYPE
SOURCE_LOCATION
EVIDENCE_REFS
EVIDENCE_STATUS
EXTRACTION_STATUS
UNRESOLVED_ISSUES
```

The actual file contains these labels as one tab-separated header row, in exactly this order. Every subsequent row is one result record. The context and evidence columns are JSON arrays/objects; a missing optional scalar is an empty TSV cell, while an empty fact collection is the JSON array `[]`.

### result_feature_list.txt — exact column order

```text
FEATURE_ID
CANONICAL_NAME
ALIASES
DEFINITION
RESULT_TYPE
UNITS_AND_DIMENSION
MEASUREMENT_METHODS
CALCULATION_METHODS
STANDARDIZATION_NOTES
COMPARABILITY_NOTES
```

The actual file contains these labels as one tab-separated header row, in exactly this order. Each subsequent row is one canonical Feature. Aliases, result types, units/dimensions, and method lists are JSON cells. No paper/sample/result-value data may be added to this file.

### Export gate

The exporter refuses to create either formal TXT file when:
- any active Canonical Feature remains provisional, under review, or split-pending;
- a result record has no valid Feature ID or maps to an unconfirmed feature;
- a record ID is duplicated;
- a visual-only result contains an exact numeric value;
- a candidate Feature pair remains pending, blocked, or under review;
- the Registry fails its JSON Schema.

Retired merged IDs are resolved through their redirect to the surviving Feature ID. Raw Feature names and values remain unchanged in the records file. The internal audit JSON is written beside, not inside, the formal output directory. The formal output directory itself contains only the two TXT files above.

## 5. Serialization invariants

The exporter must:
- emit UTF-8 text with deterministic TSV headers, row ordering, quoting, and LF line endings;
- preserve Unicode symbols, inequalities, significant figures, ranges, and scientific notation;
- distinguish unavailable scalar fields (empty TSV cell) from empty collections (JSON `[]`);
- guarantee unique Record_ID values and one row per canonical FEATURE_ID;
- preserve every eligible result record and every confirmed canonical Feature exactly once;
- reject accidental mixing of record-level values into the Feature dictionary;
- never overwrite raw source strings with normalized/converted values.

Any parser-breaking format change requires an explicit contract-version change and migration. Field labels and semantic separation in this document must not be silently renamed.

## 6. Final counts

Run counts are reported separately in the run summary/log, not inserted into either TXT file. Counts must be computed from source inventory, validated records, Registry state, and unresolved queues.
