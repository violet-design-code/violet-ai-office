"""Seminar extension: existing identities/RSVP/attendance, drafts and mock only."""
import json
import re
from datetime import datetime,time,timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from .store import now
from .seminars import JST,ACCESS,SEGMENTS,utc
from .portfolio import text,CANDIDATES
from .content import encode,digest
from .learning import risky

LABELS={'dealer':'ベネシードディーラー','subdealer':'ベネシードサブディーラー','user':'ベネシードユーザー','cast':'キャスト（契約前の見込み客）'}
INTERESTS={'health':'健康','beauty':'美容','work':'働き方','connection':'人とのつながり','other':'その他'}
KINDS={'invite':'個別/一斉案内','deadline':'申込締切前','previous_day':'開催前日','event_day':'開催当日','thanks':'参加後のお礼','question':'質問対応案','interview':'任意の面談提案','absence':'欠席後フォロー'}
PURPOSE={k:'followup' if k in ('thanks','question','interview','absence') else 'reminders' if k in ('deadline','previous_day','event_day') else 'invite' for k in KINDS}
ACTIVITIES={'planning':('AI01','AI07','AI08'),'copy':('AI09','AI16','AI17','AI21'),'reception':('AI20','AI23'),'reminder':('AI13','AI22'),'followup':('AI18','AI24'),'analysis':('AI06','AI27'),'audit':('AI25','AI28'),'integration':('AI26',),'research':('AI02','AI03','AI04','AI05'),'creative':('AI10','AI11','AI12'),'sales':('AI14','AI15','AI19')}


def migrate(store):
    if store.db.execute('SELECT 1 FROM office_migrations WHERE version=14').fetchone(): return
    def op():
        for part in (Path(__file__).resolve().parent.parent/'migrations/014_seminar_support.sql').read_text().split(';'):
            if part.strip(): store.db.execute(part)
        store.db.execute('INSERT OR IGNORE INTO office_migrations VALUES(14,?)',(now().isoformat(),))
    store._transaction(op)


def unsafe_copy(value):
    return risky(value) or bool(re.search(r'収入保証|収入を保証|健康効果を保証|ネットワークビジネスでは(?:ない|ありません)|MLMでは(?:ない|ありません)',value,re.I))


def target_allowed(db,seminar,segment):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='s14_meta'").fetchone(): return True
    meta=db.execute('SELECT segments FROM s14_meta WHERE seminar_id=?',(seminar,)).fetchone()
    return not meta or segment in json.loads(meta[0])


