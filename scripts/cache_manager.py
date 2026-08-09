#!/usr/bin/env python3
"""Revision-pinned Hugging Face cache planner and synchronizer."""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
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

    @property
    def repo_id(self) -> str:
        return f"{self.org}/{self.repo}"

    @property
    def link_name(self) -> str:
        return self.repo


def load_registry(path: Path) -> list[ModelSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("models"), list) or not raw["models"]:
        raise RegistryError("models.yaml must contain a non-empty models list")
    specs: list[ModelSpec] = []
    seen: set[str] = set()
    required = {"org", "repo", "revision", "purpose", "access", "license_url", "model_card_url"}
    for index, item in enumerate(raw["models"]):
        if not isinstance(item, dict):
            raise RegistryError(f"models[{index}] must be a mapping")
        missing = sorted(required - set(item))
        unknown = sorted(set(item) - required)
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
        spec = ModelSpec(
            org=item["org"].strip(), repo=item["repo"].strip(), revision=revision,
            purpose=item["purpose"].strip(), access=access,
            license_url=item["license_url"].strip(), model_card_url=item["model_card_url"].strip(),
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
            status = "CACHE_HIT"
            snapshot = str(path)
        item = {
            "repo_id": spec.repo_id, "revision": spec.revision, "access": spec.access,
            "purpose": spec.purpose, "status": status, "download_required": status == "CACHE_MISS",
            "resolved_snapshot": snapshot,
        }
        if status == "AUTH_REQUIRED":
            item["error"] = error
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
        try:
            snapshot = _resolve_snapshot(spec, cache_dir, local_only=False, downloader=downloader)
            if snapshot.name != spec.revision:
                raise RegistryError(
                    f"resolved snapshot for {spec.repo_id} is {snapshot.name}, expected pinned revision {spec.revision}"
                )
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
