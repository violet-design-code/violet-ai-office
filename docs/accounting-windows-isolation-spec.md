# Windows：DB・台帳・外部確認・出力の権限分離仕様案

2026-10-29。設計のみ。Windowsの設定・service・account・ACL・資格情報を作成/変更していない。現在のPCのWindows版、管理者構成、TPM、ドメイン/ID契約、実path/ACLは未確認。

## 1. 前提となる制約

SQLiteはファイル単位のアクセスであり、NTFS ACLで会計tableだけを隠せない。一般AIプロセスへ業務DBの読取り権限を渡すと、会計APIを保護しても直読で迂回できる。現在のStoreが直接DBを開く構造を、そのまま一般AI accountへ残して本番保護済みとはしない。

同じユーザーSIDで動く任意の不正プロセスを、PIDや実行ファイル名だけで安全に区別する設計もしない。一般AI workerを代表のログオン資格や代表sessionで動かさない。OS管理者によるコード改変/メモリ読取りまでアプリだけで防ぐ保証はしない。

## 2. 主体と権限（候補名、未作成）

|主体|業務DB|ローカル台帳|外部認証鍵/照会|出力・その他|
|---|---|---|---|---|
|VioletData専用service|専用private directoryでRW、journal/WAL等も管理|直接アクセスなし|外部通信/署名権限なし|認証されたGate/業務操作に限定したquery/command。任意SQLを受けない|
|VioletGate専用service|ファイル直読不可、Dataへ限定IPC|台帳/予約/証拠のRW|Anchorへ認証IPC、外部署名秘密鍵なし|停止・出力予約・監督。protected responseの所有者|
|VioletAnchor専用service|不可|必要最小のheadメタデータをIPCで受信、直接RWなし|許可された外部APIだけ、機器client認証鍵を利用|任意URL proxyや一般送信を提供しない|
|出力worker|不可|不可|不可|delivery単位のprivate buffer/限定handleだけ。任意child/外部helperを起動させない|
|一般AI worker|不可|不可|不可|割り当てられたagent/task/businessだけの短命capabilityでIPC。代表tokenを持たない|
|代表UI利用者|UI経由の管理・最終承認。通常操作でDBファイルを開かない|直書き不可|本人再認証だけ、service鍵を取得しない|全情報確認は現在認可＋復旧停止を通す|
|復旧/backup担当|明示承認した停止/保全手続きのみ|同じbackup jobへ含めない|通常の許可発行権限なし|別権限の復旧証拠・監査を保持|
|その他ユーザー/プロセス|不可|不可|不可|IPCも原則拒否|

serviceごとに専用service SID/実行accountを使い、LocalSystem万能権限を既定にしない。実際のaccount種別は対象OSと配備方式を確認して決定する。代表がWindows管理者でも日常の代表承認は管理者資格の使用と分ける。

## 3. ファイルと鍵

- 業務DB、台帳、証拠/機密log、service binaries/config、鍵の保管先を分ける。ユーザーprofileや共有folder、同期folderをprivate DBの運用先にしない。
- DB directoryのACLはDB本体だけでなくWAL/SHM/journal/一時ファイル/backupにも適用。一般ユーザー/AIにdirectory読書き・所有権変更権を渡さない。継承/DACL/ownerを実機検証する。
- 台帳は業務DBのbackup/restore対象から除外し、独立headの証拠として別保全する。パスを分けただけでPC全体rollbackに強いとはしない。
- binaries/configは配備管理者だけが変更でき、runtime serviceには必要なreadだけを与える。台帳キー保管とservice設定変更の権限を一般ユーザーへ渡さない。
- client秘密鍵はservice限定のCNG key storage/証明書private key権限等を候補とする。非export/TPM利用は実機確認を要し、搭載だけで安全と判定しない。DPAPI machine scopeだけで全ユーザーから守れるとはしない。
- config/環境変数/command line/log/一時JSONへ平文鍵や代表tokenを書かない。dump/pagefile/診断資料のアクセスも制限する。鍵rotationと失効・保全・交換手順を決める。

