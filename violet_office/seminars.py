"""Independent seminar domain. Local identities, explicit consent, mock-only delivery.

No LINE identifiers, customer conversations, raw card numbers or external SDKs.
All representative actions require an authenticated representative at the web boundary.
"""
import json
import hashlib
import hmac
import os
import re
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from uuid import uuid4
from .store import now
from .content import encode,digest
from .learning import risky
from .integrations.adapters import require

JST=ZoneInfo('Asia/Tokyo')
SEGMENTS={'dealer':'ディーラー','subdealer':'サブディーラー','user':'ユーザー','cast':'キャスト（契約前）'}
AUDIENCES={'dealer':'ディーラー向け','user':'ユーザー向け','public':'一般公開向け'}
ANSWERS={'yes':'参加する','no':'参加しない','maybe':'検討中','unanswered':'未回答'}
ACCESS={'dealer':{'dealer','user','public'},'subdealer':{'dealer','user','public'},'user':{'user','public'},'cast':{'public'}}
BUSINESS_COPY='ベネシードディーラー事業\n\n健康・美容に関する商品やサービスの提供をはじめ、さまざまな事業活動を通じて、お客様の暮らしや目標をサポートします。\n\nセミナーや体験会では、それぞれの目的に合った情報をご案内します。'


def bounded(value,limit):
    if not isinstance(value,str) or not value.strip() or len(value)>limit or any(ord(c)<32 and c not in '\n\t' for c in value):
        raise ValueError('Bounded text required')
    return value.strip()


def utc(value):
    parsed=datetime.fromisoformat(value)
    if parsed.tzinfo is None: raise ValueError('Timezone required')
    return parsed.astimezone(timezone.utc).isoformat()


