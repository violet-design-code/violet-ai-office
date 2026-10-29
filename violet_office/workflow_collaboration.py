"""Internal business work coordination; no network, external executor or permission grant."""
import json
import sqlite3
from datetime import date,timedelta
from pathlib import Path
from uuid import uuid4
from .store import now
from .content import encode,digest
from .portfolio import text
from .business_foundation import ai_allowed

ROOT=Path(__file__).resolve().parent.parent
CHANGES={'external_send':'外部送信案','booking_confirm':'面談・予約確定案','terms_publish':'契約・報酬条件の公開案','cross_business_share':'事業間共有案','pricing':'料金変更案','business_settings':'重要設定変更案','permissions':'AI権限変更案','capability':'内部業務の対応範囲・稼働可否'}


def routing(store):
    data=json.loads((ROOT/'config/workflow_routing.json').read_text())
    if data['schema_version']!=1 or not 1<=data['max_attempts']<=3 or not 1<=data['max_handoffs']<=5: raise ValueError('Bounded workflow settings required')
    for row in data['kinds'].values():
        if row['purpose'] not in ('contact','schedule','followup') or not row['candidates'] or len(set(row['candidates']))!=len(row['candidates']) or any(a not in store.agents for a in row['candidates']): raise ValueError('Existing configured roles required')
    return data


def migrate(store):
    if store.db.execute('SELECT 1 FROM office_migrations WHERE version=18').fetchone(): return
    def op():
        statement=''
        for line in (ROOT/'migrations/018_workflow_collaboration.sql').read_text().splitlines(True):
            statement+=line
            if sqlite3.complete_statement(statement):store.db.execute(statement);statement=''
        if statement.strip():raise ValueError('Incomplete migration statement')
        store.db.execute('INSERT INTO office_migrations VALUES(18,?)',(now().isoformat(),))
    store._transaction(op)


def available(store,b,agent,kind):
    cfg=routing(store)
    if kind not in cfg['kinds'] or agent not in cfg['kinds'][kind]['candidates'] or not ai_allowed(store,agent,b,'work',strict=True): return False
    row=store.db.execute('SELECT available FROM aw_capabilities WHERE business_id=? AND agent=? AND kind=?',(b,agent,kind)).fetchone()
    return not row or bool(row[0])


def guard_task(store,task,reviewer=None):
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='aw_tasks'").fetchone(): return
    row=store.db.execute('SELECT j.*,t.agent AS actual_agent,t.id AS actual_task,w.paused,w.artifact_id FROM aw_tasks a JOIN aw_jobs j ON j.business_id=a.business_id AND j.id=a.job_id JOIN tasks t ON t.id=a.task_id JOIN wf_tasks w ON w.task_id=t.id WHERE a.task_id=?',(task,)).fetchone()
    if not row: return
    b=row['business_id']
    if row['task_id']!=task or row['agent']!=row['actual_agent'] or row['state'] not in ('ready','retry_wait','done') or row['paused'] or not available(store,b,row['agent'],row['kind']): raise PermissionError('Current unpaused scoped work assignment required')
    if reviewer and not ai_allowed(store,reviewer,b,'work',strict=True): raise PermissionError('Current independent reviewer required')
    if not row['artifact_id'] and (row['attempts']>=routing(store)['max_attempts'] or row['next_at'] and row['next_at']>now().isoformat()): raise ValueError('Bounded delayed retry required')
    purpose=routing(store)['kinds'][row['kind']]['purpose']
    from .sales import Sales
    from .portfolio import Portfolio
    sales=Sales(store,Portfolio(store));deal=store.db.execute('SELECT * FROM scm_deals WHERE business_id=? AND id=?',(b,row['deal_id'])).fetchone()
    if not deal or deal['state']!='active' or not sales.contact_allowed(b,deal['contact_id'],purpose): raise ValueError('Active case, current purpose consent and no stop required')


