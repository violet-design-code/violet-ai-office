# D方式・独立安全台帳の隔離試作

2026-10-29（日本時間）。ブランチ `feat/accounting-recovery-gate-20261029`、基点 `8fb269411fbafcdbe831cd455bb78f4c934f60fc`。

本書は合成環境向け試作の説明です。本番採用、外部契約、本体接続、schema適用、DB復旧の許可ではありません。前段階12ファイルと既存アプリを維持します。

## 1. 今回のファイルと起動範囲

- `violet_office/synthetic_recovery.py`：合成realm、別々の台帳、模擬外部照合、制御操作、出力broker。
- `tests/test_synthetic_recovery.py`：一時DB/合成データ/ローカルsocketpairによる検証。
- `docs/accounting-d-isolated-prototype.md`：本書。

既存のOffice/Store/web/AccountingAccess/LegacyAccountingからimport・呼出ししません。CLI、ネットワーク接続、既存DB入力、実DB適用機能はありません。

SyntheticRealmを明示作成すると、OSの一時ディレクトリ直下に新しい `violet-d-synthetic-*` を生成し、専用marker、`local/synthetic-ledger.sqlite3`、`mock-external/mock-state.sqlite3`を作ります。既存ファイルは上書きせず、通常操作ではmode=rwで開くため、失われた台帳を暗黙に作り直しません。realm外やsymlinkを使う通常の経路を拒否します。任意の本番パスを引数として受けるコマンドはありません。

Contextは子プロセスへの接続情報です。鍵/代表tokenもテスト用のcapabilityで、OSの権限境界ではありません。試作を本番で使ってはいけません。realm.closeはその新規一時領域だけを後片付けします。実DB、既存backup、修復試験DBは扱いません。

## 2. 二つの永続状態

|状態|保存先/内容|
|---|---|
|ローカル台帳|journalに制御イベントの連番、直前hash、内容、HMAC。authority_checkpointに最後に確認した外部履歴の位置/hashとHMAC|
|模擬外部状態|独立したSQLite connection/transactionで、状態snapshotの連番・hash鎖・HMACを記録。登録、epoch、mode、policy/承認/失効、出力予約、参照ID、ローカルとの確認済みheadを保存|
|保存障害後の停止|制御操作で保存/通信のUnavailableが生じたら一時realmにFAULTを永続化。通常の再承認で解除するAPIはない|

業務データや会計金額・本文を台帳/監査に入れません。scopeには事業/社員/task、区分、許可項目、期間、目的IDを持ち、policyには期限と世代を持たせます。prototypeのjournalはsnapshot全体を保持するため、サイズ・検証費用が増える構成です。本番の保持/索引/compactionは未設計です。

更新順序：

1. ローカルBEGIN IMMEDIATEで全制御操作を直列化。
2. ローカル履歴と外部の新規nonce付き認証応答を照合。主head/確認値、instance、世代と、外部履歴が前回位置を祖先として含むことを確認。
3. 模擬外部へ条件付きで次イベントを確定。ここでは確認済みheadを未確定とする。
4. ローカルへ同じ認証イベントを挿入し、外部が新headを確認。ローカルの外部checkpointも更新。
5. ローカルCOMMITまで排他を維持。外部側の確認だけで取得を許さず、必ずコミット済みローカル履歴との再照合を要求。

これは分散transactionではありません。外部確定後のローカル保存失敗/確認失敗/commit不明では、不一致またはFAULTによって拒否します。旧状態へ自動補正しません。ローカルCOMMIT前に外部確認が済んでいても、COMMITが失敗すれば旧ローカルと外部headが一致せず許可されません。

外部の出力予約/終了記録は、承認履歴とは別に更新されます。この更新も外部連番として検証し、ローカルcheckpointで後退/分岐を検出します。承認headだけを照合して、外部の予約消失を見落とすことを避けています。

## 3. 最新照合と方式の限界

毎回ランダムnonceを発行し、instance/主head/外部head/祖先/停止/世代/確認済みheadを含む応答のHMACを検証します。古い正規応答の再送はnonce不一致、古いローカル/外部台帳はheadまたは祖先不一致で拒否します。cacheやメモリの前回許可へ戻しません。

