# 第16段階：事業別経営管理・AI社員連携

## 実装とデータの基準

既存の `/businesses`（全事業）と `/business?business=ID`（選択事業）に、経営集計欄を追加した。第15段階の事業追加・割当基盤を再利用し、新しい事業・顧客・売上は登録しない。初期費用や目標も自動設定しない。

期間はJSTの開始日以上・終了日未満。入金は現金収支であり売上に加算しない。利益は採用売上−実際の経費記帳額。税、未記帳経費、銀行照合等を網羅する会計・税務申告システムではない。

| 項目 | 定義・制限 |
|---|---|
| 売上 | 代表が選ぶ旧台帳のactual記帳額、またはSCMのmanual税抜請求−期間内取消・貸方。初期は基準未確認・未計測 |
| 経費 | 既存旧台帳actual経費。試算・モックを含めない。未記帳は未計測 |
| 利益 | 選択売上−経費。どちらか未計測なら未計測 |
| 入金純額 | SCM manual入金−返金。期間内分のみ |
| 未入金残高 | 終了日時より前の全請求・貸方・入金・返金を使用する期末残高。翌日以降の入金で過去残高を変更しない。負数は返金待ち等の確認対象 |
| 顧客数 | 期末時点の事業別登録ID数。削除済みは除外。氏名一致で統合しない |
| 新規顧客数 | 期間内の事業別登録ID作成数。後日の匿名化で過去の獲得件数を消さない |
| 契約 | SCMの代表確認manualイベント。期間内成約、取消、期末有効の期間内成約を区別。旧台帳契約記録は取消との照合が不明のため別掲 |
| 面談 | SCMの面談予約確定イベントと、第14段階の明示的な面談完了記録を別集計。サービス予約や参加だけを面談完了と推測しない |

旧台帳とSCMには重複取引や税区分の差があり得るため、両売上を足さない。代表のみが根拠と版番号付きで採用基準を変更する。全事業売上・利益は全事業が同じ基準かつ必要な記録が揃う場合だけ合計する。混在・未確認なら未計測とし、基準別に別掲する。全事業顧客数は事業別登録IDの延べ数で、実人数ではない。全事業一覧には顧客氏名・連絡先を返さない。

## KPI

標準KPIは既存の実測日次記録と完全な月に一致する既存月間目標を使用。未記録日を0と推定しない。代表作業時間の既存目標は上限/下限の方向が未定義なので達成判定しない。

第15段階の追加KPI定義について、代表がsum（日次合計）またはlast（期末観測）を指定し、期間・比較方向・目標・根拠を設定する。sumは全日、lastは終了日前日の実測が必要。actualだけを使用し、推定・モックは除外。未設定/欠測は未計測、定義や集計規則の版変更後は要再確認。未完備の日も「記録済み」を別表示する。比率を無条件に合計したり、異なる指標・期間の達成率を平均したりしない。

## AI社員・権限・監査

AI01は既存の統括役を保持。AI02〜AI28の役職・定義は変更せず、既存の多対多 `bf_ai_assignments` を使用。事業役割・スコープ・有効状態・権限を読み取り専用で表示する。自動担当追加や完成版の割当操作画面は作らない。

- `GET /business-operations-data?business=ID&start=YYYY-MM-DD&end=YYYY-MM-DD`：認証済み利用者の事業権限をDBで検証。business省略時は代表のみ全事業集計。
- `GET /business-task-kpis?task=ID&start=...&end=...`：担当AIと事業はサーバーの既存タスク関連付けから取得。agent/businessの追加指定は拒否。利用者の事業権限と、AIの現行割当・finance_read・work・事業/タスク停止状態を併せて検証。
- 内部 `business_foundation.read_for_ai(...,'kpi_read',...)` は既存finance_readを再利用。AI権限を拡張・自動付与しない。
- 代表限定の設定POST `ops_finance` / `ops_rule` / `ops_target` は既存の認証、CSRF、nonce、版番号チェックを使用。

