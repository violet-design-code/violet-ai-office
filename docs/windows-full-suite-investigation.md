# Windows全体テスト：起動・終了処理と表示月の調査

2026-10-30（日本時間）。基点 `741e26be065b4f355c68980650d2efb2b9c93692`。
作業ブランチ `fix/windows-full-suite-lifecycle-20261030`。
本番DB・backup・修復試験DB・社員定義/担当/権限・本番接続・サービス/ACL/TPM設定は変更しない。

## 確認した事実と未確定事項

Windows Python3.12の報告は925件（成功916、失敗1、エラー7、skip1）。関連83件単独は成功。7件の詳細Tracebackは未入手であり、7件のすべてを原因特定/修正済みとは判定しない。今回の実行環境はLinux Python3.12で、Windows実機では未実行。

- 確認済み：発行processの初回登録待ちも通常応答用のtimeout=1秒を使用していた。Windows spawnではPython/テストモジュール/cryptographyの初期化が必要。起動とコマンド応答に同じ短期deadlineを使う問題を、起動遅延を注入した合成テストで検証した。
- 確認済み：発行者constructorの登録待ち失敗時にprocess/pipeのcleanupがなく、setUp失敗時にtearDownも呼ばれない。残存processが後続テストへ負荷を残す可能性がある。cleanupの欠落自体はコードで確認できるが、報告された6エラーの直接原因かはTraceback未確認。
- 仮説：全体テスト時のspawn/暗号初期化遅延や残存processの蓄積が、単独実行との差へ影響した。実Windowsの起動時間/プロセス数/エラー位置を再確認する必要がある。
- 未確定：Synthetic recovery service unavailableの1件。SQLite/OS/状態形式のエラーを同じ文言に変換していたため、busy、open失敗、I/O、欠損・不整合等を報告文だけで区別できない。DBロックやファイル未closeと断定しない。
- 確認済み：セミナーは試験日+3日で作成されるが、画面は現在月が既定。月跨ぎで検査対象のtitleが画面に現れなくなり、HTML escapingのassertionが失敗する。

## 修正範囲

- `tests/synthetic_separated_abort.py`：起動登録待ち15秒と通常応答timeoutを分離。有限のready/death待ち、constructor失敗時のpipe閉鎖・子process回収。コマンドtimeout後はchannelを使い回さず停止し、遅延応答の誤再利用を防ぐ。
- `violet_office/synthetic_abort.py`：共通のpipe/process sentinel待ちを追加。固定workerと旧試作の証明発行processにも起動専用の有限budgetを使う。タイムアウト・process死亡を中断証明として扱わない。
- `tests/test_synthetic_separated_abort.py`：addCleanupを用い、setUp途中の失敗でも、監督者→realmの順序で回収する。署名/権限の期待値は変更しない。
- `tests/test_seminar_support.py`：生成したセミナーのJST開催月をGETに明示。認証/CSRF/XSS/事業分離のassertionを維持する。画面本体を変更しない。
- `violet_office/synthetic_recovery.py`：接続/rollback/close、busy timeout=5秒、整合性検証と拒否は維持し、固定診断カテゴリだけをUnavailableへ付ける。SQL/実パス/例外本文を出さない。SQLiteの1件に対する修復ではなく原因特定用の補助。
- `tests/test_windows_suite_lifecycle.py`：新規8件の回帰。意図的な有限のEvent待ちは遅い/応答しない子processの故障注入のみ。実装にsleep/retryは追加していない。

安全条件は維持：証明/HMAC fallback、公開鍵登録、署名検証、予約/receiptの原子性、停止完了、AI04/28役職/事業分離を緩めない。起動budgetを過ぎても成功にしない。予約のTTL解除、cleanup目的の強制解除、無制限retryなし。署名期限60秒は延長していない。

## 検証

新規8件：遅いspawnと起動専用budget、起動timeout時の子process回収、子process異常終了、コマンドtimeout後のchannel廃棄、起動/応答budget分離、SQLite busy診断の機密非出力、外部authority open失敗の分類、月末/年末/うるう年でも既存セミナーsecurity assertionが成立すること。

Linuxの新規8件は成功。関連163件は31.058秒で成功（失敗0・error0・skip0）。全体結果は後記。テスト削除・skip追加はしていない。

## Windows再確認

修正を取り込んだ隔離作業フォルダーで、既設のPython3.12/cryptographyを使用する。アプリは起動せず、本番データをコピーしない。

```bash
py -3 --version
py -3 -B -m unittest tests.test_windows_suite_lifecycle -v
py -3 -B -m unittest tests.test_synthetic_separated_abort tests.test_synthetic_abort tests.test_synthetic_recovery tests.test_seminar_support -v > windows-related-tests.log 2>&1
py -3 -B -m unittest discover -s tests -v > windows-full-tests.log 2>&1
```

全体は既存925件+新規8件=933件。Windowsで既存のskipがある場合は、理由と件数をそのまま報告し、成功に読み替えない。エラーが残る場合は、テスト名・Traceback・Python version・通常/起動timeout位置・新しい固定診断カテゴリを共有する。PID/process死亡は中断証明にしない。ログ中の個人情報や実パスは共有前に確認する。

新診断例：`[local_ledger:sqlite_busy]`、`[mock_authority:sqlite_open]`、`[local_ledger:filesystem_in_use]`、`[mock_authority:state_format_error]`。実際のSQLite例外コードが取得できなければsqlite_errorとなる。特定を待たずにbusy timeoutやretryを増やさない。

## 残るリスク

Windowsの6件の直接原因とSQLiteの1件の原因は、実機ログ/再実行で確定する必要がある。新しい起動budget15秒も絶対の実行時間保証ではなく、超過すれば拒否する。実Windowsの鍵保護/IPC/handle検査や本番導入の安全性を実証した変更ではない。

## 全体実行結果

Linux Python3.12：933件、75.341秒、失敗0・error0・skip0。既存925件を維持し、新規8件を追加した。これはWindows再検証の結果ではない。
