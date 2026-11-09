from __future__ import annotations

import unittest

from feature_create.integrity import audit_integrity


def registry():
    return {
        "observations": [{
            "observation_id": "OBS-1", "paper_id": "P-1", "source_id": "SRC-1",
            "raw_feature_name": "CE", "variable_role": "result_feature",
            "result_type": "measured", "discovery_status": "evidence_verified",
            "linked_registry_feature_id": "F-1",
            "evidence_refs": [{"source_id": "SRC-1", "source_type": "main_text",
                               "locator": {"page": 1}, "evidence_status": "verified_text"}],
        }],
        "canonical_features": [{
            "registry_feature_id": "F-1", "canonical_name": "Coulombic efficiency",
            "feature_status": "confirmed", "observation_ids": ["OBS-1"],
        }],
        "decision_log": [],
    }


def record():
    return {"record_id": "R-1", "observation_id": "OBS-1", "registry_feature_id": "F-1", "source_id": "SRC-1", "evidence_refs": [{"source_id": "SRC-1", "locator": {"page": 1}}]}


class IntegrityAuditTests(unittest.TestCase):
    def codes(self, report):
        return {item["code"] for item in report["issues"]}

    def test_valid_bidirectional_links_pass(self):
        report = audit_integrity(registry(), [record()], {"sources": [{"source_id": "SRC-1"}]})
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["summary"]["error_count"], 0)

    def test_record_feature_mismatch_is_error(self):
        data = registry()
        data["canonical_features"].append({"registry_feature_id": "F-2", "feature_status": "confirmed", "observation_ids": []})
        report = audit_integrity(data, [record() | {"registry_feature_id": "F-2"}])
        self.assertIn("record_observation_feature_mismatch", self.codes(report))

    def test_confirmed_feature_without_observation_is_error(self):
        data = registry()
        data["canonical_features"][0]["observation_ids"] = []
        report = audit_integrity(data, [record()])
        self.assertIn("confirmed_feature_without_observation", self.codes(report))

    def test_missing_source_in_manifest_is_error(self):
        report = audit_integrity(registry(), [record()], {"sources": []})
        self.assertIn("unknown_evidence_source", self.codes(report))

    def test_orphan_observation_is_warning_unless_strict(self):
        report = audit_integrity(registry(), [])
        self.assertEqual(report["status"], "pass_with_warnings")
        strict = audit_integrity(registry(), [], strict_observation_records=True)
        self.assertIn("observation_without_result_record", self.codes(strict))
        self.assertEqual(strict["status"], "fail")

    def test_excluded_observation_requires_decision(self):
        data = registry()
        data["observations"][0]["discovery_status"] = "excluded"
        report = audit_integrity(data, [])
        self.assertIn("excluded_observation_without_decision", self.codes(report))

    def test_merged_feature_requires_redirect_and_event(self):
        data = registry()
        data["canonical_features"][0]["feature_status"] = "retired_merged"
        data["canonical_features"][0]["redirect_to"] = "MISSING"
        report = audit_integrity(data, [])
        self.assertIn("invalid_feature_merge_redirect", self.codes(report))
        self.assertIn("merged_feature_without_decision", self.codes(report))

    def test_record_cannot_point_to_condition_only_observation(self):
        data = registry()
        data["observations"][0]["variable_role"] = "condition_only"
        report = audit_integrity(data, [record()])
        self.assertIn("record_observation_not_result_feature", self.codes(report))

    def test_record_cannot_point_to_excluded_observation(self):
        data = registry()
        data["observations"][0]["discovery_status"] = "excluded"
        report = audit_integrity(data, [record()])
        self.assertIn("record_linked_to_excluded_observation", self.codes(report))

    def test_merge_event_must_match_retired_feature_redirect(self):
        data = registry()
        data["canonical_features"].append({
            "registry_feature_id": "F-OLD", "canonical_name": "Old CE",
            "feature_status": "retired_merged", "redirect_to": "F-OTHER",
            "observation_ids": ["OBS-1"],
        })
        data["canonical_features"].append({
            "registry_feature_id": "F-OTHER", "canonical_name": "Other CE",
            "feature_status": "confirmed", "observation_ids": [],
        })
        data["decision_log"] = [{
            "event_id": "EV-MERGE-WRONG", "event_type": "feature_merged",
            "subject_ids": ["F-1", "F-OLD"], "decision": "merge_as_same_feature",
            "rationale": "The definitions and statistic scope are equivalent.",
            "evidence_observation_ids": ["OBS-1"],
        }]
        report = audit_integrity(data, [record()])
        self.assertIn("merge_event_redirect_mismatch", self.codes(report))

    def test_merge_event_evidence_must_cover_each_feature_subject(self):
        data = registry()
        data["observations"].append({
            "observation_id": "OBS-2", "paper_id": "P-2", "source_id": "SRC-2",
            "raw_feature_name": "Old CE", "variable_role": "result_feature",
            "result_type": "measured", "discovery_status": "evidence_verified",
            "linked_registry_feature_id": "F-1",
            "evidence_refs": [{"source_id": "SRC-2", "source_type": "main_text",
                               "locator": {"page": 2}, "evidence_status": "verified_text"}],
        })
        data["canonical_features"].append({
            "registry_feature_id": "F-OLD", "canonical_name": "Old CE",
            "feature_status": "retired_merged", "redirect_to": "F-1",
            "observation_ids": ["OBS-2"],
        })
        data["decision_log"] = [{
            "event_id": "EV-MERGE-ONE-SIDED", "event_type": "feature_merged",
            "subject_ids": ["F-1", "F-OLD"], "decision": "merge_as_same_feature",
            "rationale": "The definitions and statistic scope are equivalent.",
            "evidence_observation_ids": ["OBS-1"],
        }]
        report = audit_integrity(data, [record()])
        self.assertIn("decision_transition_evidence_not_linked_to_feature", self.codes(report))

    def test_exclusion_event_must_cite_the_excluded_observation(self):
        data = registry()
        data["observations"][0]["discovery_status"] = "excluded"
        data["decision_log"] = [{
            "event_id": "EV-EXCLUDE", "event_type": "feature_excluded",
            "subject_ids": ["OBS-1"], "decision": "exclude_not_a_result_feature",
            "rationale": "The source indicates this variable is a test condition.",
            "evidence_observation_ids": [],
        }]
        report = audit_integrity(data, [])
        self.assertIn("decision_event_without_evidence_chain", self.codes(report))
        self.assertIn("excluded_decision_missing_subject_evidence", self.codes(report))

    def test_valid_merge_preserves_historical_observation_link(self):
        data = registry()
        data["canonical_features"].append({
            "registry_feature_id": "F-OLD", "canonical_name": "Old CE",
            "feature_status": "retired_merged", "redirect_to": "F-1",
            "observation_ids": ["OBS-1"],
        })
        data["decision_log"] = [{
            "event_id": "EV-MERGE", "event_type": "feature_merged",
            "subject_ids": ["F-1", "F-OLD"], "decision": "merge_as_same_feature",
            "rationale": "Definitions and statistic scope are equivalent.",
            "evidence_observation_ids": ["OBS-1"],
        }]
        report = audit_integrity(data, [record()])
        self.assertNotIn("observation_feature_backlink_mismatch", self.codes(report))
        self.assertNotIn("merged_feature_without_decision", self.codes(report))


if __name__ == "__main__":
    unittest.main()
