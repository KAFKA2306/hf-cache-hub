from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "artifact_manager.py"
SPEC = importlib.util.spec_from_file_location("artifact_manager", MODULE_PATH)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)

REV = "a" * 40
SHA = "b" * 64


def write_manifest(path: Path, *, extra: str = "") -> None:
    path.write_text(
        f"""schema_version: 1
artifacts:
  - id: demo/splat
    kind: gaussian-splat
    format: ply
    storage:
      type: huggingface-bucket
      bucket: example/artifacts
      path: gaussian/demo/splat.ply
    size_bytes: 123
    sha256: {SHA}
    provenance:
      repository: KAFKA2306/AutoPhotogrammetry
      revision: {REV}
      source_path: output/demo/export/splat.ply
{extra}""",
        encoding="utf-8",
    )


class ArtifactManifestTest(unittest.TestCase):
    def test_accepts_empty_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "artifacts.yaml"
            p.write_text("schema_version: 1\nartifacts: []\n", encoding="utf-8")
            self.assertEqual([], m.load_artifact_manifest(p))

    def test_resolves_bucket_uri(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "artifacts.yaml"
            write_manifest(p)
            spec = m.load_artifact_manifest(p)[0]
            self.assertEqual(
                "hf://buckets/example/artifacts/gaussian/demo/splat.ply",
                spec.remote_uri,
            )

    def test_rejects_mutable_provenance_revision(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "artifacts.yaml"
            write_manifest(p)
            p.write_text(p.read_text().replace(REV, "main"), encoding="utf-8")
            with self.assertRaises(m.ArtifactManifestError):
                m.load_artifact_manifest(p)

    def test_rejects_missing_sha256(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "artifacts.yaml"
            write_manifest(p)
            p.write_text(p.read_text().replace(f"    sha256: {SHA}\n", ""), encoding="utf-8")
            with self.assertRaises(m.ArtifactManifestError):
                m.load_artifact_manifest(p)

    def test_rejects_unknown_field(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "artifacts.yaml"
            write_manifest(p, extra="    unexpected: true\n")
            with self.assertRaises(m.ArtifactManifestError):
                m.load_artifact_manifest(p)

    def test_rejects_credential_field_anywhere(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "artifacts.yaml"
            write_manifest(p, extra="    hf_token: secret\n")
            with self.assertRaises(m.ArtifactManifestError):
                m.load_artifact_manifest(p)

    def test_requires_source_path_or_run_id(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "artifacts.yaml"
            write_manifest(p)
            p.write_text(
                p.read_text().replace("      source_path: output/demo/export/splat.ply\n", ""),
                encoding="utf-8",
            )
            with self.assertRaises(m.ArtifactManifestError):
                m.load_artifact_manifest(p)


if __name__ == "__main__":
    unittest.main()
