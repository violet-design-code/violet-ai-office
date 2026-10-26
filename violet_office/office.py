"""Authenticated office operations, local read models and accounting. No sender."""
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from datetime import timedelta,datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4
from zoneinfo import ZoneInfo

from .store import Store, now
from .growth import Growth,GOALS,usd
from .learning import Learning,STYLES,disable_style
from .registry import load_agents
from .coordinator import Coordinator
from .content import ContentOffice, CHECKS, safe_input, encode, digest
from .integrations.adapters import require, Principal, MockAdapter
from .integrations.service import Integrations

JST=ZoneInfo('Asia/Tokyo')
DEPARTMENTS={'AI02':'経営戦略','AI07':'マーケティング','AI14':'営業・紹介促進','AI20':'顧客サポート','AI25':'システム・分析'}
STATUS={'planned':'割当済み','clarification_required':'要確認','pending':'未着手','running':'作業中（ローカル）','review_pending':'別担当レビュー待ち','approval_pending':'代表承認待ち','manual_execution_pending':'承認済み・手動実行待ち','completed':'完了','failed':'失敗','paused':'一時停止','superseded':'再依頼に引継ぎ','changes_requested':'修正依頼','rejected':'却下','draft':'下書き','quality_checked':'品質確認済み','approved':'承認済み・未送信','published':'公開済み（手動記録）','mock_succeeded':'モック成功・実送信なし','retry_wait':'再試行待ち（モック）','manual_reconciliation':'手動照合待ち（モック）','cancelled':'取消済み'}
SCHEMA='''
CREATE TABLE IF NOT EXISTS office_users (
 username TEXT PRIMARY KEY, role TEXT NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS office_sessions (
 token_hash TEXT PRIMARY KEY, username TEXT, role TEXT NOT NULL, csrf TEXT NOT NULL,
 expires_at TEXT NOT NULL, seen_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS office_login_limits (bucket TEXT PRIMARY KEY, count INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS office_audit (
 id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, ref TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS office_commands (
 nonce TEXT PRIMARY KEY, actor TEXT NOT NULL, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS office_task_links (old_id TEXT NOT NULL, new_id TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS office_budgets (month TEXT PRIMARY KEY, limit_microusd INTEGER NOT NULL CHECK(limit_microusd>=0));
CREATE TABLE IF NOT EXISTS office_month_reservations (run_id TEXT PRIMARY KEY REFERENCES runs(id), month TEXT NOT NULL, amount INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS office_costs (
 id TEXT PRIMARY KEY, entry_key TEXT UNIQUE NOT NULL, model TEXT NOT NULL, agent TEXT NOT NULL,
 task_id TEXT REFERENCES tasks(id), amount INTEGER NOT NULL CHECK(amount>=0),
 kind TEXT NOT NULL, mode TEXT NOT NULL, incurred_date TEXT NOT NULL, evidence TEXT NOT NULL);
'''


def department(agent):
    n=int(agent[2:])
    if n==1:
        return '統括本部'
    if n==28:
        return '品質・法令監査'
    lead='AI02' if n<7 else 'AI07' if n<14 else 'AI14' if n<20 else 'AI20' if n<25 else 'AI25'
    return DEPARTMENTS[lead]


def redact(value):
    text=str(value)
    def mask(item):
        if isinstance(item,dict):
            hidden={'apikey','accesstoken','token','secret','password','channelsecret','authorization','email','phone','lineuserid','fullname'}
            return {k:('[非表示]' if re.sub(r'[_-]','',str(k)).casefold() in hidden else mask(v)) for k,v in item.items()}
        if isinstance(item,list):
            return [mask(v) for v in item]
        return item
    try:
        structured=json.loads(text)
        if isinstance(structured,(dict,list)):
            text=json.dumps(mask(structured),ensure_ascii=False,indent=2)
    except (ValueError,RecursionError):
        pass
    text=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}','[メール非表示]',text)
    text=re.sub(r'(?i)(?:bearer\s+|sk-)[A-Za-z0-9_.-]+','[秘密値非表示]',text)
    text=re.sub(r'(?i)((?:api[_-]?key|token|secret|password|電話|氏名)\s*[=:：]\s*)[^\s,}\n]+',r'\1[非表示]',text)
    text=re.sub(r'U[0-9a-f]{32}','[LINE ID非表示]',text)
    text=re.sub(r'(?<![A-Za-z0-9])0\d{1,3}[- ]?\d{2,4}[- ]?\d{4}(?![A-Za-z0-9])','[電話非表示]',text)
    return text


def password_hash(password,salt):
    return hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()


