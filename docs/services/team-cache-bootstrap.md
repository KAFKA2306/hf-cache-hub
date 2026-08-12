# Team Cache Bootstrap

小規模AIチーム向けに、顧客自身が利用権を持つHugging Face repositoryを顧客管理下の共有cacheへ配置し、複数projectから同一revisionを参照するための導入手順です。

## 対象

- 2〜20台程度のGPU workstationを運用するAI開発チーム
- 複数クリエイターへ同一model setを配布する制作スタジオ
- 顧客環境へローカルAIを導入する受託開発会社

## 無料sample

`models.yaml`、`task hf:plan`、`task hf:sync` と生成される `cache-manifest.json` を使い、1台・1projectでrevision固定cacheを確認できます。

## 有償PoCの範囲

有償PoCは、顧客が用意した2〜5台・3〜10 model程度を想定し、共有cache root設計、manifest作成、bootstrap、同一resolved revisionへの到達確認、cache hit/missとsetup手順の実測を行う導入支援です。model weightそのものの販売・再配布は行いません。容量削減量、工数削減率、導入時間は実測前に保証しません。

## 顧客側で準備するもの

- 利用対象modelへアクセスできるHugging Face account
- gated/private repositoryを利用する場合の顧客自身のcredential
- cacheを置く顧客管理下storage
- 各modelの利用条件・licenseを判断する担当者

credentialは `models.yaml`、`cache-manifest.json`、Git、営業analyticsへ保存しません。gated/private entryは認証情報を取得できない場合にfail closedします。`license_url` と `model_card_url` は確認先であり、ツールは商用利用可否を自動判定しません。

## デモ手順

1. `models.yaml` の各modelをfull 40-character commit SHAへ固定する。
2. `task hf:plan` でcache hit/missとdownload要否を確認する。
3. project Aで `task hf:sync` を実行し、`cache-manifest.json` と `models/<repo>` symlinkを確認する。
4. 同じ `models.yaml` と `HF_HUB_CACHE` を使うclean project Bで再度 `task hf:sync` を実行する。
5. 両manifestの `revision`、`resolved_commit`、`snapshot` が一致することを確認する。
6. workstation count、model count、cache hit/miss、cache bytesを観測値として記録する。duplicate bytes avoidedは実測できた場合だけ記録する。

## Prospecting registry

`data/team-cache-prospects.json` は、AI/LLMの研究開発またはAIサービス開発を各組織の公式ページで確認できた候補だけを保存します。登録は営業実績を意味しません。初期状態は全件 `RESEARCHED_NOT_CONTACTED` とし、公開情報だけから「共有cacheが必要」「購入意向がある」「有効商談である」と推定しません。

提案を実施した場合も、組織ID、状態、日付、公開可能な証拠だけを記録し、担当者氏名、個人メール、Hugging Face token、model内容は営業台帳へ保存しません。Issue #2の「直接提案10件」は、実際の送信・面談等を証明できる証拠が付くまで0件として扱います。

## Funnel記録

`data/service-funnel.json` をservice funnelの正準台帳とします。イベント種別は次の6つだけです。

- `service_page_viewed`
- `sample_manifest_opened`
- `bootstrap_inquiry_started`
- `qualified_inquiry`
- `pilot_booked`
- `paid_pilot`

台帳は `repository_recorded_evidence_only` です。現時点でWebアクセス解析は接続していないため `traffic_measurement=not_instrumented` とし、ledger上の0件を「実アクセス0」と解釈しません。商談系は `commercial_measurement=evidence_only` とし、`qualified_inquiry`、`pilot_booked`、`paid_pilot` はHTTPSの証拠URLなしでは記録できません。

個人名、メール、電話、住所、Hugging Face token、API key、model名・model内容・repository IDはfunnel eventへ保存しません。記録可能なfieldは `event_id`、`event_type`、`occurred_at`、`channel`、`evidence_url` だけです。

検証と集計:

```bash
task service:funnel-audit
```

または:

```bash
python scripts/service_funnel.py validate
python scripts/service_funnel.py summary
```

`summary` の件数は「repositoryへ証拠付きで記録されたevent数」であり、実トラフィック全体や実市場需要の推計ではありません。公開後60日のKPIはこの台帳とIssue #2の証拠から集計し、未計装の指標を0へ変換しません。

## 問い合わせ

PoC相談は GitHub Issue #2 を利用してください: https://github.com/KAFKA2306/hf-cache-hub/issues/2
