# README (Minimal + Automatic Model Add)

## Shared Cache

```
HF_HOME=$HOME/hf-cache
HF_HUB_CACHE=$HF_HOME/hub
```

Add to `~/.bashrc`:

```bash
export HF_HOME="$HOME/hf-cache"
export HF_HUB_CACHE="$HF_HOME/hub"
```

---

## Add Models

List desired models in:

**models.yaml**

```yaml
models:
  - org: mobiuslabsgmbh
    repo: faster-whisper-large-v3-turbo
```

---

## Download & Link (Automatic)

Download all models:

```bash
task hf:auto-download
```

Link them into this project:

```bash
task hf:auto-link
```

Or run full sync:

```bash
task hf:sync
```

This creates:

```
models/REPO -> $HF_HUB_CACHE/models--ORG--REPO/snapshots/<hash>
```

`models/` is ignored by Git.

---

## Cache Management

List:

```bash
task hf:ls
```

Prune:

```bash
task hf:prune
```

Legacy scan:

```bash
task hf:scan
```
