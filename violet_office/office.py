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
    text=re.sub(r'0\d{1,3}[- ]?\d{2,4}[- ]?\d{4}','[電話非表示]',text)
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

    def audit(self,actor,action,ref=''):
        self.db.execute('INSERT INTO office_audit(actor,action,ref,created_at) VALUES (?,?,?,?)',(actor,action,ref,now().isoformat()))

    def add_user(self,username,role,password):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',username) or role not in ('representative','viewer') or len(password)<12 or len(password)>256:
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
        bucket=now().strftime('%Y%m%d%H')+str(now().minute//10)
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

    def _command(self,session,action,data):
        ref=data.get('id','')
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