class WorkflowCollaboration:
    def __init__(self,o): self.o=o;self.store=o.store;self.db=o.db;self.cfg=routing(self.store)
    def access(self,p,b,write=False,owner=False):
        try:self.o.portfolio.access(p,b,write,owner)
        except (PermissionError,ValueError):self.o.business_operations.audit(p,b,'collaboration_scope','denied');raise
    def event(self,p,b,event,ref='',detail=None):
        self.db.execute('INSERT INTO aw_events(business_id,ref,event,actor,detail,created_at) VALUES(?,?,?,?,?,?)',(b,ref,event,p.identity,encode(detail or {}),now().isoformat()))
    def row(self,p,b,identifier):
        self.access(p,b);row=self.db.execute('SELECT * FROM aw_jobs WHERE business_id=? AND id=?',(b,identifier)).fetchone()
        if not row:raise ValueError('Business-scoped job required')
        return row
    def consent(self,p,b,deal,kind):
        row=self.o.sales.row(p,b,'scm_deals',deal)
        return row['state']=='active' and self.o.sales.contact_allowed(b,row['contact_id'],self.cfg['kinds'][kind]['purpose'])
    def submit(self,p,b,key,deal,kind,due,priority=3):
        self.access(p,b,True);self.o.portfolio.writable(p,b);self.o.sales.row(p,b,'scm_deals',deal)
        key=text(key,80);date.fromisoformat(due)
        if kind not in self.cfg['kinds'] or not 1<=int(priority)<=5:raise ValueError('Configured work kind and priority required')
        def op():
            old=self.db.execute('SELECT * FROM aw_jobs WHERE business_id=? AND entry_key=?',(b,key)).fetchone()
            if old:
                saved=self.db.execute("SELECT detail FROM aw_events WHERE business_id=? AND ref=? AND event='submitted' ORDER BY id LIMIT 1",(b,old['id'])).fetchone()
                if not saved or json.loads(saved[0]).get('fingerprint')!=digest([deal,kind,due,int(priority)]):raise ValueError('Job key conflict')
                return old['id']
            identifier=uuid4().hex;self.o.workflow.attach(p,b,deal)
            self.db.execute("INSERT INTO aw_jobs VALUES(?,?,?,?,?,?,?,NULL,NULL,'waiting_admin',0,NULL,NULL,0,?)",(b,identifier,deal,key,kind,due,int(priority),now().isoformat()))
            self.event(p,b,'submitted',identifier,{'kind':kind,'fingerprint':digest([deal,kind,due,int(priority)])});self.dispatch(p,b,identifier);return identifier
        return self.store._transaction(op)
    def dispatch(self,p,b,identifier):
        self.access(p,b,True);self.o.portfolio.writable(p,b)
        def op():
            row=self.row(p,b,identifier)
            if row['task_id']:
                guard_task(self.store,row['task_id']);return row['task_id']
            if row['state']!='waiting_admin':raise ValueError('Waiting work required')
            choices=[a for a in self.cfg['kinds'][row['kind']]['candidates'] if available(self.store,b,a,row['kind'])] if self.consent(p,b,row['deal_id'],row['kind']) else []
            if not choices:
                self.event(p,b,'administrator_check_required',identifier,{'reason':'consent_or_scoped_capability_unavailable'});return None
            def load(a):return (self.db.execute("SELECT COUNT(*) FROM aw_jobs WHERE business_id=? AND agent=? AND state IN ('ready','retry_wait')",(b,a)).fetchone()[0],a)
            agent=min(choices,key=load);task=self.new_task(p,b,row,agent,row['kind'],0)
            self.db.execute("UPDATE aw_jobs SET agent=?,task_id=?,state='ready' WHERE business_id=? AND id=?",(agent,task,b,identifier));self.event(p,b,'routed',identifier,{'agent':agent,'reason':self.store.agents[agent]['role']+' / '+self.cfg['kinds'][row['kind']]['label']});return task
        return self.store._transaction(op)
    def new_task(self,p,b,row,agent,kind,generation,source=None):
        basis='本人希望と目的別同意、当該事業だけを参照。外部送信しない。'
        if source:basis+=' 引き継ぎ元内部タスク参照：'+source+'。原文を別事業へコピーしない。'
        task=self.o.workflow.assign(p,b,row['deal_id'],'aw:'+row['id']+':'+str(generation),agent,self.cfg['kinds'][kind]['label'],basis,row['due_day'],row['priority'])
        self.db.execute('INSERT INTO aw_tasks VALUES(?,?,?)',(task,b,row['id']));return task
    def handoff(self,p,b,identifier,key,target,kind,reason):
        self.access(p,b,True);self.o.portfolio.writable(p,b);key=text(key,80);reason=text(reason,300)
        def op():
            old=self.db.execute('SELECT * FROM aw_handoffs WHERE business_id=? AND entry_key=?',(b,key)).fetchone()
            if old:
                if [old[k] for k in ('job_id','to_agent','to_kind','reason')]!=[identifier,target,kind,reason]:raise ValueError('Handoff key conflict')
                return old['id']
            row=self.row(p,b,identifier)
            from .legacy_accounting import deny_generic_content
            deny_generic_content(self.store,row['task_id'])
            if not row['task_id'] or row['state'] not in ('ready','done'):raise ValueError('Current available work required')
            guard_task(self.store,row['task_id'])
            if kind not in self.cfg['kinds'] or not available(self.store,b,target,kind) or not self.consent(p,b,row['deal_id'],kind):raise PermissionError('Target employee lacks scoped capability or consent')
            if (target,kind)==(row['agent'],row['kind']) or row['generation']>=self.cfg['max_handoffs'] or self.db.execute('SELECT 1 FROM aw_handoffs WHERE business_id=? AND job_id=? AND from_agent=? AND from_kind=?',(b,identifier,target,kind)).fetchone():raise ValueError('No circular or unbounded handoff')
            current=self.db.execute('SELECT t.status,w.artifact_id FROM tasks t JOIN wf_tasks w ON w.task_id=t.id WHERE t.id=?',(row['task_id'],)).fetchone()
            if current['status']=='running' or current['artifact_id'] and current['status'] not in ('completed','manual_execution_pending'):raise ValueError('Finish representative review before handing off an artifact')
            task=self.new_task(p,b,row,target,kind,row['generation']+1,row['task_id'])
            if current['artifact_id']:self.db.execute('INSERT INTO dependencies(task_id,prerequisite) VALUES(?,?)',(task,row['task_id']))
            self.db.execute('UPDATE wf_tasks SET paused=1 WHERE task_id=?',(row['task_id'],))
            self.db.execute("UPDATE aw_jobs SET agent=?,kind=?,task_id=?,state='ready',generation=generation+1,attempts=0,next_at=NULL,error_code=NULL WHERE business_id=? AND id=?",(target,kind,task,b,identifier))
            hid=uuid4().hex;self.db.execute('INSERT INTO aw_handoffs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(b,hid,identifier,key,row['agent'],target,row['kind'],kind,row['task_id'],task,reason,'transferred_internal',now().isoformat()));self.event(p,b,'handoff',hid,{'from_agent':row['agent'],'to_agent':target});return hid
        return self.store._transaction(op)
    def run(self,p,b,identifier):
        self.access(p,b,True);self.o.portfolio.writable(p,b)
        def op():
            row=self.row(p,b,identifier)
            if not row['task_id'] or row['state'] not in ('ready','retry_wait'):raise ValueError('Runnable work required')
            guard_task(self.store,row['task_id'])
            artifact=self.db.execute('SELECT artifact_id FROM wf_tasks WHERE task_id=?',(row['task_id'],)).fetchone()[0]
            if artifact:return artifact
            if row['attempts']>=self.cfg['max_attempts'] or row['next_at'] and row['next_at']>now().isoformat():raise ValueError('Retry limit or delay prevents execution')
            self.db.execute('SAVEPOINT aw_attempt')
            try:artifact=self.o.workflow.run(p,b,row['task_id'])
            except Exception:  # Roll back this local attempt; record a code, never exception text.
                self.db.execute('ROLLBACK TO aw_attempt');self.db.execute('RELEASE aw_attempt');attempts=row['attempts']+1;state='failed' if attempts>=self.cfg['max_attempts'] else 'retry_wait';self.db.execute('UPDATE aw_jobs SET attempts=?,state=?,error_code=?,next_at=? WHERE business_id=? AND id=?',(attempts,state,'local_work_failed',(now()+timedelta(seconds=30)).isoformat() if state=='retry_wait' else None,b,identifier));self.event(p,b,'safe_failure',identifier,{'code':'local_work_failed','attempt':attempts});return None
            self.db.execute('RELEASE aw_attempt');self.db.execute("UPDATE aw_jobs SET attempts=attempts+1,error_code=NULL,next_at=NULL,state='ready' WHERE business_id=? AND id=?",(b,identifier));self.event(p,b,'local_result',identifier);return artifact
        return self.store._transaction(op)
    def control(self,p,b,identifier,action):
        self.access(p,b,True,True)
        def op():
            row=self.row(p,b,identifier)
            if action=='pause':state='paused'
            elif action=='resume' and row['state']=='paused':state='ready' if row['task_id'] else 'waiting_admin'
            elif action=='complete':
                if not row['task_id']:raise ValueError('Approved internal result required')
                guard_task(self.store,row['task_id']);task=self.db.execute('SELECT status FROM tasks WHERE id=?',(row['task_id'],)).fetchone()
                if task[0] not in ('manual_execution_pending','completed'):raise ValueError('Representative-approved internal result required')
                state='done'
            else:raise ValueError('Known safe control required')
            self.db.execute('UPDATE aw_jobs SET state=? WHERE business_id=? AND id=?',(state,b,identifier))
            if row['task_id']:self.db.execute('UPDATE wf_tasks SET paused=? WHERE task_id=?',(int(state=='paused'),row['task_id']))
            self.event(p,b,action,identifier)
        self.store._transaction(op)
    def snapshot(self,b,payload):
        business=dict(self.db.execute('SELECT * FROM portfolio_businesses WHERE id=?',(b,)).fetchone());assignments=[dict(r) for r in self.db.execute('SELECT * FROM bf_ai_assignments WHERE business_id=? ORDER BY agent',(b,))];caps=[dict(r) for r in self.db.execute('SELECT * FROM aw_capabilities WHERE business_id=? ORDER BY agent,kind',(b,))]
        cj=self.db.execute('SELECT * FROM cj_profiles WHERE business_id=?',(b,)).fetchone()
        return digest([business,assignments,caps,dict(cj) if cj else None,payload])
    def propose(self,p,b,key,kind,payload):
        self.access(p,b,True);key=text(key,80)
        if kind not in CHANGES or not isinstance(payload,dict):raise ValueError('Known approval category required')
        if kind=='capability':
            if set(payload)!={'agent','kind','scope','available'} or type(payload['available']) is not bool or payload['kind'] not in self.cfg['kinds'] or payload['agent'] not in self.cfg['kinds'][payload['kind']]['candidates']:raise ValueError('Configured existing role and internal capability required')
            text(payload['scope'],500)
        else:
            if set(payload)-{'reference','reason','target_business'} or not {'reference','reason'}<=set(payload):raise ValueError('Minimal internal references required, no customer content')
            text(payload['reference'],100);text(payload['reason'],500)
            if kind=='cross_business_share':
                target=payload.get('target_business')
                if not target or target==b:raise ValueError('Different explicit target business required')
                self.access(p,target,owner=True)
        encoded=encode(payload)
        def op():
            old=self.db.execute('SELECT * FROM aw_changes WHERE business_id=? AND entry_key=?',(b,key)).fetchone()
            if old:
                if old['kind']!=kind or old['payload']!=encoded:raise ValueError('Approval key conflict')
                return old['id']
            cid=uuid4().hex;self.db.execute("INSERT INTO aw_changes VALUES(?,?,?,?,?,?,'approval_pending',NULL,NULL,?)",(b,cid,key,kind,encoded,self.snapshot(b,payload),now().isoformat()));self.event(p,b,'change_proposed',cid,{'kind':kind});return cid
        return self.store._transaction(op)
    def decide(self,p,b,identifier,decision,checked):
        self.access(p,b,True,True)
        def op():
            row=self.db.execute('SELECT * FROM aw_changes WHERE business_id=? AND id=?',(b,identifier)).fetchone()
            if not row or row['status']!='approval_pending' or not checked or decision not in ('approve','reject'):raise ValueError('Pending request and explicit representative review required')
            payload=json.loads(row['payload'])
            if decision=='approve' and self.snapshot(b,payload)!=row['snapshot']:raise ValueError('Changed settings require a fresh proposal')
            if decision=='approve' and row['kind']=='terms_publish' and not self.o.customer_journey.usable(p,b):raise ValueError('Phase17 official conditions remain unverified')
            if decision=='approve' and row['kind']=='cross_business_share':
                target=payload.get('target_business');self.access(p,target,owner=True)
                pair=self.db.execute('SELECT a.contact_id AS source_contact,z.contact_id AS target_contact FROM scm_identity_links a JOIN scm_identity_links z ON z.person_id=a.person_id WHERE a.business_id=? AND a.contact_id=? AND z.business_id=? AND a.active=1 AND z.active=1',(b,payload['reference'],target)).fetchone()
                if not pair or not self.o.portfolio.has_consent(b,pair['source_contact'],'identity_link') or not self.o.portfolio.has_consent(target,pair['target_contact'],'identity_link'):raise ValueError('Existing representative verified link and both purpose consents required')
            self.db.execute('UPDATE aw_changes SET status=?,approved_by=?,approved_at=? WHERE business_id=? AND id=?',('approved_manual_only' if decision=='approve' else 'rejected',p.identity,now().isoformat(),b,identifier));self.event(p,b,'change_'+decision,identifier)
        self.store._transaction(op)
    def apply_capability(self,p,b,identifier):
        self.access(p,b,True,True)
        def op():
            row=self.db.execute('SELECT * FROM aw_changes WHERE business_id=? AND id=?',(b,identifier)).fetchone()
            if not row or row['kind']!='capability' or row['status']!='approved_manual_only':raise ValueError('Only an explicitly approved internal capability can be applied')
            payload=json.loads(row['payload']);agent,kind=payload['agent'],payload['kind']
            if self.snapshot(b,payload)!=row['snapshot'] or not ai_allowed(self.store,agent,b,'work',strict=True):raise PermissionError('Current assignment and approved snapshot required')
            self.db.execute('INSERT INTO aw_capabilities VALUES(?,?,?,?,?,1) ON CONFLICT(business_id,agent,kind) DO UPDATE SET scope_note=excluded.scope_note,available=excluded.available,revision=revision+1',(b,agent,kind,payload['scope'],int(payload['available'])))
            self.db.execute("UPDATE aw_changes SET status='applied_local' WHERE business_id=? AND id=?",(b,identifier));self.event(p,b,'internal_capability_applied',identifier)
        self.store._transaction(op)
    def listing(self,p,b):
        self.access(p,b);result=[]
        for row in self.db.execute('SELECT * FROM aw_jobs WHERE business_id=? ORDER BY created_at DESC',(b,)):
            item=dict(row);task=self.db.execute('SELECT status FROM tasks WHERE id=?',(row['task_id'],)).fetchone();item['task_status']=task[0] if task else None
            item['stored_state']=item['state']
            if row['state'] in ('ready','retry_wait') and (not available(self.store,b,row['agent'],row['kind']) or not self.consent(p,b,row['deal_id'],row['kind'])):item['state']='waiting_admin'
            result.append(item)
        return result
    def employees(self,p,b):
        self.access(p,b);result=[]
        for agent,definition in self.store.agents.items():
            assignment=self.db.execute('SELECT * FROM bf_ai_assignments WHERE business_id=? AND agent=?',(b,agent)).fetchone()
            result.append({'agent':agent,'role':definition['role'],'assigned':bool(assignment and assignment['active']),'scope':assignment['scope_note'] if assignment else None,'kinds':[k for k in self.cfg['kinds'] if available(self.store,b,agent,k)],'work_count':self.db.execute('SELECT COUNT(*) FROM aw_jobs WHERE business_id=? AND agent=?',(b,agent)).fetchone()[0]})
        return result
    def handoffs(self,p,b,job=None):
        self.access(p,b)
        if job:self.row(p,b,job)
        return [dict(r) for r in self.db.execute('SELECT * FROM aw_handoffs WHERE business_id=? AND (? IS NULL OR job_id=?) ORDER BY created_at,id',(b,job,job))]
    def changes(self,p,b):
        self.access(p,b);return [dict(r) for r in self.db.execute('SELECT * FROM aw_changes WHERE business_id=? ORDER BY created_at,id',(b,))]
