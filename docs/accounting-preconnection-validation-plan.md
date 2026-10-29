# 本番接続前の検証計画・承認チェックリスト

2026-10-29。作業ブランチ `plan/accounting-preconnection-safety-20261029`、worktree `/workspace/violet-ai-office-preconnection-design`。
基点 `25a76039b493148615eb77a053d08749725d3875`。既存のD方式/全設計/既存テストは変更しない。今回追加するのは設計4文書とテスト内モデル1ファイルのみ。

## 1. 確認済み事項の区別

代表からのWindows報告：対象56件成功、全体802件実行・失敗0・skip1。これは過去の実機結果で、skipのテスト名/理由は未確認。802件すべて成功とは表記しない。今回の新規試験をWindowsで実行したとはしない。

現在のWindows版/ACL/起動user/ID契約/秘密鍵保管/DB実path/skip理由は未確認。本番ファイル・既存backup・修復試験DBを参照して確認したわけではない。次工程で承認された読み取りinventoryを行う。

外部サービスとの通信/価格照会/契約、Windows実設定変更、既存アプリへの接続、DB修復、commit/pushは今回しない。

## 2. 仕様と合成試験の対応

|要件|今回の合成検証|実装後の受入試験|
|---|---|---|
|外部head不一致/rollback|Aだけの巻き戻し、W不一致、old nonce、両側rollbackの限界|実providerの強整合・restore権限・復元演習、登録系統の偽装拒否|
|外部障害/オフライン|照合不能で拒否|timeout/接続断/再接続/commit不明、cache fallbackなし|
|代表本人確認|subject/issuer/audience/MFA/nonce/scope/expiry/偽署名の拒否|実IDP/鍵失効/本人再認証/旧assertion再利用・CSRF回帰|
|IPC認証|模擬kernel tokenのSID/logon/相手service/nonce不一致拒否|実SSPI/pipe DACL/impersonation失敗、同SIDの別worker、remote client、偽server|
|service異常終了|証拠なしで再開/解除不可|job/handle/OS終了、PC再起動、監督crash直前/直後の各故障注入|
|flush失敗後の再送|既存17件を維持。新モデルで既知rawのfence成功/失敗を追加|実Windows buffered/socket I/O、CancelIoEx完了、close/retry/hidden buffer/handle複製|
|安全中断の成功/失敗|必要観測不足・版/対象違い・署名不正は拒否。条件一致後だけ解除|実監督観測からの証拠生成、鍵失効、機器/起動世代、公開済み成果物の区別|
|複数processの解除/停止|2processの重複解除で一回の状態遷移と監査。解除前は停止不可|全参加broker、競合cancel/finish/revoke、分断時に完了しない|
|永続化/途中失敗|解除/audit失敗rollback、再open時の未解決維持|A/W/local各commit点でのcrash、disk full、冪等receipt、分岐/欠番|
|AI04/28役職/事業分離|ROLES/FIELDSで全28人、inactive/他事業拒否。既存802件を維持|実adapterで通常認可・権限変更とのfence、代表画面/非会計維持|

今回の22件は仕様モデルの試験であり、OS認証・cloud・本体の実装完了ではない。mockの署名鍵は同じ公開fixture、kernel token/OS観測も入力前提。これを本番の信頼証明に昇格させない。

## 3. 実装の優先順位

**P0 信頼境界と承認**：外部A/W管理者・本人確認・鍵・メタデータ/費用上限・停止保証の境界、Windows account/DB所有、HTTP限定abort範囲を確定。実機inventory/skip理由の確認は別の読み取り承認で行う。

**P1 合成実装**：隔離コードで署名検証/本人確認/IPC adapter interfaceを分離し、delivery単位の監督所有とabort evidence validator、永続状態/冪等receiptを実装する。本番経路には接続しない。

**P2 Windows隔離検証**：別の検証用account/一時DB/合成値でservice SID、DACL、key storage、pipe/job/handle、process termination、socket retryを検証する。設定変更も事前承認が必要。

**P3 外部基盤の検証**：別承認したsandbox契約/予算の範囲でのみ、実providerの整合性・復旧・鍵rotation・failure injectionを確認する。本番値は送らない。

**P4 接続と互換性の検証**：既存read_for_ai(finance_read/kpi_read)、business-task-kpis、detail/audit、issue/receive、HTTP最終write、代表画面/非会計業務のadapterを合成環境で接続し、全出口を列挙する。現StoreのDB直接openを残す境界問題を解決する。

**P5 本番手順の別承認**：実DB診断/最新backup/復旧点/整合性、正式schema、個別policy、停止/復旧/rollback/限定再開を別々に承認する。bf_profiles修復と会計再承認を混ぜない。

## 4. 代表が承認する事項

- [ ] A/W/provider/リージョン、独立管理・復旧担当、break-glass、同時侵害の許容範囲。
- [ ] 送信する最小メタデータ、監査/証拠の保持、削除/保管費、月額上限、認証契約と対応担当。
- [ ] オフライン会計拒否、未解決時の長期停止、許容停止時間/データ損失、障害時の連絡/判断者。
- [ ] Windowsのaccount/service SID、DB/台帳/鍵/backup ACL、一般AIの実行主体、DB所有移行と業務影響。
- [ ] 本人再認証/代表assertion、配備・runtime鍵権限分離、PC交換/再installの手順。
- [ ] 初期abort対象を限定HTTPにすること、信頼する監督観測、既送出データの回収不能という保証境界。
- [ ] 検証用Windows設定変更、外部sandbox契約/通信、将来の本体adapter実装のそれぞれの実施範囲。
- [ ] skip1件の名前・理由、今回追加試験とWindows実機の未検証項目、本番適用前の合否基準。

今回は上記の本番実施を承認されたとは扱わない。

## 5. 次の段階の中止条件

本人/IPC/外部head/署名が不明、観測不足、未説明handle/child、writer残存、未確認I/O、双方向commit不明、監査保存不能、skipが重要保護を未検証なら止める。close/TTL/代表確認ボタンで帳尻を合わせない。

外部A/W/rootが同時に巻き戻れば検出できない限界、OS/監督者侵害、既送出データ、private memoryの物理消去不能、過去の未分類資料、可用性/運用負担は残る。新モデルの成功を本番導入可能と判定しない。

## 6. 再実行

Windows Git Bashの新しい設計worktreeで、合成検証だけを実行する方法（今回のWindows実行は未実施）：

```bash
py -3 -B -m unittest tests.test_preconnection_safety_spec -v
py -3 -B -m unittest discover -s tests
```

外部A/W・Windows token・OS証拠はモデルであり、実通信/実設定変更を必要としない。既存全体テストに含まれるローカル合成HTTP試験を除き、新規試験に通信機能はない。

## 7. 今回の合成実行結果と保存状態

クラウドで新規22件成功（0.190秒）、既存802件相当を含む全824件成功（45.008秒）、失敗0・スキップ0。外部head/rollback・障害、本人確認/IPC条件、abort証拠不足/対象違い、解除rollback/再open/冪等性/2process競合、限定buffer fenceの成功/失敗、28役職・事業分離を検証した。

Windowsの今回の新規22件は未実行。OS/実providerをモデルで代用した結果であり、本番の中断解除やWindows権限分離が完成した証拠ではない。実際のD方式試作に中断解除APIを追加していない。

新規5ファイルだけが未コミット。基点から既存追跡ファイル/設定/試作コード/テストの差分なし、空白チェック成功。元worktreeを保持し、commit/push・本番操作を行わず停止する。
