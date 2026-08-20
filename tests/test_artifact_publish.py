from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "artifact_manager.py"
SPEC = importlib.util.spec_from_file_location("artifact_manager_publish", MODULE_PATH)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)

REV = "a" * 40


def make_spec(payload: bytes) -> m.ArtifactSpec:
    return m.ArtifactSpec(
        id="demo/splat",
        kind="gaussian-splat",
        format="ply",
        storage_bucket="example/artifacts",
        storage_path="gaussian/demo/splat.ply",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        provenance_repository="KAFKA2306/AutoPhotogrammetry",
        provenance_revision=REV,
        provenance_source_path="output/demo/export/splat.ply",
    )


class ArtifactPublishTest(unittest.TestCase):
    def test_publish_uploads_then_readback_verifies(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            local = root / "splat.ply"
            payload = b"ply\n" + b"x" * 4096
            local.write_bytes(payload)
            spec = make_spec(payload)
            remote = root / "remote"

            def batcher(bucket, *, add=None, delete=None):
                self.assertEqual("example/artifacts", bucket)
                if add:
                    for source, destination in add:
                        target = remote / destination
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, target)
                if delete:
                    for destination in delete:
                        (remote / destination).unlink(missing_ok=True)

            def downloader(bucket, files, raise_on_missing_files=False):
                self.assertTrue(raise_on_missing_files)
                for source, destination in files:
                    shutil.copyfile(remote / source, destination)

            result = m.publish_artifact(spec, local, batcher=batcher, downloader=downloader)
            self.assertEqual("PUBLISHED", result["status"])
            self.assertTrue(result["remote_verified"])
            self.assertEqual(payload, (remote / spec.storage_path).read_bytes())
            self.assertNotIn("token", json.dumps(result).lower())

    def test_dry_run_does_not_call_remote(self):
        with tempfile.TemporaryDirectory() as d:
            local = Path(d) / "artifact.bin"
            payload = b"abc"
            local.write_bytes(payload)
            spec = make_spec(payload)
            result = m.publish_artifact(
                spec,
                local,
                dry_run=True,
                batcher=lambda *a, **k: self.fail("upload called"),
                downloader=lambda *a, **k: self.fail("download called"),
            )
            self.assertEqual("PLANNED", result["status"])
            self.assertTrue(local.exists())

    def test_local_mismatch_fails_before_upload(self):
        with tempfile.TemporaryDirectory() as d:
            local = Path(d) / "artifact.bin"
            local.write_bytes(b"wrong")
            spec = make_spec(b"expected")
            with self.assertRaises(m.ArtifactManifestError):
                m.publish_artifact(spec, local, batcher=lambda *a, **k: self.fail("upload called"))

    def test_readback_mismatch_cleans_remote_and_keeps_local(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            local = root / "artifact.bin"
            payload = b"expected"
            local.write_bytes(payload)
            spec = make_spec(payload)
            deleted = []

            def batcher(bucket, *, add=None, delete=None):
                if delete:
                    deleted.extend(delete)

            def downloader(bucket, files, raise_on_missing_files=False):
                for _, destination in files:
                    Path(destination).write_bytes(b"corrupt!")

            with self.assertRaises(m.ArtifactManifestError):
                m.publish_artifact(spec, local, batcher=batcher, downloader=downloader)
            self.assertEqual([spec.storage_path], deleted)
            self.assertEqual(payload, local.read_bytes())

    def test_non_transient_error_is_not_retried(self):
        calls = 0

        def fail():
            nonlocal calls
            calls += 1
            raise PermissionError("denied")

        with self.assertRaises(PermissionError):
            m._retry_transfer(fail, sleeper=lambda _: None)
        self.assertEqual(1, calls)


if __name__ == "__main__":
    unittest.main()
