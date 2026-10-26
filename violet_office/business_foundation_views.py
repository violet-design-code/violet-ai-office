"""Minimal foundation settings. Full allocation console remains phase20 work."""
import json
from uuid import uuid4
from .sales_views import scope,p
from .portfolio_views import pf,inp,opts
from .portfolio import STATES
from .web_views import e,table


def home(o,s,params):
    b,s=scope(o,s,params); owner=s['role']=='representative'; actor=p(s)
    body='<div class="notice">事業拡張の基盤設定。顧客・収支・KPIは既存の事業IDで管理します。割当の完成操作画面は第20段階の予定で、今回は内部APIと確認一覧までです。外部APIは未接続です。</div>'
    if not b:
        for row in o.db.execute('SELECT id,name FROM portfolio_businesses ORDER BY id'):
            try: o.portfolio.access(actor,row['id'])
            except PermissionError: continue
            meta=o.db.execute('SELECT * FROM bf_profiles WHERE business_id=?',(row['id'],)).fetchone()
            body+='<p><a href="/business-foundation?business='+e(row['id'])+'">'+e(row['name'])+'</a> / '+e('基盤登録済み' if meta else '旧仕様を維持・未採用')+'</p>'
        if owner: body+='<details class="panel"><summary>新規事業を追加（基盤登録）</summary>'+pf(s,'bf_create',inp('name','事業名')+'<label>事業概要</label><textarea name="overview" required maxlength="2000"></textarea>'+inp('category','カテゴリ（自由入力、既存事業の分類は変更しない）')+inp('started_on','実際の開始日（未設定可）',kind='date',required=False),{'key':uuid4().hex},'新規事業を準備中として追加')+'</details>'
        return body
    f=o.business_foundation; row=o.db.execute('SELECT * FROM portfolio_businesses WHERE id=?',(b,)).fetchone(); meta=o.db.execute('SELECT * FROM bf_profiles WHERE business_id=?',(b,)).fetchone()
    if meta: meta=f.get(actor,b)
    body+='<h2>'+e(row['name'])+'</h2><p>事業ID '+e(b)+'</p><p><a href="/business?business='+e(b)+'">既存の顧客・収支・標準KPI・人間向け権限設定</a> / <a href="/sales?business='+e(b)+'">顧客・営業管理</a></p>'
    if not meta:
        body+='<p>旧事業を変更していません。代表が明示採用するまで旧仕様を維持します。</p>'
        if owner: body+=pf(s,'bf_adopt',inp('category','代表が設定するカテゴリ')+inp('started_on','確認済み開始日（空欄可）',kind='date',required=False),{'business':b},'追加情報だけ採用（AI制限は自動切替しない）')
        return body
    body+='<p>状態 '+e(STATES[row['status']])+' / '+e('有効' if meta['enabled'] else '無効・通常データ更新/AI実行を停止')+' / AI割当制限 '+e('有効' if meta['ai_enforced'] else '未切替・旧タスク互換')+'</p><p>カテゴリ '+e(meta['category'])+' / 開始日 '+e(meta['started_on'] or '未設定')+'</p><pre>'+e(meta['overview'])+'</pre>'
    if owner: body+='<details class="panel"><summary>事業情報の更新・無効化</summary>'+pf(s,'bf_update','<label>事業概要</label><textarea name="overview" maxlength="2000">'+e(meta['overview'])+'</textarea>'+inp('category','カテゴリ',meta['category'])+inp('started_on','確認済み開始日（未設定可）',meta['started_on'] or '',kind='date',required=False)+opts('status',STATES,row['status'])+opts('enabled',{'1':'有効','0':'無効・データは保存'},str(meta['enabled']))+'<label><input type="checkbox" name="confirmed" value="1">提供中にする場合は代表が実際の提供を確認</label>',{'business':b,'revision':meta['revision'],'business_revision':meta['business_revision']},'更新（元データを削除しない）')+'</details>'
    assignments=o.db.execute('SELECT * FROM bf_ai_assignments WHERE business_id=? ORDER BY agent',(b,)).fetchall()
    body+='<h2>共通AI社員の事業別割当（確認用）</h2><p>割当/変更/取消は代表認証付き基盤APIで管理。外部送信・承認・課金権限は付与できません。</p>'+table(['社員','担当範囲','役割補足','許可','状態'],[[e(a['agent']),e(a['scope_note']),e(a['role_note']),e('・'.join({'work':'内部作業','business_read':'事業概要参照','finance_read':'収支集計参照'}[k] for k in json.loads(a['permissions']))),e('有効' if a['active'] else '停止')] for a in assignments])
    body+='<h2>追加KPI定義（データ基盤のみ）</h2>'+table(['項目','単位','状態'],[[e(r['label']),e(r['unit']),e('有効' if r['active'] else '停止')] for r in o.db.execute('SELECT * FROM bf_kpis WHERE business_id=?',(b,))])+'<p>実測・推定・モックは別記録です。未設定のKPIを0として補完しません。</p>'
    return body
