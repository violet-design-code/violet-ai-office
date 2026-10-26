"""Scoped local business administration. No transport, payments or AI provider.

Only opaque metadata crosses into the shared task store. Business rules, contacts,
photos and output remain behind explicit business scope and identity checks.
"""
import json
import re
import time
from datetime import date,datetime,timedelta,timezone
from uuid import uuid4
from .store import now
from .content import encode,digest,safe_input
from .learning import risky
from .selection import CAPABILITIES
from .seminars import BUSINESS_COPY,JST

KINDS={'health':'健康・美容','cleaning':'出張清掃','web':'ホームページ制作・LINE自動化','life':'人生プロデュース・相談','whitening':'セルフホワイトニング','education':'スマホ教室','other':'その他'}
STATES={'preparing':'準備中','active':'提供中','paused':'休止中','closed':'終了'}
CASE_KINDS={'inquiry':'問い合わせ','booking_request':'予約希望','work':'施工・制作業務','followup':'フォロー予定','maintenance':'保守契約管理','terms':'公式規約確認'}
DOCS={
 'health':{'sns':'SNS原稿','line':'LINE原稿','product':'商品・サービス説明','referral':'事業紹介・紹介希望資料','terms':'公式規約確認票','reply':'返信案'},
 'cleaning':{'listing':'掲載文案','estimate':'見積もり案','reply':'返信案','checklist':'施工チェック','completion':'完了記録案'},
 'web':{'hearing':'ヒアリング項目','proposal':'提案書案','estimate':'見積もり案','checklist':'制作タスク・納品前チェック','maintenance':'保守契約案','reply':'返信案'},
 'life':{'service_plan':'サービス企画','proposal':'相談提案書案','followup':'フォロー計画','estimate':'料金案','reply':'返信案'},
 'whitening':{'listing':'案内原稿','proposal':'サービス説明案','reply':'返信案','checklist':'利用前確認項目'},
 'education':{'listing':'教室案内原稿','hearing':'受講希望の確認項目','proposal':'レッスン提案案','reply':'返信案'},
 'other':{'proposal':'提案書案','reply':'返信案'}}
OPERATIONS={'contact':'顧客連絡','quote_send':'見積もり送信','publish':'掲載・公開','deliver':'納品','contract':'契約確定','booking_confirm':'予約確定'}
METRICS={'inquiries':'問い合わせ','bookings':'予約','contracts':'契約','owner_minutes':'代表作業時間（分）'}
CANDIDATES={**CAPABILITIES,'operations':('AI23','AI24'),'copy':('AI09','AI17'),'review':('AI28',)}
INITIAL=[('BIZ-A','健康・美容・ベネシードディーラー関連','health','',''),('BIZ-B','鏡のウロコ取り出張サービス','cleaning','佐久市内6,000円（出張費込み）。佐久市外は出張費別途、要相談。','佐久市内・市外。軽井沢エリアは対応候補。'),('BIZ-C','ホームページ制作・LINE自動化','web','',''),('BIZ-D','人生プロデュース・相談サービス','life','',''),('BIZ-E','セルフホワイトニング','whitening','',''),('BIZ-F','スマホ教室','education','','')]


def text(value,limit=2000,personal=False):
    if not isinstance(value,str) or not value.strip() or len(value)>limit: raise ValueError('Bounded text required')
    if re.search(r'(?i)(?:bearer\s+|sk-|(?:api[_-]?key|token|secret|password)\s*[=:])\S+',value): raise ValueError('Secrets must not be stored')
    if not personal: safe_input(value)
    return value.strip()


def period(start,end):
    a,b=date.fromisoformat(start),date.fromisoformat(end)
    if not 0<(b-a).days<=366: raise ValueError('Bounded date range required')
    return a,b


