from __future__ import annotations

import unittest

from scripts.export_txt import build_feature_rows, build_record_rows


class ExportLegacySchemaTests(unittest.TestCase):
    def test_preserves_legacy_canonical_name_definition_and_aliases(self) -> None:
        registry = {
            "observations": [{
                "observation_id": "OBS-1",
                "reported_unit": "%",
                "reported_method": "galvanostatic cycling",
                "result_type": "measured",
            }],
            "canonical_features": [{
                "registry_feature_id": "RFEAT-1",
                "canonical_name": "Coulombic efficiency",
                "definition": "Discharge capacity divided by charge capacity.",
                "semantic_signature": {},
                "aliases": ["CE", "Coulombic efficiency"],
                "unit_dimensions": [],
                "measurement_methods": [],
                "calculation_methods": [],
                "result_types": ["measured"],
                "observation_ids": ["OBS-1"],
                "existing_feature_id": "F001",
                "feature_status": "confirmed",
                "redirect_to": None,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }],
        }
        old_schema = {
            "features": [{
                "FEATURE_ID": "F001",
                "CANONICAL_NAME": "CE",
                "ALIASES": ["Coulombic efficiency", "Coulombic efficiency (CE)"],
                "DEFINITION": "Discharge capacity divided by charge capacity as a percentage.",
                "RESULT_TYPE": ["measured", "calculated"],
                "UNITS_AND_DIMENSION": {"reported_units": ["%"], "dimensions": ["dimensionless"]},
                "MEASUREMENT_METHODS": ["galvanostatic cycling"],
                "CALCULATION_METHODS": [],
                "STANDARDIZATION_NOTES": "Legacy notation uses CE.",
                "COMPARABILITY_NOTES": "Compare at matched cycle number.",
            }]
        }
        feature_rows, feature_by_id, canonical_names, errors = build_feature_rows(registry, old_schema)
        self.assertEqual(errors, [])
        self.assertEqual(feature_rows[0][0], "F001")
        self.assertEqual(feature_rows[0][1], "CE")
        self.assertIn("Coulombic efficiency", feature_rows[0][2])
        self.assertIn("Legacy notation uses CE.", feature_rows[0][8])
        self.assertEqual(canonical_names["RFEAT-1"], "CE")

        record = {
            "record_id": "REC-1",
            "paper_id": "PAPER-1",
            "doi": "10.1234/test",
            "registry_feature_id": "RFEAT-1",
            "raw_feature_name": "Coulombic efficiency",
            "raw_value": "99.1",
            "numeric_value": 99.1,
            "reported_unit": "%",
            "result_type": "measured",
            "value_extraction_status": "exact_text",
            "sample_identity": [],
            "condition_facts": [],
            "time_cycle_facts": [],
            "protocol_facts": [],
            "other_context_facts": [],
            "source_id": "SRC-1",
            "source_type": "main_text",
            "source_location": [],
            "evidence_refs": [],
            "evidence_status": "verified_text",
            "extraction_status": "accepted",
            "unresolved_issues": [],
        }
        record_rows, record_errors = build_record_rows([record], feature_by_id, canonical_names)
        self.assertEqual(record_errors, [])
        self.assertEqual(record_rows[0][3], "F001")
        self.assertEqual(record_rows[0][5], "CE")


if __name__ == "__main__":
    unittest.main()
