"""Scoped, explicit-period business read models. No external execution or sales creation."""
import json
from decimal import Decimal
from datetime import datetime,timedelta,timezone
from pathlib import Path
from .store import now
from .seminars import JST
from .portfolio import period,text,METRICS
from .development_finance import number
from .business_foundation import ai_allowed
from .content import digest

SOURCES={'unconfirmed':'基準未確認・合算しない','legacy':'旧台帳の記帳額（税区分未照合）','scm':'代表確認の税抜請求売上（SCM）'}


def migrate(store):
    if store.db.execute('SELECT 1 FROM office_migrations WHERE version=16').fetchone(): return
    def op():
        for statement in (Path(__file__).resolve().parent.parent/'migrations/016_business_operations.sql').read_text().split(';'):
            if statement.strip(): store.db.execute(statement)
        store.db.execute('INSERT OR IGNORE INTO office_migrations VALUES(16,?)',(now().isoformat(),))
    store._transaction(op)


def bounds(start,end):
    a,b=period(start,end)
    return a,b,datetime.combine(a,datetime.min.time(),JST).astimezone(timezone.utc).isoformat(),datetime.combine(b,datetime.min.time(),JST).astimezone(timezone.utc).isoformat()


def achievement(value,target,direction):
    if value is None or target is None: return {'achieved':None,'attainment_percent':None}
    v,t=Decimal(str(value)),Decimal(str(target)); achieved=v>=t if direction=='at_least' else v<=t
    percent=v/t*100 if direction=='at_least' and t else t/v*100 if direction=='at_most' and t and v else None
    return {'achieved':achieved,'attainment_percent':float(round(percent,2)) if percent is not None else None}


