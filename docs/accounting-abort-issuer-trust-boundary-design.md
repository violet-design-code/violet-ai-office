# 中断証明：鍵隔離・証明発行者の信頼境界設計

2026-10-29（日本時間）。設計・合成検証のみ。ブランチ `feat/accounting-verified-abort-sandbox-20261029`、基点 `25a76039b493148615eb77a053d08749725d3875`。既存8ファイルは保持し、本体・Windows設定を変更しない。GitHub保存保留を継続する。

## 1. 原因・現在の主体

`synthetic_abort.py` の `OwnedOutputSupervisor.__init__` がランダムHMAC鍵を同一realmの `mock-external/supervisor-<ID>.key` へ平文保存する。通常workerへ鍵を引数として渡さないが、同一OSユーザーの任意コードからの読み取りを禁止するOS境界がない。実機ACL・owner・他ユーザーの実効アクセスは未調査であり、別ユーザーから常に読める/読めないとは断定しない。

|主体|現試作で行う処理|信頼上の問題|
|---|---|---|
|worker|固定一時ファイル・bufferを操作、認証付き観測を監督へ返す|親と同一ユーザー。任意不正コードなら鍵ファイル・出力ファイルへのアクセスをアプリだけで禁止できない|
|監督者|worker応答とProcess終了を検査し、factsを構成|鍵と代表fixtureを持つ親プロセスを信頼。管理者や同一userからのメモリ保護はない|
|`_proof_issuer`|親から受けたbodyをHMAC認証|独立観測者ではない。別processというだけで信頼主体が分離されたわけではない|
|VerifiedAuthority|登録鍵hash/boot、HMAC、現在intentを照合して予約を削除|検証にも秘密鍵が必要。鍵保有者なら正しい形式の偽観測を作れる|
|VerifiedLedger|ローカル制御履歴と外部確定状態を同期|台帳の整合性は偽観測の真実性を保証しない|

レビューで漏えい鍵による偽造・worker稼働中の停止完了を再現済み。対象/challengeを正確に照合しても、現在のintentと鍵を取得した攻撃者の偽造は防げない。試験用HMAC鍵は本番安全と判定しない。

## 2. 方式比較

以下は代替関係だけでなく組合せ可能。費用は相対評価、ライセンス・機器・契約・見積りは未確認。外部への調査通信/契約は行っていない。

|方式|安全性・必要権限|導入/費用/運用|PC故障時|
|---|---|---|---|
|A 専用サービスアカウント|一般workerと異なるSID、鍵使用・ファイル・service設定のアクセス制御。workerから鍵を直読しにくくするが、署名要求の濫用や管理者侵害は別問題。HMACのままなら検証者も発行能力を持つ|中程度。原則サービス追加の管理者作業とWindows構成検証。追加クラウド固定費は不要でも実装・保守費が必要|SID/鍵/外部登録を照合して新機器登録。鍵喪失を新規初期化で救済しない|
|B Windowsサービス＋認証IPC|Aに加えて呼出者・呼出先・操作・scopeを検証。任意bodyへの署名APIを禁止。署名者・検証者を非対称鍵で分ける|高。service/IPC/資源監督/配布更新/監査を維持。通常のローカル運用でも復旧訓練が必要|旧起動世代/鍵の失効、新サービス本人確認、未解決job再調査。不明なら停止|
|C TPM/非エクスポート鍵|Bを補強。CNG鍵の使用権を発行serviceだけに限定。TPM搭載/非export属性だけでは正しい観測も正当な鍵使用も保証しない。侵害serviceが署名APIを悪用する限界|高。対応OS/TPM/providerの確認、機器依存テスト。既設TPMなら追加機器費不要の場合があるが、機器交換・保守費は必要|TPM鍵は通常新PCへ移せない。旧鍵失効と新鍵の独立登録。未処理証明を自動再署名しない|
|D 外部独立発行service|ローカル管理者から秘密鍵を分離可能。ただしローカルが送った「終了した」という申告へ署名するだけなら偽観測を承認する。実観測への信頼できるchain/attestationまたは出力所有者の外部移行が必要|最大。認証、外部監査、可用性、運用者分離、通信/保存/HSM等の利用量費用。外部管理者侵害も対象|独立本人確認、新機器登録、旧資格失効、最新外部head照合。オフラインでは新規証明/解除を拒否|

