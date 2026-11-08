from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.validate_evidence import (
    EvidenceValidationError,
    load_assigned_chunk_units,
    verify_quote_in_assigned_text_unit,
    validate_payload,
)


class ChunkEvidenceValidationTests(unittest.TestCase):
    def test_loads_only_units_from_the_matching_chunk_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            index_path = root / "chunk_evidence_index.json"
            index_path.write_text(json.dumps({
                "chunk_id": "CHUNK-1",
                "assigned_units": [{
                    "unit_id": "UNIT-1",
                    "source_ids": ["SRC-1"],
                    "locator": {
                        "source_text_offset_start": 0,
                        "source_text_offset_end": 10,
                    },
                }],
            }), encoding="utf-8")
            task = {
                "chunk_id": "CHUNK-1",
                "chunk_evidence_index_path": str(index_path),
                "unit_ids": ["UNIT-1"],
            }

            result = load_assigned_chunk_units(task)

            self.assertEqual({"UNIT-1"}, set(result))
            self.assertEqual(["SRC-1"], result["UNIT-1"]["source_ids"])

    def test_rejects_chunk_index_with_mismatched_unit_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            index_path = root / "chunk_evidence_index.json"
            index_path.write_text(json.dumps({
                "chunk_id": "CHUNK-1",
                "assigned_units": [{"unit_id": "UNIT-OTHER", "source_ids": ["SRC-1"]}],
            }), encoding="utf-8")
            with self.assertRaises(EvidenceValidationError):
                load_assigned_chunk_units({
                    "chunk_id": "CHUNK-1",
                    "chunk_evidence_index_path": str(index_path),
                    "unit_ids": ["UNIT-1"],
                })

    def test_cross_chunk_context_review_validates_each_side_against_its_assigned_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current_text = "Capacity for sample S-01 was 152 mAh g-1."
            target_text = "S-01 is the electrolyte containing 0.2 M additive X."
            (root / "current.md").write_text(current_text, encoding="utf-8")
            (root / "target.md").write_text(target_text, encoding="utf-8")
            manifest_path = root / "context_evidence_manifest.json"
            manifest_path.write_text(json.dumps({
                "assigned_units": [
                    {
                        "unit_id": "UNIT-A",
                        "chunk_id": "CHUNK-A",
                        "source_ids": ["SRC-A"],
                        "review_side": "current",
                        "unit_kind": "text",
                        "locator": {
                            "source_text_offset_start": 0,
                            "source_text_offset_end": len(current_text),
                        },
                    },
                    {
                        "unit_id": "UNIT-B",
                        "chunk_id": "CHUNK-B",
                        "source_ids": ["SRC-B"],
                        "review_side": "target",
                        "unit_kind": "text",
                        "locator": {
                            "source_text_offset_start": 0,
                            "source_text_offset_end": len(target_text),
                        },
                    },
                ]
            }), encoding="utf-8")
            sources = {
                "SRC-A": {"paper_id": "PAPER-1", "source_type": "main_text", "extracted_text_path": "current.md"},
                "SRC-B": {"paper_id": "PAPER-1", "source_type": "main_text", "extracted_text_path": "target.md"},
            }
            current_ref = {
                "evidence_side": "current",
                "source_id": "SRC-A",
                "locator": {
                    "chunk_id": "CHUNK-A", "unit_id": "UNIT-A",
                    "source_text_offset_start": 0, "source_text_offset_end": len(current_text),
                },
                "quote_or_cell_value": "Capacity for sample S-01 was 152 mAh g-1.",
                "evidence_status": "verified_text",
            }
            target_ref = {
                "evidence_side": "target",
                "source_id": "SRC-B",
                "locator": {
                    "chunk_id": "CHUNK-B", "unit_id": "UNIT-B",
                    "source_text_offset_start": 0, "source_text_offset_end": len(target_text),
                },
                "quote_or_cell_value": "S-01 is the electrolyte containing 0.2 M additive X.",
                "evidence_status": "verified_text",
            }
            payload = {
                "contract_version": "0.1",
                "task_type": "cross_chunk_context",
                "task_id": "TASK-CX-1",
                "paper_id": "PAPER-1",
                "unresolved_id": "UNRES-1",
                "record_id": "REC-1",
                "decision": "association_confirmed",
                "rationale": "The same explicit sample identifier is used in both passages.",
                "review_evidence_refs": [current_ref, target_ref],
                "context_facts": [{
                    "context_role": "sample_identity",
                    "raw_label": "S-01",
                    "raw_value": "electrolyte containing 0.2 M additive X",
                    "unit": None,
                    "evidence_refs": [current_ref, target_ref],
                }],
            }
            task = {
                "task_id": "TASK-CX-1",
                "paper_id": "PAPER-1",
                "worker_role": "cross_chunk_context",
                "unresolved_id": "UNRES-1",
                "record_id": "REC-1",
                "source_ids": ["SRC-A", "SRC-B"],
                "current_unit_ids": ["UNIT-A"],
                "target_unit_ids": ["UNIT-B"],
                "context_evidence_manifest_path": str(manifest_path),
                "assigned_unit_ids": ["UNIT-A", "UNIT-B"],
            }

            result = validate_payload(payload, "cross_chunk_context", task, sources, root)

            self.assertEqual("evidence_checked", result["status"], result["errors"])
            self.assertEqual(4, result["verified_reference_count"])

            invalid = json.loads(json.dumps(payload))
            invalid["review_evidence_refs"][1]["quote_or_cell_value"] = "Capacity for sample S-01"
            bad_result = validate_payload(invalid, "cross_chunk_context", task, sources, root)
            self.assertEqual("needs_review", bad_result["status"])
            self.assertTrue(any("not present within the assigned text-unit range" in item for item in bad_result["errors"]))

    def test_quote_must_be_inside_assigned_text_segment_not_elsewhere_in_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_path = root / "main.md"
            source_text = "earlier result. assigned result. later result."
            source_path.write_text(source_text, encoding="utf-8")
            source = {"extracted_text_path": "main.md"}
            start = source_text.index("assigned")
            end = start + len("assigned result.")
            unit = {"locator": {
                "source_text_offset_start": start,
                "source_text_offset_end": end,
            }}

            self.assertTrue(
                verify_quote_in_assigned_text_unit(source, "assigned result.", unit, root)[0]
            )
            self.assertFalse(
                verify_quote_in_assigned_text_unit(source, "earlier result.", unit, root)[0]
            )


if __name__ == "__main__":
    unittest.main()
