"""Business development drafts, approved internal validation, no outside executor."""
import json
from decimal import Decimal
from datetime import date,timedelta
from pathlib import Path
from uuid import uuid4
from .store import now
from .seminars import JST
from .content import encode,digest
from .portfolio import text
from .learning import risky
from .development_finance import calculate,number,SCENARIOS

TOPICS={'demand':'需要','customer':'顧客課題','competition':'競合','difference':'差別化','price':'価格帯','region':'地域性','channel':'集客経路'}
BRIEF={'industry','region','target','budget_yen','hours_month','desired_revenue_yen','desired_profit_yen','assets'}
NUMERIC={'budget_yen','hours_month','desired_revenue_yen','desired_profit_yen'}
METRICS={'inquiries':'問い合わせ','bookings':'予約','contracts':'契約','revenue_yen':'売上円','expense_yen':'経費円','hours':'作業時間'}
ROLES={
 'AI01':'統括・提案集約','AI02':'事業戦略・実行計画','AI03':'市場需要','AI04':'競合分析','AI05':'ターゲット・顧客課題','AI06':'改善・撤退条件','AI07':'集客戦略','AI08':'投稿企画','AI09':'販売準備原稿','AI10':'画像制作指示','AI11':'動画台本案','AI12':'動画編集指示','AI13':'準備スケジュール','AI14':'営業導線','AI15':'紹介条件の確認','AI16':'紹介文章案','AI17':'顧客返信案','AI18':'面談導線','AI19':'営業表現確認','AI20':'顧客対応手順','AI21':'LINE応答案','AI22':'LINE下書き計画','AI23':'予約手順','AI24':'フォロー案','AI25':'制限・運用設計','AI26':'接続未完了事項の整理','AI27':'財務試算','AI28':'品質・法令未確認事項'}
CORE=('AI03','AI04','AI05','AI27','AI02','AI07','AI14','AI20','AI25','AI28','AI01')


def migrate(store):
    if store.db.execute('SELECT 1 FROM office_migrations WHERE version=11').fetchone(): return
    sql=Path(__file__).resolve().parent.parent.joinpath('migrations/011_business_development.sql').read_text()
    def op():
        for statement in sql.split(';'):
            if statement.strip(): store.db.execute(statement)
        store.db.execute('INSERT OR IGNORE INTO office_migrations VALUES(11,?)',(now().isoformat(),))
    store._transaction(op)


