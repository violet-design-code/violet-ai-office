# 信頼分離された中断証明：隔離試作

2026-10-29（日本時間）。基点 `25a76039b493148615eb77a053d08749725d3875`。前段階の未保存10ファイルを変更せず保持し、`feat/accounting-separated-abort-sandbox-20261029` で開発する。実DB/backup/修復試験DB、Windowsサービス/ACL/TPM、本番会計経路、外部通信には触れない。commit/pushしない。

## 1. 範囲と実証の区別

追加モジュール `tests/synthetic_separated_abort.py` は、前試作の固定worker・D方式台帳を再利用する隔離拡張。アプリ本体からimportしない。任意DBを指定するCLI/修復/移行機能はない。一時SyntheticRealmだけで試験する。

**実際に動くもの**：spawnされたworkerと別の証明発行process、Ed25519署名/公開鍵検証、サイズ制限JSONのprivate pipe、SQLite台帳の直列化・永続化、実Process objectのjoin/exitcode。

**モデル上の前提**：専用Windows SID/サービス、IPCのOS本人確認、鍵の非export、監督者の信頼、固定workerの全出力資源が登録済みであること。実Windows DACL/SSPI/CNG/TPM/Job Objectの実証ではない。同一OSユーザーの任意不正コード/host管理者を隔離する保証はない。

非exportは「子processが生成し、export API/ファイル保存/親への返却を持たない」という試作APIの性質であり、ハードウェアによる抽出防止ではない。秘密鍵がメモリに存在しないという意味でもない。

## 2. 主体と鍵

- worker：既存の固定 `_worker`。一時file/bufferと制御認証鍵だけを保持。証明秘密鍵・発行者IPC capabilityは引数として渡さない。自己申告のfactsをそのまま署名するAPIは提供しない。
- 監督者：SeparatedSupervisor。自分で生成・登録したjobだけを扱い、実Process objectの終了と登録inventoryを照合。モデルでunknown_resourcesがあれば発行前に拒否する。
- 発行者：IsolatedIssuerの専用spawn process。起動時にEd25519秘密鍵を内部生成し、公開鍵/key_id/bootだけを返す。秘密鍵はprocess外へ送らず、一時realmにも保存しない。署名対象は認証された監督channelからの限定protocol bodyのみ。
- 検証/予約管理：SeparatedAuthority/SeparatedLedger。登録公開鍵だけで証明署名を検証。秘密署名鍵は保持しない。D方式台帳の整合性用HMAC fixture鍵は別用途で引き続き存在する。
- 代表fixture：公開鍵の初期登録/rotation/監督無効化の認可を行う。これはWindows本人確認ではない。workerによる公開鍵差替えを許可しない。

旧試作の `_proof_issuer` と違い、監督者は証明秘密鍵を生成/保持せず、署名を自分で再構成しない。署名のalgorithm/key_id/issuer_bootも署名対象へ含め、caller提供公開鍵を使わない。

## 3. IPCモデルの正確な信頼境界

MockAuthenticatedPeerはOS認証済み主体の**テストfixture**。許可SID/期待server/非remote/認証成功を起動時に確認し、監督にだけ新しいchannel capabilityを渡す。以後、元のpeer objectと起動processに束縛したendpointを使い、発行processでもcapability/boot/連続sequenceを照合する。

同じ内容のpeer objectの自作、WORKER-SID、未認証/remote/偽server、capabilityを持たない別processの要求を拒否する。PID検査は補助的fixture制約であり、OS本人確認の実装ではない。private Python属性はセキュリティ境界ではなく、任意同一userコードが監督object/資格を読む攻撃は防げない。Windows化にはOSで検証した接続主体と鍵使用ACLが必須。

発行者は認証済み監督者の観測を信頼する。発行者がOS資源を独立に列挙したわけではない。信頼した監督者自体が侵害されれば、完全な偽factsへ署名させ得る。この限界を非対称署名だけで解消したとはしない。

## 4. 監督と証明発行

1. 現在の通常認可・事業/AI役職/項目/期間/policyとD方式最新headを確認し、workerを起動する。AI04は取得前に拒否。
2. `managed`へmodel/監督boot/issuer boot/job/worker nonce/PID/inventory hash/scope hashを束縛し、外部台帳へ予約を確定。
3. 書込/flush失敗は予約を未解決で保持。中断要求で現在epoch/予約に結び付くchallengeと期限を作り、drainingへ移行する。
4. workerのrawを先にfenceし、buffer再flush/write拒否と登録資源closeを確認させる。監督はこの応答だけで終了とせず、自分のProcess objectをjoinしてexitcode=0を確認する。unknown_resources・観測不足・異常終了では発行者へ要求しない。
5. 固定workerが一つの同期file/bufferだけを持つという構成に限って、workerが正常終了し将来のwriteができないことを確認する。悪意あるworkerの未登録child/handle複製・kernel I/Oまで自動発見する機能はない。登録外資源をモデルへ入力した場合は拒否するが、実OSから発見する機構は未実装。
6. 監督者が限定bodyを発行者へ送り、Ed25519署名を得る。発行者故障/鍵喪失では証明を得られず予約が残る。停止完了は返さない。

