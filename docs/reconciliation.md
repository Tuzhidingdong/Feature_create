# Evidence-first Reconciliation Rules

## 1. Purpose

Reconciliation links paper-specific raw observations to stable canonical result features. It must maximize defensible coverage without increasing coverage by forcing uncertain merges.

## 2. Three identities

### Raw Feature Observation

An immutable, source-specific statement that a paper reports a particular result variable. It preserves the original name, wording, definition, unit, method, result type, and evidence locator.

### Canonical Feature

A corpus-wide concept representing one defined result variable. It may be linked to many Observations from different papers.

### Existing FEATURE_ID mapping

An optional mapping from a Canonical Feature to the user's existing feature schema. Existing IDs are reference targets, not the boundary of discovery.

## 3. Semantic equivalence criteria

Compare candidates using all available evidence:
- physical quantity or statistical quantity being reported;
- measurand and operational definition;
- mathematical formula;
- numerator, denominator, and normalization basis;
- statistic and aggregation rule (initial, average, maximum, minimum, retention, efficiency, etc.);
- temporal or cycle scope;
- unit and dimensionality;
- measurement, calculation, or simulation method;
- sample and protocol context where relevant.

Name similarity and unit equality are candidate-retrieval signals only. Neither is sufficient for a merge.

## 4. Relationship labels

Use exactly one primary relationship label for each compared candidate pair:

- `SAME_FEATURE`: definitions and measurement meaning are equivalent within the available evidence.
- `RELATED_BUT_DISTINCT`: physically or mathematically related, but not the same reported variable.
- `SAME_FAMILY_DIFFERENT_STATISTIC`: same broad quantity family, but different statistic, aggregation, or scope.
- `INSUFFICIENT_EVIDENCE`: evidence does not justify either equivalence or distinction.
- `NOT_A_RESULT_FEATURE`: item is not a result variable.
- `CONDITION_ONLY`: item is an experimental condition, protocol parameter, sample descriptor, or other context rather than a result variable.

Every semantic decision must include a rationale and references to supporting observations/evidence.

## 5. Automatic versus reviewed decisions

Python may automatically:
- normalize whitespace and Unicode presentation;
- normalize harmless spelling/case variants for candidate retrieval;
- parse units and dimensions;
- identify exact duplicated source entries;
- apply an already confirmed, versioned alias mapping.

Python must not automatically merge semantic features solely from string similarity, embedding similarity, matching dimensions, or matching units.

Model recommendations may be accepted automatically only under a conservative policy that requires:
1. explicit `SAME_FEATURE`;
2. no conflicting formula, statistic, basis, or temporal scope;
3. sufficient definitions and evidence for both candidates;
4. no unresolved contradictory observation;
5. a deterministic audit record of the decision.

Otherwise retain separate candidates and queue targeted review. When in doubt, prefer a temporary split over a false merge.

## 6. Existing schema mapping

Map to an existing FEATURE_ID only when the canonical definition and scope are compatible. A label match without semantic compatibility is not sufficient.

For genuinely new features, Python allocates a new ID in a reserved namespace after collision checks. Do not renumber pre-existing IDs.

## 7. Merge and split history

A merge:
- selects a surviving canonical ID deterministically;
- records the retired ID and redirect;
- preserves every Observation;
- appends a merge decision event.

A split:
- creates one or more new canonical IDs;
- reassigns affected Observations explicitly;
- preserves the prior decision and its rationale;
- appends a split decision event.

Never delete historical decisions or silently rewrite raw Observations.

## 8. Result versus context classification

A reported value belongs in the result records. Experimental conditions and sample descriptors belong in associated context facts. A variable measured or reported as an outcome may be a Feature; a test setting used to produce an outcome is normally context.

When role is ambiguous, keep it unresolved rather than silently promoting a condition to a result Feature or excluding a genuine outcome.

## 9. Figure handling

- `DIGITIZED`: numeric values extracted with reproducible axis calibration and a retained digitization artifact.
- `VISUAL_ONLY`: variable/series identifiable, but numeric values are not reliable enough to report as exact.
- `UNREADABLE`: the figure or relevant panel cannot be interpreted reliably.

A visually estimated coordinate must never be reported as an exact value. Caption/axis/legend evidence may still support discovery of a Feature without numeric extraction.

## 10. Quality states are orthogonal

Keep evidence verification, context completeness, and semantic mapping confidence separate. A result may have verified numeric evidence but incomplete sample context; a fully contextualized result may still have an unresolved Feature mapping.

Do not compress these distinctions into one generic confidence score.