class BusinessOperations:
    def __init__(self,office): self.o=office; self.store=office.store; self.db=office.db; self.pf=office.portfolio
    def audit(self,p,b,event,outcome,agent=None,task=None):
        registered=self.db.execute('SELECT username FROM office_users WHERE username=?',(p.identity,)).fetchone()
        actor=registered[0] if registered else 'unknown'
        self.db.execute('INSERT INTO ops_audit(actor,business_ref,event,outcome,agent,task_ref,created_at) VALUES(?,?,?,?,?,?,?)',(actor,b if outcome=='allowed' else digest(str(b)),event,outcome,agent if agent in self.store.agents else None,digest(str(task)) if task else None,now().isoformat()))
    def access(self,p,b=None,owner=False):
        try: self.pf.access(p,b,owner=owner)
        except (PermissionError,ValueError):
            self.audit(p,b,'human_scope','denied'); raise
    def finance_policy(self,p,b,source,evidence,revision=0):
        self.access(p,b,True)
        if source not in SOURCES: raise ValueError('Known explicit finance source required')
        evidence=text(evidence,300)
        def op():
            old=self.db.execute('SELECT * FROM ops_finance_policy WHERE business_id=?',(b,)).fetchone()
            if int(revision)!=(old['revision'] if old else 0): raise ValueError('Latest policy revision required')
            self.db.execute('INSERT INTO ops_finance_policy VALUES(?,?,?,1) ON CONFLICT(business_id) DO UPDATE SET source=excluded.source,evidence=excluded.evidence,revision=revision+1',(b,source,evidence))
            self.o.business_foundation.event(p,b,'finance_read_basis_changed',detail={'before':dict(old) if old else None,'source':source,'evidence':evidence}); self.audit(p,b,'finance_basis','allowed')
        self.store._transaction(op)
    def rule(self,p,b,key,aggregation,evidence,revision=0):
        self.access(p,b,True); evidence=text(evidence,300)
        if aggregation not in ('sum','last'): raise ValueError('Explicit sum or last observation required')
        def op():
            if not self.db.execute('SELECT 1 FROM bf_kpis WHERE business_id=? AND metric_key=? AND active=1',(b,key)).fetchone(): raise ValueError('Active business KPI required')
            old=self.db.execute('SELECT * FROM ops_kpi_rules WHERE business_id=? AND metric_key=?',(b,key)).fetchone()
            if int(revision)!=(old['revision'] if old else 0): raise ValueError('Latest rule revision required')
            self.db.execute('INSERT INTO ops_kpi_rules VALUES(?,?,?,?,1) ON CONFLICT(business_id,metric_key) DO UPDATE SET aggregation=excluded.aggregation,evidence=excluded.evidence,revision=revision+1',(b,key,aggregation,evidence))
            self.o.business_foundation.event(p,b,'kpi_aggregation_changed',key,{'before':dict(old) if old else None,'aggregation':aggregation}); self.audit(p,b,'kpi_rule','allowed')
        self.store._transaction(op)
    def target(self,p,b,key,start,end,target,direction,evidence,revision=0):
        self.access(p,b,True); period(start,end); target=str(number(target)); evidence=text(evidence,300)
        if direction not in ('at_least','at_most'): raise ValueError('Known goal direction required')
        def op():
            definition=self.db.execute('SELECT revision FROM bf_kpis WHERE business_id=? AND metric_key=? AND active=1',(b,key)).fetchone()
            rule=self.db.execute('SELECT revision FROM ops_kpi_rules WHERE business_id=? AND metric_key=?',(b,key)).fetchone()
            if not definition or not rule: raise ValueError('Scoped active KPI and aggregation required')
            old=self.db.execute('SELECT * FROM ops_kpi_targets WHERE business_id=? AND metric_key=? AND start_day=? AND end_day=?',(b,key,start,end)).fetchone()
            if int(revision)!=(old['revision'] if old else 0): raise ValueError('Latest target revision required')
            self.db.execute('INSERT INTO ops_kpi_targets VALUES(?,?,?,?,?,?,?,?,?,1) ON CONFLICT(business_id,metric_key,start_day,end_day) DO UPDATE SET target=excluded.target,direction=excluded.direction,definition_revision=excluded.definition_revision,rule_revision=excluded.rule_revision,evidence=excluded.evidence,revision=revision+1',(b,key,start,end,target,direction,definition[0],rule[0],evidence))
            self.o.business_foundation.event(p,b,'period_kpi_target_changed',key,{'before':dict(old) if old else None,'start':start,'end':end,'target':target,'direction':direction}); self.audit(p,b,'kpi_target','allowed')
        self.store._transaction(op)
    def kpis(self,p,b,start,end):
        self.access(p,b); a,z=period(start,end); data=self.pf.summary(p,b,start,end); output=[]
        for key,label in METRICS.items():
            metric=data['metrics'][key]; target=data['goals'].get(key)
            direction='unconfirmed' if key=='owner_minutes' else 'at_least'
            output.append({'source':'standard','key':key,'label':label,'unit':'分' if key=='owner_minutes' else '件','recorded':metric['recorded'],'value':metric['total'],'complete':metric['complete'],'measured_days':metric['days'],'target':target,'direction':direction,'target_state':'needs_review' if target is not None and direction=='unconfirmed' else 'set' if target is not None else 'unset',**achievement(metric['total'],target if direction!='unconfirmed' else None,direction)})
        for definition in self.db.execute('SELECT * FROM bf_kpis WHERE business_id=? AND active=1 ORDER BY metric_key',(b,)):
            key=definition['metric_key']; rule=self.db.execute('SELECT * FROM ops_kpi_rules WHERE business_id=? AND metric_key=?',(b,key)).fetchone()
            rows=self.db.execute("SELECT day,value FROM bf_kpi_samples WHERE business_id=? AND metric_key=? AND mode='actual' AND day>=? AND day<? ORDER BY day",(b,key,start,end)).fetchall()
            complete=False; value=recorded=None
            if rule and rule['aggregation']=='sum':
                recorded=sum((Decimal(r['value']) for r in rows),Decimal(0)) if rows else None; complete=len(rows)==(z-a).days; value=recorded if complete else None
            elif rule and rule['aggregation']=='last':
                recorded=Decimal(rows[-1]['value']) if rows else None; complete=bool(rows and rows[-1]['day']==(z-timedelta(days=1)).isoformat()); value=recorded if complete else None
            target=self.db.execute('SELECT * FROM ops_kpi_targets WHERE business_id=? AND metric_key=? AND start_day=? AND end_day=?',(b,key,start,end)).fetchone()
            fresh=bool(target and rule and target['definition_revision']==definition['revision'] and target['rule_revision']==rule['revision'])
            output.append({'source':'custom','key':key,'label':definition['label'],'unit':definition['unit'],'aggregation':rule['aggregation'] if rule else None,'recorded':str(recorded) if recorded is not None else None,'value':str(value) if value is not None else None,'complete':complete,'measured_days':len(rows),'target':target['target'] if target else None,'direction':target['direction'] if target else 'at_least','target_state':'set' if fresh else 'needs_review' if target else 'unset',**achievement(value,target['target'] if fresh else None,target['direction'] if target else 'at_least')})
        return output
    def counts(self,b,lower,upper):
        customers=self.db.execute("SELECT COUNT(*) FROM portfolio_contacts c WHERE c.business_id=? AND c.created_at<? AND NOT EXISTS(SELECT 1 FROM portfolio_events e WHERE e.business_id=c.business_id AND e.ref=c.id AND e.event='contact_redacted' AND e.created_at<?)",(b,upper,upper)).fetchone()[0]
        new=self.db.execute('SELECT COUNT(*) FROM portfolio_contacts WHERE business_id=? AND created_at>=? AND created_at<?',(b,lower,upper)).fetchone()[0]
        facts=[]
        for e in self.db.execute("SELECT e.*,a.booking_id FROM scm_events e JOIN scm_actions a ON a.business_id=e.business_id AND a.id=e.ref WHERE e.business_id=? AND e.event='pipeline_fact_recorded' AND e.created_at<? ORDER BY e.created_at,e.id",(b,upper)):
            detail=json.loads(e['detail'])
            if detail.get('mode')=='manual': facts.append((e,detail.get('kind')))
        current={}; contracted=set(); cancelled=set(); booked={}
        for e,kind in facts:
            if kind in ('contract','cancel_contract'): current[e['deal_id']]=kind
            if lower<=e['created_at']<upper:
                if kind=='contract': contracted.add(e['deal_id'])
                if kind=='cancel_contract': cancelled.add(e['deal_id'])
                if kind=='booking_confirm' and e['booking_id']:
                    booking=self.db.execute("SELECT id FROM scm_bookings WHERE business_id=? AND id=? AND kind='interview'",(b,e['booking_id'])).fetchone()
                    if booking: booked[booking['id']]=e['deal_id']
        completed=self.db.execute("SELECT COUNT(*) FROM s14_engagement e JOIN s14_meta m ON m.seminar_id=e.seminar_id WHERE m.business_id=? AND m.mode='manual' AND e.mode='manual' AND e.interview='completed' AND e.interview_at>=? AND e.interview_at<?",(b,lower,upper)).fetchone()[0]
        return {'customers':customers,'new_customers':new,'contracts_recorded':len(contracted),'contracts_cancelled':len(cancelled),'contracts_active':sum(current.get(d)=='contract' for d in contracted),'interview_bookings':len(booked),'interviews_completed':completed}
    def outstanding(self,b,upper):
        total=0
        for i in self.db.execute("SELECT * FROM scm_invoices WHERE business_id=? AND mode='manual' AND created_at<?",(b,upper)):
            credited=self.db.execute("SELECT 1 FROM scm_credits WHERE business_id=? AND invoice_id=? AND mode='manual' AND created_at<?",(b,i['id'],upper)).fetchone()
            cash=self.db.execute("SELECT COALESCE(SUM(CASE WHEN kind='payment' THEN amount_yen ELSE -amount_yen END),0) FROM scm_cash WHERE business_id=? AND invoice_id=? AND mode='manual' AND created_at<?",(b,i['id'],upper)).fetchone()[0]
            total+=(0 if credited else i['gross_yen'])-cash
        return total
    def summary(self,p,b,start,end):
        self.access(p,b); _,_,lower,upper=bounds(start,end)
        business=self.db.execute('SELECT name,status FROM portfolio_businesses WHERE id=?',(b,)).fetchone()
        policy=self.db.execute('SELECT * FROM ops_finance_policy WHERE business_id=?',(b,)).fetchone(); source=policy['source'] if policy else 'unconfirmed'
        legacy=self.pf.summary(p,b,start,end); scm=self.o.sales.dashboard(p,b,start,end)
        revenue=scm['confirmed_net_yen'] if source=='scm' else legacy['revenue_yen'] if source=='legacy' else None
        expense=legacy['expense_yen']; profit=revenue-expense if revenue is not None and expense is not None else None
        result={'business_id':b,'name':business['name'],'status':business['status'],'period':[start,end],'timezone':'Asia/Tokyo','finance_source':source,'finance_definition':SOURCES[source],'revenue_yen':revenue,'expense_yen':expense,'profit_yen':profit,'cash_yen':scm['paid_net_cash_yen'],'outstanding_yen':self.outstanding(b,upper),'legacy_revenue_yen':legacy['revenue_yen'],'scm_revenue_yen':scm['confirmed_net_yen'],'counts':self.counts(b,lower,upper),'legacy_contract_records':legacy['manual_contract_records'],'kpis':self.kpis(p,b,start,end),'measurement':'登録・代表の確認申告の集計。外部サービス/銀行未照合。未計測を0で補完しない。','limitations':'税/未入力経費/借入/USDのAPI費は概算利益に未反映。旧台帳とSCM売上は合算しない。面談完了は第14段階の明示確認のみ。'}
        self.audit(p,b,'business_summary','allowed'); return result
    def assignments(self,p,b):
        self.access(p,b)
        return [{'agent':r['agent'],'role':self.store.agents[r['agent']]['role'],'role_note':r['role_note'],'scope_note':r['scope_note'],'permissions':json.loads(r['permissions']),'active':bool(r['active'])} for r in self.db.execute('SELECT * FROM bf_ai_assignments WHERE business_id=? ORDER BY agent',(b,))]
    def global_summary(self,p,start,end):
        self.access(p,owner=True); period(start,end)
        businesses=[self.summary(p,r[0],start,end) for r in self.db.execute('SELECT id FROM portfolio_businesses ORDER BY priority,id')]
        def sums(rows,key): return sum(r[key] for r in rows) if rows and all(r[key] is not None for r in rows) else None
        groups={source:{'businesses':len(rows),'revenue_yen':sums(rows,'revenue_yen'),'expense_yen':sums(rows,'expense_yen'),'profit_yen':sums(rows,'profit_yen')} for source in ('legacy','scm') for rows in [[r for r in businesses if r['finance_source']==source]]}
        known={r['finance_source'] for r in businesses}; comparable=bool(businesses and len(known)==1 and 'unconfirmed' not in known)
        result={'period':[start,end],'businesses':businesses,'revenue_yen':sums(businesses,'revenue_yen') if comparable else None,'expense_yen':sums(businesses,'expense_yen'),'profit_yen':sums(businesses,'profit_yen') if comparable else None,'same_basis':comparable,'by_basis':groups,'counts':{key:sum(r['counts'][key] for r in businesses) for key in ('customers','new_customers','contracts_recorded','contracts_cancelled','contracts_active','interview_bookings','interviews_completed')},'note':'同期間・同じ基準の記録が全事業にある場合だけ全売上/利益を合計。混在は基準別に別掲。顧客数は事業別登録IDの延べ件数で実人数ではない。'}
        self.audit(p,None,'global_summary','allowed'); return result
    def for_task(self,p,task,start,end):
        row=self.db.execute('SELECT t.agent,t.status,p.business_id FROM tasks t JOIN portfolio_tasks p ON p.task_id=t.id WHERE t.id=?',(task,)).fetchone()
        if not row:
            self.audit(p,None,'task_scope','denied',task=task); raise PermissionError('Authenticated mapped task context required')
        b=row['business_id']; self.access(p,b)
        paused=False
        for table in ('wf_tasks','s14_work'):
            state=self.db.execute('SELECT paused FROM '+table+' WHERE task_id=?',(task,)).fetchone()
            paused=paused or bool(state and state[0])
        if row['status'] in ('paused','rejected','failed') or paused or not ai_allowed(self.store,row['agent'],b,'finance_read') or not ai_allowed(self.store,row['agent'],b,'work'):
            self.audit(p,b,'ai_scope','denied',row['agent'],task); raise PermissionError('Current employee assignment and finance permission required')
        from .legacy_accounting import LegacyAccounting
        try:
            result=LegacyAccounting(self.o).read(p,task,start,end,require_work=True)
        except PermissionError:
            self.audit(p,b,'ai_scope','denied',row['agent'],task)
            raise
        self.audit(p,b,'task_kpi_read','allowed',row['agent'],task)
        return result
