from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

CACHE_SPEC = importlib.util.spec_from_file_location("cache_manager", SCRIPTS / "cache_manager.py")
assert CACHE_SPEC and CACHE_SPEC.loader
cache_manager = importlib.util.module_from_spec(CACHE_SPEC)
sys.modules["cache_manager"] = cache_manager
CACHE_SPEC.loader.exec_module(cache_manager)

RUNTIME_SPEC = importlib.util.spec_from_file_location("runtime_model", SCRIPTS / "runtime_model.py")
assert RUNTIME_SPEC and RUNTIME_SPEC.loader
runtime_model = importlib.util.module_from_spec(RUNTIME_SPEC)
sys.modules["runtime_model"] = runtime_model
RUNTIME_SPEC.loader.exec_module(runtime_model)

REV = "a" * 40


def write_model_registry(path: Path) -> None:
    path.write_text(
        f"""models:\n  - org: example\n    repo: agent-gguf\n    revision: {REV}\n    purpose: local-agent\n    access: PUBLIC\n    license_url: https://example.test/license\n    model_card_url: https://example.test/card\n""",
        encoding="utf-8",
    )


def write_runtime_registry(path: Path, *, model_file: str = "agent-Q4_K_M.gguf") -> None:
    path.write_text(
        f"""schema_version: 1\nprofiles:\n  - id: local-agent\n    repo_id: example/agent-gguf\n    backend: llama.cpp\n    model_file: {model_file}\n    served_model_id: local-agent\n    context_size: 32768\n    host: 127.0.0.1\n    port: 18080\n    extra_args:\n      - --fit\n      - \"on\"\n""",
        encoding="utf-8",
    )


class RuntimeModelTest(unittest.TestCase):
    def test_profile_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "runtime-models.yaml"
            write_runtime_registry(path, model_file="../secret.gguf")
            with self.assertRaises(runtime_model.RuntimeProfileError):
                runtime_model.load_runtime_profiles(path)

    def test_resolve_profile_uses_exact_snapshot_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            models = root / "models.yaml"
            runtime = root / "runtime-models.yaml"
            write_model_registry(models)
            write_runtime_registry(runtime)
            snapshot = root / "cache/models--example--agent-gguf/snapshots" / REV
            snapshot.mkdir(parents=True)
            model_file = snapshot / "agent-Q4_K_M.gguf"
            model_file.write_bytes(b"gguf")
            calls = []

            def download(**kwargs):
                calls.append(kwargs)
                return str(snapshot)

            profile = runtime_model.load_runtime_profiles(runtime)[0]
            result = runtime_model.resolve_profile(
                profile,
                cache_manager.load_registry(models),
                root / "cache",
                downloader=download,
            )
            self.assertEqual("READY", result["status"])
            self.assertEqual(str(model_file.resolve()), result["model_file"])
            self.assertEqual(REV, result["revision"])
            self.assertTrue(calls[0]["local_files_only"])
            self.assertEqual("http://127.0.0.1:18080/v1", result["base_url"])

    def test_missing_declared_gguf_fails_before_server_start(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            models = root / "models.yaml"
            runtime = root / "runtime-models.yaml"
            write_model_registry(models)
            write_runtime_registry(runtime)
            snapshot = root / "cache/models--example--agent-gguf/snapshots" / REV
            snapshot.mkdir(parents=True)
            profile = runtime_model.load_runtime_profiles(runtime)[0]
            result = runtime_model.resolve_profile(
                profile,
                cache_manager.load_registry(models),
                root / "cache",
                downloader=lambda **_: str(snapshot),
            )
            self.assertEqual("FAILED", result["status"])
            self.assertIn("declared model file is missing", result["error"])

    def test_build_command_is_local_file_only_and_aliases_api_model(self):
        profile = runtime_model.RuntimeProfile(
            id="local-agent",
            repo_id="example/agent",
            backend="llama.cpp",
            model_file="agent.gguf",
            served_model_id="local-agent",
            context_size=32768,
            host="127.0.0.1",
            port=18080,
            extra_args=("--fit", "on"),
        )
        command = runtime_model.build_command(profile, Path("/cache/agent.gguf"), "/usr/bin/llama-server")
        self.assertEqual("/usr/bin/llama-server", command[0])
        self.assertIn("/cache/agent.gguf", command)
        self.assertIn("--alias", command)
        self.assertIn("local-agent", command)
        self.assertNotIn("-hf", command)
        self.assertNotIn("--hf-repo", command)

    def test_serve_writes_sanitized_state_outside_repository(self):
        class FakeProcess:
            pid = 43210
            returncode = None

            def poll(self):
                return None

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            model = root / "agent.gguf"
            model.write_bytes(b"gguf")
            profile = runtime_model.RuntimeProfile(
                id="local-agent",
                repo_id="example/agent",
                backend="llama.cpp",
                model_file="agent.gguf",
                served_model_id="local-agent",
                context_size=32768,
                host="127.0.0.1",
                port=18080,
                extra_args=(),
            )
            resolved = {
                "status": "READY",
                "model_file": str(model),
                "repo_id": "example/agent",
                "revision": REV,
                "base_url": "http://127.0.0.1:18080/v1",
            }
            old = os.environ.get("HF_RUNTIME_STATE_DIR")
            os.environ["HF_RUNTIME_STATE_DIR"] = str(root / "state")
            try:
                with (
                    patch.object(runtime_model, "_binary_path", return_value="/usr/bin/llama-server"),
                    patch.object(runtime_model, "_port_open", return_value=False),
                    patch.object(runtime_model, "_health", return_value=(False, "not ready")),
                    patch.object(runtime_model, "runtime_status", return_value={"running": False}),
                ):
                    result = runtime_model.serve_runtime(
                        profile,
                        resolved,
                        wait_seconds=0,
                        popen=lambda *args, **kwargs: FakeProcess(),
                    )
                self.assertEqual("STARTING", result["status"])
                state = runtime_model.state_path(profile.id).read_text(encoding="utf-8").lower()
                self.assertNotIn("token", state)
                self.assertNotIn("api_key", state)
                self.assertIn(REV, state)
            finally:
                if old is None:
                    os.environ.pop("HF_RUNTIME_STATE_DIR", None)
                else:
                    os.environ["HF_RUNTIME_STATE_DIR"] = old


if __name__ == "__main__":
    unittest.main()
