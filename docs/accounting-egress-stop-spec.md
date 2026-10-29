# 会計停止・失効：引き渡しまで含む整合性仕様

2026-10-29。仕様のみ。アプリ本体/HTTP handler/成果物経路に接続していません。
関連：[台帳方式](accounting-trust-method-decision.md)。

## 1. 保証の境界

**停止完了後に、旧世代・旧許可に基づく会計情報を新たにアプリの信頼境界の外へ引き渡さない。**

対象はHTTPの本文/会計を含むヘッダー、サービス戻り値、参照IDの解決/引き継ぎ、添付/ダウンロード、成果物公開・汎用履歴/ログ/通知への出力です。値を取得しただけ・JSON化しただけでは引き渡し許可とみなしません。自由文に会計を混ぜる経路は別途分類し、保護未接続なら停止します。

保証できないもの：停止完了前にOSのsocket送信キューや受領者へ既に渡ったバイトの、停止完了後の到着/表示/コピー。ブラウザに取得済みの値、受領者の記憶、画面写真も回収できません。「停止後に画面で一切見えない」という保証はしません。HTTP flush完了は相手の受領/表示完了を意味しません。

この境界は代表の承認事項です。受信側での後日表示まで強制したい場合は、管理された受信アプリ/オンライン再認可・端末制御等の別設計が必要で、通常ブラウザへの平文HTTP応答では達成できません。

## 2. 出力管理の主体

Windows単一PCの初期案は**一つのbrokerサービス**が会計出力の入口と最終引き渡しを所有する方式です。アプリの各プロセスは認証されたIPC経由で依頼し、brokerを迂回してraw accounting bytesを受け取ったり書き出したりできない構造にします。IPC/サービス設定/鍵/台帳は専用サービスアカウントとACLで保護します。

現行web.pyはOfficeApp.handleがbytesを返した後にHandler.respondがwfile.writeするため、AccountingAccess.read内のロックだけでは不足します。将来の実装ではhandlerがbrokerと出力予約を保持したまま、会計ヘッダー・本文の書込/flushまで管理します。直接サービス呼出しで値を受け取る側も信頼境界に含め、任意に遅延出力する呼出し元へ無保護の辞書を渡して終わりにしません。境界を満たせない経路は拒否します。

brokerはアプリのSQLite読取りtxnをネットワーク送信中ずっと保持しない構成を検討します。private bufferへ限定データを取得後、最終出力時に台帳/通常認可/対象版を再検査する方式です。policy/task/assignment変更も出力制御を経由する必要があります。直SQLや別経路の役職/担当更新を許したままで全変更との整合性を保証しません。

## 3. 状態・出力予約

状態はUNREGISTERED / CLOSED / RECONCILING / REVIEW_READY / ACTIVE / DRAINING / FAULT。状態不明はFAULTとして拒否します。外部台帳はinstance/epoch、policy revision、scope、decision/operation ID、状態、最新seq/head、未完了intent、最終確認済みheadを持ちます。

出力予約には、request ID、登録broker ID/起動世代、instance/business/task/agent、policy ID/内容版/許可項目・期間digest、epoch、取消revision、開始seq、対象sinkの種別を含めます。会計値を予約台帳へ保存しません。予約は使い回せず、他事業・他sink・別プロセスで再利用できません。

出力の線形化点は、現在許可を検査して**引き渡し予約を確定した時点**です。データ取得開始時刻ではありません。停止/取消受付と予約確定は、同じ外部状態の直列化/CASとbrokerの制御で順序を一意に決めます。

## 4. 読み取りと停止の順序

1. 初期認証・事業/役職/担当・policy/fields/period/expiry・最新外部状態を確認。問題があればデータ取得前に拒否。AI04は常に拒否。
2. 許可項目だけをprivate bufferへ取得。汎用ログ/成果物へ書かない。通常の会計認可は省略しない。
3. 出力前に再検査し、現在世代・scopeで一回限りの出力予約を確定する。CLOSED/DRAINING/FAULT/失効/照合不能ならbufferを破棄して拒否。
4. 予約を保持したbrokerだけが対象sinkへ書込/flushする。進行中の配信を台帳/brokerから未完了として追跡する。会計レスポンスは初期版ではサイズ上限付きの非streamingとし、任意streamや遅延添付URLは許可しない。
5. 書込完了/確実な中断を記録して予約を解放。結果/最終seqを監査する。

停止処理：

