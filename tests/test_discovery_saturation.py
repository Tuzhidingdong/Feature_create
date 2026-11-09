from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from feature_create.discovery_saturation import (
    FeatureSaturationError,
    build_feature_saturation_report,
    calculate_feature_saturation_report,
)


def observation(
    identifier: str,
    paper_id: str,
    feature_id: str | None,
    *,
    role: str = "result_feature",
    discovery_status: str = "evidence_verified",
) -> dict:
    return {
        "observation_id": identifier,
        "paper_id": paper_id,
        "source_id": f"SRC-{identifier}",
        "raw_feature_name": f"Feature {feature_id or 'uncertain'}",
        "variable_role": role,
        "result_type": "measured",
        "evidence_refs": [{"source_id": f"SRC-{identifier}", "source_type": "main_text", "locator": {}}],
        "origin_task_id": f"TASK-{identifier}",
        "discovery_status": discovery_status,
        "linked_registry_feature_id": feature_id,
    }


def feature(identifier: str, observation_ids: list[str], status: str = "confirmed", redirect_to: str | None = None) -> dict:
    return {
        "registry_feature_id": identifier,
        "canonical_name": identifier,
        "definition": f"Definition for {identifier}",
        "semantic_signature": {},
        "aliases": [identifier],
        "observation_ids": observation_ids,
        "feature_status": status,
        "redirect_to": redirect_to,
    }


def make_registry(observations: list[dict], features: list[dict]) -> dict:
    return {
        "contract_version": "0.1",
        "registry_id": "TEST-REGISTRY",
        "next_observation_sequence": len(observations) + 1,
        "next_feature_sequence": len(features) + 1,
        "observations": observations,
        "canonical_features": features,
        "decision_log": [],
    }


def make_batch(label: str, paper_ids: list[str]) -> dict:
    return {
        "index": 1,
        "label": label,
        "manifest_path": f"/virtual/{label}/source_manifest.json",
        "manifest_sha256": "a" * 64,
        "paper_count": len(paper_ids),
        "paper_ids": paper_ids,
        "paper_by_id": {paper_id: {"paper_id": paper_id, "input_status": "ready"} for paper_id in paper_ids},
        "ready_paper_count": len(paper_ids),
        "blocked_paper_count": 0,
        "sampling": None,
    }


