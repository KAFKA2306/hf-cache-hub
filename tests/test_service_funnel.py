from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.service_funnel import record_event, summarize, validate_event, validate_ledger

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "service-funnel.json"


class ServiceFunnelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads(LEDGER.read_text(encoding="utf-8"))

    def test_empty_ledger_is_not_claimed_as_zero_traffic(self) -> None:
        summary = summarize(self.payload)
        self.assertEqual(summary["traffic_measurement"], "not_instrumented")
        self.assertEqual(summary["commercial_measurement"], "evidence_only")
        self.assertEqual(summary["observed_event_total"], 0)
        self.assertEqual(summary["observed_event_counts"]["paid_pilot"], 0)

    def test_commercial_milestones_require_https_evidence(self) -> None:
        event = {
            "event_id": "qualified-001",
            "event_type": "qualified_inquiry",
            "occurred_at": "2026-08-13T03:00:00+09:00",
            "channel": "github_issue",
        }
        with self.assertRaisesRegex(ValueError, "requires evidence_url"):
            validate_event(event)
        event["evidence_url"] = "https://github.com/KAFKA2306/hf-cache-hub/issues/2"
        validate_event(event)

    def test_personal_or_secret_fields_are_rejected(self) -> None:
        event = {
            "event_id": "inquiry-001",
            "event_type": "bootstrap_inquiry_started",
            "occurred_at": "2026-08-13T03:00:00+09:00",
            "channel": "github_issue",
            "email": "do-not-store@example.invalid",
        }
        with self.assertRaisesRegex(ValueError, "forbidden field"):
            validate_event(event)

    def test_duplicate_event_ids_fail_closed(self) -> None:
        event = {
            "event_id": "pilot-001",
            "event_type": "pilot_booked",
            "occurred_at": "2026-08-13T03:00:00+09:00",
            "channel": "github_issue",
            "evidence_url": "https://github.com/KAFKA2306/hf-cache-hub/issues/2",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "service-funnel.json"
            path.write_text(json.dumps(self.payload), encoding="utf-8")
            record_event(path, event)
            with self.assertRaisesRegex(ValueError, "duplicate event_id"):
                record_event(path, event)

    def test_production_ledger_contract(self) -> None:
        validate_ledger(self.payload)


if __name__ == "__main__":
    unittest.main()