1. 認証された停止要求をoperation IDで受け、外部側にstop_intentを永続化、対象scopeをDRAININGにする。ここから新規予約を拒否。管理UIは「停止要求受付・処理中」であり「停止完了」ではない。
2. 全登録brokerへ禁止を伝達。取得中/待機中/未予約の結果は破棄。既に出力予約確定済みの送信は安全に中断、または完了を待つ。**受付前に予約された送信がDRAINING中に終わることは許容するが、完了後の送信は許可しない。**
3. 全出力予約について、引き渡し終了/確実な中断・writer終了・buffer処理を確認。応答しないプロセスは単に期限切れ扱いして無視しない。タイムアウトは停止未完了。
4. 未完了intentなし、全対象brokerが同じfenceを確認、未処理出力ゼロ、必要な監査が永続化した場合だけstop_completeを確定する。旧epoch/revisionの予約/receiptを無効化し、新世代もCLOSEDから始める。

## 5. 完了条件と障害時動作

|状況|動作/完了可否|
|---|---|
|データ取得開始済みだが出力予約前|停止受付後は予約不可、結果破棄|
|出力予約が停止受付より先|中断またはdrain。終了確認まで停止完了不可|
|別プロセスが停止/取消と同時に予約|直列化順序で決定。後なら拒否、前ならdrain対象|
|writer停止/OS socket書込が応答しない|新規出力禁止を維持、停止未完了。既定タイムアウトで「完了」にしない|
|broker crash|その起動世代のwriter終了を信頼できるOS/監督サービスで確認。brokerは再起動時CLOSEDで最新台帳へ再登録。未確認なら停止未完了|
|brokerとの通信分断|新規出力拒否。既存予約の終了を確認できないなら停止未完了|
|外部台帳 unavailable / 更新commit不明|ローカルも新規出力停止。操作成功を断定しない。operation IDで再照会、確認まで再承認不可|
|ログ/監査の永続化失敗|通常の許可を返さずFAULT。既に境界外へ出たbytesは回収できないため送信済み不確定として保全|
|TTL/leaseだけが期限切れ|pause中のwriterは再開し得る。予約を勝手に消して完了にしない|
|停止前にOSへ渡ったbytesが停止後に到着|本仕様の禁止対象外。受信後消去や到着時刻は保証しない|

単一PCではtrusted brokerがOSの出力ハンドルを実際に所有する必要があります。複数PCでは、全broker参加の障壁を採るか、唯一の信頼済みegress gatewayへ出力を集約します。raw HTTP出力可能な孤立brokerを放置したまま、外部counter更新だけで「停止完了」とはしません。fencing tokenもsinkが検証/強制しなければ効果を持ちません。

## 6. 個別失効と再承認

個別取消はbusiness/policy ID/版のscopeについて外部denyを先に永続化し、新規予約拒否→既存予約drain→業務DBの条件付き失効/同一txn監査→外部取消完了を記録します。DB書込失敗でも外部denyを維持し、「取得停止」と「失効処理全完了」を区別します。全体停止なら全scopeを対象にします。他事業の独立scopeを自動許可/失効しません。

再承認はDBのpendingからの条件付き承認と、独立した代表承認・新epoch/新policy receiptの両方を必要とします。片側成功/監査不足は許可不可。旧revoked/rejected/expiredのIDをapprovedへ戻しません。再試行はoperation IDと内容digestで冪等にし、同じIDの異内容を拒否します。

AI01 management、AI02 summary、AI06 improvement、AI07 advertising、AI14 sales、AI15 terms、AI25 system、AI27 summaryを旧3経路で維持。AI27 detailとAI28 auditは専用サービスのみ、auditは承認した期間/項目を超えない。AI04/その他禁止社員は外部receiptの有無に関係なく拒否します。

## 7. 必要なインターフェース（今後の実装契約）

- register/recover：独立本人確認・単回enrollment proof・系統・DB manifestを照合し、CLOSEDの登録結果を返す。
- current：instance/scope/nonceに束縛した現在のhead/epoch/停止・取消を認証して返す。cache fallbackなし。
- prepare/commit approval、deny/revoke：期待seq/版・operation ID・内容digestを指定し、順序/未完了状態を記録する。
- reserve_delivery：通常認可済みのscope/現在版について予約を確定。停止/失効との順序を外部側で直列化する。
- finish/abort_delivery：信頼済みbrokerのwriter結果を記録。未知/使い回しtokenを拒否。
- request_stop/status/complete_stop：受付と完了を別応答にし、対象broker・予約・未完了処理を照会可能にする。

今回のモデルでは外部サービスは一時SQLiteのAuthorityModelで代用し、実際のIPC/HTTP/TLS/本人確認/Windowsサービス/停電時耐性は検証していません。モデルを本体へimportしてはいけません。