推奨：**B＋Aを基盤に、Cの非対称鍵を組み合わせる段階案**。監督serviceが出力worker/job/handleを支配し、証明発行serviceへ認証IPCで観測要求する。発行serviceは信頼した監督資源情報を確認し署名する。検証serviceは登録済み公開鍵だけを持つ。初期実証では監督と発行serviceを別SIDとし、双方とも一般AI/代表UIと分離する。将来Dを採る場合も、外部が観測の真実性を何によって検証するかを先に確定する。

DPAPIは保存時保護の補助であり、同じ復号主体の不正processや管理者からの完全保護ではない。単なる鍵ファイルの移動/隠蔽、拡張子変更、ACLだけの修正で保存保留を解除しない。

## 3. 将来の主体・秘密・権限

|主体|所有するもの|禁止|
|---|---|---|
|一般AI/出力worker|限定capability、deliveryに限定したbuffer/許可handle|秘密鍵、代表資格、台帳直書き、自己証明、子process/handle複製/任意network|
|監督service|OSが付与したprocess/job handle、起動ID、出力資源inventory、監督用IPC権限|署名鍵のexport、caller入力factsをそのまま認定、任意SQL/任意署名|
|証明発行service|CNG/TPM秘密鍵の利用権、監督serviceの登録情報、限定観測確認|一般callerへの署名権委譲、任意body署名、worker自己申告の信用|
|検証/予約管理service|公開鍵登録・失効・復旧世代・現在challenge・予約台帳|秘密署名鍵、workerの自由な鍵登録、古い鍵への自動fallback|
|代表|独立本人確認付きの新機器/鍵登録・世代/個別再承認|通常AI IPC経由での代表昇格、日常承認でのOS管理者権限利用|

「発行者以外が秘密鍵を取得できない」は一般worker/利用者に対する要件。host管理者/kernel/署名service自体の侵害まで絶対に防ぐ主張はしない。非export鍵でも、不正に鍵使用APIを呼べれば偽署名を作れる。公開鍵の信頼登録/失効状態を管理者が巻き戻せば検証も欺かれる。OS外の独立状態・本人確認・監査で検出範囲を拡張するが、全信頼主体の共謀/同時巻き戻しは残る。

## 4. 発行・検証・解除プロトコル仕様

1. 未解決予約を維持したまま新規出力を閉じる。予約管理側がinstance/復旧世代/delivery/business/agent/target/operation/現在head/登録監督bootに束縛した一回用challengeを永続化する。
2. 呼出者入力のusername/SID/role/PID/factsを本人確認や資源確認に使わない。OS/SSPIで認証した接続の主体・logon/用途と登録capabilityを照合し、利用側も期待した発行serviceを認証する。偽server、remote pipe、再生nonce、サイズ超過、不明schema/commandは拒否。
3. 監督者はOS process handle/作成情報、Job Object、handle継承/複製制限、未完了I/O、buffer唯一所有、再出力不能を検査。PID消失/close/timeoutだけでは不足。登録外資源を排除できない場合は発行しない。
4. 発行serviceは権限と現在challengeを取得・照合し、信頼した監督者の完全な観測chainを検査する。caller提供の観測digestだけを確認した扱いにしない。異常/通信失敗/鍵不明なら発行しない。
5. 署名bodyにはversion/algorithm/key_id/instance/epoch/reservation/business/agent/target/worker誕生情報/job/inventory/監督boot/challenge/revision/operation/観測hash/事実/時刻/期限/既送出不明を含める。canonical encodingと厳密な型/サイズを固定、署名algorithmをcallerに選ばせない。候補はCNG対応ECDSA P-256/SHA-256等で、対象providerでの採用・encodingは別途実機決定。モデルは暗号実装ではない。
6. 予約管理側で、現在の登録公開鍵・鍵失効/世代・停止状態・未解決予約・challenge一致・期限を**同じ直列化境界内で**検査して、条件付き更新/件数確認、receipt/監査記録を確定する。検証者に署名秘密鍵を渡さない。現在信頼状態を確認できなければ拒否。
7. 重複証明は新規解除しない。本設計モデルでは消費済みを拒否。既存試作の同一receipt再応答を維持するなら、完全一致かつ現世代/鍵失効を再検証し、新規解除でないことを明示する。モデルと既存試作の相違であり、今回は変更しない。
8. 外部鍵失効/最新headを別DB/サービスで管理する場合、単に事前照会してローカルtransactionを開始するだけでは競合を防げない。共通の予約管理authorityで直列化するか、外部のversion条件付き確定とローカル不一致拒否を設計する。単一SQLiteモデルの原子性を分散原子性と呼ばない。
9. 停止/失効完了は関連予約が全て成功引渡し又は検証済み中断で解消した後だけ。発行者故障・鍵更新/喪失・照合不能・保存失敗では予約を維持。再起動は停止状態から始める。TTL/PID不在による解除なし。

