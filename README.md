# README

## Cache Location

Hugging Face Hub cache is stored here:

```
HF_HOME=$HOME/hf-cache
HF_HUB_CACHE=$HF_HOME/hub
```

Add this to `~/.bashrc`:

```bash
export HF_HOME="$HOME/hf-cache"
export HF_HUB_CACHE="$HOME/hf-cache/hub"
```

---

## Cache Commands (official)

List cache:

```bash
task hf:ls
```

Prune unreferenced revisions:

```bash
task hf:prune
```

Legacy CLI scan:

```bash
task hf:scan
```

---

## Project Model Linking

Link a cached model snapshot into `./models/`:

```bash
task hf:link \
  SRC="$HF_HUB_CACHE/models--ORG--REPO/snapshots/<hash>" \
  DST="REPO"
```

Remove broken links:

```bash
task hf:clean-links
```

---

## Git Ignore

`models/` is ignored and not committed.