class Development:
    def __init__(self,store,portfolio): self.store,self.db,self.portfolio=store,store.db,portfolio

    def access(self,p,idea=None,write=False,owner=False):
        if idea is None: self.portfolio.access(p,owner=True); return None
        row=self.db.execute('SELECT * FROM bd_ideas WHERE id=?',(idea,)).fetchone()
        if not row: raise ValueError('Unknown idea')
        self.portfolio.access(p,row['business_id'],write,owner or row['business_id'] is None)
        return row

    def event(self,p,idea,event,ref='',detail=None):
        self.db.execute('INSERT INTO bd_events(idea_id,actor,event,ref,detail,created_at) VALUES(?,?,?,?,?,?)',(idea,p.identity,event,ref,encode(detail or {}),now().isoformat()))

    def create(self,p,title,brief,business=None):
        self.portfolio.access(p,business,owner=True)
        if not isinstance(brief,dict) or set(brief)-BRIEF: raise ValueError('Known idea fields only')
        normalized={}
        for key in BRIEF:
            value=brief.get(key)
            normalized[key]=(str(number(value)) if value not in ('',None) else None) if key in NUMERIC else (text(value,1500) if value else '')
        identifier=uuid4().hex
        def op():
            self.db.execute('INSERT INTO bd_ideas VALUES(?,?,?,?,?,?,?,?)',(identifier,business,text(title,160),encode(normalized),1,'draft',p.identity,now().isoformat()))
            for name in SCENARIOS: self.db.execute('INSERT INTO bd_scenarios VALUES(?,?,?,?,?)',(identifier,name,'{}','',1))
            self.event(p,identifier,'idea_created',identifier,{'business_link_explicit':business is not None})
            return identifier
        return self.store._transaction(op)

    def invalidate(self,p,idea):
        self.db.execute("UPDATE bd_proposals SET status='stale' WHERE idea_id=? AND status IN ('approved','approval_pending')",(idea,))
        self.db.execute("UPDATE bd_plans SET status='paused' WHERE idea_id=?",(idea,))
        tasks=[r[0] for r in self.db.execute('SELECT t.task_id FROM bd_tasks t JOIN bd_plans p ON p.id=t.plan_id WHERE p.idea_id=?',(idea,))]
        for task in tasks:
            self.db.execute("UPDATE tasks SET status='paused' WHERE id=? AND status IN ('pending','running')",(task,))
            self.db.execute("UPDATE runs SET status='cancelled',error_code='bd_source_changed' WHERE task_id=? AND status='running'",(task,))

    def edit(self,p,idea,title,brief,revision):
        row=self.access(p,idea,True)
        if int(revision)!=row['revision'] or set(brief)-BRIEF: raise ValueError('Latest revision required')
        current=json.loads(row['brief'])
        for key,value in brief.items(): current[key]=(str(number(value)) if value not in ('',None) else None) if key in NUMERIC else (text(value,1500) if value else '')
        def op():
            self.db.execute('INSERT INTO bd_revisions(idea_id,revision,snapshot,created_at) VALUES(?,?,?,?)',(idea,row['revision'],encode(dict(row)),now().isoformat()))
            self.db.execute("UPDATE bd_ideas SET title=?,brief=?,revision=revision+1,status='draft' WHERE id=?",(text(title,160),encode(current),idea))
            self.invalidate(p,idea); self.event(p,idea,'idea_revised',idea)
        self.store._transaction(op)

    def decide_idea(self,p,idea,decision):
        self.access(p,idea,owner=True)
        if decision not in ('approved','on_hold','rejected'): raise ValueError('Known idea decision required')
        def op():
            self.db.execute('UPDATE bd_ideas SET status=? WHERE id=?',(decision,idea))
            if decision!='approved': self.invalidate(p,idea)
            self.event(p,idea,'idea_'+decision,idea)
        self.store._transaction(op)

    def source(self,p,idea,topic,claim,reference='',day=None,confidence='unassessed',state='hypothesis',unknowns=''):
        self.access(p,idea,True)
        if topic not in TOPICS or confidence not in ('unassessed','low','medium','high') or state not in ('hypothesis','unverified','human_checked'): raise ValueError('Research classification required')
        if state=='human_checked':
            self.access(p,idea,owner=True)
            if not reference or not day: raise ValueError('Source and investigation date required')
        if day and (date.fromisoformat(day)>now().astimezone(JST).date()): raise ValueError('Future research date denied')
        identifier=uuid4().hex
        def op():
            self.db.execute('INSERT INTO bd_sources VALUES(?,?,?,?,?,?,?,?,?,?)',(identifier,idea,topic,text(claim,3000),text(reference,1000) if reference else '',day,confidence,state,text(unknowns,2000) if unknowns else '',now().isoformat()))
            self.invalidate(p,idea); self.event(p,idea,'research_recorded',identifier,{'state':state,'external_fetch':False})
            return identifier
        return self.store._transaction(op)

    def withdraw_source(self,p,idea,identifier):
        self.access(p,idea,owner=True)
        row=self.db.execute('SELECT * FROM bd_sources WHERE id=? AND idea_id=?',(identifier,idea)).fetchone()
        if not row: raise ValueError('Source outside this idea')
        def op():
            self.event(p,idea,'source_withdrawn',identifier,{'prior_state':row['state']})
            self.db.execute("UPDATE bd_sources SET state='withdrawn' WHERE id=?",(identifier,))
            self.invalidate(p,idea)
        self.store._transaction(op)

    def scenario(self,p,idea,name,parameters,basis):
        self.access(p,idea,True)
        if name not in SCENARIOS: raise ValueError('Three named scenarios only')
        calculated=calculate(parameters)
        normalized={k:str(number(v)) for k,v in parameters.items() if v not in ('',None)}
        basis=text(basis,2000) if normalized else (text(basis,2000) if basis else '')
        def op():
            old=self.db.execute('SELECT * FROM bd_scenarios WHERE idea_id=? AND name=?',(idea,name)).fetchone()
            self.event(p,idea,'scenario_before_revision',name,{'parameters':json.loads(old['parameters']),'basis':old['basis'],'revision':old['revision']})
            self.db.execute('UPDATE bd_scenarios SET parameters=?,basis=?,revision=revision+1 WHERE idea_id=? AND name=?',(encode(normalized),basis,idea,name))
            self.invalidate(p,idea); self.event(p,idea,'scenario_revised',name)
            return calculated
        return self.store._transaction(op)

    def fingerprint(self,idea):
        row=dict(self.db.execute('SELECT * FROM bd_ideas WHERE id=?',(idea,)).fetchone()); row.pop('status')
        return digest([row,[dict(r) for r in self.db.execute('SELECT * FROM bd_sources WHERE idea_id=? ORDER BY id',(idea,))],[dict(r) for r in self.db.execute('SELECT * FROM bd_scenarios WHERE idea_id=? ORDER BY name',(idea,))]])

    def proposal(self,p,idea,with_content=False):
        row=self.access(p,idea,True)
        if row['status'] in ('on_hold','rejected'): raise ValueError('Idea is on hold/rejected')
        def op():
            fingerprint=self.fingerprint(idea)
            previous=self.db.execute("SELECT id,body FROM bd_proposals WHERE idea_id=? AND input_digest=? AND status IN ('approved','approval_pending') ORDER BY rowid DESC",(idea,fingerprint)).fetchall()
            for old in previous:
                if json.loads(old['body']).get('include_content',False)==bool(with_content): return old['id']
            sources=[dict(r) for r in self.db.execute("SELECT * FROM bd_sources WHERE idea_id=? AND state!='withdrawn' ORDER BY created_at",(idea,))]
            checked={r['topic'] for r in sources if r['state']=='human_checked'}
            scenarios={r['name']:{'basis':r['basis'],'calculation':calculate(json.loads(r['parameters']))} for r in self.db.execute('SELECT * FROM bd_scenarios WHERE idea_id=?',(idea,))}
            brief=json.loads(row['brief']); missing=[label for key,label in TOPICS.items() if key not in checked]
            body={'title':row['title'],'include_content':bool(with_content),'idea_id':idea,'business_id':row['business_id'],'idea_revision':row['revision'],'brief':brief,'market':sources,'research_mode':'manual_sources_and_hypotheses_no_external_search','unconfirmed_topics':missing,'assessment':'要調査' if missing else '代表確認の資料あり・独立検証未実施','scenarios':scenarios,
                'value_proposition':'入力された方向性・顧客課題をもとに、提供価値と差別化を検証する。競合や市場規模の事実は未確認なら断定しない。','strengths':'既存資産の入力内容を確認。資産から需要や成約を保証しない。','marketing':'希望する顧客が任意に情報を確認できる原稿と導線を準備し、同意・品質確認・代表承認を別途行う。','sales_flow':['任意の情報閲覧','本人からの問い合わせ','条件確認','代表の別途承認'],'execution_steps':['根拠と仮説の整理','小規模な検証設計','代表による計画承認','内部準備タスク','外部操作は別承認・現段階では未実装'],
                'risks':['需要・競合・価格の未確認','稼働時間/予算超過','医療効果・収入保証・法令適合の断定は禁止','ベネシード関連は公式情報・契約条件・費用・法的分類を人が確認。ネットワークビジネスではないと根拠なく断定しない'],
                'validation':'問い合わせ・予約・契約・売上・費用・作業時間を予測と別に記録。改善は仮説として提案。','withdrawal':'撤退基準の数値は代表が計画時に指定。未設定なら未設定のまま残す。','actual_revenue':None,'mode':'local_template_proposal_not_llm','legal_compliance':'未保証・人の確認が必要','research_freshness':'資料の調査日と現在の有効性は代表が再確認。外部検索・現在の有効性の独立検証は未実施。'}
            identifier=uuid4().hex; encoded=encode(body)
            self.db.execute('INSERT INTO bd_proposals VALUES(?,?,?,?,?,?,?)',(identifier,idea,fingerprint,encoded,digest(encoded),'approval_pending',now().isoformat()))
            roles=CORE+(('AI08','AI09') if with_content else ())
            for agent in roles:
                output={'role_work':ROLES[agent],'status':'要調査・代表確認待ち','facts':'外部調査・実モデル呼出なし','source_ids':[r['id'] for r in sources]}
                if agent=='AI27': output['scenarios']=scenarios
                elif agent=='AI04': output['evidence']=[r for r in sources if r['topic']=='competition']
                elif agent=='AI05': output['target']=brief['target'] or '未設定'
                elif agent=='AI09': output['draft']='ご希望の内容を確認したうえで、提供範囲・料金・条件をご案内するための下書きを作成します。具体的条件は代表確認待ち。'
                elif agent=='AI01': output['proposal_id']=identifier
                self.db.execute('INSERT INTO bd_draft_work VALUES(?,?,?,?,?,?,?,?,?)',(uuid4().hex,identifier,agent,'既存の専門分野に適合する下書き工程',encode({'idea_revision':row['revision'],'source_ids':[r['id'] for r in sources]}),encode(output),self.store.agents[agent]['reviewer'],'human_review_pending',now().isoformat()))
            self.event(p,idea,'proposal_prepared',identifier,{'roles':list(roles),'internal_execution_tasks_created':False})
            return identifier
        return self.store._transaction(op)

    def current_proposal(self,p,identifier,owner=False):
        row=self.db.execute('SELECT * FROM bd_proposals WHERE id=?',(identifier,)).fetchone()
        if not row: raise ValueError('Unknown proposal')
        self.access(p,row['idea_id'],owner=owner)
        if digest(row['body'])!=row['digest'] or self.fingerprint(row['idea_id'])!=row['input_digest']: raise ValueError('Stale or modified proposal')
        return row

    def decide_proposal(self,p,identifier,decision,checked):
        row=self.current_proposal(p,identifier,True)
        if row['status']!='approval_pending' or decision not in ('approve','reject'): raise ValueError('Pending proposal required')
        if decision=='approve' and (not checked or risky(row['body'])): raise ValueError('Human review of evidence, assumptions and unresolved risks required')
        self.db.execute('UPDATE bd_proposals SET status=? WHERE id=?',('approved' if decision=='approve' else 'rejected',identifier))
        self.event(p,row['idea_id'],'proposal_'+decision,identifier,{'digest':row['digest']})

    def plan(self,p,proposal,horizon,settings):
        row=self.current_proposal(p,proposal,True)
        if row['status']!='approved' or horizon not in (7,30,90): raise ValueError('Approved proposal and 7/30/90 days required')
        allowed={'budget_yen','api_budget_microusd','kpi','withdrawal','start_day'}
        if set(settings)-allowed: raise ValueError('Plan fields cannot change permissions or safety')
        budget=str(number(settings['budget_yen'])) if settings.get('budget_yen') not in ('',None) else None
        api=int(settings.get('api_budget_microusd',0))
        if api<0 or api>1000000000000: raise ValueError('Bounded API budget required')
        start=date.fromisoformat(settings['start_day'])
        config={'budget_yen':budget,'api_budget_microusd':api,'kpi':text(settings['kpi'],2000) if settings.get('kpi') else None,'withdrawal':text(settings['withdrawal'],2000) if settings.get('withdrawal') else None,'start_day':start.isoformat(),'end_day':(start+timedelta(days=horizon)).isoformat(),'approval_scope':'internal_preparation_only'}
        def op():
            fresh=self.current_proposal(p,proposal,True)
            if fresh['status']!='approved': raise ValueError('Proposal no longer approved')
            old=self.db.execute('SELECT * FROM bd_plans WHERE proposal_id=? AND horizon=?',(proposal,horizon)).fetchone()
            if old:
                if old['settings']!=encode(config): raise ValueError('Create a new approved proposal for different plan settings')
                return old['id']
            idea=self.access(p,row['idea_id']); identifier=uuid4().hex
            self.db.execute('INSERT INTO bd_plans VALUES(?,?,?,?,?,?,?)',(identifier,row['idea_id'],proposal,horizon,encode(config),'ready',now().isoformat()))
            for stage,agent in [('research','AI03'),('competition','AI04'),('finance','AI27'),('marketing','AI07'),('sales','AI14'),('quality','AI28'),('summary','AI01')]:
                task=self.store.create_task('bd:'+identifier+':'+stage,agent,{'scope':'bd_internal','reference':identifier,'stage':stage,'mode':'local_preparation_only'},action='management_decision')
                self.db.execute('INSERT INTO bd_tasks VALUES(?,?,?)',(task,identifier,stage))
                if idea['business_id']:
                    self.db.execute('INSERT INTO portfolio_tasks VALUES(?,?,?,?,?,?)',(idea['business_id'],task,'planning','新規事業開発の承認済み計画。専用画面で確認。',3,now().isoformat()))
                    self.db.execute('INSERT INTO portfolio_assignments(business_id,task_id,agent,reason,actor,created_at) VALUES(?,?,?,?,?,?)',(idea['business_id'],task,agent,'承認済みの事業開発計画の内部準備',p.identity,now().isoformat()))
            self.event(p,row['idea_id'],'plan_created',identifier,{'horizon':horizon,'settings_digest':digest(encode(config)),'external_authority':False})
            return identifier
        return self.store._transaction(op)

    def run(self,p,plan):
        row=self.db.execute('SELECT * FROM bd_plans WHERE id=?',(plan,)).fetchone()
        if not row: raise ValueError('Unknown plan')
        self.access(p,row['idea_id'],True)
        proposal=self.current_proposal(p,row['proposal_id'])
        if row['status']!='ready' or proposal['status']!='approved': raise ValueError('Current approved internal plan required')
        def op():
            completed=0
            for task in self.db.execute('SELECT * FROM bd_tasks WHERE plan_id=? ORDER BY rowid',(plan,)).fetchall():
                if self.db.execute('SELECT 1 FROM bd_results WHERE task_id=?',(task['task_id'],)).fetchone(): continue
                run=self.store.claim(task['task_id']); output={'stage':task['stage'],'proposal_id':proposal['id'],'input_digest':proposal['input_digest'],'result':'承認済みの提案と検証条件に基づく内部準備チェック。調査の実施や販売成果は未確認。','mode':'local_internal_checklist','external_execution':False}
                self.db.execute('INSERT INTO bd_results VALUES(?,?,?)',(task['task_id'],encode(output),now().isoformat()))
                self.store.finish(run,'事業開発の内部準備を記録。詳細は権限付き事業開発画面で確認。外部調査・実モデル・外部実行は未実施。')
                completed+=1
            self.event(p,row['idea_id'],'internal_plan_checked',plan,{'completed_local':completed})
            return completed
        return self.store._transaction(op)

    def pause(self,p,plan):
        row=self.db.execute('SELECT idea_id FROM bd_plans WHERE id=?',(plan,)).fetchone()
        if not row: raise ValueError('Unknown plan')
        self.access(p,row[0],owner=True)
        self.db.execute("UPDATE bd_plans SET status='paused' WHERE id=?",(plan,)); self.event(p,row[0],'plan_paused',plan)

    def resume(self,p,plan):
        row=self.db.execute('SELECT * FROM bd_plans WHERE id=?',(plan,)).fetchone()
        if not row: raise ValueError('Unknown plan')
        proposal=self.current_proposal(p,row['proposal_id'],True)
        if proposal['status']!='approved' or self.portfolio.halted(): raise ValueError('Current approval and emergency stop release required')
        self.db.execute("UPDATE bd_plans SET status='ready' WHERE id=?",(plan,))
        self.db.execute("UPDATE tasks SET status='pending' WHERE id IN(SELECT task_id FROM bd_tasks WHERE plan_id=?) AND status='paused' AND attempts<max_attempts AND NOT EXISTS(SELECT 1 FROM bd_results r WHERE r.task_id=tasks.id)",(plan,))
        self.event(p,row['idea_id'],'plan_resumed',plan)

    def actual(self,p,idea,day,metric,value,mode,evidence,previous=None):
        self.access(p,idea,True)
        if metric not in METRICS or mode not in ('actual','estimated','mock') or date.fromisoformat(day)>now().astimezone(JST).date(): raise ValueError('Past/current explicitly classified metric required')
        numeric=number(value)
        if metric in ('inquiries','bookings','contracts') and numeric!=numeric.to_integral_value(): raise ValueError('Actual counts must be whole numbers')
        value=str(numeric)
        def op():
            old=self.db.execute('SELECT id FROM bd_actuals WHERE idea_id=? AND day=? AND metric=? AND mode=? ORDER BY rowid DESC LIMIT 1',(idea,day,metric,mode)).fetchone()
            if (old[0] if old else None)!=previous: raise ValueError('Latest actual revision required')
            identifier=uuid4().hex
            self.db.execute('INSERT INTO bd_actuals VALUES(?,?,?,?,?,?,?,?,?)',(identifier,idea,day,metric,value,mode,text(evidence,1000),previous,now().isoformat()))
            self.event(p,idea,'validation_metric_recorded',identifier,{'mode':mode})
            return identifier
        return self.store._transaction(op)

    def actual_summary(self,p,idea):
        self.access(p,idea)
        rows=self.db.execute("SELECT a.* FROM bd_actuals a WHERE a.idea_id=? AND a.mode='actual' AND NOT EXISTS(SELECT 1 FROM bd_actuals n WHERE n.idea_id=a.idea_id AND n.day=a.day AND n.metric=a.metric AND n.mode=a.mode AND n.rowid>a.rowid)",(idea,)).fetchall()
        data={}
        for metric in METRICS:
            chosen=[r for r in rows if r['metric']==metric]
            data[metric]={'recorded':str(sum((Decimal(r['value']) for r in chosen),Decimal(0))) if chosen else None,'days':[r['day'] for r in chosen],'coverage':'部分計測。期間全体の実績と断定しない。'}
        return data

    def validation_review(self,p,idea):
        self.access(p,idea,True)
        data=self.actual_summary(p,idea)
        proposals=[]
        for metric in ('inquiries','contracts','revenue_yen','expense_yen','hours'):
            if data[metric]['recorded'] is None:
                proposals.append({'issue':METRICS[metric]+'は未計測','before':'根拠がないため達成/失敗を評価できない','after':'代表が計測対象・期間・根拠を決めて記録する','test':'同じ定義・期間の記録の完全性を確認','status':'approval_pending'})
        if not proposals: proposals.append({'issue':'記録はあるが期間の完全性・施策別の因果は未確認','before':'記録済み値のみ','after':'同じ期間・定義で検証し、小規模な改善案を代表が判断','test':'予測の月次仮定と任意日数の実績を直接比較しない','status':'approval_pending'})
        body={'idea_id':idea,'actuals':data,'improvements':proposals,'forecast_comparison':'月次仮定と検証期間の定義が揃うまでは差分/達成率を算定しない','automatic_apply':False}
        identifier=uuid4().hex; encoded=encode(body)
        self.db.execute('INSERT INTO bd_validation_reviews VALUES(?,?,?,?,?,?)',(identifier,idea,encoded,digest(encoded),'approval_pending',now().isoformat()))
        self.event(p,idea,'validation_review_prepared',identifier)
        return identifier

    def decide_validation_review(self,p,identifier,decision):
        row=self.db.execute('SELECT * FROM bd_validation_reviews WHERE id=?',(identifier,)).fetchone()
        if not row: raise ValueError('Unknown review')
        self.access(p,row['idea_id'],owner=True)
        if row['status']!='approval_pending' or decision not in ('approve','reject') or digest(row['body'])!=row['digest']: raise ValueError('Pending unchanged review required')
        self.db.execute('UPDATE bd_validation_reviews SET status=? WHERE id=?',('approved' if decision=='approve' else 'rejected',identifier))
        self.event(p,row['idea_id'],'validation_review_'+decision,identifier)

    def compare(self,p,ideas,criterion='evidence'):
        self.access(p)
        if not 2<=len(ideas)<=10 or len(set(ideas))!=len(ideas) or criterion not in ('evidence','operating_profit'): raise ValueError('2..10 unique ideas and known criterion required')
        rows=[]
        for idea in ideas:
            row=self.access(p,idea); sources=self.db.execute("SELECT COUNT(DISTINCT topic) FROM bd_sources WHERE idea_id=? AND state='human_checked'",(idea,)).fetchone()[0]
            scenario=self.db.execute("SELECT parameters FROM bd_scenarios WHERE idea_id=? AND name='standard'",(idea,)).fetchone()
            scenarios={r['name']:calculate(json.loads(r['parameters'])) for r in self.db.execute('SELECT name,parameters FROM bd_scenarios WHERE idea_id=?',(idea,))}
            calculation=scenarios['standard']; results=calculation.get('results')
            brief=json.loads(row['brief']); ready=sources==len(TOPICS) and results is not None and results['within_hours'] and all(s['results'] is not None for s in scenarios.values())
            if brief['budget_yen'] is None: ready=False
            elif results and number(brief['budget_yen'])<number(json.loads(scenario[0])['initial_cost_yen']): ready=False
            if brief['hours_month'] is None or results and Decimal(results['required_hours'])>number(brief['hours_month']): ready=False
            if results and Decimal(results['operating_profit_yen'])<=0: ready=False
            rows.append({'idea_id':idea,'title':row['title'],'input_digest':self.fingerprint(idea),'evidence_topics':sources,'standard':calculation,'scenarios':scenarios,'rankable':ready,'status':'検証候補（成果未保証）' if ready else '要調査・条件確認','priority':None})
        ranked=[r for r in rows if r['rankable']]
        ranked.sort(key=lambda r:(-r['evidence_topics'] if criterion=='evidence' else -Decimal(r['standard']['results']['operating_profit_yen']),r['idea_id']))
        for index,row in enumerate(ranked,1): row['priority']=index; row['reason']='代表が選択した比較軸='+criterion+'。同じ月次/円建ての仮定比較で、実績や成功確率ではない。'
        data={'criterion':criterion,'rows':rows,'unranked':'根拠・予算・時間条件不足は順位なし。数字を補完しない。','mode':'assumptions_comparison_not_recommendation_to_spend'}
        identifier=uuid4().hex; self.db.execute('INSERT INTO bd_comparisons VALUES(?,?,?)',(identifier,encode(data),now().isoformat()))
        self.event(p,None,'ideas_compared',identifier)
        return identifier,data


