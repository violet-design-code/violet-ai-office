# 出力失敗後の安全な中断確認：実装契約案

2026-10-29。設計とテスト内モデルのみ。既存 `synthetic_recovery.py` に解除APIを追加していない。現在の未解決予約は、従来どおり自動解除できない。
関連：[Windows分離](accounting-windows-isolation-spec.md)、[外部信頼基盤](accounting-external-trust-deployment-spec.md)。

## 1. 証明する内容・保証しない内容

中断確認の意味は、**当該deliveryの未送信データについて、現在も将来も旧writer/旧handleから新しい引き渡しが起きないことを、信頼した監督主体が確認した**ことである。例外、timeout、close成功、PID不在、GC、本人の「中断した」という申告ではない。

既にOSへ渡ったbytesの後日の到着/表示/コピーは取り消せない。中断証明は「相手に一切届かなかった」や「会計内容がディスク/メモリから物理消去された」を意味しない。OS/監督service自体を侵害された場合も本保証の外側である。

単一のwriter processだけをkillしても、複製handle、child、helper、別processのbufferがあれば不十分。確認範囲の漏れがあれば予約は未解決のままとする。

## 2. 初期の実装対象

最初は、Gateが起動・登録する**delivery単位の隔離worker/jobが唯一のbuffer/HTTP出力handleを所有する構成**に限定する。一般callerが持ち込む任意streamへ、汎用abort確認を発行しない。

- jobからの離脱/未登録child/handleの暗黙継承を禁止し、handle所有・起動identityを登録する。
- workerは任意の外部helper/proxy/cacheへ送出を委譲しない。共有bufferや別processへのhandle複製がある場合は初期対象外。
- 会計responseは単一response、サイズ上限、Connection: close。HTTP/2多重化、圧縮、proxy/HTTP.sys等の隠れたqueueを扱う構成は別評価。
- Data/Gateのprivate stagingも一般AIへ渡さず、別deliveryでの再利用には現在認可を要求する。raw accounting bytesを任意callerへ返して終わりにしない。

## 3. 状態と中断手順

予約状態：RESERVED → EMITTING → HANDOFF_CONFIRMED、またはUNRESOLVED → ABORT_INTENT → ABORT_OBSERVED → ABORT_VERIFIED → ABORT_RELEASED。
状態が分からない、監査が書けない、版が違う場合はUNRESOLVEDを保持。TTLによる解除はない。

1. **停止/取消intent**：外部側で新規予約を禁止し、対象delivery/epoch/版とoperation IDを永続化。全processへ同じfenceを適用する。利用者には「停止処理中」と表示する。
2. **中断intent**：監督主体へ新規challengeを発行し、worker birth ID・broker boot ID・job・handle inventory・対象transportを束縛する。intentの保存が不明なら停止未完了を維持する。
3. **producerの封鎖**：新しいwrite/retry/flush callbackの生成を止める。worker/jobを停止・終了させる案を基本とし、一般的なbuffer.closeを先に呼んで残留bytesをflushしない。
4. **I/Oと所有者の確認**：CancelIoEx/shutdown/closesocket等を使う場合も、呼出しの成功だけで証明しない。pending I/Oの結果/完了処理、worker process handleの終了、job内全process、登録handleの所有/複製/close状況、外部producer不存在を確認する。具体的なWindows API/orderingは次の実装・実機検証で確定する。
5. **未送信bufferを到達不能にする**：隔離workerのprivate bufferを失効させ、旧handleから再flush/close/retryできないことを確認。独自bufferのin-process破棄を使うなら、raw出力先の確実なfenceを先に証明してから行う。標準BufferedWriter一般に「安全なdiscardがある」と仮定しない。
6. **監督証拠**：登録された監督serviceが、OS/所有inventoryの確認結果をoperation/challenge/epochへ束縛して署名する。Windowsの観測を実際に行った証拠と、callerのboolean入力を混同しない。
7. **検証/解除**：外部A/Wとローカルの現在状態を照合し、当該未解決予約だけを状態条件付きで更新。証拠digest/receiptを保存し、二重解除を防ぐ。片側のcommit不明は未完了として扱う。
8. **停止完了**：全対象deliveryが正常引き渡しまたは検証済み中断として確定し、全broker/監督世代・監査・未完了intentを確認した場合だけ出す。

