#!/usr/bin/env python3
"""Revision-pinned Hugging Face cache planner and synchronizer."""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import yaml
from huggingface_hub import get_token, snapshot_download
from huggingface_hub.errors import LocalEntryNotFoundError

REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
ACCESS = {"PUBLIC", "GATED", "PRIVATE"}


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class ModelSpec:
    org: str
    repo: str
    revision: str
    purpose: str
    access: str
    license_url: str
    model_card_url: str
    task_families: tuple[str, ...] = ()
    required_paths: tuple[str, ...] = ()

    @property
    def repo_id(self) -> str:
        return f"{self.org}/{self.repo}"

    @property
    def link_name(self) -> str:
        return self.repo


def _load_string_list(item: dict[str, Any], field: str, index: int) -> tuple[str, ...]:
    raw = item.get(field, [])
    if not isinstance(raw, list) or not all(isinstance(value, str) and value.strip() for value in raw):
        raise RegistryError(f"models[{index}].{field} must be a list of non-empty strings")
    values = tuple(value.strip() for value in raw)
    if len(set(values)) != len(values):
        raise RegistryError(f"models[{index}].{field} must not contain duplicates")
    return values


def _validate_required_path(value: str, index: int) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or \\"\\" in value or value in {"", "."}:
        raise RegistryError(f"models[{index}].required_paths contains unsafe path: {value}")


def load_registry(path: Path) -> list[ModelSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("models"), list) or not raw["models"]:
        raise RegistryError("models.yaml must contain a non-empty models list")
    specs: list[ModelSpec] = []
    seen: set[str] = set()
    required = {"org", "repo", "revision", "purpose", "access", "license_url", "model_card_url"}
    optional = {"task_families", "required_paths"}
    for index, item in enumerate(raw["models"]):
        if not isinstance(item, dict):
            raise RegistryError(f"models[{index}] must be a mapping")
        missing = sorted(required - set(item))
        unknown = sorted(set(item) - required - optional)
        if missing or unknown:
            raise RegistryError(f"models[{index}] schema mismatch: missing={missing}, unknown={unknown}")
        values = {key: item[key] for key in required}
        if not all(isinstance(value, str) and value.strip() for value in values.values()):
            raise RegistryError(f"models[{index}] fields must be non-empty strings")
        revision = item["revision"].strip()
        if not REVISION_RE.fullmatch(revision):
            raise RegistryError(f"models[{index}].revision must be a full lowercase 40-character commit SHA")
        access = item["access"].strip().upper()
        if access not in ACCESS:
            raise RegistryError(f"models[{index}].access must be PUBLIC, GATED, or PRIVATE")
        for field in ("license_url", "model_card_url"):
            if not item[field].startswith("https://"):
                raise RegistryError(f"models[{index}].{field} must use https://")
        task_families = _load_string_list(item, "task_families", index)
        required_paths = _load_string_list(item, "required_paths", index)
        for required_path in required_paths:
            _validate_required_path(required_path, index)
        if bool(task_families) != bool(required_paths):
            raise RegistryError(
                f"models[{index}].task_families and required_paths must either both be declared or both be omitted"
            )
        spec = ModelSpec(
            org=item["org"].strip(), repo=item["repo"].strip(), revision=revision,
            purpose=item["purpose"].strip(), access=access,
            license_url=item["license_url"].strip(), model_card_url=item["model_card_url"].strip(),
            task_families=task_families, required_paths=required_paths,
        )
        if spec.repo_id.casefold() in seen:
            raise RegistryError(f"duplicate model: {spec.repo_id}")
        seen.add(spec.repo_id.casefold())
        specs.append(spec)
    return specs


def _resolve_snapshot(spec: ModelSpec, cache_dir: Path, *, local_only: bool, downloader: Callable[..., str]) -> Path:
    if spec.access != "PUBLIC" and not get_token():
        raise RegistryError(f"authentication required for {spec.repo_id} ({spec.access})")
    return Path(downloader(
        repo_id=spec.repo_id,
        revision=spec.revision,
        cache_dir=str(cache_dir),
        local_files_only=local_only,
        token=True if spec.access != "PUBLIC" else None,
    )).resolve()


def _required_path_availability(snapshot: Path | None, required_paths: tuple[str, ...]) -> dict[str, bool]:
    return {
        required_path: bool(snapshot and (snapshot / required_path).exists())
        for required_path in required_paths
    }


