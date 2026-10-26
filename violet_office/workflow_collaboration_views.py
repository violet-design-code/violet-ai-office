"""Scoped work desk; does not implement the phase20 allocation console."""
import json
from uuid import uuid4
from .sales_views import scope,p,business_links
from .portfolio_views import pf,inp,opts,LABELS
from .web_views import e,table,dt
from .workflow_collaboration import CHANGES
from .store import now
from .seminars import JST

STATES={'waiting_admin':'管理者確認待ち','ready':'内部作業・確認待ち','retry_wait':'安全な再試行待ち','failed':'失敗・自動停止','paused':'停止中','done':'内部作業完了（外部未実行）','approval_pending':'代表承認待ち','approved_manual_only':'承認済み・実行なし','rejected':'却下','applied_local':'内部対応範囲のみ適用済み'}


def home(o,s,params):
    b,s=scope(o,s,params);actor=p(s);service=o.collaboration
    if not b:return business_links(o,s).replace('/sales?','/ai-workflow?')
    service.access(actor,b);business=o.db.execute('SELECT name FROM portfolio_businesses WHERE id=?',(b,)).fetchone()[0];hidden={'business':b};owner=s['role']=='representative'
    kinds={k:v['label'] for k,v in service.cfg['kinds'].items()};today=now().astimezone(JST).date().isoformat()
    body='<div class="notice">内部業務の振り分け・引き継ぎ。固定テンプレート・LLM未接続。実LINE/メール/予約/決済/公開は行いません。権限や事業設定を自動変更しません。</div><h2>'+e(business)+'</h2><p><a href="/workflow?business='+e(b)+'">既存成果物・代表承認・優先度・停止操作</a> / <a href="/customer-journey?business='+e(b)+'">紹介・同意・面談導線</a></p>'
    deals={r['id']:r['subject']+' / '+('模擬' if r['mode']=='mock' else '人の受付') for r in o.db.execute('SELECT id,subject,mode FROM scm_deals WHERE business_id=?',(b,))}
    if deals:body+='<details><summary>既存案件を内部業務へ受付</summary>'+pf(s,'aw_submit',opts('deal',deals)+opts('kind',kinds)+inp('due','期限',today,'date')+inp('priority','優先度1〜5','3','number'),{**hidden,'key':uuid4().hex},'役割と現行事業権限から振り分け')+'</details>'
    else:body+='<p>既存営業画面で本人希望に基づく案件と目的別同意を登録してください。架空案件は自動登録しません。</p>'
    body+='<details><summary>既存28人の事業別担当状況（閲覧のみ）</summary>'+table(['社員','既存役割','現在の担当','対応可能な内部業務','担当件数'],[[e(r['agent']),e(r['role']),'担当中' if r['assigned'] else '未割当・停止',e(' / '.join(kinds[k] for k in r['kinds']) or '本導線の対象外・未許可'),e(r['work_count'])] for r in service.employees(actor,b)])+'</details>'
    jobs=service.listing(actor,b)
    body+='<h2>事業別業務一覧</h2>'+table(['業務','担当','進行状況','成果物状態','試行/引き継ぎ'],[[e(kinds[r['kind']]),e(r['agent'] or '担当不在・要確認'),e(STATES[r['state']]),e(LABELS.get(r['task_status'],r['task_status'] or '未作成')),e(str(r['attempts'])+'/'+str(r['generation']))] for r in jobs])
    for row in jobs:
        data={**hidden,'job':row['id']};body+='<details class="panel"><summary>'+e(kinds[row['kind']])+' / '+e(row['agent'] or '管理者確認待ち')+'</summary><p>業務ID '+e(row['id'])+' / 期限 '+e(row['due_day'])+'</p>'
        if row['state']=='waiting_admin' and not row['task_id']:body+=pf(s,'aw_dispatch',data=data,caption='現在の同意・担当権限で再判定')
        if row['state'] in ('ready','retry_wait'):body+=pf(s,'aw_run',data=data,caption='内部テンプレートを実行・安全に再試行')
        if owner:
            controls={'pause':'停止'}
            if row['state']=='paused':controls={'resume':'再開（失効権限は復活しない）'}
            elif row['task_id']:controls['complete']='承認済み内部成果物の完了記録'
            body+=pf(s,'aw_control',opts('control',controls),data,'管理者操作')
        if row['task_id'] and row['state'] in ('ready','done'):
            body+='<p>引き継ぎ先も当該事業の明示的な担当権限と目的別同意が必要。事業をまたぐ引き継ぎはできません。</p>'+pf(s,'aw_handoff',opts('target',{a:o.store.agents[a]['role']+' / '+a for a in sorted({a for v in service.cfg['kinds'].values() for a in v['candidates']})})+opts('kind',kinds)+inp('reason','理由（個人情報や本文を含めない）'),{**data,'key':uuid4().hex},'同一事業の担当へ引き継ぐ')
        if row['error_code']:body+='<p>安全なエラーコード：'+e(row['error_code'])+' / 次回可能時刻 '+dt(row['next_at'])+'</p>'
        body+='</details>'
    history=o.db.execute('SELECT * FROM aw_handoffs WHERE business_id=? ORDER BY created_at DESC',(b,)).fetchall()
    body+='<h2>引き継ぎ履歴</h2>'+table(['元→先','理由','状態','日時'],[[e(r['from_agent'])+' → '+e(r['to_agent']),e(r['reason']),'同一事業の内部引き継ぎ記録',dt(r['created_at'])] for r in history])
    body+='<h2>承認待ち・重要操作案</h2><p>外部操作・料金・権限等は承認しても実行しません。実行アダプターは未実装。内部対応範囲だけは代表が承認後に別操作で適用できます。</p>'
    for change in o.db.execute('SELECT * FROM aw_changes WHERE business_id=? ORDER BY created_at DESC',(b,)):
        data={**hidden,'id':change['id']};body+='<details><summary>'+e(CHANGES[change['kind']])+' / '+e(STATES[change['status']])+'</summary><pre>'+e(change['payload'])+'</pre><p>承認者 '+e(change['approved_by'] or '未承認')+' / '+dt(change['approved_at'])+'</p>'
        if owner and change['status']=='approval_pending':
            for choice,label in [('approve','内容だけ承認（実行しない）'),('reject','却下')]:body+=pf(s,'aw_decide','<label><input type="checkbox" name="checked" value="1" required>対象・現在の条件・同意・権限を代表確認</label>',{**data,'decision':choice},label)
        if owner and change['kind']=='capability' and change['status']=='approved_manual_only':body+=pf(s,'aw_apply_capability',data=data,caption='承認済み内部対応範囲だけ適用（既存権限は変更しない）')
        body+='</details>'
    body+='<p>第20段階向けの提案API・サービス層を提供。完成版の事業追加・社員割当画面は今回追加しません。</p>'
    return body
