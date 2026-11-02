from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "cache_manager.py"
SPEC = importlib.util.spec_from_file_location("cache_manager_h3", MODULE_PATH)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)

REPO_ROOT = Path(__file__).parents[1]
H3_REVISION = "42ed227ee7df40d41602854ae760620d6eb651fe"


class MiniMaxH3ContractTest(unittest.TestCase):
    def setUp(self) -> None:
        specs = m.load_registry(REPO_ROOT / "models.yaml")
        self.h3 = next(spec for spec in specs if spec.repo_id == "MiniMaxAI/MiniMax-H3")

    def _snapshot(self, root: Path) -> Path:
        snapshot = root / "cache" / "models--MiniMaxAI--MiniMax-H3" / "snapshots" / H3_REVISION
        snapshot.mkdir(parents=True)
        return snapshot

    def _materialize_requirements(self, snapshot: Path) -> None:
        for required_path in self.h3.required_paths:
            path = snapshot / required_path
            if path.suffix:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")
            else:
                path.mkdir(parents=True, exist_ok=True)

    def test_registry_pins_official_h3_identity_and_task_families(self):
        self.assertEqual(H3_REVISION, self.h3.revision)
        self.assertEqual(("t2va", "fl2va", "ref2va"), self.h3.task_families)
        self.assertIn("FL2VA/model_index.json", self.h3.required_paths)
        self.assertIn("Ref2VA/model_index.json", self.h3.required_paths)
        for family in ("FL2VA", "Ref2VA"):
            for component in ("processor", "tokenizer", "text_encoder", "transformer", "visual_vae", "audio_vae"):
                self.assertIn(f"{family}/{component}", self.h3.required_paths)

    def test_plan_reports_incomplete_snapshot_and_missing_component(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            snapshot = self._snapshot(root)
            self._materialize_requirements(snapshot)
            missing = snapshot / "Ref2VA/audio_vae"
            missing.rmdir()

            plan = m.plan_registry(
                [self.h3],
                root / "cache",
                downloader=lambda **kwargs: str(snapshot),
            )
            entry = plan["models"][0]
            self.assertEqual("CACHE_INCOMPLETE", entry["status"])
            self.assertTrue(entry["download_required"])
            self.assertFalse(entry["required_path_availability"]["Ref2VA/audio_vae"])
            self.assertIn("Ref2VA/audio_vae", entry["error"])

    def test_sync_fails_closed_when_required_component_is_missing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            snapshot = self._snapshot(root)
            self._materialize_requirements(snapshot)
            (snapshot / "FL2VA/transformer").rmdir()

            with self.assertRaises(m.RegistryError):
                m.sync_registry(
                    [self.h3],
                    root / "cache",
                    root,
                    downloader=lambda **kwargs: str(snapshot),
                )
            manifest = json.loads((root / "cache-manifest.json").read_text(encoding="utf-8"))
            entry = manifest["models"][0]
            self.assertEqual("FAILED", entry["status"])
            self.assertFalse(entry["required_path_availability"]["FL2VA/transformer"])
            self.assertFalse((root / "models/MiniMax-H3").exists())

    def test_sync_manifest_records_verified_components_without_credentials(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            snapshot = self._snapshot(root)
            self._materialize_requirements(snapshot)
            manifest = m.sync_registry(
                [self.h3],
                root / "cache",
                root,
                downloader=lambda **kwargs: str(snapshot),
                now=lambda: datetime(2026, 9, 12, tzinfo=timezone.utc),
            )
            entry = manifest["models"][0]
            self.assertEqual("READY", entry["status"])
            self.assertEqual(H3_REVISION, entry["resolved_commit"])
            self.assertEqual(["t2va", "fl2va", "ref2va"], entry["task_families"])
            self.assertTrue(all(entry["required_path_availability"].values()))
            rendered = json.dumps(manifest).lower()
            self.assertNotIn("hf_token", rendered)
            self.assertNotIn('"token"', rendered)


if __name__ == "__main__":
    unittest.main()
