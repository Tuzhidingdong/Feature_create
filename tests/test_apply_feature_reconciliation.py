from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.apply_feature_reconciliation import (
    apply_merge,
    merge_gate,
    refresh_feature_statuses,
)


class ApplyFeatureReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.obs_a = {
            "observation_id": "OBS-A",
            "paper_id": "PAPER-A",
            "source_id": "SRC-A",
            "raw_feature_name": "Coulombic efficiency",
            "reported_unit": "%",
            "discovery_status": "evidence_verified",
            "linked_registry_feature_id": "RFEAT-A",
            "evidence_refs": [{
                "source_id": "SRC-A",
                "locator": {"page": 2, "section": "Results"},
                "evidence_status": "verified_text",
            }],
        }
        self.obs_b = {
            "observation_id": "OBS-B",
            "paper_id": "PAPER-B",
            "source_id": "SRC-B",
            "raw_feature_name": "CE",
            "reported_unit": "%",
            "discovery_status": "evidence_verified",
            "linked_registry_feature_id": "RFEAT-B",
            "evidence_refs": [{
                "source_id": "SRC-B",
                "locator": {"page": 5, "section": "Cycling"},
                "evidence_status": "verified_text",
            }],
        }
        signature = {
            "physical_or_statistical_quantity": "coulombic efficiency",
            "measurand_or_operational_definition": "discharge capacity / charge capacity",
            "formula": "Q_discharge/Q_charge × 100%",
            "numerator": "discharge capacity",
            "denominator": "charge capacity",
            "normalization_basis": None,
            "statistic_or_aggregation": "per-cycle",
            "temporal_or_cycle_scope": "per cycle",
            "unit_dimension": "dimensionless ratio",
            "method_scope": "galvanostatic cycling",
        }
        self.feature_a = {
            "registry_feature_id": "RFEAT-A",
            "canonical_name": "Coulombic efficiency",
            "definition": "Discharge capacity divided by charge capacity, multiplied by 100%.",
            "semantic_signature": dict(signature),
            "aliases": ["Coulombic efficiency"],
            "unit_dimensions": [],
            "measurement_methods": ["galvanostatic cycling"],
            "calculation_methods": [],
            "result_types": ["measured"],
            "observation_ids": ["OBS-A"],
            "existing_feature_id": None,
            "feature_status": "provisional",
            "redirect_to": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        self.feature_b = {
            **self.feature_a,
            "registry_feature_id": "RFEAT-B",
            "canonical_name": "CE",
            "definition": "Ratio of discharge capacity to charge capacity, expressed as a percentage.",
            "semantic_signature": dict(signature),
            "aliases": ["CE"],
            "observation_ids": ["OBS-B"],
            "created_at": "2026-02-01T00:00:00+00:00",
        }
        self.registry = {
            "contract_version": "0.1",
            "registry_id": "REG-1",
            "next_observation_sequence": 3,
            "next_feature_sequence": 3,
            "observations": [self.obs_a, self.obs_b],
            "canonical_features": [self.feature_a, self.feature_b],
            "decision_log": [],
        }
        self.pair = {
            "candidate_pair_id": "PAIR-1",
            "registry_feature_a_id": "RFEAT-A",
            "registry_feature_b_id": "RFEAT-B",
            "feature_a": {"observation_ids": ["OBS-A"]},
            "feature_b": {"observation_ids": ["OBS-B"]},
        }
        self.decision = {
            "candidate_pair_id": "PAIR-1",
            "registry_feature_a_id": "RFEAT-A",
            "registry_feature_b_id": "RFEAT-B",
            "relationship": "SAME_FEATURE",
            "recommended_action": "merge_candidates",
            "rationale": "Both definitions describe the per-cycle discharge-to-charge capacity ratio.",
            "equivalence_assessment": {
                "quantity_equivalent": True,
                "definition_equivalent": True,
                "formula_and_normalization_compatible": True,
                "statistic_scope_compatible": True,
                "temporal_scope_compatible": True,
                "unit_dimension_compatible": True,
                "no_contradictory_evidence": True,
                "measurement_method_note": "Both are calculated from galvanostatic cycling.",
                "result_type_note": "Both are measured/calculated outcomes.",
                "missing_information": [],
            },
            "compared_observation_ids": ["OBS-A", "OBS-B"],
            "evidence_refs": [
                {
                    "observation_id": "OBS-A",
                    "source_id": "SRC-A",
                    "locator": {"page": 2, "section": "Results"},
                    "quote_or_cell_value": "CE was 99.1%",
                },
                {
                    "observation_id": "OBS-B",
                    "source_id": "SRC-B",
                    "locator": {"page": 5, "section": "Cycling"},
                    "quote_or_cell_value": "Coulombic efficiency was 98.9%",
                },
            ],
        }

    def test_all_gates_allow_same_unit_evidence_backed_merge(self) -> None:
        allowed, reasons = merge_gate(
            self.decision, self.pair, self.registry,
            {item["observation_id"]: item for item in self.registry["observations"]},
        )
        self.assertTrue(allowed, reasons)

    def test_mismatched_units_block_merge_even_when_model_says_same(self) -> None:
        self.obs_b["reported_unit"] = "mAh g-1"
        allowed, reasons = merge_gate(
            self.decision, self.pair, self.registry,
            {item["observation_id"]: item for item in self.registry["observations"]},
        )
        self.assertFalse(allowed)
        self.assertTrue(any("unit strings do not match" in reason for reason in reasons))

    def test_missing_verified_observation_blocks_merge(self) -> None:
        self.obs_b["discovery_status"] = "candidate"
        allowed, reasons = merge_gate(
            self.decision, self.pair, self.registry,
            {item["observation_id"]: item for item in self.registry["observations"]},
        )
        self.assertFalse(allowed)
        self.assertTrue(any("no locally evidence-verified" in reason for reason in reasons))

    def test_missing_evidence_for_one_side_blocks_merge(self) -> None:
        self.decision["evidence_refs"] = self.decision["evidence_refs"][:1]
        allowed, reasons = merge_gate(
            self.decision, self.pair, self.registry,
            {item["observation_id"]: item for item in self.registry["observations"]},
        )
        self.assertFalse(allowed)
        self.assertTrue(any("no source evidence reference for Feature B" in reason for reason in reasons))

    def test_merge_preserves_aliases_observations_and_redirect(self) -> None:
        survivor_id, retired_id = apply_merge(
            self.registry, self.feature_a, self.feature_b, self.decision
        )
        self.assertEqual(survivor_id, "RFEAT-A")
        self.assertEqual(retired_id, "RFEAT-B")
        self.assertIn("CE", self.feature_a["aliases"])
        self.assertEqual(set(self.feature_a["observation_ids"]), {"OBS-A", "OBS-B"})
        self.assertEqual(self.feature_b["feature_status"], "retired_merged")
        self.assertEqual(self.feature_b["redirect_to"], survivor_id)
        self.assertEqual(self.obs_b["linked_registry_feature_id"], survivor_id)

    def test_unverified_feature_without_open_pairs_does_not_become_confirmed(self) -> None:
        self.obs_a["discovery_status"] = "candidate"
        review = []
        refresh_feature_statuses(self.registry, review)
        self.assertEqual(self.feature_a["feature_status"], "under_review")

    def test_pending_pairs_keep_features_under_review(self) -> None:
        review = [{
            "candidate_pair_id": "PAIR-1",
            "registry_feature_a_id": "RFEAT-A",
            "registry_feature_b_id": "RFEAT-B",
            "decision_status": "under_review",
        }]
        refresh_feature_statuses(self.registry, review)
        self.assertEqual(self.feature_a["feature_status"], "under_review")
        self.assertEqual(self.feature_b["feature_status"], "under_review")


if __name__ == "__main__":
    unittest.main()
