# Runtime model authority

`hf-cache-hub` is the model authority for local agent runtimes. OpenClaw, Hermes Agent, OpenCode, and other clients should consume the resolved local endpoint; they should not independently choose `latest`, download model bytes, or guess Hugging Face cache paths.

## Current local-agent profile

`runtime-models.yaml` declares `ornith-9b-q4`, backed by the exact pinned Hugging Face revision in `models.yaml` and the exact file `ornith-1.0-9b-Q4_K_M.gguf`.

The profile defaults to a 32K context and llama.cpp settings intended to keep an 8 GB-class NVIDIA GPU usable: fit-to-VRAM, 512 MiB target margin, Flash Attention, and Q8 KV cache. These are runtime defaults, not model identity; edit the runtime profile if a machine needs different tuning. The model revision remains owned by `models.yaml`.

Primary model source:
- https://huggingface.co/ornith-ai/Ornith-1.0-9B-GGUF
- pinned revision: `3296bc7a404871a72ac3f1903f561459c09b5c17`

llama.cpp server options are documented upstream:
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md

## One-time environment

```bash
export HF_CACHE_HUB_ROOT="$HOME/src/hf-cache-hub"
export HF_HOME="$HOME/hf-cache"
export HF_HUB_CACHE="$HF_HOME/hub"
cd "$HF_CACHE_HUB_ROOT"
```

Install the Python dependencies with the repository's normal `uv` environment. Install a CUDA-enabled `llama-server` separately and ensure it is on `PATH`; alternatively set `LLAMA_SERVER_BIN` to its executable path.

## Resolve without network access

```bash
task runtime:plan -- ornith-9b-q4
```

A cache hit returns `status: READY`, the full model revision, exact snapshot path, exact GGUF path, OpenAI-compatible `base_url`, and `served_model_id`. A cache miss returns `CACHE_MISS`; it does not silently download.

## Prewarm the exact pinned model

```bash
task runtime:prewarm -- ornith-9b-q4
```

Only the exact revision declared in `models.yaml` is allowed. If Hugging Face resolves a different snapshot directory, the command fails closed.

## Start and verify the server

```bash
task runtime:serve -- ornith-9b-q4
# or prewarm and start in one explicit operation
task runtime:serve -- ornith-9b-q4 --sync

task runtime:status -- ornith-9b-q4
```

Runtime state and logs are written outside the Git repository under `$XDG_STATE_HOME/hf-cache-hub/runtime` or `~/.local/state/hf-cache-hub/runtime`. Set `HF_RUNTIME_STATE_DIR` to override that location.

The server is bound to `127.0.0.1` by default. Do not expose it publicly without a separate authentication/network policy.

The resulting client contract is:

```text
base_url: http://127.0.0.1:8080/v1
model: ornith-9b-q4
```

## Stop

```bash
task runtime:stop -- ornith-9b-q4
```

The stop path checks the recorded PID against `/proc/<pid>/cmdline` when available and refuses to signal a reused PID that no longer matches the recorded model server.

## Cross-runtime rule

OpenClaw / Hermes / OpenCode adapters should do this:

```text
runtime:plan or runtime:status
        ↓
READY + base_url + served_model_id
        ↓
client/provider configuration
```

They should not do this:

```text
runtime-specific hf download
runtime-specific revision selection
mtime/latest snapshot selection
runtime-specific copy of GGUF bytes
```

This keeps revision pinning, cache reuse, access boundaries, and model-file selection in one repository.