class FeatureDiscoverySaturationTests(unittest.TestCase):
    def test_current_registry_merges_retroactively_prevent_false_novelty(self) -> None:
        observations = [
            observation("O1", "P1", "F1"),
            observation("O2", "P2", "F1"),
            observation("O3", "P3", "F1"),
        ]
        registry = make_registry(
            observations,
            [feature("F1", ["O1", "O2", "O3"])],
        )
        batches = [make_batch("round-10", ["P1", "P2"]), make_batch("round-20", ["P3"])]
        report = calculate_feature_saturation_report(
            registry, "b" * 64, batches, {"P1", "P2", "P3"}, [],
        )
        self.assertEqual([1, 0], [
            item["new_current_canonical_result_features"] for item in report["batches"]
        ])
        self.assertEqual(1, report["summary"]["unique_current_canonical_result_feature_count"])
        self.assertEqual(0, report["batches"][1]["new_canonical_features_per_100_papers"])

    def test_retired_feature_redirect_is_resolved_before_counting(self) -> None:
        observations = [
            observation("O1", "P1", "F-SURVIVOR"),
            observation("O2", "P2", "F-SURVIVOR"),
        ]
        registry = make_registry(
            observations,
            [
                feature("F-SURVIVOR", ["O1", "O2"]),
                feature("F-RETIRED", [], status="retired_merged", redirect_to="F-SURVIVOR"),
            ],
        )
        report = calculate_feature_saturation_report(
            registry, "c" * 64,
            [make_batch("first", ["P1"]), make_batch("second", ["P2"])],
            {"P1", "P2"}, [],
        )
        self.assertEqual(1, report["batches"][0]["new_current_canonical_result_features"])
        self.assertEqual(0, report["batches"][1]["new_current_canonical_result_features"])

    def test_reports_marginal_yield_and_no_automatic_verdict(self) -> None:
        observations = [
            observation("O1", "P1", "F1"),
            observation("O2", "P2", "F2", discovery_status="candidate"),
            observation("O3", "P3", "F1"),
            observation("O4", "P3", "F3"),
            observation("O5", "P4", "F3"),
            observation("O6", "P4", "F4"),
            observation("O7", "P4", None, role="uncertain", discovery_status="under_review"),
        ]
        registry = make_registry(
            observations,
            [
                feature("F1", ["O1", "O3"]),
                feature("F2", ["O2"], status="provisional"),
                feature("F3", ["O4", "O5"]),
                feature("F4", ["O6"]),
            ],
        )
        batches = [
            make_batch("batch-1", ["P1", "P2"]),
            make_batch("batch-2", ["P3"]),
            make_batch("batch-3", ["P4"]),
        ]
        report = calculate_feature_saturation_report(
            registry, "d" * 64, batches, {"P1", "P2", "P3", "P4"}, [],
        )
        self.assertEqual([2, 1, 1], [
            item["new_current_canonical_result_features"] for item in report["batches"]
        ])
        self.assertEqual(1, report["batches"][2]["uncertain_observation_count"])
        self.assertEqual(1, report["batches"][0]["new_feature_status_counts"]["provisional"])
        self.assertIsNone(report["summary"]["saturation_verdict"])
        self.assertEqual(
            "descriptive_only_no_automatic_saturation_decision",
            report["summary"]["trend_comparison"]["interpretation"],
        )
        self.assertFalse(report["workflow_policy"]["required_for_extraction"])
        self.assertFalse(report["workflow_policy"]["blocks_later_batches"])

    def test_duplicate_papers_across_batch_manifests_are_rejected(self) -> None:
        with self.assertRaisesRegex(FeatureSaturationError, "overlap"):
            calculate_feature_saturation_report(
                make_registry([], []), "e" * 64,
                [make_batch("first", ["P1"]), make_batch("second", ["P1"])],
                {"P1"}, [],
            )

    def test_unassigned_registry_papers_fail_closed_unless_allowed(self) -> None:
        registry = make_registry([observation("O1", "P-OUTSIDE", "F1")], [feature("F1", ["O1"])])
        with self.assertRaisesRegex(FeatureSaturationError, "absent from the supplied batch"):
            calculate_feature_saturation_report(
                registry, "f" * 64, [make_batch("selected", ["P1"])], {"P1"}, [],
            )
        report = calculate_feature_saturation_report(
            registry, "f" * 64, [make_batch("selected", ["P1"])], {"P1"}, [],
            allow_unassigned=True,
        )
        self.assertEqual(1, report["inputs"]["unassigned_registry_paper_count"])
        self.assertTrue(any("outside the supplied batches" in item for item in report["warnings"]))

    def test_unknown_feature_link_and_broken_backlink_are_rejected(self) -> None:
        registry = make_registry([observation("O1", "P1", "MISSING")], [])
        with self.assertRaisesRegex(FeatureSaturationError, "missing Registry Feature"):
            calculate_feature_saturation_report(
                registry, "0" * 64, [make_batch("first", ["P1"])], {"P1"}, [],
            )
        registry = make_registry([observation("O1", "P1", "F1")], [feature("F1", [])])
        with self.assertRaisesRegex(FeatureSaturationError, "backlink"):
            calculate_feature_saturation_report(
                registry, "0" * 64, [make_batch("first", ["P1"])], {"P1"}, [],
            )

    def test_builder_writes_reports_and_does_not_modify_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = root / "global_feature_registry.json"
            registry = make_registry(
                [observation("O1", "P1", "F1")],
                [feature("F1", ["O1"])],
            )
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            batch_path = root / "source_manifest.json"
            batch_path.write_text(json.dumps({
                "manifest_version": "0.3",
                "paper_count": 1,
                "papers": [{"paper_id": "P1", "input_status": "ready"}],
                "sampling": None,
            }), encoding="utf-8")
            before = registry_path.read_bytes()
            report = build_feature_saturation_report(
                registry_path, [("round-10", batch_path)], root / "report",
            )
            self.assertEqual(before, registry_path.read_bytes())
            self.assertTrue(Path(report["report_files"]["json"]).is_file())
            markdown = Path(report["report_files"]["markdown"]).read_text(encoding="utf-8")
            self.assertIn("does not declare the corpus saturated", markdown)
            self.assertTrue(Path(str(registry_path) + ".lock").exists() is False)


if __name__ == "__main__":
    unittest.main()
