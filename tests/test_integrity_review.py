from __future__ import annotations

import unittest

from feature_create.integrity_review import (
    build_review_queue,
    make_ledger_entries,
    validate_review_rows,
)


def fixture():
    registry = {
        "observations": [{
            "observation_id": "OBS-1",
            "paper_id": "PAPER-1",
            "source_id": "SRC-1",
            "raw_feature_name": "Coulombic efficiency",
            "variable_role": "result_feature",
            "discovery_status": "evidence_verified",
            "linked_registry_feature_id": "F-1",
            "evidence_refs": [{
                "source_id": "SRC-1",
                "source_type": "main_text",
                "locator": {"page": 3, "section": "Results"},
                "quote_or_cell_value": "CE reached 99.1%.",
                "evidence_status": "verified_text",
            }],
        }],
        "canonical_features": [{
            "registry_feature_id": "F-1",
            "canonical_name": "Coulombic efficiency",
            "feature_status": "confirmed",
            "observation_ids": ["OBS-1"],
        }, {
            "registry_feature_id": "F-2",
            "canonical_name": "CE (legacy)",
            "feature_status": "provisional",
            "observation_ids": [],
        }],
        "decision_log": [{
            "event_id": "EV-1",
            "event_type": "feature_mapped",
            "subject_ids": ["F-1", "OBS-1"],
            "decision": "map_existing_feature",
            "rationale": "The canonical definition matches.",
            "evidence_observation_ids": ["OBS-1"],
            "actor": "human",
            "contract_version": "0.1",
        }],
    }
    records = [{
        "record_id": "REC-1",
        "paper_id": "PAPER-1",
        "source_id": "SRC-1",
        "observation_id": "OBS-1",
        "registry_feature_id": "F-2",
        "raw_feature_name": "CE",
        "value": 99.1,
        "reported_unit": "%",
        "evidence_refs": [{
            "source_id": "SRC-1",
            "locator": {"page": 3, "section": "Results"},
        }],
    }]
    manifest = {
        "sources": [{
            "source_id": "SRC-1",
            "paper_id": "PAPER-1",
            "source_type": "main_text",
            "extracted_text_path": "extracted_text/PAPER-1.txt",
        }]
    }
    report = {
        "audit_version": "1.0",
        "status": "fail",
        "summary": {"issue_count": 1, "error_count": 1, "warning_count": 0},
        "issues": [{
            "code": "record_observation_feature_mismatch",
            "severity": "error",
            "message": "Record and Observation Feature IDs disagree.",
            "context": {
                "record_id": "REC-1",
                "record_feature_id": "F-2",
                "observation_feature_id": "F-1",
            },
        }],
    }
    fingerprints = {
        "registry_sha256": "a" * 64,
        "records_sha256": "b" * 64,
        "manifest_sha256": "c" * 64,
        "strict_observation_records": False,
    }
    return report, registry, records, manifest, fingerprints