**両方の台帳とローカルcheckpointを同じ過去へ戻すと、検出できません。** 模擬外部サービスは同じPC上のファイルであり、独立した障害/管理領域を実現していません。テストでこの限界を明示的に再現し、過去の許可が再び通ることを確認しています。そのテストの成功は「本番で安全」という意味ではありません。

HMACは共有鍵の認証モデルであり、非対称署名/鍵管理基盤ではありません。Contextは検証鍵と同じ署名可能なmock鍵を保持します。悪意のある同一ユーザーのコードや管理者から守れません。代表tokenは合成本人確認の代役です。古い正規HMACの再利用拒否を検証していますが、Windows認証/DPAPI/TPM/実サービスの署名検証が完成したとは扱いません。

## 4. 初期登録・復旧・再承認

- enrollにはrealmの代表capabilityが必要。最初はclosed。引き継いだ旧policy IDはquarantinedとして登録し、approvedを取り込みません。
- open_after_reviewは明示的な履歴確認を要求。欠落や不一致があれば確認フラグを指定しても照合で拒否します。
- propose→approveは新規ID/current epoch/pending/有効期限を検査。revokedやquarantinedからapproveへ戻せません。重複/未知状態は拒否。
- request_stop→complete_stopで全出力を停止。新世代は停止完了後にnew_recovery_generationで明示発行し、古いpending/approvedを隔離します。
- 新世代でもopenと、新policyの個別propose/approveが必要。古いpolicy/参照IDは自動復活しません。
- revokeで新規取得/予約を即時拒否し、complete_revocationは当該policyを使う予約が終了するまで拒否。別事業のpolicyを自動変更しません。

履歴確認の真偽や代表tokenはテスト用の前提入力です。実際の本人確認・登録registry・DB復旧の検出/manifest照合は未実装です。試作は業務DBを開かないので、業務DBが勝手に置換されたことを自動検知するものではありません。次段階では信頼されたsource contextとDB系統/manifestの結び付けが必要です。

## 5. 出力brokerの保証範囲

DeliveryBrokerは、通常認可callback＋台帳認可→必要項目の取得→返却形/項目/期間/型の検査→通常認可再検査→台帳認可と出力予約確定→書込/flush→予約終了、の順で動作します。生の会計データを戻り値へ返さず、結果は出力IDと状態だけです。

通常認可callbackは必須で、必ずTrueを返した場合だけ進みます。テストでは既存AccountingAccess.authorize/readを合成DB内だけで組み合わせ、担当activeがfalseなら拒否することも確認しました。本番経路を変更したわけではありません。

出力はHttpSink（HTTPヘッダー/本文）、BufferSink（合成artifact/handoff）のみ。64KiBまでの非streaming JSONとし、部分writeを処理してflushまで予約を保持します。型や既知項目を超える本文は拒否します。外部HTTPサーバーを起動する機能はありません。

停止受付はdrainingにし、新規予約を拒否。受付前に予約されたwriterの正常な書込/flushと永続終了記録が確認できた場合だけ、その予約を解除します。書込/flush例外は未解決のまま保持し、新規出力もdrainingで拒否します。安全な中断を証明して予約を解除する機能は未実装です。時間経過、close、オブジェクト破棄、writer終了だけで予約を消しません。

既にOS/受領者へ渡ったbytesは取り消せません。停止完了後のネットワーク到着や画面表示まで禁止できるとはしません。socketpair試験は本番serverの検証ではありません。

callback/sink/利用側は信頼された同期処理という前提です。悪意のあるcallbackや、裏で出力を続けるsink、任意Python/SQL/ファイル操作の迂回をOSレベルで封じる機能はありません。試作クラスを汎用AIへ直接公開してはいけません。

## 6. 専用参照と役職・事業分離

参照IDはpolicy/task/scope/epochのメタデータだけを外部mock内へ記録し、会計値を複製しません。receive_referenceは、sourceとrecipient双方の現在許可・通常認可を検査し、両方のpolicyを出力予約へ関連付けます。取得中にsourceが失効した場合も、最終予約で拒否します。

