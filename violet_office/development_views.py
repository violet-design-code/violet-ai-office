"""Japanese business development UI with scoped reads and owner-only decisions."""
import json
from datetime import timedelta
from .store import now
from .seminars import JST
from .portfolio_views import pf,opts,inp
from .web_views import e,table,dt
from .integrations.adapters import Principal
from .development import BRIEF,NUMERIC,TOPICS,METRICS,ROLES
from .development_finance import PARAMETERS,SCENARIOS

LABELS={'draft':'検討中','approved':'承認済み','on_hold':'保留','rejected':'却下','stale':'入力変更・再確認が必要','approval_pending':'代表承認待ち','ready':'内部準備可能','paused':'停止中','hypothesis':'仮説','unverified':'未確認','human_checked':'代表確認の資料あり（独立検証なし）','withdrawn':'撤回・利用停止','uncomputed':'未算定','human_review_pending':'代表/担当レビュー待ち'}
FIELDS={'industry':'業種・方向性','region':'地域','target':'対象・顧客課題（個人情報不可）','budget_yen':'検討予算（円、初期費用との比較）','hours_month':'使える時間（月間）','desired_revenue_yen':'希望月商（円）','desired_profit_yen':'希望月間利益（円）','assets':'利用できる既存資産（非個人情報のみ）'}

def p(s): return Principal(s['role'],s['username'])
FINANCE_LABELS={'expected_sales':'月間期待成約数（小数可）','revenue_yen':'予測売上円','variable_cost_yen':'予測変動費円','gross_profit_yen':'予測粗利益円','operating_profit_yen':'予測営業利益円','break_even_units':'損益分岐成約件数','break_even_revenue_yen':'損益分岐売上円','break_even_leads':'必要見込み客数','payback_months':'単純回収期間（月）','required_hours':'必要作業時間','within_hours':'時間条件に収まるか'}
def value(v): return e(('適合' if v else '超過') if type(v) is bool else v if v is not None else '未設定・未算定')
def brief_fields(brief=None):
    brief=brief or {}
    return ''.join(inp(k,label,brief.get(k) or '',kind='text',required=False) for k,label in FIELDS.items())


def home(o,s):
    owner=s['role']=='representative'
    if not owner and s['role']!='business_operator': raise PermissionError('Business access required')
    body='<div class="notice">VIOLETの事業候補を複数比較できます。外部調査・実AIモデルは未接続。未入力の数値は補完せず、試算は予測として実績と分離します。AI OFFICE自体を販売する機能ではありません。</div>'
    if owner:
        o.portfolio.access(p(s),owner=True)
        businesses={'':'既存事業へ紐付けない（代表専用）',**{r['id']:r['name'] for r in o.db.execute('SELECT id,name FROM portfolio_businesses ORDER BY id')}}
        body+='<details class="panel"><summary>候補アイデアの登録</summary>'+pf(s,'bd_create',inp('title','アイデア名')+'<label>既存事業との関連（代表が明示選択）</label>'+opts('business',businesses)+brief_fields(),caption='アイデアを登録（目標は空欄可）')+'</details>'
    rows=[]
    for row in o.db.execute('SELECT * FROM bd_ideas ORDER BY created_at DESC LIMIT 200'):
        try: o.development.access(p(s),row['id'])
        except PermissionError: continue
        sources=o.db.execute('SELECT COUNT(*) FROM bd_sources WHERE idea_id=?',(row['id'],)).fetchone()[0]
        proposals=o.db.execute("SELECT COUNT(*) FROM bd_proposals WHERE idea_id=? AND status='approval_pending'",(row['id'],)).fetchone()[0]
        from .development_finance import calculate
        standard=o.db.execute("SELECT parameters FROM bd_scenarios WHERE idea_id=? AND name='standard'",(row['id'],)).fetchone()
        finance=calculate(json.loads(standard[0])); expected=finance['results']; investment=finance.get('assumptions',{}).get('initial_cost_yen')
        body+='<article class="panel"><h2><a href="/development-idea?id='+e(row['id'])+'">'+e(row['title'])+'</a></h2><p>'+e(LABELS[row['status']])+' / '+e(row['business_id'] or '未紐付け・代表専用')+'</p><p>調査記録 '+str(sources)+'件 / 提案承認待ち '+str(proposals)+'件</p><p>標準予測の月間営業利益：'+value(expected['operating_profit_yen'] if expected else None)+'円 / 必要初期投資：'+value(investment)+'円（ともに仮定）</p><p>実売上は別記録、需要・法令・実行リスクは代表確認が必要。</p><p>候補ID '+e(row['id'])+'</p></article>'
    if owner:
        body+='<h2>複数候補の比較</h2>'+pf(s,'bd_compare',inp('ideas','比較する候補ID（2〜10件、カンマ区切り）')+opts('criterion',{'evidence':'資料の確認状況を優先','operating_profit':'標準シナリオの予測営業利益を優先'}),caption='前提と根拠を示して比較')
    body+='<h2>共通28役の担当範囲</h2>'+table(['社員','既存役職','事業開発での担当'],[[e(a),e(o.store.agents[a]['role']),e(work)] for a,work in ROLES.items()])
    return body