漏えいした有効な秘密鍵で、信頼した発行者と同じ署名を作れれば検証側は偽証明を区別できない。非対称化だけでこの限界は消えない。直ちに発行/解除/会計出力を停止し、独立経路で鍵を失効、未処理証明を無効化、再調査・新鍵登録・新challengeに移行する。過去に不正解除された予約は自動的に「中断済み」と信用しない。

## 5. Windowsで必要な設定・実機検証（未実施）

- 対象Windows edition/build、TPM/CNG provider、ドメイン有無を確認してaccount種別を決定。専用低権限service account/service SIDを監督・発行・検証で分け、LocalSystemを既定にしない。対話/remoteログオンの可否とservice起動権を用途別に制限する。
- service binary/config/親directory/DLL探索先を配備管理者のみ変更可能にする。SCM service設定/実行path/再起動設定の変更権を制限。一般workerにservice制御/鍵登録権を渡さない。
- 鍵保管先・private key利用権を発行service SIDだけに限定。TPM provider/非export属性/実際の署名使用ACLを確認する。検証serviceにはpublic certificateと別保全された信頼登録だけ。代表UI/workerへ鍵・代表tokenを環境変数/command line/JSON/logで渡さない。
- 専用named pipeの明示DACL、remote拒否、最初のserver instance確保、SSPI相互認証または適合するOS認証を検証。server側token取得/impersonation失敗は拒否、確実にrevert。service SID確認に加えて登録された起動/task/scope capabilityを照合する。同一SIDの別processをSIDだけで識別しない。
- workerはrestricted token/別SID、許可handle一覧だけを継承、Job離脱/任意child/handle複製を禁止する案を検証する。署名serviceの鍵使用権は継承させない。監督者には必要最小限の検査権のみ付与。
- dump/pagefile/診断/backup/同期folder経由の鍵流出、service更新の署名/配布、監査保全、時計の巻き戻し、旧機器の再接続を試験する。実DB/本番機器の設定は今回変更しない。

## 6. PC故障・鍵更新・管理者侵害

鍵紛失時に古い鍵を無条件に信頼し直さない。独立した代表本人確認、旧鍵失効、外部最新履歴照合、新機器/鍵登録、未解決予約の実観測再調査を分ける。TPM鍵を移せない場合は新しい鍵を登録し、証明を自動移植しない。故障PCの残存writerを排除できなければ停止未完了。

通常rotationも旧未消費証明を無効にし、現challenge/観測に基づいて新発行する。鍵更新を会計policy承認や復旧世代の自動承認にしない。AI04拒否・28人の既存役職/担当/権限・事業分離は変更しない。

host管理者/kernel侵害はローカル署名要求・観測・binary・信頼登録を改変し得る。TPMが秘密鍵抽出を防いでも、署名使用や虚偽観測まで防げるとは限らない。hostを再信頼するまで会計停止し、独立外部記録/本人確認と再構築を要する。実害調査と再承認をDB整合性修復から分離する。