バックアップと復旧は既存計画を維持する。権限設定やDB移動は既存UI/業務に影響するため、将来の移行計画・検証と別承認が必要。今回はDB pathを変更していない。

## 4. IPC認証と入力制限

ローカルnamed pipe等の候補を使い、remote client拒否、明示DACL、serviceによる最初のinstance確保、server側/利用側双方の身元確認を要件にする。具体的API候補はPIPE_REJECT_REMOTE_CLIENTS、FILE_FLAG_FIRST_PIPE_INSTANCE、SSPI相互認証、ImpersonateNamedPipeClient等だが、対象Windowsでの適合確認は次工程。

serverはOS/SSPIから得た認証結果のSID/logon/必要な起動情報を使い、payloadのusername/role/agent申告を信じない。impersonation取得失敗は拒否し、revert処理を必須にする。clientも期待するservice主体と新nonceを検証し、偽pipe serverへ資格を渡さない。

AI実行単位のcapabilityは、登録workerの起動ID、task/agent/business、scope、expiry、request nonceに束縛する。複数agentが同じSIDを使う場合、SID検査だけではなりすましを防げない。Gateが信頼して起動/登録した実行単位のcapabilityを別に検証する。代表承認用capabilityはこの経路で発行しない。

schema version、messageサイズ、許可command、field型、重複nonce/operation ID、request期限を検査。未認証時にはDB/台帳/会計値を取得しない。エラーは固定コードだけとし、pipe名の知識や接続成功だけで閲覧を許可しない。既存HTTP認証・CSRF・事業ACLも残す。

## 5. 出力所有とservice再起動

会計HTTPの初期案はGateがlistener/connectionの出力管理を所有し、非会計UIと限定IPCで連携する。既存handlerへbytesを返すだけでは停止保証がない。会計responseはConnection: close・サイズ上限ありの単一responseとし、keep-alive、多重化、圧縮/proxyの隠れたbufferは最初の対象から外す。

deliveryごとに隔離したworker/jobを候補とする。workerはbuffer/socket等の唯一の所有者となり、UI/他workerへhandleを複製しない。jobからの離脱/未登録child、暗黙のhandle継承を禁止し、許可handle一覧を固定する。Gate/監督側はworkerのprocess handle/作成情報・job全体を追跡する。

service停止は新規許可を閉じて出力をdrain/検証中断する。応答しないwriterがあれば停止完了にしない。SCMのSTOPPEDだけを会計停止完了と混同しない。Gate異常終了では監督/job設定でwriterを停止する案を検証するが、job handleの複製・child残存・pending I/Oを未確認なら中断証明を作らない。

再起動はCLOSEDで始め、broker起動世代を更新する。外部最新headと全未完了予約を照合し、旧writerが不明なら維持する。予約をTTLやPID不在だけで消さない。Data/Anchor停止・証明鍵不明・IPC失敗時も会計を拒否する。

## 6. 再インストール・PC故障

既存系統の復旧申請として扱い、新規空台帳を作ってopenしない。旧機器資格の失効、代表の独立した本人確認、DB/台帳/外部証拠の照合、未完了writerの扱い、新機器登録・新epoch・個別再承認を別々に確認する。

DPAPI/TPM鍵を失えば自動復号できるとはしない。TPMクリア・鍵再生成を復旧手段として無断実行しない。全履歴を最新headまで確認できなければ停止を維持する。元PCからの古い資格再利用も外部登録で拒否する。

## 7. 実装時の受入条件

別ユーザー/一般AI/同一SIDの別worker/偽service/remote pipe/古いcapabilityを拒否する実機試験が必要。NTFS DACL、サービス設定、鍵export制御、SSPI/impersonation、job/handle継承、強制終了、PC再起動/鍵喪失は今回未検証。

現アプリへの影響は大きい。DataへのDB所有集約、Gateへの出力所有移行、通常認可の再利用、代表UI/非会計機能/HTTP認証・CSRFの維持を段階実装する。既存の28社員の定義/担当/permissions/activeは移行機構の都合で自動変更しない。