class Portfolio:
    def __init__(self,store): self.store,self.db=store,store.db

    def access(self,p,business=None,write=False,owner=False):
        row=self.db.execute('SELECT role FROM office_users WHERE username=?',(p.identity,)).fetchone()
        if not row or row[0]!=p.role: raise PermissionError('Current authenticated identity required')
        if p.role=='representative':
            if business is not None and not self.db.execute('SELECT 1 FROM portfolio_businesses WHERE id=?',(business,)).fetchone(): raise ValueError('Unknown business')
            return
        if owner or p.role!='business_operator' or business is None: raise PermissionError('Representative required')
        acl=self.db.execute('SELECT can_write FROM portfolio_acl WHERE business_id=? AND username=?',(business,p.identity)).fetchone()
        if not acl or write and not acl[0]: raise PermissionError('Business scope denied')

    def event(self,p,business,event,ref='',detail=None):
        self.db.execute('INSERT INTO portfolio_events(business_id,actor,event,ref,detail,created_at) VALUES(?,?,?,?,?,?)',(business,p.identity,event,ref,encode(detail or {}),now().isoformat()))

    def row(self,p,business,table,identifier,write=False):
        self.access(p,business,write)
        allowed={'portfolio_contacts','portfolio_cases','portfolio_documents','portfolio_operations','portfolio_media'}
        if table not in allowed: raise ValueError('Known scoped resource required')
        row=self.db.execute('SELECT * FROM '+table+' WHERE business_id=? AND id=?',(business,identifier)).fetchone()
        if not row: raise ValueError('Resource is outside this business or missing')
        return row

    def seed(self,p):
        self.access(p,owner=True)
        def op():
            for bid,name,kind,pricing,region in INITIAL:
                if not self.db.execute('SELECT 1 FROM portfolio_businesses WHERE id=? OR name=?',(bid,name)).fetchone():
                    self.create(p,bid,name,kind,pricing=pricing,region=region)
                    self.update(p,bid,{'status':'active','confirmed':True},1)
        self.store._transaction(op)

    def create(self,p,business,name,kind,services='',pricing='',audience='',region=''):
        self.access(p,owner=True)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',business) or kind not in KINDS: raise ValueError('Business ID/type required')
        name=text(name,120)
        for v in (services,pricing,audience,region):
            if v: text(v)
        self.db.execute('INSERT INTO portfolio_businesses VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(business,name,kind,'preparing',services,pricing,audience,region,0,3,1,now().isoformat()))
        self.event(p,business,'business_created',business,{'state':'preparing'})
        return business

    def update(self,p,business,data,revision):
        self.access(p,business,owner=True)
        def op():
            old=self.db.execute('SELECT * FROM portfolio_businesses WHERE id=?',(business,)).fetchone()
            state=data.get('status',old['status']); confirmed=data.get('confirmed',False)
            if state not in STATES or state=='active' and not confirmed: raise ValueError('Actual offering must be confirmed by representative')
            if old['revision']!=int(revision): raise ValueError('Current revision required')
            values={k:text(data.get(k,old[k]),2000) if data.get(k,old[k]) else '' for k in ('name','services','pricing','audience','region')}
            priority=int(data.get('priority',old['priority']))
            if not 1<=priority<=5: raise ValueError('Priority 1..5')
            self.db.execute('UPDATE portfolio_businesses SET name=?,status=?,services=?,pricing=?,audience=?,region=?,offering_confirmed=?,priority=?,revision=revision+1 WHERE id=?',(values['name'],state,values['services'],values['pricing'],values['audience'],values['region'],int(bool(confirmed)),priority,business))
            self.db.execute("UPDATE portfolio_documents SET status='approval_pending' WHERE business_id=? AND status='approved'",(business,))
            self.db.execute("UPDATE portfolio_operations SET status='approval_pending' WHERE business_id=? AND status='approved'",(business,))
            self.event(p,business,'business_updated',business,{'before_revision':old['revision'],'prior_state':old['status'],'state':state,'before_settings':{k:old[k] for k in ('services','pricing','audience','region','priority')},'after_settings':{**values,'priority':priority}})
        self.store._transaction(op)

    def grant(self,p,business,username,write):
        self.access(p,business,owner=True)
        user=self.db.execute('SELECT role FROM office_users WHERE username=?',(username,)).fetchone()
        if not user or user[0]!='business_operator': raise ValueError('Scoped operator required')
        self.db.execute('INSERT INTO portfolio_acl VALUES(?,?,?) ON CONFLICT(business_id,username) DO UPDATE SET can_write=excluded.can_write',(business,username,int(bool(write))))
        self.event(p,business,'access_granted',username,{'write':bool(write)})

    def revoke(self,p,business,username):
        self.access(p,business,owner=True)
        self.db.execute('DELETE FROM portfolio_acl WHERE business_id=? AND username=?',(business,username))
        self.event(p,business,'access_revoked',username)

    def halted(self): return bool(self.db.execute('SELECT halted FROM portfolio_controls WHERE id=1').fetchone()[0])

    def halt(self,p,halted):
        self.access(p,owner=True)
        self.db.execute('UPDATE portfolio_controls SET halted=? WHERE id=1',(int(bool(halted)),))
        self.event(p,None,'emergency_stop' if halted else 'emergency_resumed')

    def writable(self,p,business):
        self.access(p,business,True)
        if self.halted(): raise ValueError('Portfolio emergency stop')
        state=self.db.execute('SELECT status FROM portfolio_businesses WHERE id=?',(business,)).fetchone()[0]
        if state in ('paused','closed'): raise ValueError('Business is paused or closed')

    def contact(self,p,business,name,channel_ref):
        self.writable(p,business)
        identifier=uuid4().hex
        self.db.execute('INSERT INTO portfolio_contacts VALUES(?,?,?,?,?,?)',(business,identifier,text(name,100,True),text(channel_ref,160,True),'active',now().isoformat()))
        self.event(p,business,'contact_created',identifier)
        return identifier

    def consent(self,p,business,contact,purpose,granted,notice):
        self.writable(p,business)
        row=self.row(p,business,'portfolio_contacts',contact,True)
        if row['state']!='active' or purpose not in ('contact','schedule','photo','followup','identity_link') or type(granted) is not bool: raise ValueError('Active contact and explicit purpose required')
        self.db.execute('INSERT INTO portfolio_consents(business_id,contact_id,purpose,granted,notice,created_at) VALUES(?,?,?,?,?,?)',(business,contact,purpose,int(granted),text(notice,200),now().isoformat()))
        self.event(p,business,'consent_granted' if granted else 'consent_withdrawn',contact,{'purpose':purpose})
        if not granted:
            self.db.execute("UPDATE portfolio_operations SET status='approval_pending' WHERE business_id=? AND status='approved' AND case_id IN(SELECT id FROM portfolio_cases WHERE business_id=? AND contact_id=?)",(business,business,contact))
            if purpose=='identity_link' and self.db.execute("SELECT 1 FROM sqlite_master WHERE name='scm_identity_links'").fetchone():
                self.db.execute('UPDATE scm_identity_links SET active=0 WHERE business_id=? AND contact_id=?',(business,contact))
                self.event(p,business,'identity_link_withdrawn',contact)

    def has_consent(self,business,contact,purpose):
        if purpose in ('contact','schedule','followup') and self.db.execute("SELECT 1 FROM sqlite_master WHERE name='scm_customers'").fetchone():
            stop=self.db.execute('SELECT stopped FROM scm_customers WHERE business_id=? AND contact_id=?',(business,contact)).fetchone()
            if stop and stop[0]: return False
        row=self.db.execute('SELECT c.granted FROM portfolio_consents c JOIN portfolio_contacts p ON p.business_id=c.business_id AND p.id=c.contact_id WHERE c.business_id=? AND c.contact_id=? AND c.purpose=? AND p.state=? ORDER BY c.id DESC LIMIT 1',(business,contact,purpose,'active')).fetchone()
        return bool(row and row[0])

    def case(self,p,business,key,kind,subject,contact=None,start=None,end=None):
        self.writable(p,business)
        if kind not in CASE_KINDS or not key or len(key)>120: raise ValueError('Case kind/key required')
        subject=text(subject,180)
        if contact and self.row(p,business,'portfolio_contacts',contact,True)['state']!='active': raise ValueError('Active contact required')
        if bool(start)!=bool(end): raise ValueError('Both appointment times required')
        if start:
            from .seminars import utc
            start,end=utc(start),utc(end)
            if start>=end: raise ValueError('Valid appointment interval required')
        def op():
            old=self.db.execute('SELECT * FROM portfolio_cases WHERE business_id=? AND entry_key=?',(business,key)).fetchone()
            if old:
                if [old[k] for k in ('kind','subject','contact_id','start_at','end_at')]!=[kind,subject,contact,start,end]: raise ValueError('Case key conflict')
                return old['id']
            identifier=uuid4().hex
            self.db.execute('INSERT INTO portfolio_cases VALUES(?,?,?,?,?,?,?,?,?,?)',(business,identifier,key,contact,kind,subject,'requested',start,end,now().isoformat()))
            self.event(p,business,'case_created',identifier,{'kind':kind})
            return identifier
        return self.store._transaction(op)

    def document(self,p,business,kind,case=None,body=None,parent=None):
        self.writable(p,business)
        settings=self.db.execute('SELECT * FROM portfolio_businesses WHERE id=?',(business,)).fetchone()
        if kind not in DOCS[settings['kind']]: raise ValueError('Document type belongs to another business activity')
        if case: self.row(p,business,'portfolio_cases',case,True)
        if parent: self.row(p,business,'portfolio_documents',parent,True)
        if body is None:
            body={'title':DOCS[settings['kind']][kind],'service':settings['services'] or '提供内容は代表確認待ち','pricing':settings['pricing'] or '料金は代表確認待ち','region':settings['region'] or '対応地域は代表確認待ち','offering':STATES[settings['status']],'offering_confirmed':bool(settings['offering_confirmed']),'cta':'ご希望の場合のみ、内容・条件をご確認のうえお問い合わせください。','mode':'local_template_draft'}
            if settings['kind']=='health': body['copy']=BUSINESS_COPY; body['checks']='公式規約・特商法・景表法・薬機法を代表が確認。収入保証、健康効果の断定、無断勧誘は禁止。'
            if settings['kind']=='cleaning': body['checks']='施工範囲・素材への影響・写真利用同意・追加費用・日時を確認。料金/地域の候補は未確定のまま外部案内しない。'
            if settings['kind']=='web': body['items']=['目的・対象','ページ/LINE機能','素材・権利','納期・見積条件','セキュリティ','納品前チェック','保守範囲・更新条件']; body['checks']='見積送信・契約・納品・公開には個別の代表承認が必要。'
            if settings['kind']=='whitening': body['checks']='提供範囲・利用条件・料金を代表が確認。健康効果や結果を保証せず、医療判断は有資格者へ案内。'
            if settings['kind']=='education': body['checks']='受講範囲・日時・料金を確認。端末の暗証番号・アカウント認証情報は保存しない。'
            if settings['kind']=='life': body['items']=['本人の希望','相談の範囲と同意','日時・料金','任意のフォロー']; body['checks']='センシティブな相談本文は記録しない。医療・法律・金融の専門助言は行わず、有資格者へ案内。'
        encoded=encode(body); text(encoded,12000)
        identifier=uuid4().hex
        self.db.execute('INSERT INTO portfolio_documents VALUES(?,?,?,?,?,?,?,?,?,?)',(business,identifier,parent,case,kind,encoded,digest(encoded),settings['revision'],'approval_pending',now().isoformat()))
        if parent:
            self.db.execute("UPDATE portfolio_documents SET status='superseded' WHERE business_id=? AND id=?",(business,parent))
            self.db.execute("UPDATE portfolio_operations SET status='approval_pending' WHERE business_id=? AND document_id=? AND status='approved'",(business,parent))
        self.event(p,business,'document_created',identifier,{'mode':'local_template'})
        return identifier

    def decide_document(self,p,business,identifier,decision,checked=False):
        self.access(p,business,owner=True)
        row=self.row(p,business,'portfolio_documents',identifier)
        revision=self.db.execute('SELECT revision FROM portfolio_businesses WHERE id=?',(business,)).fetchone()[0]
        if row['status']!='approval_pending' or decision not in ('approve','reject','changes_requested'): raise ValueError('Pending document required')
        if decision=='approve' and (not checked or risky(row['body']) or digest(row['body'])!=row['digest'] or row['business_revision']!=revision): raise ValueError('Current checked and unchanged draft required')
        self.db.execute('UPDATE portfolio_documents SET status=? WHERE business_id=? AND id=?',({'approve':'approved','reject':'rejected','changes_requested':'changes_requested'}[decision],business,identifier))
        self.event(p,business,'document_'+decision,identifier,{'digest':row['digest'],'checked':bool(checked)})

    def operation(self,p,business,key,kind,case=None,document=None):
        self.writable(p,business)
        if kind not in OPERATIONS or not key or len(key)>120: raise ValueError('Known operation/key required')
        if case: self.row(p,business,'portfolio_cases',case,True)
        if document:
            doc=self.row(p,business,'portfolio_documents',document,True)
            if doc['case_id'] and doc['case_id']!=case: raise ValueError('Document is for another case')
            if kind=='quote_send' and doc['kind']!='estimate': raise ValueError('Estimate draft required')
        if kind not in ('booking_confirm','contract') and not document: raise ValueError('Draft required')
        if kind in ('booking_confirm','contract','contact','quote_send') and not case: raise ValueError('Scoped case required')
        fingerprint=digest([business,kind,case,document])
        old=self.db.execute('SELECT * FROM portfolio_operations WHERE business_id=? AND entry_key=?',(business,key)).fetchone()
        if old:
            if old['payload_digest']!=fingerprint: raise ValueError('Operation key conflict')
            return old['id']
        identifier=uuid4().hex
        self.db.execute('INSERT INTO portfolio_operations VALUES(?,?,?,?,?,?,?,?,?,?)',(business,identifier,key,kind,case,document,fingerprint,'approval_pending',None,now().isoformat()))
        self.event(p,business,'operation_proposed',identifier)
        return identifier

    def eligible_operation(self,p,business,row):
        self.writable(p,business)
        if digest([business,row['kind'],row['case_id'],row['document_id']])!=row['payload_digest']: raise ValueError('Operation changed')
        if row['document_id']:
            doc=self.row(p,business,'portfolio_documents',row['document_id'])
            revision=self.db.execute('SELECT revision FROM portfolio_businesses WHERE id=?',(business,)).fetchone()[0]
            approval=self.db.execute("SELECT detail FROM portfolio_events WHERE business_id=? AND ref=? AND event='document_approve' ORDER BY id DESC LIMIT 1",(business,doc['id'])).fetchone()
            if doc['status']!='approved' or digest(doc['body'])!=doc['digest'] or doc['business_revision']!=revision or not approval or json.loads(approval[0]).get('digest')!=doc['digest']: raise ValueError('Approved current document required')
        if row['case_id']:
            case=self.row(p,business,'portfolio_cases',row['case_id'])
            if case['status']=='cancelled': raise ValueError('Case is cancelled')
            if row['kind'] in ('booking_confirm','contact','quote_send','contract'):
                purpose='schedule' if row['kind']=='booking_confirm' else 'contact'
                if not case['contact_id'] or not self.has_consent(business,case['contact_id'],purpose): raise ValueError('Purpose consent required')

    def decide_operation(self,p,business,identifier,decision,checked=False):
        self.access(p,business,owner=True)
        row=self.row(p,business,'portfolio_operations',identifier)
        if row['status']!='approval_pending' or decision not in ('approve','reject'): raise ValueError('Pending operation required')
        if decision=='approve':
            if not checked: raise ValueError('Representative review required')
            self.eligible_operation(p,business,row)
        self.db.execute('UPDATE portfolio_operations SET status=? WHERE business_id=? AND id=?',('approved' if decision=='approve' else 'rejected',business,identifier))
        self.event(p,business,'operation_'+decision,identifier,{'digest':row['payload_digest']})

    def record_manual(self,p,business,identifier,evidence):
        self.access(p,business,owner=True)
        def op():
            row=self.row(p,business,'portfolio_operations',identifier)
            if row['status']!='approved': raise ValueError('Explicit approval required')
            approved=self.db.execute("SELECT detail FROM portfolio_events WHERE business_id=? AND ref=? AND event='operation_approve' ORDER BY id DESC LIMIT 1",(business,identifier)).fetchone()
            if not approved or json.loads(approved[0]).get('digest')!=row['payload_digest']: raise ValueError('Approval digest mismatch')
            self.eligible_operation(p,business,row)
            # This records a human statement; never calls any external service.
            if row['kind']=='booking_confirm':
                case=self.row(p,business,'portfolio_cases',row['case_id'])
                if not case['start_at'] or not case['end_at']: raise ValueError('Appointment interval required')
                conflict=self.db.execute("SELECT 1 FROM portfolio_cases WHERE status='scheduled_local' AND start_at<? AND end_at>? AND NOT(business_id=? AND id=?) LIMIT 1",(case['end_at'],case['start_at'],business,case['id'])).fetchone()
                if conflict: raise ValueError('Representative time is unavailable')
                self.db.execute("UPDATE portfolio_cases SET status='scheduled_local' WHERE business_id=? AND id=?",(business,case['id']))
            self.db.execute("UPDATE portfolio_operations SET status='recorded_manual',evidence=? WHERE business_id=? AND id=?",(text(evidence,200),business,identifier))
            self.event(p,business,'manual_record_external_unverified',identifier)
        self.store._transaction(op)

    def task(self,p,business,key,kind,instruction,priority=3,agent=None):
        self.writable(p,business)
        instruction=text(instruction,2000)
        if kind not in CANDIDATES or not key or len(key)>120 or not 1<=int(priority)<=5: raise ValueError('Known work and priority required')
        def op():
            choices=CANDIDATES[kind]
            loads={a:self.db.execute("SELECT COUNT(*) FROM tasks WHERE agent=? AND status IN ('pending','running')",(a,)).fetchone()[0] for a in choices}
            selected=agent or min(choices,key=lambda a:(loads[a],choices.index(a)))
            if selected not in choices: raise ValueError('Agent expertise mismatch')
            # Stable task keys do not depend on changes in shared workload.
            task_key='portfolio:'+digest([business,key])
            old=self.db.execute('SELECT t.*,p.kind,p.instruction,p.priority FROM tasks t JOIN portfolio_tasks p ON p.task_id=t.id WHERE t.dedupe_key=?',(task_key,)).fetchone()
            if old:
                if [old['kind'],old['instruction'],old['priority']]!=[kind,instruction,int(priority)] or agent and old['agent']!=agent: raise ValueError('Business task key conflict')
                return old['id']
            task=self.store.create_task(task_key,selected,{'scope':'portfolio','reference':uuid4().hex,'mode':'scoped_local_only'},action='management_decision')
            self.db.execute('INSERT INTO portfolio_tasks VALUES(?,?,?,?,?,?)',(business,task,kind,instruction,int(priority),now().isoformat()))
            self.db.execute('INSERT INTO portfolio_assignments(business_id,task_id,agent,reason,actor,created_at) VALUES(?,?,?,?,?,?)',(business,task,selected,'専門分野内の共通タスク負荷='+str(loads[selected])+'。外部権限付与なし。',p.identity,now().isoformat()))
            self.event(p,business,'task_assigned',task,{'agent':selected,'priority':int(priority)})
            return task
        return self.store._transaction(op)

    def task_control(self,p,business,task,action,priority=None):
        self.access(p,business,True)
        def op():
            row=self.db.execute('SELECT * FROM portfolio_tasks WHERE business_id=? AND task_id=?',(business,task)).fetchone()
            if not row: raise ValueError('Scoped task required')
            current=self.db.execute('SELECT * FROM tasks WHERE id=?',(task,)).fetchone()
            if action=='priority':
                if not 1<=int(priority)<=5: raise ValueError('Priority 1..5')
                self.db.execute('UPDATE portfolio_tasks SET priority=? WHERE business_id=? AND task_id=?',(int(priority),business,task))
            elif action in ('pause','resume'):
                if current['status'] not in ('pending','paused'): raise ValueError('Only unstarted tasks can be paused/resumed')
                self.db.execute('UPDATE tasks SET status=? WHERE id=?',('paused' if action=='pause' else 'pending',task))
            else: raise ValueError('Unknown task control')
            self.event(p,business,'task_'+action,task)
        self.store._transaction(op)

    def run(self,p,business,task):
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='scm_work'").fetchone() and self.db.execute('SELECT 1 FROM scm_work WHERE task_id=?',(task,)).fetchone(): raise ValueError('Use scoped sales task controls')
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='bd_tasks'").fetchone() and self.db.execute('SELECT 1 FROM bd_tasks WHERE task_id=?',(task,)).fetchone(): raise ValueError('Use approved development plan controls')
        self.writable(p,business)
        def op():
            row=self.db.execute('SELECT * FROM portfolio_tasks WHERE business_id=? AND task_id=?',(business,task)).fetchone()
            if not row: raise ValueError('Scoped task required')
            existing=self.db.execute('SELECT * FROM portfolio_results WHERE business_id=? AND task_id=?',(business,task)).fetchone()
            if existing: return json.loads(existing['body'])
            start=time.monotonic()
            run=self.store.claim(task)
            settings=self.db.execute('SELECT * FROM portfolio_businesses WHERE id=?',(business,)).fetchone()
            content={'work':row['kind'],'instruction':row['instruction'],'mode':'local_rules_not_llm','review':'代表・別担当確認待ち','external_execution':False}
            if row['kind'] in ('sns','line','referral','copy'):
                supported=DOCS[settings['kind']]
                desired={'sns':'sns','line':'line','referral':'referral','copy':'reply'}[row['kind']]
                kind=desired if desired in supported else 'listing' if 'listing' in supported else 'proposal' if 'proposal' in supported else 'reply'
                document=self.document(p,business,kind)
                content['document_id']=document
                content['result']='この事業の設定だけで下書きを作成。個別の品質確認・承認待ち。'
            elif row['kind'] in ('analysis','management'):
                today=now().astimezone(JST).date()
                content['analysis']=self.summary(p,business,today.replace(day=1).isoformat(),(today+timedelta(days=1)).isoformat())
                content['result']='計測範囲・不足データ・目標との差を確認。改善案は仮説で、自動適用しない。'
            elif row['kind'] in ('audit','review'):
                content['checks']=[{'document_id':d['id'],'blockers':['危険表現または外部命令の疑い'] if risky(d['body']) else ['事実・法令・公式規約の確認は人の判断が必要']} for d in self.db.execute("SELECT id,body FROM portfolio_documents WHERE business_id=? AND status='approval_pending' LIMIT 100",(business,))]
                content['result']='機械的なチェックだけで承認済みにしない。'
            else:
                content['plan']=['提供範囲・料金・地域の確認','本人の希望と目的別同意の確認','見積・日程・条件の下書き','代表への確認依頼']
                content['result']='事業の範囲内で業務手順案を作成。外部調査・実送信は未実施。'
            self.db.execute('INSERT INTO portfolio_results VALUES(?,?,?,?,?)',(business,task,encode(content),time.monotonic()-start,now().isoformat()))
            self.store.finish(run,'事業別成果物は権限付きの事業画面で確認。機密・顧客情報を共通成果物へ複製しない。')
            self.event(p,business,'task_completed_locally',task,{'mode':'local_rules'})
            return content
        return self.store._transaction(op)

    def budget(self,p,business,month,amount):
        self.access(p,business,owner=True)
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month) or type(amount) is not int or amount<0: raise ValueError('Month and integer microUSD limit required')
        self.db.execute('INSERT INTO portfolio_budgets VALUES(?,?,?) ON CONFLICT(business_id,month) DO UPDATE SET limit_microusd=excluded.limit_microusd',(business,month,amount))
        self.event(p,business,'budget_set',month,{'microusd':amount})

    def cost(self,p,business,key,task,model,amount,kind):
        self.access(p,business,owner=True)
        if type(amount) is not int or amount<0 or kind not in ('confirmed','estimated'): raise ValueError('Explicit accounting type and amount required')
        record=self.db.execute('SELECT t.agent FROM tasks t JOIN portfolio_tasks p ON p.task_id=t.id WHERE p.business_id=? AND t.id=?',(business,task)).fetchone()
        if not record: raise ValueError('Task belongs to another business')
        model=text(model,80)
        def op():
            entry='portfolio:'+digest([business,key])
            old=self.db.execute('SELECT * FROM office_costs WHERE entry_key=?',(entry,)).fetchone()
            if old:
                if [old[k] for k in ('task_id','amount','kind','model')]!=[task,amount,kind,model]: raise ValueError('Cost key conflict')
                return old['id']
            identifier=uuid4().hex
            self.db.execute('INSERT INTO office_costs VALUES(?,?,?,?,?,?,?,?,?,?)',(identifier,entry,model,record['agent'],task,amount,kind,'manual',now().astimezone(JST).date().isoformat(),'事業別配賦。請求APIとの照合は未実施。'))
            self.db.execute('INSERT INTO portfolio_cost_links VALUES(?,?,?)',(identifier,business,task))
            self.event(p,business,'cost_allocated',identifier,{'kind':kind,'mode':'manual'})
            return identifier
        return self.store._transaction(op)

    def ledger(self,p,business,key,day,kind,amount,mode,evidence):
        self.access(p,business,True)
        date.fromisoformat(day)
        if type(amount) is not int or amount<0 or kind not in ('revenue','expense') or mode not in ('actual','estimated') or mode=='actual' and day>now().astimezone(JST).date().isoformat(): raise ValueError('Money, mode and date required')
        evidence=text(evidence,200)
        def op():
            old=self.db.execute('SELECT * FROM portfolio_ledger WHERE business_id=? AND entry_key=?',(business,key)).fetchone()
            if old:
                if [old[k] for k in ('day','kind','amount_yen','mode','evidence')]!=[day,kind,amount,mode,evidence]: raise ValueError('Ledger key conflict')
                return old['id']
            identifier=uuid4().hex
            self.db.execute('INSERT INTO portfolio_ledger VALUES(?,?,?,?,?,?,?,?,?)',(business,identifier,key,day,kind,amount,mode,evidence,now().isoformat()))
            self.event(p,business,'ledger_recorded',identifier,{'mode':mode})
            return identifier
        return self.store._transaction(op)

    def measure(self,p,business,day,metric,value,mode,evidence,previous=None):
        self.access(p,business,True)
        date.fromisoformat(day)
        if metric not in METRICS or type(value) is not int or value<0 or mode not in ('actual','estimated') or mode=='actual' and day>now().astimezone(JST).date().isoformat(): raise ValueError('Metric, value and date required')
        def op():
            old=self.db.execute('SELECT * FROM portfolio_measures WHERE business_id=? AND day=? AND metric=? AND mode=? ORDER BY rowid DESC LIMIT 1',(business,day,metric,mode)).fetchone()
            if (old['id'] if old else None)!=previous: raise ValueError('Latest metric revision required')
            identifier=uuid4().hex
            self.db.execute('INSERT INTO portfolio_measures VALUES(?,?,?,?,?,?,?,?,?)',(business,identifier,day,metric,value,mode,text(evidence,200),previous,now().isoformat()))
            self.event(p,business,'measure_recorded',identifier,{'mode':mode})
            return identifier
        return self.store._transaction(op)

    def goal(self,p,business,month,metric,value):
        self.access(p,business,owner=True)
        if metric not in METRICS or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month) or value is not None and (type(value) is not int or value<0): raise ValueError('Goal must be explicit or unset')
        self.db.execute('INSERT INTO portfolio_goals VALUES(?,?,?,?) ON CONFLICT(business_id,month,metric) DO UPDATE SET value=excluded.value',(business,month,metric,value))
        self.event(p,business,'goal_set',month,{'metric':metric,'value':value})

    def summary(self,p,business,start,end):
        self.access(p,business)
        a,b=period(start,end); days=(b-a).days
        settings=dict(self.db.execute('SELECT * FROM portfolio_businesses WHERE id=?',(business,)).fetchone())
        data={'business_id':business,'name':settings['name'],'status':settings['status'],'period':[start,end],'measurement':'同一期間・手動実績申告/部分計測。実API未照合。','metrics':{},'mode':'local_manual_records'}
        for metric in METRICS:
            rows=self.db.execute("SELECT m.* FROM portfolio_measures m WHERE m.business_id=? AND m.day>=? AND m.day<? AND m.metric=? AND m.mode='actual' AND NOT EXISTS(SELECT 1 FROM portfolio_measures n WHERE n.business_id=m.business_id AND n.day=m.day AND n.metric=m.metric AND n.mode=m.mode AND n.rowid>m.rowid)",(business,start,end,metric)).fetchall()
            data['metrics'][metric]={'recorded':sum(r['value'] for r in rows) if rows else None,'days':len(rows),'complete':len(rows)==days,'total':sum(r['value'] for r in rows) if len(rows)==days else None}
        for kind in ('revenue','expense'):
            rows=self.db.execute("SELECT amount_yen FROM portfolio_ledger WHERE business_id=? AND day>=? AND day<? AND kind=? AND mode='actual'",(business,start,end,kind)).fetchall()
            data[kind+'_yen']=sum(r[0] for r in rows) if rows else None
        data['profit_yen']=data['revenue_yen']-data['expense_yen'] if data['revenue_yen'] is not None and data['expense_yen'] is not None else None
        data['profit_margin_percent']=round(data['profit_yen']/data['revenue_yen']*100,2) if data['profit_yen'] is not None and data['revenue_yen'] else None
        # USD is never silently added to JPY profit. No exchange rate is invented.
        data['api_microusd']={kind:self.db.execute("SELECT SUM(c.amount) FROM portfolio_cost_links l JOIN office_costs c ON c.id=l.cost_id WHERE l.business_id=? AND c.incurred_date>=? AND c.incurred_date<? AND c.kind=? AND c.mode='manual'",(business,start,end,kind)).fetchone()[0] for kind in ('confirmed','estimated')}
        lower=datetime.combine(a,datetime.min.time(),JST).astimezone(timezone.utc).isoformat()
        upper=datetime.combine(b,datetime.min.time(),JST).astimezone(timezone.utc).isoformat()
        data['local_processing_seconds']=self.db.execute('SELECT SUM(elapsed_seconds) FROM portfolio_results WHERE business_id=? AND created_at>=? AND created_at<?',(business,lower,upper)).fetchone()[0]
        data['registered_inquiries']=self.db.execute("SELECT COUNT(*) FROM portfolio_cases WHERE business_id=? AND kind='inquiry' AND created_at>=? AND created_at<?",(business,lower,upper)).fetchone()[0] or None
        data['registered_booking_requests']=self.db.execute("SELECT COUNT(*) FROM portfolio_cases WHERE business_id=? AND kind='booking_request' AND created_at>=? AND created_at<?",(business,lower,upper)).fetchone()[0] or None
        for action,key in [('booking_confirm','manual_booking_records'),('contract','manual_contract_records')]:
            data[key]=self.db.execute("SELECT COUNT(*) FROM portfolio_events e JOIN portfolio_operations o ON o.business_id=e.business_id AND o.id=e.ref WHERE e.business_id=? AND e.event='manual_record_external_unverified' AND o.kind=? AND e.created_at>=? AND e.created_at<?",(business,action,lower,upper)).fetchone()[0] or None
        data['goals']=dict(self.db.execute('SELECT metric,value FROM portfolio_goals WHERE business_id=? AND month=?',(business,start[:7]))) if start[8:]=='01' and end==(a.replace(day=28)+timedelta(days=4)).replace(day=1).isoformat() else {}
        data['goal_gaps']={metric:(data['metrics'][metric]['total']-target if target is not None and data['metrics'][metric]['complete'] else None) for metric,target in data['goals'].items()}
        return data

    def dashboard(self,p,start,end):
        self.access(p,owner=True); period(start,end)
        rows=[self.summary(p,r[0],start,end) for r in self.db.execute('SELECT id FROM portfolio_businesses ORDER BY priority,id')]
        total=lambda key:sum(r[key] for r in rows) if rows and all(r[key] is not None for r in rows) else None
        return {'period':[start,end],'businesses':rows,'revenue_yen':total('revenue_yen'),'expense_yen':total('expense_yen'),'profit_yen':total('profit_yen'),'total_note':'全事業に記録がある場合だけ記録済み額を合算。期間全体の完全計測を保証しない。','today_priority':[dict(r) for r in self.db.execute("SELECT p.business_id,p.task_id,p.kind,p.priority,t.agent,t.status FROM portfolio_tasks p JOIN tasks t ON t.id=p.task_id JOIN portfolio_businesses b ON b.id=p.business_id WHERE t.status IN ('pending','running','approval_pending') ORDER BY b.priority,p.priority,p.created_at LIMIT 50")],'unassigned_legacy':'第1〜9段階のデータは事業未配賦。自動集計・コピーしない。'}

    def report(self,p,start,end):
        self.access(p,owner=True)
        data=self.dashboard(p,start,end)
        data['owner']='AI01（ルールによるローカル集計、実LLMではない）'
        data['improvements']=[{'business_id':r['business_id'],'proposal':'欠測項目の計測範囲・問い合わせから予約までの手順を確認し、本人の希望を尊重する下書きを改善する。','reason':'取得できる手動記録のみで因果関係を断定しない。','validation':'同じ定義・期間で比較。未計測を0にしない。','status':'approval_pending'} for r in data['businesses']]
        data['approvals']=[dict(r) for r in self.db.execute("SELECT business_id,id,kind FROM portfolio_operations WHERE status='approval_pending' LIMIT 100")]
        identifier=uuid4().hex; encoded=encode(data)
        self.db.execute('INSERT INTO portfolio_reports VALUES(?,?,?,?,?,?,?)',(identifier,start,end,encoded,digest(encoded),'approval_pending',now().isoformat()))
        self.event(p,None,'meeting_report_prepared',identifier)
        return identifier

    def decide_report(self,p,identifier,decision):
        self.access(p,owner=True)
        row=self.db.execute('SELECT * FROM portfolio_reports WHERE id=?',(identifier,)).fetchone()
        if not row or row['status']!='approval_pending' or decision not in ('approve','reject') or digest(row['body'])!=row['digest']: raise ValueError('Unchanged pending report required')
        self.db.execute('UPDATE portfolio_reports SET status=? WHERE id=?',('approved' if decision=='approve' else 'rejected',identifier))
        self.event(p,None,'meeting_report_'+decision,identifier)
        # Approval records review only; recommendations are never applied here.

    def request_delete(self,p,business,contact):
        self.access(p,business,owner=True); self.row(p,business,'portfolio_contacts',contact)
        self.db.execute('INSERT INTO portfolio_deletions VALUES(?,?,?,?) ON CONFLICT(business_id,contact_id) DO NOTHING',(business,contact,'approval_pending',now().isoformat()))
        self.event(p,business,'contact_redaction_requested',contact)

    def approve_delete(self,p,business,contact,retention_checked):
        self.access(p,business,owner=True)
        if not retention_checked: raise ValueError('Retention and backup review required')
        def op():
            row=self.db.execute('SELECT * FROM portfolio_deletions WHERE business_id=? AND contact_id=?',(business,contact)).fetchone()
            if not row or row['status']!='approval_pending': raise ValueError('Separate deletion request required')
            self.db.execute("UPDATE portfolio_contacts SET display_name='削除済み',channel_ref='',state='redacted' WHERE business_id=? AND id=?",(business,contact))
            self.db.execute("UPDATE portfolio_media SET content=NULL,state='redacted' WHERE business_id=? AND case_id IN(SELECT id FROM portfolio_cases WHERE business_id=? AND contact_id=?)",(business,business,contact))
            self.db.execute("UPDATE portfolio_operations SET status='rejected' WHERE business_id=? AND status IN ('approved','approval_pending') AND case_id IN(SELECT id FROM portfolio_cases WHERE business_id=? AND contact_id=?)",(business,business,contact))
            self.db.execute("UPDATE portfolio_deletions SET status='redacted' WHERE business_id=? AND contact_id=?",(business,contact))
            from .sales import redact_contact
            redact_contact(self.store,business,contact)
            self.event(p,business,'contact_redacted',contact,{'backup_erasure':'別途保管先の確認が必要'})
        self.store._transaction(op)


    def progress(self,p,business,case,status):
        self.writable(p,business)
        old=self.row(p,business,'portfolio_cases',case,True)
        if status not in ('in_progress','completed','cancelled') or old['kind']=='booking_request' and status!='cancelled': raise ValueError('Use separately approved booking operations')
        self.db.execute('UPDATE portfolio_cases SET status=? WHERE business_id=? AND id=?',(status,business,case))
        self.event(p,business,'case_status_changed',case,{'before':old['status'],'after':status})
        if status=='cancelled': self.db.execute("UPDATE portfolio_operations SET status='rejected' WHERE business_id=? AND case_id=? AND status IN ('approved','approval_pending')",(business,case))

    def photo(self,p,business,case,stage,blob):
        self.writable(p,business)
        row=self.row(p,business,'portfolio_cases',case,True)
        if stage not in ('before','after') or not row['contact_id'] or not self.has_consent(business,row['contact_id'],'photo'): raise ValueError('Photo stage and consent required')
        from .portfolio_media import validate_photo
        mime=validate_photo(blob)
        import hashlib
        identifier=uuid4().hex
        self.db.execute('INSERT INTO portfolio_media VALUES(?,?,?,?,?,?,?,?,?)',(business,identifier,case,stage,mime,blob,hashlib.sha256(blob).hexdigest(),'private',now().isoformat()))
        self.event(p,business,'private_photo_saved',identifier,{'stage':stage})
        return identifier

    def photo_bytes(self,p,business,identifier):
        row=self.row(p,business,'portfolio_media',identifier)
        case=self.row(p,business,'portfolio_cases',row['case_id'])
        if row['state']!='private' or row['content'] is None or not self.has_consent(business,case['contact_id'],'photo'): raise ValueError('Photo unavailable or consent withdrawn')
        return row['mime'],bytes(row['content'])

    def link_seminar(self,p,business,identifier):
        self.access(p,business,owner=True)
        if not self.db.execute('SELECT 1 FROM seminars WHERE id=?',(identifier,)).fetchone(): raise ValueError('Existing seminar required')
        old=self.db.execute('SELECT business_id FROM portfolio_seminar_links WHERE seminar_id=?',(identifier,)).fetchone()
        if old:
            if old[0]!=business: raise ValueError('Seminar already linked elsewhere; representative review required')
            return
        self.db.execute('INSERT INTO portfolio_seminar_links VALUES(?,?,?,?)',(identifier,business,p.identity,now().isoformat()))
        self.event(p,business,'seminar_explicitly_linked',identifier)


