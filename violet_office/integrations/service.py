"""Transactional mock integration service. No public server or real sender exists.

Raw LINE user IDs and incoming message text are not retained. HMAC pseudonyms
cannot be used for real LINE push; production needs a separate encrypted mapping.
"""
import hashlib
import hmac
import html
import json
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from ..content import ContentOffice, encode, digest
from ..store import now
from .adapters import require, verify_line_signature, MockAdapter, RequestLimitError

PURPOSES={'健康','美容','事業紹介'}
SCHEMA='''
CREATE TABLE IF NOT EXISTS ix_customers (
 subject TEXT PRIMARY KEY, friend INTEGER NOT NULL, stopped INTEGER NOT NULL DEFAULT 0,
 booked INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ix_consents (
 id INTEGER PRIMARY KEY, subject TEXT NOT NULL REFERENCES ix_customers(subject),
 purpose TEXT NOT NULL, granted INTEGER NOT NULL, notice_version TEXT NOT NULL,
 evidence TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ix_webhooks (
 event_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, event_type TEXT NOT NULL,
 subject TEXT NOT NULL REFERENCES ix_customers(subject), processed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ix_subject_clock (subject TEXT PRIMARY KEY REFERENCES ix_customers(subject), timestamp INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ix_limits (bucket TEXT PRIMARY KEY, count INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ix_inquiries (
 id TEXT PRIMARY KEY, subject TEXT NOT NULL REFERENCES ix_customers(subject), category TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ix_steps (
 id TEXT PRIMARY KEY, subject TEXT NOT NULL REFERENCES ix_customers(subject), purpose TEXT NOT NULL,
 cycle INTEGER NOT NULL, day INTEGER NOT NULL, due_at TEXT NOT NULL, status TEXT NOT NULL,
 UNIQUE(subject,purpose,cycle,day));
CREATE TABLE IF NOT EXISTS ix_slots (
 id TEXT PRIMARY KEY, start_at TEXT NOT NULL, end_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ix_bookings (
 id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, subject TEXT NOT NULL REFERENCES ix_customers(subject),
 slot_id TEXT NOT NULL REFERENCES ix_slots(id), status TEXT NOT NULL, replacement_for TEXT REFERENCES ix_bookings(id),
 created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ix_one_slot ON ix_bookings(slot_id) WHERE status IN ('held','confirmed');
CREATE TABLE IF NOT EXISTS ix_operations (
 id TEXT PRIMARY KEY, dedupe_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
 kind TEXT NOT NULL, subject TEXT, purpose TEXT, payload TEXT NOT NULL, customer_revision INTEGER,
 status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_attempt TEXT,
 approved_digest TEXT, mode TEXT NOT NULL DEFAULT 'mock');
CREATE TABLE IF NOT EXISTS ix_approvals (
 id INTEGER PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES ix_operations(id), decision TEXT NOT NULL,
 payload_digest TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ix_attempts (
 id TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES ix_operations(id),
 outcome TEXT NOT NULL, code TEXT, mode TEXT NOT NULL, deadline TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ix_audit (
 id INTEGER PRIMARY KEY, event TEXT NOT NULL, ref TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ix_roles (
 id INTEGER PRIMARY KEY, agent TEXT NOT NULL, action TEXT NOT NULL, ref TEXT NOT NULL,
 mode TEXT NOT NULL, created_at TEXT NOT NULL);
'''


def utc(value):
    parsed=datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Timezone required')
    return parsed.astimezone(timezone.utc).isoformat()


