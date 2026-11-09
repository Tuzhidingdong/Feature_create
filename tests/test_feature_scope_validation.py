from __future__ import annotations

import unittest

from scripts.validate_evidence import (
    validate_author_emphasis_evidence,
    validate_feature_only_output,
)


class FeatureScopeValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sources = {
            "SRC-MAIN": {"source_type": "main_text"},
            "SRC-FIG": {"source_type": "main_figure"},
        }

    def observation(self, emphasis: str, source_id: str, source_type: str, status: str) -> dict:
        return {
            "candidate_observation_id": "OBS-1",
            "author_emphasis": emphasis,
            "evidence_refs": [{
                "source_id": source_id,
                "source_type": source_type,
                "evidence_status": status,
                "quote_or_cell_value": "The capacity retention increased substantially.",
                "locator": {"page": 4, "section": "Results"},
            }],
        }

    def test_substantive_author_emphasis_requires_verified_main_text(self) -> None:
        observation = self.observation("core_conclusion", "SRC-FIG", "main_figure", "visual_only")
        errors = validate_author_emphasis_evidence([observation], self.sources)
        self.assertEqual(len(errors), 1)
        self.assertIn("verified main-text passage", errors[0])

    def test_verified_main_text_supports_author_emphasis(self) -> None:
        observation = self.observation("mechanistic_interpretation", "SRC-MAIN", "main_text", "verified_text")
        self.assertEqual(validate_author_emphasis_evidence([observation], self.sources), [])

    def test_figure_only_label_does_not_require_main_text(self) -> None:
        observation = self.observation("figure_table_only", "SRC-FIG", "main_figure", "visual_only")
        self.assertEqual(validate_author_emphasis_evidence([observation], self.sources), [])

    def test_source_discovery_cannot_emit_value_records(self) -> None:
        payload = {"result_records": [{"raw_value": "99.1%"}]}
        errors = validate_feature_only_output(payload, "source_discovery")
        self.assertEqual(len(errors), 1)
        self.assertIn("must be empty", errors[0])

    def test_feature_audit_has_no_legacy_value_record_requirement(self) -> None:
        self.assertEqual(validate_feature_only_output({}, "feature_audit"), [])


if __name__ == "__main__":
    unittest.main()
