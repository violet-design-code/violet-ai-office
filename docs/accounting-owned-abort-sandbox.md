# 安全な中断証明・未解決予約解除：隔離実装

2026-10-29（日本時間）。ブランチ `feat/accounting-verified-abort-sandbox-20261029`。
基点 `25a76039b493148615eb77a053d08749725d3875`。前段階 `plan/accounting-preconnection-safety-20261029` の5ファイルをバイト一致で引き継いだ。

**合成環境専用。実際のWindows OS・job・handle列挙、ネットワーク送信やその中断、OSの本人確認を保証しない。** 本番接続、service/ACL/機器鍵設定、実DB/backup/修復試験DB、外部通信、commit/pushを行わない。

## 1. 追加ファイルと既存D方式への接続

- violet_office/synthetic_abort.py
- tests/test_synthetic_abort.py
- docs/accounting-owned-abort-sandbox.md

VerifiedLedger/VerifiedAuthorityが既存のLocalLedger/MockAuthorityを継承し、同じ合成realmのローカル履歴・模擬外部状態・最新確認値を利用する。既存synthetic_recovery.pyと全既存テストは変更していない。Office/Store/web/旧会計経路からimport・起動しない。

新APIを使うには、一時SyntheticRealmとVerifiedLedgerを明示作成し、代表fixtureのcapabilityで監督者を登録する。任意のDB pathを受けるCLI/修復機能はない。既存の一般HttpSink/BufferSinkの失敗予約は、所有workerの証拠がないのでこの機構では解除できない。

## 2. worker・監督・証明発行

1. 役職/項目/事業/現在policyと必須の通常認可callbackを確認し、限定projectionを作る。AI04等はworker生成・取得前に拒否する。
2. multiprocessingのspawnで固定コードworkerを一つ起動する。job ID、worker nonce、監督起動ID、private control keyを生成し、ready応答のPID/nonce/資源一覧を実Process objectと照合する。
3. 現在headを再検査して出力予約を確定し、監督/worker/job/資源inventory/scopeをimmutableなmanaged metadataへ結び付ける。workerには外部/ローカル台帳鍵や証明用keyを引数として渡さない。
4. workerだけが専用の一時ファイルとBufferedWriterを所有し、HTTP形式のbytesを同期書込する。socket/外部通信/任意callback/child processは使わない。成功時はflush・資源close・終了応答・実際のjoin/exitcodeを確認して通常のhandoffとして確定する。
5. 失敗は従来どおり未解決で保持する。中断時は模擬外部から現在epoch/対象に束縛されたchallengeを取得し、新規出力をdrainingで拒否する。
6. workerはraw出力先を先にfenceしてからbuffer cleanupを行う。close自体を証拠にせず、登録資源がclosedであること、raw/bufferの再write・再flushが拒否されること、同期処理が戻ったことを確認し、challenge付き応答を返す。
7. 監督側は認証応答、資源inventory、challengeと実Processの終了/exitcode=0を確認。別の証明発行processへ検証済み観測を渡し、認証付き証明を得る。このprocessの異常終了も故障注入する。証明が取れなければ予約を残す。
8. VerifiedLedgerで証明を検証し、対象予約だけを解除。完了後も停止/取消状態は保持され、証明が会計許可や再開命令になることはない。

制御・応答はサイズ制限付きJSON bytesとper-job認証コードを使う。アプリ制御frameにpickle recvを使用しない。これはWindows named pipe/SSPIの実認証ではなく、作成したprivate multiprocessing channelの模擬認証である。

## 3. 実際に確認することと前提

|項目|今回の確認|保証していない範囲|
|---|---|---|
|worker|実際にspawnしたProcess object、ready応答、nonce、join/exitcode|PIDだけから任意processを識別すること、Windows作成時刻/保護process|
|job|登録された固定worker一つという合成jobの構成と終了|Windows Job Object、全OS child/隠れたproducerの列挙|
|handle|workerが保持する同期一時ファイルとBufferedWriterのclosed状態・固定inventory|全OS handle、複製/継承/悪意ある別process、実socket handle|
|I/O|同期file write/flushが戻り、rawをfenceした後に再write/flushできないこと|kernel/disk/networkの未完了I/O、CancelIoEx、送信queueの撤回|
|残留buffer|終了する固定workerのprivate bufferから将来のwriteができないこと|メモリの物理消去、dump/SSD消去、parent/他processに不正複製された内容|
|監督者|登録ID/起動ID/専用HMAC keyによるfixture認証|Windows account、CNG/TPM、実外部署名基盤、管理者侵害対策|