証明にはprotocol、現在intent（復旧世代・予約・対象・challenge・期限・managed全体）、完全なfacts、観測digest、prior_output=possible_or_unknownを含む。既にfile/OSへ渡されたprefixや全文を取り消す保証はない。

## 5. 検証と解除

SeparatedAuthorityは新しい `release_separated_abort` だけを受け付け、登録された現在公開鍵でEd25519署名を検証する。algorithm/登録key_id/issuer boot/監督boot/現在epoch/instance/完全なfacts/期限/managed/現在intentが全一致する必要がある。署名済みでも別予約/別世代/challenge不一致は解除しない。

署名検証・現在状態検査・receipt保存・対象permit削除は模擬外部SQLiteの同一BEGIN IMMEDIATE transaction内。ローカルwriter lockも既存の制御更新中維持する。別processの同時解除は一回のみ成功し、重複証明は拒否する。停止完了要求が先なら未解決拒否、解除後なら完了可能。

二つのDBを跨ぐ単一transactionではない。外部確定後のローカル保存失敗は従来同様のhead不一致/FAULTで以降を拒否し、成功したことにしない。通常認可・D方式最新確認・事業分離は省略しない。

## 6. 旧HMAC経路・故障・鍵更新

旧 `synthetic_abort.py` は脆弱性再現と既存テストのためそのまま保持する。本番利用可能とは扱わない。新SeparatedAuthorityでは `register_abort_supervisor` と `release_verified_abort` を明示拒否する。HMAC body/macを新APIへ渡しても拒否し、互換性fallbackを作らない。旧モジュールを直接起動すれば旧試作は依然存在するため、リポジトリ全体を修復済みとは呼ばない。

鍵は発行processのメモリだけにある。強制終了/再起動では失われる。新しい発行者を旧jobへ自動紐付けせず、予約は永続台帳に残す。鍵のバックアップ/exportを試作救済に使わない。

rotationは新しいkey_id/公開鍵を代表fixtureで明示登録し、旧key_idの未消費証明を拒否する。失敗時に旧鍵を自動復活させない。鍵紛失時は新証明発行不能で停止未完了。既に正常観測後に発行された署名は、登録鍵の明示無効化/世代更新/期限切れまでは数学的に有効であり、発行processが後から停止したことを検証者が自動検知するlive heartbeatは未実装。その証明は過去に成立した中断だけを証明し、新しい会計許可ではない。即時の鍵侵害失効は信頼されたregistry更新が必要。

監督close/timeout/PID不在で予約を消さない。再起動後の旧job再調査/鍵紛失時の回復、実サービスの相互死活監視は未実装。両台帳の同時巻き戻し・信頼登録改変・同一user/host管理者侵害の限界は残る。

## 7. 実行環境とテスト

開発環境に既設の `cryptography 50.0.0` を使用。通信/依存packageのインストールは実行しない。暗号backendはtests領域だけに配置し、既存アプリ本体の外部依存ゼロを維持する。新試作を含む開発用の全体テストにはcryptographyが必要になる。未導入時にskip/fallbackせずimport失敗する。Windowsでのpackage導入は別承認と依存監査後に行う。本番の依存ファイルや起動処理は変更しない。

```bash
python3 -B -m unittest tests.test_synthetic_separated_abort -q
python3 -B -m unittest discover -s tests -q
```

テスト成功はWindows鍵保護・IPC認証・TPM非export・ネットワーク中断の実証ではない。旧鍵漏えいが成立する負の保証テストも削除しない。

## 8. 保存前レビューと次の段階

新署名protocol/encoding/公開鍵pinning、代表登録権、発行channel/capability、監督信頼と未登録資源の範囲、期限/再利用拒否、transaction/保存失敗、異常終了と永続予約、旧経路の明示拒否、cryptography依存の配布/監査をレビューする。

次段階も実装保存の別承認を必要とし、本番導入を意味しない。Windows隔離VMでは、専用service SIDと認証IPC、CNG/TPMの鍵利用/非export、任意署名要求拒否、実Job/handle唯一所有・未完了I/O、service異常終了/再起動、鍵喪失/更新/失効、管理者侵害の限界を合成データだけで実証する必要がある。

## 9. 対象テストと互換性確認

新規34件単独：8.610秒、失敗0・skip0。配置調整後、新規34件＋既存test_audit_securityの16件＝50件：11.577秒、失敗0・skip0。

初回全体918件では、暗号importをviolet_office領域に置いたことで既存の外部依存ゼロ検査が1件失敗した。検査を弱めず、新しい試作全体をtests領域へ移してアプリ本体の依存を維持した。旧ファイル/既存テストは変更していない。全体結果は後記。

