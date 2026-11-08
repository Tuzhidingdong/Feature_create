from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.audit_records import aggregate_chunk_coverage, coverage_for_task, main


class AuditRecordsTests(unittest.TestCase):
    def test_cost_audit_includes_cross_paper_and_schema_mapping_workers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recon_out = root / "recon"
            mapping_out = root / "mapping"
            recon_out.mkdir()
            mapping_out.mkdir()
            common_state = {
                "status": "complete",
                "requested_model": "opencode-go/beam-free",
                "observed_models": ["opencode-go/beam-free"],
                "reported_cost": 0,
                "tokens": {
                    "input": 100, "output": 20, "reasoning": 0,
                    "cache_read": 0, "cache_write": 0,
                },
            }
            (recon_out / "task_state.json").write_text(json.dumps(common_state), encoding="utf-8")
            (mapping_out / "task_state.json").write_text(json.dumps(common_state), encoding="utf-8")

            manifest = {"corpus_fingerprint": "fingerprint", "sources": [], "papers": []}
            queue = {"tasks": []}
            recon_queue = {"tasks": [{
                "task_id": "RECON-1",
                "worker_role": "semantic_reconciliation",
                "model_ref": "opencode-go/beam-free",
                "output_dir": str(recon_out),
            }]}
            mapping_queue = {"tasks": [{
                "task_id": "MAPPING-1",
                "worker_role": "existing_schema_mapping",
                "model_ref": "opencode-go/beam-free",
                "output_dir": str(mapping_out),
            }]}
            registry = {"canonical_features": []}

            paths = {
                "manifest": root / "manifest.json",
                "queue": root / "queue.json",
                "recon": root / "reconciliation.json",
                "mapping": root / "mapping.json",
                "registry": root / "registry.json",
                "records": root / "records.jsonl",
                "review": root / "review.jsonl",
                "output": root / "audit.json",
            }
            paths["manifest"].write_text(json.dumps(manifest), encoding="utf-8")
            paths["queue"].write_text(json.dumps(queue), encoding="utf-8")
            paths["recon"].write_text(json.dumps(recon_queue), encoding="utf-8")
            paths["mapping"].write_text(json.dumps(mapping_queue), encoding="utf-8")
            paths["registry"].write_text(json.dumps(registry), encoding="utf-8")
            paths["records"].write_text("", encoding="utf-8")
            paths["review"].write_text("", encoding="utf-8")

            code = main([
                "--manifest", str(paths["manifest"]),
                "--queue", str(paths["queue"]),
                "--reconciliation-queue", str(paths["recon"]),
                "--mapping-queue", str(paths["mapping"]),
                "--registry", str(paths["registry"]),
                "--records", str(paths["records"]),
                "--review-queue", str(paths["review"]),
                "--run-dir", str(root / "run"),
                "--output", str(paths["output"]),
            ])
            self.assertEqual(code, 0)
            report = json.loads(paths["output"].read_text(encoding="utf-8"))
            self.assertEqual(report["worker_task_totals"]["all_worker_task_count"], 2)
            self.assertEqual(
                report["model_usage_and_cost"]["semantic_reconciliation"]["reported_cost_sum"],
                0,
            )
            self.assertEqual(
                report["model_usage_and_cost"]["existing_schema_mapping"]["token_usage_sum"]["input"],
                100,
            )
            self.assertFalse(
                report["model_usage_and_cost"]["semantic_reconciliation"]["paid_cost_detected"]
            )

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


class ChunkCoverageAggregationTests(unittest.TestCase):
    def test_coverage_for_task_keeps_separate_scopes_of_same_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            (output / "response.json").write_text(json.dumps({
                "coverage": [
                    {"source_id": "SRC-1", "scope_id": "SCP-1", "status": "fully_reviewed"},
                    {"source_id": "SRC-1", "scope_id": "SCP-2", "status": "partially_reviewed"},
                ]
            }), encoding="utf-8")
            task = {
                "worker_role": "source_discovery",
                "output_dir": str(output),
                "source_scopes": [
                    {"source_id": "SRC-1", "scope_id": "SCP-1"},
                    {"source_id": "SRC-1", "scope_id": "SCP-2"},
                ],
            }
            self.assertEqual(
                {"SCP-1": "fully_reviewed", "SCP-2": "partially_reviewed"},
                coverage_for_task(task),
            )

    def test_duplicate_scope_coverage_cannot_be_counted_as_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            (output / "response.json").write_text(json.dumps({
                "coverage": [
                    {"source_id": "SRC-1", "scope_id": "SCP-1", "status": "fully_reviewed"},
                    {"source_id": "SRC-1", "scope_id": "SCP-1", "status": "fully_reviewed"},
                ]
            }), encoding="utf-8")
            task = {
                "worker_role": "source_discovery",
                "output_dir": str(output),
                "source_scopes": [{"source_id": "SRC-1", "scope_id": "SCP-1"}],
            }
            self.assertEqual(
                {"SCP-1": "duplicate_coverage_entry"},
                coverage_for_task(task),
            )

    def test_source_is_complete_only_when_every_assigned_chunk_is_complete(self) -> None:
        result = aggregate_chunk_coverage(
            {
                "SRC-MAIN": ["fully_reviewed", "fully_reviewed"],
                "SRC-SI": ["fully_reviewed", "missing_chunk_coverage"],
                "SRC-BLOCKED": ["missing_chunk_coverage", "missing_chunk_coverage"],
            },
            "fully_reviewed",
        )
        self.assertEqual("fully_reviewed", result["SRC-MAIN"])
        self.assertEqual("partially_reviewed", result["SRC-SI"])
        self.assertEqual("not_reviewed", result["SRC-BLOCKED"])

    def test_worker_b_uses_its_own_success_status(self) -> None:
        result = aggregate_chunk_coverage(
            {
                "SRC-SI": ["reviewed", "reviewed"],
                "SRC-PARTIAL": ["reviewed", "partially_reviewed"],
            },
            "reviewed",
        )
        self.assertEqual("reviewed", result["SRC-SI"])
        self.assertEqual("partially_reviewed", result["SRC-PARTIAL"])


if __name__ == "__main__":
    unittest.main()
