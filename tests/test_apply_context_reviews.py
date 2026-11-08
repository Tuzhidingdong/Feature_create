from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.apply_context_reviews import main


class ApplyContextReviewTests(unittest.TestCase):
    def _write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_confirmed_context_adds_only_context_facts_and_resolves_issue(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = root / "context_runs"
            task_output = root / "task_output"
            task_id = "FC-CX-TEST-1"
            record_id = "REC-1"
            unresolved_id = "UNRES-1"
            model = "opencode-go/worker-b-free"

            current_ref = {
                "evidence_side": "current",
                "source_id": "SRC-CURRENT",
                "locator": {"chunk_id": "CHUNK-A", "unit_id": "UNIT-A", "page_number": 3},
                "quote_or_cell_value": "Capacity for S-01 was 152 mAh g-1.",
                "evidence_status": "verified_text",
            }
            target_ref = {
                "evidence_side": "target",
                "source_id": "SRC-TARGET",
                "locator": {"chunk_id": "CHUNK-B", "unit_id": "UNIT-B", "page_number": 2},
                "quote_or_cell_value": "S-01 is the electrolyte containing 0.2 M additive X.",
                "evidence_status": "verified_text",
            }
            task = {
                "task_id": task_id,
                "worker_role": "cross_chunk_context",
                "task_status": "pending",
                "paper_id": "PAPER-1",
                "model_ref": model,
                "output_dir": str(task_output),
                "unresolved_id": unresolved_id,
                "record_id": record_id,
                "origin_worker_task_id": "TASK-A",
            }
            queue_path = root / "context_review_task_queue.json"
            self._write_json(queue_path, {"tasks": [task]})
            self._write_json(task_output / "task_state.json", {
                "status": "complete",
                "requested_model": model,
                "observed_models": [model],
            })
            response = {
                "contract_version": "0.1",
                "task_type": "cross_chunk_context",
                "task_id": task_id,
                "paper_id": "PAPER-1",
                "unresolved_id": unresolved_id,
                "record_id": record_id,
                "decision": "association_confirmed",
                "rationale": "The exact sample code matches in the two quoted source passages.",
                "review_evidence_refs": [current_ref, target_ref],
                "context_facts": [{
                    "context_role": "sample_identity",
                    "raw_label": "S-01",
                    "raw_value": "electrolyte containing 0.2 M additive X",
                    "unit": None,
                    "evidence_refs": [current_ref, target_ref],
                }],
            }
            self._write_json(task_output / "response.json", response)
            response_sha256 = hashlib.sha256((task_output / "response.json").read_bytes()).hexdigest()
            self._write_json(run_dir / "evidence_validation.json", {
                "tasks": [{
                    "task_id": task_id,
                    "status": "evidence_checked",
                    "response_sha256": response_sha256,
                }],
            })

            record = {
                "record_id": record_id,
                "paper_id": "PAPER-1",
                "worker_task_id": "TASK-A",
                "raw_feature_name": "Capacity",
                "raw_value": "152 mAh g-1",
                "numeric_value": 152,
                "reported_unit": "mAh g-1",
                "registry_feature_id": "RFEAT-1",
                "observation_id": "OBS-1",
                "result_type": "measured",
                "measurement_or_calculation_method": "galvanostatic cycling",
                "source_id": "SRC-CURRENT",
                "evidence_refs": [{"source_id": "SRC-CURRENT", "locator": {"unit_id": "UNIT-A"}}],
                "sample_identity": [],
                "condition_facts": [],
                "time_cycle_facts": [],
                "protocol_facts": [],
                "other_context_facts": [],
                "unresolved_issues": [{
                    "unresolved_id": unresolved_id,
                    "item_type": "missing_context",
                    "description": "Sample identity not resolved.",
                    "resolution_status": "pending",
                }],
            }
            records_path = root / "result_records.jsonl"
            records_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            unresolved_path = root / "unresolved_extraction_items.jsonl"
            unresolved_path.write_text(json.dumps({
                "unresolved_id": unresolved_id,
                "paper_id": "PAPER-1",
                "resolution_status": "pending",
            }) + "\n", encoding="utf-8")

            report_path = root / "application_report.json"
            self.assertEqual(main([
                "--queue", str(queue_path),
                "--run-dir", str(run_dir),
                "--records", str(records_path),
                "--unresolved", str(unresolved_path),
                "--report", str(report_path),
            ]), 0)

            updated = json.loads(records_path.read_text(encoding="utf-8").strip())
            self.assertEqual("152 mAh g-1", updated["raw_value"])
            self.assertEqual(152, updated["numeric_value"])
            self.assertEqual("RFEAT-1", updated["registry_feature_id"])
            self.assertEqual("OBS-1", updated["observation_id"])
            self.assertEqual(record["evidence_refs"], updated["evidence_refs"])
            self.assertEqual(1, len(updated["sample_identity"]))
            self.assertEqual("S-01", updated["sample_identity"][0]["raw_label"])
            self.assertEqual("association_confirmed", updated["unresolved_issues"][0]["resolution_status"])
            unresolved = json.loads(unresolved_path.read_text(encoding="utf-8").strip())
            self.assertEqual("association_confirmed", unresolved["resolution_status"])
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(1, report["tasks_applied"])
            self.assertEqual(1, report["context_facts_added"])
            self.assertEqual(1, len(updated["context_review_history"]))

            # Re-applying the same completed task must not duplicate either the
            # context fact or its decision-history entry.
            self.assertEqual(main([
                "--queue", str(queue_path),
                "--run-dir", str(run_dir),
                "--records", str(records_path),
                "--unresolved", str(unresolved_path),
                "--report", str(report_path),
            ]), 0)
            repeated = json.loads(records_path.read_text(encoding="utf-8").strip())
            self.assertEqual(1, len(repeated["sample_identity"]))
            self.assertEqual(1, len(repeated["context_review_history"]))

    def test_insufficient_evidence_keeps_unresolved_issue_open_and_adds_no_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = root / "context_runs"
            task_output = root / "task_output"
            task_id = "FC-CX-TEST-2"
            record_id = "REC-2"
            unresolved_id = "UNRES-2"
            model = "opencode-go/worker-b-free"
            task = {
                "task_id": task_id,
                "worker_role": "cross_chunk_context",
                "task_status": "pending",
                "paper_id": "PAPER-1",
                "model_ref": model,
                "output_dir": str(task_output),
                "unresolved_id": unresolved_id,
                "record_id": record_id,
                "origin_worker_task_id": "TASK-A",
            }
            queue_path = root / "context_review_task_queue.json"
            self._write_json(queue_path, {"tasks": [task]})
            self._write_json(task_output / "task_state.json", {
                "status": "complete",
                "requested_model": model,
                "observed_models": [model],
            })
            self._write_json(task_output / "response.json", {
                "contract_version": "0.1",
                "task_type": "cross_chunk_context",
                "task_id": task_id,
                "paper_id": "PAPER-1",
                "unresolved_id": unresolved_id,
                "record_id": record_id,
                "decision": "insufficient_evidence",
                "rationale": "The target paragraph does not identify this result series.",
                "review_evidence_refs": [
                    {"evidence_side": "current", "source_id": "SRC-A", "locator": {"chunk_id": "A", "unit_id": "UA"}, "evidence_status": "unverified"},
                    {"evidence_side": "target", "source_id": "SRC-B", "locator": {"chunk_id": "B", "unit_id": "UB"}, "evidence_status": "unverified"},
                ],
                "context_facts": [],
            })
            response_sha256 = hashlib.sha256((task_output / "response.json").read_bytes()).hexdigest()
            self._write_json(run_dir / "evidence_validation.json", {
                "tasks": [{
                    "task_id": task_id,
                    "status": "evidence_checked",
                    "response_sha256": response_sha256,
                }],
            })
            records_path = root / "result_records.jsonl"
            records_path.write_text(json.dumps({
                "record_id": record_id,
                "paper_id": "PAPER-1",
                "worker_task_id": "TASK-A",
                "raw_value": "42",
                "numeric_value": 42,
                "sample_identity": [],
                "unresolved_issues": [{"unresolved_id": unresolved_id, "resolution_status": "pending"}],
            }) + "\n", encoding="utf-8")
            unresolved_path = root / "unresolved_extraction_items.jsonl"
            unresolved_path.write_text(json.dumps({
                "unresolved_id": unresolved_id,
                "paper_id": "PAPER-1",
                "resolution_status": "pending",
            }) + "\n", encoding="utf-8")
            self.assertEqual(main([
                "--queue", str(queue_path),
                "--run-dir", str(run_dir),
                "--records", str(records_path),
                "--unresolved", str(unresolved_path),
                "--report", str(root / "report.json"),
            ]), 0)
            updated = json.loads(records_path.read_text(encoding="utf-8").strip())
            self.assertEqual([], updated["sample_identity"])
            self.assertEqual("insufficient_evidence", updated["unresolved_issues"][0]["resolution_status"])


if __name__ == "__main__":
    unittest.main()
