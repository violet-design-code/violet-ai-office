# ベネシードディーラー事業：事業基盤の復旧準備・最終計画

作成日：2026-10-29（日本時間）。本書は準備計画であり、本番適用の許可ではありません。実DBへの修復コマンド・SQL・自動適用機能は作成していません。GitHubへの反映も今回未実施です。

## 0. 現在の判断と停止条件

Windowsで過去に確認された事業1件・プロフィール0件・担当28件・外部キー不整合28件と、試験後の1行追加・不整合0件・他データ不変は有効な過去の証拠です。現在の業務DBの状態を保証しません。過去の `var/validation-d04179fc65ea4b97b2a8749f7ae24f5f.sqlite3` と `repair-trials/trial-profile-001.sqlite3` は保存し、再利用・上書きしません。

次の場合は進めず、代表へ差分と理由を報告します。

- 実際に参照されるDBの絶対パスが特定できない、複数DB・複数起動元がある。
- アプリ、自動処理、DB編集ツールが停止していない。または停止確認ができない。
- 非空のWAL/rollback journalが残っている。削除・強制チェックポイント・journal_mode変更で回避しない。
- integrity_checkがokでない。事業数・担当数・ID・社員集合・外部キーエラーが過去の前提と違う。
- categoryや概要の内容、enabled=1、ai_enforced=1が実際の事業状態・代表の方針と矛盾する。
- 担当変更、会計ポリシー変更、未知の追加スキーマ・トリガーの影響が判明する。
- 予期しない権限増加/縮小、未完了業務/レビュー停止、バックアップ不一致がある。

クラウドにはWindowsの最新実DBがないため、上記は今回未確認です。Windows全632件OK(skipped=1)も過去の結果として扱い、skip対象・理由を記録します。

## 1. 稼働中アプリの実DB絶対パスを調べる（読み取りのみ）

最初はSQLiteを開かず、起動元とOS上のプロセス・開いたファイルを確認します。

Git Bashから以下で候補プロセスの起動コマンドを確認できます。パスとPIDの診断専用です。出力に秘密値が含まれる場合は共有しません。

```bash
powershell.exe -NoProfile -Command 'Get-CimInstance Win32_Process | Where-Object { $_.Name -match "^(python|pythonw|py)\.exe$" } | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine | Format-List'
```

`violet_office.web serve` の実プロセスを特定し、--dbの有無と値、起動ショートカット/バッチ/サービス/タスクスケジューラの作業フォルダーを確認します。現在のコードの既定値は `var/office.sqlite3` で、相対パスはプロセスの作業フォルダー基準です。今回のGit Bashのpwdから推定してはいけません。

標準の `scripts/start_office.bat` はリポジトリのルートにcdして起動します。ただし実際の起動元がこのバッチであることを確認してください。直接起動、別チェックアウト、タスクスケジューラ起動は別です。

可能ならWindows Process Explorer等の既に利用可能な管理ツールで対象PIDの開いたDBファイルの絶対パスを確認します。SQLite接続が短時間で閉じるためハンドルが見つからない場合、通常の読み取り画面表示時に再確認します。ただし画面GETでも既存アプリが初期化・監査記録を行うため「DBを一切変更しない診断」とは区別します。今回の診断のために画面操作やサーバー再起動は不要です。起動設定とハンドルの証拠が揃わなければパス特定を未確認として停止します。

確認した絶対パスを記録します。以下は例であり、自動検出値ではありません。

```bash
export LIVE_DB='C:/実際に確認したパス/violet-ai-office/var/office.sqlite3'
```

## 2. 安全な停止

1. 代表が保守時間を確保し、他の操作担当へ新規入力・担当変更・承認操作の停止を依頼する。
2. 実行中の内部タスク・レビュー・未送信処理の状態を記録する。外部送信・支払い・配信は起動しない。
3. 別起動された自動処理、Windowsタスクスケジューラやサービスの起動元を特定し、代表が承認した停止操作で止める。再起動トリガーも保守時間中に動かないことを確認する。DBのスケジュール設定は変更しない。
4. フォアグラウンドのweb serveは元のコンソールでCtrl+Cを使い通常終了する。強制kill、タスク一括終了、DB内フラグの変更を通常手順にしない。
5. Python子プロセス、DBブラウザー、追加のコマンドプロンプト、自動再起動が残っていないことを確認する。ブラウザーを閉じるだけではサーバーは止まらない。
6. 対象DBを開く他プロセスがないことを確認する。起動中/停止不明なら補助を使わない。