キャンセル要求を出しただけ、TCPのRST/FINを要求しただけ、service managerがSTOPPEDと表示しただけでは手順4〜6を満たさない。停止完了前にkernel queueへ渡ったbytesは手順4で回収を保証せず、既送出可能性を監査の固定状態として区別する。

## 4. 証拠と検証API

証拠version、org/instance、business参照、delivery ID、policy/版・scope digest、epoch、broker boot ID、worker birth ID、job ID、handle inventory digest、transport種別、abort operation ID、challenge nonce、期待head/版、監督主体/鍵ID、観測結果、既送出の有無/不明、署名を持つ。金額・本文・顧客名・認証秘密を入れない。

必要な観測は少なくとも次の全て：

- 同じ登録workerの終了確認（PIDだけでなく作成情報/起動identityを確認）。
- job内に将来write可能なprocessが残っていない。
- 登録handleと複製を全て説明できる。
- pending I/O/完了callbackの状態を説明できる。
- 旧deliveryのprivate bufferから新しいproducerが生じない。
- 対象出力先がfenceされ、残留bufferが旧handleから引き渡せない。

受信側は署名algorithm/root/鍵失効、登録監督主体、challenge、全識別子/版/期間、現在の外部head、必要観測の完全性を検査する。検証済みrequestのcopy/正規化を行い、検証後に内容が差替わらない構造にする。

同じoperation ID＋同じ証拠digestは前回receiptを返す。違う証拠、他delivery/事業/旧世代への流用、別workerや再利用PID、異なるhandle inventoryは拒否する。監督者の署名は観測の真正性の根拠であり、外部サービスがWindows OS状態を独立して数学的に証明するものではない。監督service/OSを信頼する境界が残る。

## 5. 永続性・複数process・再起動

解除は外部A/W・ローカルへ順序付きで記録する。Aで検証済みpending→Wがhead/証拠digestを確認→ローカル確認→確定receipt、という失敗安全な手順を別実装する。物理的中断と複数DB更新を単一transactionとは呼ばない。中断は不可逆でも記録が不明なら停止未完了を維持する。

同一予約を複数processから解除する場合は、期待状態/版と更新件数を検査し、解除監査は一回だけ。別processの古い認可や別epochからの解除は拒否する。全体停止は全予約/全参加brokerを対象にし、応答しないbrokerを単に一覧から外して完了させない。

監督service異常終了・PC再起動はCLOSEDから再開する。未完了予約/abort intentを再読し、新しい監督起動identityで再調査する。以前の確認途中のbooleanやPID不在を再利用しない。OS観測/証拠が回収不能なら維持する。機器交換では旧device資格を失効し、外部最新履歴と代表確認を要する。

## 6. 成果物・引き継ぎ

初期の中断成功機能は上記の限定HTTP transportだけを候補とする。artifact/handoffへの一般化は別の受入条件を要する。

成果物はprivate stagingで作り、public path/検索/汎用履歴へ公開する前に現在認可を確認する。部分ファイルのpathを一般AIへ渡さない。鍵破棄による到達不能を採用するなら、全key holder/複製/backupまで確認し、SSD上の物理消去を保証したとはしない。

既に公開/引き渡した成果物は「未送信なので中断済み」としない。公開の線形化点、現在の閲覧時再認可、既送出/不明の監査を別途設計する。既存の会計taskの汎用成果物拒否を残す。

## 7. 今回の検証と残る境界

テスト内AbortSpecModelは、認証済みの監督観測・外部head・ローカル確認があるという前提で解除条件/冪等性/SQLite競合を検証する仕様モデル。HMAC鍵は公開fixtureであり実署名基盤ではない。Windowsのprocess/job/handle、SSPI、CancelIoEx、cloudを操作しない。

実際のBufferedWriterと同期mock rawで、flush失敗後にraw側を先にfenceすると後続flush/close/retryから新規writeできない限定例を確認する。ただしそれだけでWindows job全体の中断証明を作ったとはしない。raw fenceが失敗すると再flushで出力が起き得るため、解除を拒否するケースも検証する。

現在の試作の未解決予約をこのモデルから解除する接続はない。次の承認後に、実際の所有者・信頼された観測と署名付き証拠を分離して実装・故障注入する必要がある。
