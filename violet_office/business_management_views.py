"""Server-rendered, no-script business and shared employee editors."""
import json
from uuid import uuid4
from .web_views import e,fields,table
from .portfolio_views import pf
from .integrations.adapters import Principal
from .business_readiness import KINDS_READY
from .business_readiness_views import STATUS
from .store import now
from .seminars import JST

STATES={'preparing':'準備中','active':'運営中','paused':'停止中'}
def actor(s):return Principal(s['role'],s['username'])
def input_(key,label,value='',kind='text',required=False):
    identifier=key+'-'+uuid4().hex
    return '<label for="'+identifier+'">'+e(label)+'</label><input id="'+identifier+'" name="'+key+'" type="'+kind+'" value="'+e('' if value is None else value)+'"'+(' required' if required else '')+'>'
def select(key,label,values,current=''):
    return '<label for="'+key+'">'+e(label)+'</label><select id="'+key+'" name="'+key+'">'+''.join('<option value="'+e(k)+'"'+(' selected' if k==current else '')+'>'+e(v)+'</option>' for k,v in values.items())+'</select>'
def check(key,label,checked=False):return '<label><input type="checkbox" name="'+key+'" value="1"'+(' checked' if checked else '')+'> '+e(label)+'</label>'
def link(b):return '/business-management?business='+e(b)
def home(o,s,params):
    p=actor(s);b=params.get('business',[''])[0] or None
    if not b and s['role']=='business_operator':
        from .sales_views import business_links
        return business_links(o,s).replace('/sales?','/business-management?')
    if b:o.readiness.access(p,b)
    else:o.readiness.access(p,None,True)
    owner=s['role']=='representative'
    body='<p><a href="/business-management">事業管理</a> / <a href="/ai-management'+('?business='+e(b) if b else '')+'">AI社員管理</a></p><p class="notice">実送信・予約確定・決済・公開は行いません。変更内容の確認→代表承認→適用の3操作で保存します。</p>'
    if params.get('result',[''])[0] in ('applied','reviewed'):body+='<p role="status" class="notice">'+('承認済みの設定を適用しました。外部送信は行っていません。' if params['result'][0]=='applied' else '承認・却下を記録しました。適用は別操作です。')+'</p>'
    if not b:
        body+='<h2>登録済み事業</h2>'+('<p><a class="button" href="/business-management?new=1">＋ 新規事業を追加</a></p>' if owner else '')
        body+=table(['事業','状態'],[['<a href="'+link(r['business_id'])+'">'+e(r['name'])+'</a>',e(STATES.get(r['status'],r['status']))] for r in o.readiness.catalog(p)])
        if params.get('new') and owner:body+=editor(o,s,None,params)
    else:
        row=o.db.execute('SELECT * FROM portfolio_businesses WHERE id=?',(b,)).fetchone();body+='<h2>'+e(row['name'])+'</h2>'
        body+='<p><a href="/business?business='+e(b)+'">顧客・問い合わせ・面談予約・収支・KPI</a> / <a href="/sales?business='+e(b)+'">営業案件</a> / <a href="/ai-workflow?business='+e(b)+'">業務進捗・引き継ぎ</a></p>'
        month=params.get('month',[now().astimezone(JST).strftime('%Y-%m')])[0]
        from datetime import date
        start=date.fromisoformat(month+'-01');end=date(start.year+(start.month==12),1 if start.month==12 else start.month+1,1)
        data=o.business_operations.summary(p,b,start.isoformat(),end.isoformat());goal=o.db.execute('SELECT * FROM bm_month_goals WHERE business_id=? AND month=?',(b,month)).fetchone()
        body+='<form method="get">'+fields({'business':b})+input_('month','集計・目標の対象月',month,'month',True)+'<button>対象月を表示</button></form>'
        body+=table(['月次指標（円）','実績','目標'],[[label,e('未計測' if data.get(k) is None else data[k]),e('未設定' if not goal or goal[k] is None else goal[k])] for k,label in [('revenue_yen','売上'),('profit_yen','利益')]])
        meta=o.db.execute('SELECT * FROM bm_profiles WHERE business_id=?',(b,)).fetchone()
        if meta:body+='<p>提供サービス：'+e(meta['provided_services'])+' / 対象顧客：'+e(meta['audience'])+' / 事業責任者：'+e(meta['manager'] or '未設定')+'</p>'
        assigned=[r['agent'] for r in o.db.execute('SELECT agent FROM bf_ai_assignments WHERE business_id=? AND active=1 ORDER BY agent',(b,))]
        body+='<p>担当AI社員：'+e(' / '.join(assigned) or '未割当')+'</p>'
        body+='<p>経費：'+e('未計測' if data.get('expense_yen') is None else data['expense_yen'])+'円。目標は実績に加算しません。収支の計上基準は既存の事業別集計設定を使用します。</p>'
        body+='<p>'+e(data['finance_definition'])+' / '+e(data['limitations'])+'</p>'
        body+=table(['事業内の登録・確認件数','件数'],[[label,e(data['counts'][k])] for k,label in [('customers','顧客登録数'),('new_customers','当月の新規顧客'),('interview_bookings','面談予約の確認記録'),('interviews_completed','面談完了の確認記録'),('contracts_recorded','契約の確認記録')]])
        pending=sum(r['status']=='approval_pending' for r in o.readiness.listing(p,b))
        body+='<p>事業・担当設定の承認待ち：'+e(pending)+'件。顧客への案内・見積・予約の承認状況は営業案件画面で確認してください。</p>'
        if owner:body+=editor(o,s,b,params)
    body+=requests(o,s,b)
    return body

