from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import scripts.apply_integrity_review_decisions as apply_module
from scripts.apply_integrity_review_decisions import main as apply_main
from scripts.build_integrity_review_queue import main as build_main
from scripts.reconcile_features import registry_lock_path


class IntegrityReviewCliTests(unittest.TestCase):
    def test_builder_refuses_to_replace_active_decisions_without_archiving(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = root / "registry.json"
            records_path = root / "records.jsonl"
            output_dir = root / "review"
            registry_path.write_text(json.dumps({
                "observations": [], "canonical_features": [], "decision_log": [],
            }), encoding="utf-8")
            records_path.write_text("", encoding="utf-8")
            base_args = [
                "--registry", str(registry_path),
                "--records", str(records_path),
                "--output-dir", str(output_dir),
            ]
            self.assertEqual(build_main(base_args), 0)
            decisions_path = output_dir / "integrity_review_decisions.jsonl"
            decisions_path.write_text('{"human_work":"keep"}\\n', encoding="utf-8")
            queue_before = (output_dir / "integrity_review_queue.json").read_bytes()

            self.assertEqual(build_main(base_args + ["--force"]), 2)
            self.assertEqual((output_dir / "integrity_review_queue.json").read_bytes(), queue_before)
            self.assertEqual(decisions_path.read_text(encoding="utf-8"), '{"human_work":"keep"}\\n')

            self.assertEqual(build_main(base_args + ["--force", "--archive-existing"]), 0)
            archives = list(output_dir.glob("integrity_review_archive_*"))
            self.assertEqual(len(archives), 1)
            self.assertTrue((archives[0] / "integrity_review_decisions.jsonl").is_file())
            self.assertTrue((archives[0] / "integrity_review_queue.json").is_file())
            self.assertTrue((output_dir / "integrity_review_bundle.json").is_file())
            self.assertFalse(decisions_path.exists())

    def test_end_to_end_append_only_review_and_stale_snapshot_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = root / "global_feature_registry.json"
            records_path = root / "result_records.jsonl"
            output_dir = root / "integrity_review"
            template_path = output_dir / "integrity_review_decisions.template.jsonl"
            decisions_path = output_dir / "integrity_review_decisions.jsonl"
            queue_path = output_dir / "integrity_review_queue.json"
            ledger_path = output_dir / "integrity_review_decision_ledger.jsonl"
            report_path = output_dir / "apply_report.json"

            registry = {
                "observations": [{
                    "observation_id": "OBS-1",
                    "paper_id": "PAPER-1",
                    "source_id": "SRC-1",
                    "raw_feature_name": "Coulombic efficiency",
                    "variable_role": "result_feature",
                    "discovery_status": "candidate",
                    "evidence_refs": [],
                }],
                "canonical_features": [],
                "decision_log": [],
            }
            registry_bytes = json.dumps(registry, ensure_ascii=False, indent=2) + "\n"
            registry_path.write_text(registry_bytes, encoding="utf-8")
            records_path.write_text("", encoding="utf-8")

            self.assertEqual(build_main([
                "--registry", str(registry_path),
                "--records", str(records_path),
                "--output-dir", str(output_dir),
            ]), 0)

            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            template = [
                json.loads(line)
                for line in template_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertGreater(len(template), 0)
            finding_map = {item["finding_id"]: item for item in queue["findings"]}
            edited = []
            for row in template:
                finding = finding_map[row["finding_id"]]
                row["review_decision"] = "repair_required"
                row["action"] = finding["recommended_route"]["action"]
                row["rationale"] = "Inspect this source-backed identity and preserve prior history."
                edited.append(row)
            decisions_path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in edited),
                encoding="utf-8",
            )

            args = [
                "--queue", str(queue_path),
                "--template", str(template_path),
                "--decisions", str(decisions_path),
                "--registry", str(registry_path),
                "--records", str(records_path),
                "--ledger", str(ledger_path),
                "--report", str(report_path),
            ]
            acquired_locks = []
            real_lock = apply_module.exclusive_lock

            @contextmanager
            def observed_lock(path, timeout_seconds=30.0):
                acquired_locks.append(Path(path))
                with real_lock(path, timeout_seconds=timeout_seconds):
                    yield

            with patch.object(apply_module, "exclusive_lock", observed_lock):
                self.assertEqual(apply_main(args), 0)
            self.assertEqual(acquired_locks[0], registry_lock_path(registry_path))
            self.assertEqual(acquired_locks[1], ledger_path.with_name(ledger_path.name + ".lock"))
            ledger_before = ledger_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(ledger_before), len(template))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertFalse(report["registry_modified"])
            self.assertFalse(report["records_modified"])
            self.assertEqual(report["decision_counts"]["repair_required"], len(template))
            self.assertGreaterEqual(len(report["action_counts"]), 1)
            self.assertEqual(registry_path.read_text(encoding="utf-8"), registry_bytes)
            self.assertEqual(records_path.read_text(encoding="utf-8"), "")

            # A byte-level Registry change invalidates the prepared decision snapshot.
            registry_path.write_text(registry_bytes + " ", encoding="utf-8")
            self.assertEqual(apply_main(args), 2)
            self.assertEqual(ledger_path.read_text(encoding="utf-8").splitlines(), ledger_before)


if __name__ == "__main__":
    unittest.main()
