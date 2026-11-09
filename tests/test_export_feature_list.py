from __future__ import annotations

import unittest

from scripts.export_feature_list import FeatureExportError, render_feature_list, validate_selection
from scripts.reconcile_features import create_empty_registry


class ExportFeatureListTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = create_empty_registry()
        self.registry["observations"] = [{
            "observation_id": "OBS-1", "paper_id": "PAPER-1", "source_id": "SRC-1",
            "raw_feature_name": "Coulombic efficiency", "raw_definition": "Discharge/charge capacity",
            "reported_unit": "%", "reported_method": None, "result_type": "measured",
            "variable_role": "result_feature", "author_emphasis": "core_conclusion",
            "selection_rationale": "Highlighted in the conclusion.", "evidence_refs": [],
            "origin_task_id": "TASK-1", "origin_worker": "source_discovery",
            "discovery_status": "evidence_verified", "linked_registry_feature_id": "RFEAT-1",
            "created_at": None,
        }]
        self.registry["canonical_features"] = [{
            "registry_feature_id": "RFEAT-1", "canonical_name": "Coulombic efficiency",
            "definition": "Discharge capacity divided by charge capacity.", "semantic_signature": {},
            "aliases": ["CE"], "unit_dimensions": ["dimensionless"], "measurement_methods": [],
            "calculation_methods": [], "result_types": ["measured"], "observation_ids": ["OBS-1"],
            "existing_feature_id": None, "feature_status": "confirmed", "redirect_to": None,
            "created_at": None, "updated_at": None,
        }]

    def decisions(self, decision: str = "select") -> dict:
        return {
            "contract_version": "0.1", "registry_id": self.registry["registry_id"],
            "decisions": [{
                "registry_feature_id": "RFEAT-1", "decision": decision,
                "selection_rationale": "A central outcome explicitly emphasized by the paper.",
                "supporting_observation_ids": ["OBS-1"],
                "primary_category": "performance",
                "category_rationale": "Directly represents the battery's reported functional performance.",
            }],
        }

    def test_exports_only_selected_feature_definition(self) -> None:
        mapping = validate_selection(self.registry, self.decisions())
        content, summary = render_feature_list(self.registry, mapping)
        self.assertIn("CANONICAL_NAME: Coulombic efficiency", content)
        self.assertIn("AUTHOR_EMPHASIS: core_conclusion", content)
        self.assertNotIn("RAW_VALUE", content)
        self.assertNotIn("CONDITION_FACTS", content)
        self.assertEqual(summary["selected"], 1)

    def test_features_are_grouped_in_fixed_category_order(self) -> None:
        self.registry["observations"].append({
            **self.registry["observations"][0],
            "observation_id": "OBS-2",
            "raw_feature_name": "Crystal phase",
            "linked_registry_feature_id": "RFEAT-2",
        })
        self.registry["canonical_features"].append({
            **self.registry["canonical_features"][0],
            "registry_feature_id": "RFEAT-2",
            "canonical_name": "Crystal phase",
            "definition": "Crystalline phase identified by diffraction.",
            "aliases": [],
            "observation_ids": ["OBS-2"],
        })
        decisions = self.decisions()
        decisions["decisions"].append({
            "registry_feature_id": "RFEAT-2",
            "decision": "select",
            "selection_rationale": "A distinct structural outcome.",
            "supporting_observation_ids": ["OBS-2"],
            "primary_category": "structure_composition",
            "category_rationale": "Describes the reported crystalline phase.",
        })
        mapping = validate_selection(self.registry, decisions)
        content, summary = render_feature_list(self.registry, mapping)
        self.assertLess(content.index("CATEGORY_CODE: performance"), content.index("CATEGORY_CODE: structure_composition"))
        self.assertIn("CATEGORY: Structure and composition", content)
        self.assertEqual(summary["selected"], 2)

    def test_selected_feature_requires_a_category(self) -> None:
        decisions = self.decisions()
        del decisions["decisions"][0]["primary_category"]
        with self.assertRaises(FeatureExportError):
            validate_selection(self.registry, decisions)

    def test_requires_one_decision_for_every_active_feature(self) -> None:
        decisions = self.decisions()
        decisions["decisions"] = []
        with self.assertRaises(FeatureExportError):
            validate_selection(self.registry, decisions)

    def test_rejects_support_observation_not_linked_to_feature(self) -> None:
        decisions = self.decisions()
        decisions["decisions"][0]["supporting_observation_ids"] = ["OBS-OTHER"]
        with self.assertRaises(FeatureExportError):
            validate_selection(self.registry, decisions)

    def test_rejects_empty_selected_output(self) -> None:
        with self.assertRaises(FeatureExportError):
            validate_selection(self.registry, self.decisions("exclude"))


if __name__ == "__main__":
    unittest.main()
