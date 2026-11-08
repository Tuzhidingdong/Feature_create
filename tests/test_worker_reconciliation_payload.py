from __future__ import annotations

import unittest

from scripts.run_workers import validate_worker_payload


class ReconciliationWorkerPayloadTests(unittest.TestCase):
    def test_validates_registry_scoped_worker_b_payload_without_paper_id(self) -> None:
        task = {
            "task_id": "FC-R-001",
            "worker_role": "semantic_reconciliation",
            "registry_id": "REGISTRY-1",
            "candidate_pair_ids": ["PAIR-1"],
        }
        payload = {
            "contract_version": "0.1",
            "task_id": "FC-R-001",
            "task_type": "semantic_reconciliation",
            "registry_id": "REGISTRY-1",
            "pair_decisions": [{
                "candidate_pair_id": "PAIR-1",
                "registry_feature_a_id": "RFEAT-A",
                "registry_feature_b_id": "RFEAT-B",
                "relationship": "RELATED_BUT_DISTINCT",
                "recommended_action": "keep_separate",
                "rationale": "The candidates have different normalization bases.",
                "equivalence_assessment": {
                    "quantity_equivalent": True,
                    "definition_equivalent": False,
                    "formula_and_normalization_compatible": False,
                    "statistic_scope_compatible": True,
                    "temporal_scope_compatible": True,
                    "unit_dimension_compatible": True,
                    "no_contradictory_evidence": False,
                    "measurement_method_note": None,
                    "result_type_note": None,
                    "missing_information": [],
                },
                "compared_observation_ids": ["OBS-A", "OBS-B"],
                "evidence_refs": [
                    {"observation_id": "OBS-A", "source_id": "SRC-A", "locator": {"page": 1}},
                    {"observation_id": "OBS-B", "source_id": "SRC-B", "locator": {"page": 2}},
                ],
            }],
            "unresolved_items": [],
        }
        self.assertEqual(validate_worker_payload(payload, task), [])


if __name__ == "__main__":
    unittest.main()
