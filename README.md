# hf-cache-hub

[![CI](https://github.com/KAFKA2306/hf-cache-hub/actions/workflows/pinned-cache.yml/badge.svg)](https://github.com/KAFKA2306/hf-cache-hub/actions/workflows/pinned-cache.yml)

Hugging Face modelをrevision固定で共有cacheへ配置し、複数projectから同一snapshotを参照するための小さなbootstrap utilityです。生成artifactについては、Gitにlarge binaryを置かず、Storage Bucket上のobjectをhashとprovenanceで宣言するcontractも提供します。

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

## Artifact contract

`artifacts.yaml` は生成済みlarge artifactの実体を保持しません。Storage Bucket上のobjectを `bucket + path` で指し、mutableなremote locationとは別に `size_bytes`、SHA-256、生成元repositoryのexact Git commitを保持します。

```yaml
schema_version: 1
artifacts:
  - id: example/splat
    kind: gaussian-splat
    format: ply
    storage:
      type: huggingface-bucket
      bucket: KAFKA2306/artifacts
      path: gaussian/example/splat.ply
    size_bytes: 123456
    sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
    provenance:
      repository: KAFKA2306/AutoPhotogrammetry
      revision: 0123456789abcdef0123456789abcdef01234567
      source_path: output/example/export/splat.ply
```

検証:

```bash
python scripts/artifact_manager.py validate
```

schemaはunknown fieldを拒否し、`sha256`、`size_bytes`、exact 40-character provenance revision、`source_path`または`run_id`を必須にします。token、secret、password、credential、API keyに相当するfieldはmanifestのどの階層でも拒否します。`storage.bucket` と `storage.path` は `hf://buckets/<namespace>/<bucket>/<path>` に一意に解決されます。

## Artifact publish

manifestに宣言済みのlocal fileをStorage Bucketへpublishする前に、local sizeとSHA-256を検証します。upload後は同じremote objectを一時fileへreadbackし、sizeとSHA-256が完全一致した場合だけ `PUBLISHED` を返します。

```bash
python scripts/artifact_manager.py publish ./output/splat.ply --id example/splat
```

remoteへ書かず計画だけ確認:

```bash
python scripts/artifact_manager.py publish ./output/splat.ply --id example/splat --dry-run
```

publish/readbackにはHugging Face公式Python APIの `batch_bucket_files` と `download_bucket_files` を使用します。認証情報は引数・manifest・result JSONへ保存せず、Hugging Faceの既存credential/OIDC経路に委ねます。認証・upload・readback・hash検証のいずれかが失敗すれば非0終了し、readback不一致時はremote objectをbest effortで削除します。local artifactは自動削除しません。

Hugging Face公式ではStorage BucketsはGit historyを持たないmutable object storageで、checkpoint、logs、intermediate artifacts等のlarge working data向けです。versioned model/dataset repositoryとは責務を分離します。

- https://huggingface.co/docs/hub/storage-buckets
- https://huggingface.co/docs/huggingface_hub/guides/buckets

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
