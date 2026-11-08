from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_workers import (
    build_command,
    build_runtime_config,
    extract_json_object,
    parse_jsonl_events,
    validate_worker_payload,
)


class WorkerRunnerTests(unittest.TestCase):
    def test_runtime_config_pins_main_and_small_model(self) -> None:
        model = "opencode-go/crouton-alpha-free"
        config = json.loads(build_runtime_config(model, "source_discovery"))
        self.assertEqual(config["model"], model)
        self.assertEqual(config["small_model"], model)
        self.assertEqual(config["agent"]["feature_create_worker_a"]["model"], model)
        self.assertEqual(config["subagent_depth"], 0)
        self.assertFalse(config["agent"]["feature_create_worker_a"]["tools"]["bash"])

    def test_command_explicitly_pins_model_title_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prompt, bundle, visual = root / "prompt.md", root / "bundle.md", root / "fig1.png"
            for path in (prompt, bundle, visual):
                path.write_bytes(b"x")
            task = {
                "task_id": "FC-A-TEST123",
                "worker_role": "source_discovery",
                "model_ref": "opencode-go/crouton-alpha-free",
                "prompt_path": str(prompt),
                "bundle_path": str(bundle),
                "attachment_paths": [str(visual)],
            }
            command = build_command("opencode", task, root / "work")
            self.assertIn("--model", command)
            self.assertIn("opencode-go/crouton-alpha-free", command)
            self.assertIn("--title", command)
            self.assertIn("FC-A-TEST123", command)
            self.assertEqual(command.count("--file"), 3)
            self.assertNotIn("--auto", command)

    def test_extracts_json_from_fenced_response(self) -> None:
        parsed = extract_json_object(' ```json\n{"task_id":"T1"}\n``` ')
        self.assertEqual(parsed, {"task_id": "T1"})

    def test_parses_jsonl_text_model_and_usage(self) -> None:
        events = "\n".join([
            json.dumps({
                "type": "step_start",
                "sessionID": "ses_test",
                "part": {"messageID": "msg1", "model": {"providerID": "opencode-go", "modelID": "crouton-alpha-free"}},
            }),
            json.dumps({
                "type": "text",
                "sessionID": "ses_test",
                "part": {"messageID": "msg1", "text": '{"task_id":"T1"}'},
            }),
            json.dumps({
                "type": "step_finish",
                "sessionID": "ses_test",
                "part": {"type": "step-finish", "tokens": {"input": 10, "output": 5, "reasoning": 0, "cache": {"read": 0, "write": 0}}, "cost": 0},
            }),
        ])
        parsed = parse_jsonl_events(events)
        self.assertEqual(parsed["session_ids"], ["ses_test"])
        self.assertEqual(parsed["assistant_text"], '{"task_id":"T1"}')
        self.assertEqual(parsed["model_refs"], ["opencode-go/crouton-alpha-free"])
        self.assertEqual(parsed["reported_cost"], 0.0)
        self.assertEqual(parsed["tokens"]["input"], 10)

    def test_accepts_minimal_schema_valid_worker_a_payload(self) -> None:
        task = {"task_id": "TASK-1", "paper_id": "PAPER-1", "worker_role": "source_discovery"}
        payload = {
            "contract_version": "0.1",
            "task_id": "TASK-1",
            "worker_role": "source_discovery",
            "paper_id": "PAPER-1",
            "coverage": [],
            "feature_observations": [],
            "result_records": [],
            "unresolved_items": [],
        }
        self.assertEqual(validate_worker_payload(payload, task), [])

    def test_worker_b_must_report_each_assigned_scope(self) -> None:
        task = {
            "task_id": "TASK-B-SCOPE",
            "paper_id": "PAPER-1",
            "worker_role": "feature_audit",
            "source_ids": ["SRC-1"],
            "source_scopes": [
                {"scope_id": "SCP-1", "source_id": "SRC-1"},
                {"scope_id": "SCP-2", "source_id": "SRC-1"},
            ],
        }
        payload = {
            "contract_version": "0.1",
            "task_id": "TASK-B-SCOPE",
            "worker_role": "feature_audit",
            "paper_id": "PAPER-1",
            "coverage_audit": [{
                "source_id": "SRC-1",
                "scope_id": "SCP-1",
                "component": "main_text",
                "status": "reviewed",
                "audit_note": "Reviewed assigned unit.",
            }],
            "feature_observations": [],
            "omission_candidates": [],
            "semantic_distinctions": [],
            "unresolved_items": [],
        }
        problems = validate_worker_payload(payload, task)
        self.assertTrue(any(
            "missing assigned scope_ids: SCP-2" in problem
            for problem in problems
        ))

    def test_cross_chunk_context_worker_uses_worker_b_and_requires_both_sides(self) -> None:
        task = {
            "task_id": "TASK-CONTEXT-1",
            "paper_id": "PAPER-1",
            "worker_role": "cross_chunk_context",
            "unresolved_id": "UNRES-1",
            "record_id": "REC-1",
            "current_unit_ids": ["UNIT-A"],
            "target_unit_ids": ["UNIT-B"],
            "evidence_unit_map": [
                {"unit_id": "UNIT-A", "chunk_id": "CHUNK-A", "source_ids": ["SRC-A"]},
                {"unit_id": "UNIT-B", "chunk_id": "CHUNK-B", "source_ids": ["SRC-B"]},
            ],
        }
        current_ref = {
            "evidence_side": "current",
            "source_id": "SRC-A",
            "locator": {"chunk_id": "CHUNK-A", "unit_id": "UNIT-A"},
            "evidence_status": "verified_text",
            "quote_or_cell_value": "Capacity for S-01 was 152.",
        }
        target_ref = {
            "evidence_side": "target",
            "source_id": "SRC-B",
            "locator": {"chunk_id": "CHUNK-B", "unit_id": "UNIT-B"},
            "evidence_status": "verified_text",
            "quote_or_cell_value": "S-01 denotes the additive electrolyte.",
        }
        payload = {
            "contract_version": "0.1",
            "task_type": "cross_chunk_context",
            "task_id": "TASK-CONTEXT-1",
            "paper_id": "PAPER-1",
            "unresolved_id": "UNRES-1",
            "record_id": "REC-1",
            "decision": "association_confirmed",
            "rationale": "Exact sample identifier is shared.",
            "review_evidence_refs": [current_ref, target_ref],
            "context_facts": [{
                "context_role": "sample_identity",
                "raw_label": "S-01",
                "raw_value": "additive electrolyte",
                "unit": None,
                "evidence_refs": [current_ref, target_ref],
            }],
        }
        self.assertEqual(validate_worker_payload(payload, task), [])

        invalid = json.loads(json.dumps(payload))
        invalid["context_facts"][0]["evidence_refs"][1]["locator"]["chunk_id"] = "CHUNK-A"
        self.assertTrue(any(
            "chunk_id does not match" in item
            for item in validate_worker_payload(invalid, task)
        ))

    def test_legacy_queue_accepts_source_level_coverage_without_scope_id(self) -> None:
        task = {
            "task_id": "TASK-LEGACY",
            "paper_id": "PAPER-1",
            "worker_role": "source_discovery",
            "source_ids": ["SRC-1"],
        }
        payload = {
            "contract_version": "0.1",
            "task_id": "TASK-LEGACY",
            "worker_role": "source_discovery",
            "paper_id": "PAPER-1",
            "coverage": [{
                "component": "main_text",
                "source_id": "SRC-1",
                "status": "fully_reviewed",
            }],
            "feature_observations": [],
            "result_records": [],
            "unresolved_items": [],
        }
        self.assertEqual(validate_worker_payload(payload, task), [])

    def test_worker_must_report_each_assigned_scope_exactly_once(self) -> None:
        task = {
            "task_id": "TASK-SCOPE",
            "paper_id": "PAPER-1",
            "worker_role": "source_discovery",
            "source_ids": ["SRC-1"],
            "source_scopes": [
                {"scope_id": "SCP-1", "source_id": "SRC-1", "unit_id": "UNIT-1"},
                {"scope_id": "SCP-2", "source_id": "SRC-1", "unit_id": "UNIT-2"},
            ],
        }
        base = {
            "contract_version": "0.1",
            "task_id": "TASK-SCOPE",
            "worker_role": "source_discovery",
            "paper_id": "PAPER-1",
            "feature_observations": [],
            "result_records": [],
            "unresolved_items": [],
        }
        valid = {
            **base,
            "coverage": [
                {"component": "main_text", "source_id": "SRC-1", "scope_id": "SCP-1", "status": "fully_reviewed"},
                {"component": "main_text", "source_id": "SRC-1", "scope_id": "SCP-2", "status": "fully_reviewed"},
            ],
        }
        self.assertEqual(validate_worker_payload(valid, task), [])

        missing = {**base, "coverage": [valid["coverage"][0]]}
        self.assertTrue(any(
            "missing assigned scope_ids: SCP-2" in problem
            for problem in validate_worker_payload(missing, task)
        ))

        unexpected = {
            **base,
            "coverage": [
                valid["coverage"][0],
                {"component": "main_text", "source_id": "SRC-1", "scope_id": "SCP-X", "status": "fully_reviewed"},
            ],
        }
        self.assertTrue(any(
            "unexpected scope_ids: SCP-X" in problem
            for problem in validate_worker_payload(unexpected, task)
        ))

        wrong_source = {
            **base,
            "coverage": [
                {"component": "main_text", "source_id": "SRC-OTHER", "scope_id": "SCP-1", "status": "fully_reviewed"},
                valid["coverage"][1],
            ],
        }
        self.assertTrue(any(
            "belongs to source_id 'SRC-1', not 'SRC-OTHER'" in problem
            for problem in validate_worker_payload(wrong_source, task)
        ))


if __name__ == "__main__":
    unittest.main()
