# 第15段階：複数事業管理・新規事業拡張基盤

## 完成した範囲と前工程

第14段階コミット `d6efb96` を含む専用ブランチで、既存の事業管理を再利用して基盤を追加しました。開始時main `fcac0f2` には第14段階が未統合で、今回はmainを変更していません。[要求と計画差分](phase-15-requirements.md)を参照してください。

第15段階の範囲は基盤モデル・権限・API・最小設定画面です。完成版の割当コンソール、統合ダッシュボード、AIモデル/外部サービス接続は第20段階等の別要求を待ちます。第13.5段階の計画、既存28役の名前/役職/人数/機能/権限/設定、旧6事業、LINE/予約/セミナー/顧客仕様を自動変更していません。実業務DBは開かず、検証は合成一時DBだけです。

## 既存事業を維持

| 既存ID | 既存名称（改名・統合しない） |
| --- | --- |
| BIZ-A | 健康・美容・ベネシードディーラー関連 |
| BIZ-B | 鏡のウロコ取り出張サービス |
| BIZ-C | ホームページ制作・LINE自動化 |
| BIZ-D | 人生プロデュース・相談サービス |
| BIZ-E | セルフホワイトニング |
| BIZ-F | スマホ教室 |

上記はコードの初期候補定義であり、今回本番DBへ登録したという意味ではありません。他の既存事業もそのまま表示・管理し、A〜Fへ自動統合しません。実際の提供状況・価格・地域・開始日は代表の設定を利用し、未設定値を推測しません。

## 事業情報と安全な登録

新規登録は自動の `BIZ-<UUID>` とリクエストキーで冪等化。同じキー/入力は同じID、同じキー/異なる内容は拒否。名前が一致しても自動統合しません。新規事業は準備中で、提供中への変更には代表の確認が必要です。開始日は空欄可で、作成日を実際の開始日へ自動転記しません。

自由カテゴリは文字列として保存し、将来分類を追加してもコードの業種一覧を書き換える必要がありません。既存の `portfolio_businesses.kind` は互換用の処理種別として維持し、新しい自由カテゴリと混同しません。標準はotherの汎用処理、既知の処理種別をAPIで明示選択することも可能ですが、新業種の専用業務が自動実装されるわけではありません。

概要の正本は既存 `portfolio_businesses.services`。追加プロフィール内の概要欄は保存時スナップショットで、読取は既存の正本を参照します。更新はプロフィール版と事業版を両方確認し、旧画面で変更した概要を古いフォームで上書きしません。既存の名称/料金/地域などは従来の代表設定画面で明示変更できます。

無効化は削除ではありません。顧客/財務/履歴/割当を保存し、通常データの更新、AI実行/成果保存を拒否します。代表は再有効化のための設定にアクセスでき、許可された人間の履歴閲覧は残します。事業休止・終了・横断緊急停止も既存制御を再利用します。

## データモデル・移行

`migrations/015_business_foundation.sql` は追加型・版15。既存表のDROP/再作成/自動配賦や既存顧客コピーはありません。

| 追加テーブル | 内容 |
| --- | --- |
| bf_profiles | 既存事業IDへの概要スナップショット、自由カテゴリ、開始日、enabled、AI制限切替、版 |
| bf_creations | リクエストキー/入力ハッシュと事業IDの冪等対応 |
| bf_ai_assignments | 事業×既存社員IDの複合キー、役割補足、範囲、限定内部権限、有効/停止、版 |
| bf_kpis | 事業別の安定したKPIキー、表示名、単位、有効状態、版 |
| bf_kpi_samples | 事業/KPI/日/実行区分別の明示入力、根拠、訂正版 |
| bf_events | 登録/設定/割当/読取/計測訂正の操作履歴・変更前後 |

顧客・営業・財務は既存portfolio/scm、AI作業は共通tasksとportfolio_tasksを参照します。KPIの複合外部キーで他事業の定義を使用できません。割当の社員IDは既存registryで検証し、28人をDBへ複製・増員しません。

## 共通28人の複数事業割当とアクセス制御