def guard_claim(store,task,reserve):
    db=store.db
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='bd_tasks'").fetchone(): return
    plan=db.execute('SELECT p.* FROM bd_tasks t JOIN bd_plans p ON p.id=t.plan_id WHERE t.task_id=?',(task,)).fetchone()
    if not plan: return
    row=db.execute('SELECT * FROM bd_proposals WHERE id=?',(plan['proposal_id'],)).fetchone()
    idea=db.execute('SELECT * FROM bd_ideas WHERE id=?',(plan['idea_id'],)).fetchone()
    service=Development(store,None)
    if plan['status']!='ready' or row['status']!='approved' or idea['status'] in ('on_hold','rejected') or digest(row['body'])!=row['digest'] or service.fingerprint(plan['idea_id'])!=row['input_digest']: raise ValueError('Current approved business proposal required')
    approval=db.execute("SELECT detail FROM bd_events WHERE ref=? AND event='proposal_approve' ORDER BY id DESC LIMIT 1",(row['id'],)).fetchone()
    if not approval or json.loads(approval[0]).get('digest')!=row['digest']: raise ValueError('Proposal approval changed')
    if db.execute('SELECT halted FROM portfolio_controls').fetchone()[0]: raise ValueError('Business emergency stop')
    config=json.loads(plan['settings'])
    record=db.execute("SELECT detail FROM bd_events WHERE ref=? AND event='plan_created' ORDER BY id DESC LIMIT 1",(plan['id'],)).fetchone()
    if not record or json.loads(record[0]).get('settings_digest')!=digest(plan['settings']): raise ValueError('Plan conditions changed')
    if config['budget_yen'] is not None:
        costs=db.execute("SELECT a.value FROM bd_actuals a WHERE a.idea_id=? AND a.metric='expense_yen' AND a.mode='actual' AND a.day>=? AND a.day<? AND NOT EXISTS(SELECT 1 FROM bd_actuals n WHERE n.idea_id=a.idea_id AND n.day=a.day AND n.metric=a.metric AND n.mode=a.mode AND n.rowid>a.rowid)",(plan['idea_id'],config['start_day'],config['end_day'])).fetchall()
        spent=sum((Decimal(r[0]) for r in costs),Decimal(0))
        if spent>0 and spent>=number(config['budget_yen']): raise ValueError('Validation expense cap reached')
    budget=config['api_budget_microusd']
    held=db.execute('SELECT COALESCE(SUM(COALESCE(r.actual,r.reserved)),0) FROM reservations r JOIN runs u ON u.id=r.run_id JOIN bd_tasks t ON t.task_id=u.task_id WHERE t.plan_id=?',(plan['id'],)).fetchone()[0]
    billed=db.execute("SELECT COALESCE(SUM(c.amount),0) FROM office_costs c JOIN bd_tasks t ON t.task_id=c.task_id WHERE t.plan_id=? AND c.mode='manual'",(plan['id'],)).fetchone()[0]
    if reserve+held+billed>budget or billed+held>=budget and billed+held>0: raise ValueError('Project API budget reached')
