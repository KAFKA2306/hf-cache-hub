from __future__ import annotations

import argparse
import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "generate_video.py"
SPEC = importlib.util.spec_from_file_location("generate_video", MODULE_PATH)
assert SPEC and SPEC.loader
generate_video = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generate_video)


class GenerateVideoTest(unittest.TestCase):
    def test_selects_single_video_model(self) -> None:
        spec = generate_video.ModelSpec(
            org="example",
            repo="video",
            revision="a" * 40,
            purpose="video-generation",
            access="PUBLIC",
            license_url="https://example.com/license",
            model_card_url="https://example.com/model",
        )
        self.assertEqual(spec, generate_video.select_video_model([spec], None))

    def test_rejects_non_video_registry(self) -> None:
        spec = generate_video.ModelSpec(
            org="example",
            repo="image",
            revision="a" * 40,
            purpose="image-generation",
            access="PUBLIC",
            license_url="https://example.com/license",
            model_card_url="https://example.com/model",
        )
        with self.assertRaises(generate_video.RegistryError):
            generate_video.select_video_model([spec], None)

    def test_resolve_snapshot_requires_exact_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            expected = "a" * 40
            wrong = root / ("b" * 40)
            wrong.mkdir()
            (root / "models").mkdir()
            (root / "models" / "video").symlink_to(wrong, target_is_directory=True)
            spec = generate_video.ModelSpec(
                org="example",
                repo="video",
                revision=expected,
                purpose="video-generation",
                access="PUBLIC",
                license_url="https://example.com/license",
                model_card_url="https://example.com/model",
            )
            with self.assertRaises(generate_video.RegistryError):
                generate_video.resolve_snapshot(spec, root)

    def test_build_record_keeps_revision_and_prompt_hash(self) -> None:
        revision = "a" * 40
        spec = generate_video.ModelSpec(
            org="example",
            repo="video",
            revision=revision,
            purpose="video-generation",
            access="PUBLIC",
            license_url="https://example.com/license",
            model_card_url="https://example.com/model",
        )
        args = argparse.Namespace(
            seed=42,
            fps=24,
            device="cuda",
            dtype="bfloat16",
            cpu_offload=True,
            height=None,
            width=None,
            num_frames=81,
            num_inference_steps=None,
            guidance_scale=None,
            prompt="hello video",
            output=Path("out.mp4"),
        )
        record = generate_video.build_record(spec, Path("/cache") / revision, args)
        self.assertEqual(revision, record["revision"])
        self.assertEqual(revision, record["resolved_commit"])
        self.assertEqual(64, len(record["prompt_sha256"]))
        self.assertEqual(81, record["params"]["num_frames"])


if __name__ == "__main__":
    unittest.main()
