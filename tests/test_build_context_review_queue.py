from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_context_review_queue import _is_valid_relation, main


class BuildContextReviewQueueTests(unittest.TestCase):
    def test_main_si_reference_basis_requires_a_real_indexed_reference(self) -> None:
        paper_index = {
            "unit_navigation": [
                {
                    "unit_id": "MAIN-UNIT",
                    "doc_type": "MAIN",
                    "page_number": 4,
                    "source_ids": ["SRC-MAIN"],
                },
                {
                    "unit_id": "SI-TABLE-UNIT",
                    "doc_type": "SI",
                    "page_number": 2,
                    "source_ids": ["SRC-SI"],
                    "table_id": "SI_TABLE_S2",
                },
            ],
            "cross_source_references": [{
                "source_doc_type": "MAIN",
                "source_page_number": 4,
                "reference_text": "See Table S2",
                "target_candidates": [{
                    "evidence_kind": "table",
                    "target_id": "SI_TABLE_S2",
                    "page_number": 2,
                }],
            }],
        }
        self.assertTrue(_is_valid_relation(
            "main_si_reference",
            {"MAIN-UNIT"},
            {"SI-TABLE-UNIT"},
            paper_index,
        ))
        main_collision_index = json.loads(json.dumps(paper_index))
        main_collision_index["unit_navigation"][1]["doc_type"] = "MAIN"
        self.assertFalse(_is_valid_relation(
            "main_si_reference",
            {"MAIN-UNIT"},
            {"SI-TABLE-UNIT"},
            main_collision_index,
        ))
        paper_index["cross_source_references"] = []
        self.assertFalse(_is_valid_relation(
            "main_si_reference",
            {"MAIN-UNIT"},
            {"SI-TABLE-UNIT"},
            paper_index,
        ))

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
            policy = json.loads(
                (Path(__file__).resolve().parents[1] / "config" / "execution_policy.json").read_text(encoding="utf-8")
            )
            first_pass_worker_b_model = policy["workers"]["worker_b"]["model_ref"]
            worker_b_current = {
                "task_id": "TASK-B-CURRENT",
                "paper_id": "PAPER-1",
                "worker_role": "feature_audit",
                "chunk_id": "CHUNK-A",
                "model_ref": first_pass_worker_b_model,
            }
            worker_b_target = {
                "task_id": "TASK-B-TARGET",
                "paper_id": "PAPER-1",
                "worker_role": "feature_audit",
                "chunk_id": "CHUNK-B",
                "model_ref": first_pass_worker_b_model,
            }
            source_queue = prepared / "task_queue.json"
            source_queue.write_text(
                json.dumps({"tasks": [current_task, target_task, worker_b_current, worker_b_target]}),
                encoding="utf-8",
            )

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
            self.assertTrue(task["input_fingerprint"])
            self.assertTrue(task["prompt_sha256"])
            self.assertTrue(task["bundle_sha256"])
            self.assertTrue(task["context_evidence_manifest_sha256"])
            self.assertTrue(task["record_snapshot_sha256"])
            self.assertTrue(task["unresolved_snapshot_sha256"])
            self.assertEqual(
                {"SRC-CURRENT", "SRC-TARGET"},
                set(task["source_text_sha256"]),
            )
            self.assertTrue(all(task["source_text_sha256"].values()))
            self.assertTrue(task["record_snapshot_sha256"])
            self.assertTrue(task["unresolved_snapshot_sha256"])
            self.assertEqual(
                {"SRC-CURRENT", "SRC-TARGET"},
                set(task["source_text_sha256"]),
            )
            self.assertTrue(all(task["source_text_sha256"].values()))
            policy = json.loads(
                (Path(__file__).resolve().parents[1] / "config" / "execution_policy.json").read_text(encoding="utf-8")
            )
            self.assertEqual(policy["workers"]["worker_b"]["model_ref"], task["model_ref"])
            self.assertEqual(
                {"TASK-B-CURRENT", "TASK-B-TARGET"},
                set(task["first_pass_worker_b_task_ids"]),
            )
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

            # A corpus-wide manifest update for an unrelated paper must change
            # the integrity hash but not this paper's task identity.
            manifest_rows = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_rows["sources"].append({
                "source_id": "SRC-UNRELATED",
                "paper_id": "PAPER-OTHER",
                "source_role": "main",
                "source_type": "main_text",
                "extracted_text_path": "extracted_text/unrelated.txt",
            })
            manifest_path.write_text(
                json.dumps(manifest_rows, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            unrelated_change_output = root / "context_dispatch_unrelated_manifest_change"
            self.assertEqual(main([
                "--manifest", str(manifest_path),
                "--queue", str(source_queue),
                "--unresolved", str(unresolved),
                "--records", str(records),
                "--output", str(unrelated_change_output),
            ]), 0)
            unchanged_task = json.loads(
                (unrelated_change_output / "context_review_task_queue.json").read_text(encoding="utf-8")
            )["tasks"][0]
            self.assertEqual(task["task_id"], unchanged_task["task_id"])
            self.assertNotEqual(task["source_manifest_sha256"], unchanged_task["source_manifest_sha256"])

            mismatched_tasks = json.loads(source_queue.read_text(encoding="utf-8"))["tasks"]
            mismatched_tasks[-1]["model_ref"] = policy["workers"]["worker_a"]["model_ref"]
            mismatched_queue = prepared / "task_queue_mismatched_worker_b.json"
            mismatched_queue.write_text(json.dumps({"tasks": mismatched_tasks}), encoding="utf-8")
            mismatched_output = root / "context_dispatch_mismatched_worker_b"
            self.assertEqual(main([
                "--manifest", str(manifest_path),
                "--queue", str(mismatched_queue),
                "--unresolved", str(unresolved),
                "--records", str(records),
                "--output", str(mismatched_output),
            ]), 0)
            mismatched_result = json.loads((mismatched_output / "context_review_task_queue.json").read_text(encoding="utf-8"))
            self.assertEqual(0, mismatched_result["task_count"])
            self.assertEqual(1, mismatched_result["blocked_unresolved_count"])
            self.assertIn(
                "first_pass_worker_b_model_mismatch",
                mismatched_result["blocked_unresolved"][0]["reason"],
            )

            unresolved_rows = [json.loads(line) for line in unresolved.read_text(encoding="utf-8").splitlines() if line.strip()]
            unresolved_rows[0]["description"] += " The follow-up specifically asks for the electrolyte definition."
            unresolved.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in unresolved_rows) + "\n",
                encoding="utf-8",
            )
            revised_output = root / "context_dispatch_revised"
            self.assertEqual(main([
                "--manifest", str(manifest_path),
                "--queue", str(source_queue),
                "--unresolved", str(unresolved),
                "--records", str(records),
                "--output", str(revised_output),
            ]), 0)
            revised_queue = json.loads((revised_output / "context_review_task_queue.json").read_text(encoding="utf-8"))
            self.assertNotEqual(task["task_id"], revised_queue["tasks"][0]["task_id"])

            unresolved_rows[0]["resolution_status"] = "association_confirmed"
            unresolved.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in unresolved_rows) + "\n",
                encoding="utf-8",
            )
            resolved_output = root / "context_dispatch_resolved"
            self.assertEqual(main([
                "--manifest", str(manifest_path),
                "--queue", str(source_queue),
                "--unresolved", str(unresolved),
                "--records", str(records),
                "--output", str(resolved_output),
            ]), 0)
            resolved_queue = json.loads((resolved_output / "context_review_task_queue.json").read_text(encoding="utf-8"))
            self.assertEqual(0, resolved_queue["task_count"])
            self.assertEqual(1, resolved_queue["skipped_unresolved_count"])
            self.assertIn("already_reviewed_status", resolved_queue["skipped_unresolved"][0]["reason"])

            unresolved_rows[0]["resolution_status"] = "insufficient_evidence"
            unresolved.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in unresolved_rows) + "\n",
                encoding="utf-8",
            )
            inconclusive_default = root / "context_dispatch_inconclusive_default"
            self.assertEqual(main([
                "--manifest", str(manifest_path),
                "--queue", str(source_queue),
                "--unresolved", str(unresolved),
                "--records", str(records),
                "--output", str(inconclusive_default),
            ]), 0)
            default_queue = json.loads((inconclusive_default / "context_review_task_queue.json").read_text(encoding="utf-8"))
            self.assertEqual(0, default_queue["task_count"])

            inconclusive_retry = root / "context_dispatch_inconclusive_retry"
            self.assertEqual(main([
                "--manifest", str(manifest_path),
                "--queue", str(source_queue),
                "--unresolved", str(unresolved),
                "--records", str(records),
                "--output", str(inconclusive_retry),
                "--retry-inconclusive",
            ]), 0)
            retry_queue = json.loads((inconclusive_retry / "context_review_task_queue.json").read_text(encoding="utf-8"))
            self.assertEqual(1, retry_queue["task_count"])
            self.assertNotEqual(revised_queue["tasks"][0]["task_id"], retry_queue["tasks"][0]["task_id"])
            self.assertEqual(1, retry_queue["tasks"][0]["review_attempt"])


if __name__ == "__main__":
    unittest.main()
