from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "artifact_cache.py"
SPEC = importlib.util.spec_from_file_location("artifact_cache", MODULE_PATH)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)


def artifact(payload: bytes = b"artifact-bytes"):
    return m.ArtifactSpec(
        id="demo/blob",
        kind="dataset",
        format="bin",
        storage_bucket="k4fka/artifacts",
        storage_path="demo/blob.bin",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        provenance_repository="KAFKA2306/example",
        provenance_revision="a" * 40,
        provenance_source_path="output/blob.bin",
    )


class ArtifactCacheTest(unittest.TestCase):
    def test_default_cache_is_under_hf_home(self):
        with tempfile.TemporaryDirectory() as d:
            old_home = os.environ.get("HF_HOME")
            old_cache = os.environ.pop("HF_ARTIFACT_CACHE", None)
            try:
                os.environ["HF_HOME"] = d
                self.assertEqual(Path(d) / "artifacts", m.default_cache_root())
            finally:
                if old_home is None:
                    os.environ.pop("HF_HOME", None)
                else:
                    os.environ["HF_HOME"] = old_home
                if old_cache is not None:
                    os.environ["HF_ARTIFACT_CACHE"] = old_cache

    def test_plan_reports_miss_hit_and_corrupt(self):
        payload = b"abc"
        spec = artifact(payload)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertEqual("MISS", m.cache_state(spec, root))
            target = m.cache_path(spec, root)
            target.parent.mkdir(parents=True)
            target.write_bytes(payload)
            self.assertEqual("HIT", m.cache_state(spec, root))
            target.write_bytes(b"bad")
            self.assertEqual("CORRUPT", m.cache_state(spec, root))

    def test_two_projects_reuse_one_download(self):
        payload = b"shared-payload"
        spec = artifact(payload)
        calls = []

        def download(bucket, *, files, raise_on_missing_files):
            calls.append((bucket, files[0][0], raise_on_missing_files))
            Path(files[0][1]).write_bytes(payload)

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            a = root / "project-a" / "artifact.bin"
            b = root / "project-b" / "artifact.bin"
            first = m.resolve_artifact(spec, root / "cache", downloader=download, materialize=a)
            second = m.resolve_artifact(spec, root / "cache", downloader=download, materialize=b)
            self.assertTrue(first["downloaded"])
            self.assertEqual(spec.size_bytes, first["transferred_bytes"])
            self.assertTrue(second["cache_hit"])
            self.assertEqual(0, second["transferred_bytes"])
            self.assertEqual(1, len(calls))
            self.assertEqual(m.cache_path(spec, root / "cache").resolve(), a.resolve())
            self.assertEqual(a.resolve(), b.resolve())

    def test_corrupt_cache_is_replaced(self):
        payload = b"correct"
        spec = artifact(payload)
        calls = 0

        def download(bucket, *, files, raise_on_missing_files):
            nonlocal calls
            calls += 1
            Path(files[0][1]).write_bytes(payload)

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = m.cache_path(spec, root)
            target.parent.mkdir(parents=True)
            target.write_bytes(b"corrupt")
            result = m.resolve_artifact(spec, root, downloader=download)
            self.assertTrue(result["downloaded"])
            self.assertEqual(payload, target.read_bytes())
            self.assertEqual(1, calls)

    def test_bad_download_never_poison_cache(self):
        spec = artifact(b"expected")

        def download(bucket, *, files, raise_on_missing_files):
            Path(files[0][1]).write_bytes(b"wrong")

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaises(m.ArtifactManifestError):
                m.resolve_artifact(spec, root, downloader=download)
            self.assertFalse(m.cache_path(spec, root).exists())
            self.assertEqual([], list(root.rglob("*.part")))

    def test_copy_fallback_materialization_is_verified(self):
        payload = b"copy-me"
        spec = artifact(payload)

        def download(bucket, *, files, raise_on_missing_files):
            Path(files[0][1]).write_bytes(payload)

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            original = m.Path.symlink_to
            try:
                m.Path.symlink_to = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no symlink"))
                destination = root / "project" / "artifact.bin"
                result = m.resolve_artifact(
                    spec, root / "cache", downloader=download,
                    materialize=destination, copy_fallback=True,
                )
            finally:
                m.Path.symlink_to = original
            self.assertEqual("copy", result["materialization"])
            self.assertEqual(payload, destination.read_bytes())


if __name__ == "__main__":
    unittest.main()