class Office:
    def __init__(self,store):
        self.store=store
        self.db=store.db
        self.content=ContentOffice(store)
        self.coordinator=Coordinator(store)
        # Schema only. Do not initialize a webhook, fake connection or secret.
        from .integrations.service import SCHEMA as IX_SCHEMA
        self.db.executescript(IX_SCHEMA)
        self.db.executescript(SCHEMA)
        self.db.execute('INSERT OR IGNORE INTO office_budgets VALUES (?,0)',(now().astimezone(JST).strftime('%Y-%m'),))
        self.growth=Growth(store)
        self.learning=Learning(store)
        from .phase9_migrations import migrate
        from .seminars import Seminars
        from .selection import Selection
        migrate(store)
        self.seminars=Seminars(store)
        self.selection=Selection(store,self.learning)
        from .portfolio_schema import migrate as migrate_portfolio
        from .portfolio import Portfolio
        migrate_portfolio(store)
        self.portfolio=Portfolio(store)
        from .development import migrate as migrate_development,Development
        migrate_development(store)
        self.development=Development(store,self.portfolio)
        from .sales import migrate as migrate_sales,Sales
        migrate_sales(store)
        self.sales=Sales(store,self.portfolio)
        from .workflow import migrate as migrate_workflow,Workflow
        migrate_workflow(store)
        self.workflow=Workflow(self)
        from .seminar_support import migrate as migrate_seminar_support,SeminarSupport
        migrate_seminar_support(store)
        self.seminar_support=SeminarSupport(self)
        from .business_foundation import migrate as migrate_business_foundation,BusinessFoundation
        migrate_business_foundation(store)
        self.business_foundation=BusinessFoundation(self)
        from .business_operations import migrate as migrate_operations,BusinessOperations
        migrate_operations(store)
        self.business_operations=BusinessOperations(self)

    def audit(self,actor,action,ref=''):
        self.db.execute('INSERT INTO office_audit(actor,action,ref,created_at) VALUES (?,?,?,?)',(actor,action,ref,now().isoformat()))

    def add_user(self,username,role,password):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',username) or role not in ('representative','viewer','participant','business_operator') or len(password)<12 or len(password)>256:
            raise ValueError('Invalid account')
        salt=secrets.token_hex(16)
        self.db.execute('INSERT INTO office_users VALUES (?,?,?,?)',(username,role,salt,password_hash(password,salt)))

    def session(self,token):
        if not token or len(token)>256:
            return None
        key=hashlib.sha256(token.encode()).hexdigest()
        row=self.db.execute('SELECT * FROM office_sessions WHERE token_hash=?',(key,)).fetchone()
        if not row or row['expires_at']<=now().isoformat() or row['seen_at']<(now()-timedelta(minutes=30)).isoformat():
            return None
        if row['username']:
            user=self.db.execute('SELECT role FROM office_users WHERE username=?',(row['username'],)).fetchone()
            if not user or user['role']!=row['role']:
                return None
        self.db.execute('UPDATE office_sessions SET seen_at=? WHERE token_hash=?',(now().isoformat(),key))
        return dict(row)

    def new_session(self,username=None,role='anonymous'):
        token=secrets.token_urlsafe(32)
        csrf=secrets.token_urlsafe(32)
        self.db.execute('INSERT INTO office_sessions VALUES (?,?,?,?,?,?)',
            (hashlib.sha256(token.encode()).hexdigest(),username,role,csrf,(now()+timedelta(hours=8 if username else 1)).isoformat(),now().isoformat()))
        self.db.execute('DELETE FROM office_sessions WHERE expires_at<?',(now().isoformat(),))
        return token,csrf

    def logout(self,token):
        self.db.execute('DELETE FROM office_sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))

    def login(self,username,password):
        if len(username)>80 or len(password)>256:
            raise PermissionError('認証できません')
        sampled_at=now()
        bucket=sampled_at.strftime('%Y%m%d%H')+str(sampled_at.minute//10)
        def check_limit():
            self.db.execute('INSERT INTO office_login_limits VALUES (?,1) ON CONFLICT(bucket) DO UPDATE SET count=count+1',(bucket,))
            return self.db.execute('SELECT count FROM office_login_limits WHERE bucket=?',(bucket,)).fetchone()[0]
        if self.store._transaction(check_limit)>10:
            self.audit('anonymous','login_rate_limited')
            raise PermissionError('時間をおいて再試行してください')
        row=self.db.execute('SELECT * FROM office_users WHERE username=?',(username,)).fetchone()
        candidate=password_hash(password,row['salt'] if row else '0'*32)
        if not row or not hmac.compare_digest(candidate,row['password_hash']):
            self.audit('anonymous','login_failed')
            raise PermissionError('認証できません')
        self.audit(row['username'],'login_succeeded')
        return self.new_session(row['username'],row['role'])

    def commands(self,session,nonce,action,data):
        if session['role']=='business_operator':
            return self.portfolio_commands(session,nonce,action,data)
        if session['role']=='participant':
            return self.member_commands(session,nonce,action,data)
        require(Principal(session['role']),True)
        if data.get('id') and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',data['id']):
            raise ValueError('Invalid reference')
        if not re.fullmatch(r'[a-f0-9]{32}',nonce):
            raise ValueError('Invalid command')
        def op():
            current=self.db.execute('SELECT s.*,u.role AS current_role FROM office_sessions s JOIN office_users u ON u.username=s.username WHERE s.token_hash=?',(session.get('token_hash'),)).fetchone()
            if not current or current['username']!=session['username'] or current['current_role']!='representative' or current['role']!='representative' or current['expires_at']<=now().isoformat() or current['seen_at']<(now()-timedelta(minutes=30)).isoformat():
                raise PermissionError('セッションが失効しています')
            fingerprint=digest([action,data])
            old=self.db.execute('SELECT * FROM office_commands WHERE nonce=?',(nonce,)).fetchone()
            if old:
                if old['actor']!=session['username'] or old['fingerprint']!=fingerprint:
                    raise ValueError('Command key conflict')
                return old['result']
            result=self._command(session,action,data)
            self.db.execute('INSERT INTO office_commands VALUES (?,?,?,?)',(nonce,session['username'],fingerprint,result))
            self.audit(session['username'],action,data.get('id',''))
            return result
        return self.store._transaction(op)

    def _mock_admin(self):
        # Management-only instance: no signature key, no recipient mapping, no webhook route.
        integration=object.__new__(Integrations)
        integration.store=self.store
        integration.db=self.db
        integration.content=self.content
        integration.adapter=MockAdapter()
        integration._secret=None
        integration._key=None
        integration._destination=None
        return integration

    def member_commands(self,session,nonce,action,data):
        if not re.fullmatch(r'[a-f0-9]{32}',nonce): raise ValueError('Invalid command')
        def op():
            current=self.session_by_snapshot(session)
            if not current or current['role']!='participant': raise PermissionError('Participant session required')
            fingerprint=digest([session['username'],action,data])
            old=self.db.execute('SELECT * FROM office_commands WHERE nonce=?',(nonce,)).fetchone()
            if old:
                if old['actor']!=session['username'] or old['fingerprint']!=fingerprint: raise ValueError('Conflicting command')
                return old['result']
            username=session['username']
            if action=='seminar_member_register': self.seminars.register(username,data['segment'],data.get('card',''))
            elif action=='seminar_member_response': self.seminars.respond(username,data['id'],data['answer'])
            elif action=='seminar_member_consent': self.seminars.consent(username,data['granted']=='1')
            elif action=='s14_member_consent': self.seminar_support.consent(Principal('participant',username),data['id'],data['purpose'],data['granted']=='1')
            elif action=='s14_member_preferences': self.seminar_support.preferences(Principal('participant',username),data['id'],[k for k in ('health','beauty','work','connection','other') if data.get('interest_'+k)=='1'])
            elif action=='s14_member_response': self.seminar_support.response(Principal('participant',username),data['id'],data['answer'])
            elif action=='s14_member_interview': self.seminar_support.request_interview(Principal('participant',username),data['id'])
            else: raise PermissionError('Participant action denied')
            result='/seminar-participation' if action.startswith('s14_') else '/seminars'
            self.db.execute('INSERT INTO office_commands VALUES(?,?,?,?)',(nonce,username,fingerprint,result))
            self.audit(username,action)
            return result
        return self.store._transaction(op)

    def session_by_snapshot(self,session):
        row=self.db.execute('SELECT s.*,u.role AS current_role FROM office_sessions s JOIN office_users u USING(username) WHERE s.token_hash=?',(session.get('token_hash'),)).fetchone()
        return row if row and row['username']==session['username'] and row['role']==row['current_role'] and row['expires_at']>now().isoformat() and row['seen_at']>(now()-timedelta(minutes=30)).isoformat() else None

    def phase9_command(self,session,action,data):
        principal=Principal(session['role'],session['username'])
        sem=self.seminars
        if action=='selection_toggle': self.selection.set_enabled(principal,data['enabled']=='1')
        elif action=='selection_propose': self.selection.propose(principal,data['key'],data['kind'],data['start'],data['end'])
        elif action=='selection_assign': self.selection.assign(principal,data['id'],data['agent'])
        elif action=='seminar_account':
            self.add_user(data['username'],'participant',data['password'])
            sem.account(principal,data['username'],data['display_name'],data['evidence'],data.get('attested')=='1')
        elif action=='seminar_save':
            values=dict(data)
            for key in ('start_at','end_at','deadline'):
                parsed=datetime.fromisoformat(values[key])
                if parsed.tzinfo is None: parsed=parsed.replace(tzinfo=JST)
                values[key]=parsed.isoformat()
            sem.save(principal,values,data.get('id') or None,data.get('revision'))
        elif action=='seminar_state': sem.change_state(principal,data['id'],data['status'])
        elif action=='seminar_registration': sem.decide_registration(principal,data['username'],data['decision'])
        elif action=='seminar_schedule': sem.schedule(principal,data['enabled']=='1')
        elif action=='seminar_tick': sem.tick(principal)
        elif action=='seminar_reprepare': sem.reprepare(principal,data['id'])
        elif action=='seminar_approve': sem.approve_reminder(principal,data['id'],data['decision'])
        elif action=='seminar_mock': sem.dispatch_mock(principal,data['id'])
        elif action=='seminar_work': sem.prepare_work(principal,data['id'])
        else: raise ValueError('Unknown phase-9 action')
        self.audit(session['username'],action,data.get('id',''))
        return '/selection' if action.startswith('selection_') else '/seminar-admin'

    def portfolio_commands(self,session,nonce,action,data):
        if not action.startswith(('portfolio_','bd_','scm_','wf_','s14_','bf_','ops_')) or not re.fullmatch(r'[a-f0-9]{32}',nonce): raise PermissionError('Scoped operation required')
        def op():
            current=self.session_by_snapshot(session)
            if not current or current['role']!='business_operator': raise PermissionError('Scoped session expired')
            fingerprint=digest([session['username'],action,data])
            old=self.db.execute('SELECT * FROM office_commands WHERE nonce=?',(nonce,)).fetchone()
            if old:
                if old['actor']!=session['username'] or old['fingerprint']!=fingerprint: raise ValueError('Command conflict')
                # Recheck access even for replay after a grant is revoked.
                if action.startswith('bd_'): self.development.access(Principal(session['role'],session['username']),data.get('idea'),True)
                else: self.portfolio.access(Principal(session['role'],session['username']),data.get('business'),True)
                return old['result']
            result=self.business_operations_command(session,action,data) if action.startswith('ops_') else self.business_foundation_command(session,action,data) if action.startswith('bf_') else self.seminar_support_command(session,action,data) if action.startswith('s14_') else self.workflow_command(session,action,data) if action.startswith('wf_') else self.sales_command(session,action,data) if action.startswith('scm_') else self.development_command(session,action,data) if action.startswith('bd_') else self.portfolio_command(session,action,data)
            self.db.execute('INSERT INTO office_commands VALUES(?,?,?,?)',(nonce,session['username'],fingerprint,result))
            return result
        return self.store._transaction(op)

    def portfolio_command(self,session,action,data):
        service=self.portfolio; p=Principal(session['role'],session['username']); business=data.get('business')
        if action=='portfolio_seed': service.seed(p)
        elif action=='portfolio_create': service.create(p,data['business'],data['name'],data['kind'])
        elif action=='portfolio_update': service.update(p,business,{**data,'confirmed':data.get('confirmed')=='1'},data['revision'])
        elif action=='portfolio_grant':
            service.access(p,business,owner=True)
            if not self.db.execute('SELECT 1 FROM office_users WHERE username=?',(data['username'],)).fetchone(): self.add_user(data['username'],'business_operator',data['password'])
            service.grant(p,business,data['username'],data.get('write')=='1')
        elif action=='portfolio_revoke': service.revoke(p,business,data['username'])
        elif action=='portfolio_halt': service.halt(p,data['halted']=='1')
        elif action=='portfolio_contact': service.contact(p,business,data['name'],data['channel_ref'])
        elif action=='portfolio_consent': service.consent(p,business,data['contact'],data['purpose'],data['granted']=='1',data['notice'])
        elif action=='portfolio_case':
            times=[]
            for key in ('start_at','end_at'):
                value=data.get(key)
                if value:
                    parsed=datetime.fromisoformat(value)
                    if parsed.tzinfo is None: parsed=parsed.replace(tzinfo=JST)
                    value=parsed.isoformat()
                times.append(value or None)
            service.case(p,business,data['key'],data['kind'],data['subject'],data.get('contact') or None,*times)
        elif action=='portfolio_progress': service.progress(p,business,data['id'],data['status'])
        elif action=='portfolio_document': service.document(p,business,data['kind'],data.get('case') or None,{'body':data['body']} if data.get('body') else None,data.get('parent') or None)
        elif action=='portfolio_document_decide': service.decide_document(p,business,data['id'],data['decision'],data.get('checked')=='1')
        elif action=='portfolio_operation': service.operation(p,business,data['key'],data['kind'],data.get('case') or None,data.get('document') or None)
        elif action=='portfolio_operation_decide': service.decide_operation(p,business,data['id'],data['decision'],data.get('checked')=='1')
        elif action=='portfolio_manual': service.record_manual(p,business,data['id'],data['evidence'])
        elif action=='portfolio_task': service.task(p,business,data['key'],data['kind'],data['instruction'],int(data['priority']),data.get('agent') or None)
        elif action=='portfolio_run': service.run(p,business,data['id'])
        elif action=='portfolio_task_control': service.task_control(p,business,data['id'],data['control'],data.get('priority'))
        elif action=='portfolio_cost': service.cost(p,business,data['key'],data['task'],data['model'],usd(data['amount']),data['kind'])
        elif action=='portfolio_budget': service.budget(p,business,data['month'],usd(data['amount']))
        elif action=='portfolio_ledger': service.ledger(p,business,data['key'],data['day'],data['kind'],int(data['amount']),data['mode'],data['evidence'])
        elif action=='portfolio_measure': service.measure(p,business,data['day'],data['metric'],int(data['value']),data['mode'],data['evidence'],data.get('previous') or None)
        elif action=='portfolio_goal': service.goal(p,business,data['month'],data['metric'],int(data['value']) if data.get('value') else None)
        elif action=='portfolio_report': service.report(p,data['start'],data['end'])
        elif action=='portfolio_report_decide': service.decide_report(p,data['id'],data['decision'])
        elif action=='portfolio_delete_request': service.request_delete(p,business,data['contact'])
        elif action=='portfolio_delete_approve': service.approve_delete(p,business,data['contact'],data.get('checked')=='1')
        elif action=='portfolio_seminar': service.link_seminar(p,business,data['id'])
        elif action=='portfolio_photo':
            import base64
            service.photo(p,business,data['case'],data['stage'],base64.b64decode(data['image'],validate=True))
        else: raise ValueError('Unknown scoped action')
        return '/business?business='+business if business and action!='portfolio_create' else '/businesses'

    def development_command(self,session,action,data):
        from .development import BRIEF
        from .development_finance import PARAMETERS
        d=self.development; p=Principal(session['role'],session['username']); idea=data.get('idea')
        if action=='bd_create':
            idea=d.create(p,data['title'],{k:data.get(k) for k in BRIEF},data.get('business') or None)
        elif action=='bd_edit': d.edit(p,idea,data['title'],{k:data.get(k) for k in BRIEF},int(data['revision']))
        elif action=='bd_idea_decide': d.decide_idea(p,idea,data['decision'])
        elif action=='bd_source': d.source(p,idea,data['topic'],data['claim'],data.get('source_ref',''),data.get('research_day') or None,data['confidence'],data['state'],data.get('unknowns',''))
        elif action=='bd_source_withdraw': d.withdraw_source(p,idea,data['id'])
        elif action=='bd_scenario': d.scenario(p,idea,data['name'],{k:data.get(k) for k in PARAMETERS},data.get('basis',''))
        elif action=='bd_proposal': d.proposal(p,idea,data.get('with_content')=='1')
        elif action=='bd_proposal_decide': d.decide_proposal(p,data['id'],data['decision'],data.get('checked')=='1')
        elif action=='bd_plan': d.plan(p,data['id'],int(data['horizon']),{'budget_yen':data.get('budget_yen'),'api_budget_microusd':usd(data.get('api_budget_usd','0')),'start_day':data['start_day'],'kpi':data.get('kpi'),'withdrawal':data.get('withdrawal')})
        elif action=='bd_run': d.run(p,data['id'])
        elif action=='bd_resume': d.resume(p,data['id'])
        elif action=='bd_pause': d.pause(p,data['id'])
        elif action=='bd_actual': d.actual(p,idea,data['day'],data['metric'],data['value'],data['mode'],data['evidence'],data.get('previous') or None)
        elif action=='bd_validation_review': d.validation_review(p,idea)
        elif action=='bd_validation_decide': d.decide_validation_review(p,data['id'],data['decision'])
        elif action=='bd_compare':
            comparison,_=d.compare(p,[v.strip() for v in data['ideas'].split(',') if v.strip()],data['criterion'])
            return '/development-compare?id='+comparison
        else: raise ValueError('Unknown development action')
        return '/development-idea?id='+idea if idea else '/development'

    def sales_command(self,session,action,data):
        sales=self.sales; p=Principal(session['role'],session['username']); b=data.get('business'); deal=data.get('deal')
        if action=='scm_customer': sales.customer(p,b,data['name'],data['channel'])
        elif action=='scm_customer_update': sales.update_customer(p,b,data['contact'],data['name'],data['channel'],data.get('verified')=='1')
        elif action=='scm_stop': sales.stop(p,b,data['contact'],data['stopped']=='1')
        elif action=='scm_consent': self.portfolio.consent(p,b,data['contact'],data['purpose'],data['granted']=='1',data['notice'])
        elif action=='scm_link': sales.link(p,b,data['contact'],data['other_business'],data['other_contact'],data['evidence'])
        elif action=='scm_unlink': sales.unlink(p,b,data['contact'])
        elif action=='scm_tax': sales.tax_policy(p,b,data['tax_type'],data.get('rate',''),data.get('rounding',''),data.get('confirmed')=='1')
        elif action=='scm_rate': sales.rate(p,b,data['label'],data.get('unit'),data['conditions'],data.get('confirmed')=='1',data.get('id') or None,data.get('revision'))
        elif action=='scm_cleaning_candidate': sales.cleaning_candidate(p,b)
        elif action=='scm_deal': deal=sales.deal(p,b,data['key'],data['contact'],data['subject'],data['inquiry'],data['mode'],data.get('agent') or None,data.get('human') or None,data.get('next_day') or None)
        elif action=='scm_assign': sales.update_deal(p,b,deal,data.get('next_day'),data.get('human'),data.get('agent'),data['revision'])
        elif action=='scm_stage': sales.transition(p,b,deal,data.get('stage') or None,data.get('state') or None,data['revision'])
        elif action=='scm_quote': sales.quote(p,b,deal,json.loads(data['lines']) if data.get('lines') else [{'label':data['line_label_'+str(i)],'quantity':data['quantity_'+str(i)],'unit_yen':data['unit_'+str(i)]} for i in range(1,6) if data.get('line_label_'+str(i))],data['conditions'],data['expires'],data.get('previous') or None)
        elif action=='scm_quote_decide': sales.decide_quote(p,b,data['id'],data['decision'],data.get('checked')=='1')
        elif action=='scm_booking':
            def local(key):
                parsed=datetime.fromisoformat(data[key]); return (parsed if parsed.tzinfo else parsed.replace(tzinfo=JST)).isoformat()
            sales.booking(p,b,deal,data['kind'],local('start'),local('end'))
        elif action=='scm_action': sales.action(p,b,deal,data['key'],data['kind'],data.get('quote') or None,data.get('booking') or None,{'message':data.get('message',''),'purpose':data.get('purpose','contact'),**({'invoice_id':data['invoice']} if data.get('invoice') else {}),**({'amount_yen':int(data['amount'])} if data.get('amount') else {})})
        elif action=='scm_action_decide': sales.decide_action(p,b,data['id'],data['decision'],data.get('checked')=='1')
        elif action=='scm_mock': sales.execute_mock(p,b,data['id'],data.get('outcome','success'))
        elif action=='scm_manual': sales.record_manual(p,b,data['id'],data['evidence'],data.get('observed')=='1')
        elif action=='scm_delete': sales.delete_contact(p,b,data['id'],data.get('checked')=='1')
        elif action=='scm_note': sales.note(p,b,deal,data['body'])
        elif action=='scm_work': sales.work(p,b,deal,data['key'],data['activity'],data.get('body') or None,data.get('agent') or None)
        elif action=='scm_run': sales.run_work(p,b,data['id'])
        elif action=='scm_role_request': sales.role_request(p,b,data['work'],data.get('agent') or None,data['reason'])
        elif action=='scm_seminar': sales.seminar(p,b,deal,data['seminar'],data.get('participant') or None,data.get('evidence'))
        else: raise ValueError('Unknown sales command')
        return '/sales-deal?business='+b+'&id='+deal if deal else '/sales?business='+b

    def workflow_command(self,session,action,data):
        w=self.workflow; p=Principal(session['role'],session['username']); b=data['business']; d=data.get('deal')
        if action=='wf_attach': w.attach(p,b,d)
        elif action=='wf_advance': w.advance(p,b,d,data['stage'],data['revision'])
        elif action=='wf_assign': w.assign(p,b,d,data['key'],data['agent'],data['purpose'],data['basis'],data['due'],data.get('priority',3))
        elif action=='wf_control': w.control(p,b,data['task'],data['control'],data.get('value'))
        elif action=='wf_run': w.run(p,b,data['task'])
        elif action=='wf_decide': w.decide_task(p,b,data['task'],data['decision'],data.get('checked')=='1')
        elif action=='wf_draft': w.draft(p,b,d,data['seminar'],data['username'],data['kind'])
        elif action=='wf_draft_decide': w.decide_draft(p,b,data['id'],data['decision'],data.get('checked')=='1')
        elif action=='wf_attendance': w.attendance(p,b,d,data['seminar'],data['username'],data['outcome'],data['evidence'])
        else: raise ValueError('Unknown workflow command')
        return '/workflow?business='+b

    def seminar_support_command(self,session,action,data):
        service=self.seminar_support; p=Principal(session['role'],session['username']); b=data['business']; sid=data.get('seminar')
        if action=='s14_save':
            values=dict(data)
            for key in ('start_at','end_at','deadline'):
                parsed=datetime.fromisoformat(data[key]); values[key]=(parsed if parsed.tzinfo else parsed.replace(tzinfo=JST)).isoformat()
            sid=service.save(p,b,values,[k for k in ('dealer','subdealer','user','cast') if data.get('target_'+k)=='1'],data.get('online',''),data['mode'],data.get('lead_hours',24),data.get('clock','09:00'),sid or None,data.get('revision'))
        elif action=='s14_adopt': service.adopt(p,b,sid,data['mode'])
        elif action=='s14_state': service.state(p,b,sid,data['state'])
        elif action=='s14_engagement':
            when=data.get('when')
            if when:
                parsed=datetime.fromisoformat(when); when=(parsed if parsed.tzinfo else parsed.replace(tzinfo=JST)).isoformat()
            service.engagement(p,b,sid,data['username'],data['state'],data['evidence'],when)
        elif action=='s14_prepare': service.prepare(p,b,sid,data['username'],data['kind'])
        elif action=='s14_bulk': service.bulk(p,b,sid,data['kind'])
        elif action=='s14_decide': service.decide(p,b,data['id'],data['decision'],data.get('checked')=='1')
        elif action=='s14_revise': service.revise(p,b,data['id'],data['body'])
        elif action=='s14_mock': service.mock(p,b,data['id'],data.get('outcome','success'))
        elif action=='s14_schedule': service.scheduler(p,b,data['enabled']=='1')
        elif action=='s14_tick': service.tick(p,b)
        elif action=='s14_work': service.work(p,b,sid,data['activity'],data['key'],data.get('agent') or None)
        elif action=='s14_run': service.run(p,b,data['task'])
        elif action=='s14_control': service.control(p,b,data['task'],data['control'])
        elif action=='s14_unattribute': service.unattribute(p,b,data['id'],data.get('checked')=='1')
        elif action=='s14_attribute': service.attribute(p,b,sid,data['kind'],data['source'],data['evidence'])
        else: raise ValueError('Unknown seminar extension action')
        return '/seminar-office?business='+b

    def business_foundation_command(self,session,action,data):
        def flag(name,default=None):
            value=data.get(name,default)
            if value not in ('0','1'): raise ValueError('Explicit boolean form field required')
            return value=='1'
        f=self.business_foundation; p=Principal(session['role'],session['username']); b=data.get('business')
        if action=='bf_create': b=f.create(p,data['key'],data['name'],data['overview'],data['category'],data.get('started_on') or None,data.get('kind','other'))
        elif action=='bf_adopt': f.adopt(p,b,data['category'],data.get('started_on') or None)
        elif action=='bf_update': f.update(p,b,data['overview'],data['category'],data.get('started_on') or None,flag('enabled'),data['status'],flag('confirmed','0'),data['revision'],data['business_revision'])
        elif action=='bf_assign': f.assign(p,b,data['agent'],data['role_note'],data['scope_note'],json.loads(data['permissions']),flag('active'),data.get('revision',0))
        elif action=='bf_enforce': f.enforce(p,b,flag('enabled'),flag('checked','0'))
        elif action=='bf_kpi': f.kpi(p,b,data['metric_key'],data['label'],data['unit'],flag('active'),data.get('revision',0))
        elif action=='bf_measure': f.measure(p,b,data['metric_key'],data['day'],data['value'],data['mode'],data['evidence'],data.get('revision',0))
        else: raise ValueError('Unknown foundation action')
        return '/business-foundation?business='+b

    def business_operations_command(self,session,action,data):
        service=self.business_operations; p=Principal(session['role'],session['username']); b=data['business']
        if action=='ops_finance': service.finance_policy(p,b,data['source'],data['evidence'],data.get('revision',0))
        elif action=='ops_rule': service.rule(p,b,data['metric_key'],data['aggregation'],data['evidence'],data.get('revision',0))
        elif action=='ops_target': service.target(p,b,data['metric_key'],data['start'],data['end'],data['target'],data['direction'],data['evidence'],data.get('revision',0))
        else: raise ValueError('Unknown operations action')
        return '/business?business='+b+'&start='+data['start']+'&end='+data['end']

    def _command(self,session,action,data):
        if action.startswith('ops_'):
            return self.business_operations_command(session,action,data)
        if action.startswith('bf_'):
            return self.business_foundation_command(session,action,data)
        if action.startswith('s14_'):
            return self.seminar_support_command(session,action,data)
        if action.startswith('wf_'):
            return self.workflow_command(session,action,data)
        if action.startswith('scm_'):
            return self.sales_command(session,action,data)
        if action.startswith('bd_'):
            return self.development_command(session,action,data)
        if action.startswith('portfolio_'):
            return self.portfolio_command(session,action,data)
        if action.startswith(('seminar_','selection_')):
            return self.phase9_command(session,action,data)

        ref=data.get('id','')
        principal=Principal(session['role'],session['username'])
        if action=='learning_feedback':
            self.learning.feedback(principal,data['key'],data['target_kind'],data['target_id'],data['choice'],data['comment'],{key:data.get(key)=='1' for key in STYLES},data.get('attested')=='1')
            return '/learning'
        if action=='learning_feedback_decide':
            self.learning.decide_feedback(principal,ref,data['decision'])
            return '/learning'
        if action in ('learning_knowledge','learning_promote'):
            expiry=datetime.fromisoformat(data['expires'])
            if expiry.tzinfo is None:
                expiry=expiry.replace(tzinfo=JST)
            scope=[s.strip() for s in data['scope'].split(',') if s.strip()]
            if action=='learning_promote':
                self.learning.promote_feedback(principal,ref,data['title'],scope,expiry.isoformat())
            else:
                self.learning.knowledge(principal,data['category'],data['task_kind'],data['title'],data['body'],data['source_kind'],data['source_ref'],scope,expiry.isoformat(),data.get('attested')=='1',data.get('previous') or None)
            return '/learning'
        if action=='learning_knowledge_decide':
            self.learning.decide_knowledge(principal,ref,data['decision'])
            return '/learning'
        if action=='learning_rate':
            self.learning.rate(principal,data['target_kind'],data['target_id'],int(data['score']),data['rubric'])
            return '/learning'
        if action=='learning_evaluate':
            identifier,_=self.learning.evaluate(principal,data['task_kind'],data['start'],data['end'])
            return '/learning?evaluation='+identifier
        if action=='learning_propose':
            self.learning.propose(principal,data['task_kind'],[s.strip() for s in data.get('knowledge_ids','').split(',') if s.strip()],data.get('evaluation_id') or None)
            return '/learning'
        if action=='learning_compare':
            self.learning.compare(principal,ref)
            return '/learning'
        if action=='learning_improvement_decide':
            self.learning.decide_proposal(principal,ref,data['decision'])
            return '/learning'
        if action=='learning_apply':
            self.learning.apply(principal,ref)
            return '/learning'
        if action=='learning_disable':
            disable_style(self.learning,principal,ref)
            return '/learning'
        if action=='learning_index':
            self.learning.index_history(principal)
            return '/learning'
        if action=='growth_goals':
            values={key:None if data.get(key,'').strip()=='' else int(data[key]) for key in GOALS if key!='ai_budget_microusd'}
            values['ai_budget_microusd']=None if not data.get('ai_budget_usd','').strip() else usd(data['ai_budget_usd'])
            self.growth.set_goals(data['month'],values,session['username'])
            return '/growth?month='+data['month']
        if action=='growth_measure':
            self.growth.record_metric(data['key'],data['metric'],data['day'],int(data['value']),data['quality'],data['mode'],data['source'],data['evidence'],session['username'],data.get('replace_id') or None)
            return '/growth'
        if action=='growth_time':
            self.growth.record_time(data['key'],data['day'],int(data['minutes']),data['quality'],data['mode'],data['evidence'],session['username'])
            return '/growth'
        if action=='growth_schedule':
            first=datetime.fromisoformat(data['first_due'])
            if first.tzinfo is None:
                first=first.replace(tzinfo=JST)
            self.growth.create_schedule(data['key'],data['cadence'],first,data['period_mode'],data.get('prepare_draft')=='1',session['username'])
            return '/growth'
        if action=='growth_schedule_edit':
            first=datetime.fromisoformat(data['first_due'])
            if first.tzinfo is None:
                first=first.replace(tzinfo=JST)
            self.growth.edit_schedule(ref,first,data['period_mode'],data.get('prepare_draft')=='1',session['username'])
            return '/growth'
        if action=='growth_state':
            if data['enabled'] not in ('0','1'):
                raise ValueError('Invalid schedule state')
            self.growth.schedule_state(ref,data['enabled']=='1',session['username'])
            return '/growth'
        if action=='growth_tick':
            self.growth.tick(actor=session['username'])
            return '/growth'
        if action=='growth_instruction':
            run=self.growth.instruction(data['key'],data['instruction'],session['username'])
            return '/reports?id='+run
        if action=='growth_report':
            run=self.growth.run(data['key'],data['cadence'],mode=data['period_mode'],actor=session['username'])
            return '/reports?id='+run
        if action=='growth_decide':
            self.growth.decide(ref,data['decision'],session['username'])
            return '/reports?id='+ref
        if action=='submit':
            safe_input(data['instruction'])
            request=self.coordinator.submit(data['key'],data['instruction'],int(data.get('priority','3')))
            return '/requests?id='+request
        if action=='generate':
            # Explicit template mode, never a hidden claim of general language understanding.
            safe_input(data['instruction'])
            campaign=self.content.generate(data['key'],data['instruction'],int(data['days']),data['platform'],data['start_date'])
            return '/approvals?campaign='+campaign
        if action in ('start','stop','retry'):
            if self.db.execute('SELECT 1 FROM bd_tasks WHERE task_id=?',(ref,)).fetchone(): raise ValueError('Use development plan controls')
            if self.db.execute('SELECT 1 FROM portfolio_tasks WHERE task_id=?',(ref,)).fetchone(): raise ValueError('Use the scoped business task controls')
            if self.db.execute('SELECT 1 FROM growth_work WHERE task_id=?',(ref,)).fetchone():
                raise ValueError('Use the local scheduler controls for generated work')
            row=self.db.execute('SELECT * FROM tasks WHERE id=?',(ref,)).fetchone()
            if not row:
                raise ValueError('Unknown task')
            if action=='start':
                if row['status']=='paused':
                    self.db.execute("UPDATE tasks SET status='pending' WHERE id=?",(ref,))
                self.store.claim(ref)
            elif action=='stop':
                if row['status'] not in ('pending','running'):
                    raise ValueError('Task cannot be paused')
                self.db.execute("UPDATE runs SET status='cancelled' WHERE task_id=? AND status='running'",(ref,))
                self.db.execute("UPDATE tasks SET status='paused' WHERE id=?",(ref,))
                self.store._event(ref,'representative_paused')
            else:
                if row['status']!='failed':
                    raise ValueError('Only failed local tasks can be reissued')
                # Do not invalidate a result already collected or reviewed.
                if self.db.execute("SELECT 1 FROM dependencies d JOIN tasks t ON t.id=d.task_id WHERE d.prerequisite=? AND t.status!='pending'",(ref,)).fetchone():
                    raise ValueError('Dependent task already advanced')
                if self.db.execute('SELECT 1 FROM campaign_work WHERE task_id=?',(ref,)).fetchone():
                    raise ValueError('Campaign must be reissued as a whole')
                new=self.store.create_task('office-retry:'+uuid4().hex,row['agent'],json.loads(row['payload']),row['action'],row['max_attempts'],row['timeout_seconds'])
                self.db.execute('UPDATE request_tasks SET task_id=? WHERE task_id=?',(new,ref))
                self.db.execute('UPDATE dependencies SET prerequisite=? WHERE prerequisite=?',(new,ref))
                self.db.execute('UPDATE dependencies SET task_id=? WHERE task_id=?',(new,ref))
                self.db.execute('INSERT INTO office_task_links VALUES (?,?,?)',(ref,new,now().isoformat()))
                self.db.execute('UPDATE growth_proposals SET task_id=? WHERE task_id=?',(new,ref))
                self.db.execute("UPDATE tasks SET status='superseded' WHERE id=?",(ref,))
            return '/employees?id='+row['agent']
        if action=='priority':
            priority=int(data['priority'])
            if not 1<=priority<=5:
                raise ValueError('Invalid priority')
            if not self.db.execute('SELECT 1 FROM requests WHERE id=?',(ref,)).fetchone():
                raise ValueError('Unknown request')
            self.db.execute('UPDATE request_options SET priority=? WHERE request_id=?',(priority,ref))
            return '/requests?id='+ref
        if action=='result':
            if self.db.execute('SELECT 1 FROM bd_tasks WHERE task_id=?',(ref,)).fetchone(): raise ValueError('Use development plan controls')
            if self.db.execute('SELECT 1 FROM portfolio_tasks WHERE task_id=?',(ref,)).fetchone(): raise ValueError('Business outputs must remain in scoped storage')
            safe_input(data['content'])
            run=self.db.execute("SELECT id FROM runs WHERE task_id=? AND status='running'",(ref,)).fetchone()
            if not run:
                raise ValueError('Start a local task first')
            self.store.finish(run[0],data['content'])
            return '/approvals'
        if action=='collect':
            self.coordinator.collect(ref)
            return '/requests?id='+ref
        if action=='task_review':
            artifact=self.db.execute('SELECT * FROM artifacts WHERE id=?',(ref,)).fetchone()
            if not artifact:
                raise ValueError('Unknown artifact')
            reviewer=self.store.agents[artifact['author']]['reviewer']
            safe_input(data['note'])
            self.store.review(ref,reviewer,data['verdict'],data['note'])
            return '/approvals'
        if action=='task_decide':
            if data['decision']=='changes':
                task=self.db.execute('SELECT task_id FROM artifacts WHERE id=?',(ref,)).fetchone()
                if not task:
                    raise ValueError('Unknown artifact')
                if self.db.execute("UPDATE tasks SET status='changes_requested' WHERE id=? AND status='approval_pending'",(task[0],)).rowcount!=1:
                    raise ValueError('Not awaiting approval')
                self.store._event(task[0],'representative_changes_requested')
            else:
                self.store.decide(ref,data['decision'],'representative')
            return '/approvals'
        if action=='content_check':
            self.content.confirm_check(ref,data['check'],data['evidence'])
            return '/approvals'
        if action=='content_decide':
            if data['decision']=='changes':
                row=self.content._latest(ref)
                if row['status']!='approval_pending':
                    raise ValueError('Not awaiting approval')
                self.db.execute("UPDATE content_versions SET status='changes_requested' WHERE id=?",(ref,))
                self.content._event(ref,'changes_requested',session['username'])
            else:
                self.content.decide(ref,data['decision'],'representative')
            return '/approvals'
        if action=='revise':
            content=self.content.revise(ref,json.loads(data['body']))
            self.content.audit(content)
            return '/approvals'
        if action=='operation_decide':
            integration=self._mock_admin()
            if data['decision']=='changes':
                if self.db.execute("UPDATE ix_operations SET status='changes_requested' WHERE id=? AND status='approval_pending'",(ref,)).rowcount!=1:
                    raise ValueError('Not awaiting approval')
                integration._audit('representative_changes_requested',ref,session['username'])
            else:
                integration.decide(Principal('representative'),ref,data['decision'])
            return '/approvals'
        if action=='budget':
            amount=self._money(data['amount'])
            month=data['month']
            if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month):
                raise ValueError('Invalid month')
            self.db.execute('INSERT INTO office_budgets VALUES (?,?) ON CONFLICT(month) DO UPDATE SET limit_microusd=excluded.limit_microusd',(month,amount))
            return '/costs'
        if action=='cost':
            if data['kind'] not in ('estimated','confirmed') or data['mode'] not in ('manual','mock') or data['agent'] not in self.store.agents:
                raise ValueError('Invalid accounting entry')
            from datetime import date
            date.fromisoformat(data['date'])
            safe_input(data['model'])
            safe_input(data['evidence'])
            task=data.get('task') or None
            if task:
                row=self.db.execute('SELECT agent FROM tasks WHERE id=?',(task,)).fetchone()
                if not row or row['agent']!=data['agent']:
                    raise ValueError('Task agent mismatch')
            entry_key=data['key']
            if self.db.execute('SELECT 1 FROM office_costs WHERE entry_key=?',(entry_key,)).fetchone():
                raise ValueError('Accounting entry exists')
            self.db.execute('INSERT INTO office_costs VALUES (?,?,?,?,?,?,?,?,?,?)',(uuid4().hex,entry_key,data['model'],data['agent'],task,self._money(data['amount']),data['kind'],data['mode'],data['date'],data['evidence']))
            return '/costs'
        raise ValueError('Unsupported operation')

    @staticmethod
    def _money(value):
        try:
            number=Decimal(value)
            if not number.is_finite() or number<0 or number>1000000 or number*1000000!=(number*1000000).to_integral_value():
                raise ValueError('Invalid USD value')
            return int(number*1000000)
        except InvalidOperation:
            raise ValueError('Invalid USD value')

    def tasks(self):
        return [dict(r) for r in self.db.execute('SELECT t.*,MAX(e.created_at) AS updated_at,rt.request_id,COALESCE(o.priority,g.priority) AS priority,COALESCE(g.due_at,w.due_at) AS planned_due FROM tasks t LEFT JOIN events e ON e.task_id=t.id LEFT JOIN request_tasks rt ON rt.task_id=t.id LEFT JOIN request_options o ON o.request_id=rt.request_id LEFT JOIN growth_proposals g ON g.task_id=t.id LEFT JOIN growth_work w ON w.task_id=t.id GROUP BY t.id ORDER BY COALESCE(o.priority,g.priority,3),updated_at DESC')]

    def costs(self):
        entries=[dict(r) for r in self.db.execute('SELECT * FROM office_costs ORDER BY incurred_date DESC')]
        totals={}
        for entry in entries:
            for group,value in [('model',entry['model']),('day',entry['incurred_date']),('month',entry['incurred_date'][:7]),('department',department(entry['agent'])),('task',entry['task_id'] or 'タスク未分類')]:
                key=(group,value,entry['kind'],entry['mode'])
                totals[key]=totals.get(key,0)+entry['amount']
        return {'entries':entries,'groups':[{'group':k[0],'label':k[1],'kind':k[2],'mode':k[3],'amount':v} for k,v in totals.items()],
            'budgets':[dict(r) for r in self.db.execute('SELECT * FROM office_budgets ORDER BY month DESC')],
            'reservations':[dict(r) for r in self.db.execute('SELECT r.run_id,r.reserved,r.actual,t.agent,ru.task_id,ru.deadline FROM reservations r JOIN runs ru ON ru.id=r.run_id JOIN tasks t ON t.id=ru.task_id')]}

    def metrics(self):
        tasks=self.tasks()
        today=now().astimezone(JST).date().isoformat()
        created={r[0]:r[1] for r in self.db.execute("SELECT task_id,MIN(created_at) FROM events WHERE event='created' GROUP BY task_id")}
        todays=sum(datetime.fromisoformat(value).astimezone(JST).date().isoformat()==today for value in created.values())
        pending=self.db.execute("SELECT COUNT(*) FROM content_versions WHERE status='approval_pending'").fetchone()[0]+sum(t['status']=='approval_pending' for t in tasks)+self.db.execute("SELECT COUNT(*) FROM ix_operations WHERE status='approval_pending'").fetchone()[0]
        pending+=self.db.execute("SELECT COUNT(*) FROM growth_runs WHERE status='succeeded_local' AND decision='approval_pending'").fetchone()[0]
        manual=self.growth.metrics(today[:7]+'-01',(now().astimezone(JST).date()+timedelta(days=1)).isoformat())
        mapping={'今月の問い合わせ':'inquiries','今月の面談予約':'appointments','紹介件数':'referrals','契約件数':'contracts','SNS投稿数':'sns_posts','LINE登録数':'line_registrations','SNSからの流入':'sns_visits','面談希望数':'appointment_requests'}
        business={title:manual[key]['value'] if manual[key]['complete'] else None for title,key in mapping.items()}
        business.update({'問い合わせ→面談転換率':None,'面談→契約転換率':None})
        return {'manual_business':manual,'local':{'本日のローカルタスク':todays if tasks else None,'完了タスク':sum(t['status']=='completed' for t in tasks) if tasks else None,'定期ローカル処理完了':self.db.execute("SELECT COUNT(*) FROM growth_runs WHERE status='succeeded_local'").fetchone()[0],'承認待ち':pending,'タスクエラー':sum(t['status']=='failed' for t in tasks)},
            'business':business,
            'mock':{'LINE仮名登録':self.db.execute('SELECT COUNT(*) FROM ix_customers').fetchone()[0], '問い合わせ':self.db.execute('SELECT COUNT(*) FROM ix_inquiries').fetchone()[0], '予約確定':self.db.execute("SELECT COUNT(*) FROM ix_bookings WHERE status='confirmed'").fetchone()[0]},
            'month':today[:7]}

    def alerts(self):
        alerts=[]
        for label,query in [
            ('定期処理の期限切れ',"SELECT COUNT(*) FROM growth_runs WHERE status='running' AND deadline<=?"),
            ('定期スケジュールの自動停止',"SELECT COUNT(*) FROM growth_schedules WHERE state IN ('auto_paused','attempt_limit')"),
            ('定期ローカル処理の失敗・確認待ち',"SELECT COUNT(*) FROM growth_runs WHERE status='failed' OR status='paused' AND error_code IN ('lease_expired','recovery_hold')"),
            ('失敗タスク',"SELECT COUNT(*) FROM tasks WHERE status='failed'"),
            ('モックAPI失敗/結果不明',"SELECT COUNT(*) FROM ix_operations WHERE status IN ('failed','manual_reconciliation')"),
            ('期限切れの実行',"SELECT COUNT(*) FROM runs WHERE status='running' AND deadline<=?"),
            ('修正が必要な原稿',"SELECT COUNT(*) FROM content_versions WHERE status='changes_requested'"),
        ]:
            count=self.db.execute(query,(now().isoformat(),) if '?' in query else ()).fetchone()[0]
            if count:
                alerts.append({'label':label,'count':count})
        return alerts