固定workerは任意コードやchildを起動しないという前提。監督者の確認を外部側で独立したOS証明に変換したとは扱わない。証明のfactsは、上記の限定観測を監督側が認証したものである。

一時出力ファイルに既に書かれたprefix/全文は残り得る。証明/receiptにはprior_output=possible_or_unknownを明記し、「相手に一切届かなかった」「過去のデータが消えた」としない。ファイルを他の同一ユーザーprocessが直接開いて書く攻撃や、任意Pythonコードによる内部状態変更は防げない。OS権限分離は未実装である。

## 4. 証明と解除条件

証明にはinstance、delivery、epoch、監督ID/boot、job、worker nonce/PID、資源inventory digest、scope digest、モデル種別、operation、challenge、revision、発行時のhead、期限、固定facts、観測digest、既送出可能性と認証コードを含む。金額/本文/例外文字列/秘密keyは含めない。

- 模擬外部が作った現在のintentと全て一致しなければ拒否。
- supervisor登録がactiveで、boot/key digestが一致し、HMAC認証が成立する必要がある。
- current epoch、有効期限、完全なfacts、観測digest形式を検査。期限切れは予約を消す理由ではなく、解除拒否の理由である。
- 任意streamの予約、未登録supervisor、他delivery/事業/scope/worker、別challenge/旧revisionへ流用させない。
- 同一epoch・有効期間内の同一証明再送は、同じreceiptを返すだけ。新たな解除・制御履歴を発生させない。異内容の証明は競合として拒否する。
- challenge更新後、証明期限後、監督者無効化後、新復旧世代では古い証明を受け付けない。再取得不能なら停止未完了を維持する。

HMAC keyは監督側でランダム生成し、workerには渡さず、模擬外部側の一時key fileと登録key digestで検証する。key本文は監査journalへ書かない。key fileは同じ合成PC内にあり、同一OS userの悪意ある任意コードから守る機構ではない。実鍵設定は行っていない。

## 5. 永続更新と複数process

中断intentとmanaged reservationは模擬外部の認証履歴に保存し、ローカルの外部観測位置を更新する。解除は新しいD方式の**制御イベント**として記録する。ローカルBEGIN IMMEDIATE→外部の現在head/状態検査・解除→ローカル記録→外部確認→COMMITの既存手順を維持する。

外部確定後のローカル保存失敗/確認不能ではhead不一致またはFAULTで停止する。物理中断と二つのDBの単一transactionを保証するものではない。成功を返せない場合に古い許可へfallbackしない。

2processから同じ証明を提出しても、ローカルの直列化と外部の状態検査で一つの解除receiptだけを確定する。別processの停止完了要求が先ならpending、解除後ならcompleteとなる。複数jobのうち一つだけが解除されても、他の予約が残る間は停止を完了できない。

監督close/worker終了/強制終了・timeoutだけで予約を解除しない。worker応答不明、fence失敗、異常exit、証明発行processのcrash、署名失敗、保存失敗は保持する。プロセス掃除は行っても証明を捏造しない。

## 6. 最新状態・権限の維持

現在head/nonce/連続した外部観測履歴を照合する既存D方式をそのまま使う。ローカル制御履歴の巻き戻し、外部状態の後退/不一致、障害時は拒否。古い観測位置だけが残る場合は、正当な外部履歴の連続性を検証して追従するが、許可は必ず現在の外部状態で判定する。

両台帳/登録/鍵を同じ過去に戻せば検出できないという模擬基盤の限界は解消していない。証明の期限を、この問題を解決する最新性の証拠にしない。

AI04拒否・28人のROLES/FIELDS・通常認可・事業/task/期間/目的/期限の制限を維持する。解除は出力予約のcleanupであり、policyの承認・担当変更・active変更ではない。取消完了後に旧policyで新たな出力を開始できず、復旧後も旧policyは隔離されたままである。

既存の一般artifact/handoff、専用報告UI、実HTTP responseへの中断接続は行っていない。今回の対象は新しい固定workerの合成file出力だけであり、それ以外を保護/解除できるとは報告しない。

## 7. テスト・故障注入

