# 台帳信頼方式・停止ゲート：合成検証記録と次工程

2026-10-29。対象 `feat/accounting-recovery-gate-20261029`、HEAD `8fb269411fbafcdbe831cd455bb78f4c934f60fc`。

## 1. 今回の変更と検証範囲

新規ファイル：

- docs/accounting-trust-method-decision.md
- docs/accounting-egress-stop-spec.md
- docs/accounting-trust-validation.md
- tests/test_accounting_trust_models.py

既存8成果、アプリ本体/社員定義/設定/既存テストは無変更。本番DB/既存backup/repair試験DB、外部契約、ゲート本体接続、commit/pushは対象外です。

## 2. 方式の限界を含む合成検証

|検証|モデル/結果の意味|
|---|---|
|Aの古い正規台帳|正当なHMACを含む過去履歴はローカル検証だけでは通る。脆弱性を直したとの主張ではなく方式の限界を再現|
|Bの鍵/OS保護だけの限界|同じ鍵で保護された古いblobの認証は通る。DPAPI実機試験ではない|
|Bの保持された単調anchor|外側の最新headが保持されていれば古い台帳を拒否。台帳とanchor両方を戻すと検知不能|
|C/Dの外部最新head|保持された独立最新headと不一致の過去履歴は拒否。外部サービス自身がrollback不能という性質は模擬前提|
|改変/欠番/順序変更|履歴検証で拒否|
|署名/認証済み応答の再送|別nonce/instanceへ再利用を拒否。ただしnonceは外部サービス自身の最新性の証明ではない|
|初期登録・履歴不明・代表以外|明示登録だけ、既存ファイルは上書き不可、初期stopped。未照合/旧policy/代表以外の再承認を拒否|
|台帳消失/破損/更新途中例外|拒否し自動再作成しない。更新途中のseq/監査もROLLBACK|
|取得後・出力予約前の停止|private bufferがあっても新規予約拒否|
|出力予約後の停止|進行中handoffが終わるまで停止完了拒否。handoff seq < stop_complete seq|
|未応答writer|時間経過だけで予約を消さず、drainingを維持|
|複数プロセス更新|spawnした3プロセス×8更新が欠番/重複なしのseq 1〜24として確定|
|別プロセスの引き渡し競合|Eventで予約済みwriterを止めて停止受付、完了拒否を確認。writer終了後だけ完了、以降の旧receiptは拒否|
|個別取消|予約済み出力を待ち、新規予約拒否。別事業の承認済みscopeは維持。失効した同じpolicy scopeの再発行と、新世代を偽装した旧receiptも拒否|
|28人の役職/範囲|ROLES/FIELDSに合う区分のみ。AI04/その他禁止、他事業/期限切れ/未承認/失効/範囲外を拒否|

AuthorityModelはテスト専用のSQLiteへBEGIN IMMEDIATEで状態・予約・seq・監査を記録します。この一時DBそのものは巻き戻し耐性を持たず、「外部が最新値を正しく保持する」という前提の模擬です。単純な文字列で表現した代表主体やscopeは、本番認証・完全な通常認可の代わりではありません。既存723件の回帰と合わせて評価します。

## 3. 実際の署名による補助実験

クラウドに既に存在したcryptographyのEd25519を利用し、メモリ上で一時鍵を生成して次の3点を確認しました。秘密鍵・会計情報を保存/外部送信していません。

1. seq=1/approvedの正規署名は、seq=2/revokedが作られた後も検証に成功する。
2. 古い署名で内容だけrevokedへ変更するとInvalidSignatureになる。
3. 別途保持した最新(epoch,seq)との不一致で古い記録を拒否できる。

再現用の任意補助実験（既にcryptographyがある隔離環境のみ。自動installしない）：

