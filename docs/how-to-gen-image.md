# How to Generate Images with Z-Image-Turbo

Reference Implementation: `/home/kafka/projects/huggingface`

This guide provides a minimal setup to generate images using `Tongyi-MAI/Z-Image-Turbo`.
Copy and paste this context to an LLM to replicate the environment, or refer to the absolute paths below.

## 1. Dependencies

**Path**: `/home/kafka/projects/huggingface/pyproject.toml`

Crucial: `diffusers` must be installed from the git source to support `ZImagePipeline`.

```toml
[project]
name = "image-gen"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
  "torch",
  "diffusers @ git+https://github.com/huggingface/diffusers",
  "transformers",
  "huggingface-hub",
]
```

## 2. Minimal Script

**Path**: `/home/kafka/projects/huggingface/src/main.py`

```python
import torch
from diffusers import ZImagePipeline

# Load pipeline (requires CUDA)
pipe = ZImagePipeline.from_pretrained(
    "Tongyi-MAI/Z-Image-Turbo",
    torch_dtype=torch.bfloat16,
    low_cpu_mem_usage=False
).to("cuda")

# Generate image
# 9 steps is recommended for Turbo
image = pipe(
    prompt="a cozy evening street in Kyoto, cinematic lighting",
    height=1024,
    width=1024,
    num_inference_steps=9,
    guidance_scale=0.0,
    generator=torch.Generator("cuda").manual_seed(42),
).images[0]

image.save("output.png")
```

## 3. Execution

Working Directory: `/home/kafka/projects/huggingface`

Using `uv`:

```bash
cd /home/kafka/projects/huggingface
uv sync
uv run src/main.py
```