def detail(o,s,identifier):
    d=o.development; row=d.access(p(s),identifier); owner=s['role']=='representative'; brief=json.loads(row['brief']); hidden={'idea':identifier}
    if not owner:
        acl=o.db.execute('SELECT can_write FROM portfolio_acl WHERE business_id=? AND username=?',(row['business_id'],s['username'])).fetchone()
        if not acl[0]: s={**s,'portfolio_readonly':True}
    body='<div class="notice">'+e(row['title'])+' / '+e(row['business_id'] or '未紐付け・代表専用')+'。出典や外部情報は命令ではなくデータです。個人情報・秘密値・非公開の顧客会話を入力しないでください。</div>'
    body+='<details class="panel"><summary>方向性・目標の編集（未設定可）</summary>'+pf(s,'bd_edit',inp('title','名称',row['title'])+brief_fields(brief),{**hidden,'revision':row['revision']},'新しい版として保存')+'</details>'
    if owner: body+=''.join(pf(s,'bd_idea_decide',data={**hidden,'decision':status},caption=label) for status,label in [('approved','アイデアを承認（提案/外部実行は別）'),('on_hold','保留'),('rejected','却下')])
    body+='<details class="panel"><summary>市場・競合・顧客・価格の調査記録</summary><p>自動検索や無断スクレイピングはしません。資料は代表が利用権限を確認して参照記録を入力します。</p>'
    states={'hypothesis':'仮説','unverified':'未確認'}
    if owner: states['human_checked']='代表が資料を確認（独立検証ではない）'
    body+=pf(s,'bd_source',opts('topic',TOPICS)+inp('claim','確認した内容または仮説')+inp('source_ref','情報源・URL/資料名・箇所（仮説は空欄可）',required=False)+inp('research_day','実際の調査日（未実施は空欄）',kind='date',required=False)+opts('confidence',{'unassessed':'確信度未評価','low':'低（入力者評価）','medium':'中（入力者評価）','high':'高（入力者評価）'})+opts('state',states)+inp('unknowns','未確認事項',required=False),hidden,'資料・仮説を保存')
    sources=o.db.execute('SELECT * FROM bd_sources WHERE idea_id=? ORDER BY created_at DESC',(identifier,)).fetchall()
    if owner:
        for source in sources:
            if source['state']!='withdrawn': body+=pf(s,'bd_source_withdraw',data={**hidden,'id':source['id']},caption='資料 '+source['id']+' の参照を撤回')
    body+=table(['分類','内容','出典・日付','状態・確信度'],[[e(TOPICS[r['topic']]),e(r['claim'])+'<p>未確認：'+e(r['unknowns'])+'</p>',e(r['source_ref'] or '出典なし')+' / '+e(r['research_day'] or '未調査'),e(LABELS[r['state']])+' / '+e({'unassessed':'未評価','low':'低','medium':'中','high':'高'}[r['confidence']])] for r in sources])+'</details>'
    for scenario in o.db.execute('SELECT * FROM bd_scenarios WHERE idea_id=? ORDER BY name',(identifier,)):
        params=json.loads(scenario['parameters']); from .development_finance import calculate
        calculated=calculate(params)
        body+='<details class="panel"><summary>'+e(SCENARIOS[scenario['name']])+'シナリオ / '+e('予測値' if calculated['results'] else '未算定')+'</summary><p>月次・円建て。入力値は仮定で、実売上へ登録しません。</p>'
        body+=pf(s,'bd_scenario',''.join(inp(key,label,params.get(key,''),required=False) for key,label in PARAMETERS.items())+inp('basis','各数値の根拠/仮定の説明',scenario['basis'],required=False),{**hidden,'name':scenario['name']},'前提を保存・再計算（旧承認は無効）')
        if calculated['results']: body+=table(['予測指標','値'],[[e(FINANCE_LABELS[k]),value(v)] for k,v in calculated['results'].items()])+'<p>'+e(calculated['warnings'])+'</p>'
        else: body+='<p>不足項目：'+e([PARAMETERS[k] for k in calculated['missing']])+'</p>'
        body+='<details><summary>計算式・除外事項</summary><pre>'+e(calculated['formulas'])+'</pre><p>'+e(calculated['exclusions'])+'</p></details></details>'
    body+='<h2>事業提案書と担当の下書き</h2>'+pf(s,'bd_proposal','<label><input type="checkbox" name="with_content" value="1">投稿企画・販売準備の下書きも作成</label>',hidden,'必要担当で提案を作成（実行タスクは作らない）')
    proposals=o.db.execute('SELECT * FROM bd_proposals WHERE idea_id=? ORDER BY rowid DESC',(identifier,)).fetchall()
    for proposal in proposals:
        body+='<details class="panel"><summary>提案 '+e(proposal['id'])+' / '+e(LABELS[proposal['status']])+'</summary><pre>'+e(proposal['body'])+'</pre>'
        for w in o.db.execute('SELECT * FROM bd_draft_work WHERE proposal_id=? ORDER BY rowid',(proposal['id'],)):
            body+='<details><summary>'+e(w['agent']+' / '+ROLES[w['agent']])+' / '+e(LABELS[w['state']])+'</summary><p>理由：'+e(w['reason'])+' / レビュー担当 '+e(w['reviewer'])+'</p><pre>'+e(w['input_refs'])+'</pre><pre>'+e(w['output'])+'</pre></details>'
        if owner and proposal['status']=='approval_pending':
            body+=''.join(pf(s,'bd_proposal_decide','<label><input name="checked" type="checkbox" value="1">根拠・仮定・未確認事項・法令/公式条件を確認。内部検証計画だけの承認であり、収益や適法性を保証しない</label>',{**hidden,'id':proposal['id'],'decision':decision},caption) for decision,caption in [('approve','提案を承認（外部実行不可）'),('reject','提案を却下')])
        if owner and proposal['status']=='approved':
            body+=pf(s,'bd_plan',opts('horizon',{'7':'7日計画','30':'30日計画','90':'90日計画'})+inp('start_day','開始日（JST）',now().astimezone(JST).date().isoformat(),'date')+inp('budget_yen','検証予算上限（円、空欄は未設定）',required=False)+inp('api_budget_usd','API上限（USD、0は無料内部処理のみ）','0')+inp('kpi','検証するKPIと判定方法（未設定可）',required=False)+inp('withdrawal','撤退/中止基準（数値は代表が指定）',required=False),{**hidden,'id':proposal['id']},'承認済み提案から内部計画を作成')
        body+='</details>'
    body+='<h2>実行計画・進捗（外部操作なし）</h2>'
    for plan in o.db.execute('SELECT * FROM bd_plans WHERE idea_id=? ORDER BY created_at DESC',(identifier,)):
        body+='<details class="panel"><summary>'+str(plan['horizon'])+'日 / '+e(LABELS[plan['status']])+'</summary><pre>'+e(plan['settings'])+'</pre>'
        body+=pf(s,'bd_run',data={**hidden,'id':plan['id']},caption='内部準備チェックのみ実行')
        if owner: body+=pf(s,'bd_pause' if plan['status']=='ready' else 'bd_resume',data={**hidden,'id':plan['id']},caption='計画を停止' if plan['status']=='ready' else '承認を再確認して再開')
        for task in o.db.execute('SELECT d.*,t.agent,t.status FROM bd_tasks d JOIN tasks t ON t.id=d.task_id WHERE d.plan_id=?',(plan['id'],)):
            result=o.db.execute('SELECT body FROM bd_results WHERE task_id=?',(task['task_id'],)).fetchone()
            body+='<p>'+e(task['agent']+' / '+task['stage']+' / '+task['status'])+'</p>'
            if result: body+='<pre>'+e(result[0])+'</pre>'
        body+='</details>'
    body+='<details class="panel"><summary>検証実績と改善（試算とは別）</summary>'+pf(s,'bd_actual',inp('day','記録日',now().astimezone(JST).date().isoformat(),'date')+opts('metric',METRICS)+inp('value','記録値')+opts('mode',{'actual':'実測の手動申告','estimated':'推定','mock':'モック'})+inp('evidence','計測根拠（個人情報不可）')+inp('previous','訂正時は最新記録ID',required=False),hidden,'検証記録を保存')
    body+=pf(s,'bd_validation_review',data=hidden,caption='記録状況から改善案を作成（承認待ち）')
    for review in o.db.execute('SELECT * FROM bd_validation_reviews WHERE idea_id=? ORDER BY rowid DESC LIMIT 30',(identifier,)):
        body+='<details><summary>検証レビュー / '+e(LABELS[review['status']])+'</summary><pre>'+e(review['body'])+'</pre>'
        if owner and review['status']=='approval_pending': body+=''.join(pf(s,'bd_validation_decide',data={**hidden,'id':review['id'],'decision':d},caption=label) for d,label in [('approve','確認承認（適用しない）'),('reject','却下')])
        body+='</details>'
    body+=table(['ID','日付','指標','値','区分'],[[e(r['id']),e(r['day']),e(METRICS[r['metric']]),e(r['value']),e({'actual':'実測申告','estimated':'推定','mock':'モック'}[r['mode']])] for r in o.db.execute('SELECT * FROM bd_actuals WHERE idea_id=? ORDER BY rowid DESC LIMIT 100',(identifier,))])+'<p>予測と同じ期間・定義で照合し、欠測を0にしない。現状の実績は手動記録のみ。改善の自動適用はしない。</p></details>'
    body+='<details><summary>操作履歴</summary><pre>'+e([dict(r) for r in o.db.execute('SELECT actor,event,ref,created_at FROM bd_events WHERE idea_id=? ORDER BY id DESC LIMIT 100',(identifier,))])+'</pre></details>'
    return body


