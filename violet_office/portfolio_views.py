"""Authenticated portfolio screens; all business resources carry an explicit scope."""
import html
import json
from datetime import timedelta
from uuid import uuid4
from .store import now
from .office import JST
from .web_views import e,dt,fields,table,card
from .portfolio import KINDS,STATES,CASE_KINDS,DOCS,OPERATIONS,METRICS,CANDIDATES
from .integrations.adapters import Principal

LABELS={'approval_pending':'代表承認待ち','approved':'承認済み・外部未実行','rejected':'却下','changes_requested':'修正依頼','superseded':'旧版','recorded_manual':'手動記録・外部結果未照合','requested':'受付済み','scheduled_local':'手動予約記録','in_progress':'進行中','completed':'完了記録','cancelled':'取消','pending':'未着手','review_pending':'レビュー待ち','paused':'停止中','running':'ローカル処理中'}

def principal(s): return Principal(s['role'],s['username'])
def opts(name,values,selected=''): return '<select name="'+name+'">'+''.join('<option value="'+e(k)+'"'+(' selected' if k==selected else '')+'>'+e(v)+'</option>' for k,v in values.items())+'</select>'
def inp(name,label,value='',kind='text',required=True): return '<label>'+label+'</label><input name="'+name+'" type="'+kind+'" value="'+e(value)+'"'+(' required' if required else '')+'>'
def pf(s,action,body='',data=None,caption='保存'):
    if s.get('portfolio_readonly') or s['role'] not in ('representative','business_operator'): return '<p class="muted">この事業は閲覧のみ許可されています。</p>'
    return '<form method="post" action="/actions/'+action+'">'+fields({'csrf':s['csrf'],'nonce':uuid4().hex,**(data or {})})+body+'<button>'+caption+'</button></form>'
def periods(params):
    today=now().astimezone(JST).date(); start=params.get('start',[today.replace(day=1).isoformat()])[0]; end=params.get('end',[(today+timedelta(days=1)).isoformat()])[0]
    return start,end

def stats(data):
    rows=[]
    for key,label in METRICS.items():
        metric=data['metrics'][key]
        value='未計測' if metric['recorded'] is None else str(metric['total'])+'（期間全日分あり）' if metric['complete'] else str(metric['recorded'])+'（部分計測・期間合計は未確定）'
        rows.append([label,e(value)])
    for key,label in [('revenue_yen','記録済み売上（円）'),('expense_yen','記録済み経費（円）'),('profit_yen','記録済み差額（円、API費別）'),('profit_margin_percent','記録済み差額率%')]:
        rows.append([label,e(data[key] if data[key] is not None else '未計測')])
    for key,label in [('confirmed','確定記帳API料金（USD、実請求未照合）'),('estimated','推定API料金（USD）')]:
        value=data['api_microusd'][key]
        rows.append([label,e(value/1000000 if value is not None else '未計測')])
    rows.append(['ローカル処理時間（秒、代表作業時間とは別）',e(data['local_processing_seconds'] if data['local_processing_seconds'] is not None else '未計測')])
    rows.append(['システムに登録された問い合わせ',e(data['registered_inquiries'] if data['registered_inquiries'] is not None else '未計測')])
    for key,label in [('registered_booking_requests','登録済み予約希望'),('manual_booking_records','手動予約申告件数（外部未照合）'),('manual_contract_records','手動契約申告件数（外部未照合）')]:
        rows.append([label,e(data[key] if data[key] is not None else '未計測')])
    return table(['指標','同一期間の値'],rows)


def readable(value):
    labels={'businesses':'事業別実績','business_id':'事業ID','period':'集計期間','revenue_yen':'記録済み売上円','expense_yen':'記録済み経費円','profit_yen':'記録済み差額円・API費別','profit_margin_percent':'差額率%','metrics':'計測状況','recorded':'記録済み','total':'全日分の合計','days':'計測日数','complete':'全日分あり','owner_minutes':'代表作業時間分','inquiries':'問い合わせ','bookings':'予約','contracts':'契約','goal_gaps':'目標との差','goals':'目標','improvements':'改善案','approvals':'必要な承認','today_priority':'優先業務','api_microusd':'API手動記帳額（microUSD）','confirmed':'確定記帳','estimated':'推定記帳'}
    if value is None: return '未計測・未設定'
    if isinstance(value,dict): return {labels.get(k,k):readable(v) for k,v in value.items()}
    if isinstance(value,list): return [readable(v) for v in value]
    return value


