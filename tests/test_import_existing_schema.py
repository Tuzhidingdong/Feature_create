from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.import_existing_schema import (
    canonicalize_row,
    parse_json_schema,
    parse_text_blocks,
    parse_tsv,
    main,
)


class ImportExistingSchemaTests(unittest.TestCase):
    def test_parses_tsv_feature_dictionary(self) -> None:
        text = (
            "FEATURE_ID\tCANONICAL_NAME\tALIASES\tDEFINITION\tUNITS_AND_DIMENSION\n"
            "F001\tCoulombic efficiency\tCE; coulombic efficiency\tDischarge capacity / charge capacity\t%\n"
        )
        features = parse_tsv(text)
        self.assertEqual(features[0]["FEATURE_ID"], "F001")
        self.assertIn("CE", features[0]["ALIASES"])
        self.assertEqual(features[0]["UNITS_AND_DIMENSION"]["reported_units"], ["%"])

    def test_parses_json_feature_list(self) -> None:
        text = json.dumps({
            "features": [{
                "FEATURE_ID": "F002",
                "CANONICAL_NAME": "Specific capacity",
                "ALIASES": ["gravimetric capacity"],
                "DEFINITION": "Capacity per active mass.",
                "RESULT_TYPE": ["measured"],
                "UNITS_AND_DIMENSION": {"reported_units": ["mAh g-1"], "dimensions": ["charge/mass"]},
                "MEASUREMENT_METHODS": ["galvanostatic cycling"],
                "CALCULATION_METHODS": [],
            }]
        })
        features = parse_json_schema(text)
        self.assertEqual(features[0]["FEATURE_ID"], "F002")
        self.assertEqual(features[0]["UNITS_AND_DIMENSION"]["dimensions"], ["charge/mass"])

    def test_parses_multiple_legacy_text_blocks(self) -> None:
        text = """
[FEATURE]
FEATURE_ID: F003
CANONICAL_NAME: Coulombic efficiency
ALIASES: CE; Coulombic efficiency
DEFINITION: Discharge capacity divided by charge capacity.

[FEATURE]
FEATURE_ID: F004
CANONICAL_NAME: Specific capacity
ALIASES: Gravimetric capacity
DEFINITION: Capacity normalized by active mass.
"""
        features = parse_text_blocks(text)
        self.assertEqual([item["FEATURE_ID"] for item in features], ["F003", "F004"])
        self.assertEqual(features[0]["ALIASES"][0], "CE")

    def test_parses_legacy_text_blocks_without_explicit_markers(self) -> None:
        text = """
FEATURE_ID: F005
CANONICAL_NAME: Capacity retention
ALIASES: retention
DEFINITION: Remaining capacity as a fraction of initial capacity.

FEATURE_ID: F006
CANONICAL_NAME: Areal capacity
ALIASES: areal storage capacity
DEFINITION: Capacity per electrode area.
"""
        features = parse_text_blocks(text)
        self.assertEqual([item["FEATURE_ID"] for item in features], ["F005", "F006"])

    def test_importer_writes_normalized_schema_with_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "result_feature_list.txt"
            output = root / "existing_feature_schema.json"
            source.write_text(
                "FEATURE_ID: F007\nCANONICAL_NAME: Energy density\nDEFINITION: Energy per unit volume.\n",
                encoding="utf-8",
            )
            code = main(["--input", str(source), "--output", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["source_format"], "text_blocks")
            self.assertEqual(len(result["source_sha256"]), 64)
            self.assertEqual(result["features"][0]["FEATURE_ID"], "F007")

    def test_rejects_rows_without_id(self) -> None:
        with self.assertRaises(Exception):
            canonicalize_row({"CANONICAL_NAME": "Missing ID"})


if __name__ == "__main__":
    unittest.main()
