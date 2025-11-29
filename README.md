## キャッシュ場所（公式）
HF_HOME=$HOME/hf-cache
HF_HUB_CACHE=$HF_HOME/hub
すべての Hugging Face Hub キャッシュはここに集約されます。

## キャッシュ操作（公式 CLI）
- 一覧  
  `task hf:ls`
- 不要分の削除  
  `task hf:prune`
- 旧 CLI の互換スキャン  
  `task hf:scan`

## プロジェクトでのモデルの使い方（方式③）
``models/`` に symlink を貼ることでプロジェクトに“見える化”します。

```bash
task hf:link SRC="$HF_HUB_CACHE/models--ORG--NAME/snapshots/<hash>" DST="NAME"
```

壊れリンク削除：

```bash
task hf:clean-links
```

これにより、実体は共有キャッシュ、プロジェクトは models/ だけを参照します。

