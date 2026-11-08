from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.build_task_queue import _paper_chunk_index
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
        linked = [
            relation
            for locator in table_locators
            for relation in locator["same_row_related_units"]
        ]
        self.assertTrue(linked)
        self.assertTrue(all(relation["chunk_id"] for relation in linked))
        self.assertTrue(any(
            relation["chunk_id"] != locator["chunk_id"]
            for locator in table_locators
            for relation in locator["same_row_related_units"]
        ))
        self.assertTrue(all(
            relation["unit_id"] != locator["unit_id"]
            for locator in table_locators
            for relation in locator["same_row_related_units"]
        ))


if __name__ == "__main__":
    unittest.main()
