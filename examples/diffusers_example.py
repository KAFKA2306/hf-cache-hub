import torch
from diffusers import ZImagePipeline

pipe = ZImagePipeline.from_pretrained(
    "Tongyi-MAI/Z-Image-Turbo",
    torch_dtype=torch.bfloat16,
)

device = "cuda" if torch.cuda.is_available() else "cpu"
pipe.to(device)

print("Pipeline loaded. Performing inference...")
img = pipe("a photo of a cat", num_inference_steps=9, guidance_scale=0.0).images[0]

output_path = "out_diffuser.png"
img.save(output_path)

print(f"Image saved to {output_path}")