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

## Simple thematic organization

For each Feature marked `select`, assign exactly one primary category. Choose the category that best describes the Feature's scientific role, not the paper's topic or experimental method. Do not invent a category and do not force a precise assignment when the concept spans categories or evidence is thin.

Use only these categories:
- `performance`: overall function, capability, efficiency, or application-level performance;
- `material_property`: physical or chemical properties of a material/system (e.g. capacity, conductivity, strength, solubility);
- `structure_composition`: reported structure, phase, morphology, composition, defects, or chemical-state outcomes;
- `kinetics_mechanism`: rates, activation behavior, reaction pathways, or mechanistic descriptors;
- `stability_durability`: degradation, retention, lifetime, cycling durability, or long-term stability;
- `environmental_process`: removal/degradation outcomes, emissions, resource/energy use, or process-level outcomes;
- `other`: a clearly identified result that does not fit the groups above;
- `unclassified`: use when the primary category cannot be assigned confidently.

A Feature receives one primary category only. Do not duplicate a Feature in multiple groups. A brief `category_rationale` is required for every selected Feature. The same physical quantity can be grouped differently only when its scientific role in the source differs; explain the choice.

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

Return JSON conforming to `contracts/feature-selection.schema.json`. Include exactly one decision for every active canonical Feature in the supplied Registry, excluding only Features marked `retired_merged`. Use `select`, `exclude`, or `review`. Selected decisions must include `primary_category` and `category_rationale`. Do not include reported values, sample data, experimental conditions, or protocols. No prose outside JSON.
