#!/usr/bin/env python3
"""Resolve declared Storage Bucket artifacts into a shared content-addressed cache."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

from huggingface_hub import download_bucket_files
from huggingface_hub.errors import HfHubHTTPError

MODULE_PATH = Path(__file__).with_name("artifact_manager.py")
SPEC = importlib.util.spec_from_file_location("artifact_manager", MODULE_PATH)
assert SPEC and SPEC.loader
artifact_manager = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = artifact_manager
SPEC.loader.exec_module(artifact_manager)

ArtifactManifestError = artifact_manager.ArtifactManifestError
ArtifactSpec = artifact_manager.ArtifactSpec
find_artifact = artifact_manager.find_artifact
load_artifact_manifest = artifact_manager.load_artifact_manifest
verify_file = artifact_manager._verify_file


def default_cache_root() -> Path:
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    return Path(os.environ.get("HF_ARTIFACT_CACHE", hf_home / "artifacts"))


def cache_path(spec: ArtifactSpec, cache_root: Path) -> Path:
    return cache_root / "sha256" / spec.sha256 / Path(spec.storage_path).name


def cache_state(spec: ArtifactSpec, cache_root: Path) -> str:
    target = cache_path(spec, cache_root)
    if not target.exists():
        return "MISS"
    try:
        verify_file(target, spec, "cached artifact")
    except ArtifactManifestError:
        return "CORRUPT"
    return "HIT"


def _materialize(cache_object: Path, destination: Path, *, copy_fallback: bool) -> str:
    destination = destination.resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        destination.unlink()
    try:
        destination.symlink_to(cache_object.resolve())
        return "symlink"
    except OSError:
        if not copy_fallback:
            raise
        shutil.copy2(cache_object, destination)
        return "copy"


def resolve_artifact(
    spec: ArtifactSpec,
    cache_root: Path,
    *,
    downloader: Callable[..., Any] = download_bucket_files,
    materialize: Path | None = None,
    copy_fallback: bool = False,
) -> dict[str, Any]:
    cache_root = cache_root.expanduser().resolve()
    target = cache_path(spec, cache_root)
    state = cache_state(spec, cache_root)
    downloaded = False

    if state == "CORRUPT":
        target.unlink()
        state = "MISS"

    if state == "MISS":
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
        try:
            downloader(
                spec.storage_bucket,
                files=[(spec.storage_path, temporary)],
                raise_on_missing_files=True,
            )
            verify_file(temporary, spec, "downloaded artifact")
            os.replace(temporary, target)
            downloaded = True
        except Exception:
            if temporary.exists():
                temporary.unlink()
            raise

    verify_file(target, spec, "cached artifact")
    materialized_path = None
    materialization = None
    if materialize is not None:
        materialization = _materialize(target, materialize, copy_fallback=copy_fallback)
        materialized_path = str(materialize.resolve(strict=False))
        if materialization == "copy":
            verify_file(Path(materialized_path), spec, "materialized artifact")

    return {
        "schema_version": 1,
        "status": "READY",
        "artifact_id": spec.id,
        "cache_state": "MISS" if downloaded else "HIT",
        "cache_hit": not downloaded,
        "downloaded": downloaded,
        "transferred_bytes": spec.size_bytes if downloaded else 0,
        "cache_path": str(target),
        "remote_uri": spec.remote_uri,
        "size_bytes": spec.size_bytes,
        "sha256": spec.sha256,
        "materialized_path": materialized_path,
        "materialization": materialization,
    }


def plan_artifacts(specs: list[ArtifactSpec], cache_root: Path) -> dict[str, Any]:
    root = cache_root.expanduser().resolve()
    return {
        "schema_version": 1,
        "cache_root": str(root),
        "artifacts": [
            {
                "id": spec.id,
                "state": cache_state(spec, root),
                "cache_path": str(cache_path(spec, root)),
                "size_bytes": spec.size_bytes,
                "sha256": spec.sha256,
                "remote_uri": spec.remote_uri,
            }
            for spec in specs
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--manifest", type=Path, default=Path("artifacts.yaml"))
    plan.add_argument("--cache-root", type=Path, default=default_cache_root())

    resolve = subparsers.add_parser("resolve")
    resolve.add_argument("--manifest", type=Path, default=Path("artifacts.yaml"))
    resolve.add_argument("--cache-root", type=Path, default=default_cache_root())
    resolve.add_argument("--id", dest="artifact_id", required=True)
    resolve.add_argument("--materialize", type=Path)
    resolve.add_argument("--copy-fallback", action="store_true")

    args = parser.parse_args()
    try:
        specs = load_artifact_manifest(args.manifest)
        if args.command == "plan":
            result = plan_artifacts(specs, args.cache_root)
        else:
            result = resolve_artifact(
                find_artifact(specs, args.artifact_id),
                args.cache_root,
                materialize=args.materialize,
                copy_fallback=args.copy_fallback,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    except (ArtifactManifestError, OSError, HfHubHTTPError) as exc:
        print(json.dumps({"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
