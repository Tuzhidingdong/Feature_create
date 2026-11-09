from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts.export_txt import main


class ExportTxtTests(unittest.TestCase):
    def make_registry(self, status: str = "confirmed") -> dict:
        return {
            "contract_version": "0.1",
            "registry_id": "REG-TEST",
            "next_observation_sequence": 2,
            "next_feature_sequence": 2,
            "observations": [{
                "observation_id": "OBS-1",
                "paper_id": "PAPER-1",
                "source_id": "SRC-1",
                "raw_feature_name": "Coulombic efficiency",
                "raw_definition": "Discharge capacity divided by charge capacity",
                "reported_unit": "%",
                "reported_method": "galvanostatic cycling",
                "result_type": "measured",
                "variable_role": "result_feature",
                "evidence_refs": [{
                    "source_id": "SRC-1",
                    "source_type": "main_text",
                    "locator": {"page": 2},
                    "quote_or_cell_value": "CE was 99.1%",
                    "evidence_status": "verified_text",
                }],
                "origin_task_id": "TASK-1",
                "origin_worker": "source_discovery",
                "discovery_status": "evidence_verified",
                "linked_registry_feature_id": "RFEAT-1",
                "created_at": "2026-11-08T00:00:00+00:00",
            }],
            "canonical_features": [{
                "registry_feature_id": "RFEAT-1",
                "canonical_name": "Coulombic efficiency",
                "definition": "Discharge capacity divided by charge capacity, multiplied by 100%.",
                "semantic_signature": {
                    "physical_or_statistical_quantity": "coulombic efficiency",
                    "measurand_or_operational_definition": "discharge capacity / charge capacity",
                    "formula": "Qdischarge/Qcharge × 100%",
                    "numerator": "discharge capacity",
                    "denominator": "charge capacity",
                    "normalization_basis": None,
                    "statistic_or_aggregation": "per-cycle",
                    "temporal_or_cycle_scope": "per cycle",
                    "unit_dimension": "dimensionless ratio",
                    "method_scope": "galvanostatic cycling",
                },
                "aliases": ["CE", "Coulombic efficiency"],
                "unit_dimensions": ["dimensionless"],
                "measurement_methods": ["galvanostatic cycling"],
                "calculation_methods": [],
                "result_types": ["measured"],
                "observation_ids": ["OBS-1"],
                "existing_feature_id": None,
                "feature_status": status,
                "redirect_to": None,
                "created_at": "2026-11-08T00:00:00+00:00",
                "updated_at": "2026-11-08T00:00:00+00:00",
            }],
            "decision_log": [],
        }

    def make_record(self, feature_id: str = "RFEAT-1") -> dict:
        return {
            "record_id": "REC-1",
            "paper_id": "PAPER-1",
            "doi": "10.1234/example",
            "registry_feature_id": feature_id,
            "observation_id": "OBS-1",
            "raw_feature_name": "CE",
            "raw_value": "99.1",
            "numeric_value": 99.1,
            "reported_unit": "%",
            "result_type": "measured",
            "value_extraction_status": "exact_text",
            "extraction_status": "accepted",
            "statistic_label": "per cycle",
            "replicate_count": 3,
            "sample_identity": [{"raw_label": "Cathode", "raw_value": "PBA-A"}],
            "condition_facts": [{"raw_label": "Current density", "raw_value": "1 A g-1"}],
            "time_cycle_facts": [{"raw_label": "Cycle", "raw_value": "100"}],
            "protocol_facts": [],
            "other_context_facts": [],
            "measurement_or_calculation_method": "galvanostatic cycling",
            "source_id": "SRC-1",
            "source_type": "main_text",
            "source_location": [{"page": 2}],
            "evidence_refs": [{"source_id": "SRC-1", "locator": {"page": 2}}],
            "evidence_status": "verified_text",
            "evidence_verification": {"locally_verified": True},
            "context_notes": None,
            "unresolved_issues": [],
            "worker_task_id": "TASK-1",
            "worker_model": "opencode-go/crouton-alpha-free",
            "created_at": "2026-11-08T00:00:00+00:00",
        }

    def invoke_export(
        self,
        root: Path,
        registry: dict,
        record: dict,
        review_rows: list[dict],
        allow_unresolved_context: bool = False,
    ) -> int:
        registry_path = root / "registry.json"
        records_path = root / "records.jsonl"
        review_path = root / "pairs.jsonl"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        records_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        review_path.write_text("".join(json.dumps(row) + "\n" for row in review_rows), encoding="utf-8")
        output = root / "formal"
        return main([
            "--registry", str(registry_path),
            "--records", str(records_path),
            "--review-queue", str(review_path),
            "--output-dir", str(output),
            *(
                ["--allow-unresolved-context"]
                if allow_unresolved_context else []
            ),
        ])

    def test_exports_exactly_two_tsv_txt_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            status = self.invoke_export(root, self.make_registry(), self.make_record(), [])
            self.assertEqual(status, 0)
            output = root / "formal"
            self.assertTrue((output / "result_feature_records.txt").is_file())
            self.assertTrue((output / "result_feature_list.txt").is_file())
            self.assertTrue((root / "formal_export_audit.json").is_file())

            with (output / "result_feature_records.txt").open(encoding="utf-8", newline="") as handle:
                records = list(csv.DictReader(handle, delimiter="\t"))
            with (output / "result_feature_list.txt").open(encoding="utf-8", newline="") as handle:
                features = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["RAW_VALUE"], "99.1")
            self.assertEqual(records[0]["FEATURE_ID"], "RFEAT-1")
            self.assertEqual(records[0]["CONTEXT_ASSOCIATION_STATUS"], "not_required")
            self.assertEqual(json.loads(records[0]["OPEN_CONTEXT_UNRESOLVED_ITEMS"]), [])
            self.assertEqual(json.loads(records[0]["CONDITION_FACTS"])[0]["raw_label"], "Current density")
            self.assertEqual(len(features), 1)
            self.assertEqual(features[0]["CANONICAL_NAME"], "Coulombic efficiency")
            self.assertNotIn("99.1", (output / "result_feature_list.txt").read_text(encoding="utf-8"))
            self.assertEqual(
                sorted(path.name for path in output.glob("*.txt")),
                ["result_feature_list.txt", "result_feature_records.txt"],
            )

    def test_blocks_export_when_features_are_not_reconciled(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            status = self.invoke_export(
                root,
                self.make_registry(status="under_review"),
                self.make_record(),
                [{"candidate_pair_id": "PAIR-1", "decision_status": "under_review"}],
            )
            self.assertEqual(status, 2)
            self.assertFalse((root / "formal" / "result_feature_records.txt").exists())
            report = json.loads((root / "formal_export_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "blocked")
            self.assertTrue(any("remain pending" in item for item in report["errors"]))

    def test_resolves_retired_feature_redirect_for_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry = self.make_registry()
            retired = dict(registry["canonical_features"][0])
            retired.update({
                "registry_feature_id": "RFEAT-OLD",
                "canonical_name": "CE",
                "feature_status": "retired_merged",
                "redirect_to": "RFEAT-1",
                "observation_ids": [],
            })
            registry["canonical_features"].append(retired)
            record = self.make_record(feature_id="RFEAT-OLD")
            status = self.invoke_export(root, registry, record, [])
            self.assertEqual(status, 0)
            with (root / "formal" / "result_feature_records.txt").open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(row["FEATURE_ID"], "RFEAT-1")


    def test_blocks_export_when_context_association_is_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = self.make_record()
            record["unresolved_issues"] = [{
                "unresolved_id": "UNRES-PENDING",
                "item_type": "missing_context",
                "description": "Sample identity association crosses chunks.",
                "resolution_status": "pending",
            }]
            status = self.invoke_export(root, self.make_registry(), record, [])
            self.assertEqual(status, 2)
            self.assertFalse((root / "formal" / "result_feature_records.txt").exists())
            report = json.loads((root / "formal_export_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["open_context_issue_count"], 1)
            self.assertEqual(report["records_with_open_context"], 1)
            self.assertEqual(report["context_association_status_counts"], {"pending": 1})
            self.assertTrue(any("context association item(s) remain unresolved" in item for item in report["errors"]))

    def test_explicit_override_exports_open_context_as_provisional(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = self.make_record()
            record["unresolved_issues"] = [{
                "unresolved_id": "UNRES-OPEN",
                "item_type": "missing_context",
                "description": "Condition association could not be established.",
                "resolution_status": "insufficient_evidence",
            }]
            status = self.invoke_export(
                root, self.make_registry(), record, [], allow_unresolved_context=True
            )
            self.assertEqual(status, 0)
            with (root / "formal" / "result_feature_records.txt").open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(row["CONTEXT_ASSOCIATION_STATUS"], "inconclusive")
            open_items = json.loads(row["OPEN_CONTEXT_UNRESOLVED_ITEMS"])
            self.assertEqual(open_items[0]["unresolved_id"], "UNRES-OPEN")
            self.assertEqual(open_items[0]["resolution_status"], "insufficient_evidence")
            self.assertEqual(row["RAW_VALUE"], "99.1")
            report = json.loads((root / "formal_export_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "exported_with_unresolved_context")
            self.assertTrue(report["warnings"])
            self.assertEqual(report["open_context_issue_count"], 1)

    def test_confirmed_and_no_association_decisions_close_context_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = self.make_record()
            record["unresolved_issues"] = [
                {
                    "unresolved_id": "UNRES-CONFIRMED",
                    "item_type": "missing_context",
                    "description": "Sample identity was checked across chunks.",
                    "resolution_status": "association_confirmed",
                },
                {
                    "unresolved_id": "UNRES-NO-LINK",
                    "item_type": "missing_context",
                    "description": "Condition statement refers to a different sample.",
                    "resolution_status": "no_association",
                },
            ]
            status = self.invoke_export(root, self.make_registry(), record, [])
            self.assertEqual(status, 0)
            with (root / "formal" / "result_feature_records.txt").open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(row["CONTEXT_ASSOCIATION_STATUS"], "resolved")
            self.assertEqual(json.loads(row["OPEN_CONTEXT_UNRESOLVED_ITEMS"]), [])
            self.assertEqual(json.loads(row["UNRESOLVED_ISSUES"])[1]["resolution_status"], "no_association")
            report = json.loads((root / "formal_export_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "exported")
            self.assertEqual(report["open_context_issue_count"], 0)

    def test_conflicting_context_evidence_blocks_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = self.make_record()
            record["unresolved_issues"] = [{
                "unresolved_id": "UNRES-CONFLICT",
                "item_type": "missing_context",
                "description": "Conflicting sample association evidence.",
                "resolution_status": "conflicting_evidence",
            }]
            status = self.invoke_export(root, self.make_registry(), record, [])
            self.assertEqual(status, 2)
            self.assertFalse((root / "formal" / "result_feature_records.txt").exists())
            report = json.loads((root / "formal_export_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(report["context_association_status_counts"], {"inconclusive": 1})

    def test_unknown_context_resolution_status_fails_closed_even_with_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = self.make_record()
            record["unresolved_issues"] = [{
                "unresolved_id": "UNRES-UNKNOWN",
                "item_type": "missing_context",
                "description": "Unknown state must not be assumed resolved.",
                "resolution_status": "auto_resolved",
            }]
            status = self.invoke_export(
                root, self.make_registry(), record, [], allow_unresolved_context=True
            )
            self.assertEqual(status, 2)
            self.assertFalse((root / "formal" / "result_feature_records.txt").exists())
            report = json.loads((root / "formal_export_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(report["invalid_context_status_count"], 1)
            self.assertTrue(any("unknown resolution status" in item for item in report["errors"]))


if __name__ == "__main__":
    unittest.main()
