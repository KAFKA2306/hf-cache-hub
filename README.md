# Hugging Face Cache Hub

## キャッシュ場所（公式仕様）
キャッシュの実体は以下に保存されます。

- `$HF_HOME = $HOME/hf-cache`
- `$HF_HUB_CACHE = $HOME/hf-cache/hub`

`~/.bashrc` に以下を追加してください。

```bash
export HF_HOME="$HOME/hf-cache"
export HF_HUB_CACHE="$HOME/hf-cache/hub"
```

## キャッシュ操作（公式 CLI）

### 一覧

```bash
task hf:ls
```

### 不要リビジョン削除

```bash
task hf:prune
```

### 旧 CLI 互換（scan-cache）

```bash
task hf:scan
```

## プロジェクトでのモデル見える化（方式③）

共有キャッシュ内のモデル実体を、各プロジェクトの `models/` に
**シンボリックリンクで見える化**できます。

```bash
task hf:link \
  SRC="$HF_HUB_CACHE/models--ORG--REPO/snapshots/<hash>" \
  DST="REPO"
```

壊れたリンク削除：

```bash
task hf:clean-links
```

※ `models/` は `.gitignore` 済みのためリポジトリには含まれません。