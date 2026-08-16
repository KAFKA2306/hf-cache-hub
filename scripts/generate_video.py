#!/usr/bin/env python3
"""Generate a video from a revision-pinned local Hugging Face snapshot."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from cache_manager import ModelSpec, RegistryError, load_registry


def select_video_model(specs: list[ModelSpec], name: str | None) -> ModelSpec:
    video = [spec for spec in specs if spec.purpose == "video-generation"]
    if name:
        video = [spec for spec in video if name in {spec.repo, spec.repo_id}]
    if len(video) != 1:
        choices = [spec.repo_id for spec in video]
        raise RegistryError(f"expected exactly one video-generation model, found {len(video)}: {choices}")
    return video[0]


def resolve_snapshot(spec: ModelSpec, project_root: Path) -> Path:
    link = project_root / "models" / spec.link_name
    if not link.is_symlink():
        raise RegistryError(f"missing pinned model link: {link}; run task hf:sync first")
    snapshot = link.resolve()
    if not snapshot.is_dir():
        raise RegistryError(f"model snapshot does not exist: {snapshot}")
    if snapshot.name != spec.revision:
        raise RegistryError(
            f"resolved snapshot for {spec.repo_id} is {snapshot.name}, expected {spec.revision}"
        )
    return snapshot


def build_record(spec: ModelSpec, snapshot: Path, args: argparse.Namespace) -> dict[str, Any]:
    params = {
        "seed": args.seed,
        "fps": args.fps,
        "device": args.device,
        "dtype": args.dtype,
        "cpu_offload": args.cpu_offload,
    }
    for key in ("height", "width", "num_frames", "num_inference_steps", "guidance_scale"):
        value = getattr(args, key)
        if value is not None:
            params[key] = value
    return {
        "repo_id": spec.repo_id,
        "revision": spec.revision,
        "resolved_commit": snapshot.name,
        "snapshot": str(snapshot),
        "prompt": args.prompt,
        "prompt_sha256": hashlib.sha256(args.prompt.encode("utf-8")).hexdigest(),
        "output": str(args.output),
        "params": params,
    }


def generate(record: dict[str, Any], args: argparse.Namespace) -> None:
    import torch
    from diffusers import DiffusionPipeline
    from diffusers.utils import export_to_video

    dtype = None if args.dtype == "auto" else getattr(torch, args.dtype)
    load_kwargs = {} if dtype is None else {"torch_dtype": dtype}
    pipe = DiffusionPipeline.from_pretrained(record["snapshot"], **load_kwargs)
    if args.cpu_offload:
        pipe.enable_model_cpu_offload()
    else:
        pipe.to(args.device)

    generator_device = "cuda" if args.device.startswith("cuda") else args.device
    kwargs: dict[str, Any] = {
        "prompt": args.prompt,
        "generator": torch.Generator(generator_device).manual_seed(args.seed),
    }
    for key in ("height", "width", "num_frames", "num_inference_steps", "guidance_scale"):
        value = getattr(args, key)
        if value is not None:
            kwargs[key] = value

    result = pipe(**kwargs)
    frames = getattr(result, "frames", None)
    if frames is None:
        raise RegistryError("pipeline output has no frames")
    if len(frames) == 1 and isinstance(frames[0], (list, tuple)):
        frames = frames[0]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    export_to_video(frames, str(args.output), fps=args.fps)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", help="repo name or org/repo; optional when registry has exactly one video model")
    parser.add_argument("--registry", type=Path, default=Path("models.yaml"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--dtype", choices=["auto", "float16", "bfloat16", "float32"], default="auto")
    parser.add_argument("--cpu-offload", action="store_true")
    parser.add_argument("--height", type=int)
    parser.add_argument("--width", type=int)
    parser.add_argument("--num-frames", type=int)
    parser.add_argument("--num-inference-steps", type=int)
    parser.add_argument("--guidance-scale", type=float)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        spec = select_video_model(load_registry(args.registry), args.model)
        snapshot = resolve_snapshot(spec, args.project_root)
        record = build_record(spec, snapshot, args)
        if not args.dry_run:
            generate(record, args)
            sidecar = args.output.with_suffix(args.output.suffix + ".json")
            sidecar.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"status": "READY" if args.dry_run else "DONE", **record}, ensure_ascii=False, indent=2, sort_keys=True))
    except (RegistryError, AttributeError, OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