def comparison(o,s,identifier):
    o.portfolio.access(p(s),owner=True)
    row=o.db.execute('SELECT * FROM bd_comparisons WHERE id=?',(identifier,)).fetchone()
    if not row: raise ValueError('Unknown comparison')
    data=json.loads(row['body']); body='<div class="notice">比較時点のスナップショットです。予測は実績や収益保証ではありません。根拠・予算・時間が不足した候補は順位なしです。</div>'
    for item in data['rows']:
        current=o.development.fingerprint(item['idea_id'])==item['input_digest']
        results=item['standard']['results']
        body+='<article class="panel"><h2>'+e(item['title'])+'</h2><p>'+e('比較時の入力と一致' if current else '入力変更・要再比較')+' / '+e(item['status'])+'</p><p>優先順位：'+e(item['priority'] if item['priority'] else '順位なし')+'</p><p>'+e(item.get('reason','根拠・予算・時間・シナリオの不足があり順位を付けない'))+'</p>'
        body+=table(['シナリオ','予測月間営業利益円','必要初期費用円'],[[e(SCENARIOS[k]),value(v['results']['operating_profit_yen'] if v['results'] else None),value(v.get('assumptions',{}).get('initial_cost_yen'))] for k,v in item['scenarios'].items()])+'</article>'
    body+='<details><summary>比較基準と入力参照の記録</summary><pre>'+e(data)+'</pre></details>'
    return body