def plan_registry(specs: list[ModelSpec], cache_dir: Path, *, downloader: Callable[..., str] = snapshot_download) -> dict[str, Any]:
    models = []
    for spec in specs:
        status = "CACHE_MISS"
        snapshot = None
        error = None
        try:
            path = _resolve_snapshot(spec, cache_dir, local_only=True, downloader=downloader)
        except (LocalEntryNotFoundError, FileNotFoundError):
            pass
        except RegistryError as exc:
            status = "AUTH_REQUIRED"
            error = str(exc)
        else:
            snapshot = path
            availability = _required_path_availability(snapshot, spec.required_paths)
            status = "CACHE_HIT" if all(availability.values()) else "CACHE_INCOMPLETE"
            if status == "CACHE_INCOMPLETE":
                missing = [path for path, available in availability.items() if not available]
                error = f"missing required paths: {', '.join(missing)}"
        availability = _required_path_availability(snapshot, spec.required_paths)
        item = {
            "repo_id": spec.repo_id, "revision": spec.revision, "access": spec.access,
            "purpose": spec.purpose, "status": status,
            "download_required": status in {"CACHE_MISS", "CACHE_INCOMPLETE"},
            "resolved_snapshot": str(snapshot) if snapshot else None,
            "task_families": list(spec.task_families),
            "required_paths": list(spec.required_paths),
            "required_path_availability": availability,
        }
        if error:
            item["error"] = error
        if status == "AUTH_REQUIRED":
            item["download_required"] = False
        models.append(item)
    return {"schema_version": 1, "cache_root": str(cache_dir.resolve()), "models": models}


def _atomic_symlink(snapshot: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp")
    if temp.exists() or temp.is_symlink():
        temp.unlink()
    temp.symlink_to(snapshot, target_is_directory=True)
    temp.replace(target)


def sync_registry(
    specs: list[ModelSpec], cache_dir: Path, project_root: Path, *,
    downloader: Callable[..., str] = snapshot_download,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    failures: list[str] = []
    for spec in specs:
        link_path = project_root / "models" / spec.link_name
        availability: dict[str, bool] = {path: False for path in spec.required_paths}
        try:
            snapshot = _resolve_snapshot(spec, cache_dir, local_only=False, downloader=downloader)
            if snapshot.name != spec.revision:
                raise RegistryError(
                    f"resolved snapshot for {spec.repo_id} is {snapshot.name}, expected pinned revision {spec.revision}"
                )
            availability = _required_path_availability(snapshot, spec.required_paths)
            missing = [path for path, available in availability.items() if not available]
            if missing:
                raise RegistryError(f"missing required paths for {spec.repo_id}: {', '.join(missing)}")
            _atomic_symlink(snapshot, link_path)
            status = "READY"
            failure = None
        except Exception as exc:
            snapshot = None
            status = "FAILED"
            failure = f"{type(exc).__name__}: {exc}"
            failures.append(f"{spec.repo_id}: {failure}")
        entry = {
            "repo_id": spec.repo_id, "revision": spec.revision, "resolved_commit": spec.revision,
            "snapshot": str(snapshot) if snapshot else None,
            "link": str(link_path), "status": status, "purpose": spec.purpose, "access": spec.access,
            "license_url": spec.license_url, "model_card_url": spec.model_card_url,
            "task_families": list(spec.task_families), "required_paths": list(spec.required_paths),
            "required_path_availability": availability,
        }
        if failure:
            entry["error"] = failure
        entries.append(entry)
    manifest = {
        "schema_version": 1,
        "generated_at": now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "cache_root": str(cache_dir.resolve()),
        "models": entries,
    }
    out = project_root / "cache-manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if failures:
        raise RegistryError("; ".join(failures))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["plan", "sync"])
    parser.add_argument("--registry", type=Path, default=Path("models.yaml"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("HF_HUB_CACHE", Path.home() / ".cache/huggingface/hub")))
    args = parser.parse_args()
    try:
        specs = load_registry(args.registry)
        if args.command == "plan":
            print(json.dumps(plan_registry(specs, args.cache_dir), ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(json.dumps(sync_registry(specs, args.cache_dir, args.project_root), ensure_ascii=False, indent=2, sort_keys=True))
    except RegistryError as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