def guard_claim(store,task,reserve):
    """Additional gate for mapped tasks only; legacy task semantics remain unchanged."""
    db=store.db
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='portfolio_tasks'").fetchone(): return
    row=db.execute('SELECT business_id FROM portfolio_tasks WHERE task_id=?',(task,)).fetchone()
    if not row: return
    business=row[0]
    if db.execute('SELECT halted FROM portfolio_controls').fetchone()[0] or db.execute('SELECT status FROM portfolio_businesses WHERE id=?',(business,)).fetchone()[0] in ('paused','closed'): raise ValueError('Business work is stopped')
    from zoneinfo import ZoneInfo
    month=now().astimezone(ZoneInfo('Asia/Tokyo')).strftime('%Y-%m')
    cap=db.execute('SELECT limit_microusd FROM portfolio_budgets WHERE business_id=? AND month=?',(business,month)).fetchone()
    limit=cap[0] if cap else 0
    used=db.execute("SELECT COALESCE(SUM(c.amount),0) FROM portfolio_cost_links l JOIN office_costs c ON c.id=l.cost_id WHERE l.business_id=? AND substr(c.incurred_date,1,7)=? AND c.mode='manual'",(business,month)).fetchone()[0]
    held=db.execute("SELECT COALESCE(SUM(COALESCE(r.actual,r.reserved)),0) FROM reservations r JOIN runs u ON u.id=r.run_id JOIN portfolio_tasks p ON p.task_id=u.task_id JOIN office_month_reservations m ON m.run_id=u.id WHERE p.business_id=? AND m.month=?",(business,month)).fetchone()[0]
    if reserve+used+held>limit or used+held>=limit and used+held>0: raise ValueError('Business API budget reached')
