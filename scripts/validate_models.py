#!/usr/bin/env python3
"""Validate the Hugging Face model registry and emit an audit report."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

MODEL_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_FIELDS = {
    "org",
    "repo",
    "revision",
    "link_name",
    "purpose",
    "access",
    "license_url",
    "model_card_url",
}


def _issue(code: str, message: str, index: int | None = None) -> dict[str, Any]:
    issue: dict[str, Any] = {"code": code, "message": message}
    if index is not None:
        issue["index"] = index
    return issue


def validate_registry(data: Any, *, require_revision: bool = False) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    normalized: list[dict[str, str]] = []

    if not isinstance(data, dict):
        errors.append(_issue("root_not_mapping", "registry root must be a mapping"))
        models: list[Any] = []
    else:
        models_value = data.get("models")
        if not isinstance(models_value, list):
            errors.append(_issue("models_not_list", "models must be a list"))
            models = []
        else:
            models = models_value

    seen_ids: dict[str, int] = {}
    seen_links: dict[str, int] = {}

    for index, raw in enumerate(models):
        if not isinstance(raw, dict):
            errors.append(_issue("model_not_mapping", "model entry must be a mapping", index))
            continue

        org = raw.get("org")
        repo = raw.get("repo")
        revision = raw.get("revision")
        link_name = raw.get("link_name", repo)

        if not isinstance(org, str) or not MODEL_PART_RE.fullmatch(org):
            errors.append(_issue("invalid_org", "org must be a valid Hugging Face namespace", index))
        if not isinstance(repo, str) or not MODEL_PART_RE.fullmatch(repo):
            errors.append(_issue("invalid_repo", "repo must be a valid Hugging Face repository name", index))
        if not isinstance(link_name, str) or not MODEL_PART_RE.fullmatch(link_name):
            errors.append(_issue("invalid_link_name", "link_name must be a safe path component", index))

        if revision is None:
            target = errors if require_revision else warnings
            target.append(
                _issue(
                    "missing_revision",
                    "revision is not pinned; synchronization is not fully reproducible",
                    index,
                )
            )
        elif not isinstance(revision, str) or not REVISION_RE.fullmatch(revision):
            errors.append(
                _issue(
                    "invalid_revision",
                    "revision must be a full 40-character lowercase commit SHA",
                    index,
                )
            )

        if isinstance(org, str) and isinstance(repo, str):
            model_id = f"{org}/{repo}"
            canonical = model_id.casefold()
            if canonical in seen_ids:
                errors.append(
                    _issue(
                        "duplicate_model",
                        f"duplicates model at index {seen_ids[canonical]}: {model_id}",
                        index,
                    )
                )
            else:
                seen_ids[canonical] = index

            if isinstance(link_name, str):
                link_key = link_name.casefold()
                if link_key in seen_links:
                    errors.append(
                        _issue(
                            "duplicate_link_name",
                            f"duplicates link_name at index {seen_links[link_key]}: {link_name}",
                            index,
                        )
                    )
                else:
                    seen_links[link_key] = index

            normalized_entry = {"model_id": model_id, "link_name": str(link_name)}
            if isinstance(revision, str):
                normalized_entry["revision"] = revision
            normalized.append(normalized_entry)

        unknown = sorted(set(raw) - ALLOWED_FIELDS)
        if unknown:
            errors.append(
                _issue(
                    "unknown_fields",
                    f"unsupported fields: {', '.join(unknown)}",
                    index,
                )
            )

    if not models:
        errors.append(_issue("empty_registry", "at least one model must be declared"))

    return {
        "schema_version": 1,
        "model_count": len(normalized),
        "models": sorted(normalized, key=lambda item: item["model_id"].casefold()),
        "errors": errors,
        "warnings": warnings,
    }


def audit_file(path: Path, *, require_revision: bool = False) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        result = {
            "schema_version": 1,
            "model_count": 0,
            "models": [],
            "errors": [_issue("invalid_yaml", str(exc))],
            "warnings": [],
        }
    else:
        result = validate_registry(parsed, require_revision=require_revision)

    result.update(
        {
            "source": str(path),
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "error_count": len(result["errors"]),
            "warning_count": len(result["warnings"]),
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("registry", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--require-revision", action="store_true")
    args = parser.parse_args()

    result = audit_file(args.registry, require_revision=args.require_revision)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
