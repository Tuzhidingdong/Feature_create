from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.adjudicate_unresolved import main


class HumanContextAdjudicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manifest_path = self.root / "prepared" / "source_manifest.json"
        self.registry_path = self.root / "global_feature_registry.json"
        self.records_path = self.root / "result_records.jsonl"
        self.unresolved_path = self.root / "unresolved_extraction_items.jsonl"
        self.template_path = self.root / "manual_context_template.jsonl"
        self.decisions_path = self.root / "manual_context_decisions.jsonl"
        self.report_path = self.root / "manual_context_report.json"

        main_text = self.root / "prepared" / "text" / "main.txt"
        si_text = self.root / "prepared" / "text" / "si.txt"
        main_text.parent.mkdir(parents=True, exist_ok=True)
        main_text.write_text("Capacity of S-01 was 150 mAh g-1.", encoding="utf-8")
        si_text.write_text("S-01 denotes the electrolyte containing 0.2 M additive X.", encoding="utf-8")
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json(self.manifest_path, {
            "sources": [
                {
                    "source_id": "SRC-MAIN",
                    "paper_id": "PAPER-1",
                    "source_type": "main_text",
                    "extracted_text_path": "text/main.txt",
                },
                {
                    "source_id": "SRC-SI",
                    "paper_id": "PAPER-1",
                    "source_type": "si_text",
                    "extracted_text_path": "text/si.txt",
                },
            ],
            "papers": [{"paper_id": "PAPER-1"}],
        })
        self._write_json(self.registry_path, {"contract_version": "0.1"})
        self.record = {
            "record_id": "REC-1",
            "origin_candidate_record_id": "CAND-REC-1",
            "origin_candidate_observation_id": "CAND-OBS-1",
            "paper_id": "PAPER-1",
            "worker_task_id": "TASK-A",
            "registry_feature_id": "RFEAT-1",
            "observation_id": "OBS-1",
            "raw_feature_name": "Capacity",
            "raw_value": "150",
            "numeric_value": 150.0,
            "reported_unit": "mAh g-1",
            "sample_identity": [],
            "condition_facts": [],
            "time_cycle_facts": [],
            "protocol_facts": [],
            "other_context_facts": [],
            "unresolved_issues": [],
            "context_review_history": [],
        }
        self.unresolved = {
            "unresolved_id": "UNRES-1",
            "item_type": "missing_context",
            "description": "The sample code needs to be linked to its electrolyte composition.",
            "paper_id": "PAPER-1",
            "worker_task_id": "TASK-A",
            "related_candidate_record_id": "CAND-REC-1",
            "related_candidate_observation_id": "CAND-OBS-1",
            "resolution_status": "insufficient_evidence",
            "evidence_refs": [],
            "cross_chunk_target": {
                "context_role": "sample_identity",
                "relation_basis": "main_si_reference",
                "target_units": [],
                "rationale": "The sample label is defined in SI.",
            },
            "resolution_history": [],
        }
        self._write_jsonl(self.records_path, [self.record])
        self._write_jsonl(self.unresolved_path, [self.unresolved])

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _write_jsonl(path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _prepare(self) -> dict:
        code = main([
            "--prepare",
            "--manifest", str(self.manifest_path),
            "--registry", str(self.registry_path),
            "--records", str(self.records_path),
            "--unresolved", str(self.unresolved_path),
            "--output-template", str(self.template_path),
            "--report", str(self.report_path),
        ])
        self.assertEqual(code, 0)
        return self._read_jsonl(self.template_path)[0]

    def _ref(self, side: str, source_id: str, quote: str) -> dict:
        return {
            "evidence_side": side,
            "source_id": source_id,
            "locator": {"page_number": 1, "chunk_id": "CHUNK-" + side.upper(), "unit_id": "UNIT-" + side.upper()},
            "quote_or_cell_value": quote,
            "evidence_status": "verified_text",
        }

    def _valid_both_sides(self) -> list[dict]:
        return [
            self._ref("current", "SRC-MAIN", "Capacity of S-01 was 150 mAh g-1."),
            self._ref("target", "SRC-SI", "S-01 denotes the electrolyte containing 0.2 M additive X."),
        ]

    def _apply(self, template: dict, decision: str, **overrides) -> int:
        row = dict(template)
        row.update({
            "decision": decision,
            "rationale": "The cited current and supplementary passages support this decision.",
            "review_evidence_refs": self._valid_both_sides() if decision in {"association_confirmed", "no_association"} else [],
            "context_facts": [],
        })
        row.update(overrides)
        self._write_jsonl(self.decisions_path, [row])
        return main([
            "--apply",
            "--manifest", str(self.manifest_path),
            "--registry", str(self.registry_path),
            "--records", str(self.records_path),
            "--unresolved", str(self.unresolved_path),
            "--template", str(self.template_path),
            "--decisions", str(self.decisions_path),
            "--report", str(self.report_path),
        ])

    def test_prepare_creates_snapshot_bound_candidate_template(self) -> None:
        template = self._prepare()
        self.assertEqual(template["unresolved_id"], "UNRES-1")
        self.assertEqual(template["record_id"], "REC-1")
        self.assertFalse(template["record_selection_required"])
        self.assertEqual(template["candidate_records"][0]["raw_value"], "150")
        self.assertEqual(len(template["unresolved_snapshot_sha256"]), 64)
        self.assertEqual(len(template["source_manifest_sha256"]), 64)
        self.assertEqual(len(template["paper_source_text_sha256"]["SRC-MAIN"]), 64)

    def test_confirmed_decision_adds_verified_context_and_closes_issue(self) -> None:
        template = self._prepare()
        refs = self._valid_both_sides()
        fact = {
            "context_role": "sample_identity",
            "raw_label": "S-01",
            "raw_value": "Electrolyte containing 0.2 M additive X",
            "unit": None,
            "evidence_refs": refs,
        }
        code = self._apply(
            template,
            "association_confirmed",
            context_facts=[fact],
        )
        self.assertEqual(code, 0)
        record = self._read_jsonl(self.records_path)[0]
        unresolved = self._read_jsonl(self.unresolved_path)[0]
        self.assertEqual(record["raw_value"], "150")
        self.assertEqual(len(record["sample_identity"]), 1)
        self.assertEqual(record["sample_identity"][0]["raw_value"], fact["raw_value"])
        self.assertEqual(record["sample_identity"][0]["human_adjudication_ids"], [template["decision_id"]])
        self.assertEqual(unresolved["resolution_status"], "association_confirmed")
        self.assertEqual(unresolved["resolution_actor"], "human")
        self.assertEqual(unresolved["manual_review_history"][0]["actor"], "human")
        self.assertEqual(record["unresolved_issues"][0]["resolution_status"], "association_confirmed")

    def test_no_association_requires_positive_counterevidence_basis(self) -> None:
        template = self._prepare()
        code = self._apply(
            template,
            "no_association",
            counterevidence_basis="explicit_other_sample_mapping",
        )
        self.assertEqual(code, 0)
        record = self._read_jsonl(self.records_path)[0]
        unresolved = self._read_jsonl(self.unresolved_path)[0]
        self.assertEqual(record["sample_identity"], [])
        self.assertEqual(unresolved["resolution_status"], "no_association")
        self.assertEqual(unresolved["manual_review_history"][0]["counterevidence_basis"], "explicit_other_sample_mapping")

    def test_remain_unresolved_retains_blocking_state_and_records_human_review(self) -> None:
        template = self._prepare()
        code = self._apply(
            template,
            "remain_unresolved",
            rationale="The existing source excerpts do not establish a reliable association.",
        )
        self.assertEqual(code, 0)
        record = self._read_jsonl(self.records_path)[0]
        unresolved = self._read_jsonl(self.unresolved_path)[0]
        self.assertEqual(unresolved["resolution_status"], "insufficient_evidence")
        self.assertEqual(unresolved["last_human_review_decision"], "remain_unresolved")
        self.assertEqual(record["unresolved_issues"][0]["resolution_status"], "insufficient_evidence")
        self.assertEqual(record["context_review_history"][0]["decision"], "remain_unresolved")

    def test_stale_record_snapshot_blocks_without_mutating_files(self) -> None:
        template = self._prepare()
        changed = self._read_jsonl(self.records_path)[0]
        changed["raw_value"] = "151"
        self._write_jsonl(self.records_path, [changed])
        before_records = self.records_path.read_bytes()
        before_unresolved = self.unresolved_path.read_bytes()
        code = self._apply(template, "association_confirmed", context_facts=[{
            "context_role": "sample_identity",
            "raw_label": "S-01",
            "raw_value": "Electrolyte containing 0.2 M additive X",
            "evidence_refs": self._valid_both_sides(),
        }])
        self.assertEqual(code, 2)
        self.assertEqual(self.records_path.read_bytes(), before_records)
        self.assertEqual(self.unresolved_path.read_bytes(), before_unresolved)

    def test_unverifiable_quote_blocks_resolution(self) -> None:
        template = self._prepare()
        bad_refs = [
            self._ref("current", "SRC-MAIN", "This sentence is not in the paper."),
            self._ref("target", "SRC-SI", "S-01 denotes the electrolyte containing 0.2 M additive X."),
        ]
        code = self._apply(
            template,
            "association_confirmed",
            review_evidence_refs=bad_refs,
            context_facts=[{
                "context_role": "sample_identity",
                "raw_label": "S-01",
                "raw_value": "Electrolyte containing 0.2 M additive X",
                "evidence_refs": self._valid_both_sides(),
            }],
        )
        self.assertEqual(code, 2)
        unresolved = self._read_jsonl(self.unresolved_path)[0]
        self.assertEqual(unresolved["resolution_status"], "insufficient_evidence")
        self.assertNotIn("resolution_actor", unresolved)

    def test_observation_level_link_requires_explicit_record_selection(self) -> None:
        template_source = self._read_jsonl(self.records_path)[0]
        second = dict(template_source)
        second.update({
            "record_id": "REC-2",
            "origin_candidate_record_id": "CAND-REC-2",
            "raw_value": "145",
        })
        self._write_jsonl(self.records_path, [template_source, second])
        unresolved = dict(self.unresolved)
        unresolved["related_candidate_record_id"] = None
        self.unresolved_path.write_text(json.dumps(unresolved) + "\n", encoding="utf-8")
        template = self._prepare()
        self.assertEqual(template["record_id"], "")
        self.assertTrue(template["record_selection_required"])
        self.assertEqual({item["record_id"] for item in template["candidate_records"]}, {"REC-1", "REC-2"})


if __name__ == "__main__":
    unittest.main()