新規37件：正常handoff、中断証明成立、部分write/flush失敗、再flush/close/再送、出力中停止、worker異常exit/応答欠落、fence確認失敗/timeout、監督証明発行process異常終了、監督teardown/別監督、署名/制御認証不正、facts不足、他対象/世代/challenge/期限不一致、無効化、同一証明再送、取消完了待ち、旧policy拒否、28役職/事業分離、台帳後退/保存失敗、監査内容、2process解除と停止競合、2job独立解除、一般streamの解除禁止。

Windows OS・ネットワーク送信の中断を保証するテストではない。実装は次の合成コマンドで確認する。

```bash
py -3 -B -m unittest tests.test_synthetic_abort -v
py -3 -B -m unittest discover -s tests
```

今回はWindowsで実行していない。既存802件と前段階22件を削除/変更/skipせず、全体861件に含める。

## 8. 保存前レビューと残るリスク

GitHub保存前に、証明生成が固定workerの観測に限定されること、監督/鍵/bootの登録、challenge更新/有効期間/同一証明receipt、解除の原子性と保存失敗、制御frameのサイズ/JSON認証、全job終了の判定、cleanupからの迂回解除がないことをレビューする。

実Windows job/handle/SSPI/秘密鍵隔離、実network停止・外部信頼基盤、PC再起動・権限分離・機器交換は未検証/未実装。固定コード/監督者/同一userを信頼する境界があり、任意の悪意あるPythonコードやhost管理者から保護しない。実署名鍵/clock/witnessも模擬である。

証明が失われる/期限切れになる/監督が応答不能になると、物理的には閉じていても予約を解除できない場合がある。新しい監督者による旧jobの自動引き継ぎ、証拠欠落時の復旧/再調査は未実装。PID不在・close・期限切れだけで解除する救済は作らない。

次段階は安全レビュー、Windows隔離環境での監督資源所有/認証/中断実機検証の別承認。本番接続・実DB復旧・外部契約・GitHub保存は今回の対象外。

## 9. 今回の実行結果

Linux隔離環境で新規37件が成功（10.486秒）、全体861件が成功（53.533秒）。いずれも失敗0・スキップ0。全体には既存802件と前段階22件を含む。新規実装のWindows実機検証は未実施。


## 10. GitHub保存前安全レビュー（2026-10-29）

判定：不合格・保存保留。実装の修正、commit/push、本番操作は行わない。

追加の負の保証テストで、同一OSユーザーが一時supervisor key fileを読み取れる場合、現在intentと完全なfactsを自作し、稼働中workerを終了させずに解除・停止完了できることを再現した。監督者/HMAC鍵を信頼する既知のモデル限界を具体化したものであり、暗号そのものの破綻ではない。偽造されたobservation_hashに対応する実観測をauthorityが独立確認する機構はない。不正worker/別processからの鍵隔離を要求する用途では高リスクであり、この試作をその保証付きで保存可能とは判定しない。worker終了後以外の任意書込を防ぐOS資源所有も前提であり未実装。

レビュー前の37件は8.888秒で成功。追加5件を含む42件は10.215秒で成功、いずれも失敗0・skip0。鍵漏えいのテスト成功は安全性の成功ではなく、上記の攻撃成立条件の確認である。高リスク再現を受けて停止したため、今回の全体861件は未再実行。前回861件成功と混同しない。

追加検証：別processの監督者無効化が証明検証中のローカルwriter lockで待機し、解除後に確定して旧証明を拒否すること、別process再起動時の未解決予約保持、正しいMACでもscope/inventory/worker/job/boot変更を拒否すること、外部履歴保存失敗時の予約保持、漏えい鍵による偽造成立を確認した。

通常の鍵信頼前提下では、外部SQLite内の証明検証・receipt記録・予約削除は同一transactionで直列化される。外部/ローカル二つのDBを跨ぐ単一原子transactionではなく、途中失敗時の不一致/FAULT拒否を使う。この整合性は偽造された真正証明を見抜く保証の代わりにはならない。

登録外資源の検出は固定workerのinventory前提に限定され、OS全体のhandle/子process/未完了I/Oの探索は行わない。実OS/外部へ既送出のデータは取り消せない。両台帳同時巻戻し、同一user/host管理者による侵害、時計/鍵の実保護、証拠喪失後の可用性は未解決。

次段階は、信頼境界の代表確認と、別主体による鍵保持・監督者本人確認・資源所有を隔離環境で実証するための別承認。一般caller/workerが観測factsを自作して署名できない設計が必要。今回その修正は行わない。
