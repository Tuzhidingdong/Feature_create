from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.reconcile_features import (
    add_observation,
    build_feature_pair_queue,
    create_empty_registry,
    evidence_summary,
    make_record,
    validate_registry,
)


class RegistryIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.text_dir = self.root / "extracted_text"
        self.text_dir.mkdir()
        self.text_path = self.text_dir / "source.txt"
        self.text_path.write_text(
            "The Coulombic efficiency (CE) was 99.1% after 100 cycles.",
            encoding="utf-8",
        )
        self.source = {
            "source_id": "SRC-1",
            "paper_id": "PAPER-1",
            "source_type": "main_text",
            "extracted_text_path": "extracted_text/source.txt",
        }
        self.source_map = {"SRC-1": self.source}
        self.reference = {
            "source_id": "SRC-1",
            "source_type": "main_text",
            "locator": {"page": 4, "section": "Results"},
            "quote_or_cell_value": "Coulombic efficiency (CE) was 99.1%",
            "evidence_status": "verified_text",
        }
        self.task = {
            "task_id": "FC-A-001",
            "worker_role": "source_discovery",
            "model_ref": "opencode-go/crouton-alpha-free",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def candidate(self, candidate_id: str, name: str, definition: str) -> dict:
        return {
            "candidate_observation_id": candidate_id,
            "source_id": "SRC-1",
            "raw_feature_name": name,
            "raw_definition": definition,
            "reported_unit": "%",
            "reported_method": "galvanostatic cycling",
            "result_type": "measured",
            "variable_role": "result_feature",
            "evidence_refs": [self.reference],
        }

    def test_creates_provisional_feature_and_is_idempotent(self) -> None:
        registry = create_empty_registry()
        candidate = self.candidate("OBS-CAND-1", "Coulombic efficiency", "Discharge capacity / charge capacity × 100%")
        first = add_observation(
            registry, "PAPER-1", self.task, candidate, self.source_map, self.root
        )
        second = add_observation(
            registry, "PAPER-1", self.task, candidate, self.source_map, self.root
        )
        self.assertEqual(first[0], second[0])
        self.assertEqual(len(registry["observations"]), 1)
        self.assertEqual(len(registry["canonical_features"]), 1)
        self.assertEqual(registry["canonical_features"][0]["feature_status"], "provisional")
        self.assertEqual(registry["observations"][0]["discovery_status"], "evidence_verified")
        validate_registry(registry)

    def test_condition_only_observation_is_not_a_canonical_feature(self) -> None:
        registry = create_empty_registry()
        candidate = self.candidate("OBS-CAND-2", "Current density", "Applied current per electrode area")
        candidate["variable_role"] = "condition_only"
        observation_id, feature_id, _ = add_observation(
            registry, "PAPER-1", self.task, candidate, self.source_map, self.root
        )
        self.assertIsNone(feature_id)
        self.assertEqual(registry["observations"][0]["observation_id"], observation_id)
        self.assertEqual(registry["canonical_features"], [])
        validate_registry(registry)

    def test_claimed_verified_quote_must_exist_locally(self) -> None:
        reference = dict(self.reference)
        reference["quote_or_cell_value"] = "A quote that is not present in the source."
        result = evidence_summary([reference], self.source_map, self.root)
        self.assertFalse(result["locally_verified"])
        self.assertEqual(result["status"], "unverified")

    def test_context_difference_produces_distinct_record_ids(self) -> None:
        candidate = self.candidate("OBS-CAND-3", "Coulombic efficiency", "Discharge capacity / charge capacity × 100%")
        registry = create_empty_registry()
        observation_id, feature_id, _ = add_observation(
            registry, "PAPER-1", self.task, candidate, self.source_map, self.root
        )
        base = {
            "candidate_record_id": "REC-CAND-1",
            "candidate_observation_id": "OBS-CAND-3",
            "source_id": "SRC-1",
            "raw_value": "99.1",
            "numeric_value": 99.1,
            "value_extraction_status": "exact_text",
            "reported_unit": "%",
            "result_type": "measured",
            "sample_identity": [],
            "condition_facts": [],
            "time_cycle_facts": [],
            "protocol_facts": [],
            "other_context_facts": [],
            "evidence_refs": [self.reference],
        }
        first = make_record(
            {"paper_id": "PAPER-1", "doi": None},
            self.task,
            base,
            observation_id,
            feature_id,
            "Coulombic efficiency",
            "galvanostatic cycling",
            self.source_map,
            self.root,
        )
        second_candidate = dict(base)
        second_candidate["condition_facts"] = [{
            "raw_label": "Current density",
            "raw_value": "1 A g-1",
            "unit": None,
            "context_role": "condition",
            "evidence_refs": [self.reference],
        }]
        second = make_record(
            {"paper_id": "PAPER-1", "doi": None},
            self.task,
            second_candidate,
            observation_id,
            feature_id,
            "Coulombic efficiency",
            "galvanostatic cycling",
            self.source_map,
            self.root,
            unresolved_issues=[{
                "item_type": "missing_context",
                "description": "Current density was not reported for this value.",
                "evidence_refs": [self.reference],
                "suggested_review": None,
            }],
        )
        self.assertNotEqual(first["record_id"], second["record_id"])
        self.assertEqual(second["unresolved_issues"][0]["item_type"], "missing_context")

    def test_candidate_queue_detects_abbreviation_without_merging(self) -> None:
        registry = create_empty_registry()
        full_name = self.candidate(
            "OBS-CAND-FULL",
            "Coulombic efficiency",
            "Discharge capacity divided by charge capacity, multiplied by 100%.",
        )
        abbreviation = self.candidate(
            "OBS-CAND-ABBR",
            "CE",
            "Ratio of discharge capacity to charge capacity, expressed as a percentage.",
        )
        add_observation(registry, "PAPER-1", self.task, full_name, self.source_map, self.root)
        second_task = dict(self.task, task_id="FC-B-002", worker_role="feature_audit")
        add_observation(registry, "PAPER-2", second_task, abbreviation, self.source_map, self.root)
        with tempfile.TemporaryDirectory() as temp:
            queue_path = Path(temp) / "pairs.jsonl"
            summary = build_feature_pair_queue(registry, queue_path)
            self.assertEqual(summary["candidate_pairs_added"], 1)
            pair = queue_path.read_text(encoding="utf-8")
            self.assertIn("possible_acronym_alias", pair)
            self.assertEqual(len(registry["canonical_features"]), 2)
            self.assertNotEqual(
                registry["canonical_features"][0]["registry_feature_id"],
                registry["canonical_features"][1]["registry_feature_id"],
            )


if __name__ == "__main__":
    unittest.main()
