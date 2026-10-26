# 第18段階：事業別AI業務・引き継ぎ・重要操作案

## 起動と操作

既存のローカル起動 `python3 -m violet_office.web serve --open-browser` 後、代表で `http://127.0.0.1:8765/ai-workflow` を開く。既存の事業を選択し、既存営業案件を内部業務へ受け付ける。サンプル事業・顧客・売上は本番DBへ自動登録しない。

1. 問い合わせ、相談、面談候補、予約確認案、フォロー案を選び、期限・優先順位を指定する。
2. 当該事業の現行割当・役割・work権限・目的別同意と連絡停止を確認し、対応可能な担当へ自動振り分け。候補不在/同意不足は管理者確認待ちとし、タスクを捏造しない。
3. 「内部テンプレートを実行」で既存ワークフローの固定テンプレートを作る。LLM生成・顧客への連絡ではない。成果物の代表承認・優先度・停止は既存の業務画面を再利用する。
4. 引き継ぎは同一事業内だけで、元/先の現行権限と目的別同意を再検証。旧タスクは停止し、新しい内部タスクと履歴を作る。旧タスクを既存画面で再開しても再実行を拒否する。
5. 成果物がある場合は代表の内部承認後だけ引き継ぎ、依存関係と元タスク参照を保存する。顧客本文を別事業へコピーしない。
6. 内部作業完了の記録には代表承認済み成果物が必要。完了は売上・契約・予約確定・送信の実績ではない。

担当権限・同意が後から失効すると、一覧は管理者確認待ちとなり、実行も拒否する。自動的に権限を戻さない。管理者は既存同意・割当の正当性を確認する必要がある。独立レビュー担当も当該事業の現行work権限が必要。

## データと既存資産

版18は6表、追加表内の複合FKと範囲検証トリガーを追加。既存タスク表の追加索引には依存しない。既存行の変更・削除はしない。

| 追加対象 | 用途 |
|---|---|
| aw_capabilities | 事業×既存AI×業務種別の対応範囲補足・稼働可否・版 |
| aw_jobs | 既存営業案件に関連する内部業務・担当・状態・期限・優先度・再試行上限 |
| aw_tasks | 旧/現タスクを含む事業別業務との関連 |
| aw_handoffs | 引き継ぎ元/先・業務・同一事業・理由・日時・状態・元/先タスク |
| aw_changes | 重要操作の提案内容・承認指紋・状態・代表・日時 |
| aw_events | 本文を複製しない操作履歴・安全な失敗コード |

顧客/同意/売上/予約は既存portfolio/scm、成果物/タスクはwf/tasks、担当権限はbf_ai_assignmentsを使用する。事業IDをすべてのクエリ・複合FKで検証。aw_jobs/aw_tasks/aw_handoffsには他事業の営業案件・内部タスクを結び付けられない。事業名や顧客を自動統合しない。

`config/workflow_routing.json` に種別、目的別同意、既存役割IDの候補を定義。業務種別と担当候補を事業名付きコードに埋め込まない。新しい業務候補設定は既存28役の定義変更・権限付与ではない。将来変更時も存在する役割と上限を検証する。

対応範囲の補足文は人への指示で、自然言語の細かな範囲を自動認可するものではない。機械的な制限は事業ID、業務種別、現行権限、稼働可否、同意、停止で行う。補足未登録時は、設定された役割候補と明示的な既存事業割当を使用。既存割当なしなら自動実行しない。

## 安全な引き継ぎ・再試行

