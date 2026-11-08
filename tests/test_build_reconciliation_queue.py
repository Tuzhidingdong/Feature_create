from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_reconciliation_queue import main


ROOT = Path(__file__).resolve().parents[1]


class BuildReconciliationQueueTests(unittest.TestCase):
    def test_builds_worker_b_reconciliation_task_with_source_excerpts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepared = root / "prepared"
            run = root / "recon_run"
            text_dir = prepared / "extracted_text"
            text_dir.mkdir(parents=True)
            source_a_text = "Results: Coulombic efficiency (CE) was 99.1%."
            source_b_text = "Cycling data: CE was 98.9%."
            (text_dir / "a.txt").write_text(source_a_text, encoding="utf-8")
            (text_dir / "b.txt").write_text(source_b_text, encoding="utf-8")

            manifest = {
                "sources": [
                    {
                        "source_id": "SRC-A", "paper_id": "PAPER-A",
                        "absolute_path": str(prepared / "paperA.md"),
                        "extracted_text_path": "extracted_text/a.txt",
                        "source_type": "main_text", "visual_pages": [],
                    },
                    {
                        "source_id": "SRC-B", "paper_id": "PAPER-B",
                        "absolute_path": str(prepared / "paperB.md"),
                        "extracted_text_path": "extracted_text/b.txt",
                        "source_type": "main_text", "visual_pages": [],
                    },
                ]
            }
            registry = {
                "registry_id": "REGISTRY-TEST",
                "observations": [
                    {
                        "observation_id": "OBS-A", "paper_id": "PAPER-A", "source_id": "SRC-A",
                        "raw_feature_name": "Coulombic efficiency",
                        "raw_definition": "Discharge capacity / charge capacity",
                        "reported_unit": "%", "reported_method": "cycling",
                        "result_type": "measured", "variable_role": "result_feature",
                        "discovery_status": "evidence_verified",
                        "evidence_refs": [{
                            "source_id": "SRC-A", "source_type": "main_text",
                            "locator": {"page": 1}, "quote_or_cell_value": "Coulombic efficiency (CE) was 99.1%",
                            "evidence_status": "verified_text",
                        }],
                    },
                    {
                        "observation_id": "OBS-B", "paper_id": "PAPER-B", "source_id": "SRC-B",
                        "raw_feature_name": "CE",
                        "raw_definition": "Ratio of discharge capacity to charge capacity",
                        "reported_unit": "%", "reported_method": "cycling",
                        "result_type": "measured", "variable_role": "result_feature",
                        "discovery_status": "evidence_verified",
                        "evidence_refs": [{
                            "source_id": "SRC-B", "source_type": "main_text",
                            "locator": {"page": 2}, "quote_or_cell_value": "CE was 98.9%",
                            "evidence_status": "verified_text",
                        }],
                    },
                ],
            }
            pair = {
                "candidate_pair_id": "PAIR-TEST",
                "registry_feature_a_id": "RFEAT-A",
                "registry_feature_b_id": "RFEAT-B",
                "feature_a": {"canonical_name": "Coulombic efficiency", "definition": "Discharge capacity / charge capacity", "aliases": ["CE"], "units": ["%"], "observation_ids": ["OBS-A"]},
                "feature_b": {"canonical_name": "CE", "definition": "Ratio of discharge capacity to charge capacity", "aliases": ["Coulombic efficiency"], "units": ["%"], "observation_ids": ["OBS-B"]},
                "candidate_retrieval": {"reasons": ["possible_acronym_alias"]},
                "cross_paper": True,
                "paper_ids": ["PAPER-A", "PAPER-B"],
                "decision_status": "pending_semantic_review",
            }

            manifest_path = prepared / "source_manifest.json"
            registry_path = prepared / "registry.json"
            queue_path = prepared / "pairs.jsonl"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            queue_path.write_text(json.dumps(pair) + "\n", encoding="utf-8")

            exit_code = main([
                "--manifest", str(manifest_path),
                "--registry", str(registry_path),
                "--review-queue", str(queue_path),
                "--output", str(run),
            ])
            self.assertEqual(exit_code, 0)
            queue = json.loads((run / "reconciliation_task_queue.json").read_text(encoding="utf-8"))
            self.assertEqual(queue["task_count"], 1)
            task = queue["tasks"][0]
            self.assertEqual(task["worker_role"], "semantic_reconciliation")
            self.assertEqual(task["model_ref"], "opencode-go/beam-free")
            self.assertEqual(task["candidate_pair_ids"], ["PAIR-TEST"])
            bundle = json.loads(Path(task["bundle_path"]).read_text(encoding="utf-8"))
            context_a = bundle["reconciliation_pairs"][0]["observation_evidence"]["feature_a"][0]
            self.assertIn("Coulombic efficiency (CE)", context_a["evidence"][0]["source_excerpt"])


if __name__ == "__main__":
    unittest.main()
