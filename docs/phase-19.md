# 第19段階：事業・担当設定の完成準備

## 実装と範囲

第15段階の事業作成・更新・既存社員割当を再利用し、登録/編集/有効化/停止、個別担当変更/論理解除、最大28人の一括担当変更を、検証→保存前確認→代表承認→別操作で適用するサービス/APIとして追加した。第20段階の完成ボタン・入力ウィザード・28人割当エディターは追加していない。

既存ローカル起動 `python3 -m violet_office.web serve --open-browser` 後、`http://127.0.0.1:8765/business-readiness` を代表で開くと、既存事業、28人の担当確認、準備済み変更案の前後差分、承認・適用履歴を確認できる。事業担当者は権限のある事業だけを閲覧でき、管理変更はできない。変更案の入力はAPI/サービス層で行い、完成版の入力操作は第20段階に残した。

[第20段階の画面仕様・API契約・受入条件](phase-20-screen-contract.md) に、代表が事業を追加し既存28人を割り当てる操作手順と残作業を記録した。

## 初期設定と重複防止

- 事業IDは既存の自動一意ID。新規は準備中、AIの明示割当制限を有効化し、顧客・担当・売上・経費・目標・価格・APIキーを捏造しない。
- 事業名はNFKC、大小文字、空白の正規化で新規重複を検出。既存事業を自動統合・改名しない。既存の重複名を持つ事業の非名称設定を勝手に修正しない。
- 準備キー・コマンドnonce・適用状態により、二重送信・並行適用で事業を重複作成しない。適用済み変更案の再送は同じ結果を返す。
- 新規の登録案を同時に複数作った場合も、登録一覧が変わったら古い案を再確認させる。
- 旧事業で基盤メタデータが未登録の場合は、代表がカテゴリ等を明示確認して更新した後に担当を設定する。推測カテゴリで自動採用しない。

## 担当・権限・互換性

既存28人のID・役職・レビュー担当・設定を変更せず、bf_ai_assignmentsを使う。同じAIが複数事業を担当でき、事業別の役割補足・範囲・限定権限を保存する。自由記述の役割補足は指示情報であり、任意の権限を与えるコードではない。

権限は既存business_read/work/finance_readのみ。work/finance_readにはbusiness_readが必要。解除はactive=falseで履歴を保持する。変更する事業/社員以外の割当は変更しない。

旧事業のai_enforced=0という互換運用は全体として保持する一方、今回明示承認して担当変更/解除した事業×社員にはbr_assignment_controlsを記録し、その組だけ現行権限を厳密に検証する。これにより、旧互換モードでも解除した担当が旧タスクを実行できない。他の社員や兼務先の事業へ制限・権限を勝手に広げない。

担当不在や権限失効時は第18段階の確認待ち/拒否へつながる。同一事業の引き継ぎは既存の履歴・権限・同意・回数上限を再利用し、自動引き継ぎや権限復旧を行わない。進行中の仕事がある場合、解除前後の対応は管理者が確認する必要がある。

一括変更は選択した1〜28人のみ対象。社員ID重複・未知の権限を拒否。全件を1つの確認・承認として扱い、途中エラーなら割当と制限記録を全件ロールバックする。未指定の社員を自動解除しない。

## データ分離・承認・監査

顧客・問い合わせ・予約/面談・同意・収支/KPI・セミナーは既存の事業ID付きデータを使用し、新しい顧客/会計台帳を作らない。新規事業へ他事業の顧客情報をコピーしない。事業間共有には既存の両事業権限・本人確認・目的別同意・代表操作が必要なまま。

変更前設定の指紋とプレビューを保存し、承認/適用直前に現在設定を再検証する。並行更新や旧APIでの明示変更も古い承認の再利用を止める。staleは画面/APIで要再確認とする。代表以外は変更案作成・承認・適用を拒否する。

全体の変更案・管理情報は代表のみ閲覧可能。事業担当者にはその事業の操作仕様・担当設定・変更案・履歴だけ返し、顧客名・連絡先・問い合わせ本文・他事業の収支は返さない。全事業の実績集計は既存の代表専用第16段階を維持する。

入力型・上限・未知フィールド・限定権限・秘密値を検証し、画面はエスケープ。APIは既存ログイン/現行DBロール/事業ACL/Origin/CSRF/nonceを使用。操作履歴はbr_eventsと既存監査、拒否はops_auditに記録し、秘密値・顧客本文をログに出さない。自動個人情報検出の完全性を保証するものではなく、説明欄には必要最小限の業務情報だけ入力する。

事業を停止してもデータを削除せず、新しい仕事と進行中タスクの完了を権限でブロックする。再開でも自動実行を開始しない。事業名・内容・カテゴリ変更は当該事業の第17段階紹介承認を確認待ちへ戻し、資格・法的分類・報酬を推測で更新しない。

