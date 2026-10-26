# VIOLET AI OFFICE

バイオレットのAI社員28人、ローカルタスク、原稿、承認、経費、モック連携を管理する日本語のAI社長室です。Python 3.12以上、追加ランタイム依存なし。

```sh
python3 -m violet_office.web init
python3 -m violet_office.web serve --open-browser
```

Windowsは `python3` の代わりに `py -3` を使用します。初回に代表・閲覧者の別々のパスワードを設定。ユーザー名は `representative` / `viewer`。ブラウザのアドレス欄に `http://127.0.0.1:8765/` と入力しても開けます。PC内限定で、LAN/インターネット公開用ではありません。

- [起動・デスクトップからの使い方・検証・制限](docs/phase-5.md)
- [第4段階のモック連携](docs/phase-4.md)
- [第3段階のテンプレート原稿](docs/phase-3.md)
- [設計](docs/architecture.md)

```sh
python3 -m unittest discover -v
```

実LLM、LINE、SNS、予約、外部CRMは未接続。原稿作成は固定テンプレート、担当選択はルール方式です。未計測の事業KPIを架空の値で表示しません。承認だけでは送信しません。別リポジトリの既存LINEシステムは変更しません。

第6段階の監査・運用準備:

- [監査結果・実装/未実施・本番移行可否](docs/phase-6.md)
- [日常運用・障害・バックアップ/復元](docs/runbook.md)
- [本番移行チェックリスト（未承認）](docs/production-checklist.md)
- [法令・規約の人による確認手順](docs/legal-review.md)

```sh
python3 -m violet_office.ops health --db var/office.sqlite3
python3 -m violet_office.ops backup --db var/office.sqlite3 --output var/backups/NEW.sqlite3
```

バックアップは平文で、信頼できるアクセス制限付き保管先が必要です。復元は別の新規ファイルのみで、自動再開しません。最新CVE・既存LINE本体・実API・法令の確認は未完了で、本番運用は不可です。

第7段階では「目標・定期業務」「業務レポート」を追加しました。目標未設定・未計測を保持し、日次/週次/月次のローカル集計と提案を作成できます。設定したスケジュールは初期停止で、Web起動だけでは実行しません。

- [第7段階の実装範囲・手順・テスト・残課題](docs/phase-7.md)

```sh
python3 -m violet_office.growth_cli status
python3 -m violet_office.growth_cli tick
```

常駐ワーカーは必要な場合だけ `python3 -m violet_office.growth_cli watch --interval 30` で明示起動します。計画承認と外部送信承認は別で、実モデル・外部APIは未接続です。

第8段階では「知識・改善」と社員別業務履歴を追加しました。出典・版・期限・共有範囲を確認した承認済み知識を検索し、代表の指導と評価を保存します。モデル学習は行いません。改善の自動適用は比較・承認を経た固定文章スタイルのみです。

- [第8段階の実装範囲・評価・検証・操作手順](docs/phase-8.md)
- [合成データによる7日分のサンプル](examples/phase-8/sample.md)

`python3 -m violet_office.learning_demo` で一時DBによる再現確認ができます。通常のWeb起動では本番運用や外部送信を開始しません。

第9段階では「評価・選抜」「セミナー管理」と、参加者専用画面を追加しました。自動選抜は初期停止、再案内は代表承認待ちのモック下書きまで。公式カード照合・実LINE・iPhone実機接続は未対応です。第10段階以降の仕様は変更していません。

- [第9段階の機能・互換性・試験・操作手順](docs/phase-9.md)

`python3 -m violet_office.phase9_demo` は一時DBだけで合成シナリオを実行します。`python3 -m violet_office.seminar_cli status` で再案内準備の停止/有効状態を確認できます。
