"""Additive business/AI assignment foundation; no external services or new staff."""
import json,re
from datetime import date
from pathlib import Path
from uuid import uuid4
from .store import now
from .seminars import JST
from .portfolio import text,period
from .content import encode,digest
from .development_finance import number

PERMISSIONS={'business_read','work','finance_read'}


def migrate(store):
    if store.db.execute('SELECT 1 FROM office_migrations WHERE version=15').fetchone(): return
    def op():
        for statement in (Path(__file__).resolve().parent.parent/'migrations/015_business_foundation.sql').read_text().split(';'):
            if statement.strip(): store.db.execute(statement)
        store.db.execute('INSERT OR IGNORE INTO office_migrations VALUES(15,?)',(now().isoformat(),))
    store._transaction(op)


def profile(db,business):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='bf_profiles'").fetchone(): return None
    return db.execute('SELECT * FROM bf_profiles WHERE business_id=?',(business,)).fetchone()


def ai_allowed(store,agent,business,permission,strict=True):
    if permission not in PERMISSIONS or agent not in store.agents: return False
    row=profile(store.db,business)
    if not row: return not strict
    if not row['enabled']: return False
    if permission=='work' and (store.db.execute('SELECT halted FROM portfolio_controls').fetchone()[0] or store.db.execute('SELECT status FROM portfolio_businesses WHERE id=?',(business,)).fetchone()[0] in ('paused','closed')): return False
    if not strict and not row['ai_enforced']: return True
    assigned=store.db.execute('SELECT * FROM bf_ai_assignments WHERE business_id=? AND agent=?',(business,agent)).fetchone()
    return bool(assigned and assigned['active'] and permission in json.loads(assigned['permissions']))


def require_work(store,agent,business):
    if not ai_allowed(store,agent,business,'work',strict=False): raise PermissionError('Employee not assigned to this business for internal work')


def eligible(store,business,candidates):
    return tuple(a for a in candidates if ai_allowed(store,a,business,'work',strict=False))


def guard_task(store,task):
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='portfolio_tasks'").fetchone(): return
    row=store.db.execute('SELECT p.business_id,t.agent FROM portfolio_tasks p JOIN tasks t ON t.id=p.task_id WHERE p.task_id=?',(task,)).fetchone()
    if row: require_work(store,row['agent'],row['business_id'])


def guard_reviewer(store,task,reviewer):
    row=store.db.execute('SELECT business_id FROM portfolio_tasks WHERE task_id=?',(task,)).fetchone() if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='portfolio_tasks'").fetchone() else None
    if row: require_work(store,reviewer,row[0])


