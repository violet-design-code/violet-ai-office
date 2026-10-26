# 第2段階: 依頼の分類・役割分担・成果物集約

## 調査・実装範囲

第1段階の28人設定、SQLite基盤、ローカルCLI、既存13テストを確認し、全件成功後に変更した。既存の担当自動選択・依頼単位の関連付け・優先順位・集約が不足していたため追加した。別リポジトリ violet-benessseed-line-ai とそのDBは一切変更していない。

本段階はオフラインのルールベース統括AI01。日本語依頼を専門分野のキーワードと照合し、該当する担当を選定、理由と仕事内容を保存する。28人のうちAI01は集約担当、残る27人は設定ルールから選択可能。ただし設定のenabledはすべてfalseのまま。割当は作業依頼の作成を意味し、AIの自律実行やLLM呼出を意味しない。

「最適」は登録されたルールに該当する担当という限定的な意味。否定文・曖昧な依頼・同義語の理解は行わず、複数キーワードは複数担当へ割り当てる。分類と担当はplanで事前確認できる。不一致は要確認として保存し、推測で担当を作らない。高度な文章理解と生成AI実行は未接続。

## 実装

- config/routing.json: 27担当のキーワードと5つの業務分類
- coordinator.py: 分析、複数担当への分解、依頼保存、選定理由、優先度、進捗、日本語報告、参照付き集約
- store.py: requests/request_tasks/request_options/dependenciesを追加。既存テーブル・既存データは保持。依存担当のレビューが未完了の場合は直接claimしても集約を開始できない
- __main__.py: plan/submit/queue/request/result/review/collect/decideを追加
- tests/test_coordination.py: 新規依頼・並行実行・承認・CLI等のテスト

代表依頼のキーは一意。同じキーと同じ入力の再送は同じ依頼を返し、内容・優先度が異なれば拒否。依頼と全子タスクは同じトランザクションで保存し、途中失敗は全体ロールバック。SQLiteトランザクションと第1段階の一意制約で同時実行・二重集約を防ぐ。

専門担当の成果物 → 別担当レビュー → AI01の参照付き集約 → AI28レビュー。自動合格は行わない。集約は原文・担当・成果物IDを保存する処理でありLLM要約ではない。各担当の期待成果物はagents.jsonから参照。失敗・差戻し・却下は依頼を対応必要状態にし、未レビュー成果物は集約しない。

重要判断はmanagement_decision、外部用途は従来のactionに分類し代表承認を要求する。戦略部門の成果物とそれを含む集約にも代表承認が必要。レビュー通過後、代表承認前の資料も内部集約は可能だが、集約自体の代表承認は省略できない。承認は送信や停止解除ではなく、最終状態も手動実行待ち。外部操作・課金・本番接続の実装はない。

優先度は1が最高、5が最低、既定3。queueはレビュー済み依存関係のみを満たしたpendingタスクを優先度と依頼日時順に返す。自動ワーカーは起動しない。変更は新しい依頼キーで作成し、既存の承認を流用しない。

## 操作例（匿名・架空データのみ）

```sh
cd /workspace/violet-ai-office
python3 -m violet_office init
python3 -m violet_office plan --instruction 'SNS投稿文とLINEの7日間配信を準備'
python3 -m violet_office submit --key demo-v1 --instruction '市場調査と競合調査' --priority 2
python3 -m violet_office queue
python3 -m violet_office request --request-id REQUEST_ID
python3 -m violet_office result --task-id TASK_ID --content-file /tmp/anonymized-result.txt
python3 -m violet_office review --artifact-id ARTIFACT_ID --reviewer AI28 --verdict pass
# 全専門担当のレビュー通過後
python3 -m violet_office collect --request-id REQUEST_ID
# 集約成果物も別途AI28レビュー。対外用途・重要判断は代表のローカル操作で決定
python3 -m violet_office decide --artifact-id AGGREGATE_ARTIFACT_ID --decision approve
python3 -m unittest discover -v
```

REQUEST_ID等は前のコマンドが返す実IDに置き換える。reviewはレビュー済み事実を信頼されたローカル利用者が登録する操作であり、AI28が自動監査したという意味ではない。decideは代表本人がローカル操作する前提。本人認証未実装のため公開不可。

requestは成果物本文も返す。機密情報・個人情報は入力しない。本文の自動匿名化は未実装。キーをプロンプトやログに含めない。エラー時のCLIは本文を表示せず終了コード2。DBは従来の平文ローカル管理。

## 検証・残課題

変更前13件成功。変更後30件（既存13件＋新規17件）成功。新規CLIテストは一時DBに依頼→割当→成果物登録→レビュー→集約→集約レビュー→完了を実行。依頼競合、ロールバック、優先度、重要判断承認、期限・予算の既存回帰も確認。

現在のAPI認証情報は不要。未実装: LLM呼出、生成原稿、バックオフ付き自動ワーカー、外部呼出の実タイムアウト、管理画面、本人認証、法令の実査。これらを実施済みと扱わない。次段階はルールの業務確認と、少数担当からの生成実行方式・予算の選定。28人同時稼働は行わない。