他事業、別区分、目的の異なる引き継ぎ、項目/期間の拡大を拒否します。AI27 detailとAI28 auditは専用区分だけ、AI04/その他禁止社員は取得前に拒否。役職表は既存ROLES/FIELDSを参照し、定義・担当・既存permissions/activeを変更しません。

専用「報告UI」、承認済み報告の永続成果物storeは未実装です。BufferSinkは合成出力先であり、汎用artifactsへの公開機能ではありません。

## 7. 本番経路への接続予定箇所（今回は未接続）

|既存経路|次段階の接続・検証|
|---|---|
|BusinessFoundation.read_for_ai finance_read/kpi_read→LegacyAccounting|既存task/事業/finance_read認可を維持し、共通brokerへ委譲するadapter|
|BusinessOperations.for_task→/business-task-kpis|OfficeApp.handleのデータ作成だけで終えず、Handler.respondのヘッダー/本文書込・flushまで出力予約を管理|
|AccountingAccess.read（detail/audit）|AI27/AI28の通常認可を維持。任意の呼出し元へ無保護の辞書を返す経路を整理|
|AccountingAccess.issue/receive|旧handle/source/recipient両方と新epochを検査し、最終出力まで予約を保持|
|Store.finish/Coordinator.report/WorkflowCollaboration.handoff|既存の会計取得済みタスクの汎用出力拒否を維持。専用参照/報告へ接続する別実装が必要|
|代表用集計/帳票/検索/添付・履歴|対象一覧を確定し、復旧停止境界に含める。今回保護済みとはしない|

新しい本番schemaやmigrationはありません。acct_task_policies/readsの導入計画、修復試験、復旧準備機能も変更しません。

## 8. 運用上の残件と次工程

本番に不足するもの：独立providerの真正性/最新性/自身の復元手順、代表本人確認、登録registry、Windowsサービス/認証IPC/ACL、非対称検証と鍵rotation、業務DB系統/manifest、全出口の強制、永続監査/未完了処理の調査、停電/容量不足/強制終了/実socket障害のWindows実機検証です。

試作でFAULTやhead不一致になったrealmを自動修復するAPIは作りません。手動で台帳を書き換えて再開する運用も推奨しません。障害証拠の保全・照合と再開手順は別設計が必要です。停止marker自体の保存も失敗した場合、操作は成功扱いせずUnavailableを返します。実運用での確実な隔離は外部基盤とbroker監督が必要です。

次の安全な段階は、今回の差分の独立安全レビュー→模擬provider/本人確認/出力brokerの信頼境界分離→Windows上の故障注入・認証IPC・socket書込競合検証です。実経路接続・外部契約・本番適用・DB修復は別承認とします。

## 9. 再実行

合成環境のリポジトリ作業ルートで実行します。Windowsで今回実行したとは報告しません。

```bash
py -3 -B -m unittest tests.test_synthetic_recovery -v
py -3 -B -m unittest discover -s tests
```

新規39件は初期登録/未登録、台帳巻戻し・両側巻戻しの限界、認証済み応答再送、消失/破損/更新失敗、再承認、28役職、事業/項目/期間分離、source/recipient再検査、通常認可、複数プロセス更新・承認失効競合、出力中停止、socketpairでのHTTP書込、partial write、未解決writerを検証します。

## 10. 隔離試作初版の結果（今回の修正前）

クラウドで新規39件成功（2.878秒）、全785件成功（42.970秒）、失敗0・スキップ0。既存746件相当は削除/変更/追加skipなしで維持しています。新規試験は3プロセスの各3件の提案が一本の履歴に確定すること、2プロセスの承認/失効競合、別プロセスHTTP writerを待つ停止も含みます。

前段階12ファイルのSHA-256一致、既存追跡ファイル/設定の差分なし、新規ファイルの空白チェック成功。本番経路からのimportなし（試作をimportするのは新規テストのみ）、commit/pushなし。Windowsでの今回の39件実行は未確認です。

合成試作の安全レビューへ提出可能ですが、本番の巻き戻し耐性・本人確認・OS隔離を保証する状態ではありません。両側同時rollbackの限界試験は、その残存リスクを再現したものです。