class Integrations:
    def __init__(self, store, pseudonym_key, channel_secret, expected_destination, adapter=None):
        if len(pseudonym_key)<32 or not channel_secret or not expected_destination:
            raise ValueError('Secure local configuration required')
        self.store=store
        self.db=store.db
        self.content=ContentOffice(store)
        self.db.executescript(SCHEMA)
        self._key=pseudonym_key.encode()
        self._secret=channel_secret
        self._destination=expected_destination
        self.adapter=adapter or MockAdapter()
        # Prevent a caller accidentally injecting a live transport into this stage.
        if type(self.adapter) is not MockAdapter:
            raise ValueError('Only the built-in non-network mock adapter is supported')
        self._role('AI25','mock_service_initialized','mock-config')

    def subject_ref(self, raw_user_id):
        return 'subject_'+hmac.new(self._key,raw_user_id.encode(),hashlib.sha256).hexdigest()

    def _audit(self,event,ref,actor='system'):
        self.db.execute('INSERT INTO ix_audit(event,ref,actor,created_at) VALUES (?,?,?,?)',(event,ref,actor,now().isoformat()))

    def _role(self,agent,action,ref):
        if agent not in self.store.agents:
            raise ValueError('Unknown agent')
        self.db.execute('INSERT INTO ix_roles(agent,action,ref,mode,created_at) VALUES (?,?,?,?,?)',(agent,action,ref,'mock_rules_v1',now().isoformat()))

    def _customer(self,subject):
        row=self.db.execute('SELECT * FROM ix_customers WHERE subject=?',(subject,)).fetchone()
        if not row:
            raise ValueError('Unknown customer')
        return row

    def _consent(self,subject,purpose):
        row=self.db.execute('SELECT granted FROM ix_consents WHERE subject=? AND purpose=? ORDER BY id DESC LIMIT 1',(subject,purpose)).fetchone()
        return bool(row and row[0])

    def eligible(self,subject,purpose):
        row=self._customer(subject)
        unresolved_booking=self.db.execute("SELECT 1 FROM ix_bookings WHERE subject=? AND status='held' LIMIT 1",(subject,)).fetchone()
        return bool(not unresolved_booking and row['friend'] and not row['stopped'] and not row['booked'] and row['expires_at']>now().isoformat() and self._consent(subject,purpose))

    def _pause(self,subject,reason):
        self.db.execute("UPDATE ix_steps SET status='stopped' WHERE subject=? AND status='scheduled_mock'",(subject,))
        self.db.execute("UPDATE ix_operations SET status='cancelled' WHERE subject=? AND kind='line' AND status IN ('approval_pending','approved','retry_wait')",(subject,))
        self._audit('paused:'+reason,subject)
        self._role('AI22','pause_mock_steps',subject)

    def _consent_change(self,subject,purpose,granted,evidence):
        if purpose not in PURPOSES:
            raise ValueError('Unsupported purpose')
        self.db.execute('INSERT INTO ix_consents(subject,purpose,granted,notice_version,evidence,created_at) VALUES (?,?,?,?,?,?)',
            (subject,purpose,int(granted),'mock-notice-v1',evidence,now().isoformat()))
        self.db.execute('UPDATE ix_customers SET revision=revision+1 WHERE subject=?',(subject,))
        # No prior approval is carried across a consent change.
        self.db.execute("UPDATE ix_operations SET status='cancelled' WHERE subject=? AND kind='line' AND status IN ('approval_pending','approved','retry_wait')",(subject,))
        if not granted:
            self.db.execute("UPDATE ix_steps SET status='stopped' WHERE subject=? AND purpose=? AND status='scheduled_mock'",(subject,purpose))
        self._audit('consent_granted' if granted else 'consent_withdrawn',subject)
        self._role('AI20','consent_recorded',subject)

    def _steps(self,subject,purpose):
        if not self.eligible(subject,purpose):
            return
        # Separate mock schedule, never imported into or replacing existing LINE DB.
        active=self.db.execute("SELECT 1 FROM ix_steps WHERE subject=? AND purpose=? AND status='scheduled_mock'",(subject,purpose)).fetchone()
        if active:
            return
        cycle=self._customer(subject)['revision']
        self.db.executemany('INSERT INTO ix_steps VALUES (?,?,?,?,?,?,?)',[
            (uuid4().hex,subject,purpose,cycle,day,(now()+timedelta(days=day-1)).isoformat(),'scheduled_mock') for day in range(1,8)])
        self._role('AI22','schedule_seven_days_mock',subject)

    def webhook(self, body, signature):
        verify_line_signature(body,signature,self._secret)
        data=json.loads(body)
        if not isinstance(data,dict) or data.get('destination')!=self._destination or not isinstance(data.get('events'),list) or len(data['events'])>50:
            raise ValueError('Invalid webhook envelope')
        def op():
            # Rate limiting only after authenticating the webhook; no IPs retained.
            bucket=now().strftime('%Y%m%d%H%M')
            count=self.db.execute('SELECT count FROM ix_limits WHERE bucket=?',(bucket,)).fetchone()
            if count and count[0]>=60:
                raise RequestLimitError('Request limit exceeded')
            self.db.execute('INSERT INTO ix_limits VALUES (?,1) ON CONFLICT(bucket) DO UPDATE SET count=count+1',(bucket,))
            self.db.execute('DELETE FROM ix_limits WHERE bucket<?',((now()-timedelta(hours=1)).strftime('%Y%m%d%H%M'),))
            processed=duplicates=0
            for event in data['events']:
                if not isinstance(event,dict):
                    raise ValueError('Invalid event')
                event_id=event.get('webhookEventId')
                source=event.get('source',{})
                if not isinstance(source,dict) or source.get('type')!='user':
                    continue  # Do not enroll groups or rooms.
                raw=source.get('userId')
                if not isinstance(event_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',event_id) or not isinstance(raw,str) or not re.fullmatch(r'U[0-9a-f]{32}',raw):
                    raise ValueError('Invalid event identity')
                # deliveryContext/isRedelivery and replyToken must not change dedupe identity.
                fingerprint=hmac.new(self._key,encode({k:event.get(k) for k in ('type','source','timestamp','message')}).encode(),hashlib.sha256).hexdigest()
                old=self.db.execute('SELECT fingerprint FROM ix_webhooks WHERE event_id=?',(event_id,)).fetchone()
                if old:
                    if old[0]!=fingerprint:
                        raise ValueError('Event ID conflict')
                    duplicates+=1
                    continue
                subject=self.subject_ref(raw)
                kind=event.get('type')
                self.db.execute('INSERT OR IGNORE INTO ix_customers VALUES (?,?,?,?,?,?,?,?)',
                    (subject,0,0,0,'new',0,now().isoformat(),(now()+timedelta(days=90)).isoformat()))
                timestamp=event.get('timestamp')
                if type(timestamp) is not int or timestamp<0 or timestamp>int((now()+timedelta(minutes=5)).timestamp()*1000):
                    raise ValueError('Invalid event timestamp')
                clock=self.db.execute('SELECT timestamp FROM ix_subject_clock WHERE subject=?',(subject,)).fetchone()
                if clock and timestamp<=clock[0]:
                    # Conservatively skip out-of-order consent/follow, while stop is always applied.
                    stop_event=kind=='unfollow' or (kind=='message' and event.get('message',{}).get('text','') in {'停止','撤回:健康','撤回:美容','撤回:事業紹介'})
                    if not stop_event:
                        self.db.execute('INSERT INTO ix_webhooks VALUES (?,?,?,?,?)',(event_id,fingerprint,'stale_ignored',subject,now().isoformat()))
                        self._audit('stale_event_ignored',event_id)
                        continue
                self.db.execute('INSERT INTO ix_subject_clock VALUES (?,?) ON CONFLICT(subject) DO UPDATE SET timestamp=MAX(timestamp,excluded.timestamp)',(subject,timestamp))
                if kind=='follow':
                    self.db.execute("UPDATE ix_customers SET friend=1,state='friend' WHERE subject=?",(subject,))
                    self._role('AI21','follow_without_consent',subject)
                    self._operation('follow:'+event_id,'line',subject,None,{'text':'情報提供は任意です。目的（健康・美容・事業紹介）、利用目的、停止方法をご確認のうえ、希望する場合のみ「同意:目的」を選択してください。これはモック用同意案内の返信候補です。'})
                elif kind=='unfollow':
                    self.db.execute('UPDATE ix_customers SET friend=0,stopped=1,revision=revision+1 WHERE subject=?',(subject,))
                    self._pause(subject,'unfollow')
                elif kind=='message':
                    message=event.get('message',{})
                    text=message.get('text','') if isinstance(message,dict) and message.get('type')=='text' else ''
                    if text=='停止':
                        self.db.execute("UPDATE ix_customers SET stopped=1,revision=revision+1,state='stopped' WHERE subject=?",(subject,))
                        for purpose in PURPOSES:
                            self._consent_change(subject,purpose,False,event_id)
                        self._pause(subject,'withdrawal')
                    elif text.startswith('同意:') or text.startswith('再同意:'):
                        purpose=text.split(':',1)[1]
                        if purpose in PURPOSES:
                            # Only synthetic mock-notice consent, not evidence of production notice display.
                            self._consent_change(subject,purpose,True,event_id)
                            if text.startswith('再同意:'):
                                self.db.execute('UPDATE ix_customers SET stopped=0 WHERE subject=?',(subject,))
                            self._steps(subject,purpose)
                            self._operation('consent-reply:'+event_id,'line',subject,purpose,{'text':'ご希望の情報について同意を記録しました。この返信は代表確認待ちのモック候補です。停止はいつでも申し出られます。'})
                    elif text.startswith('撤回:'):
                        purpose=text.split(':',1)[1]
                        if purpose in PURPOSES:
                            self._consent_change(subject,purpose,False,event_id)
                    else:
                        category='面談希望' if text=='面談希望' else '問い合わせ'
                        inquiry=uuid4().hex
                        self.db.execute('INSERT INTO ix_inquiries VALUES (?,?,?,?,?)',(inquiry,subject,category,'open',now().isoformat()))
                        self.db.execute("UPDATE ix_customers SET state=? WHERE subject=?",(category,subject))
                        self._role('AI18' if category=='面談希望' else 'AI21','reply_candidate',subject)
                        self._operation('reply:'+event_id,'line',subject,'事業紹介',{'text':'ご希望をお寄せいただきありがとうございます。面談や情報提供は任意です。代表確認後に候補をご案内します。'})
                self.db.execute('INSERT INTO ix_webhooks VALUES (?,?,?,?,?)',(event_id,fingerprint,str(kind),subject,now().isoformat()))
                self._audit('webhook_processed',event_id)
                processed+=1
            return {'processed':processed,'duplicates':duplicates,'mode':'mock','external_send':False}
        return self.store._transaction(op)

    def _operation(self,key,kind,subject,purpose,payload):
        if kind not in {'line','sns','booking_confirm','booking_cancel'}:
            raise ValueError('Unsupported operation')
        fingerprint=digest([kind,subject,purpose,payload])
        old=self.db.execute('SELECT * FROM ix_operations WHERE dedupe_key=?',(key,)).fetchone()
        if old:
            if old['fingerprint']!=fingerprint:
                raise ValueError('Operation key conflict')
            return old['id']
        if kind=='sns' and self.db.execute("SELECT 1 FROM ix_operations WHERE kind='sns' AND fingerprint=? AND status NOT IN ('rejected','cancelled','failed')",(fingerprint,)).fetchone():
            raise ValueError('SNS content already queued')
        revision=self._customer(subject)['revision'] if subject else None
        operation=uuid4().hex
        self.db.execute('INSERT INTO ix_operations(id,dedupe_key,fingerprint,kind,subject,purpose,payload,customer_revision,status) VALUES (?,?,?,?,?,?,?,?,?)',
            (operation,key,fingerprint,kind,subject,purpose,encode(payload),revision,'approval_pending'))
        self._audit('operation_proposed',operation)
        self._role('AI01','approval_required',operation)
        return operation

    def add_slot(self,principal,slot,start,end):
        require(principal,True)
        start,end=utc(start),utc(end)
        if start>=end or start<=now().isoformat():
            raise ValueError('Future interval required')
        self.db.execute('INSERT INTO ix_slots VALUES (?,?,?)',(slot,start,end))

    def slots(self,principal):
        require(principal)
        return [dict(r) for r in self.db.execute("SELECT * FROM ix_slots s WHERE start_at>? AND NOT EXISTS(SELECT 1 FROM ix_bookings b JOIN ix_slots bs ON bs.id=b.slot_id WHERE b.status IN ('held','confirmed') AND bs.start_at<s.end_at AND bs.end_at>s.start_at) ORDER BY start_at",(now().isoformat(),))]

    def request_booking(self,principal,key,subject,slot,replacement_for=None):
        require(principal,True)
        def op():
            self._customer(subject)
            old=self.db.execute('SELECT * FROM ix_bookings WHERE request_key=?',(key,)).fetchone()
            if old:
                if (old['subject'],old['slot_id'],old['replacement_for'])!=(subject,slot,replacement_for):
                    raise ValueError('Booking key conflict')
                operation=self.db.execute("SELECT id FROM ix_operations WHERE dedupe_key=?",('booking:'+old['id'],)).fetchone()
                return {'booking_id':old['id'],'operation_id':operation[0]}
            if not self.db.execute('SELECT 1 FROM ix_slots WHERE id=?',(slot,)).fetchone():
                raise ValueError('Unknown slot')
            if replacement_for:
                prior=self.db.execute("SELECT 1 FROM ix_bookings WHERE id=? AND subject=? AND status='confirmed'",(replacement_for,subject)).fetchone()
                if not prior:
                    raise ValueError('Confirmed original booking required')
            booking=uuid4().hex
            self.db.execute('INSERT INTO ix_bookings VALUES (?,?,?,?,?,?,?)',(booking,key,subject,slot,'requested',replacement_for,now().isoformat()))
            operation=self._operation('booking:'+booking,'booking_confirm',subject,None,{'booking_id':booking})
            self.db.execute("UPDATE ix_customers SET state='面談希望' WHERE subject=?",(subject,))
            self._role('AI23','booking_requested',booking)
            self._role('AI14','consultation_handoff',booking)
            return {'booking_id':booking,'operation_id':operation}
        return self.store._transaction(op)

    def cancel_booking(self,principal,booking):
        require(principal,True)
        def op():
            row=self.db.execute("SELECT * FROM ix_bookings WHERE id=? AND status='confirmed'",(booking,)).fetchone()
            if not row:
                raise ValueError('Confirmed booking required')
            return self._operation('cancel:'+booking,'booking_cancel',row['subject'],None,{'booking_id':booking})
        return self.store._transaction(op)

    def propose_sns(self,principal,key,content_id,assets=None):
        require(principal,True)
        def op():
            row=self.content._latest(content_id)
            if row['kind']!='sns':
                raise ValueError('SNS content required')
            if assets and any(not isinstance(a,str) or not re.fullmatch(r'asset_[A-Za-z0-9_-]{1,80}',a) for a in assets):
                raise ValueError('Use local approved asset references, not credentials or URLs')
            return self._operation(key,'sns',None,None,{'content_id':content_id,'digest':row['digest'],'assets':assets or [],'scheduled_date':row['scheduled_date']})
        return self.store._transaction(op)

    def _gate(self,row):
        if row['kind']=='line':
            if row['purpose'] not in PURPOSES or not self.eligible(row['subject'],row['purpose']) or self._customer(row['subject'])['revision']!=row['customer_revision']:
                raise ValueError('Consent or delivery state changed')
        elif row['kind']=='sns':
            payload=json.loads(row['payload'])
            content=self.content._latest(payload['content_id'])
            if content['status']!='approved' or content['digest']!=payload['digest'] or digest(json.loads(content['body']))!=payload['digest']:
                raise ValueError('Current approved content required')
            if json.loads(content['body']).get('platform')=='Instagram' and not payload['assets']:
                raise ValueError('Instagram requires an approved media reference')
        elif row['kind']=='booking_confirm':
            payload=json.loads(row['payload'])
            booking=self.db.execute('SELECT * FROM ix_bookings WHERE id=?',(payload['booking_id'],)).fetchone()
            if not booking or booking['status'] not in ('requested','held'):
                raise ValueError('Booking unavailable')
            slot=self.db.execute('SELECT * FROM ix_slots WHERE id=?',(booking['slot_id'],)).fetchone()
            if slot['start_at']<=now().isoformat():
                raise ValueError('Slot expired')
            overlap=self.db.execute("SELECT 1 FROM ix_bookings b JOIN ix_slots s ON s.id=b.slot_id WHERE b.id!=? AND b.id!=? AND b.status IN ('held','confirmed') AND s.start_at<? AND s.end_at>? LIMIT 1",
                (booking['id'],booking['replacement_for'] or '',slot['end_at'],slot['start_at'])).fetchone()
            if overlap:
                raise ValueError('Slot already reserved')
        elif row['kind']=='booking_cancel':
            booking=json.loads(row['payload'])['booking_id']
            if not self.db.execute("SELECT 1 FROM ix_bookings WHERE id=? AND status='confirmed'",(booking,)).fetchone():
                raise ValueError('Booking no longer confirmed')

    def decide(self,principal,operation,decision):
        require(principal,True)
        if decision not in ('approve','reject'):
            raise ValueError('Invalid decision')
        def op():
            row=self.db.execute('SELECT * FROM ix_operations WHERE id=?',(operation,)).fetchone()
            if not row or row['status']!='approval_pending':
                raise ValueError('Operation not awaiting approval')
            if decision=='approve':
                self._gate(row)
                if row['kind']=='booking_confirm':
                    booking=json.loads(row['payload'])['booking_id']
                    self.db.execute("UPDATE ix_bookings SET status='held' WHERE id=?",(booking,))
            if decision=='reject' and row['kind']=='booking_confirm':
                self.db.execute("UPDATE ix_bookings SET status='rejected' WHERE id=?",(json.loads(row['payload'])['booking_id'],))
            self.db.execute('INSERT INTO ix_approvals(operation_id,decision,payload_digest,actor,created_at) VALUES (?,?,?,?,?)',
                (operation,decision,digest(json.loads(row['payload'])),principal.role,now().isoformat()))
            self.db.execute('UPDATE ix_operations SET status=?,approved_digest=? WHERE id=?',
                ('approved' if decision=='approve' else 'rejected',digest(json.loads(row['payload'])) if decision=='approve' else None,operation))
            self._role('AI28','approval_gate_recorded',operation)
            self._audit('representative:'+decision,operation,principal.role)
        return self.store._transaction(op)

    def dispatch_mock(self,principal,operation):
        require(principal,True)
        # Only built-in in-memory adapter is called under the transaction. No IO.
        def op():
            row=self.db.execute('SELECT * FROM ix_operations WHERE id=?',(operation,)).fetchone()
            if not row or row['status'] not in ('approved','retry_wait') or row['attempts']>=3:
                raise ValueError('Operation unavailable')
            if row['next_attempt'] and row['next_attempt']>now().isoformat():
                raise ValueError('Backoff not elapsed')
            if row['approved_digest']!=digest(json.loads(row['payload'])):
                raise ValueError('Approved payload changed')
            self._gate(row)
            attempt=uuid4().hex
            deadline=(now()+timedelta(seconds=5)).isoformat()
            self.db.execute('INSERT INTO ix_attempts VALUES (?,?,?,?,?,?,?)',(attempt,operation,'running',None,'mock',deadline,now().isoformat()))
            self.db.execute("UPDATE ix_operations SET status='running',attempts=attempts+1 WHERE id=?",(operation,))
            try:
                result=self.adapter.execute(row['kind'],json.loads(row['payload']),row['id'])
                outcome,code=result.outcome,result.code
            except Exception:
                outcome,code='unknown','mock_result_unknown'
            status={'success':'mock_succeeded','unknown':'manual_reconciliation','permanent':'failed','retryable':'retry_wait'}[outcome]
            if status=='retry_wait' and row['attempts']+1>=3:
                status='failed'
            self.db.execute('UPDATE ix_attempts SET outcome=?,code=? WHERE id=?',(outcome,code,attempt))
            self.db.execute('UPDATE ix_operations SET status=?,next_attempt=? WHERE id=?',
                (status,(now()+timedelta(seconds=2**(row['attempts']+1))).isoformat() if status=='retry_wait' else None,operation))
            if outcome=='success' and row['kind']=='booking_confirm':
                booking=json.loads(row['payload'])['booking_id']
                prior=self.db.execute('SELECT replacement_for FROM ix_bookings WHERE id=?',(booking,)).fetchone()[0]
                if prior:
                    self.db.execute("UPDATE ix_bookings SET status='cancelled' WHERE id=?",(prior,))
                self.db.execute("UPDATE ix_bookings SET status='confirmed' WHERE id=?",(booking,))
                self.db.execute("UPDATE ix_customers SET booked=1,revision=revision+1,state='予約済み' WHERE subject=?",(row['subject'],))
                self._pause(row['subject'],'booking_confirmed_mock')
            elif outcome=='success' and row['kind']=='booking_cancel':
                self.db.execute("UPDATE ix_bookings SET status='cancelled' WHERE id=?",(json.loads(row['payload'])['booking_id'],))
                # Cancellation never silently resumes step delivery.
                self._audit('cancelled_no_automatic_resume',row['subject'])
            if status=='manual_reconciliation' and row['kind']=='booking_confirm':
                self._pause(row['subject'],'booking_result_unknown')
            if status=='failed' and row['kind']=='booking_confirm':
                self.db.execute("UPDATE ix_bookings SET status='failed' WHERE id=?",(json.loads(row['payload'])['booking_id'],))
            self._role('AI26','mock_adapter_attempt',operation)
            self._audit(status,operation)
            return {'status':status,'mode':'mock','actual_external_calls':0,'actual_cost_microusd':0}
        return self.store._transaction(op)

    def dashboard_data(self,principal):
        require(principal)
        def rows(query):
            return [dict(r) for r in self.db.execute(query)]
        return {'mode':'mock','connections':{'LINE':'未接続','既存LINE7日間配信':'未接続・契約のみ','予約':'未選定・モック','Instagram':'未接続','Facebook':'未接続','CRM':'ローカル仮名データのみ'},
            'customers':rows('SELECT subject,friend,stopped,booked,state FROM ix_customers'),
            'consents':rows('SELECT subject,purpose,granted,notice_version,created_at FROM ix_consents'),
            'inquiries':rows('SELECT * FROM ix_inquiries'), 'bookings':rows('SELECT * FROM ix_bookings'),
            'steps':rows('SELECT * FROM ix_steps'),'operations':rows('SELECT id,kind,status,attempts,mode FROM ix_operations'),
            'errors':rows("SELECT operation_id,outcome,code,mode FROM ix_attempts WHERE outcome!='success'"),
            'agents':rows('SELECT agent,action,ref,mode,created_at FROM ix_roles'),
            'cost':{'actual_microusd':0,'network_transport':'未実装','paid_calls_enabled':False}}

    def dashboard_html(self,principal):
        data=self.dashboard_data(principal)
        return '<!doctype html><html lang="ja"><meta charset="utf-8"><title>バイオレット外部連携管理</title><h1>外部連携管理（モック）</h1><p>実サービス未接続。公開サーバーなし。操作は認証付きローカルAPI。</p>'+''.join('<h2>'+html.escape(k)+'</h2><pre>'+html.escape(json.dumps(v,ensure_ascii=False,indent=2))+'</pre>' for k,v in data.items())+'</html>'

    def export_customer(self,principal,subject):
        require(principal,True)
        self._customer(subject)
        self._audit('customer_export',subject,principal.role)
        return {'customer':dict(self._customer(subject)), 'consents':[dict(r) for r in self.db.execute('SELECT purpose,granted,notice_version,created_at FROM ix_consents WHERE subject=?',(subject,))],
            'bookings':[dict(r) for r in self.db.execute('SELECT id,slot_id,status FROM ix_bookings WHERE subject=?',(subject,))]}

    def erasure_plan(self,principal,subject):
        """Reviewable plan only: never deletes existing data automatically."""
        require(principal,True)
        customer=self._customer(subject)
        counts={}
        for table,column in [('ix_consents','subject'),('ix_webhooks','subject'),('ix_inquiries','subject'),('ix_steps','subject'),('ix_bookings','subject'),('ix_operations','subject')]:
            counts[table]=self.db.execute(f'SELECT COUNT(*) FROM {table} WHERE {column}=?',(subject,)).fetchone()[0]
        self._audit('erasure_plan_created',subject,principal.role)
        return {'subject':subject,'expires_at':customer['expires_at'],'counts':counts,
            'executed':False,'requires_representative_approval':True,
            'review':['予約・結果不明処理の照合','監査・重複防止用最小記録の保持方針','バックアップの削除方針','法令上の保持義務','既存LINE側との本人同意に基づく調整']}
