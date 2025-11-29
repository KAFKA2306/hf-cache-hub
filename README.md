# README (Minimal but Context-Preserving)

## Shared Cache

All Hugging Face models are stored in a shared cache:

```
HF_HOME=$HOME/hf-cache
HF_HUB_CACHE=$HOME/hf-cache/hub
```

Add to your shell:

```bash
export HF_HOME="$HOME/hf-cache"
export HF_HUB_CACHE="$HOME/hf-cache/hub"
```

This ensures all projects reuse the same downloaded models.

---

## Cache Management

List all cached models:

```bash
task hf:ls
```

Prune unreferenced revisions:

```bash
task hf:prune
```

Legacy detailed scan:

```bash
task hf:scan
```

---

## Adding a Model to This Project

Models **are not downloaded into this repo**.
They live in the shared cache, and the project only “links” to them.

1. Download (if not already cached):

```bash
uvx hf download ORG/REPO
```

2. Link the snapshot into this project:

```bash
task hf:link \
  SRC="$HF_HUB_CACHE/models--ORG--REPO/snapshots/<hash>" \
  DST="REPO"
```

The model will appear under:

```
./models/REPO
```

Links are safe to delete anytime:

```bash
task hf:clean-links
```

`models/` is ignored by Git.

---

## Purpose

This project provides:

* A unified Hugging Face cache
* Commands to inspect and clean it
* A mechanism to **add models to any project without duplicating storage**

Use this repository whenever you want to **add, link, or manage HF models** reproducibly across workspaces.