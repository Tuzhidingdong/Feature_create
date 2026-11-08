from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.validate_evidence import (
    EvidenceValidationError,
    load_assigned_chunk_units,
    verify_quote_in_assigned_text_unit,
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