- 受付と引き継ぎは内容付き冪等キーで二重作成を防止。引き継ぎで種別が変わっても元の受付キー再送は同じ業務を返す。
- 作業負荷は当該事業内の未完了業務数で比較。他事業の私的情報を渡さない。
- 同じ担当/種別への循環を拒否し、引き継ぎは最大5回。実行中のタスクや未承認成果物を引き継がない。
- 旧担当タスクは履歴として保持し、現タスク以外のclaim/finish/reviewをサーバー側で拒否。既存の直接担当変更で引き継ぎ履歴を迂回できない。
- 実行失敗はSAVEPOINTで当該試行を戻し、例外文を保存せずlocal_work_failedだけ記録。30秒待機・最大3試行後に失敗停止。タイムアウトの故障注入も同じ動作。
- 成功済み成果物を再生成せず、失敗後の再試行でも成果物を二重作成しない。既存タスクの30秒期限・予算/緊急停止も継承。
- 自動実行デーモン/定期起動は追加していない。自動化するのは受付時の内部担当選択まで。再試行は待機後に明示操作する。

## 承認と実行の分離

外部送信、面談/予約確定、契約/報酬条件公開、事業間共有、料金、重要事業設定、AI権限変更は、重要操作案として承認待ちへ保存できる。代表の明示確認以外では承認できず、設定・割当・紹介条件等が変わると古い承認指紋を使えない。

承認してもapproved_manual_only（実行なし）で止まる。これらの外部操作/設定変更の新しい実行アダプターは未実装であり、承認から本番操作を起動しない。既存代表専用の明示操作は従来どおり保持し、本導線から自動呼出ししない。

唯一のローカル適用はcapability（本導線の業務対応範囲・稼働可否）。代表が提案を承認し、別操作で現在指紋と既存work権限を確認してaw_capabilitiesへ適用する。bf_ai_assignmentsの既存役割/権限を追加・変更しない。設定済み役割候補以外に業務を与えない。

契約/報酬条件公開案は第17段階の紹介情報と原資料が確認済みでなければ承認不可。公式原文の取得不可/確認待ちを解除していない。事業間共有案は両事業の代表権限に加え、既存の本人確認済み関連付けと両側のidentity_link同意を確認する。ただし承認後も情報共有を実行しない。営業送信や予約の実行には従来の個別同意・代表承認・接続検証が引き続き必要。

## 第20段階向けAPI・サービス

認証・現行DBロール・事業ACL・CSRF・Origin・nonceは既存基盤を使用する。利用者/権限をリクエスト本文だけから信頼しない。

`GET /ai-workflow-data?business=ID` は事業を必須とし、業務、28人の担当/対応状態、対応範囲、引き継ぎ、承認案を返す。顧客名/連絡先/問い合わせ本文・他事業レコードを返さない。APIは個別事業の読取権限を検証し、認証必須。未知クエリでのagent指定等も拒否。

POSTは既存のform-urlencoded `/actions/aw_*` で、business・csrf・nonceが必須。代表/事業担当の現在権限を再確認する。

| action | 主な入力 | サービス |
|---|---|---|
| aw_submit | key,deal,kind,due,priority | submit：既存案件受付と担当選択 |
| aw_dispatch | job | dispatch：同意/担当が整った未割当業務を再判定 |
| aw_run | job | run：限度付き内部テンプレート実行 |
| aw_handoff | job,key,target,kind,reason | handoff：同一事業だけの引き継ぎ |
| aw_control | job,control | control：代表のpause/resume/complete |
| aw_propose | key,kind,payload(JSON文字列) | propose：重要操作案の登録 |
| aw_decide | id,decision,checked=1 | decide：代表のapprove/reject |
| aw_apply_capability | id | apply_capability：承認済み内部対応範囲だけ適用 |

サービスのlisting/employees/handoffs/changesは事業権限を検証する。capabilityのpayloadはagent/kind/scope/available、その他はreference/reason（事業間共有のみtarget_businessも必須）。秘密値・顧客本文・権限を直接付与する任意コードを渡さない。参照IDと必要最小限の説明を使用する。

本版の画面は業務・状態・引き継ぎ・承認を扱い、完成版の28人割当操作画面や新規事業追加ボタンを新設しない。

## テストと実行範囲