def editor(o,s,b,params):
    r=dict(o.db.execute('SELECT * FROM portfolio_businesses WHERE id=?',(b,)).fetchone()) if b else {};f=o.db.execute('SELECT * FROM bf_profiles WHERE business_id=?',(b,)).fetchone() if b else None;m=o.db.execute('SELECT * FROM bm_profiles WHERE business_id=?',(b,)).fetchone() if b else None
    month=params.get('month',[now().astimezone(JST).strftime('%Y-%m')])[0];g=o.db.execute('SELECT * FROM bm_month_goals WHERE business_id=? AND month=?',(b,month)).fetchone() if b else None
    users={r['username']:r['username'] for r in o.db.execute("SELECT username FROM office_users WHERE role IN ('representative','business_operator') ORDER BY username")}
    body='<h2>'+('事業情報の編集・停止・再開' if b else '新規事業登録')+'</h2><form method="post" action="/business-management-confirm">'+fields({'csrf':s['csrf'],'key':uuid4().hex,'business':b or '', 'kind':'update' if b else 'create','snapshot':o.readiness.snapshot(b)})
    for key,label,value,required in [('name','事業名',r.get('name',''),True),('overview','事業概要',r.get('services',''),True),('category','事業カテゴリー',f['category'] if f else '',True),('provided_services','提供サービス',m['provided_services'] if m else '',False),('audience','対象顧客',m['audience'] if m else r.get('audience',''),False)]:body+=input_(key,label,value,required=required)
    body+=select('manager','事業責任者（選択しても権限は付与されません）',{'':'未設定',**users},m['manager'] if m else '')+select('status','事業ステータス',STATES,r.get('status','preparing'))+check('confirmed','提供状況と事業内容を代表が確認済み',bool(r.get('offering_confirmed')))+input_('month','目標の対象月',month,'month',True)
    for k,label in [('revenue_yen','目標売上（円・空欄は未設定）'),('profit_yen','目標利益（円・空欄は未設定、赤字目標は負数）')]:body+=input_(k,label,g[k] if g else '', 'number')
    return body+'<p>料金・地域・既存顧客・既存実績は変更しません。停止時も履歴を保持し、再開しても自動実行しません。</p><button>変更内容を確認</button></form>'

def employees(o,s,params):
    p=actor(s);b=params.get('business',[''])[0] or None
    if not b and s['role']=='business_operator':
        from .sales_views import business_links
        return business_links(o,s).replace('/sales?','/ai-management?')
    o.readiness.access(p,b,b is None)
    body='<p><a href="/business-management'+('?business='+e(b) if b else '')+'">事業管理へ</a></p><h2>既存AI社員28人</h2><p>社員の定義は変更しません。1人で複数事業を兼務できます。担当範囲は説明であり、実際のアクセスは限定権限で制御します。</p>'
    catalog=o.readiness.catalog(p,b) if s['role']!='representative' else o.readiness.catalog(p)
    body+='<form method="get">'+select('business','担当を確認・変更する事業',{'':'事業を選択',**{r['business_id']:r['name'] for r in catalog}},b or '')+'<button>事業を表示</button></form>'
    rows=o.readiness.contract(p,b)['employees']
    for row in rows:
        a=row['id'];v=row['assignment'];body+='<details class="panel"><summary>'+e(a)+' '+e(row['role'])+'</summary><p>既存の役割：'+e(row['role'])+'</p>'
        assignments=[]
        for c in catalog:
            current=o.db.execute('SELECT active,role_note FROM bf_ai_assignments WHERE business_id=? AND agent=?',(c['business_id'],a)).fetchone()
            if current:assignments.append(c['name']+'：'+current['role_note']+('（担当中）' if current['active'] else '（解除済み・履歴保持）'))
        body+='<p>担当事業：'+e(' / '.join(assignments) or '未割当')+'</p>'
        if b:
            count=o.db.execute("SELECT COUNT(*) FROM tasks t WHERE t.agent=? AND t.status NOT IN ('completed','rejected','superseded') AND (t.id IN (SELECT task_id FROM portfolio_tasks WHERE business_id=?) OR t.id IN (SELECT task_id FROM wf_tasks WHERE business_id=?) OR t.id IN (SELECT task_id FROM s14_work WHERE business_id=?))",(a,b,b,b)).fetchone()[0]
            body+='<p>当該事業の未完了タスク：'+e(count)+'件（解除しても削除しません）。</p>'
        if b and s['role']=='representative':
            if not o.db.execute('SELECT 1 FROM bf_profiles WHERE business_id=?',(b,)).fetchone():body+='<p>先に事業管理画面でカテゴリー等を確認・保存してください。</p>'
            else:
                body+='<form method="post" action="/business-management-confirm">'+fields({'csrf':s['csrf'],'key':uuid4().hex,'business':b,'kind':'assign','snapshot':o.readiness.snapshot(b),'agent':a})+input_('role_note','事業内の担当業務',v['role_note'] if v else '',required=True)+input_('scope_note','対応可能な範囲',v['scope_note'] if v else '',required=True)
                for k,label in [('business_read','事業情報の参照'),('work','内部業務の実行'),('finance_read','事業集計の参照')]:body+=check(k,label,bool(v and k in v['permissions']))
                body+=check('active','担当を有効にする',not v or v['active'])+'<button>担当変更を確認</button></form>'
                if v and v['active']:body+='<form method="post" action="/business-management-confirm">'+fields({'csrf':s['csrf'],'key':uuid4().hex,'business':b,'kind':'unassign','snapshot':o.readiness.snapshot(b),'agent':a})+'<p>未完了業務と履歴を削除せず、実行権限を停止します。引き継ぎは別途管理者が確認してください。</p><button>担当解除を確認</button></form>'
        body+='</details>'
    return body+requests(o,s,b)

