#!/usr/bin/env python3
"""Validate declarative manifests for generated artifacts stored outside Git."""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
BUCKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
SECRET_FIELD_RE = re.compile(r"(?:^|_)(?:token|secret|password|credential|api_key)(?:$|_)", re.IGNORECASE)

KINDS = {"gaussian-splat", "checkpoint", "render", "video", "dataset"}
STORAGE_TYPE = "huggingface-bucket"


class ArtifactManifestError(ValueError):
    pass


@dataclass(frozen=True)
class ArtifactSpec:
    id: str
    kind: str
    format: str
    storage_bucket: str
    storage_path: str
    size_bytes: int
    sha256: str
    provenance_repository: str
    provenance_revision: str
    provenance_source_path: str | None = None
    provenance_run_id: str | None = None
    license_url: str | None = None
    source_url: str | None = None

    @property
    def remote_uri(self) -> str:
        return f"hf://buckets/{self.storage_bucket}/{self.storage_path}"


def _reject_secret_fields(value: Any, location: str = "manifest") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and SECRET_FIELD_RE.search(key):
                raise ArtifactManifestError(f"{location} contains forbidden credential field: {key}")
            _reject_secret_fields(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_fields(child, f"{location}[{index}]")


def _require_mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ArtifactManifestError(f"{location} must be a mapping")
    return value


def _validate_fields(item: dict[str, Any], required: set[str], optional: set[str], location: str) -> None:
    missing = sorted(required - set(item))
    unknown = sorted(set(item) - required - optional)
    if missing or unknown:
        raise ArtifactManifestError(f"{location} schema mismatch: missing={missing}, unknown={unknown}")


def _non_empty_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArtifactManifestError(f"{location} must be a non-empty string")
    return value.strip()


def load_artifact_manifest(path: Path) -> list[ArtifactSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw = _require_mapping(raw, "manifest")
    _reject_secret_fields(raw)
    _validate_fields(raw, {"schema_version", "artifacts"}, set(), "manifest")
    if raw["schema_version"] != 1:
        raise ArtifactManifestError("manifest.schema_version must equal 1")
    if not isinstance(raw["artifacts"], list):
        raise ArtifactManifestError("manifest.artifacts must be a list")

    specs: list[ArtifactSpec] = []
    seen_ids: set[str] = set()
    for index, raw_item in enumerate(raw["artifacts"]):
        location = f"artifacts[{index}]"
        item = _require_mapping(raw_item, location)
        _validate_fields(
            item,
            {"id", "kind", "format", "storage", "size_bytes", "sha256", "provenance"},
            {"license_url", "source_url"},
            location,
        )

        artifact_id = _non_empty_string(item["id"], f"{location}.id")
        if not ID_RE.fullmatch(artifact_id) or ".." in artifact_id.split("/"):
            raise ArtifactManifestError(f"{location}.id contains unsupported characters")
        if artifact_id.casefold() in seen_ids:
            raise ArtifactManifestError(f"duplicate artifact id: {artifact_id}")
        seen_ids.add(artifact_id.casefold())

        kind = _non_empty_string(item["kind"], f"{location}.kind")
        if kind not in KINDS:
            raise ArtifactManifestError(f"{location}.kind must be one of {sorted(KINDS)}")
        format_value = _non_empty_string(item["format"], f"{location}.format")

        storage = _require_mapping(item["storage"], f"{location}.storage")
        _validate_fields(storage, {"type", "bucket", "path"}, set(), f"{location}.storage")
        if storage["type"] != STORAGE_TYPE:
            raise ArtifactManifestError(f"{location}.storage.type must equal {STORAGE_TYPE!r}")
        bucket = _non_empty_string(storage["bucket"], f"{location}.storage.bucket")
        if not BUCKET_RE.fullmatch(bucket):
            raise ArtifactManifestError(f"{location}.storage.bucket must be namespace/name")
        storage_path = _non_empty_string(storage["path"], f"{location}.storage.path")
        path_parts = Path(storage_path).parts
        if storage_path.startswith(("/", "\\")) or ".." in path_parts or storage_path.endswith("/"):
            raise ArtifactManifestError(f"{location}.storage.path must be a relative object path")
        if "\\" in storage_path:
            raise ArtifactManifestError(f"{location}.storage.path must use forward slashes")

        size_bytes = item["size_bytes"]
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
            raise ArtifactManifestError(f"{location}.size_bytes must be a non-negative integer")
        sha256 = _non_empty_string(item["sha256"], f"{location}.sha256")
        if not SHA256_RE.fullmatch(sha256):
            raise ArtifactManifestError(f"{location}.sha256 must be 64 lowercase hexadecimal characters")

        provenance = _require_mapping(item["provenance"], f"{location}.provenance")
        _validate_fields(
            provenance,
            {"repository", "revision"},
            {"source_path", "run_id"},
            f"{location}.provenance",
        )
        repository = _non_empty_string(provenance["repository"], f"{location}.provenance.repository")
        revision = _non_empty_string(provenance["revision"], f"{location}.provenance.revision")
        if not REVISION_RE.fullmatch(revision):
            raise ArtifactManifestError(
                f"{location}.provenance.revision must be a full lowercase 40-character Git commit SHA"
            )
        source_path = provenance.get("source_path")
        run_id = provenance.get("run_id")
        if source_path is not None:
            source_path = _non_empty_string(source_path, f"{location}.provenance.source_path")
        if run_id is not None:
            run_id = _non_empty_string(run_id, f"{location}.provenance.run_id")
        if source_path is None and run_id is None:
            raise ArtifactManifestError(f"{location}.provenance requires source_path or run_id")

        optional_urls: dict[str, str | None] = {}
        for field in ("license_url", "source_url"):
            value = item.get(field)
            if value is not None:
                value = _non_empty_string(value, f"{location}.{field}")
                if not value.startswith("https://"):
                    raise ArtifactManifestError(f"{location}.{field} must use https://")
            optional_urls[field] = value

        specs.append(
            ArtifactSpec(
                id=artifact_id,
                kind=kind,
                format=format_value,
                storage_bucket=bucket,
                storage_path=storage_path,
                size_bytes=size_bytes,
                sha256=sha256,
                provenance_repository=repository,
                provenance_revision=revision,
                provenance_source_path=source_path,
                provenance_run_id=run_id,
                license_url=optional_urls["license_url"],
                source_url=optional_urls["source_url"],
            )
        )
    return specs


def manifest_summary(specs: list[ArtifactSpec], source: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": str(source),
        "artifact_count": len(specs),
        "artifacts": [
            {
                "id": spec.id,
                "kind": spec.kind,
                "format": spec.format,
                "remote_uri": spec.remote_uri,
                "size_bytes": spec.size_bytes,
                "sha256": spec.sha256,
                "provenance": {
                    "repository": spec.provenance_repository,
                    "revision": spec.provenance_revision,
                    **({"source_path": spec.provenance_source_path} if spec.provenance_source_path else {}),
                    **({"run_id": spec.provenance_run_id} if spec.provenance_run_id else {}),
                },
            }
            for spec in specs
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["validate"])
    parser.add_argument("--manifest", type=Path, default=Path("artifacts.yaml"))
    args = parser.parse_args()
    try:
        specs = load_artifact_manifest(args.manifest)
        print(json.dumps(manifest_summary(specs, args.manifest), ensure_ascii=False, indent=2, sort_keys=True))
    except (ArtifactManifestError, OSError, yaml.YAMLError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