## DB・移行・復旧

追加版19はbr_requests（変更案/承認/結果）、br_assignment_controls（明示変更した担当の現行権限制御）、br_events（事業別の操作履歴）の3表のみ。移行時に既存行・設定を変更せず、再実行可能。設定を変更するのは代表が案を承認した後の明示applyだけ。

実DBは本作業では使用していない。Office起動で版19を適用するため、既存実データへの適用前に代表確認と [既存バックアップ手順](runbook.md) が必要。

1. 旧コードと元DB、移行前バックアップを保全する。
2. `python3 -m violet_office.ops restore --db BACKUP --output NEW_COPY` で新しいコピーへ復元しhealthを確認。
3. コピーだけで版19、旧行保持、担当制限、登録/停止/承認と既存機能を検証。
4. 復元後は未適用承認を再承認待ちへ戻す。担当制限と履歴は保持、従来の割当/自動処理/紹介再確認も維持。復元による版変更でstaleになった案は作り直す。
5. ロールバックは旧コードと移行前バックアップの新規コピーで行う。新DBから表を削除して旧コードへ戻す破壊的手順は使用しない。新しい担当制限を失わないよう切替後差分は人間が照合する。

## 検証結果

変更前556件、変更後589件合格、失敗/エラー/スキップ0（追加33件）。[自動テスト証跡](../reports/phase19-validation.json)。登録/編集/停止/再開、28役保持、兼務/解除、旧互換タスクの権限失効、保存前確認、並行適用、重複名、一括ロールバック、同意/承認/監査、移行/復旧、第18段階の確認待ち/引き継ぎとの連携を確認した。

[実Chromiumの画面・操作検証](../reports/phase19-browser.json) は合成一時DBのみ。PC幅1440px・390px幅で事前確認API、変更案の承認→別適用、一覧反映、停止/再開、同一社員の兼務と事業別解除、事業担当者の閲覧/変更拒否を確認。既存画面回帰と合わせ82画面・経路を各幅で検証（phase19-regression-*）。

本リポジトリのLINEモック、停止・再同意・予約停止等の既存回帰は合格。別リポジトリのLINE本体、実LLM/LINE/メール/予約/決済、iPhone実機、最新CVE照合・公式原文の実査は未実施。未確認事項を合格・接続済みとしていない。

## 未実装と第20段階への引き継ぎ

新規事業追加ボタンからの完成版入力ウィザード、既存28人の事業別割当エディター、モバイルでの最終入力操作検証は第20段階に残す。基盤API・サービスと保存前確認/承認画面は本版で用意した。自律LLM・外部実送信・実予約・決済・公開は未接続/未実装で、自動実行しない。

PCは上記loopback起動で確認できる。390px幅はブラウザ開発者ツールでも確認可能。iPhone実機接続のために無認証公開やloopback制限解除を行わない。

代表は差分・テスト・[第20段階の画面契約](phase-20-screen-contract.md)を確認し、必要に応じbase main / compare phase-19-business-readinessで手動PRを作成する。mainへの統合や本番移行は別の代表判断。本作業ではPR作成・main統合・本番公開・第20段階の実装開始はしていない。

## 変更ファイル

- `README.md`
- `docs/VIOLET_AI_OFFICE_ROADMAP_PHASE10-20.md`
- `docs/architecture.md`
- `docs/phase-19-requirements.md`
- `docs/phase-19.md`
- `docs/phase-20-screen-contract.md`
- `docs/runbook.md`
- `examples/phase-19/desktop.png`
- `examples/phase-19/mobile.png`
- `migrations/019_business_readiness.sql`
- `reports/phase19-browser.json`
- `reports/phase19-regression-phase10.json`
- `reports/phase19-regression-phase11.json`
- `reports/phase19-regression-phase12.json`
- `reports/phase19-regression-phase13.json`
- `reports/phase19-regression-phase14.json`
- `reports/phase19-regression-phase15.json`
- `reports/phase19-regression-phase16.json`
- `reports/phase19-regression-phase17.json`
- `reports/phase19-regression-phase18.json`
- `reports/phase19-regression-phase9.json`
- `reports/phase19-regression-smoke.json`
- `reports/phase19-validation.json`
- `tests/browser_phase19.py`
- `tests/test_business_readiness.py`
- `violet_office/business_foundation.py`
- `violet_office/business_readiness.py`
- `violet_office/business_readiness_views.py`
- `violet_office/office.py`
- `violet_office/ops.py`
- `violet_office/validation.py`
- `violet_office/web.py`
- `violet_office/web_views.py`