## 11. 出力失敗時の停止保証修正

### 原因と変更

初版はwrite/flushが例外を投げても、finallyでfinish(aborted)を呼んで予約を解除しました。io.BufferedWriterはflush失敗後も未送信データを保持し得るため、「例外が返った」と「後続出力が不可能」を同一視できませんでした。後のflush/closeで、停止完了後にHTTPデータが新規に引き渡される再現がありました。

修正版は以下の通りです。

- 無条件のfinally解除とfinish(aborted)を廃止。正常なsend＋flush＋永続終了記録の成功時だけhanded_offとして解除する。
- 失敗は既存の予約をunresolvedに遷移させ、drainingで新規出力を拒否する。監査には固定アクションdelivery_unresolvedと既存メタデータのみを残し、例外文字列やHTTP本文を保存しない。
- unresolvedの予約に後からhanded_off/abortedを指定しても解除しない。二重解除、未知token、reserved状態での成功解除も拒否する。
- 失敗記録自体が保存不能でも、先に永続化した予約は削除しない。停止完了を拒否し続ける。失敗記録が書けない間の状態表示は以前のphaseのままとなり得るが、完了扱いにはしない。
- KeyboardInterrupt等の終了例外でも予約を保持。再flush・close・GC・プロセス終了/強制終了・時間経過からの自動解除はしない。
- draining中の明示stop要求は繰り返し受け付けられるが、complete_stop/complete_revocationの未解決予約検査は維持する。

### 再flush/closeの保証を取り違えない

失敗したストリームの再flush/close自体をOSレベルで禁止したわけではありません。未送信bytesは後で引き渡され得ます。その間は予約を未解決として維持し、**停止完了を出さない**ことで、完了後の送出という誤った保証を防ぎます。通常のcloseも内部でflushし得るため、中断の証拠にはしません。

試作には、安全なバッファ破棄/transport切断の証拠を認証して失敗予約を解除するAPIを追加していません。失敗realmは停止未完了のままになります。可用性より安全を優先する制約であり、本番運用の回復機構が完成したとの意味ではありません。任意の確認フラグで解除する運用は認めません。

### 回帰試験

新規 `tests/test_synthetic_recovery_output_failures.py` に17件を追加。実際のio.BufferedWriterでflush失敗後の残留データ、再flush、close/close失敗、部分書込、停止中write、別プロセスでの停止・再flush/close・終了/強制終了、未確認abort/abort失敗、二重解除、旧abortedフラグ拒否、停止完了後の新規出力拒否、GC/KeyboardInterrupt、監査内容/監査失敗を検証します。

既存39件のうち1件は「書込失敗後に停止完了できる」という不適切な期待値を、「未解決として停止完了を拒否」へ強化し名称も合わせて変更しました。テストを削除/skipせず件数を維持しています。初版39件だけではこの不具合を検出できなかったため、新しい回帰試験を必須とします。

次の安全レビューでは、成功時だけの解除、全例外経路で予約保持、終了・再送からの迂回解除がないこと、実際のWindows buffered/socket I/Oでの動作を重点確認します。本体接続・外部契約・GitHub保存は今回行いません。

修正後の最終検証（クラウド合成環境）：新規17件成功（1.081秒）、既存試作39件成功（2.769秒）、全802件成功（45.133秒）、失敗0・スキップ0。前段階12ファイルは修正開始時のSHA-256と一致。既存追跡ファイル/設定に差分なし、ステージ済み変更なし、空白チェック成功。Windowsの今回の追加試験は未実行です。

元の再現手順では、最初のflush失敗後に停止完了を拒否し、その後の再flush/close・別プロセス終了後も未解決を保持することを確認しました。停止完了後の新規broker出力拒否は、正常出力を完了して停止した別試験で確認しています。例外時に予約を残すため、停止が永続的に未完了となる制約は残ります。

今回の停止保証修正はGitHub保存前の再レビューへ提出可能です。本番利用の安全保証や、全コードの最終安全承認を意味しません。コミット・プッシュ・本体接続・実DB操作は実施していません。
