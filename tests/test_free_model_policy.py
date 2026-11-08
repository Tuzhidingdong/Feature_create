from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from scripts.validate_free_models import ModelPolicyError, validate_policy


ROOT = Path(__file__).resolve().parents[1]


class FreeModelPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.free_policy = json.loads(
            (ROOT / "config" / "free_model_policy.json").read_text(encoding="utf-8")
        )
        self.execution_policy = json.loads(
            (ROOT / "config" / "execution_policy.json").read_text(encoding="utf-8")
        )
        self.models_output = (
            "opencode-go/crouton-alpha-free\n"
            "opencode-go/beam-free\n"
        )

    def test_accepts_two_distinct_free_models(self) -> None:
        pair = validate_policy(
            self.free_policy,
            self.execution_policy,
            self.models_output,
            today=date(2026, 11, 8),
        )
        self.assertEqual(
            pair,
            [
                "opencode-go/crouton-alpha-free",
                "opencode-go/beam-free",
            ],
        )

    def test_rejects_missing_model_from_catalog(self) -> None:
        with self.assertRaises(ModelPolicyError):
            validate_policy(
                self.free_policy,
                self.execution_policy,
                "opencode-go/crouton-alpha-free\n",
                today=date(2026, 11, 8),
            )

    def test_rejects_identical_worker_models(self) -> None:
        bad = json.loads(json.dumps(self.execution_policy))
        bad["workers"]["worker_b"]["model_ref"] = (
            bad["workers"]["worker_a"]["model_ref"]
        )
        with self.assertRaises(ModelPolicyError):
            validate_policy(
                self.free_policy,
                bad,
                self.models_output,
                today=date(2026, 11, 8),
            )

    def test_rejects_paid_fallback(self) -> None:
        bad = json.loads(json.dumps(self.execution_policy))
        bad["workers"]["paid_fallback_allowed"] = True
        with self.assertRaises(ModelPolicyError):
            validate_policy(
                self.free_policy,
                bad,
                self.models_output,
                today=date(2026, 11, 8),
            )

    def test_rejects_expired_price_snapshot(self) -> None:
        with self.assertRaises(ModelPolicyError):
            validate_policy(
                self.free_policy,
                self.execution_policy,
                self.models_output,
                today=date(2026, 11, 20),
            )


if __name__ == "__main__":
    unittest.main()
