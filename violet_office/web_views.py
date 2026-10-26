"""Server-rendered Japanese UI. All data is escaped; no JavaScript or remote assets."""
import html
import json
from uuid import uuid4
from datetime import datetime
from .office import DEPARTMENTS,STATUS,CHECKS,department,redact,JST
from .store import now


def e(value):
    return html.escape(redact(value),quote=True)


def dt(value):
    if not value:
        return '未記録'
    return e(datetime.fromisoformat(value).astimezone(JST).strftime('%Y-%m-%d %H:%M JST'))


def label(value):
    return STATUS.get(value,value)


def fields(data):
    return ''.join(f'<input type="hidden" name="{html.escape(str(k),quote=True)}" value="{html.escape(str(v),quote=True)}">' for k,v in data.items())


def form(session,action,body='',data=None,caption='実行',style=''):
    if session['role']!='representative':
        return '<span class="muted micro">閲覧専用</span>'
    return f'<form method="post" action="/actions/{e(action)}" class="{e(style)}">'+fields({'csrf':session['csrf'],'nonce':uuid4().hex,**(data or {})})+body+f'<button>{e(caption)}</button></form>'


def table(headers,rows):
    return '<div class="table-wrap"><table><thead><tr>'+''.join('<th>'+e(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+(''.join('<tr>'+''.join('<td>'+cell+'</td>' for cell in row)+'</tr>' for row in rows) or f'<tr><td colspan="{len(headers)}">記録がありません</td></tr>')+'</tbody></table></div>'


def card(title,value,note=''):
    return f'<div class="card"><div class="label">{e(title)}</div><div class="number">{e("未計測" if value is None else value)}</div><div class="muted micro">{e(note)}</div></div>'


def layout(title,body,session=None,path=''):
    nav=[('/','社長室'),('/employees','AI社員28人'),('/instructions','統括へ指示'),('/approvals','承認センター'),('/analytics','集客・営業'),('/costs','AI経費'),('/monitor','システム監視'),('/growth','目標・定期業務'),('/reports','業務レポート'),('/learning','知識・改善'),('/selection','評価・選抜'),('/seminar-admin','セミナー管理'),('/businesses','複数事業'),('/business-reports','経営会議'),('/development','新規事業開発'),('/sales','営業・顧客管理'),('/sales-dashboard','営業実績'),('/workflow','業務・営業支援'),('/seminar-office','セミナー運営'),('/business-foundation','事業基盤'),('/customer-journey','事業別案内・導線')]
    if session and session.get('role')=='participant': nav=[('/seminars','初回登録・セミナー'),('/seminar-participation','申込・参加希望') ]
    if session and session.get('role')=='business_operator': nav=[('/businesses','担当事業'),('/development','事業開発'),('/sales','営業・顧客管理'),('/sales-dashboard','営業実績'),('/workflow','業務・営業支援'),('/seminar-office','セミナー運営'),('/business-foundation','事業基盤'),('/customer-journey','事業別案内・導線')]
    navigation='<nav>'+''.join(f'<a href="{url}"'+(' aria-current="page"' if path==url else '')+'>'+name+'</a>' for url,name in nav)+'</nav>' if session and session.get('username') else ''
    logout=''
    if session and session.get('username'):
        logout=f'<div>{e(session["username"])} / {e("代表" if session["role"]=="representative" else "参加者" if session["role"]=="participant" else "事業担当" if session["role"]=="business_operator" else "閲覧者")}<form method="post" action="/logout">'+fields({'csrf':session['csrf']})+'<button class="secondary">ログアウト</button></form></div>'
    return '<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>'+e(title)+' | VIOLET AI OFFICE</title><link rel="stylesheet" href="/assets/office.css"></head><body><header><div class="brand">VIOLET AI OFFICE<small>バイオレット ｜ AI社長室</small></div>'+logout+'</header>'+navigation+'<main><h1>'+e(title)+'</h1>'+body+'</main><footer class="footer">ローカル専用 · 外部送信なし · 有料API未接続 · 時刻は日本時間</footer></body></html>'


def login(session,message=''):
    return layout('社長室にログイン','<div class="panel login"><h2>おかえりなさい</h2><p class="muted">代表・閲覧者・参加者のアカウントでログインしてください。</p>'+('<p class="error">'+e(message)+'</p>' if message else '')+'<form method="post" action="/login">'+fields({'csrf':session['csrf']})+'<label for="username">ユーザー名</label><input id="username" name="username" autocomplete="username" required maxlength="40"><label for="password">パスワード</label><input id="password" name="password" type="password" autocomplete="current-password" required maxlength="256"><button>ログイン</button></form></div>')


def home(office,session):
    m=office.metrics()
    tasks=office.tasks()
    body='<p class="subtitle">代表の判断に必要な進捗・成果・承認を、一つの場所に。</p><div class="notice">現在はローカル管理・テンプレート生成・モック連携です。28人の自律AIと外部APIは未接続。モックを実績として集計しません。</div>'
    if office.alerts():
        body+='<div class="notice">要対応：'+e(' / '.join(a['label']+' '+str(a['count'])+'件' for a in office.alerts()))+' <a href="/monitor">監視画面を確認</a></div>'
    body+='<div class="grid">'+''.join(card(k,v,'ローカルDBの記録') for k,v in m['local'].items())+card('API確定利用料金',sum(r['amount'] for r in office.costs()['entries'] if r['kind']=='confirmed' and r['mode']=='manual')/1000000 if any(r['kind']=='confirmed' and r['mode']=='manual' for r in office.costs()['entries']) else None,'USD・手動確認記録／API自動取得は未接続')+'</div>'
    body+='<h2>経営指標</h2><div class="grid">'+''.join(card(k,v,'手動実測申告・対象期間の全日分' if v is not None else '自動取得未接続・計測不足') for k,v in list(m['business'].items())[:6])+'</div>'
    body+='<h2>5人のAI部門長</h2><div class="grid">'+''.join('<a class="card" href="/employees?id='+agent+'"><span class="id">'+agent+'</span><h3>'+e(name+'部長')+'</h3><span class="badge">モデルAPI未接続</span></a>' for agent,name in DEPARTMENTS.items())+'</div>'
    body+='<h2>ローカルタスクの状態</h2><div class="panel">'
    for status in ('pending','running','review_pending','approval_pending','completed','failed','paused'):
        count=sum(t['status']==status for t in tasks)
        body+=f'<label>{e(label(status))}：{count}</label><meter class="progress" min="0" max="{max(len(tasks),1)}" value="{count}">{count}</meter>'
    return body+'</div>'


def employees(office,session,agent_id=None):
    tasks=office.tasks()
    agents=office.store.agents
    if agent_id:
        if agent_id not in agents:
            raise ValueError('Unknown agent')
        agent=agents[agent_id]
        mine=[t for t in tasks if t['agent']==agent_id]
        body=f'<p>{e(department(agent_id))} / {e(agent["role"])} <span class="badge">モデルAPI未接続</span></p><div class="notice">開始はローカル手動作業の受付です。生成AIを起動しません。停止はタスクの受付・状態を止め、外部プロセスの中断は行いません。</div>'
        rows=[]
        for task in mine:
            controls=''
            blocked=office.db.execute("SELECT 1 FROM dependencies d JOIN tasks t ON t.id=d.prerequisite WHERE d.task_id=? AND t.status NOT IN ('completed','approval_pending','manual_execution_pending')",(task['id'],)).fetchone()
            aggregate=office.db.execute("SELECT 1 FROM request_tasks WHERE task_id=? AND kind='aggregate'",(task['id'],)).fetchone()
            proposal=office.db.execute('SELECT status FROM growth_proposals WHERE task_id=?',(task['id'],)).fetchone()
            if proposal and proposal['status']!='approved_local':
                blocked=True
            if task['status'] in ('pending','paused'):
                if not blocked and not aggregate and task['attempts']<task['max_attempts']:
                    controls+=form(session,'start',data={'id':task['id']},caption='手動作業を開始',style='inline')
                else:
                    controls+='<button disabled>前提作業・集約手順・試行上限を確認</button>'
            if task['status'] in ('pending','running'):
                controls+=form(session,'stop',data={'id':task['id']},caption='一時停止',style='inline')
            if task['status']=='failed':
                advanced=office.db.execute("SELECT 1 FROM dependencies d JOIN tasks t ON t.id=d.task_id WHERE d.prerequisite=? AND t.status!='pending'",(task['id'],)).fetchone()
                campaign=office.db.execute('SELECT 1 FROM campaign_work WHERE task_id=?',(task['id'],)).fetchone()
                if not advanced and not campaign:
                    controls+=form(session,'retry',data={'id':task['id']},caption='新しい試行を依頼',style='inline')
                else:
                    controls+='<button disabled>依頼全体の再作成が必要</button>'
            if task['status']=='running':
                controls+=form(session,'result','<label>匿名化した成果物（手動登録）</label><textarea name="content" required maxlength="10000"></textarea>',{'id':task['id']},'成果物を登録')
            scoped=office.db.execute('SELECT business_id FROM portfolio_tasks WHERE task_id=?',(task['id'],)).fetchone()
            bd=office.db.execute('SELECT p.idea_id FROM bd_tasks t JOIN bd_plans p ON p.id=t.plan_id WHERE t.task_id=?',(task['id'],)).fetchone()
            scm=office.db.execute('SELECT business_id,deal_id FROM scm_work WHERE task_id=?',(task['id'],)).fetchone()
            seminar_task=office.db.execute('SELECT business_id FROM s14_work WHERE task_id=?',(task['id'],)).fetchone()
            if seminar_task: controls='<a href="/seminar-office?business='+e(seminar_task[0])+'">セミナー運営画面で管理</a>' if session['role']=='representative' else '事業権限付きセミナー画面で管理'
            elif scm: controls='<a href="/sales-deal?business='+e(scm[0])+'&amp;id='+e(scm[1])+'">営業画面で管理</a>' if session['role']=='representative' else '権限付き営業画面で管理'
            elif bd: controls='<a href="/development-idea?id='+e(bd[0])+'">事業開発画面で管理</a>' if session['role']=='representative' else '権限付き事業開発画面で管理'
            elif scoped: controls='<a href="/business?business='+e(scoped[0])+'">事業別画面で管理</a>' if session['role']=='representative' else '事業別の権限付き画面で管理'
            payload=json.loads(task['payload'])
            summary=payload.get('instruction',payload.get('brief',payload.get('purpose','ローカル作業'))) if isinstance(payload,dict) else 'ローカル作業'
            rows.append([e(task['id']),e(summary if session['role']=='representative' else '詳細は代表のみ'),e(label(task['status'])),e(task['priority'] or '未設定'),dt(task['updated_at']),dt(task['planned_due']) if task.get('planned_due') else '未設定',controls or '操作対象なし'])
        body+='<p><a href="/employee-learning?id='+agent_id+'">業務記録・指導・評価・改善履歴</a></p>'
        body+=table(['タスクID','仕事内容','進捗','優先度','最終イベント（日本時間）','計画期限（日本時間）','ローカル操作'],rows)
        artifacts=office.db.execute('SELECT a.*,t.status FROM artifacts a JOIN tasks t ON t.id=a.task_id WHERE a.author=? ORDER BY a.rowid DESC',(agent_id,)).fetchall()
        body+='<h2>成果物</h2>'
        for a in artifacts:
            body+='<details class="panel"><summary>'+e(a['id'])+' / '+e(label(a['status']))+'</summary><pre>'+e(a['content'] if session['role']=='representative' else '本文は代表のみ閲覧できます')+'</pre></details>'
        errors=office.db.execute('SELECT r.task_id,r.error_code FROM runs r JOIN tasks t ON t.id=r.task_id WHERE t.agent=? AND r.error_code IS NOT NULL',(agent_id,)).fetchall()
        body+='<h2>実行エラー</h2>'+table(['タスク','コード'],[[e(r[0]),e(r[1])] for r in errors])
        body+='<h2>API料金</h2>'+table(['種別','モード','USD'],[[e(r['kind']),e(r['mode']),e(r['amount']/1000000)] for r in office.costs()['entries'] if r['agent']==agent_id])+'<p>API請求の自動取得は未接続です。</p>'
        return body
    body='<p class="subtitle">役割は設定済み。稼働表示はローカルタスクの状態です。</p>'
    for dep in ['統括本部',*DEPARTMENTS.values(),'品質・法令監査']:
        body+='<h2>'+e(dep)+'</h2><div class="grid">'
        for aid,agent in agents.items():
            if department(aid)!=dep:
                continue
            mine=[t for t in tasks if t['agent']==aid]
            status='エラーあり' if any(t['status']=='failed' for t in mine) else '作業中（ローカル）' if any(t['status']=='running' for t in mine) else '待機中'
            updated=office.db.execute("SELECT MAX(e.created_at) FROM events e JOIN tasks t ON t.id=e.task_id WHERE t.agent=? AND e.event='claimed'",(aid,)).fetchone()[0]
            body+='<a class="card" href="/employees?id='+aid+'"><span class="id">'+aid+'</span><h3>'+e(agent['role'])+'</h3><span class="badge">'+status+'</span><span class="badge">API未接続</span><p class="micro">タスク '+str(len(mine))+'件 / 最終実行 '+(dt(updated) if updated else '未実行')+'</p></a>'
        body+='</div>'
    return body


def instructions(office,session):
    today=now().astimezone(JST).date().isoformat()
    body='<div class="split"><div class="panel"><h2>AI01に仕事を依頼</h2><p>日本語キーワードで分類・担当を選択します。自由な文章理解は未接続。合致しない依頼は要確認になります。</p>'
    body+=form(session,'submit','<label>依頼内容（匿名情報のみ）</label><textarea name="instruction" required maxlength="4000" placeholder="今月の問い合わせと面談予約を分析して"></textarea><label>優先度（1が最高）</label><select name="priority">'+''.join(f'<option value="{i}">{i}</option>' for i in range(1,6))+'</select>',{'key':'web-'+uuid4().hex},'担当へ割り当てる')+'</div><div class="panel"><h2>原稿をテンプレートで作成</h2><p>媒体・日数を指定する固定テンプレート機能です。任意の依頼の意味を解釈して生成する機能は未実装です。</p>'
    body+=form(session,'generate','<label>企画メモ</label><textarea name="instruction" required maxlength="4000" placeholder="来週のInstagram投稿を7日分作成して"></textarea><label>媒体</label><select name="platform"><option>Instagram</option><option>Facebook</option><option>LINE</option></select><label>日数</label><select name="days"><option>7</option><option>14</option><option>30</option></select><label>開始日</label><input type="date" name="start_date" value="'+today+'" required>',{'key':'web-content-'+uuid4().hex},'原稿を作成・承認待ちへ')+'</div></div>'
    requests=office.db.execute('SELECT r.*,o.priority FROM requests r JOIN request_options o ON o.request_id=r.id ORDER BY r.created_at DESC').fetchall()
    body+='<h2>依頼と進捗</h2>'+table(['依頼','仕事内容','状況','優先度','作成日時（日本時間）'],[[f'<a href="/requests?id={r["id"]}">'+e(r['id'])+'</a>',e(r['instruction'] if session['role']=='representative' else '詳細は代表のみ'),e(label(r['status'])),e(r['priority']),dt(r['created_at'])] for r in requests])
    return body


def request_view(office,session,ref):
    report=office.coordinator.report(ref)
    original=office.db.execute('SELECT instruction FROM requests WHERE id=?',(ref,)).fetchone()[0]
    body='<div class="notice">'+e(report['report_ja'])+'</div><h2>依頼内容</h2><p>'+e(original if session['role']=='representative' else '詳細は代表のみ閲覧できます')+'</p>'
    body+=form(session,'priority','<label>依頼の優先度</label><select name="priority">'+''.join(f'<option value="{i}">{i}</option>' for i in range(1,6))+'</select>',{'id':ref},'優先度を変更')
    if report['status']=='ready_to_collect':
        body+=form(session,'collect',data={'id':ref},caption='レビュー済み成果物を集約')
    body+=table(['担当','タスク','状態','成果物'],[[f'<a href="/employees?id={t["agent"]}">'+t['agent']+'</a>',e(t['id']),e(label(t['status'])),e(t['artifact_id'] or '未作成')] for t in report['tasks']])
    return body


def decisions(session,action,ref):
    return '<div class="actions">'+''.join(form(session,action,data={'id':ref,'decision':value},caption=text,style='inline') for value,text in [('approve','承認（送信なし）'),('changes','修正依頼'),('reject','却下')])+'</div>'


def approvals(office,session,campaign=None):
    body='<div class="notice">承認だけでは送信・公開・実予約確定しません。外部実行はすべて未接続。自動チェックは法令適合を保証しません。原稿承認には全確認項目の根拠記録が必要です。</div>'
    query='SELECT * FROM content_versions WHERE status!=\'superseded\''
    rows=office.db.execute(query+(' AND campaign_id=?' if campaign else '')+' ORDER BY rowid DESC', (campaign,) if campaign else ()).fetchall()
    reports=office.db.execute("SELECT COUNT(*) FROM growth_runs WHERE status='succeeded_local' AND decision='approval_pending'").fetchone()[0]
    body+='<div class="notice">業務計画・レポートの承認待ち '+str(reports)+'件。<a href="/reports">レポートを確認</a></div>'
    body+='<h2>SNS・LINE・紹介文・制作指示</h2>'
    for row in rows:
        audit=office.db.execute('SELECT * FROM content_audits WHERE content_id=?',(row['id'],)).fetchone()
        body+='<details class="panel"><summary>'+e(row['logical_key'])+' / '+e(row['kind'])+' / '+e(label(row['status']))+' / v'+str(row['version'])+'</summary><p class="micro">'+e(row['id'])+'</p><pre>'+e(row['body'] if session['role']=='representative' else '本文は代表のみ閲覧できます')+'</pre>'
        if row['status']=='approved':
            try:
                office.content.verify_approved(row['id'])
            except ValueError:
                body+='<div class="notice">過去の承認記録はありますが、現行基準では要再確認です。新しい版を作成して監査してください。</div>'
        if audit:
            body+='<p>自動監査・要確認：</p><pre>'+e(audit['findings'])+'\n'+e(audit['blockers'])+'</pre>'
            checks=json.loads(audit['checks'])
            body+=table(['確認項目','根拠記録'],[[e(k),e(v or '未確認')] for k,v in checks.items()])
            if row['status']=='approval_pending':
                body+=form(session,'content_check','<label>実資料を確認した項目</label><select name="check">'+''.join('<option>'+e(k)+'</option>' for k in CHECKS)+'</select><label>確認根拠（秘密値・個人情報は禁止）</label><input name="evidence" required maxlength="1000">',{'id':row['id']},'確認を記録')
                body+=decisions(session,'content_decide',row['id'])
        if row['status'] in ('approval_pending','changes_requested','rejected','approved','draft'):
            body+=form(session,'revise','<label>修正版のJSON（新しい版として保存）</label><textarea name="body" required maxlength="10000">'+e(row['body'] if session['role']=='representative' else '')+'</textarea>',{'id':row['id']},'新しい版を作成・再監査')
        history=office.db.execute('SELECT event,actor,created_at FROM content_events WHERE content_id=? ORDER BY id',(row['id'],)).fetchall()
        body+='<details><summary>承認・修正履歴</summary>'+table(['操作','担当','日時（日本時間）'],[[e(r[0]),e(r[1]),dt(r[2])] for r in history])+'</details></details>'
    body+='<h2>担当作業のレビュー・承認</h2>'
    for row in office.db.execute("SELECT a.*,t.status FROM artifacts a JOIN tasks t ON t.id=a.task_id WHERE t.status IN ('review_pending','approval_pending') ORDER BY a.rowid DESC"):
        body+='<details class="panel"><summary>'+e(row['author'])+' / '+e(label(row['status']))+'</summary><pre>'+e(row['content'] if session['role']=='representative' else '本文は代表のみ閲覧できます')+'</pre>'
        if row['status']=='review_pending':
            body+='<p>指定レビュアー '+e(office.store.agents[row['author']]['reviewer'])+' のレビュー結果を代表が記録します。自動合格ではありません。</p>'+form(session,'task_review','<label>レビュー結果</label><select name="verdict"><option value="pass">確認済み</option><option value="changes_requested">修正必要</option></select><label>確認内容</label><input name="note" required maxlength="1000">',{'id':row['id']},'別担当の確認結果を記録')
        else:
            body+=decisions(session,'task_decide',row['id'])
        body+='</details>'
    body+='<h2>外部操作・予約（モック台帳のみ）</h2>'
    for row in office.db.execute('SELECT * FROM ix_operations ORDER BY rowid DESC'):
        body+='<div class="panel"><h3>'+e(row['kind'])+' / '+e(label(row['status']))+'</h3><p class="micro">'+e(row['id'])+' / モック・実送信なし</p>'
        if session['role']=='representative':
            payload=json.loads(row['payload'])
            body+='<pre>'+e(json.dumps(payload,ensure_ascii=False,indent=2))+'</pre>'
            if row['kind'] in ('booking_confirm','booking_cancel'):
                booking=office.db.execute('SELECT b.id,b.status,s.start_at,s.end_at FROM ix_bookings b JOIN ix_slots s ON s.id=b.slot_id WHERE b.id=?',(payload.get('booking_id'),)).fetchone()
                if booking:
                    body+=table(['予約参照','開始','終了','状態'],[[e(booking[0]),dt(booking[2]),dt(booking[3]),e(booking[1])]])
        if row['status']=='approval_pending':
            body+=decisions(session,'operation_decide',row['id'])
        body+='<button disabled>本番送信・公開・予約確定：未接続</button></div>'
    return body


def analytics(office,session):
    m=office.metrics()
    body='<p>対象月：'+e(m['month'])+'（日本時間）。自動取得は未接続。全日分の手動実測申告がある場合のみ月間件数を表示します。顧客単位の転換率は未計測です。<a href="/growth">実績の記録</a></p><div class="grid">'+''.join(card(k,v,'手動実測申告' if v is not None else '未接続または計測不足') for k,v in m['business'].items())+'</div>'
    body+='<h2>手動実測の計測範囲（部分計測は月間実績と扱わない）</h2>'+table(['指標','記録済み件数','状態','日数'],[[e(v['title']),e(v['value'] if v['value'] is not None else '未計測'),e(v['status']),e(str(v['coverage_days'])+'/'+str(v['expected_days']))] for v in m['manual_business'].values()])
    return body+'<h2>モック検証データ（実績に含めない）</h2><div class="grid">'+''.join(card(k,v,'第4段階モックDBの累計') for k,v in m['mock'].items())+'</div>'


def costs(office,session):
    data=office.costs()
    month=now().astimezone(JST).strftime('%Y-%m')
    body='<div class="notice">有料API未接続。請求自動取得は未計測です。以下は手動記帳・モック・タスク費用予約を分離した管理です。通貨はUSD。入力しても支払いは発生しません。</div>'
    budget_rows=[]
    for r in data['budgets']:
        accounted=sum(c['amount'] for c in data['entries'] if c['mode']=='manual' and c['incurred_date'].startswith(r['month']))
        reserved=office.db.execute('SELECT COALESCE(SUM(amount),0) FROM office_month_reservations WHERE month=?',(r['month'],)).fetchone()[0]
        budget_rows.append([e(r['month']),e(r['limit_microusd']/1000000),e(accounted/1000000),e(reserved/1000000),e(max(0,r['limit_microusd']-accounted-reserved)/1000000)])
    body+='<h2>月間予算上限</h2>'+table(['対象月','上限USD','手動推定・確定USD','予約USD','保守的な残額USD'],budget_rows)+'<p class="micro">上限判定は手動推定・確定記録と予約を保守的に加算します。同じ費用の推定から確定への振替・予約精算は未実装です。</p>'
    body+=form(session,'budget','<label>月</label><input type="month" name="month" value="'+month+'" required><label>月間上限USD（0は有料作業の予約不可）</label><input type="number" name="amount" min="0" max="1000000" step="0.000001" required> ',caption='月間上限を設定')
    groups={'model':'モデル別','day':'日別','month':'月別','department':'部門別','task':'タスク別'}
    for key,name in groups.items():
        body+='<h2>'+name+'</h2>'+table(['分類','推定／確定','情報源','USD'],[[e(r['label']),e('推定' if r['kind']=='estimated' else '確定'),e('手動記帳' if r['mode']=='manual' else 'モック'),e(r['amount']/1000000)] for r in data['groups'] if r['group']==key])
    body+='<h2>タスク費用予約</h2>'+table(['タスク','担当','予約USD','確定USD'],[[e(r['task_id']),e(r['agent']),e(r['reserved']/1000000),e(r['actual']/1000000 if r['actual'] is not None else '未確定')] for r in data['reservations']])
    body+='<h2>利用料金を手動記帳</h2>'+form(session,'cost','<label>モデル名</label><input name="model" required maxlength="80"><label>担当</label><select name="agent">'+''.join('<option>'+a+'</option>' for a in office.store.agents)+'</select><label>タスクID（任意）</label><input name="task" maxlength="64"><label>金額USD</label><input type="number" name="amount" required min="0" step="0.000001"><label>推定／確定</label><select name="kind"><option value="estimated">推定</option><option value="confirmed">請求等で確認済み</option></select><label>情報源</label><select name="mode"><option value="mock">モック</option><option value="manual">実利用の手動記帳</option></select><label>利用日</label><input type="date" name="date" required value="'+now().astimezone(JST).date().isoformat()+'"><label>根拠参照（秘密値禁止）</label><input name="evidence" required maxlength="500">',{'key':'cost-'+uuid4().hex},'記帳（課金なし）')
    return body


def monitor(office,session):
    db=office.db
    okay=db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    services=[['LINE API','未接続'],['既存LINE7日間配信','別リポジトリ・未接続・未検証'],['予約サービス','未選定・モックのみ'],['Instagram / Facebook','未接続'],['AIモデルAPI','未接続'],['SQLite','正常（quick_check）' if okay else '要確認'],['Webhook','本番入口なし・署名検証はモック試験済み']]
    from .validation import saved_assessment
    readiness=saved_assessment()
    body='<div class="notice">本番移行不可。外部接続・実LINE回帰・セキュリティ確認・代表承認が未完了です。</div>'+table(['対象','状態'],[[e(k),e(v)] for k,v in services])
    body+='<h2>定期業務の異常履歴</h2>'+table(['処理','参照','日時'],[[e(r[0]),e(r[1]),dt(r[2])] for r in db.execute("SELECT action,ref,created_at FROM growth_audit WHERE action IN ('local_failure_auto_stop','schedule_prepare_failure','attempt_limit_reached') ORDER BY id DESC LIMIT 100")])
    body+='<h2>定期業務</h2>'+table(['頻度','状態','次回'],[[e(r[0]),e(r[1]),dt(r[2])] for r in db.execute('SELECT cadence,state,next_due FROM growth_schedules')])
    body+='<h2>要対応の通知（画面内のみ）</h2>'+table(['問題','件数'],[[e(a['label']),e(a['count'])] for a in office.alerts()])
    body+='<h2>5部門長の担当管理（ローカル集計）</h2>'+table(['部門長','部下','担当タスク'],[[e(d['head']),e(', '.join(d['members'])),e(len(d['tasks']))] for d in office.coordinator.department_report()])
    body+='<h2>本番移行ゲート</h2>'+table(['条件','状態','根拠'],[[e(g['condition']),e(g['state']),e(g['note'])] for g in readiness['gates']])
    success=db.execute("SELECT MAX(e.created_at) FROM events e WHERE e.event='artifact_created'").fetchone()[0]
    mock_success=db.execute("SELECT MAX(created_at) FROM ix_attempts WHERE outcome='success'").fetchone()[0]
    webhook_count=db.execute('SELECT COUNT(*) FROM ix_webhooks').fetchone()[0]
    webhook_last=db.execute('SELECT MAX(processed_at) FROM ix_webhooks').fetchone()[0]
    rejected=db.execute("SELECT COUNT(*) FROM ix_audit WHERE event='webhook_rejected'").fetchone()[0]
    body+='<h2>Webhookモック処理</h2>'+table(['受理済みイベント','拒否記録','最終受理'],[[e(webhook_count),e(rejected),dt(webhook_last) if webhook_last else '未実行']])
    body+='<h2>最終正常処理</h2>'+table(['範囲','日時（日本時間）'],[['ローカル成果物保存',dt(success) if success else '未実行'],['モックAPI成功',dt(mock_success) if mock_success else '未実行'],['本番API成功','未接続・未計測']])
    errors=[dict(r) for r in db.execute("SELECT task_id,error_code,deadline FROM runs WHERE error_code IS NOT NULL ORDER BY rowid DESC")]
    body+='<h2>エラー履歴</h2>'+table(['タスク','コード','期限（日本時間）'],[[e(r['task_id']),e(r['error_code']),dt(r['deadline'])] for r in errors])
    body+='<h2>モックAPIエラー</h2>'+table(['操作','結果','コード'],[[e(r[0]),e(r[1]),e(r[2])] for r in db.execute("SELECT operation_id,outcome,code FROM ix_attempts WHERE outcome!='success'")])
    body+='<h2>原稿生成の処理エラー</h2>'+table(['処理','コード','日時（日本時間）'],[[e(r[0]),e(r[1]),dt(r[2])] for r in db.execute('SELECT command,code,created_at FROM pipeline_errors ORDER BY id DESC LIMIT 100')])
    body+='<h2>管理操作の監査ログ</h2>'+table(['担当','操作','参照','日時（日本時間）'],[[e(r[0]),e(r[1]),e(r[2]),dt(r[3])] for r in db.execute('SELECT actor,action,ref,created_at FROM office_audit ORDER BY id DESC LIMIT 100')])
    return body