変更前524件、変更後556件合格、失敗/エラー/スキップ0（追加32件）。[全テスト証跡](../reports/phase18-validation.json)。既存LINEモックの停止/再同意/予約停止、全過去段階の回帰、28役維持、複数事業、同意、担当選択、引き継ぎ、依存関係、並行二重登録、権限失効、承認前適用防止、故障注入・再試行、DB保持/復元を確認。

[ブラウザ証跡](../reports/phase18-browser.json) は合成一時DB・実Chromiumで受付→担当選択→引き継ぎ→固定テンプレート、承認→別操作による内部対応範囲適用、外部操作承認後も実行なし、権限を確認。PC幅1440px/390px幅で7経路を確認。既存社長室・第9〜17段階の回帰と合わせ75画面・経路を各幅で確認（証跡phase18-regression-*）。実際に新導線でテンプレートを実行した役割はAI17/AI20。28人のLLMが本番稼働したという意味ではない。

iPhone実機、別リポジトリのLINE本体、実モデル/LINE/メール/予約/決済API、最新CVE・契約原文の実査は未実施。実サービスは接続済みと表示しない。本開発による有料API利用は0で、実績売上も作成しない。

## 移行・復旧

本開発は一時DBだけで検証。実DBへ新コードを向けるとOffice初期化時に版18が適用されるため、実データ適用前に代表確認と [既存バックアップ/復元手順](runbook.md) が必要。

1. 元DBと旧コードを保全し、バックアップを作成。
2. `python3 -m violet_office.ops restore --db BACKUP --output NEW_COPY` で別ファイルへ復元し、health確認。
3. 新コードをコピーで起動し、版18、旧データ保持、複合FK、担当/同意/承認の整合を確認。再実行しても旧行を変更しない。
4. 復元ではaw_jobsを停止、未実行承認を再承認待ちへ戻す。割当/既存自動処理停止・第17段階紹介再確認も従来どおり。業務と引き継ぎ履歴は保持する。勝手に再開しない。
5. ロールバックは旧コードと移行前バックアップの別コピーを使用。新DBから表を削除するダウングレードはしない。切替後差分は人間が監査照合する。

## 残課題・次の判断

実送信/外部予約/決済、重要設定/権限変更案の実行アダプター、公開の顧客用画面、LLM連携は未実装/未接続。担当不在・失敗上限・却下済み成果物は管理者が原因を確認し、必要なら新しい内部業務を明示登録する。上限の自動解除や自動権限付与は行わない。

代表は差分とテスト報告を確認し、必要ならbase main / compare phase-18-ai-workflowで手動PRを作る。ソース統合と本番移行は別承認。第19段階には着手しない。

## 変更ファイル

- `README.md`
- `config/workflow_routing.json`
- `docs/VIOLET_AI_OFFICE_ROADMAP_PHASE10-20.md`
- `docs/architecture.md`
- `docs/phase-18-requirements.md`
- `docs/phase-18.md`
- `docs/runbook.md`
- `examples/phase-18/desktop.png`
- `examples/phase-18/mobile.png`
- `migrations/018_workflow_collaboration.sql`
- `reports/phase18-browser.json`
- `reports/phase18-regression-phase10.json`
- `reports/phase18-regression-phase11.json`
- `reports/phase18-regression-phase12.json`
- `reports/phase18-regression-phase13.json`
- `reports/phase18-regression-phase14.json`
- `reports/phase18-regression-phase15.json`
- `reports/phase18-regression-phase16.json`
- `reports/phase18-regression-phase17.json`
- `reports/phase18-regression-phase9.json`
- `reports/phase18-regression-smoke.json`
- `reports/phase18-validation.json`
- `tests/browser_phase18.py`
- `tests/test_workflow_collaboration.py`
- `violet_office/business_foundation.py`
- `violet_office/office.py`
- `violet_office/ops.py`
- `violet_office/validation.py`
- `violet_office/web.py`
- `violet_office/web_views.py`
- `violet_office/workflow.py`
- `violet_office/workflow_collaboration.py`
- `violet_office/workflow_collaboration_views.py`
