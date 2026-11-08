from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.apply_existing_feature_mapping import mapping_gate
from scripts.build_schema_mapping_queue import main


class ExistingFeatureMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.observation = {
            "observation_id": "OBS-1",
            "paper_id": "PAPER-1",
            "source_id": "SRC-1",
            "raw_feature_name": "Coulombic efficiency",
            "raw_definition": "Discharge capacity divided by charge capacity",
            "reported_unit": "%",
            "reported_method": "galvanostatic cycling",
            "result_type": "measured",
            "discovery_status": "evidence_verified",
        }
        self.feature = {
            "registry_feature_id": "RFEAT-1",
            "canonical_name": "Coulombic efficiency",
            "definition": "Discharge capacity divided by charge capacity, multiplied by 100%.",
            "observation_ids": ["OBS-1"],
            "feature_status": "confirmed",
        }
        self.old_feature = {
            "FEATURE_ID": "F001",
            "CANONICAL_NAME": "Coulombic efficiency",
            "ALIASES": ["CE"],
            "DEFINITION": "Ratio of discharge capacity to charge capacity expressed as a percentage.",
            "RESULT_TYPE": ["measured", "calculated"],
            "UNITS_AND_DIMENSION": {
                "reported_units": ["%"],
                "dimensions": ["dimensionless ratio"],
            },
            "MEASUREMENT_METHODS": ["galvanostatic cycling"],
            "CALCULATION_METHODS": [],
        }
        self.decision = {
            "registry_feature_id": "RFEAT-1",
            "mapping_status": "MAP_TO_EXISTING",
            "existing_feature_id": "F001",
            "rationale": "Both definitions are the same per-cycle discharge/charge capacity ratio.",
            "mapping_assessment": {
                "quantity_equivalent": True,
                "definition_equivalent": True,
                "formula_and_normalization_compatible": True,
                "statistic_scope_compatible": True,
                "temporal_scope_compatible": True,
                "unit_dimension_compatible": True,
                "no_contradictory_evidence": True,
                "missing_information": [],
            },
            "compared_observation_ids": ["OBS-1"],
        }

    def test_all_compatibility_gates_allow_mapping(self) -> None:
        allowed, reasons = mapping_gate(
            self.decision,
            self.feature,
            self.old_feature,
            {"OBS-1": self.observation},
        )
        self.assertTrue(allowed, reasons)

    def test_unit_conflict_blocks_mapping(self) -> None:
        self.observation["reported_unit"] = "mAh g-1"
        allowed, reasons = mapping_gate(
            self.decision,
            self.feature,
            self.old_feature,
            {"OBS-1": self.observation},
        )
        self.assertFalse(allowed)
        self.assertTrue(any("units do not match" in reason for reason in reasons))

    def test_unverified_source_blocks_mapping(self) -> None:
        self.observation["discovery_status"] = "candidate"
        allowed, reasons = mapping_gate(
            self.decision,
            self.feature,
            self.old_feature,
            {"OBS-1": self.observation},
        )
        self.assertFalse(allowed)
        self.assertTrue(any("evidence-verified" in reason for reason in reasons))

    def test_builds_worker_b_queue_with_all_existing_schema_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepared = root / "prepared"
            out = root / "mapping"
            extracted = prepared / "extracted_text"
            extracted.mkdir(parents=True)
            (extracted / "paper.txt").write_text(
                "Coulombic efficiency (CE) was 99.1% after cycling.",
                encoding="utf-8",
            )
            manifest = {
                "sources": [{
                    "source_id": "SRC-1",
                    "paper_id": "PAPER-1",
                    "absolute_path": str(prepared / "paper.md"),
                    "extracted_text_path": "extracted_text/paper.txt",
                    "source_type": "main_text",
                    "visual_pages": [],
                }]
            }
            registry = {
                "registry_id": "REG-1",
                "observations": [{
                    **self.observation,
                    "source_id": "SRC-1",
                    "evidence_refs": [{
                        "source_id": "SRC-1",
                        "source_type": "main_text",
                        "locator": {"page": 1},
                        "quote_or_cell_value": "Coulombic efficiency (CE) was 99.1%",
                        "evidence_status": "verified_text",
                    }],
                }],
                "canonical_features": [{
                    **self.feature,
                    "measurement_methods": ["galvanostatic cycling"],
                    "calculation_methods": [],
                    "semantic_signature": {},
                    "aliases": ["CE"],
                    "unit_dimensions": [],
                    "result_types": ["measured"],
                    "existing_feature_id": None,
                }],
            }
            old_schema = {
                "contract_version": "0.1",
                "source_file": "legacy.txt",
                "source_sha256": "a" * 64,
                "source_format": "text_blocks",
                "features": [self.old_feature],
            }
            manifest_path = prepared / "source_manifest.json"
            registry_path = prepared / "registry.json"
            schema_path = prepared / "existing_schema.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            schema_path.write_text(json.dumps(old_schema), encoding="utf-8")

            code = main([
                "--registry", str(registry_path),
                "--existing-schema", str(schema_path),
                "--manifest", str(manifest_path),
                "--output", str(out),
            ])
            self.assertEqual(code, 0)
            queue = json.loads((out / "existing_schema_mapping_queue.json").read_text(encoding="utf-8"))
            self.assertEqual(queue["task_count"], 1)
            task = queue["tasks"][0]
            self.assertEqual(task["worker_role"], "existing_schema_mapping")
            self.assertEqual(task["model_ref"], "opencode-go/beam-free")
            bundle = json.loads(Path(task["bundle_path"]).read_text(encoding="utf-8"))
            self.assertEqual(len(bundle["mapping_candidates"][0]["existing_schema_candidates"]), 1)


if __name__ == "__main__":
    unittest.main()