## 7. 合成検証と限界

`tests/test_abort_issuer_boundary_design.py` は一時SQLiteに、模擬OS主体、信頼済み観測store、模擬署名ticket、現在の鍵/世代/予約を配置する**設計モデル**。実署名/TPM/ACL/SSPIは使用しない。signature ticketは信頼された署名検証結果の代用品であり、安全な暗号方式ではない。TrustedPeerをcaller自身が作れないこともモデル上の仮定。本体のHMAC試作を修正/置換していない。

18件：同一userでの一時平文読取り、別SID拒否モデル、認証監督者、worker自己証明拒否、なりすまし/偽server/remote IPC拒否、caller facts拒否、欠落観測、偽証明、対象/世代/challenge束縛、期限/再利用拒否、発行者停止、鍵喪失/更新、署名後世代変更、保存失敗rollback、複数process解除/停止競合、侵害発行者による偽造成立の負の保証。

最後の負の保証テストの成功は安全性の成功ではない。署名者/管理者が信頼storeを書き換えればモデルの検証者を欺けるという限界の確認。別SID拒否テストは実Windowsのアクセス拒否を保証しない。

## 8. Windows隔離実機での次段階受入試験

別承認後、実DB・本番資格・本番ネットワークを持たない破棄可能VM/別PCで、合成鍵・合成出力だけを使う。基準snapshotを保全し、対象SID/ACL/鍵設定/署名provider/OS buildを記録する。

一般user、worker、同一SID別process、別userから鍵読取り/export/署名使用/署名IPCを試し、署名権濫用を拒否する。検証userの秘密鍵不在、偽pipe/remote/再生capability/代表偽装拒否を確認。管理者による侵害試験は保証外の成立範囲も記録する。

実Job/handle複製/worker誕生情報/CancelIoEx等の適合性とpending I/O/残留buffer/終了競合を計測する。署名要求のfacts偽装、サービスkill/再起動、key rotation/喪失、clock変動、stop/release競合を試す。最初は一時ファイルだけで実施し、実network送信の中断試験には別の承認・脅威モデルが必要。送信済みOS bytesは取り消せない。

成功条件は「ファイル鍵が読めない」だけではない。無権限署名要求・偽観測から証明を作れず、未観測資源/不明状態で停止完了できないことを確認する。

## 9. 保存保留解除の条件

1. 代表が、保護対象/脅威主体と管理者侵害時の限界を承認する。
2. worker・監督・発行・検証・鍵登録の権限分離と、任意署名APIを持たない設計をレビューする。
3. 別承認の隔離実装で、一般callerが鍵取得/鍵使用/自己観測の偽装をできないことを実証する。
4. 実WindowsのIPC/鍵/資源所有・終了証拠、原子性/失敗時停止、再起動/rotation/喪失の試験を実施する。
5. 既存の攻撃成立テストを隠さず保持し、修正後の拒否テストと全体回帰を独立レビューする。
6. 指定差分/秘密不在/既存権限制限の維持を確認し、GitHub保存の明示承認を得る。

設計書・モデルだけの保存を別途希望する場合は、脆弱な試作が未修正であることを明記して保存対象を限定し、別承認を受ける。今回の設計完了はコード保存保留の解除や本番導入承認を意味しない。

## 10. 今回の実行結果

Linux隔離環境で `python3 -B -m unittest tests.test_abort_issuer_boundary_design -q`：18件成功（0.132秒）、失敗0・skip0。`python3 -B -m unittest discover -s tests -q`：884件成功（54.868秒）、失敗0・skip0。内訳は既存802件＋前段階22件＋中断証明37件＋前回レビュー5件＋今回18件。Windows実機検証は未実施。

モデル初回実行ではfixture初期データのcommit漏れによる失敗があり、今回の新規テスト内だけを訂正して再実行した。既存コード/テストの期待値や制限は変更していない。全体成功には脆弱性・信頼限界の成立を確認する負の保証テストも含まれ、本番安全を意味しない。
