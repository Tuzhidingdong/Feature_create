from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_task_queue import _chunk_evidence_index, _paper_chunk_index
from scripts.chunking import build_evidence_units, group_units_into_chunks


class CrossChunkRelationshipIndexTests(unittest.TestCase):
    def test_adjacent_text_segments_link_across_chunk_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            text = (
                "# Main Analysis\nDOI: 10.1234/linkage\n\n"
                "## Page 1 (MAIN)\n\n"
                + ("A reported result and its sample details. " * 50)
                + "\n\n## Page 2 (MAIN)\n\n"
                + ("Another reported result and its test conditions. " * 50)
            )
            (root / "main.md").write_text(text, encoding="utf-8")
            source = {
                "source_id": "SRC-TEXT",
                "source_role": "main",
                "source_type": "main_text",
                "component": "main_text",
                "extracted_text_path": "main.md",
            }
            units = build_evidence_units([source], root, max_unit_chars=600)
            chunks = group_units_into_chunks(units, max_chars=700, max_attachments=4)
            paper_index = _paper_chunk_index(
                {"paper_id": "PAPER-1", "doi": "10.1234/linkage"},
                chunks,
                {"package_version": "1.1.0"},
                "upstream-index-sha",
            )

            locators = [
                locator
                for chunk in paper_index["chunks"]
                for locator in chunk["text_segment_locators"]
            ]
            self.assertGreater(len(chunks), 1)
            self.assertEqual(len(units), len(locators))
            for locator in locators:
                if locator["next_segment"]:
                    self.assertEqual(
                        locator["source_text_offset_end"],
                        locator["next_segment"]["source_text_offset_start"],
                    )
                    self.assertTrue(locator["next_segment"]["chunk_id"])
                if locator["previous_segment"]:
                    self.assertTrue(locator["previous_segment"]["chunk_id"])
            self.assertTrue(any(
                locator["next_segment"]
                and locator["next_segment"]["chunk_id"] != locator["chunk_id"]
                for chunk in paper_index["chunks"]
                for locator in chunk["text_segment_locators"]
            ))

    def test_large_header_context_row_is_referenced_not_duplicated(self) -> None:
        long_cell = "large-cell-content-" * 300
        source = {
            "source_id": "SRC-LARGE-HEADER",
            "source_role": "main",
            "source_type": "main_table",
            "component": "main_tables",
            "table_id": "MAIN_TAB_P5_01",
            "relative_path": "preprocessing/tables/MAIN_TAB_P5_01.csv",
            "parse_status": "upstream_preprocessed_table",
            "sha256": "table-source-sha",
            "table_data": {
                "table_id": "MAIN_TAB_P5_01",
                "doc_type": "MAIN",
                "page_number": 5,
                "caption": "Long table row",
                "rows": [
                    ["Sample", "Large cell"],
                    ["S-01", long_cell],
                ],
            },
        }
        units = build_evidence_units([source], Path("."), max_unit_chars=900)
        chunks = group_units_into_chunks(units, max_chars=1000, max_attachments=4)
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "chunk_evidence_index.json"
            output = _chunk_evidence_index(
                {"paper_id": "PAPER-LARGE-HEADER"},
                chunks[0],
                {"SRC-LARGE-HEADER": source},
                {
                    "tables": [{
                        "table_id": "MAIN_TAB_P5_01",
                        "doc_type": "MAIN",
                        "page_number": 5,
                        "caption": "Long table row",
                        "col_count": 2,
                        "rows": [["Sample", "Large cell"], ["S-01", long_cell]],
                    }],
                },
                "upstream-index-sha",
                destination,
            )
            saved = json.loads(output.read_text(encoding="utf-8"))
            header_row = next(
                row for row in saved["tables"][0]["header_context_rows"]
                if row["row_index_1based"] == 2
            )

        self.assertEqual("locator_only_large_row", header_row["context_payload_kind"])
        self.assertNotIn("cells", header_row)
        self.assertEqual(
            hashlib.sha256(long_cell.encode("utf-8")).hexdigest(),
            header_row["cell_metadata"][1]["sha256"],
        )
        self.assertEqual(len(long_cell), header_row["cell_metadata"][1]["char_count"])

    def test_same_original_table_row_links_cell_units_across_chunks(self) -> None:
        long_a = "capacity-series-A-" * 80
        long_b = "retention-series-B-" * 80
        source = {
            "source_id": "SRC-TABLE",
            "source_role": "main",
            "source_type": "main_table",
            "component": "main_tables",
            "table_id": "MAIN_TAB_P3_01",
            "table_data": {
                "table_id": "MAIN_TAB_P3_01",
                "doc_type": "MAIN",
                "page_number": 3,
                "caption": "Long values for one sample row",
                "rows": [
                    ["Sample", "Capacity data", "Retention data"],
                    ["S-01", long_a, long_b],
                ],
            },
        }
        units = build_evidence_units([source], Path("."), max_unit_chars=900)
        chunks = group_units_into_chunks(units, max_chars=1000, max_attachments=4)
        paper_index = _paper_chunk_index(
            {"paper_id": "PAPER-2", "doi": "10.1234/table"},
            chunks,
            {"package_version": "1.1.0"},
            "upstream-table-index-sha",
        )

        table_locators = [
            locator
            for chunk in paper_index["chunks"]
            for locator in chunk["table_locators"]
        ]
        self.assertGreater(len(chunks), 1)
        row_groups = paper_index["same_original_table_row_groups"]
        self.assertTrue(row_groups)
        matching_groups = [
            group for group in row_groups
            if group["table_id"] == "MAIN_TAB_P3_01"
            and group["original_row_index_1based"] == 2
        ]
        self.assertEqual(1, len(matching_groups))
        group = matching_groups[0]
        self.assertGreater(group["unit_count"], 1)
        self.assertGreater(len(group["chunk_ids"]), 1)
        self.assertTrue(all(unit["chunk_id"] for unit in group["units"]))
        self.assertTrue(all(unit["unit_id"] for unit in group["units"]))
        referenced_group_ids = {
            group_id for locator in table_locators for group_id in locator["row_group_ids"]
        }
        self.assertIn(group["row_group_id"], referenced_group_ids)


if __name__ == "__main__":
    unittest.main()