許可・拒否をops_auditに記録し、設定変更の前後は既存bf_eventsにも保存。拒否されたID・タスクはハッシュ、未知利用者はunknownとし、生の不正入力や連絡先は監査に入れない。POSTがロールバックされても拒否監査を残す。SQLは事業条件と複合FKで分離するが、SQLiteファイル自体へのOSアクセスに行単位制限はない。OS権限・暗号化・端末保護は引き続き必要。

## 操作・起動

既存手順で `python3 -m violet_office.web serve --open-browser` を起動し、代表でログイン。`http://127.0.0.1:8765/businesses` から事業を開き、開始日・終了日を変更する。代表のみ集計基準・追加KPI規則と目標を変更できる。未計測は架空値で埋めない。

PCと390px幅Chromiumで検証済み。iPhone実機・LAN公開は未実施。iPhone検証をする場合は承認された保護ネットワークとHTTPS等を用意する。確認目的で本版のloopback制限を解除しない。

## 移行・バックアップ・復旧

追加版16は `ops_finance_policy`、`ops_kpi_rules`、`ops_kpi_targets`、`ops_audit` の4表のみを追加。既存テーブル・取引・顧客・設定は変更せず、同版の再適用も安全。Office起動で適用するため、既存実データに新コードを向ける前に代表承認とバックアップが必要。本開発では一時DBだけに適用した。

1. [既存バックアップ手順](runbook.md)で元DBを保全する。
2. `python3 -m violet_office.ops restore --db BACKUP --output NEW_COPY` で別ファイルへ復元し、healthで検証。
3. 新コードを復元コピーだけで起動し、版16・元データ・期間集計を確認。
4. 復元は既存仕様どおり割当・予定・タスクを停止、成果物を再承認対象とする。集計基準とKPI設定は保持されるが、自動実行を再開しない。
5. ロールバックは旧コードと移行前バックアップの新規コピーで行う。新DBから表を削除する破壊的ダウングレードはしない。検証済みバックアップ以降の操作は監査記録から人間が照合する。

## 検証・制限

変更前466件、変更後494件（追加28件）、失敗・エラー・スキップ0。機械可読証跡は [全自動テスト](../reports/phase16-validation.json)、[第16段階ブラウザ](../reports/phase16-browser.json)、同ディレクトリのphase16-regression-*。版15以前の行保持・再適用・別ファイル復旧、返金/取消/未入金、欠測、権限違反、タスクID偽装拒否を検証した。

ブラウザは合成一時DBのみ。既存各段階画面と第16段階画面をPC/390px幅で確認。実LINEシステムは別リポジトリのため本体回帰は未実施。本リポジトリのLINEモック・同意/停止・再同意・予約停止の既存テストは合格。実LLM、LINE、予約、決済、銀行/会計サービスは未接続・実動作未検証。最新CVE照合と法令/公式規約の実査、iPhone実機、実顧客利用承認は残課題。

第17段階へは代表の差分レビュー・統合判断と正式な開発要求の確認後に進める。本番移行の判断とは別であり、本版は自動本番開始しない。

## 変更ファイル

- `README.md`
- `docs/VIOLET_AI_OFFICE_ROADMAP_PHASE10-20.md`
- `docs/architecture.md`
- `docs/phase-16-requirements.md`
- `docs/phase-16.md`
- `docs/runbook.md`
- `examples/phase-16/desktop.png`
- `examples/phase-16/global-desktop.png`
- `examples/phase-16/mobile.png`
- `migrations/016_business_operations.sql`
- `reports/phase16-browser.json`
- `reports/phase16-regression-phase10.json`
- `reports/phase16-regression-phase11.json`
- `reports/phase16-regression-phase12.json`
- `reports/phase16-regression-phase13.json`
- `reports/phase16-regression-phase14.json`
- `reports/phase16-regression-phase15.json`
- `reports/phase16-regression-phase9.json`
- `reports/phase16-regression-smoke.json`
- `reports/phase16-validation.json`
- `tests/browser_phase16.py`
- `tests/test_business_operations.py`
- `violet_office/business_foundation.py`
- `violet_office/business_operations.py`
- `violet_office/business_operations_views.py`
- `violet_office/office.py`
- `violet_office/portfolio_views.py`
- `violet_office/validation.py`
- `violet_office/web.py`
- `violet_office/web_assets/office.css`