class IntegrityReviewQueueTests(unittest.TestCase):
    def test_queue_expands_finding_to_related_objects_evidence_and_history(self):
        report, registry, records, manifest, fingerprints = fixture()
        queue, template = build_review_queue(
            report, registry, records, manifest, fingerprints,
        )
        self.assertEqual(queue["summary"]["finding_count"], 1)
        finding = queue["findings"][0]
        self.assertEqual(finding["primary_object"], {
            "object_type": "result_record", "object_id": "REC-1",
        })
        self.assertEqual(finding["object_context"]["related_ids"]["observation_ids"], ["OBS-1"])
        self.assertEqual(finding["object_context"]["related_ids"]["feature_ids"], ["F-1", "F-2"])
        self.assertEqual(finding["object_context"]["related_ids"]["event_ids"], ["EV-1"])
        self.assertEqual(finding["object_context"]["sources"][0]["source_id"], "SRC-1")
        self.assertEqual(finding["recommended_route"]["action"], "record_review")
        self.assertEqual(template[0]["review_decision"], "")

    def test_prior_review_is_marked_changed_when_finding_fingerprint_changes(self):
        report, registry, records, manifest, fingerprints = fixture()
        first, _ = build_review_queue(report, registry, records, manifest, fingerprints)
        ledger = [{
            "finding_id": first["findings"][0]["finding_id"],
            "finding_fingerprint": first["findings"][0]["finding_fingerprint"],
            "review_decision": "repair_required",
        }]
        changed = dict(report)
        changed["issues"] = [dict(report["issues"][0], message="A different mismatch remains.")]
        queue, _ = build_review_queue(changed, registry, records, manifest, fingerprints, ledger)
        self.assertEqual(queue["findings"][0]["review_state"], "changed_since_review")

    def test_disappeared_repair_finding_is_reported_as_audit_clearance(self):
        report, registry, records, manifest, fingerprints = fixture()
        first, _ = build_review_queue(report, registry, records, manifest, fingerprints)
        ledger = [{
            "finding_id": first["findings"][0]["finding_id"],
            "finding_fingerprint": first["findings"][0]["finding_fingerprint"],
            "review_decision": "repair_required",
            "code": "record_observation_feature_mismatch",
        }]
        cleared_report = dict(report, status="pass", summary={"issue_count": 0, "error_count": 0, "warning_count": 0}, issues=[])
        queue, _ = build_review_queue(cleared_report, registry, records, manifest, fingerprints, ledger)
        self.assertEqual(queue["summary"]["cleared_since_previous_review_count"], 1)
        self.assertEqual(queue["cleared_since_previous_review"][0]["clearance"], "not_reported_by_current_audit")

    def test_hard_error_cannot_be_accepted_as_warning(self):
        report, registry, records, manifest, fingerprints = fixture()
        queue, template = build_review_queue(report, registry, records, manifest, fingerprints)
        edited = [dict(template[0], review_decision="accepted_warning", action="none",
                       rationale="This is accepted after review.", review_evidence_refs=[{
                           "source_id": "SRC-1", "locator": {"page": 3},
                       }])]
        with self.assertRaisesRegex(ValueError, "hard errors"):
            validate_review_rows(queue, template, edited)

    def test_repair_decision_requires_action_and_rationale(self):
        report, registry, records, manifest, fingerprints = fixture()
        queue, template = build_review_queue(report, registry, records, manifest, fingerprints)
        edited = [dict(template[0], review_decision="repair_required", action="none",
                       rationale="This needs correction.")]
        with self.assertRaisesRegex(ValueError, "concrete review action"):
            validate_review_rows(queue, template, edited)

    def test_valid_review_decision_becomes_human_ledger_entry(self):
        report, registry, records, manifest, fingerprints = fixture()
        queue, template = build_review_queue(report, registry, records, manifest, fingerprints)
        edited = [dict(template[0], review_decision="repair_required",
                       action="record_review",
                       rationale="Review this result record against the parsed source evidence.")]
        validated = validate_review_rows(queue, template, edited)
        entries = make_ledger_entries(validated)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["actor"], "human")
        self.assertEqual(entries[0]["finding_id"], template[0]["finding_id"])

    def test_template_immutable_identity_cannot_be_edited(self):
        report, registry, records, manifest, fingerprints = fixture()
        queue, template = build_review_queue(report, registry, records, manifest, fingerprints)
        edited = [dict(template[0], code="invented_code", review_decision="defer",
                       action="none", rationale="Defer until more evidence is available.")]
        with self.assertRaisesRegex(ValueError, "immutable field code"):
            validate_review_rows(queue, template, edited)

    def test_duplicate_issue_instances_receive_distinct_finding_ids(self):
        report, registry, records, manifest, fingerprints = fixture()
        duplicate_report = dict(report, issues=[report["issues"][0], dict(report["issues"][0])])
        queue, template = build_review_queue(duplicate_report, registry, records, manifest, fingerprints)
        self.assertEqual(len(queue["findings"]), 2)
        self.assertNotEqual(queue["findings"][0]["finding_id"], queue["findings"][1]["finding_id"])
        self.assertEqual(len({row["finding_id"] for row in template}), 2)

    def test_decision_id_changes_when_action_or_evidence_changes(self):
        base = {
            "finding_id": "IRF-0123456789ABCDEF0123",
            "audit_fingerprint": "a" * 64,
            "review_decision": "repair_required",
            "action": "record_review",
            "rationale": "Review this source-backed record in context.",
            "review_evidence_refs": [{"source_id": "SRC-1", "locator": {"page": 1}}],
            "follow_up_reference": None,
        }
        first = make_ledger_entries([base])[0]["decision_id"]
        changed_action = make_ledger_entries([dict(base, action="evidence_revalidation")])[0]["decision_id"]
        changed_evidence = make_ledger_entries([dict(base, review_evidence_refs=[{
            "source_id": "SRC-1", "locator": {"page": 2},
        }])])[0]["decision_id"]
        self.assertEqual(len({first, changed_action, changed_evidence}), 3)

    def test_decision_template_is_bound_to_the_complete_queue_snapshot(self):
        report, registry, records, manifest, fingerprints = fixture()
        first_queue, first_template = build_review_queue(
            report, registry, records, manifest, fingerprints,
        )
        changed_fingerprints = dict(fingerprints, registry_sha256="d" * 64)
        second_queue, second_template = build_review_queue(
            report, registry, records, manifest, changed_fingerprints,
        )
        self.assertNotEqual(first_queue["queue_fingerprint"], second_queue["queue_fingerprint"])
        self.assertEqual(first_template[0]["queue_fingerprint"], first_queue["queue_fingerprint"])
        self.assertEqual(second_template[0]["queue_fingerprint"], second_queue["queue_fingerprint"])
        stale_decision = [dict(
            first_template[0], review_decision="defer", action="none",
            rationale="Wait for a complete source review before changing this item.",
        )]
        with self.assertRaisesRegex(ValueError, "immutable field queue_fingerprint"):
            validate_review_rows(second_queue, second_template, stale_decision)

    def test_review_evidence_must_exist_in_supplied_manifest(self):
        report, registry, records, manifest, fingerprints = fixture()
        queue, template = build_review_queue(report, registry, records, manifest, fingerprints)
        edited = [dict(template[0], review_decision="repair_required",
                       action="record_review",
                       rationale="Review this record against the source package.",
                       review_evidence_refs=[{
                           "source_id": "SRC-NOT-IN-MANIFEST", "locator": {"page": 1},
                       }])]
        with self.assertRaisesRegex(ValueError, "absent from the prepared manifest"):
            validate_review_rows(queue, template, edited)

    def test_accepted_warning_requires_evidence(self):
        report, registry, records, manifest, fingerprints = fixture()
        warning_report = dict(report, status="pass_with_warnings", summary={
            "issue_count": 1, "error_count": 0, "warning_count": 1,
        }, issues=[dict(report["issues"][0], severity="warning")])
        queue, template = build_review_queue(warning_report, registry, records, manifest, fingerprints)
        edited = [dict(template[0], review_decision="accepted_warning",
                       action="none", rationale="Accepted because this is a visual-only series.",
                       review_evidence_refs=[])]
        with self.assertRaisesRegex(ValueError, "requires explicit review_evidence_refs"):
            validate_review_rows(queue, template, edited)


if __name__ == "__main__":
    unittest.main()
