from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ALLOWED_EVENT_TYPES = {
    "service_page_viewed",
    "sample_manifest_opened",
    "bootstrap_inquiry_started",
    "qualified_inquiry",
    "pilot_booked",
    "paid_pilot",
}
EVIDENCE_REQUIRED = {"qualified_inquiry", "pilot_booked", "paid_pilot"}
FORBIDDEN_KEYS = {
    "name",
    "email",
    "phone",
    "address",
    "hf_token",
    "access_token",
    "password",
    "api_key",
    "model_content",
    "model_name",
    "repo_id",
}
LEDGER_SCHEMA = "kafka.hf-cache-hub.service-funnel.v1"


def _require_https(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("evidence_url must be an absolute HTTPS URL")


def _validate_timestamp(value: str) -> None:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("occurred_at must include an explicit timezone")


def _reject_forbidden_keys(value: object, path: str = "event") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in FORBIDDEN_KEYS:
                raise ValueError(f"forbidden field at {path}.{key}")
            _reject_forbidden_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_keys(child, f"{path}[{index}]")


def validate_event(event: dict[str, object]) -> None:
    _reject_forbidden_keys(event)
    allowed_keys = {"event_id", "event_type", "occurred_at", "channel", "evidence_url"}
    unknown = set(event) - allowed_keys
    if unknown:
        raise ValueError(f"unknown event fields: {sorted(unknown)}")

    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("event_id is required")

    event_type = event.get("event_type")
    if event_type not in ALLOWED_EVENT_TYPES:
        raise ValueError(f"unsupported event_type: {event_type}")

    occurred_at = event.get("occurred_at")
    if not isinstance(occurred_at, str):
        raise ValueError("occurred_at is required")
    _validate_timestamp(occurred_at)

    channel = event.get("channel")
    if not isinstance(channel, str) or not channel.strip():
        raise ValueError("channel is required")

    evidence_url = event.get("evidence_url")
    if evidence_url is not None:
        if not isinstance(evidence_url, str):
            raise ValueError("evidence_url must be a string or null")
        _require_https(evidence_url)
    if event_type in EVIDENCE_REQUIRED and not evidence_url:
        raise ValueError(f"{event_type} requires evidence_url")


def validate_ledger(payload: dict[str, object]) -> None:
    if payload.get("schema_version") != LEDGER_SCHEMA:
        raise ValueError("unsupported service funnel schema")
    if payload.get("measurement_scope") != "repository_recorded_evidence_only":
        raise ValueError("measurement_scope must remain evidence-only")
    if payload.get("traffic_measurement") != "not_instrumented":
        raise ValueError("traffic_measurement must remain not_instrumented until real telemetry exists")
    if payload.get("commercial_measurement") != "evidence_only":
        raise ValueError("commercial_measurement must remain evidence_only")

    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("events must be a list")

    seen: set[str] = set()
    for raw_event in events:
        if not isinstance(raw_event, dict):
            raise ValueError("every event must be an object")
        validate_event(raw_event)
        event_id = str(raw_event["event_id"])
        if event_id in seen:
            raise ValueError(f"duplicate event_id: {event_id}")
        seen.add(event_id)


def summarize(payload: dict[str, object]) -> dict[str, object]:
    validate_ledger(payload)
    counts = {event_type: 0 for event_type in sorted(ALLOWED_EVENT_TYPES)}
    for event in payload["events"]:
        counts[event["event_type"]] += 1
    return {
        "schema_version": payload["schema_version"],
        "measurement_scope": payload["measurement_scope"],
        "traffic_measurement": payload["traffic_measurement"],
        "commercial_measurement": payload["commercial_measurement"],
        "observed_event_counts": counts,
        "observed_event_total": sum(counts.values()),
    }


def record_event(path: Path, event: dict[str, object]) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate_ledger(payload)
    validate_event(event)
    if any(existing["event_id"] == event["event_id"] for existing in payload["events"]):
        raise ValueError(f"duplicate event_id: {event['event_id']}")
    payload["events"].append(event)
    validate_ledger(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and summarize the privacy-safe service funnel ledger")
    parser.add_argument("command", choices=("validate", "summary"))
    parser.add_argument("--ledger", type=Path, default=Path("data/service-funnel.json"))
    args = parser.parse_args()

    payload = json.loads(args.ledger.read_text(encoding="utf-8"))
    if args.command == "validate":
        validate_ledger(payload)
        print(json.dumps({"status": "ok", "events": len(payload["events"])}, sort_keys=True))
    else:
        print(json.dumps(summarize(payload), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