1人を複数事業へ割当可能。role_noteは担当の補足、scope_noteは人が読む範囲説明で、既存役職や機能の変更ではありません。機械的な許可は下記の限定権限と事業IDで判定します。

| 権限 | 許可する範囲 | 許可しない範囲 |
| --- | --- | --- |
| business_read | 割当先だけの事業概要/カテゴリ/状態/開始日 | 他事業、顧客一覧、個人情報、秘密 |
| work | 割当先で既存専門分野に沿う内部タスクと成果物、独立レビュー | 外部送信、契約、課金、本番設定、代表承認 |
| finance_read | 割当先の既存Sales集計（個人明細を含まない） | 他事業の収支、財務の自動作成、実決済 |

work/finance_readにはbusiness_readも必要。知らない権限、外部送信/承認/spend/production_access等は付与できません。すべての社員の元の権限制限・enabled=falseを維持します。

新しい基盤経由の事業はAI制限を初期有効とし、未割当のAIは実行不可。専門分野の候補から許可済み社員だけを選び、Sales/Workflow/事業開発/第14段階もゲートを再利用します。共通Store.claim、finish、reviewで再検証し、途中の担当取消・無効化・休止/緊急停止後の保存も拒否。作者だけでなく独立レビュアーも割当が必要です。

AIへの読取ゲートは `BusinessFoundation.read_for_ai`。代表が要求しても、指定AIの割当外事業は返しません。顧客情報をそのまま渡すAPIを追加していません。個人本文が必要な従来の内部作業は、その事業の人間ACLと社員のwork制限、従来の必要最小限処理を維持します。LLM/外部ツールは未接続です。

これはアプリケーション内の制御で、SQLiteの物理RLSや任意Pythonコードのサンドボックスではありません。将来AIモデルへ任意SQL/DBオブジェクトを渡さず、認証済みゲートを使う必要があります。Principalの文字列を受け取るPython内部API自体はリモート認証方式ではなく、Web入口では既存セッションから生成します。

## 旧事業の互換性と明示切替

マイグレーションは旧事業プロフィール/AI割当を自動作成しません。既存の人間が開始する固定テンプレート業務を突然停止させず、旧仕様を維持します。

旧事業を基盤へ明示採用してもAI制限はまだ未切替。代表が必要な作者/レビュアーを割当し、`enforce` で明示有効化します。未完了タスクの担当/レビュアーが未割当なら切替を拒否し、影響を確認する機会を残します。有効化を自動的に解除するAPIは用意せず、範囲別の割当停止や事業無効化で管理します。

新しいAI読取ゲートは、互換運用の旧事業であっても明示割当なしには使用不可。旧人間向け固定処理を互換運用できることを、AIへの全事業権限付与と扱いません。人間の代表/事業担当者ACLは継続します。

既存の手動ID付き `portfolio_create` は互換API/画面として保持し、新しい制限へ自動採用しません。新規事業は「新規事業を追加・基盤設定」の導線を推奨し、旧入口で作った事業は「旧仕様を維持・未採用」と明示します。全旧事業が自動で厳格化済みとは報告しません。

## 全事業管理者と人間/AI権限の区別

| 主体 | 範囲 |
| --- | --- |
| 代表 representative | 既存の全事業管理者。事業作成/設定/割当/切替/KPI定義、既存の承認。外部実行は別途承認/接続が必要 |
| business_operator | 既存portfolio_aclで許可された事業だけ。read-onlyは閲覧、can_writeなら許可範囲の人間業務/計測。全事業の登録/権限変更不可 |
| AI社員 | 新規のログインアカウントではない。元の役職/外部禁止を維持し、事業ごとの内部ゲートで限定 |
| participant / viewer | 基盤管理権限を付与しない。既存の本人用画面等を維持 |

AI01も自動的な全事業アクセスを得ません。全事業統括という役職とデータ権限を区別し、必要な各事業へ明示割当します。代表による事業間顧客の本人確認/目的別同意/関連付け・解除は第12段階を再利用し、今回自動統合しません。

## 顧客・財務・利益・KPI