```python
import json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.exceptions import InvalidSignature
encode = lambda x: json.dumps(x, sort_keys=True, separators=(',', ':')).encode()
key = Ed25519PrivateKey.generate()
old = {'instance':'SYNTHETIC-I1','epoch':1,'seq':1,'status':'approved'}
latest = {**old, 'seq':2, 'status':'revoked'}
sig = key.sign(encode(old))
key.public_key().verify(sig, encode(old))  # 成功。古いことは署名だけでは分からない。
try:
    key.public_key().verify(sig, encode(latest))
except InvalidSignature:
    pass
else:
    raise AssertionError('Changed content unexpectedly accepted')
assert (old['epoch'], old['seq']) != (latest['epoch'], latest['seq'])
```

標準の23件はstdlibのHMACによる認証モデルであり、非対称署名/DPAPI/TPMを実装するものではありません。補助実験はunittest件数に含めず、依存設定も変更しません。

## 4. 実装に進むための条件・受入試験

次の段階として、D方式のAuthorityインターフェースと障害注入可能な合成実装、brokerの出力予約/停止障壁の隔離実装へ進むことは可能です。本番基盤の実在/契約は合成実装の前提ではありませんが、模擬の成功を本番保証としません。本体への接続は今回しません。

本番へ進む前に必要な条件：

1. 代表がD/オンライン必須、停止保証の境界、外部へ出す最小メタデータ/保持/費用、障害時の許容停止時間を承認する。
2. 独立providerの強整合・単調性・registry/鍵の別管理・自身の復元手順・認証/nonce・sequence/監査保持を仕様化し、選定後に確認する。
3. Windowsサービスアカウント/ACL/認証IPC、代表再認証、PC交換/初回登録の本人確認と証拠を具体化する。現PCのDPAPI/TPM利用可否は未確認。
4. 全会計出口の一覧を確定し、broker迂回不可を検査する。旧3経路、AI27 detail/AI28 audit、issue/receive、HTTP書込、汎用成果物拒否、将来の専用報告UIを区別する。
5. API途中失敗、DB/外部headの不一致、二重要求、承認/失効/出力の全順序、外部headの古い再送、key rotation、disk full/停電、broker強制終了/再起動、回線分断を追加検証する。
6. Windows上でspawn/サービス/IPC/実socket書込・flush・切断・プロセス停止を検証し、停止完了と実書込の順序を記録する。モデルのhandoff eventを実通信の証明とは扱わない。
7. DB整合性修復、acct_task_policies/reads等の正式schema導入、会計再承認、コード配備を別承認にする。現行の位置指定INSERT/分類マーカー/旧policyの履歴を維持する。

残るリスク：外部基盤自身のrollback/管理者侵害、Windows管理者によるbroker迂回/メモリ読取り、実送信済みデータの回収不能、未分類の過去資料、同一人物の代表資格盗用、履歴不足の長期停止、将来の複数PCでの分断と停止完了遅延です。今回これらを解決済みとは報告しません。

## 5. 再検証方法

Windows Git Bashのリポジトリ作業ルートで、将来以下を実行できます（今回Windows実行なし）：

```bash
py -3 -B -m unittest tests.test_accounting_trust_models -v
py -3 -B -m unittest discover -s tests
```

どちらもテストのTemporaryDirectory/メモリだけを使い、台帳の本番初期化や本番サーバー起動を行いません。全体には既存の合成HTTPテストが含まれます。

## 6. 最終実行結果

クラウド合成環境で追加23件成功（0.252秒）、既存723件を含む全746件成功（41.489秒）、失敗0・スキップ0。HMACモデルの限界再現、外部最新head比較、認証応答の再利用拒否、個別再承認、複数プロセスの更新/出力停止競合を確認しました。Ed25519補助実験の3点も確認済みです。

本体接続・Windows実機・実socket送信の停止障壁・TPM/DPAPI・実外部providerの信頼保証を検証した結果ではありません。これらは次工程です。既存8成果はHEADとバイト一致、追跡ファイル/設定は無変更、新規4ファイルのみ。差分/空白チェック成功。コミット・プッシュは行わず停止します。
