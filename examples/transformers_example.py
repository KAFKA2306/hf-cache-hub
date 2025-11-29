import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

model_id = "Qwen/Qwen2.5-32B-Instruct"

print(f"Loading tokenizer for '{model_id}'...")
tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)

print(f"Loading model '{model_id}'...")
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    device_map="auto",
    torch_dtype="auto",
    trust_remote_code=True,
)

print("Model loaded. Generating text...")
prompt = "hello"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
out = model.generate(**inputs, max_new_tokens=64)
decoded_output = tokenizer.decode(out[0], skip_special_tokens=True)

print(f"Prompt: {prompt}")
print(f"Output: {decoded_output}")