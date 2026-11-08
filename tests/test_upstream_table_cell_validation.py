from __future__ import annotations

import unittest

from scripts.validate_evidence import verify_table_cell


class UpstreamTableCellValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = {
            "source_type": "main_table",
            "table_data": {
                "table_id": "MAIN_TAB_P3_01",
                "rows": [
                    ["Metric", "Value"],
                    ["Capacity", "220 mAh g-1"],
                    ["Retention", "91%"],
                ],
            },
        }

    def test_verifies_exact_one_based_table_cell(self) -> None:
        ok, note = verify_table_cell(
            self.source,
            {"table_id": "MAIN_TAB_P3_01", "row_index_1based": 2, "column_index_1based": 2},
            "220 mAh g-1",
        )
        self.assertTrue(ok)
        self.assertIn("exact upstream table cell verified", note)

    def test_rejects_wrong_value_at_cited_cell(self) -> None:
        ok, note = verify_table_cell(
            self.source,
            {"row_index_1based": 3, "column_index_1based": 2},
            "220 mAh g-1",
        )
        self.assertFalse(ok)
        self.assertIn("does not match", note)

    def test_rejects_out_of_range_cell(self) -> None:
        ok, note = verify_table_cell(
            self.source,
            {"row_index_1based": 8, "column_index_1based": 2},
            "220 mAh g-1",
        )
        self.assertFalse(ok)
        self.assertIn("out of range", note)

    def test_requires_machine_checkable_coordinates(self) -> None:
        ok, note = verify_table_cell(self.source, {"table_id": "MAIN_TAB_P3_01"}, "220 mAh g-1")
        self.assertIsNone(ok)
        self.assertIn("row_index_1based", note)

    def test_accepts_a1_style_cell_address(self) -> None:
        ok, _ = verify_table_cell(
            self.source, {"cell_address": "B2"}, "220 mAh g-1"
        )
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