def legacy_reminder_allowed(db,seminar,username):
    """Preserve phase9 behavior except for explicitly adopted extension seminars."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='s14_meta'").fetchone(): return True
    meta=db.execute('SELECT * FROM s14_meta WHERE seminar_id=?',(seminar,)).fetchone()
    if not meta: return True
    if db.execute('SELECT halted FROM portfolio_controls').fetchone()[0] or db.execute('SELECT status FROM portfolio_businesses WHERE id=?',(meta['business_id'],)).fetchone()[0] in ('paused','closed'): return False
    consent=db.execute("SELECT granted FROM s14_consents WHERE seminar_id=? AND username=? AND purpose='reminders' ORDER BY id DESC LIMIT 1",(seminar,username)).fetchone()
    if not consent or not consent[0]: return False
    for d in db.execute('SELECT d.* FROM scm_seminars l JOIN scm_deals d ON d.id=l.deal_id AND d.business_id=l.business_id WHERE l.business_id=? AND l.seminar_id=? AND l.participant_username=?',(meta['business_id'],seminar,username)):
        contact=db.execute('SELECT state FROM portfolio_contacts WHERE business_id=? AND id=?',(d['business_id'],d['contact_id'])).fetchone()
        stopped=db.execute('SELECT stopped FROM scm_customers WHERE business_id=? AND contact_id=?',(d['business_id'],d['contact_id'])).fetchone()
        if d['state']!='active' or not contact or contact[0]!='active' or stopped and stopped[0]: return False
        for purpose in ('contact','schedule'):
            c=db.execute('SELECT granted FROM portfolio_consents WHERE business_id=? AND contact_id=? AND purpose=? ORDER BY id DESC LIMIT 1',(d['business_id'],d['contact_id'],purpose)).fetchone()
            if not c or not c[0]: return False
    return True


class SeminarSupport:
    def __init__(self,office):
        self.o=office; self.store=office.store; self.db=office.db; self.sem=office.seminars; self.pf=office.portfolio
    def access(self,p,b,s=None,write=False,owner=False):
        self.pf.access(p,b,write,owner)
        if s:
            row=self.db.execute('SELECT * FROM s14_meta WHERE business_id=? AND seminar_id=?',(b,s)).fetchone()
            if not row: raise ValueError('Seminar is not in this business extension')
            return row
    def event(self,p,b,s,event,ref='',detail=None):
        self.db.execute('INSERT INTO s14_events(business_id,seminar_id,actor,event,ref,detail,created_at) VALUES(?,?,?,?,?,?,?)',(b,s,p.identity,event,ref,encode(detail or {}),now().isoformat()))
    def save(self,p,b,data,segments,online='',mode='mock',lead_hours=24,clock='09:00',identifier=None,revision=None):
        self.access(p,b,owner=True); self.pf.writable(p,b)
        if not isinstance(segments,list) or not segments or len(segments)!=len(set(segments)) or not set(segments)<=set(SEGMENTS): raise ValueError('Explicit target segments required')
        if mode not in ('manual','mock') or not 1<=int(lead_hours)<=168: raise ValueError('Mode and reminder lead hours required')
        parsed=time.fromisoformat(clock)
        if parsed.second or parsed.microsecond or parsed.tzinfo: raise ValueError('Local HH:MM clock required')
        clock=parsed.strftime('%H:%M')
        if online:
            online=text(online,600); url=urlsplit(online)
            if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.hostname in ('localhost','127.0.0.1','::1'): raise ValueError('Public HTTPS meeting reference without secrets/query required')
        for key in ('title','location','description'): text(data[key],4000)
        def op():
            if identifier:
                old=self.access(p,b,identifier,owner=True)
                if old['revision']!=int(revision) or old['mode']!=mode: raise ValueError('Current extension revision and unchanged mode required')
                for r in self.db.execute("SELECT r.segment FROM seminar_responses x JOIN seminar_registrations r USING(username) WHERE x.seminar_id=? AND x.answer='yes'",(identifier,)):
                    if r[0] not in segments: raise ValueError('Cancel/review existing applications before narrowing targets')
            values={**data,'audience':'public' if 'cast' in segments else 'user' if 'user' in segments else 'dealer'}
            sid=self.sem.save(p,values,identifier,self.sem.get(identifier)['revision'] if identifier else None)
            self.pf.link_seminar(p,b,sid)
            self.db.execute('INSERT INTO s14_meta VALUES(?,?,?,?,?,?,?,1) ON CONFLICT(seminar_id) DO UPDATE SET online_url=excluded.online_url,segments=excluded.segments,lead_hours=excluded.lead_hours,clock=excluded.clock,revision=revision+1',(sid,b,online,encode(sorted(segments)),mode,int(lead_hours),clock))
            self.db.execute("UPDATE s14_drafts SET status='stale' WHERE seminar_id=? AND status IN ('approval_pending','send_waiting_no_api','retry_wait')",(sid,))
            self.event(p,b,sid,'seminar_saved',sid,{'mode':mode}); return sid
        return self.store._transaction(op)
    def adopt(self,p,b,s,mode='mock'):
        self.access(p,b,owner=True); self.pf.writable(p,b)
        if mode not in ('manual','mock'): raise ValueError('Explicit mode required')
        if not self.db.execute('SELECT 1 FROM portfolio_seminar_links WHERE business_id=? AND seminar_id=?',(b,s)).fetchone(): raise ValueError('Existing explicit business link required')
        core=self.sem.get(s); targets=[k for k,v in ACCESS.items() if core['audience'] in v]
        self.db.execute('INSERT OR IGNORE INTO s14_meta VALUES(?,?,?,?,?,?,?,1)',(s,b,'',encode(targets),mode,24,'09:00')); self.event(p,b,s,'existing_seminar_adopted',s)
    def state(self,p,b,s,state):
        self.access(p,b,s,owner=True); self.pf.writable(p,b)
        self.sem.change_state(p,s,state); self.db.execute("UPDATE s14_drafts SET status='stale' WHERE seminar_id=? AND status IN ('approval_pending','send_waiting_no_api','retry_wait')",(s,)); self.event(p,b,s,'seminar_'+state,s)
    def member(self,p,s):
        if p.role!='participant': raise PermissionError('Participant self-service required')
        user=self.db.execute('SELECT role FROM office_users WHERE username=?',(p.identity,)).fetchone()
        if not user or user[0]!='participant': raise PermissionError('Authenticated participant required')
        self.sem.visible(p.identity,s)
        row=self.db.execute('SELECT * FROM s14_meta WHERE seminar_id=?',(s,)).fetchone()
        if not row: raise ValueError('Extension seminar required')
        return row
    def consent(self,p,s,purpose,granted):
        meta=self.member(p,s)
        if purpose not in ('invite','reminders','followup') or type(granted) is not bool: raise ValueError('Purpose and explicit consent required')
        self.db.execute('INSERT INTO s14_consents(seminar_id,username,purpose,granted,notice,created_at) VALUES(?,?,?,?,?,?)',(s,p.identity,purpose,int(granted),'s14-local-mock-v1',now().isoformat()))
        self.db.execute("UPDATE s14_drafts SET status='stale' WHERE seminar_id=? AND username=? AND status IN ('approval_pending','send_waiting_no_api','retry_wait')",(s,p.identity)); self.event(p,meta['business_id'],s,'purpose_consent_changed',purpose,{'granted':granted})
    def preferences(self,p,s,interests):
        meta=self.member(p,s)
        if not isinstance(interests,list) or not set(interests)<=set(INTERESTS) or len(interests)>5: raise ValueError('Self-reported coarse interests only')
        self.db.execute('INSERT INTO s14_preferences VALUES(?,?,?,1) ON CONFLICT(seminar_id,username) DO UPDATE SET interests=excluded.interests,revision=revision+1',(s,p.identity,encode(sorted(set(interests)))))
        self.event(p,meta['business_id'],s,'self_reported_interests_changed')
    def response(self,p,s,answer):
        meta=self.member(p,s)
        if answer=='cancel': self.sem.withdraw(p.identity,s)
        else: self.sem.respond(p.identity,s,answer)
        self.event(p,meta['business_id'],s,'participant_response',answer)
    def engagement(self,p,b,s,username,state,evidence='',when=None):
        meta=self.access(p,b,s,owner=True); self.pf.writable(p,b); self.sem.visible(username,s)
        if state not in ('attended','absent','interview_requested','interview_completed'): raise ValueError('Known observed state required')
        evidence=text(evidence,300); core=self.sem.get(s)
        if core['status']=='cancelled': raise ValueError('Cancelled seminar cannot gain new outcomes')
        ever=self.applicants(s)
        if username not in ever: raise ValueError('Observed applicant required')
        def op():
            if state in ('attended','absent'):
                if core['end_at']>now().isoformat(): raise ValueError('After-event observation required')
                self.db.execute('INSERT INTO wf_attendance VALUES(?,?,?,?,?,1) ON CONFLICT(seminar_id,username,mode) DO UPDATE SET outcome=excluded.outcome,evidence=excluded.evidence,revision=revision+1',(s,username,state,evidence,meta['mode']))
            else:
                old=self.db.execute('SELECT * FROM s14_engagement WHERE seminar_id=? AND username=? AND mode=?',(s,username,meta['mode'])).fetchone()
                if state=='interview_completed' and (not old or old['interview']!='requested' or not when or utc(when)>now().isoformat()): raise ValueError('Observed past interview after explicit request required')
                if old and old['interview']=='completed' and state=='interview_requested': raise ValueError('Completed observation cannot be silently reopened')
                self.db.execute('INSERT INTO s14_engagement VALUES(?,?,?,?,?,?,1) ON CONFLICT(seminar_id,username,mode) DO UPDATE SET interview=excluded.interview,evidence=excluded.evidence,interview_at=excluded.interview_at,revision=revision+1',(s,username,meta['mode'],'completed' if state=='interview_completed' else 'requested',evidence,utc(when) if when else None))
            self.event(p,b,s,'observed_'+state,username,{'mode':meta['mode']})
        self.store._transaction(op)
    def request_interview(self,p,s):
        meta=self.member(p,s)
        if self.sem.get(s)['status']=='cancelled' or p.identity not in self.applicants(s): raise ValueError('Active seminar and applicant request required')
        old=self.db.execute('SELECT interview FROM s14_engagement WHERE seminar_id=? AND username=? AND mode=?',(s,p.identity,meta['mode'])).fetchone()
        if old: return
        self.db.execute("INSERT INTO s14_engagement VALUES(?,?,?,'requested','本人がローカル画面で希望を入力',NULL,1)",(s,p.identity,meta['mode'])); self.event(p,meta['business_id'],s,'self_requested_interview')
    def applicants(self,s):
        users={r[0] for r in self.db.execute("SELECT username FROM seminar_responses WHERE seminar_id=? AND answer='yes'",(s,))}
        for row in self.db.execute("SELECT actor,detail FROM seminar_events WHERE ref=? AND event='response_changed'",(s,)):
            if json.loads(row['detail']).get('after')=='yes': users.add(row['actor'])
        return users
    def conditions(self,p,b,s,username,kind):
        meta=self.access(p,b,s,owner=True); self.pf.writable(p,b); core=self.sem.visible(username,s); reg=self.sem.member(username,True)
        if kind not in KINDS or core['status']=='cancelled': raise ValueError('Known action on non-cancelled seminar required')
        answer=self.db.execute('SELECT * FROM seminar_responses WHERE seminar_id=? AND username=?',(s,username)).fetchone()
        if answer and answer['answer']=='no': raise ValueError('Decline/cancellation respected')
        legacy=self.sem.latest_consent(username)
        consent=self.db.execute('SELECT * FROM s14_consents WHERE seminar_id=? AND username=? AND purpose=? ORDER BY id DESC LIMIT 1',(s,username,PURPOSE[kind])).fetchone()
        if not legacy or not legacy['granted'] or not consent or not consent['granted']: raise ValueError('Seminar stop and separate purpose consent required')
        if kind in ('invite','deadline') and (core['status']!='open' or core['deadline']<=now().isoformat()): raise ValueError('Registration period closed')
        if kind in ('invite','deadline','previous_day','event_day') and core['start_at']<=now().isoformat(): raise ValueError('Seminar already started')
        if kind in ('previous_day','event_day') and (not answer or answer['answer']!='yes'): raise ValueError('Scheduled attendees only')
        attendance=self.db.execute('SELECT * FROM wf_attendance WHERE seminar_id=? AND username=? AND mode=?',(s,username,meta['mode'])).fetchone()
        if PURPOSE[kind]=='followup':
            if core['end_at']>now().isoformat() or not attendance or attendance['outcome']!=('absent' if kind=='absence' else 'attended'): raise ValueError('Observed attendance/absence after event required')
        preference=self.db.execute('SELECT * FROM s14_preferences WHERE seminar_id=? AND username=?',(s,username)).fetchone()
        interview=self.db.execute('SELECT * FROM s14_engagement WHERE seminar_id=? AND username=? AND mode=?',(s,username,meta['mode'])).fetchone()
        links=[]
        for row in self.db.execute('SELECT d.* FROM scm_seminars l JOIN scm_deals d ON d.business_id=l.business_id AND d.id=l.deal_id WHERE l.business_id=? AND l.seminar_id=? AND l.participant_username=?',(b,s,username)):
            purpose='followup' if PURPOSE[kind]=='followup' else 'contact'
            if row['state']!='active' or not self.o.sales.contact_allowed(b,row['contact_id'],purpose) or not self.o.sales.contact_allowed(b,row['contact_id'],'schedule'): raise ValueError('Existing business contact consent/stop required')
            links.append(self.o.sales.snapshot(b,row,kind,None,None,{}))
        history=[dict(r) for r in self.db.execute('SELECT a.seminar_id,a.outcome,a.revision FROM wf_attendance a JOIN s14_meta m ON m.seminar_id=a.seminar_id WHERE m.business_id=? AND a.username=? AND a.mode=? ORDER BY a.seminar_id',(b,username,meta['mode']))]
        snapshot=digest([dict(meta),dict(core),dict(reg),dict(legacy),dict(consent),dict(answer) if answer else None,dict(attendance) if attendance else None,dict(preference) if preference else None,dict(interview) if interview else None,links,history,kind])
        return snapshot,meta,core,reg,preference,history
    def due(self,core,meta,kind):
        start=datetime.fromisoformat(core['start_at']).astimezone(JST)
        if kind=='deadline': return (datetime.fromisoformat(core['deadline'])-timedelta(hours=meta['lead_hours'])).isoformat()
        if kind in ('previous_day','event_day'):
            day=start.date()-timedelta(days=int(kind=='previous_day'))
            return datetime.combine(day,time.fromisoformat(meta['clock']),JST).astimezone(start.tzinfo).astimezone(datetime.fromisoformat(core['start_at']).tzinfo).isoformat()
        return now().isoformat()
    def prepare(self,p,b,s,username,kind):
        def op():
            snapshot,meta,core,reg,pref,history=self.conditions(p,b,s,username,kind)
            completed=self.db.execute("SELECT id FROM s14_drafts WHERE seminar_id=? AND username=? AND kind=? AND status='mock_sent'",(s,username,kind)).fetchone()
            if completed: return completed[0]
            old=self.db.execute('SELECT id FROM s14_drafts WHERE seminar_id=? AND username=? AND kind=? AND snapshot=?',(s,username,kind,snapshot)).fetchone()
            if old: return old[0]
            due=self.due(core,meta,kind)
            if due>=core['start_at'] and PURPOSE[kind]!='followup': raise ValueError('Reminder clock must precede start, representative adjustment required')
            interests=('・'.join(INTERESTS[k] for k in INTERESTS if k in json.loads(pref['interests'])) if pref else '') or '本人の関心は未申告'
            wording={'invite':'参加は任意です。ご希望の場合のみお申し込みください。','deadline':'申込期限をご確認ください。参加は任意です。','previous_day':'参加予定の日時・場所をご確認ください。','event_day':'本日の参加予定をご確認ください。ご都合の変更もお知らせいただけます。','thanks':'ご参加ありがとうございました。必要な情報があれば任意にお知らせください。','question':'ご質問の内容を確認して担当者が回答するための案です。具体的回答は代表確認待ちです。','interview':'ご希望の場合に限り、個別面談の候補をご案内できます。予約は確定していません。','absence':'ご事情を尊重します。今後の情報が必要な場合のみお知らせください。'}[kind]
            start_text=datetime.fromisoformat(core['start_at']).astimezone(JST).strftime('%Y年%m月%d日 %H:%M JST')
            end_text=datetime.fromisoformat(core['end_at']).astimezone(JST).strftime('%Y年%m月%d日 %H:%M JST')
            body='【ローカル固定テンプレート・LLM未使用・未送信】\n'+LABELS[reg['segment']]+'向け\n'+core['title']+'\n'+start_text+' ～ '+end_text+'\n'+core['location']+'\nオンライン参照 '+(meta['online_url'] or '未登録')+'\n参加費 '+str(core['fee_yen'])+'円\n内容：'+core['description']+'\n本人申告の関心：'+interests+'\n同一事業での確認済み過去参加記録 '+str(sum(r['outcome']=='attended' and r['seminar_id']!=s for r in history))+'件\n'+wording+'\n健康効果・収入・報酬・法的性質の未確認事項は要確認です。'
            if unsafe_copy(body): raise ValueError('Unsupported claim needs human correction')
            self.db.execute("UPDATE s14_drafts SET status='stale' WHERE seminar_id=? AND username=? AND kind=? AND status IN ('approval_pending','send_waiting_no_api','retry_wait')",(s,username,kind))
            if kind in ('invite','absence'):
                self.db.execute("UPDATE wf_drafts SET status='stale' WHERE seminar_id=? AND username=? AND kind=? AND mode=? AND status IN ('approval_pending','send_waiting_no_api','retry_wait')",(s,username,kind,meta['mode']))
            identifier=uuid4().hex
            self.db.execute('INSERT INTO s14_drafts VALUES(?,?,?,?,?,?,?,?,?,?,?,0,NULL,NULL,?)',(identifier,b,s,username,kind,meta['mode'],body,digest(body),snapshot,due,'approval_pending',now().isoformat()))
            self.event(p,b,s,'draft_prepared',identifier,{'kind':kind,'llm_used':False}); return identifier
        return self.store._transaction(op)
    def bulk(self,p,b,s,kind,limit=200):
        if kind not in KINDS or not 1<=limit<=200: raise ValueError('Known kind and bounded batch required')
        self.access(p,b,s,owner=True); prepared=[]; skipped=0
        for r in self.sem.recipients(p,s)[:limit]:
            try: prepared.append(self.prepare(p,b,s,r['username'],kind))
            except (ValueError,PermissionError): skipped+=1
        self.event(p,b,s,'bulk_prepared',kind,{'prepared':len(prepared),'skipped':skipped,'limit':limit}); return {'ids':prepared,'skipped':skipped}
    def current(self,p,b,row):
        try: return digest(row['body'])==row['body_digest'] and not unsafe_copy(row['body']) and self.conditions(p,b,row['seminar_id'],row['username'],row['kind'])[0]==row['snapshot']
        except (ValueError,PermissionError): return False
    def decide(self,p,b,identifier,decision,checked):
        self.access(p,b,owner=True)
        def op():
            row=self.db.execute('SELECT * FROM s14_drafts WHERE id=? AND business_id=?',(identifier,b)).fetchone()
            if not row or row['status']!='approval_pending' or not checked or decision not in ('approve','reject'): raise ValueError('Pending draft and explicit representative decision required')
            if decision=='approve' and not self.current(p,b,row): raise ValueError('Current content, consent and seminar required')
            self.db.execute('UPDATE s14_drafts SET status=?,approved_by=? WHERE id=?',('send_waiting_no_api' if decision=='approve' else 'rejected',p.identity,identifier)); self.event(p,b,row['seminar_id'],'draft_'+decision,identifier,{'snapshot':row['snapshot'],'body_digest':row['body_digest']})
        self.store._transaction(op)
    def revise(self,p,b,identifier,body):
        self.access(p,b,owner=True); self.pf.writable(p,b); body=text(body,7000)
        if unsafe_copy(body): raise ValueError('Unsupported claim needs correction')
        def op():
            row=self.db.execute('SELECT * FROM s14_drafts WHERE business_id=? AND id=?',(b,identifier)).fetchone()
            if not row or row['status'] not in ('approval_pending','send_waiting_no_api') or row['attempts']: raise ValueError('Only unattempted drafts may be revised')
            self.db.execute('INSERT INTO s14_draft_history VALUES(?,?,?,?,?)',(uuid4().hex,identifier,row['body'],row['body_digest'],now().isoformat()))
            self.db.execute("UPDATE s14_drafts SET body=?,body_digest=?,status='approval_pending',approved_by=NULL WHERE id=?",(body,digest(body),identifier)); self.event(p,b,row['seminar_id'],'draft_revised',identifier,{'old_digest':row['body_digest'],'new_digest':digest(body)})
        self.store._transaction(op)

    def queue(self,p,b):
        self.access(p,b,owner=True)
        return [dict(r) for r in self.db.execute("SELECT * FROM s14_drafts WHERE business_id=? AND status IN ('send_waiting_no_api','retry_wait')",(b,)) if self.current(p,b,r)]
    def mock(self,p,b,identifier,outcome='success'):
        self.access(p,b,owner=True)
        if outcome not in ('success','transient','permanent','timeout'): raise ValueError('Mock outcome required')
        def op():
            row=self.db.execute('SELECT * FROM s14_drafts WHERE business_id=? AND id=?',(b,identifier)).fetchone()
            if not row or row['status'] not in ('send_waiting_no_api','retry_wait') or row['attempts']>=3 or row['next_at'] and row['next_at']>now().isoformat() or row['due_at']>now().isoformat() or not self.current(p,b,row): raise ValueError('Current approved due draft required')
            approval=self.db.execute("SELECT detail FROM s14_events WHERE ref=? AND event='draft_approve' ORDER BY id DESC LIMIT 1",(identifier,)).fetchone()
            if not approval or json.loads(approval[0])!={'snapshot':row['snapshot'],'body_digest':row['body_digest']}: raise ValueError('Approval snapshot mismatch')
            attempts=row['attempts']+1; status='mock_sent' if outcome=='success' else 'manual_reconciliation' if outcome=='timeout' else 'failed' if outcome=='permanent' or attempts>=3 else 'retry_wait'
            self.db.execute('UPDATE s14_drafts SET status=?,attempts=?,next_at=? WHERE id=?',(status,attempts,(now()+timedelta(seconds=60*attempts)).isoformat() if status=='retry_wait' else None,identifier)); self.db.execute('INSERT INTO s14_attempts VALUES(?,?,?,?,?)',(uuid4().hex,identifier,outcome,'mock',now().isoformat())); self.event(p,b,row['seminar_id'],'mock_result',identifier,{'status':status,'external_call':False}); return status
        return self.store._transaction(op)
    def scheduler(self,p,b,enabled):
        self.access(p,b,owner=True)
        if type(enabled) is not bool: raise ValueError('Explicit setting required')
        self.db.execute('INSERT INTO s14_scheduler VALUES(?,?) ON CONFLICT(business_id) DO UPDATE SET enabled=excluded.enabled',(b,int(enabled))); self.event(p,b,None,'scheduler_changed',detail={'enabled':enabled})
    def tick(self,p,b):
        self.access(p,b,owner=True); row=self.db.execute('SELECT enabled FROM s14_scheduler WHERE business_id=?',(b,)).fetchone()
        if not row or not row[0]: return 0
        count=0
        for meta in self.db.execute('SELECT * FROM s14_meta WHERE business_id=?',(b,)):
            core=self.sem.get(meta['seminar_id'])
            if core['status']=='cancelled': continue
            for kind in ('event_day','previous_day','deadline'):
                due=self.due(core,meta,kind)
                if datetime.fromisoformat(due).astimezone(JST).date()!=now().astimezone(JST).date(): continue
                result=self.bulk(p,b,meta['seminar_id'],kind,200-count); count+=len(result['ids']); break
            if count>=200: break
        self.event(p,b,None,'scheduled_drafts_only',detail={'count':count,'sent':0}); return count
    def work(self,p,b,s,activity,key,agent=None):
        self.access(p,b,s,write=True); self.pf.writable(p,b)
        if not key or len(key)>60 or activity not in ACTIVITIES or agent and agent not in ACTIVITIES[activity]: raise ValueError('Existing specialty required')
        choices=ACTIVITIES[activity]
        old=self.db.execute('SELECT w.task_id,w.activity,t.agent FROM s14_work w JOIN tasks t ON t.id=w.task_id WHERE t.dedupe_key=?',('portfolio:'+digest([b,'s14:'+s+':'+key]),)).fetchone()
        if old:
            if old['activity']!=activity or agent and old['agent']!=agent: raise ValueError('Task key conflict')
            return old[0]
        selected=agent or min(choices,key=lambda a:self.db.execute("SELECT COUNT(*) FROM tasks WHERE agent=? AND status='pending'",(a,)).fetchone()[0])
        category=next(k for k,v in CANDIDATES.items() if selected in v)
        task=self.pf.task(p,b,'s14:'+s+':'+key,category,'セミナーの内部準備。個人本文は事業権限付き画面でのみ参照。',agent=selected)
        self.db.execute('INSERT INTO s14_work VALUES(?,?,?,?,NULL,NULL,NULL,0)',(task,b,s,activity)); self.event(p,b,s,'work_assigned',task,{'agent':selected,'activity':activity}); return task
    def run(self,p,b,task):
        self.access(p,b,write=True); self.pf.writable(p,b)
        def op():
            row=self.db.execute('SELECT w.*,t.agent FROM s14_work w JOIN tasks t ON t.id=w.task_id WHERE w.business_id=? AND w.task_id=?',(b,task)).fetchone()
            if not row or row['paused']: raise ValueError('Unpaused scoped task required')
            if row['artifact_id']: return row['artifact_id']
            core=self.sem.get(row['seminar_id'])
            if core['status']=='cancelled': raise ValueError('Cancelled seminar')
            run=self.store.claim(task)
            result='【固定テンプレート・LLM未使用】'+self.store.agents[row['agent']]['role']+'：'+row['activity']+'\n'+core['title']+'\n区分・本人希望・目的別同意・停止・日程・料金・根拠・公式規約を確認し、必要な作業だけ代表へ提案する。未確認事項は要確認。送信・面談確定・契約・課金は行わない。'
            if row['activity']=='analysis': result+='\n'+encode(self.metrics(p,b,row['seminar_id']))
            artifact=self.store.finish(run,'セミナー内部確認用の固定テンプレートを事業権限付き画面に保存。外部実行なし。')
            self.db.execute('UPDATE s14_work SET artifact_id=?,result=? WHERE task_id=?',(artifact,result,task)); self.event(p,b,row['seminar_id'],'local_work_result',task); return artifact
        return self.store._transaction(op)
    def control(self,p,b,task,action,value=None):
        def op():
            self.access(p,b,owner=True)
            row=self.db.execute('SELECT * FROM s14_work WHERE business_id=? AND task_id=?',(b,task)).fetchone()
            if not row: raise ValueError('Scoped task required')
            if action in ('pause','resume'): self.db.execute('UPDATE s14_work SET paused=? WHERE task_id=?',(int(action=='pause'),task))
            elif action in ('approve','reject'):
                self.pf.writable(p,b)
                if self.sem.get(row['seminar_id'])['status']=='cancelled' or action=='approve' and unsafe_copy(row['result'] or ''): raise ValueError('Cancelled or unsafe work cannot be approved')
                if row['paused'] or not row['artifact_id']: raise ValueError('Unpaused output required')
                self.store.review(row['artifact_id'],self.store.agents[self.db.execute('SELECT agent FROM tasks WHERE id=?',(task,)).fetchone()[0]]['reviewer'],'pass','固定書式の確認。実AI監査ではない。'); self.store.decide(row['artifact_id'],action,'representative'); self.db.execute('UPDATE s14_work SET approved_by=? WHERE task_id=?',(p.identity if action=='approve' else None,task))
            else: raise ValueError('Known task control required')
            self.event(p,b,row['seminar_id'],'task_'+action,task)
        return self.store._transaction(op)
    def attribute(self,p,b,s,kind,source,evidence):
        meta=self.access(p,b,s,owner=True); self.pf.writable(p,b)
        if meta['mode']!='manual' or kind not in ('invoice','expense'): raise ValueError('Only observed actual financial sources')
        if kind=='invoice':
            row=self.o.sales.row(p,b,'scm_invoices',source)
            if row['mode']!='manual' or not self.db.execute('SELECT 1 FROM scm_seminars WHERE business_id=? AND seminar_id=? AND deal_id=?',(b,s,row['deal_id'])).fetchone(): raise ValueError('Explicit linked actual deal/invoice required')
        else:
            row=self.db.execute("SELECT * FROM portfolio_ledger WHERE business_id=? AND id=? AND kind='expense' AND mode='actual'",(b,source)).fetchone()
            if not row: raise ValueError('Actual business expense required')
        evidence=text(evidence,300)
        old=self.db.execute('SELECT * FROM s14_attributions WHERE kind=? AND source_ref=? AND active=1',(kind,source)).fetchone()
        if old:
            if old['business_id']!=b or old['seminar_id']!=s: raise ValueError('Already attributed elsewhere; no double counting')
            return old['id']
        identifier=uuid4().hex; self.db.execute('INSERT INTO s14_attributions VALUES(?,?,?,?,?,?,1)',(identifier,b,s,kind,source,evidence)); self.event(p,b,s,'financial_source_attributed',identifier); return identifier
    def unattribute(self,p,b,identifier,checked):
        self.access(p,b,owner=True)
        if not checked: raise ValueError('Explicit representative correction required')
        def op():
            row=self.db.execute('SELECT * FROM s14_attributions WHERE business_id=? AND id=?',(b,identifier)).fetchone()
            if not row or not row['active']: raise ValueError('Active scoped attribution required')
            self.db.execute('UPDATE s14_attributions SET active=0 WHERE id=?',(identifier,)); self.event(p,b,row['seminar_id'],'financial_attribution_released',identifier)
        self.store._transaction(op)

    def metrics(self,p,b,s):
        meta=self.access(p,b,s); core=self.sem.get(s); users=self.applicants(s)
        current={r['username']:r['answer'] for r in self.db.execute('SELECT * FROM seminar_responses WHERE seminar_id=?',(s,))}
        cancelled=sum(current.get(u)=='no' for u in users)
        attendance=[dict(r) for r in self.db.execute('SELECT * FROM wf_attendance WHERE seminar_id=? AND mode=?',(s,meta['mode']))]
        attended={r['username'] for r in attendance if r['outcome']=='attended'}&users
        interviews=[dict(r) for r in self.db.execute("SELECT * FROM s14_engagement WHERE seminar_id=? AND mode=? AND interview IN ('requested','completed')",(s,meta['mode']))]
        requested={r['username'] for r in interviews}&users
        completed={r['username'] for r in interviews if r['interview']=='completed'}&users
        actual=meta['mode']=='manual'; invoices=[]; expenses=[]
        for a in self.db.execute('SELECT * FROM s14_attributions WHERE business_id=? AND seminar_id=? AND active=1',(b,s)):
            if a['kind']=='invoice':
                invoice=self.o.sales.row(p,b,'scm_invoices',a['source_ref']); credited=self.db.execute('SELECT 1 FROM scm_credits WHERE business_id=? AND invoice_id=?',(b,invoice['id'])).fetchone(); invoices.append((invoice,credited))
            else: expenses.append(self.db.execute('SELECT amount_yen FROM portfolio_ledger WHERE business_id=? AND id=?',(b,a['source_ref'])).fetchone()[0])
        ratio=lambda n,d:round(n/d*100,2) if d else None
        counts={'applicants':len(users),'planned':sum(a=='yes' for a in current.values()),'cancelled':cancelled,'attended':len(attended),'absent':sum(r['outcome']=='absent' and r['username'] in users for r in attendance),'interview_requested':len(requested),'interview_completed':len(completed),'attendance_percent':ratio(len(attended),len(users)),'cancellation_percent':ratio(cancelled,len(users)),'interview_percent':ratio(len(attended&requested),len(attended))}
        return {'mode':meta['mode'],'actual':counts if actual else {k:None for k in counts},'mock_preview':counts if not actual else None,'sales_net_yen':sum(0 if credit else i['net_yen'] for i,credit in invoices) if actual and invoices else None,'cash_yen':sum(self.o.sales.invoice_balance(b,i)[1] for i,_ in invoices) if actual and invoices else None,'expense_yen':sum(expenses) if actual and expenses else None,'denominators':'セミナー全期間の一度でも参加申込した本人。取消率は現在不参加の既申込者。面談化率は実参加者中の明示的面談希望者。','finance_definition':'代表が根拠付きで明示配賦した既存請求/経費のみ。予測・モック・参加費×人数は売上にしない。税抜請求−全額取消、入金−返金。外部照合は未接続。'}