顧客/契約/売上/経費は既存の事業ID付き複合キーとACLを維持。新しい顧客/財務台帳を二重に作っていません。`summary` は同じ開始日以上/終了日未満で既存PortfolioとSalesの集計を**別掲**します。旧台帳の売上−経費と、税抜請求/入金/返金の定義を混ぜて自動合算しません。実銀行照合は未接続で、元の「人間確認記録」等の定義を保ちます。未計測は未計測、明示測定した0は0です。

標準KPI（問い合わせ・予約・契約・代表時間等）は既存機能を維持。追加KPIは事業別にキー/ラベル/単位を定義し、actual/estimate/mockを別に保存。値は有限・非負・上限/精度付き、actualの未来日は拒否します。負の利益等は既存の派生利益で扱い、新しい追加KPI入力は非負だけの現仕様です。未入力/未定義を0にしません。

訂正には最新の版と根拠を必要とし、変更前をbf_eventsに保全。計測後に単位は変更できず、新しいキーを使います。新しいKPIの目標・グラフ・高度集計・完成操作画面は第20段階等へ残します。仮数値はモックとして明示し、実売上に転記しません。

## 基盤APIと最小画面

GET `/business-foundation` は許可事業の確認と代表の「新規事業を追加」ボタン。`?business=<ID>` で概要/カテゴリ/開始日/状態/無効化、割当の読取一覧、追加KPI定義一覧を表示します。既存 `/business` / `/sales` へ顧客・収支・人間ACLのリンクを出します。

完成版の割当操作コンソールは作っていません。割当/切替/KPIは認証付き基盤POSTと内部サービスで検証可能です。Webは既存セッション、Host/Origin、CSRF、nonce、CSP、エスケープ、要求制限を継承。外部公開RESTやモデルAPIは追加していません。

| POST /actions/ | 主な項目（csrf/nonceは共通必須） | 権限 |
| --- | --- | --- |
| bf_create | key/name/overview/category/started_on（任意）/kind（既知互換種別、既定other） | 代表 |
| bf_adopt | business/category/started_on | 代表 |
| bf_update | business/overview/category/started_on/enabled/status/confirmed/revision/business_revision | 代表 |
| bf_assign | business/agent/role_note/scope_note/permissions（JSON配列）/active/revision | 代表 |
| bf_enforce | business/enabled=1/checked=1 | 代表 |
| bf_kpi | business/metric_key/label/unit/active/revision | 代表 |
| bf_measure | business/metric_key/day/value/mode/evidence/revision | 許可事業の書込担当または代表 |

許可例は `permissions=["business_read","work"]`。AI17への作業割当とAI28への独立レビュー割当は別レコードです。初回版は0、更新は現在の版を指定。boolは0/1のみ。秘密値や顧客会話を役割補足/根拠欄に入れません。割当取消はactive=0で履歴を保持し、行を削除しません。自動一括割当やAI増員はありません。

## 安全な移行・復旧

実データへの移行は今回実行していません。

1. 代表承認後、既存サーバーを停止しops backupで新規スナップショット。WALを落とす単純コピーをしない。
2. ops restoreで**別の新規検証ファイル**へ復元、新コードのOffice初期化で版15をトランザクション適用。既存第14段階の全表/行を保つ追加型で、再適用も冪等です。
3. health/外部キー、事業/顧客/財務/セミナー件数、旧画面を確認。旧事業へ割当・分類・開始日を自動投入しません。
4. 復元コピーでは全AI割当をactive=0・版更新にし、既存の事業緊急停止/予定・タスク保留/再承認も維持。制限有効事業は代表の再割当までAI不可。旧互換事業も従来の復旧停止を代表確認なしに解除しません。
5. ロールバックは旧コードと**移行前のDBコピー**を別パスで確認。旧コードを新しい割当ポリシー付きDBへ向けない（旧コードは新制限を理解しない）。新DBは保全し、差分照合後に代表が切替判断。DROP/破壊的逆移行をしません。

具体的なbackup/restore/healthコマンドは [運用手順](runbook.md)。暗号化・保持/削除・TLS/MFA・最新脆弱性照合は従来同様の本番残課題であり、物理データ分離や外部サービスの安全性を保証するものではありません。

## 実行と確認

