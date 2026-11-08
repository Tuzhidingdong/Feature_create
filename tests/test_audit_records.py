from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.audit_records import main


class AuditRecordsTests(unittest.TestCase):
    def test_counts_sources_from_both_worker_coverage_not_model_self_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paper_out_a = root / "a"
            paper_out_b = root / "b"
            paper_out_a.mkdir()
            paper_out_b.mkdir()
            (paper_out_a / "response.json").write_text(json.dumps({
                "coverage": [
                    {"source_id": "SRC-M", "status": "fully_reviewed"},
                    {"source_id": "SRC-SI", "status": "fully_reviewed"},
                ]
            }), encoding="utf-8")
            (paper_out_b / "response.json").write_text(json.dumps({
                "coverage_audit": [
                    {"source_id": "SRC-M", "status": "reviewed"},
                    {"source_id": "SRC-SI", "status": "partially_reviewed"},
                ]
            }), encoding="utf-8")
            (paper_out_a / "task_state.json").write_text(json.dumps({"status": "complete"}), encoding="utf-8")
            (paper_out_b / "task_state.json").write_text(json.dumps({"status": "complete"}), encoding="utf-8")

            manifest = {
                "corpus_fingerprint": "fingerprint",
                "sources": [
                    {"source_id": "SRC-M", "source_role": "main", "parse_status": "parsed", "is_visual_asset": False},
                    {"source_id": "SRC-SI", "source_role": "si", "parse_status": "parsed", "is_visual_asset": False},
                ],
                "papers": [{"paper_id": "PAPER-1", "source_ids": ["SRC-M", "SRC-SI"]}],
            }
            queue = {
                "tasks": [
                    {
                        "task_id": "TASK-A", "worker_role": "source_discovery",
                        "output_dir": str(paper_out_a),
                    },
                    {
                        "task_id": "TASK-B", "worker_role": "feature_audit",
                        "output_dir": str(paper_out_b),
                    },
                ]
            }
            registry = {
                "canonical_features": [
                    {"registry_feature_id": "RFEAT-1", "feature_status": "confirmed", "existing_feature_id": None}
                ]
            }
            record = {
                "record_id": "REC-1",
                "extraction_status": "accepted",
                "result_type": "measured",
                "evidence_status": "verified_text",
                "context_notes": "Current density was not reported in this section.",
                "unresolved_issues": [],
            }
            pairs = []
            paths = {
                "manifest": root / "manifest.json",
                "queue": root / "queue.json",
                "registry": root / "registry.json",
                "records": root / "records.jsonl",
                "review": root / "review.jsonl",
                "output": root / "audit.json",
            }
            paths["manifest"].write_text(json.dumps(manifest), encoding="utf-8")
            paths["queue"].write_text(json.dumps(queue), encoding="utf-8")
            paths["registry"].write_text(json.dumps(registry), encoding="utf-8")
            paths["records"].write_text(json.dumps(record) + "\n", encoding="utf-8")
            paths["review"].write_text("".join(json.dumps(item) + "\n" for item in pairs), encoding="utf-8")

            code = main([
                "--manifest", str(paths["manifest"]),
                "--queue", str(paths["queue"]),
                "--registry", str(paths["registry"]),
                "--records", str(paths["records"]),
                "--review-queue", str(paths["review"]),
                "--run-dir", str(root / "run"),
                "--output", str(paths["output"]),
            ])
            self.assertEqual(code, 0)
            report = json.loads(paths["output"].read_text(encoding="utf-8"))
            self.assertEqual(report["review_coverage"]["sources_fully_reviewed_by_both"], 1)
            self.assertEqual(report["review_coverage"]["main_sources_fully_reviewed_by_both"], 1)
            self.assertEqual(report["review_coverage"]["si_sources_fully_reviewed_by_both"], 0)
            self.assertEqual(report["review_coverage"]["papers_partially_reviewed"], 1)
            self.assertEqual(report["result_extraction"]["result_record_count"], 1)
            self.assertEqual(report["result_extraction"]["records_with_context_notes"], 1)
            self.assertTrue(report["interpretation"]["missing_usage_or_cost_metadata_is_unknown_not_zero"])


if __name__ == "__main__":
    unittest.main()
