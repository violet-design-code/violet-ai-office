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
