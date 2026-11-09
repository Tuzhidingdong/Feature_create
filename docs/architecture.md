# Feature_create — focused architecture

## Purpose

Feature_create selects a representative set of scientific Result Features from papers already preprocessed by chem-literature.

Its responsibility ends at the Feature definition/list. It does not fill reported values, identify per-paper samples, or extract experimental conditions and protocols. Those belong to a separate downstream project.

## Flow

```text
chem-literature preprocessing
          |
          v
input validation and source indexing
          |
          v
candidate Result Feature discovery
          |
          +---- author emphasis in Abstract / Results / Discussion / Conclusion
          |
          +---- supporting evidence from tables, figures, captions, and SI
          |
          v
independent omission / distinction review
          |
          v
cross-paper semantic deduplication
          |
          v
representative Feature selection
          |
          v
single primary category assignment
          |
          v
result_feature_list.txt (grouped in fixed category order)
          |
          v
separate downstream project: fill values + conditions
```

## Selection principles

1. Substantive main-text author discussion is a strong importance signal.
2. Principal conclusions, mechanistic interpretation, and comparisons are stronger than incidental mentions.
3. Figure/table-only Features remain eligible when scientifically distinctive or needed to represent an important result dimension.
4. Repeated mentions of one concept do not create multiple Features.
5. Cross-paper recurrence supports selection but is not a frequency-only ranking.
6. Important low-frequency Features may be retained.
7. The set should be manageable and representative, not exhaustive; no fixed percentage/count is required.

## Implementation boundaries

- chem-literature owns PDF/SI preprocessing.
- Feature_create indexes and validates upstream packages; it does not render or re-parse source PDFs.
- Python handles stable IDs, source locators, schema/evidence validation, safe Registry updates, and deterministic output.
- Workers discover and assess semantic Features; they must not invent source evidence.
- Corpus sampling and sampling/marginal-yield reports are optional diagnostics.


## Simple Feature categories

Every selected Feature receives one primary category: performance, material_property, structure_composition, kinetics_mechanism, stability_durability, environmental_process, other, or unclassified. Category assignment is explanatory organization, not a scientific ranking. A brief evidence-grounded rationale is required; uncertain cases remain unclassified. No multi-label tagging or inferred hierarchy is used.