def confirm(o,s,data):
    return o.store._transaction(lambda:_confirm(o,s,data))

def _confirm(o,s,data):
    p=actor(s);b=data.get('business') or None;o.readiness.access(p,b,True)
    if data.get('snapshot')!=o.readiness.snapshot(b):raise ValueError('他の操作で設定が変更されました。再表示して確認してください。')
    kind=data['kind']
    common={'csrf','key','business','kind','snapshot'}
    if kind in ('create','update'):
        allowed=common|{'name','overview','category','provided_services','audience','manager','status','confirmed','month','revenue_yen','profit_yen'}
        if set(data)-allowed:raise ValueError('Unknown form fields')
        v={k:data[k] for k in ('name','overview','category')}
        m={k:data.get(k,'') for k in ('provided_services','audience','status')};m.update(manager=data.get('manager') or None,confirmed=data.get('confirmed')=='1',month=data.get('month') or None)
        for k in ('revenue_yen','profit_yen'):
            raw=data.get(k,'')
            import re
            if raw and not re.fullmatch(r'-?[0-9]{1,13}',raw):raise ValueError('Whole yen required')
            m[k]=int(raw) if raw else None
        v['management']=m
        if b:
            f=o.db.execute('SELECT * FROM bf_profiles WHERE business_id=?',(b,)).fetchone();v.update(started_on=f['started_on'] if f else None,status=m['status'],enabled=m['status']!='paused',confirmed=m['confirmed'])
    elif kind=='assign':
        if set(data)-(common|{'agent','role_note','scope_note','business_read','work','finance_read','active'}):raise ValueError('Unknown assignment fields')
        v={k:data[k] for k in ('agent','role_note','scope_note')};v.update(permissions=[k for k in ('business_read','work','finance_read') if data.get(k)=='1'],active=data.get('active')=='1')
    elif kind=='unassign':
        if set(data)-(common|{'agent'}):raise ValueError('Unknown release fields')
        v={'agent':data['agent']}
    else:raise ValueError('Known operation required')
    identifier=o.readiness.prepare(p,data['key'],kind,b,v)
    return '<h2>保存前の確認</h2><p>まだ事業・担当設定には反映していません。</p>'+requests(o,s,b,identifier)

def requests(o,s,b,identifier=None):
    p=actor(s);body='<h2>確認・承認・適用履歴</h2>'
    for r in o.readiness.listing(p,b):
        if identifier and r['id']!=identifier:continue
        view=json.loads(r['preview']);body+='<details class="panel"'+(' open' if identifier else '')+'><summary>'+e(KINDS_READY[r['kind']])+' / '+e(STATUS[r['status']])+('（要再確認）' if r['stale'] else '')+'</summary><h3>変更前</h3><pre>'+e(json.dumps(view['before'],ensure_ascii=False,indent=2))+'</pre><h3>変更後</h3><pre>'+e(json.dumps(view['after'],ensure_ascii=False,indent=2))+'</pre><p>'+e(' / '.join(view['impact']))+'</p><p>承認者：'+e(r['approved_by'] or '未承認')+' / 承認日時：'+e(r['approved_at'] or '未承認')+' / 適用者：'+e(r['applied_by'] or '未適用')+'</p>'
        hidden={'id':r['id'], 'business':b or '', 'management_ui':'1'}
        if s['role']=='representative':
            if r['status']=='approval_pending':
                for decision,label in [('approve','確認した内容を承認（まだ適用しない）'),('reject','却下')]:
                    if decision=='approve' and r['stale']:continue
                    body+=pf(s,'br_decide',check('checked','変更前後・権限・未完了業務への影響を確認'),{**hidden,'decision':decision},label)
            if r['status']=='approved' and not r['stale']:body+=pf(s,'br_apply',data=hidden,caption='承認済みの設定を適用')
        body+='</details>'
    return body
