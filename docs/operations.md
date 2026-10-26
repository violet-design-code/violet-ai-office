# ローカル運用

必要条件: Python 3.12以上。外部依存・認証情報なし。既存チェックアウトを使用し、明示指示なしにworktreeを作成しない。

```sh
cd /workspace/violet-ai-office
python3 -m unittest discover -v
python3 -m violet_office agents
python3 -m violet_office init
python3 -m violet_office status
```

initは既存データを消さず、statusは本文を表示しない。サービスの常駐は不要。テストは一時DBだけを利用。テスト前の本番DB消去は不要かつ禁止。

タスク管理はStoreのPython APIから匿名化データのみ使用する。APIの例:

```python
from violet_office.store import Store
s = Store('var/office.sqlite3')
try:
    task = s.create_task('draft-example-v1', 'AI09', {'brief': '一般的な挨拶文の下書き'}, action='sns_post')
    # ここからの処理は段階2の実行器が担当する。現段階は保存基盤のみ。
finally:
    s.close()
```

代表承認は信頼されたローカル呼出のみ。外部向け成果物を承認しても送信せず、manual_execution_pendingとなる。手動実行済みの確認・送信アダプターは未実装。状態を直接書き換えて自動送信につなげない。

異常時: statusと定型エラーコードを調査し、本文・資格情報をログに貼らない。期限切れはStore.expireで回収。再試行上限に達した場合は原因確認後に新規キーで依頼。予算不足は失敗を繰り返さず代表が上限と方針を決定する。DBのバックアップはプロセス停止またはSQLite backup APIでWALを含む整合性を保つ。実顧客投入前に復元試験と保持・削除方針を追加する。

必要な次の情報: 既存LINEリポジトリ violet-benessseed-line-ai の連携仕様（秘密値不要、既存DBは変更しない）、公式規約、予約/SNSサービスの選定、LLMプロバイダーと予算。認証情報は第4段階で方式確認後に安全な環境設定へ登録し、チャットには送らない。
