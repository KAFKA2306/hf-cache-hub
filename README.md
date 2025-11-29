# Hugging Face モデル管理

本プロジェクトは `Taskfile.yaml` を使用して、Hugging Face モデルのキャッシュ管理を自動化します。
環境変数が未設定の場合でも、`$HOME/hf-cache` をキャッシュディレクトリとして自動的に利用します。

---

## 使い方

### 1. 共有キャッシュの場所を確認する

以下のコマンドで、現在有効なキャッシュディレクトリのパスを確認できます。

```bash
task hf:env
```

`HF_HOME` や `HF_HUB_CACHE` といった環境変数を事前に設定していない場合、デフォルトで `$HOME/hf-cache` および `$HOME/hf-cache/hub` が使用されます。

### 2. モデルを共有キャッシュに事前取得する

モデルをダウンロードするには `hf:prefetch` タスクを実行します。

```bash
# 指定したモデルをダウンロード
task hf:prefetch REPO="Tongyi-MAI/Z-Image-Turbo"

# デフォルトのモデル (org/model-name) をダウンロード
task hf:prefetch
```

ダウンロードされたモデルは共有キャッシュに保存され、すべてのプロジェクトから参照可能になります。

### 3. キャッシュの状態を検査する

キャッシュが正常か、またどのモデルが保存されているかを確認するには `hf:scan` タスクを使用します。

```bash
task hf:scan
```

---

## コードからの利用

コード側では特別な設定は不要です。
`from_pretrained` を通常通り呼び出すだけで、`Taskfile.yaml` が管理する共有キャッシュからモデルが自動的に読み込まれます。

```python
# 例: Diffusers
from diffusers import ZImagePipeline
pipe = ZImagePipeline.from_pretrained("Tongyi-MAI/Z-Image-Turbo")

# 例: Transformers
from transformers import AutoModelForCausalLM
model = AutoModelForCausalLM.from_pretrained("Qwen/Qwen2.5-32B-Instruct")
```# hf-cache-hub
