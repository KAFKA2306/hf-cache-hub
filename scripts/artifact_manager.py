#!/usr/bin/env python3
"""Validate and publish generated artifacts stored outside Git."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml
from huggingface_hub import batch_bucket_files, download_bucket_files
from huggingface_hub.errors import HfHubHTTPError

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
BUCKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
SECRET_FIELD_RE = re.compile(r"(?:^|_)(?:token|secret|password|credential|api_key)(?:$|_)", re.IGNORECASE)

KINDS = {"gaussian-splat", "checkpoint", "render", "video", "dataset"}
STORAGE_TYPE = "huggingface-bucket"
TRANSIENT_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504}


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


def find_artifact(specs: list[ArtifactSpec], artifact_id: str) -> ArtifactSpec:
    matches = [spec for spec in specs if spec.id == artifact_id]
    if len(matches) != 1:
        raise ArtifactManifestError(f"artifact id not found: {artifact_id}")
    return matches[0]


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_file(path: Path, spec: ArtifactSpec, label: str) -> None:
    if not path.is_file():
        raise ArtifactManifestError(f"{label} is not a file: {path}")
    actual_size = path.stat().st_size
    if actual_size != spec.size_bytes:
        raise ArtifactManifestError(
            f"{label} size mismatch for {spec.id}: expected {spec.size_bytes}, got {actual_size}"
        )
    actual_sha = _sha256_file(path)
    if actual_sha != spec.sha256:
        raise ArtifactManifestError(
            f"{label} sha256 mismatch for {spec.id}: expected {spec.sha256}, got {actual_sha}"
        )


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, HfHubHTTPError) and exc.response is not None:
        return exc.response.status_code in TRANSIENT_HTTP_STATUS
    return False


def _retry_transfer(
    operation: Callable[[], None],
    *,
    attempts: int = 3,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    for attempt in range(1, attempts + 1):
        try:
            operation()
            return
        except Exception as exc:
            if attempt == attempts or not _is_transient(exc):
                raise
            sleeper(float(attempt))


def publish_artifact(
    spec: ArtifactSpec,
    local_path: Path,
    *,
    dry_run: bool = False,
    batcher: Callable[..., Any] = batch_bucket_files,
    downloader: Callable[..., Any] = download_bucket_files,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    local_path = local_path.resolve()
    _verify_file(local_path, spec, "local artifact")
    base_result = {
        "schema_version": 1,
        "artifact_id": spec.id,
        "remote_uri": spec.remote_uri,
        "size_bytes": spec.size_bytes,
        "sha256": spec.sha256,
        "local_path": str(local_path),
    }
    if dry_run:
        return {**base_result, "status": "PLANNED", "remote_verified": False}

    upload_completed = False
    try:
        _retry_transfer(
            lambda: batcher(spec.storage_bucket, add=[(local_path, spec.storage_path)]),
            sleeper=sleeper,
        )
        upload_completed = True
        with tempfile.TemporaryDirectory(prefix="hf-cache-readback-") as temp_dir:
            readback = Path(temp_dir) / Path(spec.storage_path).name
            _retry_transfer(
                lambda: downloader(
                    spec.storage_bucket,
                    files=[(spec.storage_path, readback)],
                    raise_on_missing_files=True,
                ),
                sleeper=sleeper,
            )
            _verify_file(readback, spec, "remote readback")
    except Exception:
        if upload_completed:
            try:
                batcher(spec.storage_bucket, delete=[spec.storage_path])
            except Exception:
                pass
        raise

    return {**base_result, "status": "PUBLISHED", "remote_verified": True}


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
    parser.add_argument("--manifest", type=Path, default=Path("artifacts.yaml"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")

    publish = subparsers.add_parser("publish")
    publish.add_argument("path", type=Path)
    publish.add_argument("--id", dest="artifact_id", required=True)
    publish.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    try:
        specs = load_artifact_manifest(args.manifest)
        if args.command == "validate":
            result = manifest_summary(specs, args.manifest)
        else:
            result = publish_artifact(
                find_artifact(specs, args.artifact_id),
                args.path,
                dry_run=args.dry_run,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    except (ArtifactManifestError, OSError, yaml.YAMLError, HfHubHTTPError) as exc:
        print(json.dumps({"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