```sh
python3 -m violet_office.business_foundation_demo
python3 -m unittest discover -v
python3 -m tests.browser_phase15 --output-dir /tmp/violet-business-foundation-new
```

デモは一時DBだけで旧6事業を保ち、2つの合成事業、同じAI17の複数割当、未割当拒否、無効化、モックKPIを確認。実顧客/売上/課金は発生しません。通常のPC内起動は既存手順を使い `/business-foundation` を開きます。既存DBは先にバックアップ/復元コピーで確認してください。

[PC画面](../examples/phase-15/desktop.png) / [390px画面](../examples/phase-15/mobile.png)。実iPhoneは未検証、ループバック限定サーバーを無断LAN公開しません。新しいAPIキーは不要です。

## 検証結果・未実施

[全テスト](../reports/phase15-validation.json)：変更前440件、変更後466件すべて合格（新規26件、失敗/エラー/スキップ0）。[新画面/API](../reports/phase15-browser.json)、[一時DBサンプル](../reports/phase15-sample.json)と既存画面の回帰結果を保存。

- 検証：登録/更新/無効化、同一キー/同時登録、28役維持・複数割当、事業外拒否、途中取消/独立レビュー制限、人間ACL、顧客/収支/KPI分離、旧画面との版競合、既存タスクの明示切替、セミナー/事業開発の制限、移行/復元、CSRF/XSS。
- Chromium 1440px/390pxで基盤7経路、既存社長室11・セミナー5・事業管理5・事業開発5・営業7・業務フロー7・セミナー運営7経路を回帰確認。すべて合成DB、外部ブラウザ通信遮断。
- 未実施：別リポジトリの実LINE本体、実モデル/実API/決済/銀行照合、実iPhone/Windows/macOS、最新CVE/法令/公式規約の実査。
- 未実装：第20段階の完成版事業/割当コンソール、カスタムKPIの完成画面/グラフ、業種別の新しい専用業務、物理テナントDB分離、実外部連携。旧事業の一括移行・強制ポリシー変更もしません。

## GitHub・第16段階へ進む条件

第14段階がmainに未統合のため、まず第14段階との差分だけでレビューできる積み上げPRを想定。代表が第14段階をmainへ統合後、差分を確認して第15段階のベースをmainへ変更します。mainへの自動マージ・本番公開はしません。

ローカルの事業拡張基盤としては第16段階へ引継ぎ可能ですが、前工程と本PRの代表レビュー/統合判断、次段階の正式要求確認が必要です。従来第15段階「CREATOR STUDIO」や第13.5段階の商品計画を今回実装したことにはなりません。第16段階の詳細を推測して開発せず、事業ID・割当/内部権限・KPI区分・既存収支の定義・復元時保留を引継ぎます。本番運用は不可です。

## 変更ファイル

- `README.md`
- `docs/VIOLET_AI_OFFICE_ROADMAP_PHASE10-20.md`
- `docs/architecture.md`
- `docs/phase-15-requirements.md`
- `docs/phase-15.md`
- `docs/runbook.md`
- `examples/phase-15/desktop.png`
- `examples/phase-15/mobile.png`
- `migrations/015_business_foundation.sql`
- `reports/phase15-browser.json`
- `reports/phase15-regression-development.json`
- `reports/phase15-regression-office.json`
- `reports/phase15-regression-portfolio.json`
- `reports/phase15-regression-sales.json`
- `reports/phase15-regression-seminar.json`
- `reports/phase15-regression-seminar_support.json`
- `reports/phase15-regression-workflow.json`
- `reports/phase15-sample.json`
- `reports/phase15-validation.json`
- `tests/browser_phase15.py`
- `tests/test_business_foundation.py`
- `violet_office/business_foundation.py`
- `violet_office/business_foundation_demo.py`
- `violet_office/business_foundation_views.py`
- `violet_office/development.py`
- `violet_office/office.py`
- `violet_office/ops.py`
- `violet_office/portfolio.py`
- `violet_office/portfolio_views.py`
- `violet_office/sales.py`
- `violet_office/seminar_support.py`
- `violet_office/store.py`
- `violet_office/validation.py`
- `violet_office/web.py`
- `violet_office/web_views.py`
- `violet_office/workflow.py`
