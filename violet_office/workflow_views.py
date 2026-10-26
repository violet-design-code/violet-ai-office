"""Scoped Japanese business work desk. All values escaped, no client scripts."""
import html
from uuid import uuid4
from .sales_views import scope,business_links,p
from .portfolio_views import pf,inp,opts
from .web_views import e,table,card
from .workflow import STAGES,WORK
from .seminars import ANSWERS,SEGMENTS


def home(o,s,params):
    b,s=scope(o,s,params)
    if not b: return business_links(o,s).replace('/sales?','/workflow?')
    w=o.workflow; actor=p(s); owner=s['role']=='representative'; hidden={'business':b}; stats=w.dashboard(actor,b)
    body='<div class="notice">AI社員業務自動化・営業支援。ローカル固定テンプレート・ルールのみ。LLM未接続。外部送信・契約・決済は実行しません。</div><p>事業 '+e(b)+' / 実測記録は代表の申告・外部未照合。期間 '+e(stats['period'])+'</p><div class="grid">'
    for label,value in [('期限が本日以前の未完了内部タスク',stats['due_internal_tasks']),('対応中案件（全期間）',stats['active_deals']),('予約候補（全期間）',stats['bookings']['candidate']),('予約確定申告（全期間）',stats['bookings']['confirmed']),('未入金残高（全期間・税込）',stats['unpaid_yen']),('当月請求売上（税抜）',stats['sales']['confirmed_net_yen']),('当月受付案件の見積（税込）',stats['sales']['quote_gross_yen'])]: body+=card(label,value)
    body+='</div><h2>本日の対応・通知</h2><p>通知はこの画面内だけ。返信未着は担当者が返信確認後、次工程へ更新してください。</p>'
    for key,label in [('overdue','次回予定日が本日以前'),('unhandled','未対応'),('unreplied','返信待ち'),('quote_unanswered','見積段階・契約未確認')]:
        body+='<details><summary>'+label+' '+str(len(stats['alerts'][key]))+'件</summary>'+''.join('<p><a href="/sales-deal?business='+e(b)+'&amp;id='+e(d)+'">案件 '+e(d)+'</a></p>' for d in stats['alerts'][key])+'</details>'
    body+='<h2>営業の進行</h2><p>案内済み・成約・実施・入金は第12段階の代表確認記録が必要です。この画面の操作で売上は作りません。</p>'
    for deal in o.db.execute('SELECT d.*,w.stage AS flow,w.revision AS flow_revision FROM scm_deals d LEFT JOIN wf_cases w ON w.business_id=d.business_id AND w.deal_id=d.id WHERE d.business_id=? ORDER BY d.created_at DESC LIMIT 200',(b,)):
        h={**hidden,'deal':deal['id']}; body+='<details class="panel"><summary>'+e(deal['subject'])+' / '+e(STAGES.get(deal['flow'],'業務フロー未関連付け'))+' / '+e('モック' if deal['mode']=='mock' else '人間確認用')+'</summary><p>案件ID '+e(deal['id'])+' / 第12段階 '+e(deal['stage'])+' / '+e(deal['state'])+'</p><a href="/sales-deal?business='+e(b)+'&amp;id='+e(deal['id'])+'">顧客・見積・予約・確認記録へ</a>'
        if not deal['flow']: body+=pf(s,'wf_attach',data=h,caption='既存案件に業務フローを関連付け')
        else:
            if owner: body+=pf(s,'wf_advance',opts('stage',STAGES),{**h,'revision':deal['flow_revision']},'代表が次工程・見送り・再開を確認')
            body+=pf(s,'wf_assign',inp('key','作業キー',uuid4().hex)+opts('agent',{a:a+' '+o.store.agents[a]['role']+' / '+work for a,work in WORK.items()})+inp('purpose','目的（個人情報不可）')+inp('basis','入力根拠・参照ID（個人情報/秘密値不可）')+inp('due','期限（JST）',kind='date')+opts('priority',{str(i):str(i) for i in range(1,6)},'3'),h,'必要な1人へ内部タスク割当')
            if owner:
                body+='<p>セミナーは既存の本人確認済み参照が必要です。<a href="/seminar-admin">区分・回答・3日前準備を既存画面で管理</a></p>'+pf(s,'wf_draft',inp('seminar','既存セミナーID')+inp('username','本人確認済み参加者アカウント')+opts('kind',{'invite':'区分別案内','reminder':'既存3日前リマインドの確認','absence':'欠席確認後の任意フォロー'}),h,'固定テンプレート案を準備（送信しない）')
                body+=pf(s,'wf_attendance',inp('seminar','開催済みセミナーID')+inp('username','本人確認済み参加者アカウント')+opts('outcome',{'attended':'参加確認','absent':'欠席確認'})+inp('evidence','出欠の確認根拠参照（個人情報不可）'),h,'代表が確認済み出欠を記録（回答から推測しない）')
        body+='</details>'
    body+='<h2>28人の担当状況（内部タスク。モック案件分も含む）</h2>'+table(['社員','役割','担当件数','承認済み内部成果物','停止中'],[[e(r['agent']),e(o.store.agents[r['agent']]['role']),e(r['assigned']),e(r['approved']),e(r['paused'])] for r in stats['employees']])
    body+='<h2>仕事一覧・承認待ち</h2>'
    for row in o.db.execute('SELECT w.*,t.agent,t.status,p.priority FROM wf_tasks w JOIN tasks t ON t.id=w.task_id JOIN portfolio_tasks p ON p.task_id=w.task_id WHERE w.business_id=? ORDER BY p.priority,w.due_day LIMIT 200',(b,)):
        h={**hidden,'task':row['task_id']}; body+='<details class="panel"><summary>'+e(row['agent']+' / '+row['purpose'])+' / '+e('停止中' if row['paused'] else {'pending':'未着手','review_pending':'代表確認待ち','manual_execution_pending':'内部成果物承認済み・外部未実行','rejected':'却下','failed':'失敗'}.get(row['status'],row['status']))+'</summary><p>優先度 '+e(row['priority'])+' / 期限 '+e(row['due_day'])+' / 承認者 '+e(row['approved_by'] or '未承認')+'</p><p>根拠：'+e(row['basis'])+'</p>'
        if row['result']: body+='<pre>'+html.escape(row['result'])+'</pre>'
        if owner:
            body+=pf(s,'wf_control',opts('control',{'pause':'停止','resume':'再開','priority':'優先順位変更','assign':'担当変更'})+inp('value','優先順位1〜5または担当AI ID（停止・再開は空欄可）',required=False),h,'代表の指示を保存')
            if row['status']=='review_pending' and not row['paused']:
                for choice,label in [('approve','内部成果物を承認（送信しない）'),('reject','却下')]: body+=pf(s,'wf_decide','<label><input type="checkbox" name="checked" value="1" required>内容・根拠・権限を代表が確認</label>',{**h,'decision':choice},label)
        if row['status']=='pending' and not row['paused']: body+=pf(s,'wf_run',data=h,caption='ローカル固定テンプレートを1回作成')
        body+='<pre>'+e([dict(r) for r in o.db.execute('SELECT event,created_at FROM events WHERE task_id=? ORDER BY id DESC LIMIT 20',(row['task_id'],))])+'</pre></details>'
    if owner:
        queue={r['id'] for r in w.queue(actor,b)}
        body+='<h2>セミナー案内・承認待ち・送信待ち</h2><p>実送信APIは未接続。承認済みでも送信しません。条件変更後の待ちデータは使用不可です。</p>'
        for row in o.db.execute('SELECT * FROM wf_drafts WHERE business_id=? ORDER BY created_at DESC LIMIT 100',(b,)):
            body+='<details><summary>'+e(row['kind'])+' / '+e('モック下書き' if row['mode']=='mock' else '人間確認用下書き')+' / '+e('承認済み送信待ち・API未接続' if row['id'] in queue else '条件再確認・未承認または無効')+'</summary><pre>'+e(row['body'])+'</pre>'
            if row['status']=='approval_pending':
                for choice,label in [('approve','送信待ちとして承認（送らない）'),('reject','却下')]: body+=pf(s,'wf_draft_decide','<label><input type="checkbox" name="checked" value="1" required>区分・同意・参加意思・停止・表現を代表確認</label>',{**hidden,'id':row['id'],'decision':choice},label)
            body+='</details>'
        for link in o.db.execute('SELECT seminar_id FROM portfolio_seminar_links WHERE business_id=?',(b,)):
            seminar=o.seminars.get(link[0]); recipients=o.seminars.recipients(actor,link[0])
            body+='<details><summary>'+e(seminar['title'])+'の参加状況（代表専用）</summary>'+table(['区分','回答','人数'],[[e(SEGMENTS[segment]),e(ANSWERS[answer]),e(sum(r['segment']==segment and r['answer']==answer for r in recipients))] for segment in SEGMENTS for answer in ANSWERS])+'</details>'
    return body
