from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "validate_models.py"
SPEC = importlib.util.spec_from_file_location("validate_models", MODULE_PATH)
assert SPEC and SPEC.loader
validate_models = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate_models)


class ValidateModelsTest(unittest.TestCase):
    def test_valid_pinned_registry(self) -> None:
        result = validate_models.validate_registry(
            {
                "models": [
                    {
                        "org": "example-org",
                        "repo": "example-model",
                        "revision": "a" * 40,
                        "link_name": "example-model",
                    }
                ]
            },
            require_revision=True,
        )
        self.assertEqual([], result["errors"])
        self.assertEqual([], result["warnings"])
        self.assertEqual(1, result["model_count"])

    def test_duplicate_model_and_link_are_rejected(self) -> None:
        result = validate_models.validate_registry(
            {
                "models": [
                    {"org": "Org", "repo": "Model", "link_name": "shared"},
                    {"org": "org", "repo": "model", "link_name": "SHARED"},
                ]
            }
        )
        codes = {issue["code"] for issue in result["errors"]}
        self.assertIn("duplicate_model", codes)
        self.assertIn("duplicate_link_name", codes)
        self.assertEqual(2, result["warning_count"] if "warning_count" in result else len(result["warnings"]))

    def test_mutable_revision_is_rejected(self) -> None:
        result = validate_models.validate_registry(
            {"models": [{"org": "org", "repo": "model", "revision": "main"}]}
        )
        self.assertIn("invalid_revision", {item["code"] for item in result["errors"]})

    def test_audit_records_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "models.yaml"
            path.write_text("models:\n  - org: org\n    repo: model\n", encoding="utf-8")
            result = validate_models.audit_file(path)
        self.assertEqual(64, len(result["source_sha256"]))
        self.assertEqual(0, result["error_count"])
        self.assertEqual(1, result["warning_count"])


if __name__ == "__main__":
    unittest.main()
