from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_context_review_queue import main


class BuildContextReviewQueueTests(unittest.TestCase):
    def test_builds_targeted_worker_b_task_from_explicit_remote_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepared = root / "prepared"
            extracted = prepared / "extracted_text"
            extracted.mkdir(parents=True)
            current_text = "Capacity was 152 mAh g-1 for sample S-01."
            target_text = "S-01 denotes the electrolyte containing 0.2 M additive X."
            (extracted / "current.txt").write_text(current_text, encoding="utf-8")
            (extracted / "target.txt").write_text(target_text, encoding="utf-8")

            current_source = {
                "source_id": "SRC-CURRENT",
                "paper_id": "PAPER-1",
                "source_role": "main",
                "source_type": "main_text",
                "component": "main_text",
                "extracted_text_path": "extracted_text/current.txt",
            }
            target_source = {
                "source_id": "SRC-TARGET",
                "paper_id": "PAPER-1",
                "source_role": "main",
                "source_type": "main_text",
                "component": "main_text",
                "extracted_text_path": "extracted_text/target.txt",
            }
            manifest_path = prepared / "source_manifest.json"
            manifest_path.write_text(json.dumps({
                "sources": [current_source, target_source],
                "papers": [{"paper_id": "PAPER-1"}],
            }), encoding="utf-8")

            current_unit = {
                "unit_id": "UNIT-CURRENT",
                "chunk_id": "CHUNK-A",
                "source_id": "SRC-CURRENT",
                "source_ids": ["SRC-CURRENT"],
                "source_type": "main_text",
                "component": "main_text",
                "unit_kind": "text",
                "doc_type": "MAIN",
                "page_number": 3,
                "locator": {
                    "source_text_offset_start": 0,
                    "source_text_offset_end": len(current_text),
                    "page_number": 3,
                },
            }
            target_unit = {
                "unit_id": "UNIT-TARGET",
                "chunk_id": "CHUNK-B",
                "source_id": "SRC-TARGET",
                "source_ids": ["SRC-TARGET"],
                "source_type": "main_text",
                "component": "main_text",
                "unit_kind": "text",
                "doc_type": "MAIN",
                "page_number": 2,
                "locator": {
                    "source_text_offset_start": 0,
                    "source_text_offset_end": len(target_text),
                    "page_number": 2,
                },
            }
            current_index = prepared / "chunk_current.json"
            target_index = prepared / "chunk_target.json"
            current_index.write_text(json.dumps({
                "chunk_id": "CHUNK-A",
                "assigned_units": [current_unit],
            }), encoding="utf-8")
            target_index.write_text(json.dumps({
                "chunk_id": "CHUNK-B",
                "assigned_units": [target_unit],
            }), encoding="utf-8")

            paper_index = prepared / "paper_chunk_index.json"
            paper_index.write_text(json.dumps({
                "paper_id": "PAPER-1",
                "unit_navigation": [
                    {**current_unit, "source_ids": ["SRC-CURRENT"]},
                    {**target_unit, "source_ids": ["SRC-TARGET"]},
                ],
                "cross_chunk_text_boundaries": [{
                    "from_segment": {"unit_id": "UNIT-CURRENT", "chunk_id": "CHUNK-A"},
                    "to_segment": {"unit_id": "UNIT-TARGET", "chunk_id": "CHUNK-B"},
                }],
                "same_original_table_row_groups": [],
            }), encoding="utf-8")

            current_task = {
                "task_id": "TASK-A-CURRENT",
                "paper_id": "PAPER-1",
                "doi": "10.1234/example",
                "worker_role": "source_discovery",
                "chunk_id": "CHUNK-A",
                "paper_chunk_index_path": str(paper_index),
                "chunk_evidence_index_path": str(current_index),
            }
            target_task = {
                "task_id": "TASK-A-TARGET",
                "paper_id": "PAPER-1",
                "worker_role": "source_discovery",
                "chunk_id": "CHUNK-B",
                "chunk_evidence_index_path": str(target_index),
            }
            source_queue = prepared / "task_queue.json"
            source_queue.write_text(json.dumps({"tasks": [current_task, target_task]}), encoding="utf-8")

            unresolved = root / "unresolved_extraction_items.jsonl"
            unresolved.write_text(json.dumps({
                "unresolved_id": "UNRES-1",
                "paper_id": "PAPER-1",
                "worker_task_id": "TASK-A-CURRENT",
                "item_type": "missing_context",
                "description": "The sample identity associated with the capacity value is unresolved.",
                "related_candidate_record_id": "CAND-RECORD-1",
                "evidence_refs": [{
                    "source_id": "SRC-CURRENT",
                    "source_type": "main_text",
                    "locator": {
                        "chunk_id": "CHUNK-A",
                        "unit_id": "UNIT-CURRENT",
                        "source_text_offset_start": 0,
                        "source_text_offset_end": len(current_text),
                    },
                    "quote_or_cell_value": current_text,
                    "evidence_status": "verified_text",
                }],
                "cross_chunk_target": {
                    "relation_basis": "adjacent_text_boundary",
                    "context_role": "sample_identity",
                    "target_units": [{
                        "chunk_id": "CHUNK-B",
                        "unit_id": "UNIT-TARGET",
                        "source_id": "SRC-TARGET",
                    }],
                    "rationale": "The next paragraph defines the sample code.",
                },
            }) + "\n", encoding="utf-8")
            records = root / "result_records.jsonl"
            records.write_text(json.dumps({
                "record_id": "REC-1",
                "origin_candidate_record_id": "CAND-RECORD-1",
                "worker_task_id": "TASK-A-CURRENT",
                "paper_id": "PAPER-1",
                "raw_feature_name": "Capacity",
                "raw_value": "152 mAh g-1",
                "numeric_value": 152,
                "sample_identity": [],
                "condition_facts": [],
                "time_cycle_facts": [],
                "protocol_facts": [],
                "other_context_facts": [],
            }) + "\n", encoding="utf-8")

            output = root / "context_dispatch"
            self.assertEqual(main([
                "--manifest", str(manifest_path),
                "--queue", str(source_queue),
                "--unresolved", str(unresolved),
                "--records", str(records),
                "--output", str(output),
            ]), 0)

            queue = json.loads((output / "context_review_task_queue.json").read_text(encoding="utf-8"))
            self.assertEqual(1, queue["task_count"])
            task = queue["tasks"][0]
            self.assertEqual("cross_chunk_context", task["worker_role"])
            self.assertEqual("opencode-go/gpt-6-luna", task["model_ref"] if task["model_ref"] == "opencode-go/gpt-6-luna" else task["model_ref"])
            self.assertEqual({"UNIT-CURRENT", "UNIT-TARGET"}, set(task["assigned_unit_ids"]))
            self.assertEqual(["UNIT-CURRENT"], task["current_unit_ids"])
            self.assertEqual(["UNIT-TARGET"], task["target_unit_ids"])
            bundle = Path(task["bundle_path"]).read_text(encoding="utf-8")
            self.assertIn(current_text, bundle)
            self.assertIn(target_text, bundle)
            self.assertIn("152 mAh g-1", bundle)
            self.assertNotIn("raw PDF", bundle.lower())
            evidence = json.loads(Path(task["context_evidence_manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual({"UNIT-CURRENT", "UNIT-TARGET"}, {
                item["unit_id"] for item in evidence["assigned_units"]
            })


if __name__ == "__main__":
    unittest.main()
