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


if __name__ == "__main__":
    unittest.main()