配置調整後の全体：918件、63.730秒、失敗0・skip0。既存884件＋今回34件。Windows実機では未実行。負の保証テストを含むため全体成功は旧HMAC問題の解消や本番安全を意味しない。


## 10. GitHub保存前安全レビュー（2026-10-29）

判定は**隔離試作として条件付き合格**。今回確認したモデルの想定信頼境界内（固定worker、信頼された監督者、未漏えいの発行channel資格、真正な公開鍵登録、信頼されたfixture host）では、高リスクの偽造・停止保証破りは確認されなかった。OS上の不正同一userや管理者を防ぐ保証は対象外であり、実OS安全の合格ではない。

### A/B/Cの判断

- A 隔離試作としてGitHub保存：可能。ただし合成専用・旧HMAC問題が残ること・本番未接続・依存を明記し、保存対象/全差分を再確認して代表の別承認を得る。今回はcommit/pushしない。
- B Windows実機検証：破棄可能VM/合成資格/一時DB/非本番通信の範囲で次段階へ進める。実サービス/ACL/TPM/認証IPCの設定・実装には別承認が必要。Ed25519が対象CNG/TPM providerで利用できるかは未確認。別algorithmを採用する場合は新protocol/鍵登録/署名encodingのレビューが必要。
- C 本番接続：不可。OS鍵分離・相互認証・資源所有/未完了I/O・独立信頼基盤・障害復旧の実証が不足する。

### 追加の再現確認

1. 旧VerifiedLedger/OwnedOutputSupervisorで実際に発行した有効HMAC証明を、新SeparatedLedgerが拒否する。旧履歴の予約は残る。
2. 新しく稼働中のworkerに対して、現在intentと完全な偽factsを作って既知のfixture HMAC鍵で認証しても、新方式の署名検証は拒否し予約を保持する。以前の鍵読取り→HMAC偽造だけでは新公開鍵の署名を作れない。
3. 実Ed25519署名の検証直後に別processから監督無効化を試みると、現在の解除transactionが保持するwriter lockで待機する。解除後に無効化が確定し、後続証明は拒否する。無効化が先なら既存テストどおり解除拒否。
4. 外部履歴insert失敗では削除前stateがrollbackされ、実際のpermitが残る。ローカル保存失敗では外部とローカルの不一致/FAULTが残り停止完了できない。後者は二つのDBの同時rollback/全予約保存を意味しない。
5. 正常な中断観測後の署名を得てから発行processだけを停止した場合、有効な署名は期限/鍵無効化/世代更新まで解除に使用できる。workerが既に終了していることを確認した上での既知の境界であり、新たな会計出力の許可ではない。発行者死活を常時検証する機能ではない。
6. 発行channelの古いsequenceを拒否する。

### 鍵・IPC・観測の限界

署名秘密鍵exportのcommand/戻り値/ファイル保存はない。ただしcrash/lose_key/rotate等の故障注入commandは認証されたモデルchannel上に存在する。本番debug APIとして移植しない。

モデルのpeer objectやPIDだけをOS本人確認の代替にしない。capability/監督processメモリを取得した攻撃者は想定外の信頼主体侵害であり、署名を悪用し得る。public key registryを直に書き換えられるhost侵害も同様。秘密鍵の非exportだけでは防げない。

監督者はworker自己申告に加えて実Process終了を確認するが、全資源の完全な独立OS観測は未実装。unknown_resourcesは信頼したモデル入力であり、隠れたhandle/子processの発見能力を示さない。IPC資格も秘密鍵も一般userのメモリ読取りからOSで隔離したとはしない。

監査には証明/識別子/hash/固定状態を記録し、本文/金額/署名秘密鍵/channel capabilityや例外内容は書かないことをテストで確認する。エラーを将来汎用ログへ追加する際は再レビューする。既送出データの取消、両台帳同時巻き戻し、clock/鍵更新失敗/証拠喪失の可用性、暗号依存の配布・監査は残る。

元の34件：8.956秒、失敗0・skip0。追加6件を含む対象40件：10.298秒、失敗0・skip0。既存のテストは削除/skip追加/期待値変更なし。本体試作への修正なし。

全体再実行：924件、65.690秒、失敗0・skip0（既存918件＋今回レビュー6件）。Windows実機は未実行。

確認した基点との差分13ファイル：

- `docs/accounting-abort-issuer-trust-boundary-design.md`
- `docs/accounting-external-trust-deployment-spec.md`
- `docs/accounting-owned-abort-sandbox.md`
- `docs/accounting-preconnection-validation-plan.md`
- `docs/accounting-separated-abort-sandbox.md`
- `docs/accounting-verified-abort-spec.md`
- `docs/accounting-windows-isolation-spec.md`
- `tests/synthetic_separated_abort.py`
- `tests/test_abort_issuer_boundary_design.py`
- `tests/test_preconnection_safety_spec.py`
- `tests/test_synthetic_abort.py`
- `tests/test_synthetic_separated_abort.py`
- `violet_office/synthetic_abort.py`