class BusinessFoundation:
    def __init__(self,office): self.o=office; self.store=office.store; self.db=office.db; self.pf=office.portfolio
    def event(self,p,b,event,ref='',detail=None):
        self.db.execute('INSERT INTO bf_events(business_id,actor,event,ref,detail,created_at) VALUES(?,?,?,?,?,?)',(b,p.identity,event,ref,encode(detail or {}),now().isoformat()))
    def get(self,p,b,owner=False):
        self.pf.access(p,b,owner=owner)
        row=profile(self.db,b)
        if not row: raise ValueError('Explicit foundation registration required')
        business=self.db.execute('SELECT services,revision FROM portfolio_businesses WHERE id=?',(b,)).fetchone()
        return {**dict(row),'overview':business['services'],'business_revision':business['revision']}
    def create(self,p,key,name,overview,category,started_on=None,kind='other'):
        self.pf.access(p,owner=True)
        if not key or len(key)>100: raise ValueError('Bounded idempotency key required')
        name,overview,category=text(name,120),text(overview,2000),text(category,120)
        if started_on: date.fromisoformat(started_on)
        fingerprint=digest([name,overview,category,started_on or None,kind])
        def op():
            old=self.db.execute('SELECT * FROM bf_creations WHERE request_key=?',(key,)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint: raise ValueError('Creation key conflict')
                return old['business_id']
            b='BIZ-'+uuid4().hex
            self.pf.create(p,b,name,kind,services=overview)
            self.db.execute('INSERT INTO bf_profiles VALUES(?,?,?,?,1,1,1)',(b,overview,category,started_on or None))
            self.db.execute('INSERT INTO bf_creations VALUES(?,?,?)',(key,b,fingerprint)); self.event(p,b,'business_registered',b,{'status':'preparing','ai_enforced':True}); return b
        return self.store._transaction(op)
    def adopt(self,p,b,category,started_on=None):
        self.pf.access(p,b,owner=True); category=text(category,120)
        if started_on: date.fromisoformat(started_on)
        def op():
            old=profile(self.db,b)
            if old: return b
            legacy=self.db.execute('SELECT services FROM portfolio_businesses WHERE id=?',(b,)).fetchone()
            self.db.execute('INSERT INTO bf_profiles VALUES(?,?,?,?,1,0,1)',(b,legacy['services'],category,started_on or None)); self.event(p,b,'legacy_business_adopted',b,{'ai_enforced':False}); return b
        return self.store._transaction(op)
    def update(self,p,b,overview,category,started_on,enabled,status,confirmed,revision,business_revision):
        self.pf.access(p,b,owner=True)
        if type(enabled) is not bool or type(confirmed) is not bool: raise ValueError('Explicit state flags required')
        overview=text(overview,2000) if overview else ''; category=text(category,120)
        if started_on: date.fromisoformat(started_on)
        def op():
            old=self.get(p,b,True)
            if old['revision']!=int(revision) or old['business_revision']!=int(business_revision): raise ValueError('Latest profile and business revisions required')
            business=self.db.execute('SELECT * FROM portfolio_businesses WHERE id=?',(b,)).fetchone()
            self.pf.update(p,b,{'services':overview,'status':status,'confirmed':confirmed},business['revision'])
            self.db.execute('UPDATE bf_profiles SET overview=?,category=?,started_on=?,enabled=?,revision=revision+1 WHERE business_id=?',(overview,category,started_on or None,int(enabled),b))
            self.event(p,b,'profile_updated',b,{'before':dict(old),'after':{'overview':overview,'category':category,'started_on':started_on or None,'enabled':enabled,'status':status}})
        self.store._transaction(op)
    def assign(self,p,b,agent,role_note,scope_note,permissions,active=True,revision=0):
        self.get(p,b,True)
        if agent not in self.store.agents or not isinstance(permissions,list) or not all(isinstance(item,str) for item in permissions) or len(set(permissions))!=len(permissions) or not set(permissions)<=PERMISSIONS or type(active) is not bool: raise ValueError('Existing employee and bounded internal permissions required')
        if set(permissions)&{'work','finance_read'} and 'business_read' not in permissions: raise ValueError('Scoped business read permission is a prerequisite')
        role_note,scope_note=text(role_note,500),text(scope_note,1000)
        def op():
            old=self.db.execute('SELECT * FROM bf_ai_assignments WHERE business_id=? AND agent=?',(b,agent)).fetchone()
            if int(revision)!=(old['revision'] if old else 0): raise ValueError('Latest assignment revision required')
            self.db.execute('INSERT INTO bf_ai_assignments VALUES(?,?,?,?,?,?,1) ON CONFLICT(business_id,agent) DO UPDATE SET role_note=excluded.role_note,scope_note=excluded.scope_note,permissions=excluded.permissions,active=excluded.active,revision=revision+1',(b,agent,role_note,scope_note,encode(sorted(permissions)),int(active)))
            self.event(p,b,'employee_assignment_changed',agent,{'before':dict(old) if old else None,'after':{'role_note':role_note,'scope_note':scope_note,'permissions':sorted(permissions),'active':active}})
        self.store._transaction(op)
    def enforce(self,p,b,enabled,checked):
        self.get(p,b,True)
        if type(enabled) is not bool or not checked: raise ValueError('Explicit representative policy confirmation required')
        if not enabled: raise ValueError('Enforcement cannot be automatically weakened; retain policy and edit scoped assignments')
        def op():
            agents={r[0] for r in self.db.execute("SELECT DISTINCT t.agent FROM tasks t JOIN portfolio_tasks p ON p.task_id=t.id WHERE p.business_id=? AND t.status IN ('pending','running','paused','review_pending','approval_pending')",(b,))}
            agents|={self.store.agents[a]['reviewer'] for a in tuple(agents)}
            for agent in agents:
                if not ai_allowed(self.store,agent,b,'work'): raise ValueError('Assign current business workers before enforcing')
            self.db.execute('UPDATE bf_profiles SET ai_enforced=1,revision=revision+1 WHERE business_id=?',(b,)); self.event(p,b,'ai_policy_enforced',b)
        self.store._transaction(op)
    def read_for_ai(self,p,b,agent,resource='business_read',start=None,end=None):
        self.pf.access(p,b)
        permission='finance_read' if resource=='kpi_read' else resource
        if resource not in ('business_read','finance_read','kpi_read') or not ai_allowed(self.store,agent,b,permission):
            if resource=='kpi_read': self.o.business_operations.audit(p,b,'ai_scope','denied',agent)
            raise PermissionError('Assigned business and explicit read permission required')
        row=self.db.execute('SELECT name,status FROM portfolio_businesses WHERE id=?',(b,)).fetchone(); meta=self.get(p,b)
        if resource=='business_read': result={'business_id':b,'name':row['name'],'status':row['status'],'overview':meta['overview'],'category':meta['category'],'started_on':meta['started_on']}
        elif resource=='kpi_read':
            period(start,end); result=self.o.business_operations.kpis(p,b,start,end)
        else:
            period(start,end); result=self.o.sales.dashboard(p,b,start,end)
        self.event(p,b,'scoped_ai_read',agent,{'resource':resource}); return result
    def kpi(self,p,b,key,label,unit,active=True,revision=0):
        self.get(p,b,True)
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,49}',key) or type(active) is not bool: raise ValueError('Stable KPI key and explicit state required')
        label,unit=text(label,120),text(unit,40)
        def op():
            old=self.db.execute('SELECT * FROM bf_kpis WHERE business_id=? AND metric_key=?',(b,key)).fetchone()
            if int(revision)!=(old['revision'] if old else 0): raise ValueError('Latest KPI revision required')
            if old and old['unit']!=unit and self.db.execute('SELECT 1 FROM bf_kpi_samples WHERE business_id=? AND metric_key=?',(b,key)).fetchone(): raise ValueError('Measured unit is immutable; create a new KPI key')
            self.db.execute('INSERT INTO bf_kpis VALUES(?,?,?,?,?,1) ON CONFLICT(business_id,metric_key) DO UPDATE SET label=excluded.label,unit=excluded.unit,active=excluded.active,revision=revision+1',(b,key,label,unit,int(active))); self.event(p,b,'kpi_definition_changed',key,{'before':dict(old) if old else None,'label':label,'unit':unit,'active':active})
        self.store._transaction(op)
    def measure(self,p,b,key,day,value,mode,evidence,revision=0):
        self.get(p,b); self.pf.writable(p,b); date.fromisoformat(day)
        if mode not in ('actual','estimate','mock') or mode=='actual' and day>now().astimezone(JST).date().isoformat(): raise ValueError('Separate measured, estimated and mock modes; no future actuals')
        value=str(number(value)); evidence=text(evidence,300)
        def op():
            definition=self.db.execute('SELECT * FROM bf_kpis WHERE business_id=? AND metric_key=? AND active=1',(b,key)).fetchone()
            if not definition: raise ValueError('Active scoped KPI required')
            old=self.db.execute('SELECT * FROM bf_kpi_samples WHERE business_id=? AND metric_key=? AND day=? AND mode=?',(b,key,day,mode)).fetchone()
            if int(revision)!=(old['revision'] if old else 0): raise ValueError('Explicit latest observation revision required')
            self.event(p,b,'kpi_observation_revised',key,{'before':dict(old) if old else None,'day':day,'mode':mode,'value':value,'evidence':evidence})
            self.db.execute('INSERT INTO bf_kpi_samples VALUES(?,?,?,?,?,?,1) ON CONFLICT(business_id,metric_key,day,mode) DO UPDATE SET value=excluded.value,evidence=excluded.evidence,revision=revision+1',(b,key,day,mode,value,evidence))
        self.store._transaction(op)
    def summary(self,p,b,start,end):
        meta=self.get(p,b); period(start,end)
        return {'business_id':b,'profile':dict(meta),'sales':self.o.sales.dashboard(p,b,start,end),'portfolio':self.pf.summary(p,b,start,end),'kpi_definitions':[dict(r) for r in self.db.execute('SELECT * FROM bf_kpis WHERE business_id=?',(b,))],'kpis':[dict(r) for r in self.db.execute('SELECT * FROM bf_kpi_samples WHERE business_id=? AND day>=? AND day<? ORDER BY metric_key,day,mode',(b,start,end))]}
