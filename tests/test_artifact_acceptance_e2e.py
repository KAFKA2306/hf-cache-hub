from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "artifact_cache.py"
SPEC = importlib.util.spec_from_file_location("artifact_cache_acceptance", MODULE_PATH)
assert SPEC and SPEC.loader
cache = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cache
SPEC.loader.exec_module(cache)


SYNTHETIC_SIZE = 100 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    git(path, "init", "-q")
    git(path, "config", "user.name", "artifact-acceptance")
    git(path, "config", "user.email", "artifact-acceptance@example.invalid")


def git_dir_size(path: Path) -> int:
    return sum(item.stat().st_size for item in (path / ".git").rglob("*") if item.is_file())


class HeavyArtifactAcceptanceTest(unittest.TestCase):
    def test_100_mib_artifact_stays_out_of_git_and_is_shared_between_two_repositories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            producer = root / "producer"
            consumer_a = root / "consumer-a"
            consumer_b = root / "consumer-b"
            shared_cache = root / "hf-home" / "artifacts"
            remote = root / "fake-storage-bucket" / "synthetic-100m.bin"

            init_repo(producer)
            (producer / ".gitignore").write_text("working/*.bin\n", encoding="utf-8")
            (producer / "README.md").write_text("synthetic producer\n", encoding="utf-8")
            working = producer / "working" / "synthetic-100m.bin"
            working.parent.mkdir()

            block = bytes(range(256)) * 4096
            with working.open("wb") as stream:
                for _ in range(100):
                    stream.write(block)
            self.assertEqual(SYNTHETIC_SIZE, working.stat().st_size)
            digest = sha256_file(working)

            manifest = {
                "schema_version": 1,
                "artifacts": [{
                    "id": "acceptance/synthetic-100m",
                    "kind": "dataset",
                    "format": "bin",
                    "storage": {
                        "type": "huggingface-bucket",
                        "bucket": "k4fka/acceptance-fixture",
                        "path": f"acceptance/{digest}.bin",
                    },
                    "size_bytes": SYNTHETIC_SIZE,
                    "sha256": digest,
                    "provenance": {
                        "repository": "synthetic/producer",
                        "revision": "a" * 40,
                        "source_path": "working/synthetic-100m.bin",
                    },
                }],
            }
            manifest_path = producer / "artifacts.yaml"
            manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
            git(producer, "add", ".gitignore", "README.md", "artifacts.yaml")
            git(producer, "commit", "-qm", "Add thin artifact manifest")
            producer_git_bytes = git_dir_size(producer)
            self.assertNotIn("working/synthetic-100m.bin", git(producer, "ls-files").splitlines())
            self.assertEqual("", git(producer, "status", "--porcelain"))

            remote.parent.mkdir(parents=True)
            shutil.copyfile(working, remote)
            self.assertEqual(digest, sha256_file(remote))

            for consumer in (consumer_a, consumer_b):
                init_repo(consumer)
                (consumer / ".gitignore").write_text("Library/\n", encoding="utf-8")
                (consumer / "artifact-ref.json").write_text(
                    json.dumps({"artifact_id": "acceptance/synthetic-100m", "sha256": digest}) + "\n",
                    encoding="utf-8",
                )
                git(consumer, "add", ".gitignore", "artifact-ref.json")
                git(consumer, "commit", "-qm", "Add thin artifact reference")

            specs = cache.load_artifact_manifest(manifest_path)
            spec = cache.find_artifact(specs, "acceptance/synthetic-100m")
            download_calls = []

            def downloader(bucket, *, files, raise_on_missing_files):
                download_calls.append({"bucket": bucket, "path": files[0][0]})
                shutil.copyfile(remote, Path(files[0][1]))

            first_destination = consumer_a / "Library" / "synthetic-100m.bin"
            first = cache.resolve_artifact(
                spec,
                shared_cache,
                downloader=downloader,
                materialize=first_destination,
                copy_fallback=True,
            )
            self.assertTrue(first["downloaded"])
            self.assertFalse(first["cache_hit"])
            self.assertIsNone(first["transferred_bytes"])
            self.assertEqual("unavailable", first["transfer_measurement"])
            self.assertEqual(digest, sha256_file(first_destination))
            self.assertEqual("", git(consumer_a, "status", "--porcelain"))

            second_destination = consumer_b / "Library" / "synthetic-100m.bin"
            second = cache.resolve_artifact(
                spec,
                shared_cache,
                downloader=downloader,
                materialize=second_destination,
                copy_fallback=True,
            )
            self.assertFalse(second["downloaded"])
            self.assertTrue(second["cache_hit"])
            self.assertEqual(0, second["transferred_bytes"])
            self.assertEqual("no_remote_call", second["transfer_measurement"])
            self.assertEqual(1, len(download_calls))
            self.assertEqual(first["cache_path"], second["cache_path"])
            self.assertEqual(digest, sha256_file(second_destination))
            self.assertEqual("", git(consumer_b, "status", "--porcelain"))

            metrics = {
                "status": "PASS_LOCAL_SYNTHETIC",
                "artifact_size_bytes": SYNTHETIC_SIZE,
                "sha256": digest,
                "producer_git_bytes": producer_git_bytes,
                "consumer_a_git_bytes": git_dir_size(consumer_a),
                "consumer_b_git_bytes": git_dir_size(consumer_b),
                "first_resolve_transferred_bytes": first["transferred_bytes"],
                "second_resolve_transferred_bytes": second["transferred_bytes"],
                "second_resolve_cache_hit": second["cache_hit"],
                "shared_cache_path": first["cache_path"],
                "download_calls": len(download_calls),
            }
            print("ARTIFACT_ACCEPTANCE_METRICS=" + json.dumps(metrics, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