`--writers-stopped` は操作者の明示確認であって、停止を自動検出する保証ではありません。非空WALが残ったら停止し、SQLiteのWALを含むバックアップ方法を別途検討・承認する。WALを削除して単独DB化しないでください。

## 3. 読み取り診断・変更前スナップショット

追加した `recovery_prepare.py` は停止済み単独DBだけを `mode=ro&immutable=1` とquery_onlyで開きます。元DBにStore/Office初期化、移行、PRAGMA foreign_keys=OFF、チェックポイントは実行しません。非空-wal/-journalがあれば拒否します。

出力には次を含みます。

- 正規化された絶対パス、DBファイルSHA-256、社員定義ファイルSHA-256。
- integrity_check、外部キー不整合のテーブル/行参照/参照先。
- 全テーブルの行数と型・重複数を含む行集合の指紋、スキーマ指紋。
- 事業本体、bf_profiles、担当28件の全列（active、permissions、revision等）。

顧客・会計明細の本文は診断レポートに出さず、指紋だけを記録します。ただし事業概要と担当メモは出るため、レポートはローカルのアクセス制限された機密資料として扱い、Gitや外部チャットへ全文投稿しません。

リポジトリ直下で新規の一意な作業名を用意します。既存の実DB・バックアップ・過去検証DBは上書きしません。

```bash
set -o noclobber
export PYTHONIOENCODING=utf-8
export PREP_ID="$(py -3 -c 'from uuid import uuid4; print(uuid4().hex, end="")')"
mkdir -p var/recovery-preparation var/backups repair-trials
export PRE_REPORT="var/recovery-preparation/before-$PREP_ID.json"
export SNAPSHOT_DB="var/backups/pre-foundation-$PREP_ID.sqlite3"
export VALIDATION_DB="var/validation-final-$PREP_ID.sqlite3"
export TRIAL_DB="repair-trials/trial-final-$PREP_ID.sqlite3"
py -3 -B -m violet_office.recovery_prepare inspect \
  --source "$LIVE_DB" --writers-stopped > "$PRE_REPORT"
```

各コマンドの終了コードを確認し、非0なら次へ進みません。失敗時の空レポートも上書きせず新しいPREP_IDで再試行します。

前提チェック：登録事業1件、bf_profiles0件、bf_ai_assignments28件、担当の社員集合AI01〜AI28、すべて同じ既存事業ID、不整合28件がすべてbf_ai_assignments→bf_profilesであること。実際のactiveとpermissionsを28人分確認し、過去と違えば代表確認へ戻します。

## 4. 最新の正式SQLiteバックアップ

重要：既存 `ops.backup` はhealthyを要求するため、今回の既知の外部キー不整合でも拒否します。既存機能は変更せず、準備用バックアップ補助を使います。

```bash
py -3 -B -m violet_office.recovery_prepare backup \
  --source "$LIVE_DB" --output "$SNAPSHOT_DB" --writers-stopped \
  > "var/recovery-preparation/backup-$PREP_ID.json"
py -3 -B -m violet_office.recovery_prepare inspect \
  --source "$SNAPSHOT_DB" --writers-stopped \
  > "var/recovery-preparation/snapshot-$PREP_ID.json"
```

Python sqlite3のConnection.backup（SQLite Online Backup API）で新規ファイルへ複製します。今回は安全条件を狭め、停止済み・単独DBだけを扱います。出力の排他的新規作成、全テーブル/スキーマの一致、integrity_check、外部キー不整合の同一性、入力ファイルSHA-256不変を検証します。バックアップ内に既存不整合28件が残るのが正しい証拠保存であり、隠す/修復する処理はありません。

バックアップは暗号化されません。Windowsのフォルダー権限・保存先・別媒体保管・SHA-256を確認します。ハッシュは改ざん防止の署名ではありません。失敗した新規出力は証拠として残す仕様なので、成功報告なしのファイルを有効バックアップとして使わないでください。

入力とバックアップのファイルハッシュはSQLiteのページ配置等で違う場合があります。比較すべきものはスキーマ・全テーブルの行集合・外部キー検査結果です。保存後のバックアップ自身のハッシュは、その後の改変検出に使います。

## 5. 最新バックアップから新しい検証用DBと試験出力を作る

