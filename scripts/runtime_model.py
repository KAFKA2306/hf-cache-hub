#!/usr/bin/env python3
"""Resolve and supervise pinned local model runtimes from hf-cache-hub contracts."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

import cache_manager


class RuntimeProfileError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeProfile:
    id: str
    repo_id: str
    backend: str
    model_file: str
    served_model_id: str
    context_size: int
    host: str
    port: int
    extra_args: tuple[str, ...]


def load_runtime_profiles(path: Path) -> list[RuntimeProfile]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise RuntimeProfileError("runtime model registry must have schema_version: 1")
    if set(raw) != {"schema_version", "profiles"} or not isinstance(raw.get("profiles"), list):
        raise RuntimeProfileError("runtime model registry must contain only schema_version and profiles")
    profiles: list[RuntimeProfile] = []
    seen: set[str] = set()
    required = {
        "id",
        "repo_id",
        "backend",
        "model_file",
        "served_model_id",
        "context_size",
        "host",
        "port",
        "extra_args",
    }
    for index, item in enumerate(raw["profiles"]):
        if not isinstance(item, dict):
            raise RuntimeProfileError(f"profiles[{index}] must be a mapping")
        missing = sorted(required - set(item))
        unknown = sorted(set(item) - required)
        if missing or unknown:
            raise RuntimeProfileError(
                f"profiles[{index}] schema mismatch: missing={missing}, unknown={unknown}"
            )
        string_fields = ("id", "repo_id", "backend", "model_file", "served_model_id", "host")
        if any(not isinstance(item[field], str) or not item[field].strip() for field in string_fields):
            raise RuntimeProfileError(f"profiles[{index}] string fields must be non-empty")
        if item["backend"] != "llama.cpp":
            raise RuntimeProfileError(f"profiles[{index}].backend must be llama.cpp")
        model_path = Path(item["model_file"])
        if model_path.is_absolute() or ".." in model_path.parts:
            raise RuntimeProfileError(f"profiles[{index}].model_file must be package-relative")
        if not isinstance(item["context_size"], int) or item["context_size"] < 4096:
            raise RuntimeProfileError(f"profiles[{index}].context_size must be an integer >= 4096")
        if not isinstance(item["port"], int) or not 1 <= item["port"] <= 65535:
            raise RuntimeProfileError(f"profiles[{index}].port must be 1..65535")
        if not isinstance(item["extra_args"], list) or any(
            not isinstance(value, str) or not value for value in item["extra_args"]
        ):
            raise RuntimeProfileError(f"profiles[{index}].extra_args must be a list of strings")
        profile_id = item["id"].strip()
        if profile_id.casefold() in seen:
            raise RuntimeProfileError(f"duplicate runtime profile: {profile_id}")
        seen.add(profile_id.casefold())
        profiles.append(
            RuntimeProfile(
                id=profile_id,
                repo_id=item["repo_id"].strip(),
                backend=item["backend"],
                model_file=item["model_file"],
                served_model_id=item["served_model_id"].strip(),
                context_size=item["context_size"],
                host=item["host"].strip(),
                port=item["port"],
                extra_args=tuple(item["extra_args"]),
            )
        )
    if not profiles:
        raise RuntimeProfileError("runtime model registry must contain at least one profile")
    return profiles


def select_profile(profiles: list[RuntimeProfile], profile_id: str) -> RuntimeProfile:
    matches = [profile for profile in profiles if profile.id.casefold() == profile_id.casefold()]
    if len(matches) != 1:
        raise RuntimeProfileError(f"runtime profile not found: {profile_id}")
    return matches[0]


def state_root() -> Path:
    configured = os.environ.get("HF_RUNTIME_STATE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    return (base / "hf-cache-hub" / "runtime").resolve()


def state_path(profile_id: str) -> Path:
    return state_root() / f"{profile_id}.json"


def log_path(profile_id: str) -> Path:
    return state_root() / f"{profile_id}.log"


def resolve_profile(
    profile: RuntimeProfile,
    model_specs: list[cache_manager.ModelSpec],
    cache_dir: Path,
    *,
    sync: bool = False,
    downloader: Callable[..., str] = cache_manager.snapshot_download,
) -> dict[str, Any]:
    spec = cache_manager.select_model(model_specs, repo_id=profile.repo_id)
    resolved = cache_manager.resolve_model(
        spec,
        cache_dir,
        local_only=not sync,
        downloader=downloader,
    )
    base = {
        "profile_id": profile.id,
        "backend": profile.backend,
        "served_model_id": profile.served_model_id,
        "context_size": profile.context_size,
        "host": profile.host,
        "port": profile.port,
        "base_url": f"http://{profile.host}:{profile.port}/v1",
        "repo_id": spec.repo_id,
        "revision": spec.revision,
    }
    if resolved["status"] != "READY":
        return {**base, **resolved, "model_file": None}
    snapshot = Path(resolved["snapshot"])
    model_file = (snapshot / profile.model_file).resolve()
    try:
        model_file.relative_to(snapshot)
    except ValueError as exc:
        raise RuntimeProfileError("resolved model file escaped snapshot root") from exc
    if not model_file.is_file():
        return {
            **base,
            **resolved,
            "status": "FAILED",
            "model_file": str(model_file),
            "error": f"declared model file is missing: {profile.model_file}",
        }
    return {**base, **resolved, "model_file": str(model_file), "error": None}


def _binary_path() -> str | None:
    configured = os.environ.get("LLAMA_SERVER_BIN", "llama-server")
    if os.path.sep in configured:
        path = Path(configured).expanduser().resolve()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    return shutil.which(configured)


def build_command(profile: RuntimeProfile, model_file: Path, binary: str) -> list[str]:
    return [
        binary,
        "-m",
        str(model_file),
        "--ctx-size",
        str(profile.context_size),
        "--host",
        profile.host,
        "--port",
        str(profile.port),
        "--alias",
        profile.served_model_id,
        *profile.extra_args,
    ]


def _port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _health(host: str, port: int, timeout: float = 2.0) -> tuple[bool, str | None]:
    url = f"http://{host}:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            ok = 200 <= response.status < 300
            return ok, None if ok else f"HTTP {response.status}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _pid_matches_state(state: dict[str, Any]) -> bool:
    pid = state.get("pid")
    model_file = state.get("model_file")
    binary = state.get("binary")
    if not isinstance(pid, int) or not isinstance(model_file, str) or not isinstance(binary, str):
        return False
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if not proc_cmdline.exists():
        return _pid_alive(pid)
    try:
        raw = proc_cmdline.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace")
    except OSError:
        return False
    return Path(binary).name in raw and model_file in raw


def _read_state(profile_id: str) -> dict[str, Any] | None:
    path = state_path(profile_id)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def runtime_status(profile: RuntimeProfile) -> dict[str, Any]:
    state = _read_state(profile.id)
    if state is None:
        return {"profile_id": profile.id, "status": "STOPPED", "running": False}
    pid = state.get("pid")
    if not isinstance(pid, int) or not _pid_alive(pid):
        return {**state, "status": "STOPPED", "running": False, "health": "unavailable"}
    if not _pid_matches_state(state):
        return {**state, "status": "PID_REUSED", "running": False, "health": "unavailable"}
    healthy, error = _health(profile.host, profile.port)
    return {
        **state,
        "status": "READY" if healthy else "STARTING_OR_UNHEALTHY",
        "running": True,
        "health": "ok" if healthy else "unreachable",
        "health_error": error,
    }


def serve_runtime(
    profile: RuntimeProfile,
    resolved: dict[str, Any],
    *,
    wait_seconds: float = 90.0,
    popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
) -> dict[str, Any]:
    if resolved.get("status") != "READY" or not resolved.get("model_file"):
        return {**resolved, "status": resolved.get("status", "FAILED")}
    existing = runtime_status(profile)
    if existing.get("running"):
        return {**existing, "status": "ALREADY_RUNNING"}
    if _port_open(profile.host, profile.port):
        return {
            **resolved,
            "status": "PORT_IN_USE",
            "error": f"{profile.host}:{profile.port} is already in use by an unmanaged process",
        }
    binary = _binary_path()
    if binary is None:
        return {**resolved, "status": "BINARY_MISSING", "error": "llama-server is not on PATH"}

    root = state_root()
    root.mkdir(parents=True, exist_ok=True)
    command = build_command(profile, Path(resolved["model_file"]), binary)
    log = log_path(profile.id)
    stream = log.open("ab")
    try:
        process = popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        stream.close()
    state = {
        "profile_id": profile.id,
        "pid": process.pid,
        "binary": binary,
        "model_file": resolved["model_file"],
        "repo_id": resolved["repo_id"],
        "revision": resolved["revision"],
        "served_model_id": profile.served_model_id,
        "host": profile.host,
        "port": profile.port,
        "base_url": resolved["base_url"],
        "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "log": str(log),
    }
    state_path(profile.id).write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    deadline = time.monotonic() + max(wait_seconds, 0)
    while time.monotonic() <= deadline:
        if process.poll() is not None:
            return {
                **state,
                "status": "FAILED",
                "running": False,
                "error": f"llama-server exited with code {process.returncode}",
            }
        healthy, error = _health(profile.host, profile.port)
        if healthy:
            return {**state, "status": "READY", "running": True, "health": "ok"}
        if wait_seconds <= 0:
            return {
                **state,
                "status": "STARTING",
                "running": True,
                "health": "unreachable",
                "health_error": error,
            }
        time.sleep(1)
    return {
        **state,
        "status": "STARTING_OR_UNHEALTHY",
        "running": True,
        "health": "unreachable",
        "error": f"health endpoint did not become ready within {wait_seconds:g}s",
    }


def stop_runtime(profile: RuntimeProfile, *, grace_seconds: float = 10.0) -> dict[str, Any]:
    state = _read_state(profile.id)
    if state is None:
        return {"profile_id": profile.id, "status": "STOPPED", "running": False}
    pid = state.get("pid")
    if not isinstance(pid, int) or not _pid_alive(pid):
        state_path(profile.id).unlink(missing_ok=True)
        return {**state, "status": "STOPPED", "running": False}
    if not _pid_matches_state(state):
        return {
            **state,
            "status": "PID_REUSED",
            "running": False,
            "error": "refusing to signal a PID that no longer matches the recorded llama-server",
        }
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + max(grace_seconds, 0)
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    if _pid_alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            os.kill(pid, signal.SIGKILL)
    state_path(profile.id).unlink(missing_ok=True)
    return {**state, "status": "STOPPED", "running": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["plan", "prewarm", "serve", "status", "stop"])
    parser.add_argument("profile")
    parser.add_argument("--runtime-registry", type=Path, default=Path("runtime-models.yaml"))
    parser.add_argument("--model-registry", type=Path, default=Path("models.yaml"))
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(os.environ.get("HF_HUB_CACHE", Path.home() / ".cache/huggingface/hub")),
    )
    parser.add_argument("--sync", action="store_true", help="Allow serve to prewarm a missing pinned snapshot")
    parser.add_argument("--wait-seconds", type=float, default=90.0)
    args = parser.parse_args()

    try:
        profile = select_profile(load_runtime_profiles(args.runtime_registry), args.profile)
        model_specs = cache_manager.load_registry(args.model_registry)
        if args.command == "status":
            result = runtime_status(profile)
        elif args.command == "stop":
            result = stop_runtime(profile)
        else:
            sync = args.command == "prewarm" or (args.command == "serve" and args.sync)
            resolved = resolve_profile(profile, model_specs, args.cache_dir, sync=sync)
            if args.command in {"plan", "prewarm"}:
                result = resolved
            else:
                result = serve_runtime(profile, resolved, wait_seconds=args.wait_seconds)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.get("status") in {"READY", "ALREADY_RUNNING", "STOPPED"} else 2
    except (RuntimeProfileError, cache_manager.RegistryError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
