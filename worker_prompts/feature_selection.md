# Global representative Feature selection

## Purpose

Select a manageable, representative set of canonical Result Features from the reconciled Feature Registry. Feature_create does not extract values or conditions; a separate downstream project fills them for the selected Features.

## Evidence priority

The strongest positive signal is substantive author attention in the main text:
1. highlighted in a principal conclusion;
2. used in mechanistic interpretation;
3. compared across systems/materials or with prior work;
4. substantively discussed in Results/Discussion.

Inspect each Observation's `author_emphasis`, `selection_rationale`, and exact `evidence_refs`; do not trust a label without checking its evidence. Figure/table/SI-only observations are secondary, but remain eligible when they represent a distinct important scientific dimension.

Consider scientific meaning, distinctiveness, recurrence across independent papers, and contribution to the selected set. Frequency is supporting evidence, never the sole selection rule. Preserve scientifically important low-frequency Features. Exclude synonyms, redundant variants, incidental mentions, and items that are not result outcomes.

## Selection discipline

- Choose a representative set, not an exhaustive inventory.
- Do not impose an arbitrary percentage or Feature-count cap.
- Prefer distinct outcome dimensions over a long list of near-duplicates.
- Use `review` when the definition, evidence, or scientific role is unclear.
- Do not invent categories, values, units, conditions, or mechanisms.
- Every decision cites Observation IDs linked to that canonical Feature.
- Explain why a selected Feature belongs in the representative set.
- Explain exclusions (redundant, incidental, not an outcome, or low scientific value).
- Explain what remains uncertain for review decisions.

## Output

Return JSON conforming to `contracts/feature-selection.schema.json`. Include exactly one decision for every active canonical Feature in the supplied Registry, excluding only Features marked `retired_merged`. Use `select`, `exclude`, or `review`. Do not include reported values, sample data, experimental conditions, or protocols. No prose outside JSON.