def home(o,s,params):
    p=principal(s); owner=s['role']=='representative'
    if not owner:
        o.portfolio.access(p, next((r[0] for r in o.db.execute('SELECT business_id FROM portfolio_acl WHERE username=?',(s['username'],))),None))
        rows=o.db.execute('SELECT b.* FROM portfolio_businesses b JOIN portfolio_acl a ON a.business_id=b.id WHERE a.username=? ORDER BY b.priority,b.id',(s['username'],)).fetchall()
        return '<p>許可された事業だけ表示します。</p>'+table(['事業','状態'],[[f'<a href="/business?business={e(r["id"])}">'+e(r['name'])+'</a>',e(STATES[r['status']])] for r in rows])
    start,end=periods(params); data=o.portfolio.dashboard(p,start,end)
    body='<div class="notice">共通AI社員28人を利用します。旧LINE・セミナー・顧客データは未配賦で、事業へ自動コピーしません。金額は円、API料金はUSD別管理。未計測を0とみなしません。</div>'
    body+=pf(s,'portfolio_seed',caption='確認済みの初期6事業を登録（既存IDは維持）')
    body+='<form method="get">'+inp('start','集計開始日（JST）',start,'date')+inp('end','集計終了日（含まない）',end,'date')+'<button>同じ期間で集計</button></form>'
    body+='<div class="grid">'+card('全事業の記録済み売上（円）',data['revenue_yen'])+card('全事業の記録済み経費（円）',data['expense_yen'])+card('記録済み差額（円、API費別）',data['profit_yen'])+'</div><p>'+e(data['total_note'])+'</p>'
    body+='<div class="business-grid">'
    for b in data['businesses']:
        body+='<article class="panel"><h2><a href="/business?business='+e(b['business_id'])+'&amp;start='+e(start)+'&amp;end='+e(end)+'">'+e(b['name'])+'</a></h2><span class="badge">'+e(STATES[b['status']])+'</span>'
        for key,label in [('revenue_yen','記録済み売上（円）'),('expense_yen','記録済み経費（円）'),('profit_yen','差額（円、API費別）')]:
            body+='<div class="business-stat"><span>'+label+'</span><strong>'+e(b[key] if b[key] is not None else '未計測')+'</strong></div>'
        body+='<details><summary>件数・負担・API料金</summary>'+stats(b)+'</details></article>'
    body+='</div>'
    body+='<h2>事業の追加</h2>'+pf(s,'portfolio_create',inp('business','事業ID')+inp('name','事業名')+'<label>業種</label>'+opts('kind',KINDS),caption='準備中として追加')
    body+='<h2>緊急停止</h2><p>'+('停止中' if o.portfolio.halted() else 'ローカル業務受付中（本番未接続）')+'</p>'+pf(s,'portfolio_halt',data={'halted':'0' if o.portfolio.halted() else '1'},caption='停止を解除' if o.portfolio.halted() else '事業横断の新規実行を停止')
    body+='<h2>今日の優先業務</h2>'+table(['事業','担当','業務','優先度','状態'],[[e(t['business_id']),e(t['agent']),e(t['kind']),e(t['priority']),e(LABELS.get(t['status'],t['status']))] for t in data['today_priority']])
    body+='<h2>承認待ち</h2>'+table(['事業','種別','状態'],[[f'<a href="/business?business={e(r[0])}">'+e(r[0])+'</a>',e(r[1]),'代表承認待ち'] for r in o.db.execute("SELECT business_id,kind FROM portfolio_operations WHERE status='approval_pending' UNION ALL SELECT business_id,kind FROM portfolio_documents WHERE status='approval_pending' LIMIT 100")])
    body+='<h2>AI経営会議・改善案</h2>'+pf(s,'portfolio_report',inp('start','開始日',start,'date')+inp('end','終了日（含まない）',end,'date'),caption='承認待ちの経営レポートを作成')+'<p><a href="/business-reports">今月の改善提案・レポート・必要承認を確認</a></p>'
    body+='<h2>共通AI社員の状態（業務内容は各事業内）</h2>'+table(['社員','稼働タスク数'],[[e(a),e(o.db.execute("SELECT COUNT(*) FROM tasks WHERE agent=? AND status='running'",(a,)).fetchone()[0])] for a in o.store.agents])
    return body


