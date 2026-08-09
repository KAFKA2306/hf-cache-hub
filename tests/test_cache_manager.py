from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "cache_manager.py"
SPEC = importlib.util.spec_from_file_location("cache_manager", MODULE_PATH)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)

REV = "a" * 40


def write_registry(path: Path, *, access: str = "PUBLIC") -> None:
    path.write_text(
        f"""models:\n  - org: example\n    repo: model\n    revision: {REV}\n    purpose: test\n    access: {access}\n    license_url: https://example.test/license\n    model_card_url: https://example.test/card\n""",
        encoding="utf-8",
    )


class CacheManagerTest(unittest.TestCase):
    def test_rejects_mutable_revision(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "models.yaml"
            write_registry(p)
            p.write_text(p.read_text().replace(REV, "main"))
            with self.assertRaises(m.RegistryError):
                m.load_registry(p)

    def test_plan_reports_hit_for_exact_revision(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            reg = root / "models.yaml"
            write_registry(reg)
            snapshot = root / "cache/models--example--model/snapshots" / REV
            snapshot.mkdir(parents=True)

            def download(**kwargs):
                self.assertTrue(kwargs["local_files_only"])
                self.assertEqual(REV, kwargs["revision"])
                return str(snapshot)

            plan = m.plan_registry(m.load_registry(reg), root / "cache", downloader=download)
            self.assertEqual("CACHE_HIT", plan["models"][0]["status"])
            self.assertFalse(plan["models"][0]["download_required"])

    def test_two_projects_link_same_pinned_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            reg = root / "models.yaml"
            write_registry(reg)
            cache = root / "cache"
            snapshot = cache / "models--example--model/snapshots" / REV
            snapshot.mkdir(parents=True)

            def download(**kwargs):
                self.assertEqual(REV, kwargs["revision"])
                return str(snapshot)

            now = lambda: datetime(2026, 8, 9, tzinfo=timezone.utc)
            specs = m.load_registry(reg)
            manifests = []
            for name in ("project-a", "project-b"):
                project = root / name
                project.mkdir()
                manifests.append(m.sync_registry(specs, cache, project, downloader=download, now=now))
                self.assertEqual(snapshot.resolve(), (project / "models/model").resolve())
                saved = json.loads((project / "cache-manifest.json").read_text())
                self.assertEqual(REV, saved["models"][0]["resolved_commit"])
            self.assertEqual(manifests[0]["models"][0]["snapshot"], manifests[1]["models"][0]["snapshot"])

    def test_sync_fails_closed_on_wrong_resolved_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            reg = root / "models.yaml"
            write_registry(reg)
            wrong = root / "cache/models--example--model/snapshots" / ("b" * 40)
            wrong.mkdir(parents=True)
            with self.assertRaises(m.RegistryError):
                m.sync_registry(m.load_registry(reg), root / "cache", root, downloader=lambda **_: str(wrong))
            manifest = json.loads((root / "cache-manifest.json").read_text())
            self.assertEqual("FAILED", manifest["models"][0]["status"])
            self.assertFalse((root / "models/model").exists())

    def test_manifest_contains_no_token_field(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            reg = root / "models.yaml"
            write_registry(reg)
            snapshot = root / "cache/models--example--model/snapshots" / REV
            snapshot.mkdir(parents=True)
            manifest = m.sync_registry(m.load_registry(reg), root / "cache", root, downloader=lambda **_: str(snapshot))
            rendered = json.dumps(manifest).lower()
            self.assertNotIn("hf_token", rendered)
            self.assertNotIn('"token"', rendered)


if __name__ == "__main__":
    unittest.main()
