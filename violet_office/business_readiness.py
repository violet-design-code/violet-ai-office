"""Previewed, representative-approved business and assignment changes; no external effects."""
import json,unicodedata
from datetime import date
from pathlib import Path
from uuid import uuid4
from .store import now
from .portfolio import text,STATES,KINDS
from .business_foundation import PERMISSIONS,ai_allowed
from .content import encode,digest

KINDS_READY={'create':'事業の新規登録','update':'事業の編集・有効化・停止','assign':'担当の追加・変更','unassign':'担当の解除（履歴保持）','assign_many':'複数社員の担当を一括確認・適用'}


def migrate(store):
    if store.db.execute('SELECT 1 FROM office_migrations WHERE version=19').fetchone():return
    def op():
        for sql in (Path(__file__).resolve().parent.parent/'migrations/019_business_readiness.sql').read_text().split(';'):
            if sql.strip():store.db.execute(sql)
        store.db.execute('INSERT INTO office_migrations VALUES(19,?)',(now().isoformat(),))
    store._transaction(op)


def normalized(value):return ' '.join(unicodedata.normalize('NFKC',value).casefold().split())


class BusinessReadiness:
    def __init__(self,o):self.o=o;self.store=o.store;self.db=o.db;self.pf=o.portfolio;self.f=o.business_foundation
    def access(self,p,b=None,owner=False):
        try:self.pf.access(p,b,owner=owner)
        except (PermissionError,ValueError):self.o.business_operations.audit(p,b,'readiness_scope','denied');raise
    def row(self,p,identifier,owner=False):
        row=self.db.execute('SELECT * FROM br_requests WHERE id=?',(identifier,)).fetchone()
        if not row:raise ValueError('Readiness request required')
        self.access(p,row['business_id'],owner or row['business_id'] is None);return row
    def event(self,p,row,event):self.db.execute('INSERT INTO br_events(request_id,business_id,actor,event,created_at) VALUES(?,?,?,?,?)',(row['id'],row['business_id'],p.identity,event,now().isoformat()))
    def validate(self,p,kind,b,payload,check_duplicates=True):
        self.access(p,b,True)
        if kind not in KINDS_READY or not isinstance(payload,dict):raise ValueError('Known operation and explicit fields required')
        if kind=='create':
            if b is not None or set(payload)-{'name','overview','category','started_on','kind'} or not {'name','overview','category'}<=set(payload):raise ValueError('New business fields required, no arbitrary IDs or metrics')
            result={k:text(payload[k],120 if k!='overview' else 2000) for k in ('name','overview','category')};result['started_on']=payload.get('started_on') or None;result['kind']=payload.get('kind','other')
            if result['kind'] not in KINDS:raise ValueError('Known generic business category required')
            if result['started_on']:date.fromisoformat(result['started_on'])
        elif kind=='update':
            if not b or set(payload)!={'name','overview','category','started_on','status','enabled','confirmed'}:raise ValueError('Complete business settings required')
            if payload['status'] not in STATES or type(payload['enabled']) is not bool or type(payload['confirmed']) is not bool or payload['status']=='active' and (not payload['enabled'] or not payload['confirmed']):raise ValueError('Explicit operating status and representative confirmation required')
            result={**payload,'name':text(payload['name'],120),'overview':text(payload['overview'],2000) if payload['overview'] else '', 'category':text(payload['category'],120),'started_on':payload['started_on'] or None}
            if result['started_on']:date.fromisoformat(result['started_on'])
        elif kind=='assign_many':
            if set(payload)!={'assignments'} or not isinstance(payload['assignments'],list) or not 1<=len(payload['assignments'])<=28:raise ValueError('One to 28 explicit assignment changes required')
            nodes=[self.validate(p,'assign',b,node) for node in payload['assignments']]
            if len({v['agent'] for v in nodes})!=len(nodes):raise ValueError('Duplicate employees in batch')
            result={'assignments':sorted(nodes,key=lambda v:v['agent'])}
        else:
            if not b or payload.get('agent') not in self.store.agents:raise ValueError('Existing registered employee and business required')
            if not self.db.execute('SELECT 1 FROM bf_profiles WHERE business_id=?',(b,)).fetchone():raise ValueError('Review existing business category and metadata before assigning employees')
            if kind=='unassign':
                if set(payload)!={'agent'} or not self.db.execute('SELECT 1 FROM bf_ai_assignments WHERE business_id=? AND agent=?',(b,payload['agent'])).fetchone():raise ValueError('Current assignment required for release')
                result=dict(payload)
            else:
                if set(payload)!={'agent','role_note','scope_note','permissions','active'} or type(payload['active']) is not bool or not isinstance(payload['permissions'],list) or any(not isinstance(v,str) for v in payload['permissions']) or len(set(payload['permissions']))!=len(payload['permissions']) or not set(payload['permissions'])<=PERMISSIONS:raise ValueError('Known limited permissions and explicit assignment fields required')
                if set(payload['permissions'])&{'work','finance_read'} and 'business_read' not in payload['permissions']:raise ValueError('Business read is a prerequisite')
                result={**payload,'permissions':sorted(payload['permissions']),'role_note':text(payload['role_note'],500),'scope_note':text(payload['scope_note'],1000)}
        if check_duplicates and kind in ('create','update'):
            old=self.db.execute('SELECT name FROM portfolio_businesses WHERE id=?',(b,)).fetchone() if b else None
            if not old or result['name']!=old['name']:
                if any(normalized(r['name'])==normalized(result['name']) for r in self.db.execute('SELECT id,name FROM portfolio_businesses') if r['id']!=b):raise ValueError('Business name already exists; never merge automatically')
        return result
    def snapshot(self,b):
        if b is None:return digest([(r['id'],normalized(r['name'])) for r in self.db.execute('SELECT id,name FROM portfolio_businesses ORDER BY id')])
        business=self.db.execute('SELECT * FROM portfolio_businesses WHERE id=?',(b,)).fetchone();profile=self.db.execute('SELECT * FROM bf_profiles WHERE business_id=?',(b,)).fetchone();assignments=[dict(r) for r in self.db.execute('SELECT * FROM bf_ai_assignments WHERE business_id=? ORDER BY agent',(b,))];controls=[r['agent'] for r in self.db.execute('SELECT agent FROM br_assignment_controls WHERE business_id=? ORDER BY agent',(b,))]
        return digest([dict(business) if business else None,dict(profile) if profile else None,assignments,controls])
    def preview(self,p,kind,b,payload):
        values=self.validate(p,kind,b,payload);before=None;after=values
        if kind=='create':impact=['準備中・AI制限有効で登録。料金/目標/顧客/実績/APIキー/担当は未設定。']
        elif kind=='update':
            row=self.db.execute('SELECT * FROM portfolio_businesses WHERE id=?',(b,)).fetchone();profile=self.db.execute('SELECT * FROM bf_profiles WHERE business_id=?',(b,)).fetchone();before={'name':row['name'],'overview':row['services'],'status':row['status'],'category':profile['category'] if profile else None,'enabled':bool(profile['enabled']) if profile else True,'started_on':profile['started_on'] if profile else None,'confirmed':bool(row['offering_confirmed'])}
            impact=['ID・顧客・収支・同意・履歴を保持。停止時は作業を権限でブロック。再開で自動実行しない。']
            if not profile:impact.append('明示承認後、既存事業の基盤メタデータだけ採用。既存割当は作らない。')
        elif kind=='assign_many':
            before=[]
            for node in values['assignments']:
                row=self.db.execute('SELECT * FROM bf_ai_assignments WHERE business_id=? AND agent=?',(b,node['agent'])).fetchone()
                before.append({'agent':node['agent'],'current':{**dict(row),'permissions':json.loads(row['permissions'])} if row else None})
            impact=['選択した既存社員だけ一括で承認・適用。全件成功または全件取消。未指定の担当は変更しない。','旧互換運用でも対象の事業/社員だけ現行権限を厳密検証。進行中業務は管理者確認。']
        else:
            row=self.db.execute('SELECT * FROM bf_ai_assignments WHERE business_id=? AND agent=?',(b,values['agent'])).fetchone();before=dict(row) if row else None
            if before:before['permissions']=json.loads(before['permissions'])
            if kind=='unassign':after={'agent':values['agent'],'active':False,'permissions':before['permissions']}
            impact=['既存社員定義は変更しない。当該事業・当該社員だけ現行権限を厳密検証。解除は論理停止。','既存の進行中/待機中タスク・引き継ぎは人間が確認し、無断で担当変更しない。']
        return {'kind':kind,'business_id':b,'before':before,'after':after,'impact':impact,'external_execution':False}
    def prepare(self,p,key,kind,b,payload):
        key=text(key,80)
        def op():
            values=self.validate(p,kind,b,payload,check_duplicates=False);encoded=encode(values);old=self.db.execute('SELECT * FROM br_requests WHERE entry_key=?',(key,)).fetchone()
            if old:
                self.access(p,old['business_id'],True)
                if old['kind']!=kind or old['business_id']!=b or old['payload']!=encoded:raise ValueError('Request key conflict')
                return old['id']
            view=self.preview(p,kind,b,values);identifier=uuid4().hex
            self.db.execute("INSERT INTO br_requests VALUES(?,?,?,?,?,?,?,'approval_pending',?,NULL,NULL,NULL,NULL,?)",(identifier,b,key,kind,encoded,self.snapshot(b),encode(view),p.identity,now().isoformat()));self.event(p,self.row(p,identifier),'prepared');return identifier
        return self.store._transaction(op)
    def decide(self,p,identifier,decision,checked):
        def op():
            row=self.row(p,identifier,True)
            if row['status']!='approval_pending' or decision not in ('approve','reject') or not checked:raise ValueError('Pending request and explicit representative confirmation required')
            if decision=='approve':
                if row['snapshot']!=self.snapshot(row['business_id']):raise ValueError('Settings changed; prepare a fresh preview')
                self.validate(p,row['kind'],row['business_id'],json.loads(row['payload']))
            self.db.execute('UPDATE br_requests SET status=?,approved_by=?,approved_at=? WHERE id=?',('approved' if decision=='approve' else 'rejected',p.identity,now().isoformat(),identifier));self.event(p,row,decision)
        self.store._transaction(op)
    def apply(self,p,identifier):
        def op():
            row=self.row(p,identifier,True)
            if row['status']=='applied':return row['result_business']
            if row['status']!='approved' or row['snapshot']!=self.snapshot(row['business_id']):raise ValueError('Current explicitly approved preview required')
            b=row['business_id'];v=self.validate(p,row['kind'],b,json.loads(row['payload']))
            if row['kind']=='create':b=self.f.create(p,'br:'+identifier,v['name'],v['overview'],v['category'],v['started_on'],v['kind'])
            elif row['kind']=='update':
                if not self.db.execute('SELECT 1 FROM bf_profiles WHERE business_id=?',(b,)).fetchone():self.f.adopt(p,b,v['category'],v['started_on'])
                old=self.f.get(p,b,True);before=self.db.execute('SELECT name,services FROM portfolio_businesses WHERE id=?',(b,)).fetchone();self.f.update(p,b,v['overview'],v['category'],v['started_on'],v['enabled'],v['status'],v['confirmed'],old['revision'],old['business_revision'])
                current=self.db.execute('SELECT name,revision FROM portfolio_businesses WHERE id=?',(b,)).fetchone()
                if current['name']!=v['name']:self.pf.update(p,b,{'name':v['name'],'confirmed':v['confirmed']},current['revision'])
                if before['name']!=v['name'] or before['services']!=v['overview'] or old['category']!=v['category']:self.db.execute("UPDATE cj_profiles SET status='pending',revision=revision+1,approved_by=NULL,approved_at=NULL WHERE business_id=?",(b,))
            else:
                for node in (v['assignments'] if row['kind']=='assign_many' else [v]):
                    old=self.db.execute('SELECT * FROM bf_ai_assignments WHERE business_id=? AND agent=?',(b,node['agent'])).fetchone()
                    if row['kind']=='unassign':self.f.assign(p,b,node['agent'],old['role_note'],old['scope_note'],json.loads(old['permissions']),False,old['revision'])
                    else:self.f.assign(p,b,node['agent'],node['role_note'],node['scope_note'],node['permissions'],node['active'],old['revision'] if old else 0)
                    self.db.execute('INSERT OR IGNORE INTO br_assignment_controls VALUES(?,?)',(b,node['agent']))
            self.db.execute("UPDATE br_requests SET status='applied',applied_by=?,result_business=? WHERE id=?",(p.identity,b,identifier));self.event(p,self.row(p,identifier),'applied');return b
        return self.store._transaction(op)
    def contract(self,p,b=None):
        self.access(p,b,b is None)
        assignments={r['agent']:dict(r) for r in self.db.execute('SELECT * FROM bf_ai_assignments WHERE business_id=?',(b,))} if b else {}
        return {'schema_version':1,'business_id':b,'business_states':STATES,'business_kinds':KINDS,'permissions':sorted(PERMISSIONS),'operations':KINDS_READY,'employees':[{'id':a,'role':v['role'],'reviewer':v['reviewer'],'assignment':{**assignments[a],'permissions':json.loads(assignments[a]['permissions'])} if a in assignments else None,'work_allowed':ai_allowed(self.store,a,b,'work',strict=True) if b else False} for a,v in self.store.agents.items()],'create_defaults':{'status':'preparing','ai_enforced':True,'customers':0,'assignments':0,'pricing':'未設定','goals':'未設定'},'operation_fields':{'create':{'required':['name','overview','category'],'optional':['started_on','kind']},'update':{'required':['name','overview','category','started_on','status','enabled','confirmed']},'assign':{'required':['agent','role_note','scope_note','permissions','active']},'unassign':{'required':['agent']},'assign_many':{'required':['assignments'],'min_items':1,'max_items':28}},'confirmation_required':True,'external_execution':False,'final_phase20_ui':False}
    def listing(self,p,b=None):
        self.access(p,b,b is None)
        rows=self.db.execute('SELECT * FROM br_requests WHERE business_id=? ORDER BY created_at,id',(b,)).fetchall() if b else self.db.execute('SELECT * FROM br_requests ORDER BY created_at,id').fetchall()
        return [{**dict(r),'stale':r['status'] in ('approval_pending','approved') and r['snapshot']!=self.snapshot(r['business_id'])} for r in rows]

    def history(self,p,b=None):
        self.access(p,b,b is None)
        return [dict(r) for r in self.db.execute('SELECT * FROM br_events WHERE (? IS NULL OR business_id=?) ORDER BY created_at,id',(b,b))]

    def catalog(self,p,b=None):
        self.access(p,b,b is None)
        return [dict(r) for r in self.db.execute('SELECT p.id AS business_id,p.name,p.kind,p.status,p.services AS overview,p.revision AS business_revision,f.category,f.started_on,f.enabled,f.revision AS profile_revision FROM portfolio_businesses p LEFT JOIN bf_profiles f ON f.business_id=p.id WHERE (? IS NULL OR p.id=?) ORDER BY p.id',(b,b))]