def business(o,s,params):
    p=principal(s); bid=params.get('business',[''])[0]; service=o.portfolio
    service.access(p,bid); owner=s['role']=='representative'
    b=o.db.execute('SELECT * FROM portfolio_businesses WHERE id=?',(bid,)).fetchone(); start,end=periods(params)
    if not owner:
        acl=o.db.execute('SELECT can_write FROM portfolio_acl WHERE business_id=? AND username=?',(bid,s['username'])).fetchone()
        if not acl[0]: s={**s,'portfolio_readonly':True}
    data=service.summary(p,bid,start,end); hidden={'business':bid}
    body='<div class="notice">事業ID '+e(bid)+' / '+e(b['name'])+'。この事業の顧客・原稿・規則だけを表示します。秘密値・センシティブな相談本文は入力しないでください。</div>'
    body+='<form method="get">'+fields({'business':bid})+inp('start','集計開始日（JST）',start,'date')+inp('end','集計終了日（含まない）',end,'date')+'<button>この期間で集計</button></form>'
    body+='<p>状態：'+e(STATES[b['status']])+' / 優先度 '+str(b['priority'])+'</p><pre>'+e({'サービス':b['services'] or '未設定','料金':b['pricing'] or '未設定','対象':b['audience'] or '未設定','地域':b['region'] or '未設定'})+'</pre>'
    if owner:
        body+='<details class="panel"><summary>事業設定・提供状況・優先度</summary>'+pf(s,'portfolio_update',inp('name','事業名',b['name'])+'<label>状態</label>'+opts('status',STATES,b['status'])+''.join(inp(k,label,b[k],required=False) for k,label in [('services','サービス内容'),('pricing','料金（未確定部分は明記）'),('audience','対象顧客'),('region','対応地域')])+inp('priority','事業優先度1〜5',b['priority'],'number')+'<label><input type="checkbox" name="confirmed" value="1">提供状況・料金・地域の確認済み部分を代表が確認</label>',{**hidden,'revision':b['revision']},'設定を更新')+'</details>'
        body+='<details class="panel"><summary>事業担当者の権限</summary>'+pf(s,'portfolio_grant',inp('username','仮名の担当者ID')+inp('password','新規アカウントのパスワード（12文字以上）','','password',False)+'<label><input type="checkbox" name="write" value="1">この事業の登録・下書き作成を許可（承認権限なし）</label>',hidden,'担当者を作成・この事業だけ許可')
        for acl in o.db.execute('SELECT * FROM portfolio_acl WHERE business_id=?',(bid,)):
            body+='<p>'+e(acl['username'])+'</p>'+pf(s,'portfolio_revoke',data={**hidden,'username':acl['username']},caption='この事業の権限を撤回')
        body+='</details>'
    body+='<details class="panel"><summary>同じ期間の実績・目標</summary>'+stats(data)+'<p>目標と差：'+e(readable({'goals':data['goals'],'goal_gaps':data['goal_gaps']}))+'</p>'
    body+=pf(s,'portfolio_ledger',inp('key','重複防止キー',uuid4().hex)+inp('day','取引日',now().astimezone(JST).date().isoformat(),'date')+opts('kind',{'revenue':'売上','expense':'経費'})+inp('amount','金額（円）','','number')+opts('mode',{'actual':'実績申告','estimated':'推定'})+inp('evidence','証憑参照（個人情報を含めない）'),hidden,'売上・経費を記帳')
    body+=pf(s,'portfolio_measure',inp('day','計測日',now().astimezone(JST).date().isoformat(),'date')+opts('metric',METRICS)+inp('value','日別の計測値（明示の0も可）','','number')+opts('mode',{'actual':'実測申告','estimated':'推定'})+inp('evidence','根拠参照')+inp('previous','訂正時は最新の計測ID（任意）',required=False),hidden,'日別実績を保存')
    if owner: body+=pf(s,'portfolio_goal',inp('month','目標月',start[:7],'month')+opts('metric',METRICS)+inp('value','目標（空欄は未設定）',kind='number',required=False),hidden,'目標を設定')
    body+='</details>'
    contacts=o.db.execute('SELECT * FROM portfolio_contacts WHERE business_id=? ORDER BY created_at DESC LIMIT 100',(bid,)).fetchall()
    choices={'':'指定なし',**{c['id']:c['display_name'] for c in contacts if c['state']=='active'}}
    body+='<details class="panel"><summary>顧客・利用目的別同意</summary>'+pf(s,'portfolio_contact',inp('name','顧客表示名（必要最小限）')+inp('channel_ref','本人申告の連絡先または安全な参照ID（秘密値不可）'),hidden,'この事業だけに登録')
    body+=table(['顧客ID','表示名','連絡先/参照','状態'],[[e(c['id']),html.escape(c['display_name']),html.escape(c['channel_ref']),e(c['state'])] for c in contacts])
    body+=pf(s,'portfolio_consent',opts('contact',{k:v for k,v in choices.items() if k})+opts('purpose',{'contact':'連絡','schedule':'予約・日程','photo':'写真の内部管理','followup':'フォロー'})+opts('granted',{'1':'本人同意あり','0':'撤回・停止'})+inp('notice','利用目的と本人確認記録の参照（本文・秘密値は不可）'),hidden,'目的別同意を記録')
    if owner:
        body+=pf(s,'portfolio_delete_request',opts('contact',{k:v for k,v in choices.items() if k}),hidden,'個人情報の削除申請（まだ削除しない）')
        for d in o.db.execute("SELECT * FROM portfolio_deletions WHERE business_id=? AND status='approval_pending'",(bid,)):
            body+=pf(s,'portfolio_delete_approve','<p>対象ID '+e(d['contact_id'])+'</p><label><input type="checkbox" name="checked" value="1" required>保存義務・目的・バックアップ対応を確認し、この事業の氏名/連絡先/写真の消去を承認</label>',{**hidden,'contact':d['contact_id']},'承認して個人情報を消去')
    body+='</details>'
    cases=o.db.execute('SELECT * FROM portfolio_cases WHERE business_id=? ORDER BY created_at DESC LIMIT 100',(bid,)).fetchall()
    caseopts={'':'指定なし',**{c['id']:CASE_KINDS[c['kind']]+' / '+c['subject'] for c in cases}}
    body+='<details class="panel"><summary>問い合わせ・予約希望・施工/制作・フォロー</summary>'+pf(s,'portfolio_case',inp('key','受付キー',uuid4().hex)+opts('kind',CASE_KINDS)+inp('subject','業務件名（相談本文・個人情報は保存しない）')+opts('contact',choices)+inp('start_at','希望開始日時（任意、JST）',kind='datetime-local',required=False)+inp('end_at','希望終了日時（任意、JST）',kind='datetime-local',required=False),hidden,'受付・予定を登録')
    for c in cases:
        body+='<details><summary>'+e(CASE_KINDS[c['kind']]+' / '+c['subject'])+' / '+e(LABELS.get(c['status'],c['status']))+'</summary><p>ID '+e(c['id'])+' / '+dt(c['start_at'])+'</p>'+pf(s,'portfolio_progress',opts('status',{'in_progress':'作業中','completed':'完了記録','cancelled':'取消'}),{**hidden,'id':c['id']},'進捗を記録')+'</details>'
    body+='</details>'
    body+='<details class="panel"><summary>施工前後の写真（非公開）</summary><p>PNG/JPEG・2MiB以下。位置情報等のメタデータ付き写真は事前除去が必要。写真はAIへ渡さず、この事業の権限内で保存します。公開利用は別途同意・承認が必要です。</p>'
    if not s.get('portfolio_readonly'):
        body+='<form method="post" action="/business-photo-upload" enctype="multipart/form-data">'+fields({'csrf':s['csrf'],'nonce':uuid4().hex,'business':bid})+opts('case',{k:v for k,v in caseopts.items() if k})+opts('stage',{'before':'施工前','after':'施工後'})+'<input name="photo" type="file" accept="image/png,image/jpeg" required><button>本人の写真管理同意を確認して保存</button></form>'
    for photo in o.db.execute('SELECT id,stage,state FROM portfolio_media WHERE business_id=?',(bid,)):
        body+='<p>'+e(photo['stage'])+' / '+e(photo['state'])+(' <a href="/business-photo?business='+e(bid)+'&id='+e(photo['id'])+'">権限付きダウンロード</a>' if photo['state']=='private' else '')+'</p>'
    body+='</details>'
    body+='<details class="panel"><summary>下書き・見積もり・品質確認</summary>'+pf(s,'portfolio_document',opts('kind',DOCS[b['kind']])+opts('case',caseopts)+inp('parent','修正元の文書ID（任意）',required=False)+'<label>修正本文（任意・公開可能な情報だけ）</label><textarea name="body" maxlength="8000"></textarea>',hidden,'固定テンプレートの下書きを作成')
    documents=o.db.execute('SELECT * FROM portfolio_documents WHERE business_id=? ORDER BY created_at DESC LIMIT 100',(bid,)).fetchall()
    for d in documents:
        body+='<details><summary>'+e(d['kind'])+' / '+e(LABELS.get(d['status'],d['status']))+'</summary><p>ID '+e(d['id'])+'</p><pre>'+e(d['body'])+'</pre>'
        if owner and d['status']=='approval_pending':
            for decision,title in [('approve','内容を承認（送信しない）'),('changes_requested','修正依頼'),('reject','却下')]:
                body+=pf(s,'portfolio_document_decide','<label><input name="checked" type="checkbox" value="1">事実・条件・法令/公式規約・同意の確認済み</label>',{**hidden,'id':d['id'],'decision':decision},title)
        body+='</details>'
    body+='</details>'
    body+='<details class="panel"><summary>代表承認センター（外部操作は手動）</summary>'+pf(s,'portfolio_operation',inp('key','操作キー',uuid4().hex)+opts('kind',OPERATIONS)+opts('case',caseopts)+opts('document',{'':'指定なし',**{d['id']:d['kind']+' / '+d['id'] for d in documents}}),hidden,'操作案を承認待ちに登録')
    for op in o.db.execute('SELECT * FROM portfolio_operations WHERE business_id=? ORDER BY created_at DESC LIMIT 100',(bid,)):
        body+='<p>'+e(OPERATIONS[op['kind']])+' / '+e(LABELS.get(op['status'],op['status']))+'</p>'
        if owner and op['status']=='approval_pending':
            body+=''.join(pf(s,'portfolio_operation_decide','<label><input type="checkbox" name="checked" value="1">対象・原稿・同意・条件を代表が確認</label>',{**hidden,'id':op['id'],'decision':d},title) for d,title in [('approve','承認（実行しない）'),('reject','却下')])
        elif owner and op['status']=='approved': body+=pf(s,'portfolio_manual',inp('evidence','既に手動実施した操作の証跡参照。外部結果は未照合'),{**hidden,'id':op['id']},'手動実施の申告だけ記録（APIは呼ばない）')
    body+='</details>'
    body+='<details class="panel"><summary>共通AI社員への割当・進捗・API料金</summary>'+pf(s,'portfolio_task',inp('key','タスクキー',uuid4().hex)+opts('kind',{k:k+' / '+','.join(v) for k,v in CANDIDATES.items()})+inp('instruction','この事業の業務指示（個人情報・秘密値不可）')+inp('priority','タスク優先度1〜5','3','number')+inp('agent','担当ID（空欄は専門分野内で負荷を確認）',required=False),hidden,'必要な担当だけ割当')
    tasks=o.db.execute('SELECT p.*,t.agent,t.status FROM portfolio_tasks p JOIN tasks t ON t.id=p.task_id WHERE p.business_id=? ORDER BY p.priority,p.created_at LIMIT 100',(bid,)).fetchall()
    for t in tasks:
        body+='<details><summary>'+e(t['agent']+' / '+t['kind'])+' / '+e(LABELS.get(t['status'],t['status']))+'</summary><p>ID '+e(t['task_id'])+'</p><pre>'+e(t['instruction'])+'</pre>'
        body+=pf(s,'portfolio_run',data={**hidden,'id':t['task_id']},caption='ローカル処理（モデルAPI未接続）')+pf(s,'portfolio_task_control',opts('control',{'priority':'優先度変更','pause':'未着手を停止','resume':'再開'})+inp('priority','優先度1〜5',t['priority'],'number'),{**hidden,'id':t['task_id']},'変更')
        result=o.db.execute('SELECT * FROM portfolio_results WHERE business_id=? AND task_id=?',(bid,t['task_id'])).fetchone()
        if result: body+='<pre>'+e(result['body'])+'</pre><p>ローカル経過秒 '+e(result['elapsed_seconds'])+'</p>'
        body+='</details>'
    if owner:
        body+=pf(s,'portfolio_budget',inp('month','月',start[:7],'month')+inp('amount','事業別API予算（USD）','0'),hidden,'予算上限を設定（全体上限は変更しない）')
        body+=pf(s,'portfolio_cost',inp('key','料金記帳キー',uuid4().hex)+opts('task',{t['task_id']:t['agent']+' / '+t['task_id'] for t in tasks})+inp('model','モデル/提供元の記帳名')+inp('amount','手動記帳額（USD）')+opts('kind',{'confirmed':'確定記帳（請求API未照合）','estimated':'推定記帳'}),hidden,'既存経費台帳へ1回だけ記帳・配賦')
    body+='</details>'
    if owner: body+='<details class="panel"><summary>第9段階セミナーとの明示的な関連付け</summary>'+pf(s,'portfolio_seminar',inp('id','既存セミナーID（参加者データはコピーしない）'),hidden,'この事業との参照だけ追加')+'<pre>'+e([dict(r) for r in o.db.execute('SELECT seminar_id FROM portfolio_seminar_links WHERE business_id=?',(bid,))])+'</pre></details>'
    body+='<details class="panel"><summary>事業内の操作・担当履歴</summary><pre>'+e([dict(r) for r in o.db.execute('SELECT actor,event,ref,detail,created_at FROM portfolio_events WHERE business_id=? ORDER BY id DESC LIMIT 50',(bid,))])+'</pre><pre>'+e([dict(r) for r in o.db.execute('SELECT agent,reason,created_at FROM portfolio_assignments WHERE business_id=? ORDER BY id DESC LIMIT 50',(bid,))])+'</pre></details>'
    return body


def reports(o,s):
    o.portfolio.access(principal(s),owner=True)
    body='<div class="notice">AI01のローカル集計です。因果関係・売上効果を断定せず、改善は自動適用しません。承認も外部操作の承認とは別です。</div>'
    for r in o.db.execute('SELECT * FROM portfolio_reports ORDER BY created_at DESC LIMIT 30'):
        body+='<details class="panel"><summary>'+e(r['start_day']+' ～ '+r['end_day'])+' / '+e(LABELS.get(r['status'],r['status']))+'</summary><pre>'+e(readable(json.loads(r['body'])))+'</pre>'
        if r['status']=='approval_pending': body+=''.join(pf(s,'portfolio_report_decide',data={'id':r['id'],'decision':d},caption=caption) for d,caption in [('approve','内容の確認を承認（改善は実行しない）'),('reject','却下')])
        body+='</details>'
    return body