class Seminars:
    def __init__(self,store):
        self.store,self.db=store,store.db

    def event(self,actor,event,ref,detail=None):
        self.db.execute('INSERT INTO seminar_events(actor,event,ref,detail,created_at) VALUES(?,?,?,?,?)',(actor,event,ref,encode(detail or {}),now().isoformat()))

    def account(self,principal,username,name,evidence,attested):
        require(principal,True)
        if not attested: raise ValueError('Manual identity verification required')
        user=self.db.execute('SELECT role FROM office_users WHERE username=?',(username,)).fetchone()
        if not user or user[0]!='participant': raise ValueError('Participant account required')
        # Verification reference must not contain a copy of identity documents.
        self.db.execute('INSERT INTO seminar_accounts VALUES(?,?,?,?,?)',(username,bounded(name,100),bounded(evidence,120),principal.identity or 'representative',now().isoformat()))
        self.event(principal.identity or 'representative','identity_verified',username)

    def member(self,username,approved=False):
        user=self.db.execute('SELECT a.username FROM seminar_accounts a JOIN office_users u ON a.username=u.username WHERE a.username=? AND u.role=?',(username,'participant')).fetchone()
        if not user: raise PermissionError('Verified participant required')
        reg=self.db.execute('SELECT * FROM seminar_registrations WHERE username=?',(username,)).fetchone()
        if approved and (not reg or reg['status']!='approved'): raise PermissionError('Initial representative approval required')
        return reg

    def register(self,username,segment,card):
        def op():
            reg=self.member(username)
            if segment not in SEGMENTS: raise ValueError('Known segment required')
            if reg: raise ValueError('Already registered; representative must review changes')
            card_hash=suffix=None
            if segment=='cast':
                if card: raise ValueError('Card not required for cast')
            else:
                normalized=re.sub(r'[ -]','',card).upper()
                if not re.fullmatch(r'[A-Z0-9]{4,64}',normalized): raise ValueError('Card format needs human verification')
                key=os.environ.get('SEMINAR_CARD_HMAC_KEY','')
                if len(key)<32: raise ValueError('Secure card hash key required')
                fingerprint=hmac.new(key.encode(),b'violet-seminar-key-v1',hashlib.sha256).hexdigest()
                existing=self.db.execute('SELECT fingerprint FROM seminar_key_ids WHERE id=1').fetchone()
                if existing and not hmac.compare_digest(existing[0],fingerprint): raise ValueError('Key rotation requires migration')
                self.db.execute('INSERT OR IGNORE INTO seminar_key_ids VALUES(1,?)',(fingerprint,))
                card_hash=hmac.new(key.encode(),normalized.encode(),hashlib.sha256).hexdigest()
                suffix=normalized[-4:]
                if self.db.execute('SELECT 1 FROM seminar_registrations WHERE card_hash=?',(card_hash,)).fetchone(): raise ValueError('Registration cannot be accepted; contact representative')
            self.db.execute('INSERT INTO seminar_registrations VALUES(?,?,?,?,?,?,?)',(username,segment,card_hash,suffix,'approval_pending',1,now().isoformat()))
            self.event(username,'initial_registration',username,{'segment':segment})
        return self.store._transaction(op)

    def decide_registration(self,principal,username,decision):
        require(principal,True)
        if decision not in ('approve','reject','revoke'): raise ValueError('Decision required')
        def op():
            row=self.db.execute('SELECT * FROM seminar_registrations WHERE username=?',(username,)).fetchone()
            if not row: raise ValueError('Unknown registration')
            status={'approve':'approved','reject':'rejected','revoke':'revoked'}[decision]
            self.db.execute('UPDATE seminar_registrations SET status=?,revision=revision+1 WHERE username=?',(status,username))
            self.db.execute("UPDATE seminar_reminders SET status='cancelled' WHERE username=? AND status IN ('approval_pending','approved','retry_wait')",(username,))
            self.event(principal.identity or 'representative','registration_'+decision,username)
        return self.store._transaction(op)

    def save(self,principal,data,identifier=None,revision=None):
        require(principal,True)
        title=bounded(data['title'],160); location=bounded(data['location'],1000); description=bounded(data['description'],4000)
        start,end,deadline=utc(data['start_at']),utc(data['end_at']),utc(data['deadline'])
        if not now().isoformat()<start<end or deadline>start: raise ValueError('Future interval and application deadline required')
        fee=int(data['fee_yen']); capacity=int(data['capacity']) if data.get('capacity') not in (None,'') else None
        if fee<0 or fee>10000000 or (capacity is not None and not 1<=capacity<=100000): raise ValueError('Fee/capacity out of bounds')
        if data['audience'] not in AUDIENCES: raise ValueError('Known audience required')
        def op():
            seminar=identifier or uuid4().hex
            if identifier:
                old=self.get(identifier)
                if old['revision']!=int(revision) or old['status']!='open': raise ValueError('Current open revision required')
                attending=self.db.execute("SELECT COUNT(*) FROM seminar_responses WHERE seminar_id=? AND answer='yes'",(identifier,)).fetchone()[0]
                if capacity is not None and attending>capacity: raise ValueError('Capacity below existing responses')
                self.db.execute('UPDATE seminars SET title=?,start_at=?,end_at=?,location=?,fee_yen=?,description=?,deadline=?,audience=?,capacity=?,revision=revision+1 WHERE id=?',(title,start,end,location,fee,description,deadline,data['audience'],capacity,seminar))
                self.db.execute("UPDATE seminar_reminders SET status='cancelled' WHERE seminar_id=? AND status IN ('approval_pending','approved','retry_wait')",(seminar,))
            else:
                self.db.execute('INSERT INTO seminars VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(seminar,title,start,end,location,fee,description,deadline,data['audience'],capacity,'open',1,now().isoformat()))
            self.event(principal.identity or 'representative','seminar_saved',seminar,{'revision':1 if not identifier else int(revision)+1})
            return seminar
        return self.store._transaction(op)

    def get(self,identifier):
        row=self.db.execute('SELECT * FROM seminars WHERE id=?',(identifier,)).fetchone()
        if not row: raise ValueError('Unknown seminar')
        return row

    def change_state(self,principal,identifier,status):
        require(principal,True)
        if status not in ('cancelled','closed'): raise ValueError('Only cancellation or closing supported')
        def op():
            self.get(identifier)
            self.db.execute('UPDATE seminars SET status=?,revision=revision+1 WHERE id=?',(status,identifier))
            self.db.execute("UPDATE seminar_reminders SET status='cancelled' WHERE seminar_id=? AND status IN ('approval_pending','approved','retry_wait')",(identifier,))
            self.event(principal.identity or 'representative','seminar_'+status,identifier)
        return self.store._transaction(op)

    def visible(self,username,identifier):
        reg=self.member(username,True)
        seminar=self.get(identifier)
        from .seminar_support import target_allowed
        if seminar['audience'] not in ACCESS[reg['segment']] or not target_allowed(self.db,identifier,reg['segment']): raise PermissionError('Audience denied')
        return seminar

    def listing(self,username):
        reg=self.member(username,True)
        from .seminar_support import target_allowed
        return [dict(r) for r in self.db.execute('SELECT * FROM seminars ORDER BY start_at LIMIT 500') if r['audience'] in ACCESS[reg['segment']] and target_allowed(self.db,r['id'],reg['segment'])]

    def respond(self,username,identifier,answer):
        if answer not in {'yes','no','maybe'}: raise ValueError('Explicit response required')
        def op():
            seminar=self.visible(username,identifier)
            old=self.db.execute('SELECT * FROM seminar_responses WHERE seminar_id=? AND username=?',(identifier,username)).fetchone()
            if seminar['status']!='open' or now().isoformat()>seminar['deadline']: raise ValueError('Registration closed')
            if old and old['answer']==answer: return
            count=self.db.execute("SELECT COUNT(*) FROM seminar_responses WHERE seminar_id=? AND answer='yes'",(identifier,)).fetchone()[0]
            if answer=='yes' and seminar['capacity'] is not None and count>=seminar['capacity']: raise ValueError('Capacity reached')
            revision=old['revision']+1 if old else 1
            self.db.execute('INSERT INTO seminar_responses VALUES(?,?,?,?,?) ON CONFLICT(seminar_id,username) DO UPDATE SET answer=excluded.answer,answered_at=excluded.answered_at,revision=excluded.revision',(identifier,username,answer,now().isoformat(),revision))
            self.db.execute("UPDATE seminar_reminders SET status='cancelled' WHERE seminar_id=? AND username=? AND status IN ('approval_pending','approved','retry_wait')",(identifier,username))
            self.event(username,'response_changed',identifier,{'before':old['answer'] if old else 'unanswered','after':answer,'revision':revision})
        return self.store._transaction(op)

    def withdraw(self,username,identifier):
        """Explicit participant cancellation even after deadline; never creates a booking."""
        self.member(username)
        def op():
            old=self.db.execute('SELECT * FROM seminar_responses WHERE seminar_id=? AND username=?',(identifier,username)).fetchone()
            if not old: raise ValueError('Existing response required')
            if old['answer']=='no': return
            self.db.execute("UPDATE seminar_responses SET answer='no',answered_at=?,revision=revision+1 WHERE seminar_id=? AND username=?",(now().isoformat(),identifier,username))
            self.db.execute("UPDATE seminar_reminders SET status='cancelled' WHERE seminar_id=? AND username=? AND status IN ('approval_pending','approved','retry_wait')",(identifier,username))
            self.event(username,'response_changed',identifier,{'before':old['answer'],'after':'no','revision':old['revision']+1,'reason':'explicit_cancellation'})
        self.store._transaction(op)

    def consent(self,username,granted):
        self.member(username)
        if type(granted) is not bool: raise ValueError('Explicit consent required')
        def op():
            self.db.execute('INSERT INTO seminar_consents(username,granted,notice_version,created_at) VALUES(?,?,?,?)',(username,int(granted),'seminar-reminder-mock-v1',now().isoformat()))
            self.db.execute("UPDATE seminar_reminders SET status='cancelled' WHERE username=? AND status IN ('approval_pending','approved','retry_wait')",(username,))
            self.event(username,'reminder_consent_granted' if granted else 'reminder_stopped',username)
        return self.store._transaction(op)

    def latest_consent(self,username):
        return self.db.execute('SELECT * FROM seminar_consents WHERE username=? ORDER BY id DESC LIMIT 1',(username,)).fetchone()

    def recipients(self,principal,identifier,segment='',answer=''):
        require(principal,True)
        seminar=self.get(identifier)
        if segment and segment not in SEGMENTS or answer and answer not in ANSWERS: raise ValueError('Known filters required')
        rows=self.db.execute('SELECT r.*,a.display_name,x.answer,x.answered_at,x.revision AS response_revision FROM seminar_registrations r JOIN seminar_accounts a USING(username) LEFT JOIN seminar_responses x ON x.username=r.username AND x.seminar_id=? WHERE r.status=? ORDER BY a.display_name',(identifier,'approved')).fetchall()
        result=[]
        for row in rows:
            item=dict(row); item['answer']=item['answer'] or 'unanswered'
            from .seminar_support import target_allowed
            if seminar['audience'] in ACCESS[row['segment']] and target_allowed(self.db,identifier,row['segment']) and (not segment or row['segment']==segment) and (not answer or item['answer']==answer):
                result.append(item)
        return result

    def schedule(self,principal,enabled):
        require(principal,True)
        self.db.execute('UPDATE seminar_scheduler SET enabled=?,state=? WHERE id=1',(int(bool(enabled)),'enabled' if enabled else 'paused'))
        self.event(principal.identity or 'representative','scheduler_enabled' if enabled else 'scheduler_paused','scheduler')

    def tick(self,principal):
        require(principal,True)
        def op():
            prepared=0
            day=now().astimezone(JST).date()
            enabled=self.db.execute('SELECT enabled FROM seminar_scheduler WHERE id=1').fetchone()[0]
            if not enabled: return 0
            due_day=day+timedelta(days=3)
            lower=datetime.combine(due_day,datetime.min.time(),JST).astimezone(timezone.utc).isoformat()
            upper=datetime.combine(due_day+timedelta(days=1),datetime.min.time(),JST).astimezone(timezone.utc).isoformat()
            for seminar in self.db.execute("SELECT * FROM seminars WHERE status IN ('open','closed') AND start_at>=? AND start_at<? ORDER BY start_at LIMIT 200",(lower,upper)):
                if datetime.fromisoformat(seminar['start_at']).astimezone(JST).date()-timedelta(days=3)!=day: continue
                for recipient in self.recipients(principal,seminar['id']):
                    if prepared>=500: break
                    consent=self.latest_consent(recipient['username'])
                    from .seminar_support import legacy_reminder_allowed
                    if recipient['answer']=='no' or not consent or not consent['granted'] or not legacy_reminder_allowed(self.db,seminar['id'],recipient['username']): continue
                    if self.db.execute('SELECT 1 FROM seminar_reminders WHERE seminar_id=? AND username=?',(seminar['id'],recipient['username'])).fetchone(): continue
                    text=('参加予定のリマインドです。' if recipient['answer']=='yes' else 'ご希望の場合のみ参加可否をお知らせください。')
                    body=seminar['title']+'\n'+seminar['start_at']+' 〜 '+seminar['end_at']+'\n'+seminar['location']+'\n'+seminar['description']+'\n参加費 '+str(seminar['fee_yen'])+'円\n'+text
                    identifier=uuid4().hex
                    self.db.execute('INSERT INTO seminar_reminders VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(identifier,seminar['id'],recipient['username'],seminar['revision'],recipient['revision'],recipient['response_revision'] or 0,consent['id'],body,digest(body),'approval_pending',0,None))
                    self.event('AI22','reminder_prepared',identifier,{'mode':'mock','seminar_revision':seminar['revision']})
                    prepared+=1
            self.db.execute('INSERT INTO seminar_ticks VALUES(?,?,?,?,?)',(uuid4().hex,day.isoformat(),'prepared_only',prepared,now().isoformat()))
            return prepared
        return self.store._transaction(op)

    def _current(self,row):
        seminar=self.visible(row['username'],row['seminar_id'])
        reg=self.member(row['username'],True)
        response=self.db.execute('SELECT * FROM seminar_responses WHERE seminar_id=? AND username=?',(row['seminar_id'],row['username'])).fetchone()
        consent=self.latest_consent(row['username'])
        day=now().astimezone(JST).date()
        from .seminar_support import legacy_reminder_allowed
        return bool(legacy_reminder_allowed(self.db,row['seminar_id'],row['username']) and seminar['status'] in ('open','closed') and seminar['revision']==row['seminar_revision'] and reg['revision']==row['registration_revision'] and (response['revision'] if response else 0)==row['response_revision'] and (not response or response['answer']!='no') and consent and consent['granted'] and consent['id']==row['consent_id'] and digest(row['body'])==row['digest'] and datetime.fromisoformat(seminar['start_at']).astimezone(JST).date()-timedelta(days=3)==day)

    def reprepare(self,principal,identifier):
        """Explicit regeneration only when no delivery attempt ever occurred."""
        require(principal,True)
        def op():
            old=self.db.execute('SELECT * FROM seminar_reminders WHERE id=?',(identifier,)).fetchone()
            if not old or old['status'] not in ('cancelled','rejected') or old['attempts']:
                raise ValueError('Only unattempted cancelled/rejected drafts can be regenerated')
            seminar=self.visible(old['username'],old['seminar_id'])
            reg=self.member(old['username'],True)
            response=self.db.execute('SELECT * FROM seminar_responses WHERE seminar_id=? AND username=?',(old['seminar_id'],old['username'])).fetchone()
            consent=self.latest_consent(old['username'])
            if seminar['status']=='cancelled' or not consent or not consent['granted'] or response and response['answer']=='no':
                raise ValueError('Current consent and eligible response required')
            if datetime.fromisoformat(seminar['start_at']).astimezone(JST).date()-timedelta(days=3)!=now().astimezone(JST).date():
                raise ValueError('Only the three-day preparation date')
            text='参加予定のリマインドです。' if response and response['answer']=='yes' else 'ご希望の場合のみ参加可否をお知らせください。'
            body=seminar['title']+'\n'+seminar['start_at']+' 〜 '+seminar['end_at']+'\n'+seminar['location']+'\n'+seminar['description']+'\n参加費 '+str(seminar['fee_yen'])+'円\n'+text
            self.db.execute('INSERT INTO seminar_reminder_history VALUES(?,?,?,?)',(uuid4().hex,identifier,encode(dict(old)),now().isoformat()))
            self.db.execute("UPDATE seminar_reminders SET seminar_revision=?,registration_revision=?,response_revision=?,consent_id=?,body=?,digest=?,status='approval_pending',next_at=NULL WHERE id=?",(seminar['revision'],reg['revision'],response['revision'] if response else 0,consent['id'],body,digest(body),identifier))
            self.event(principal.identity or 'representative','reminder_regenerated',identifier,{'prior_digest':old['digest'],'new_digest':digest(body)})
        return self.store._transaction(op)

    def approve_reminder(self,principal,identifier,decision):
        require(principal,True)
        if decision not in ('approve','reject'): raise ValueError('Decision required')
        def op():
            row=self.db.execute('SELECT * FROM seminar_reminders WHERE id=?',(identifier,)).fetchone()
            if not row or row['status']!='approval_pending' or not self._current(row) or risky(row['body']): raise ValueError('Current eligible reminder required')
            self.db.execute('UPDATE seminar_reminders SET status=? WHERE id=?',('approved' if decision=='approve' else 'rejected',identifier))
            self.event(principal.identity or 'representative','reminder_'+decision,identifier,{'digest':row['digest']})
        return self.store._transaction(op)

    def dispatch_mock(self,principal,identifier,outcome='success'):
        require(principal,True)
        if outcome not in ('success','transient','timeout','permanent'): raise ValueError('Mock outcome required')
        def op():
            row=self.db.execute('SELECT * FROM seminar_reminders WHERE id=?',(identifier,)).fetchone()
            if not row or row['status'] not in ('approved','retry_wait'): raise ValueError('Approved reminder required')
            if row['attempts']>=3 or row['next_at'] and row['next_at']>now().isoformat(): raise ValueError('Bounded retry not due')
            try: eligible=self._current(row)
            except (ValueError,PermissionError): eligible=False
            approved=self.db.execute("SELECT detail FROM seminar_events WHERE event='reminder_approve' AND ref=? ORDER BY id DESC LIMIT 1",(identifier,)).fetchone()
            eligible=eligible and bool(approved and json.loads(approved[0]).get('digest')==row['digest'])
            if not eligible:
                self.db.execute("UPDATE seminar_reminders SET status='cancelled' WHERE id=?",(identifier,))
                return 'cancelled'
            attempts=row['attempts']+1
            status='mock_sent' if outcome=='success' else 'failed' if outcome=='permanent' or attempts>=3 else 'retry_wait'
            self.db.execute('UPDATE seminar_reminders SET status=?,attempts=?,next_at=? WHERE id=?',(status,attempts,(now()+timedelta(seconds=60*attempts)).isoformat() if status=='retry_wait' else None,identifier))
            self.db.execute('INSERT INTO seminar_attempts VALUES(?,?,?,?,?,?,?)',(uuid4().hex,identifier,outcome,'mock_'+outcome,'mock',(now()+timedelta(seconds=5)).isoformat(),now().isoformat()))
            self.event(principal.identity or 'representative','mock_attempt',identifier,{'status':status})
            return status
        return self.store._transaction(op)

    def prepare_work(self,principal,identifier):
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='s14_meta'").fetchone() and self.db.execute('SELECT 1 FROM s14_meta WHERE seminar_id=?',(identifier,)).fetchone(): raise ValueError('Use business-scoped seminar extension tasks')
        require(principal,True)
        def op():
            seminar=self.get(identifier)
            counts={answer:sum(r['answer']==answer for r in self.recipients(principal,identifier)) for answer in ANSWERS}
            input_digest=digest([seminar['revision'],counts])
            results=[]
            for agent,work in [('AI08','セミナー情報整理'),('AI17','対象区分別の案内原稿'),('AI23','参加可否の受付手順'),('AI22','3日前の再案内準備'),('AI27','参加状況の集計'),('AI28','未確認事項の品質監査'),('AI01','統括レビュー用集約')]:
                task=self.store.create_task(f'seminar:{identifier}:{seminar["revision"]}:{input_digest}:{agent}',agent,{'seminar_id':identifier,'revision':seminar['revision'],'work':work,'input_digest':input_digest,'mode':'local_template_no_private_data'},action='customer_contact' if agent in ('AI17','AI22') else 'internal')
                old=self.db.execute('SELECT id FROM artifacts WHERE task_id=?',(task,)).fetchone()
                if not old:
                    content={'work':work,'snapshot_at':now().isoformat(),'input_digest':input_digest,'seminar_revision':seminar['revision'],'counts':counts if agent in ('AI27','AI01') else None,'copy':BUSINESS_COPY+'\n\n'+AUDIENCES[seminar['audience']]+'のご案内：'+seminar['title']+'。ご希望の場合だけ参加可否をご回答ください。' if agent=='AI17' else '公開前に公式資料、対象区分、料金、法令、同意を代表が確認。','quality_blockers':['誇大表現・外部命令の疑い。代表が修正確認'] if risky(seminar['title']+' '+seminar['description']+' '+seminar['location']) else ['事実・法令・公式規約は人による確認が必要'], 'measurement':'参加回答の集計のみ。面談成果は未計測。','status':'代表確認待ち・外部送信なし'}
                    self.store.finish(self.store.claim(task),encode(content))
                self.db.execute('INSERT OR IGNORE INTO seminar_work VALUES(?,?,?,?,?,?)',(uuid4().hex,identifier,seminar['revision'],input_digest,agent,task))
                results.append(task)
            self.event(principal.identity or 'representative','work_prepared',identifier,{'agents':['AI08','AI17','AI23','AI22','AI27','AI28','AI01']})
            return results
        return self.store._transaction(op)
