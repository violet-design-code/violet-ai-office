"""Readiness checks and review only. Phase20 creation/allocation editors are not included."""
import json
from .sales_views import p,business_links
from .portfolio_views import pf
from .web_views import e,table,dt
from .business_readiness import KINDS_READY

STATUS={'approval_pending':'代表承認待ち','approved':'承認済み・未適用','rejected':'却下','applied':'代表の明示操作で適用済み'}


def home(o,s,params):
    b=params.get('business',[''])[0] or None;actor=p(s)
    if b is None and s['role']=='business_operator':return business_links(o,s).replace('/sales?','/business-readiness?')
    o.readiness.access(actor,b,b is None);data=o.readiness.contract(actor,b)
    body='<div class="notice">第20段階の画面に向けた最終基盤。保存前確認→代表承認→別操作で適用。実送信・予約確定・決済・公開なし。新しい事業入力ウィザード・28人割当エディターは今回未実装。</div>'
    if b:body+='<h2>'+e(o.db.execute('SELECT name FROM portfolio_businesses WHERE id=?',(b,)).fetchone()[0])+'</h2><p><a href="/business?business='+e(b)+'">事業詳細・既存実績</a> / <a href="/ai-workflow?business='+e(b)+'">内部業務・引き継ぎ</a></p>'
    elif s['role']=='representative':
        body+='<h2>既存事業の確認</h2>'+table(['事業ID','事業名','状態'],[[e(r['id']),'<a href="/business-readiness?business='+e(r['id'])+'">'+e(r['name'])+'</a>',e(data['business_states'][r['status']])] for r in o.db.execute('SELECT id,name,status FROM portfolio_businesses ORDER BY id')])
    body+='<details><summary>第20段階向け操作仕様</summary><p>事業登録：事業名・内容・カテゴリ・開始日・分類。新規は準備中、料金・目標・顧客・担当は未設定。既存事業と自動統合しません。</p><p>担当：既存28人から選択し、事業別役割補足・範囲・限定権限を確認。解除は論理停止。変更する事業/社員だけを厳密な現行権限で制限します。</p><p>実装済みの認証付きAPI・サービスを用いて将来画面を作成します。完成版のボタン・入力画面は第20段階で検証します。</p></details>'
    if b:body+='<details><summary>既存28人の担当状況（確認のみ）</summary>'+table(['ID','役割','当該事業の登録','内部作業可否'],[[e(r['id']),e(r['role']),'設定あり' if r['assignment'] else '未割当','許可' if r['work_allowed'] else '未許可・停止'] for r in data['employees']])+'</details>'
    body+='<h2>保存前確認と承認・適用履歴</h2>'
    for row in o.readiness.listing(actor,b):
        view=json.loads(row['preview']);body+='<details class="panel"><summary>'+e(KINDS_READY[row['kind']])+' / '+e('要再確認（現在の設定が変更済み）' if row['stale'] else STATUS[row['status']])+'</summary><p>変更ID '+e(row['id'])+'</p><h3>変更前</h3><pre>'+e(json.dumps(view['before'],ensure_ascii=False,indent=2))+'</pre><h3>確認する変更後</h3><pre>'+e(json.dumps(view['after'],ensure_ascii=False,indent=2))+'</pre><p>'+e(' / '.join(view['impact']))+'</p><p>承認者 '+e(row['approved_by'] or '未承認')+' / '+dt(row['approved_at'])+' / 適用者 '+e(row['applied_by'] or '未適用')+'</p>'
        hidden={'id':row['id'],**({'business':b} if b else {})}
        if s['role']=='representative' and row['status']=='approval_pending':
            for choice,label in ([('reject','却下')] if row['stale'] else [('approve','この変更内容を承認（まだ適用しない）'),('reject','却下')]):body+=pf(s,'br_decide','<label><input name="checked" type="checkbox" value="1" required>変更前後・影響・現在の権限を代表が確認</label>',{**hidden,'decision':choice},label)
        if s['role']=='representative' and row['status']=='approved' and not row['stale']:body+=pf(s,'br_apply',data=hidden,caption='承認済みの内部設定だけ適用')
        body+='</details>'
    return body