```bash
py -3 -B -m violet_office.recovery_prepare backup \
  --source "$SNAPSHOT_DB" --output "$VALIDATION_DB" --writers-stopped \
  > "var/recovery-preparation/validation-copy-$PREP_ID.json"
py -3 -B -m violet_office.repair_trial \
  --validation-db "$VALIDATION_DB" --output "$TRIAL_DB" \
  --category 'ベネシードディーラー事業' \
  > "var/recovery-preparation/trial-$PREP_ID.json"
py -3 -B -m violet_office.recovery_prepare inspect \
  --source "$TRIAL_DB" --writers-stopped \
  > "var/recovery-preparation/candidate-$PREP_ID.json"
```

repair_trialはbackupを含むパスを入力として拒否するため、バックアップから別の新規validationパスへ正式バックアップ機構で複製して渡します。試験DBを実DBへコピー・置換しません。

合格条件：added_profiles=1、all_other_data_unchanged=true、schema_unchanged=true、foreign_key_errors_before=28、after=0、integrity=ok。既存repair_trialはsqlite_masterと全テーブルを行集合・重複数で比較し、1行以外の差分があれば拒否します。acct_*が存在する場合も比較対象です。

候補行は既存id、既存services、category=ベネシードディーラー事業、started_on=NULL、enabled=1、ai_enforced=1、revision=1。categoryは既存の文字列検証を通すが、業務上の承認は別です。

補助レポートからも、試験前後の差分を読み取りだけで確認できます。

```bash
py -3 - "$PRE_REPORT" "var/recovery-preparation/candidate-$PREP_ID.json" <<'PYCOMPARE'
import json, sys
from pathlib import Path
a, b = [json.loads(Path(p).read_text(encoding='utf-8')) for p in sys.argv[1:]]
assert a['integrity'] == b['integrity'] == ['ok']
assert a['schema_sha256'] == b['schema_sha256']
assert a['agents_config_sha256'] == b['agents_config_sha256']
assert set(a['tables']) == set(b['tables'])
assert all(a['tables'][t] == b['tables'][t] for t in a['tables'] if t != 'bf_profiles')
assert a['tables']['bf_profiles']['rows'] == 0 and b['tables']['bf_profiles']['rows'] == 1
assert len(a['foreign_key_violations']) == 28 and b['foreign_key_violations'] == []
assert len(a['portfolio_businesses']) == 1
business = a['portfolio_businesses'][0]
assert b['bf_profiles'] == [dict(business_id=business['id'], overview=business['services'],
    category='ベネシードディーラー事業', started_on=None, enabled=1, ai_enforced=1, revision=1)]
print('Candidate has exactly the expected profile change; other data fingerprints match.')
PYCOMPARE
```

この段階で元実DBを再診断し、beforeと全指紋が一致することも確認します。保守中に変更が起きた場合は試験を無効として停止します。

```bash
py -3 -B -m violet_office.recovery_prepare inspect \
  --source "$LIVE_DB" --writers-stopped \
  > "var/recovery-preparation/source-recheck-$PREP_ID.json"
```

beforeとsource-recheckのJSONは同じコード・同じ停止期間なら全体一致を期待します。違えば適用準備は不成立です。

## 6. 権限・既存業務・会計閲覧への影響

permission_changesを全行確認します。プロフィール欠落時のstrict=Falseの旧許可動作が、ai_enforced=1で担当activeとpermissionsの検査へ切り替わります。workのtrue→falseは既存作業/独立レビューの停止要因です。strict=Trueのfalse→trueは、既存の担当権限に基づく参照許可の復旧ですが、不要なfinance_readがあれば開示範囲の問題になります。担当設定をこの復旧に便乗して修正しません。

blocked_pending_work_or_review=[]はプログラムが検査するportfolio_tasks/wf_tasks/s14_workの該当未完了タスクと定義上のレビュー担当で権限不足を検出しなかった意味です。対象0件でも空配列になり、全経路・将来業務を保証しません。検査対象の件数、28人のactive/permissions、実レビュー担当を確認します。

会計第1段階は独立機能で、通常起動でacct_*を作りません。既存finance_readは変更しません。ただし独立機能でもbf_profiles.enabledを確認するため、プロフィール追加によって、有効な承認済み会計ポリシーの参照が可能になる場合があります。acct_*の行不変だけで「有効権限も不変」とは判断しません。承認済みポリシー、対象事業・社員・期間・期限と受領者を確認します。acct_*がなければ、この復旧のために初期化しません。

## 7. 担当変更フォームと代表承認の確認

表示条件は、代表として認証、当該事業を選択、同じbusiness_idのbf_profiles行が存在することです。事業担当者には代表用変更フォームを出さず、未選択時にも出ません。存在確認だけの条件であり、行追加後の画面表示を事前に断定しません。

