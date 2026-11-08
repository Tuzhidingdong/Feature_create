from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.chunking import build_evidence_units, group_units_into_chunks


class EvidenceChunkingTests(unittest.TestCase):
    def test_page_text_is_split_losslessly_and_keeps_original_page_locators(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            text = (
                "# Main Analysis\nDOI: 10.1234/example\n\n"
                "## Page 1 (MAIN)\n\n" + ("Page one paragraph with a reported result. " * 8) + "\n\n"
                "## Page 2 (MAIN)\n\n" + ("Page two paragraph with a second result. " * 8) + "\n"
            )
            (root / "source.md").write_text(text, encoding="utf-8")
            source = {
                "source_id": "SRC-MAIN",
                "source_role": "main",
                "source_type": "main_text",
                "component": "main_text",
                "extracted_text_path": "source.md",
            }

            units = build_evidence_units([source], root, max_unit_chars=120)

            self.assertGreater(len(units), 2)
            self.assertEqual("".join(unit["content"] for unit in units), text)
            self.assertEqual({1, 2}, {unit["page_number"] for unit in units})
            self.assertTrue(all(unit["source_id"] == "SRC-MAIN" for unit in units))
            self.assertTrue(all(unit["locator"]["source_text_offset_end"] > unit["locator"]["source_text_offset_start"] for unit in units))

    def test_large_table_is_split_without_renumbering_original_rows(self) -> None:
        source = {
            "source_id": "SRC-TABLE",
            "source_role": "main",
            "source_type": "main_table",
            "component": "main_tables",
            "table_id": "MAIN_TAB_P2_01",
            "page_number": 2,
            "table_data": {
                "table_id": "MAIN_TAB_P2_01",
                "doc_type": "MAIN",
                "page_number": 2,
                "caption": "Electrochemical performance",
                "rows": [
                    ["Sample", "Capacity (mAh g-1)", "Retention (%)"],
                    ["Blank", "120", "80"],
                    ["A-1", "145", "91"],
                    ["A-2", "151", "93"],
                    ["B-1", "139", "88"],
                    ["B-2", "142", "90"],
                    ["C-1", "160", "95"],
                ],
            },
        }

        units = build_evidence_units([source], Path("."), max_unit_chars=260)

        self.assertGreater(len(units), 1)
        assigned = [
            row_index
            for unit in units
            for row_index in unit["locator"]["row_indices_1based"]
        ]
        self.assertEqual(list(range(1, 8)), sorted(assigned))
        self.assertEqual(len(assigned), len(set(assigned)))
        for unit in units:
            self.assertEqual("MAIN_TAB_P2_01", unit["locator"]["table_id"])
            self.assertIn("ORIGINAL_ROW_INDEX_1BASED", unit["content"])
            self.assertIn("TABLE PREFIX ROWS FOR HEADER/STRUCTURE CONTEXT ONLY", unit["content"])

    def test_visual_asset_is_attached_once_to_its_parent_table_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_path = root / "table.png"
            image_path.write_bytes(b"upstream-extracted-image")
            table = {
                "source_id": "SRC-TABLE",
                "source_role": "main",
                "source_type": "main_table",
                "component": "main_tables",
                "table_id": "MAIN_TAB_P1_01",
                "page_number": 1,
                "table_data": {
                    "table_id": "MAIN_TAB_P1_01",
                    "doc_type": "MAIN",
                    "page_number": 1,
                    "rows": [["A", "B"], ["1", "2"]],
                },
            }
            asset = {
                "source_id": "SRC-ASSET",
                "source_role": "main",
                "source_type": "main_table",
                "component": "main_tables",
                "table_id": "MAIN_TAB_P1_01",
                "asset_id": "ASSET_00001",
                "asset_type": "table_image",
                "is_visual_asset": True,
                "attachment_path": str(image_path),
                "sha256": "abc",
            }

            units = build_evidence_units([table, asset], root, max_unit_chars=10000)

            self.assertEqual(1, len(units))
            self.assertEqual({"SRC-TABLE", "SRC-ASSET"}, set(units[0]["source_ids"]))
            self.assertEqual([str(image_path.resolve())], units[0]["attachment_paths"])
            self.assertEqual(["ASSET_00001"], units[0]["asset_ids"])

    def test_chunk_grouping_obeys_attachment_cap_and_retains_all_units(self) -> None:
        units = []
        for index in range(5):
            units.append({
                "unit_id": f"UNIT-{index}",
                "source_id": f"SRC-{index}",
                "source_ids": [f"SRC-{index}"],
                "content": "x" * 80,
                "attachment_paths": [f"C:/assets/{index}.png"],
            })

        chunks = group_units_into_chunks(units, max_chars=200, max_attachments=2)

        self.assertGreater(len(chunks), 1)
        self.assertEqual(
            {unit["unit_id"] for chunk in chunks for unit in chunk["units"]},
            {f"UNIT-{index}" for index in range(5)},
        )
        self.assertTrue(all(len(chunk["attachment_paths"]) <= 2 for chunk in chunks))
        self.assertEqual(list(range(1, len(chunks) + 1)), [chunk["chunk_index"] for chunk in chunks])
        self.assertTrue(all(chunk["chunk_count"] == len(chunks) for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
