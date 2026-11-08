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

    def _bind_context_artifacts(self, task: dict, root: Path) -> dict:
        source_manifest = root / "source_manifest.json"
        context_manifest = root / "context_evidence_manifest.json"
        bundle = root / "task_input.md"
        prompt = root / "worker_b_cross_chunk_context.md"
        image = root / "figure.png"
        self._write_json(source_manifest, {"sources": []})
        self._write_json(context_manifest, {"assigned_units": []})
        bundle.write_text("targeted task bundle", encoding="utf-8")
        prompt.write_text("targeted context prompt", encoding="utf-8")
        image.write_bytes(b"synthetic image bytes")

        task.update({
            "source_manifest_path": str(source_manifest),
            "source_manifest_sha256": hashlib.sha256(source_manifest.read_bytes()).hexdigest(),
            "context_evidence_manifest_path": str(context_manifest),
            "context_evidence_manifest_sha256": hashlib.sha256(context_manifest.read_bytes()).hexdigest(),
            "bundle_path": str(bundle),
            "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            "prompt_path": str(prompt),
            "prompt_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
            "attachment_sha256": {
                str(image): hashlib.sha256(image.read_bytes()).hexdigest(),
            },
            "attachment_paths": [str(context_manifest), str(image)],
        })
        return {
            "source_manifest": task["source_manifest_sha256"],
            "context_evidence_manifest": task["context_evidence_manifest_sha256"],
            "bundle": task["bundle_sha256"],
            "prompt": task["prompt_sha256"],
            "attachments": {
                str(image): task["attachment_sha256"][str(image)],
            },
        }

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
            artifact_hashes = self._bind_context_artifacts(task, root)
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
                "manifest_path": task["source_manifest_path"],
                "manifest_sha256": artifact_hashes["source_manifest"],
                "tasks": [{
                    "task_id": task_id,
                    "status": "evidence_checked",
                    "response_sha256": response_sha256,
                    "artifact_sha256": artifact_hashes,
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
                "sample_identity": [{
                    "raw_label": "S-01",
                    "raw_value": "electrolyte containing 0.2 M additive X",
                    "unit": None,
                    "context_role": "sample_identity",
                    "evidence_refs": [current_ref],
                }],
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
            self.assertEqual(
                {"SRC-CURRENT", "SRC-TARGET"},
                {ref["source_id"] for ref in updated["sample_identity"][0]["evidence_refs"]},
            )
            self.assertEqual("association_confirmed", updated["unresolved_issues"][0]["resolution_status"])
            self.assertEqual(1, len(updated["context_review_history"]))
            unresolved = json.loads(unresolved_path.read_text(encoding="utf-8").strip())
            self.assertEqual("association_confirmed", unresolved["resolution_status"])
            self.assertEqual(1, len(unresolved["resolution_history"]))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(1, report["tasks_applied"])
            self.assertEqual(0, report["context_facts_added"])
            self.assertEqual(1, report["context_facts_enriched"])
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

    def test_rejects_response_modified_after_evidence_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = root / "context_runs"
            task_output = root / "task_output"
            task_id = "FC-CX-TAMPER"
            record_id = "REC-TAMPER"
            unresolved_id = "UNRES-TAMPER"
            model = "opencode-go/beam-free"
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
            artifact_hashes = self._bind_context_artifacts(task, root)
            queue_path = root / "context_review_task_queue.json"
            self._write_json(queue_path, {"tasks": [task]})
            self._write_json(task_output / "task_state.json", {
                "status": "complete",
                "requested_model": model,
                "observed_models": [model],
            })
            response_path = task_output / "response.json"
            self._write_json(response_path, {
                "contract_version": "0.1",
                "task_type": "cross_chunk_context",
                "task_id": task_id,
                "paper_id": "PAPER-1",
                "unresolved_id": unresolved_id,
                "record_id": record_id,
                "decision": "insufficient_evidence",
                "rationale": "Insufficient evidence.",
                "review_evidence_refs": [],
                "context_facts": [],
            })
            validated_hash = hashlib.sha256(response_path.read_bytes()).hexdigest()
            self._write_json(run_dir / "evidence_validation.json", {
                "manifest_path": task["source_manifest_path"],
                "manifest_sha256": artifact_hashes["source_manifest"],
                "tasks": [{
                    "task_id": task_id,
                    "status": "evidence_checked",
                    "response_sha256": validated_hash,
                    "artifact_sha256": artifact_hashes,
                }],
            })
            # Simulate a response being edited/replaced after validation.
            tampered = json.loads(response_path.read_text(encoding="utf-8"))
            tampered["decision"] = "association_confirmed"
            tampered["rationale"] = "This post-validation response must not be applied."
            tampered["context_facts"] = [{
                "context_role": "sample_identity",
                "raw_label": "S-01",
                "raw_value": "guessed sample",
                "evidence_refs": [],
            }]
            self._write_json(response_path, tampered)

            records_path = root / "result_records.jsonl"
            records_path.write_text(json.dumps({
                "record_id": record_id,
                "paper_id": "PAPER-1",
                "worker_task_id": "TASK-A",
                "raw_value": "77",
                "numeric_value": 77,
                "sample_identity": [],
                "unresolved_issues": [{"unresolved_id": unresolved_id, "resolution_status": "pending"}],
            }) + "\n", encoding="utf-8")
            unresolved_path = root / "unresolved_extraction_items.jsonl"
            unresolved_path.write_text(json.dumps({
                "unresolved_id": unresolved_id,
                "paper_id": "PAPER-1",
                "resolution_status": "pending",
            }) + "\n", encoding="utf-8")

            report_path = root / "report.json"
            self.assertEqual(main([
                "--queue", str(queue_path),
                "--run-dir", str(run_dir),
                "--records", str(records_path),
                "--unresolved", str(unresolved_path),
                "--report", str(report_path),
            ]), 0)
            updated = json.loads(records_path.read_text(encoding="utf-8").strip())
            self.assertEqual(77, updated["numeric_value"])
            self.assertEqual([], updated["sample_identity"])
            self.assertEqual("pending", updated["unresolved_issues"][0]["resolution_status"])
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(0, report["tasks_applied"])
            self.assertEqual(
                "response_file_changed_after_evidence_validation",
                report["skipped_tasks"][0]["reason"],
            )

    def test_rejects_bundle_modified_after_evidence_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = root / "context_runs"
            task_output = root / "task_output"
            task_id = "FC-CX-BUNDLE-TAMPER"
            record_id = "REC-BUNDLE"
            unresolved_id = "UNRES-BUNDLE"
            model = "opencode-go/beam-free"
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
            artifact_hashes = self._bind_context_artifacts(task, root)
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
                "decision": "insufficient_evidence",
                "rationale": "The selected target unit does not contain a verified sample mapping.",
                "review_evidence_refs": [
                    {"evidence_side": "current", "source_id": "SRC-A", "locator": {"chunk_id": "A", "unit_id": "UA"}, "evidence_status": "unverified"},
                    {"evidence_side": "target", "source_id": "SRC-B", "locator": {"chunk_id": "B", "unit_id": "UB"}, "evidence_status": "unverified"},
                ],
                "context_facts": [],
            }
            response_path = task_output / "response.json"
            self._write_json(response_path, response)
            response_sha256 = hashlib.sha256(response_path.read_bytes()).hexdigest()
            self._write_json(run_dir / "evidence_validation.json", {
                "manifest_path": task["source_manifest_path"],
                "manifest_sha256": artifact_hashes["source_manifest"],
                "tasks": [{
                    "task_id": task_id,
                    "status": "evidence_checked",
                    "response_sha256": response_sha256,
                    "artifact_sha256": artifact_hashes,
                }],
            })

            # Keep the Worker B response unchanged, but alter the exact input
            # bundle after validation. The context decision must not be applied.
            Path(task["bundle_path"]).write_text("changed after validation", encoding="utf-8")
            # Simulate an edited queue that now declares the changed bundle as
            # the expected input; the older validation report must still reject it.
            task["bundle_sha256"] = hashlib.sha256(
                Path(task["bundle_path"]).read_bytes()
            ).hexdigest()
            self._write_json(queue_path, {"tasks": [task]})

            records_path = root / "result_records.jsonl"
            records_path.write_text(json.dumps({
                "record_id": record_id,
                "paper_id": "PAPER-1",
                "worker_task_id": "TASK-A",
                "raw_value": "91",
                "numeric_value": 91,
                "sample_identity": [],
                "unresolved_issues": [{"unresolved_id": unresolved_id, "resolution_status": "pending"}],
            }) + "\n", encoding="utf-8")
            unresolved_path = root / "unresolved_extraction_items.jsonl"
            unresolved_path.write_text(json.dumps({
                "unresolved_id": unresolved_id,
                "paper_id": "PAPER-1",
                "resolution_status": "pending",
            }) + "\n", encoding="utf-8")

            report_path = root / "report.json"
            self.assertEqual(main([
                "--queue", str(queue_path),
                "--run-dir", str(run_dir),
                "--records", str(records_path),
                "--unresolved", str(unresolved_path),
                "--report", str(report_path),
            ]), 0)
            updated = json.loads(records_path.read_text(encoding="utf-8").strip())
            self.assertEqual(91, updated["numeric_value"])
            self.assertEqual([], updated["sample_identity"])
            self.assertEqual("pending", updated["unresolved_issues"][0]["resolution_status"])
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(0, report["tasks_applied"])
            self.assertEqual(
                "context_artifact_changed_after_evidence_validation:bundle",
                report["skipped_tasks"][0]["reason"],
            )

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
            artifact_hashes = self._bind_context_artifacts(task, root)
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
                "manifest_path": task["source_manifest_path"],
                "manifest_sha256": artifact_hashes["source_manifest"],
                "tasks": [{
                    "task_id": task_id,
                    "status": "evidence_checked",
                    "response_sha256": response_sha256,
                    "artifact_sha256": artifact_hashes,
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
            unresolved = json.loads(unresolved_path.read_text(encoding="utf-8").strip())
            self.assertEqual(1, len(unresolved["resolution_history"]))
            self.assertEqual("insufficient_evidence", unresolved["resolution_history"][0]["decision"])


if __name__ == "__main__":
    unittest.main()