まず合成テストを実行します。

```bash
py -3 -B -m unittest tests.test_recovery_prepare tests.test_repair_trial tests.test_accounting_access tests.test_business_management -v
py -3 -B -m unittest discover -s tests
```

UIの実データ検証は、修復試験出力そのものではなく、さらに新しい使い捨てのUI専用コピーで行います。バックアップ補助で新規パスへ複製し、そのコピーだけを別ポート・明示的な絶対--dbパスで開く手順を代表と確認します。ここでは実DBサーバー起動コマンドを提供しません。

画面GETでもStore/Office初期化や監査が起きます。UI試験コピーは厳密な1行差分検証の証拠DBと分離してください。28人のカード・担当業務/範囲/限定権限のフォーム、保存前確認→承認（まだ未適用）→別適用、CSRF拒否、他事業/非代表の拒否を確認します。担当変更の実適用試験は合成DBのみで行い、実データコピー上では表示確認に留めます。本番の外部送信・支払・自動配信は起動しません。

## 8. 将来の本番適用と事後検証（今回未許可）

予定する論理差分はbf_profilesの候補1行のみです。その他のスキーマ・テーブル・担当・社員定義の差分は許可しません。物理DBファイルのハッシュは行追加で変わります。

承認後に別途開発・レビューする適用手段には、実パス/事業ID/最新スナップショットの一致、外部キー有効のトランザクション、1行のみの差分検証、エラー時のコミット前ロールバックが必要です。本書にはその修復コマンド・SQLを含めません。

将来、コミット後もアプリ再開前に独立した読み取りでintegrity=ok、FK=0、候補行一致、全指紋比較で他データ不変、社員定義ハッシュ不変、permission_changes承認範囲内を確認します。停止状態では画面確認できないため、厳密なDB比較を先に完了し、その後に別途承認された限定再開で表示確認をします。アプリ初期化・監査による差分を修復の差分に混ぜません。

## 9. 失敗時のロールバック計画

- コミット前の失敗：将来の適用処理がトランザクションをロールバックする。アプリは止めたまま、再診断で元スナップショット一致を確認する。
- コミット後の異常：業務再開を止め、失敗DBと付随ファイルを保全する。バックアップ・実DBを上書きせず、試験出力も本番へ置換しない。
- 既存ops.restoreはセッション破棄、担当active=0、revision変更、承認待ち化、各種停止などを行い、今回の担当不変条件に適合しません。無断使用しません。
- 別途代表承認後、正規の修復前バックアップから新規の隔離パスへ正式バックアップ機構で再複製し、全データ・スキーマ・FKの一致を確認する計画とします。実DBの置換ではなく、保全した元DBを残したままアプリの参照先を切り替える方式の可否を確認します。切り替え・起動は今回実行しません。
- 修復前状態へ戻せば外部キー不整合28件とフォーム未表示も戻ります。復元後も業務再開せず代表判断へ戻します。再開後に発生した更新は修復前バックアップには含まれないため、復帰時点と新規更新の扱いは別承認です。
- 1行削除による自動取り消しは作りません。新しい参照行や業務更新が生じた後には安全でない可能性があります。

## 10. 代表承認チェックリスト

- [ ] 実稼働DB絶対パス、起動元、全書き込み処理の停止と保守時間。
- [ ] 最新診断の事業/担当/ID/FK前提、バックアップ成功・保管先・復元可能性。
- [ ] 候補category、servicesの概要利用、NULL開始日、enabled=1、ai_enforced=1、revision=1。
- [ ] 28人分の権限判定差分、レビューを含む既存業務影響、会計ポリシーへの実効的影響。
- [ ] 最新コピーでの1行差分・FK0件・全データ保持、フォーム表示/承認機構の合成試験。
- [ ] 今後作成する適用手段の仕様とレビュー。試験DBの実DB置換は不可。
- [ ] エラー停止条件、バックアップからの隔離復元、参照先切替、再開/再承認の責任者。
- [ ] 実DB適用と限定再開の明示的な別承認。承認がない現在は準備で停止する。

## 今回のクラウド検証記録

合成データのみで全637件成功（31.880秒）。新規準備テスト5件には、外部キー不整合を保持する正式バックアップ、全データ一致、元ファイル不変、上書き/ハードリンク/WAL/journal拒否、acct_*を持つ修復試験DBの会計テーブル不変確認を含みます。既存repair_trialとaccounting_access本体は変更していません。Windows最新実DBの診断・停止・バックアップ・修復は今回未実施です。
