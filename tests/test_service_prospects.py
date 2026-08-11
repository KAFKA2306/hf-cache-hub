from __future__ import annotations

import json
import unittest
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "team-cache-prospects.json"


class ServiceProspectRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
        cls.prospects = cls.payload["prospects"]

    def test_registry_has_at_least_ten_unique_candidates(self) -> None:
        self.assertGreaterEqual(len(self.prospects), 10)
        ids = [item["id"] for item in self.prospects]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_candidate_has_https_public_evidence(self) -> None:
        for item in self.prospects:
            with self.subTest(id=item["id"]):
                parsed = urlparse(item["evidence_url"])
                self.assertEqual(parsed.scheme, "https")
                self.assertTrue(parsed.netloc)
                self.assertTrue(item["public_fact"].strip())

    def test_research_is_not_counted_as_outreach(self) -> None:
        for item in self.prospects:
            with self.subTest(id=item["id"]):
                self.assertEqual(item["outreach_status"], "RESEARCHED_NOT_CONTACTED")
                self.assertEqual(item["outreach_evidence"], [])

    def test_registry_contains_no_secret_fields(self) -> None:
        serialized = json.dumps(self.payload).lower()
        for forbidden in ("hf_token", "access_token", "password", "api_key"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
