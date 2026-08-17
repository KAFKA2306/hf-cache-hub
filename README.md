# hf-cache-hub

[![Model registry integrity](https://github.com/KAFKA2306/hf-cache-hub/actions/workflows/model-registry-integrity.yml/badge.svg)](https://github.com/KAFKA2306/hf-cache-hub/actions/workflows/model-registry-integrity.yml)
[![Pinned cache contract](https://github.com/KAFKA2306/hf-cache-hub/actions/workflows/pinned-cache.yml/badge.svg)](https://github.com/KAFKA2306/hf-cache-hub/actions/workflows/pinned-cache.yml)

Hugging Face modelをrevision固定で共有cacheへ配置し、複数projectから同一snapshotを参照するための小さなbootstrap utilityです。

## Shared cache

```bash
export HF_HOME="$HOME/hf-cache"
export HF_HUB_CACHE="$HF_HOME/hub"
```

## Model contract

`models.yaml` では `org`、`repo`、full 40-character `revision`、`purpose`、`access`、`license_url`、`model_card_url` を宣言します。tokenやsecretはmanifestへ書きません。

```yaml
models:
  - org: Tongyi-MAI
    repo: Z-Image-Turbo
    revision: f332072aa78be7aecdf3ee76d5c247082da564a6
    purpose: image-generation
    access: PUBLIC
    license_url: https://www.apache.org/licenses/LICENSE-2.0
    model_card_url: https://huggingface.co/Tongyi-MAI/Z-Image-Turbo
```

## Plan and sync

Download前のcache状態を確認:

```bash
task hf:plan
```

指定revisionをdownloadし、そのrevisionのsnapshotだけをlinkして `cache-manifest.json` を生成:

```bash
task hf:sync
```

生成link:

```text
models/REPO -> $HF_HUB_CACHE/models--ORG--REPO/snapshots/<pinned-commit>
```

`hf:sync` はsnapshotのmtimeから「最新」を推測しません。download APIが返したsnapshot名が指定revisionと一致しなければfail closedします。

## Cache management

```bash
task hf:ls
task hf:prune
```

gated/private repositoryは顧客自身のHugging Face credentialを使用します。credentialが取得できなければsyncしません。license URL/model card URLは確認先として保持するだけで、商用利用可否を自動判定しません。

チーム導入PoCの境界と2-project demo手順は [`docs/services/team-cache-bootstrap.md`](docs/services/team-cache-bootstrap.md) を参照してください。
