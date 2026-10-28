# bf_profiles 修復候補のオフライン検証（実DBへの適用不可）

このツールはWindowsで報告された「事業1件、bf_profiles 0件、同じ事業のAI01〜AI28の担当28件、担当からbf_profilesへの外部キー不整合28件」を検証します。条件が一致しない入力は拒否します。WindowsのDB自体を開発時に検証したものではありません。

## 動作と境界

- 入力は検証用DBだけです。SQLite URIの `mode=ro&immutable=1` で読み取り、メモリ内にSQLite backup APIで複製します。入力の初期化・移行・修復はしません。
- 入力は停止済みの単独ファイルである必要があります。非空の `-wal` がある場合は拒否します。稼働中のDBやWALを落とした単純コピーを入力しないでください。既存のSQLiteバックアップから作成済みの検証用DBを使い、実行中は他のプログラムで開かないでください。
- 出力は新規の `repair-trials/trial-*.sqlite3` に限定します。既存ファイル、同一パス、存在するハードリンク、シンボリックリンクの上書きを拒否します。パスは正規化して検査します。
- `office.sqlite3` というファイル名、およびパス要素に `backup` を含む入力・出力は拒否します。任意の名前に改名された実DBを内容だけで識別することはできません。`--validation-db` には必ず作成済み検証用DBを指定してください。
- `bf_profiles` に1行だけ追加します。business_id・overviewは既存事業のid・services、categoryは明示入力、started_onは不明のためNULL、enabled=1、ai_enforced=1、revision=1です。事業の状態・確認フラグは変更しません。
- categoryは既存の `portfolio.text(value,120)` を使用します。固定の分類列挙ではありません。空白のみ、長すぎる文字列、機密情報など既存検証が拒否する値は使用できません。業務上の分類は代表が選択してください。
- 全テーブルを重複数を含む行集合で比較し、許可する差分を上記の1行だけに限定します。sqlite_masterも比較し、スキーマ・他テーブル・担当28件の変更を拒否します。入力・出力のintegrity_checkと修復後のforeign_key_checkも確認します。全データをメモリに保持するため、十分なメモリが必要です。
- 既存Store/Officeの初期化やサーバー起動、マイグレーションは修復ツールから呼びません。修復候補DBを既存アプリで開くことは、このツールの全データ不変保証の対象外です。

## ai_enforced=1 の影響

既存コードはプロフィールがない場合、内部業務の一部で旧来の許可動作をします。追加後は担当のactiveとpermissionsを使います。無効な担当・work権限のない担当や独立レビュー担当は既存タスクを進められなくなる場合があります。権限を自動追加して回避する処理はありません。

JSONの `permission_changes` は28人×3権限のstrict有無について修復前後を比較した結果です。`blocked_pending_work_or_review` はportfolio_tasks / wf_tasks / s14_workに紐づく未完了タスクの実行者・社員定義上のレビュー担当について、work権限不足を列挙します。実際の業務実行、全経路の動的実行、外部接続の検証ではありません。停止状態など他の業務条件は既存コードに従います。変更があること自体を成功・安全の証明として扱わず、Windows側で内容を確認してください。

## Windows / Git Bash

リポジトリのルートで実行します。標準ライブラリ以外の追加インストールは不要です。以下の入力パスとcategoryは実際の検証用DB・代表確認の分類に置き換えてください。

```bash
cd /c/your/path/violet-ai-office
py -3 --version
py -3 -B -m unittest tests.test_repair_trial tests.test_business_foundation tests.test_business_readiness tests.test_business_management -v
mkdir -p repair-trials
py -3 -B -m violet_office.repair_trial \
  --validation-db 'C:/your/validation/office-validation.sqlite3' \
  --output 'repair-trials/trial-profile-001.sqlite3' \
  --category '代表が確認した分類'
```

`py` がない環境ではPython 3の `python` に置き換えてください。成功時はJSONを表示します。失敗時は終了コード1と `Trial refused` を表示します。再実行時は出力名を `trial-profile-002.sqlite3` など未使用名にしてください。出力DBはローカルの機密データとして扱い、Gitには登録しないでください。

確認項目は `added_profiles: 1`、`all_other_data_unchanged: true`、`schema_unchanged: true`、外部キー不整合28→0、および権限差分・既存業務への影響です。`windows_validation_completed` は常にfalseで、このプログラムがWindows検証完了を認定することはありません。

試験DBを実DBへコピー、置換、restoreする操作はこの手順に含みません。実DB適用コマンド・適用モードはありません。今回の結果を代表が確認するところで停止してください。

## 自動テスト

合成DBで既知の不整合を再現し、1行のみの追加、全テーブル・スキーマ比較、入力ハッシュ不変、28人定義のバイト不変、外部キー、権限の縮小、事業分離、既存タスク・レビューの権限、修復後の担当変更フォームと確認→承認→別適用、誤った入力・上書き・WAL・副作用トリガーの拒否を確認します。フォーム操作のテストも一時的な合成DBだけに実行します。

```bash
py -3 -B -m unittest discover -s tests
```

これはクラウドでの合成テスト用コマンドでもあります。Windowsの実データによる試験結果とは区別してください。
