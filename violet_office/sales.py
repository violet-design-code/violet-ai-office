"""Scoped sales ledger; mock transport and explicit human statements only."""
import json
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from uuid import uuid4
from .store import now
from .seminars import JST,utc
from .portfolio import text,period
from .content import encode,digest
from .learning import risky
from .sales_finance import quote_totals,TAX,ROUNDING
from .development_finance import number

STAGES={'inquiry':'問い合わせ','prospect':'見込み客','proposal':'提案準備','quote':'見積もり','candidate':'予約候補','booked':'予約確定記録','provided':'サービス提供記録','invoiced':'請求記録','paid':'入金確認記録','followup':'フォロー'}
STATES={'active':'対応中','hold':'保留','lost':'失注','cancelled':'キャンセル'}
ACTIVITIES={'classify':('AI17',),'reply':('AI17','AI24'),'proposal':('AI16','AI17'),'estimate':('AI27',),'booking':('AI23',),'followup':('AI24',),'unhandled':('AI14',),'analysis':('AI27',),'improvement':('AI06','AI27'),'quality':('AI28',)}
ACTIONS={'send':'連絡/見積送信','booking_confirm':'予約確定','contract':'契約成立記録','provided':'サービス提供確認','invoice':'請求確認','payment':'入金確認','charge':'課金','refund':'全額返金','void_invoice':'未入金請求の取消','publish':'公開','cancel_booking':'予約取消','cancel_contract':'契約終了確認','delete':'顧客情報の論理消去'}


def migrate(store):
    if store.db.execute('SELECT 1 FROM office_migrations WHERE version=12').fetchone(): return
    sql=Path(__file__).resolve().parent.parent.joinpath('migrations/012_sales_customer.sql').read_text()
    def op():
        for statement in sql.split(';'):
            if statement.strip(): store.db.execute(statement)
        store.db.execute('INSERT OR IGNORE INTO office_migrations VALUES(12,?)',(now().isoformat(),))
    store._transaction(op)


class Sales:
    def __init__(self,store,portfolio): self.store,self.db,self.portfolio=store,store.db,portfolio

    def access(self,p,business,write=False,owner=False): self.portfolio.access(p,business,write,owner)
    def writable(self,p,business): self.portfolio.writable(p,business)
    def row(self,p,business,table,identifier):
        self.access(p,business)
        if table not in {'scm_deals','scm_quotes','scm_bookings','scm_actions','scm_invoices'}: raise ValueError('Known scoped resource required')
        row=self.db.execute('SELECT * FROM '+table+' WHERE business_id=? AND id=?',(business,identifier)).fetchone()
        if not row: raise ValueError('Resource missing or outside business')
        return row
    def event(self,p,business,event,ref='',deal=None,detail=None):
        self.db.execute('INSERT INTO scm_events(business_id,deal_id,actor,event,ref,detail,created_at) VALUES(?,?,?,?,?,?,?)',(business,deal,p.identity,event,ref,encode(detail or {}),now().isoformat()))

    def customer(self,p,business,name,channel):
        c=self.portfolio.contact(p,business,name,channel)
        self.db.execute('INSERT INTO scm_customers VALUES(?,?,0,1)',(business,c)); return c
    def update_customer(self,p,business,contact,name,channel,verified):
        self.access(p,business,owner=True)
        if not verified: raise ValueError('Same-person correction must be verified')
        old=self.portfolio.row(p,business,'portfolio_contacts',contact)
        if old['state']!='active': raise ValueError('Redacted contact cannot be recreated')
        name,channel=text(name,100,True),text(channel,160,True)
        def op():
            self.db.execute('UPDATE portfolio_contacts SET display_name=?,channel_ref=? WHERE business_id=? AND id=?',(name,channel,business,contact))
            self.stop(p,business,contact,True)
            for purpose in ('contact','schedule','followup','identity_link'):
                self.db.execute('INSERT INTO portfolio_consents(business_id,contact_id,purpose,granted,notice,created_at) VALUES(?,?,?,0,?,?)',(business,contact,purpose,'同一本人の連絡先訂正。再同意と停止解除を要する。',now().isoformat()))
            self.unlink(p,business,contact)
            self.event(p,business,'same_person_contact_corrected',contact,detail={'prior_digest':digest([old['display_name'],old['channel_ref']]),'new_digest':digest([name,channel])})
        self.store._transaction(op)

    def stop(self,p,business,contact,stopped):
        self.access(p,business,True); self.portfolio.row(p,business,'portfolio_contacts',contact)
        def op():
            self.db.execute('INSERT INTO scm_customers VALUES(?,?,?,1) ON CONFLICT(business_id,contact_id) DO UPDATE SET stopped=excluded.stopped,revision=revision+1',(business,contact,int(bool(stopped))))
            self.db.execute("UPDATE scm_actions SET status='stale' WHERE business_id=? AND status IN ('approval_pending','approved','retry_wait') AND deal_id IN(SELECT id FROM scm_deals WHERE business_id=? AND contact_id=?)",(business,business,contact))
            self.event(p,business,'contact_stopped' if stopped else 'contact_stop_released',contact)
        self.store._transaction(op)
    def contact_allowed(self,business,contact,purpose):
        row=self.db.execute('SELECT stopped FROM scm_customers WHERE business_id=? AND contact_id=?',(business,contact)).fetchone()
        return bool(not(row and row[0]) and self.portfolio.has_consent(business,contact,purpose))

    def link(self,p,one_business,one_contact,two_business,two_contact,evidence):
        self.access(p,one_business,owner=True); self.access(p,two_business,owner=True)
        if one_business==two_business: raise ValueError('Different businesses required')
        for b,c in [(one_business,one_contact),(two_business,two_contact)]:
            self.portfolio.row(p,b,'portfolio_contacts',c)
            if not self.portfolio.has_consent(b,c,'identity_link'): raise ValueError('Separate identity-link purposes and consent required')
        text(evidence,200)
        def op():
            groups=[self.db.execute('SELECT person_id FROM scm_identity_links WHERE business_id=? AND contact_id=? AND active=1',(b,c)).fetchone() for b,c in [(one_business,one_contact),(two_business,two_contact)]]
            ids={g[0] for g in groups if g}
            if len(ids)>1: raise ValueError('Do not merge existing identity groups automatically')
            person=next(iter(ids)) if ids else uuid4().hex
            self.db.execute('INSERT OR IGNORE INTO scm_people VALUES(?,?)',(person,now().isoformat()))
            for b,c in [(one_business,one_contact),(two_business,two_contact)]:
                if not self.db.execute('SELECT 1 FROM scm_identity_links WHERE business_id=? AND contact_id=? AND active=1',(b,c)).fetchone(): self.db.execute('INSERT INTO scm_identity_links VALUES(?,?,?,?,1,?)',(uuid4().hex,person,b,c,now().isoformat()))
                self.event(p,b,'identity_linked',c,detail={'person_ref':person,'verification_reference':evidence})
            return person
        return self.store._transaction(op)
    def unlink(self,p,business,contact):
        self.access(p,business,owner=True)
        self.db.execute('UPDATE scm_identity_links SET active=0 WHERE business_id=? AND contact_id=? AND active=1',(business,contact)); self.event(p,business,'identity_unlinked',contact)

    def tax_policy(self,p,business,tax_type,rate,rounding,confirmed):
        self.access(p,business,owner=True)
        policy={'tax_type':tax_type,'rate':rate,'rounding':rounding,'confirmed':bool(confirmed)}
        quote_totals([{'label':'設定検証','quantity':'1','unit_yen':'0'}],policy)
        if confirmed and (tax_type=='unknown' or rate in ('',None) or rounding not in ROUNDING): raise ValueError('Explicit tax settings required')
        old=self.db.execute('SELECT * FROM scm_tax WHERE business_id=?',(business,)).fetchone()
        self.db.execute('INSERT INTO scm_tax VALUES(?,?,?,?,?,?) ON CONFLICT(business_id) DO UPDATE SET tax_type=excluded.tax_type,rate=excluded.rate,rounding=excluded.rounding,revision=excluded.revision,confirmed=excluded.confirmed',(business,tax_type,str(rate) if rate not in ('',None) else '',rounding,(old['revision']+1) if old else 1,int(bool(confirmed))))
        self.event(p,business,'tax_settings_changed',business,detail={'before':dict(old) if old else None,'after':policy})
    def rate(self,p,business,label,unit,conditions,confirmed=False,identifier=None,revision=None):
        self.access(p,business,owner=True)
        amount=str(number(unit)) if unit not in ('',None) else None
        if confirmed and amount is None: raise ValueError('Confirmed price required')
        if identifier:
            old=self.db.execute('SELECT * FROM scm_rates WHERE business_id=? AND id=?',(business,identifier)).fetchone()
            if not old or old['revision']!=int(revision): raise ValueError('Current rate revision required')
            self.event(p,business,'rate_before_revision',identifier,detail=dict(old))
            self.db.execute('UPDATE scm_rates SET label=?,unit_yen=?,conditions=?,confirmed=?,revision=revision+1 WHERE business_id=? AND id=?',(text(label,160),amount,text(conditions,1000),int(bool(confirmed)),business,identifier))
        else:
            identifier=uuid4().hex; self.db.execute('INSERT INTO scm_rates VALUES(?,?,?,?,?,?,1)',(business,identifier,text(label,160),amount,text(conditions,1000),int(bool(confirmed))))
        self.event(p,business,'rate_saved',identifier); return identifier
    def cleaning_candidate(self,p,business):
        self.access(p,business,owner=True)
        if self.db.execute('SELECT kind FROM portfolio_businesses WHERE id=?',(business,)).fetchone()[0]!='cleaning': raise ValueError('Cleaning business only')
        old=self.db.execute("SELECT id FROM scm_rates WHERE business_id=? AND label='佐久市内の鏡ウロコ取り（候補）'",(business,)).fetchone()
        return old[0] if old else self.rate(p,business,'佐久市内の鏡ウロコ取り（候補）','6000','出張費込み。税込/税別未確認。エリア外は別途相談。',False)

    def deal(self,p,business,key,contact,subject,inquiry,mode='manual',agent=None,human=None,next_day=None):
        self.writable(p,business); row=self.portfolio.row(p,business,'portfolio_contacts',contact)
        if row['state']!='active' or mode not in ('manual','mock') or not key or len(key)>120: raise ValueError('Active contact, mode and key required')
        if agent and agent not in self.store.agents: raise ValueError('Existing employee required')
        if human:
            user=self.db.execute('SELECT role FROM office_users WHERE username=?',(human,)).fetchone()
            if not user or user[0] not in ('representative','business_operator') or user[0]=='business_operator' and not self.db.execute('SELECT 1 FROM portfolio_acl WHERE business_id=? AND username=? AND can_write=1',(business,human)).fetchone(): raise ValueError('Authorized human owner required')
        if next_day: date.fromisoformat(next_day)
        subject=text(subject,160); inquiry=text(inquiry,2000,True)
        def op():
            old=self.db.execute('SELECT * FROM scm_deals WHERE business_id=? AND entry_key=?',(business,key)).fetchone()
            if old:
                if [old[k] for k in ('contact_id','subject','inquiry','mode')]!=[contact,subject,inquiry,mode]: raise ValueError('Deal key conflict')
                return old['id']
            identifier=uuid4().hex
            self.db.execute('INSERT INTO scm_deals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(business,identifier,key,contact,subject,inquiry,'inquiry','active',mode,agent,human,next_day,None,1,now().isoformat()))
            self.event(p,business,'deal_created',identifier,identifier,{'mode':mode}); return identifier
        return self.store._transaction(op)
    def invalidate(self,business,deal,except_id=None):
        self.db.execute("UPDATE scm_actions SET status='stale' WHERE business_id=? AND deal_id=? AND status IN ('approval_pending','approved','retry_wait') AND id!=?",(business,deal,except_id or ''))
    def update_deal(self,p,business,identifier,next_day,human,agent,revision):
        self.access(p,business,True); row=self.row(p,business,'scm_deals',identifier)
        if row['revision']!=int(revision) or agent and agent not in self.store.agents: raise ValueError('Current revision and existing role required')
        if next_day: date.fromisoformat(next_day)
        if human:
            user=self.db.execute('SELECT role FROM office_users WHERE username=?',(human,)).fetchone()
            if not user or user[0] not in ('representative','business_operator') or user[0]=='business_operator' and not self.db.execute('SELECT 1 FROM portfolio_acl WHERE business_id=? AND username=? AND can_write=1',(business,human)).fetchone(): raise ValueError('Human lacks scope')
        self.db.execute('UPDATE scm_deals SET next_day=?,human_owner=?,agent=?,revision=revision+1 WHERE business_id=? AND id=?',(next_day or None,human or None,agent or None,business,identifier)); self.invalidate(business,identifier); self.event(p,business,'deal_assignment_changed',identifier,identifier)
    def transition(self,p,business,identifier,stage=None,state=None,revision=None):
        self.writable(p,business)
        def op():
            row=self.row(p,business,'scm_deals',identifier)
            if row['revision']!=int(revision): raise ValueError('Latest deal version required')
            if state:
                if state not in STATES: raise ValueError('Known state required')
                if state in ('lost','cancelled') and (row['stage'] in ('booked','provided','invoiced','paid','followup')): raise ValueError('Representative must reconcile booking/invoice cancellation first')
                self.db.execute('UPDATE scm_deals SET state=?,revision=revision+1 WHERE business_id=? AND id=?',(state,business,identifier))
            else:
                order=list(STAGES)
                if row['state']!='active' or stage not in STAGES or order.index(stage)!=order.index(row['stage'])+1 or stage in ('booked','provided','invoiced','paid'): raise ValueError('Adjacent internal stages only; critical facts require approval')
                if stage=='quote' and not self.db.execute('SELECT 1 FROM scm_quotes WHERE business_id=? AND deal_id=?',(business,identifier)).fetchone(): raise ValueError('Quote draft required')
                if stage=='candidate' and not self.db.execute('SELECT 1 FROM scm_bookings WHERE business_id=? AND deal_id=?',(business,identifier)).fetchone(): raise ValueError('Booking candidate required')
                self.db.execute('UPDATE scm_deals SET stage=?,revision=revision+1 WHERE business_id=? AND id=?',(stage,business,identifier))
            self.invalidate(business,identifier); self.event(p,business,'deal_stage_changed',identifier,identifier,{'before':row['stage'],'after':stage or row['stage'],'state':state or row['state']})
        self.store._transaction(op)

    def quote(self,p,business,deal,lines,conditions,expires,previous=None):
        self.writable(p,business); row=self.row(p,business,'scm_deals',deal)
        if date.fromisoformat(expires)<now().astimezone(JST).date(): raise ValueError('Quote validity required')
        if self.contract_current(business,row) or self.db.execute('SELECT 1 FROM scm_invoices i WHERE i.business_id=? AND i.deal_id=? AND NOT EXISTS(SELECT 1 FROM scm_credits c WHERE c.business_id=i.business_id AND c.invoice_id=i.id)',(business,deal)).fetchone(): raise ValueError('Active contract/invoice needs separately confirmed cancellation')
        policy=self.db.execute('SELECT * FROM scm_tax WHERE business_id=?',(business,)).fetchone()
        body=quote_totals(lines,dict(policy) if policy else {'tax_type':'unknown','rate':'','rounding':'','confirmed':False}); body['conditions']=text(conditions,2000)
        def op():
            latest=self.db.execute('SELECT * FROM scm_quotes WHERE business_id=? AND deal_id=? ORDER BY version DESC LIMIT 1',(business,deal)).fetchone()
            if (latest['id'] if latest else None)!=previous: raise ValueError('Latest quote ID required')
            identifier=uuid4().hex; version=latest['version']+1 if latest else 1; encoded=encode(body)
            self.db.execute('INSERT INTO scm_quotes VALUES(?,?,?,?,?,?,?,?,?,?)',(business,deal,identifier,version,previous,encoded,digest([encoded,expires]),expires,'approval_pending',now().isoformat()))
            if latest: self.db.execute("UPDATE scm_quotes SET status='superseded' WHERE business_id=? AND id=?",(business,previous))
            self.db.execute('UPDATE scm_deals SET revision=revision+1 WHERE business_id=? AND id=?',(business,deal)); self.invalidate(business,deal)
            self.event(p,business,'quote_created',identifier,deal,{'version':version}); return identifier
        return self.store._transaction(op)
    def quote_valid(self,p,business,identifier,approved=True,allow_expired=False):
        q=self.row(p,business,'scm_quotes',identifier); body=json.loads(q['body'])
        if digest([q['body'],q['expires_day']])!=q['digest'] or (not allow_expired and q['expires_day']<now().astimezone(JST).date().isoformat()) or q['status']=='superseded' or body['totals'] is None: raise ValueError('Unchanged quote with confirmed tax and valid deadline required')
        if approved:
            event=self.db.execute("SELECT detail FROM scm_events WHERE business_id=? AND ref=? AND event='quote_approve' ORDER BY id DESC LIMIT 1",(business,identifier)).fetchone()
            if q['status']!='approved' or not event or json.loads(event[0]).get('digest')!=q['digest']: raise ValueError('Representative quote approval required')
        return q
    def decide_quote(self,p,business,identifier,decision,checked):
        self.access(p,business,owner=True); q=self.row(p,business,'scm_quotes',identifier)
        if q['status']!='approval_pending' or decision not in ('approve','reject'): raise ValueError('Pending quote required')
        if decision=='approve':
            self.quote_valid(p,business,identifier,False)
            if not checked or risky(q['body']): raise ValueError('Representative review required')
        self.db.execute('UPDATE scm_quotes SET status=? WHERE business_id=? AND id=?',('approved' if decision=='approve' else 'rejected',business,identifier)); self.event(p,business,'quote_'+decision,identifier,q['deal_id'],{'digest':q['digest']})

    def booking(self,p,business,deal,kind,start,end):
        self.writable(p,business); row=self.row(p,business,'scm_deals',deal)
        if kind not in ('service','interview'): raise ValueError('Seminar references are separate from appointments')
        start,end=utc(start),utc(end)
        if start>=end: raise ValueError('Valid interval required')
        identifier=uuid4().hex
        case=self.portfolio.case(p,business,'scm:'+identifier,'booking_request','営業案件の'+('サービス予約' if kind=='service' else '個別面談')+'希望',row['contact_id'],start,end) if row['mode']=='manual' else None
        self.db.execute('INSERT INTO scm_bookings VALUES(?,?,?,?,?,?,?,?,?)',(business,deal,identifier,kind,start,end,case,'candidate',now().isoformat())); self.event(p,business,'booking_candidate',identifier,deal); return identifier

    def action(self,p,business,deal,key,kind,quote=None,booking=None,body=None):
        self.writable(p,business); row=self.row(p,business,'scm_deals',deal)
        if kind not in ACTIONS or not key or len(key)>120: raise ValueError('Known action and key required')
        if quote and self.row(p,business,'scm_quotes',quote)['deal_id']!=deal: raise ValueError('Quote for another deal')
        if booking and self.row(p,business,'scm_bookings',booking)['deal_id']!=deal: raise ValueError('Booking for another deal')
        body=body or {}
        if not isinstance(body,dict) or set(body)-{'message','purpose','invoice_id','amount_yen'} or body.get('purpose','contact') not in ('contact','followup'): raise ValueError('Known action fields and purposes required')
        text(encode(body),8000)
        def op():
            snapshot=self.snapshot(business,row,kind,quote,booking,body)
            old=self.db.execute('SELECT * FROM scm_actions WHERE business_id=? AND (entry_key=? OR (deal_id=? AND kind=? AND snapshot=?))',(business,key,deal,kind,snapshot)).fetchone()
            if old:
                if old['snapshot']!=snapshot: raise ValueError('Action key conflict')
                return old['id']
            identifier=uuid4().hex
            self.db.execute('INSERT INTO scm_actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(business,deal,identifier,key,kind,quote,booking,encode(body),snapshot,'approval_pending',0,None,None,now().isoformat(),None))
            self.event(p,business,'action_proposed',identifier,deal,{'kind':kind}); return identifier
        return self.store._transaction(op)
    def snapshot(self,business,deal,kind,quote,booking,body):
        contact=self.db.execute('SELECT * FROM portfolio_contacts WHERE business_id=? AND id=?',(business,deal['contact_id'])).fetchone()
        state=self.db.execute('SELECT * FROM scm_customers WHERE business_id=? AND contact_id=?',(business,deal['contact_id'])).fetchone()
        consents=[dict(r) for r in self.db.execute('SELECT * FROM portfolio_consents WHERE business_id=? AND contact_id=? ORDER BY id',(business,deal['contact_id']))]
        q=self.db.execute('SELECT digest FROM scm_quotes WHERE business_id=? AND id=?',(business,quote)).fetchone() if quote else None
        payload=[dict(deal),dict(contact),dict(state) if state else None,consents,kind,quote,q[0] if q else None,booking,body]
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='cj_cases'").fetchone() and self.db.execute('SELECT 1 FROM cj_cases WHERE business_id=? AND deal_id=?',(business,deal['id'])).fetchone():
            profile=self.db.execute('SELECT * FROM cj_profiles WHERE business_id=?',(business,)).fetchone()
            sources=[dict(r) for r in self.db.execute('SELECT * FROM cj_sources WHERE business_id=? ORDER BY id',(business,))]
            payload.append([dict(profile) if profile else None,sources])
        return digest(payload)
    def eligible(self,p,business,action):
        self.writable(p,business); deal=self.row(p,business,'scm_deals',action['deal_id']); body=json.loads(action['body'])
        if deal['state']!='active' or self.snapshot(business,deal,action['kind'],action['quote_id'],action['booking_id'],body)!=action['snapshot']: raise ValueError('Deal, consent or content changed')
        # Office supplies authoritative shared policy; absent journey cases leave legacy behavior unchanged.
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='cj_cases'").fetchone():
            from .customer_journey import check_sales_action
            check_sales_action(self,p,business,action)
        if risky(action['body']): raise ValueError('Unsupported claim or instruction')
        if action['kind'] in ('send','publish','contract','booking_confirm','invoice','charge','payment','refund','void_invoice','provided'):
            if not action['quote_id']: raise ValueError('Approved tax-confirmed quote required')
            committed=self.contract_current(business,deal,action['quote_id'])
            self.quote_valid(p,business,action['quote_id'],allow_expired=bool(committed) and action['kind'] not in ('send','publish','contract'))
        if action['kind']=='send' and not self.contact_allowed(business,deal['contact_id'],body.get('purpose','contact')): raise ValueError('Contact consent or stop prevents send')
        if action['kind'] in ('booking_confirm','cancel_booking') and not self.contact_allowed(business,deal['contact_id'],'schedule'): raise ValueError('Schedule consent required')
        if action['kind']=='booking_confirm' and (not action['booking_id'] or deal['stage']!='candidate'): raise ValueError('Current candidate stage required')
        if action['kind']=='provided' and deal['stage']!='booked': raise ValueError('Confirmed booking required')
        if action['kind']=='invoice' and (deal['stage']!='provided' or not self.contract_current(business,deal,action['quote_id'])): raise ValueError('Human contract and service evidence required')
        if action['kind'] in ('payment','refund','void_invoice'):
            invoice=self.row(p,business,'scm_invoices',body.get('invoice_id',''))
            if invoice['deal_id']!=deal['id'] or invoice['quote_id']!=action['quote_id']: raise ValueError('Invoice outside deal or quote')
            if self.db.execute('SELECT 1 FROM scm_credits WHERE business_id=? AND invoice_id=?',(business,invoice['id'])).fetchone(): raise ValueError('Invoice already credited')
        return deal
    def decide_action(self,p,business,identifier,decision,checked):
        self.access(p,business,owner=True); action=self.row(p,business,'scm_actions',identifier)
        if action['status']!='approval_pending' or decision not in ('approve','reject'): raise ValueError('Pending action required')
        if decision=='approve':
            if not checked: raise ValueError('Explicit representative approval required')
            self.eligible(p,business,action)
        self.db.execute('UPDATE scm_actions SET status=? WHERE business_id=? AND id=?',('approved' if decision=='approve' else 'rejected',business,identifier)); self.event(p,business,'action_'+decision,identifier,action['deal_id'],{'snapshot':action['snapshot']})

    def execute_mock(self,p,business,identifier,outcome='success'):
        self.access(p,business,owner=True)
        if outcome not in ('success','transient','permanent','timeout'): raise ValueError('Built-in mock outcomes only')
        def op():
            action=self.row(p,business,'scm_actions',identifier)
            if action['status'] not in ('approved','retry_wait') or action['attempts']>=3 or action['next_at'] and action['next_at']>now().isoformat(): raise ValueError('Approved due bounded action required')
            deal=self.eligible(p,business,action); self.verify_approval(business,action)
            attempts=action['attempts']+1
            status='mock_succeeded' if outcome=='success' else 'manual_reconciliation' if outcome=='timeout' else 'failed' if outcome=='permanent' or attempts>=3 else 'retry_wait'
            self.db.execute('UPDATE scm_actions SET status=?,attempts=?,next_at=?,recorded_at=? WHERE business_id=? AND id=?',(status,attempts,(now()+timedelta(seconds=60*attempts)).isoformat() if status=='retry_wait' else None,now().isoformat() if status=='mock_succeeded' else None,business,identifier))
            self.db.execute('INSERT INTO scm_attempts VALUES(?,?,?,?,?,?,?)',(uuid4().hex,business,identifier,outcome,'mock',(now()+timedelta(seconds=5)).isoformat(),now().isoformat()))
            if outcome=='success' and deal['mode']=='mock' and action['kind']!='delete': self.apply(p,business,action,deal,'mock')
            self.event(p,business,'mock_action_result',identifier,deal['id'],{'status':status,'actual_external_call':False})
            return status
        return self.store._transaction(op)
    def verify_approval(self,business,action):
        approval=self.db.execute("SELECT detail FROM scm_events WHERE business_id=? AND ref=? AND event='action_approve' ORDER BY id DESC LIMIT 1",(business,action['id'])).fetchone()
        if not approval or json.loads(approval[0]).get('snapshot')!=action['snapshot']: raise ValueError('Approval digest mismatch')
    def record_manual(self,p,business,identifier,evidence,observed=False):
        self.access(p,business,owner=True)
        if not observed: raise ValueError('Representative must attest an already-observed event; no API execution')
        evidence=text(evidence,300)
        def op():
            action=self.row(p,business,'scm_actions',identifier)
            if action['status']!='approved': raise ValueError('Current approved action required')
            deal=self.eligible(p,business,action); self.verify_approval(business,action)
            if deal['mode']!='manual': raise ValueError('Mock deals never produce actual records')
            if action['kind']=='delete': raise ValueError('Use explicit retention-checked deletion')
            self.db.execute("UPDATE scm_actions SET status='recorded_manual',evidence=?,recorded_at=? WHERE business_id=? AND id=?",(evidence,now().isoformat(),business,identifier))
            self.apply(p,business,action,deal,'manual')
            self.event(p,business,'manual_event_unverified_externally',identifier,deal['id'],{'kind':action['kind']})
        self.store._transaction(op)
    def contract_current(self,business,deal,quote=None):
        status='recorded_manual' if deal['mode']=='manual' else 'mock_succeeded'
        row=self.db.execute("SELECT kind,quote_id FROM scm_actions WHERE business_id=? AND deal_id=? AND kind IN ('contract','cancel_contract') AND status=? ORDER BY recorded_at DESC,rowid DESC LIMIT 1",(business,deal['id'],status)).fetchone()
        return bool(row and row['kind']=='contract' and (not quote or row['quote_id']==quote))

    def invoice_balance(self,business,invoice):
        cash=self.db.execute("SELECT COALESCE(SUM(CASE WHEN kind='payment' THEN amount_yen ELSE -amount_yen END),0) FROM scm_cash WHERE business_id=? AND invoice_id=?",(business,invoice['id'])).fetchone()[0]
        credited=bool(self.db.execute('SELECT 1 FROM scm_credits WHERE business_id=? AND invoice_id=?',(business,invoice['id'])).fetchone())
        return (0 if credited else invoice['gross_yen'])-cash,cash
    def apply(self,p,business,action,deal,mode):
        kind=action['kind']; body=json.loads(action['body']); stage=deal['stage']
        if kind=='booking_confirm':
            booking=self.row(p,business,'scm_bookings',action['booking_id'])
            if booking['status']!='candidate': raise ValueError('Booking already confirmed/cancelled')
            if mode=='manual':
                op=self.portfolio.operation(p,business,'scm:'+action['id'],'booking_confirm',booking['case_id'])
                self.portfolio.decide_operation(p,business,op,'approve',True); self.portfolio.record_manual(p,business,op,'SCMの代表承認・予約済み手動申告。外部カレンダー未照合。')
            self.db.execute("UPDATE scm_bookings SET status='confirmed' WHERE business_id=? AND id=?",(business,booking['id'])); stage='booked'
        elif kind=='contract':
            previous=self.db.execute("SELECT kind FROM scm_actions WHERE business_id=? AND deal_id=? AND id!=? AND kind IN ('contract','cancel_contract') AND status=? ORDER BY recorded_at DESC,rowid DESC LIMIT 1",(business,deal['id'],action['id'],'recorded_manual' if mode=='manual' else 'mock_succeeded')).fetchone()
            if previous and previous['kind']=='contract': raise ValueError('Contract already recorded')
        elif kind=='cancel_contract':
            if self.db.execute('SELECT 1 FROM scm_invoices i WHERE i.business_id=? AND i.deal_id=? AND NOT EXISTS(SELECT 1 FROM scm_credits c WHERE c.business_id=i.business_id AND c.invoice_id=i.id)',(business,deal['id'])).fetchone() or self.db.execute("SELECT 1 FROM scm_bookings WHERE business_id=? AND deal_id=? AND status='confirmed'",(business,deal['id'])).fetchone(): raise ValueError('Reconcile invoices and bookings before contract termination record')
            stage='quote'
        elif kind=='provided': stage='provided'
        elif kind=='invoice':
            if self.db.execute('SELECT 1 FROM scm_invoices i WHERE i.business_id=? AND i.deal_id=? AND NOT EXISTS(SELECT 1 FROM scm_credits c WHERE c.business_id=i.business_id AND c.invoice_id=i.id)',(business,deal['id'])).fetchone(): raise ValueError('Active invoice already recorded')
            quote=self.quote_valid(p,business,action['quote_id'],allow_expired=True); totals=json.loads(quote['body'])['totals']
            self.db.execute('INSERT INTO scm_invoices VALUES(?,?,?,?,?,?,?,?,?,?)',(business,deal['id'],uuid4().hex,quote['id'],totals['net_yen'],totals['tax_yen'],totals['gross_yen'],mode,action['id'],now().isoformat())); stage='invoiced'
        elif kind in ('payment','refund','void_invoice'):
            invoice=self.row(p,business,'scm_invoices',body['invoice_id'])
            if invoice['mode']!=mode: raise ValueError('Do not mix mock and manual money')
            outstanding,paid=self.invoice_balance(business,invoice)
            amount=int(body.get('amount_yen',0))
            if kind=='payment':
                if amount<0 or amount>outstanding or amount==0 and (invoice['gross_yen']!=0 or self.db.execute('SELECT 1 FROM scm_cash WHERE business_id=? AND invoice_id=?',(business,invoice['id'])).fetchone()): raise ValueError('Amount within balance, or explicit zero-value invoice confirmation required')
                self.db.execute('INSERT INTO scm_cash VALUES(?,?,?,?,?,?,?,?,?)',(business,deal['id'],uuid4().hex,invoice['id'],'payment',amount,mode,action['id'],now().isoformat()))
                if amount==outstanding: stage='paid'
            elif kind=='refund':
                if paid!=invoice['gross_yen'] or amount!=paid or paid<=0: raise ValueError('Only full invoice refund after full payment supported')
                self.db.execute('INSERT INTO scm_cash VALUES(?,?,?,?,?,?,?,?,?)',(business,deal['id'],uuid4().hex,invoice['id'],'refund',amount,mode,action['id'],now().isoformat()))
                self.db.execute('INSERT INTO scm_credits VALUES(?,?,?,?,?,?)',(business,uuid4().hex,invoice['id'],action['id'],mode,now().isoformat())); stage='provided'
            else:
                if paid!=0: raise ValueError('Paid invoice needs separately approved refund')
                self.db.execute('INSERT INTO scm_credits VALUES(?,?,?,?,?,?)',(business,uuid4().hex,invoice['id'],action['id'],mode,now().isoformat())); stage='provided'
        elif kind=='cancel_booking':
            bookings=self.db.execute("SELECT * FROM scm_bookings WHERE business_id=? AND deal_id=? AND status='confirmed'",(business,deal['id'])).fetchall()
            if self.db.execute('SELECT 1 FROM scm_invoices i WHERE i.business_id=? AND i.deal_id=? AND NOT EXISTS(SELECT 1 FROM scm_credits c WHERE c.business_id=i.business_id AND c.invoice_id=i.id)',(business,deal['id'])).fetchone(): raise ValueError('Reconcile invoice first')
            for booking in bookings:
                if mode=='manual' and booking['case_id']: self.portfolio.progress(p,business,booking['case_id'],'cancelled')
            self.db.execute("UPDATE scm_bookings SET status='cancelled' WHERE business_id=? AND deal_id=?",(business,deal['id'])); stage='candidate'
        if kind in ('send','contract','booking_confirm','provided','invoice','payment','refund','void_invoice','cancel_booking','cancel_contract'): self.db.execute('UPDATE scm_deals SET last_response_at=? WHERE business_id=? AND id=?',(now().isoformat(),business,deal['id']))
        self.db.execute('UPDATE scm_deals SET stage=?,revision=revision+1 WHERE business_id=? AND id=?',(stage,business,deal['id']))
        self.invalidate(business,deal['id'],action['id'])
        self.event(p,business,'pipeline_fact_recorded',action['id'],deal['id'],{'kind':kind,'mode':mode,'stage':stage})

    def note(self,p,business,deal,body):
        self.access(p,business,True); self.row(p,business,'scm_deals',deal)
        identifier=uuid4().hex
        self.db.execute('INSERT INTO scm_notes VALUES(?,?,?,?,?,?)',(business,deal,identifier,text(body,2000,True),'internal_human_note',now().isoformat())); self.event(p,business,'internal_note',identifier,deal)
        return identifier
    def work(self,p,business,deal,key,activity,manual_body=None,agent=None):
        self.writable(p,business); row=self.row(p,business,'scm_deals',deal)
        if activity not in ACTIVITIES: raise ValueError('Unsupported activity; submit a role request or work manually')
        if manual_body:
            body=text(manual_body,4000); identifier=uuid4().hex
            self.db.execute('INSERT INTO scm_notes VALUES(?,?,?,?,?,?)',(business,deal,identifier,body,'human_draft_not_sent',now().isoformat())); self.event(p,business,'manual_draft',identifier,deal); return identifier
        existing=self.db.execute('SELECT w.*,t.agent FROM scm_work w JOIN tasks t ON t.id=w.task_id WHERE t.dedupe_key=?',('portfolio:'+digest([business,'scm:'+key]),)).fetchone()
        if existing:
            if existing['business_id']!=business or existing['deal_id']!=deal or existing['activity']!=activity or agent and existing['agent']!=agent: raise ValueError('Work key conflict')
            return existing['task_id']
        from .business_foundation import eligible
        choices=eligible(self.store,business,ACTIVITIES[activity])
        if not choices: raise PermissionError('Assign an employee for this business first')
        agent=agent or min(choices,key=lambda a:self.db.execute("SELECT COUNT(*) FROM tasks WHERE agent=? AND status IN ('pending','running')",(a,)).fetchone()[0])
        if agent not in choices: raise ValueError('Existing expertise required')
        from .customer_journey import check_sales_work
        check_sales_work(self,p,business,deal,agent,activity)
        from .portfolio import CANDIDATES
        category=next((k for k,v in CANDIDATES.items() if agent in v),'planning')
        task=self.portfolio.task(p,business,'scm:'+key,category,'営業支援の下書き。顧客本文は権限付き営業画面でのみ参照。',agent=agent)
        old=self.db.execute('SELECT * FROM scm_work WHERE task_id=?',(task,)).fetchone()
        if old and (old['deal_id']!=deal or old['activity']!=activity): raise ValueError('Work key conflict')
        self.db.execute('INSERT OR IGNORE INTO scm_work VALUES(?,?,?,?)',(task,business,deal,activity)); self.event(p,business,'work_assigned',task,deal,{'agent':agent,'activity':activity}); return task
    def run_work(self,p,business,task):
        self.writable(p,business)
        row=self.db.execute('SELECT * FROM scm_work WHERE business_id=? AND task_id=?',(business,task)).fetchone()
        if not row: raise ValueError('Scoped work required')
        from .customer_journey import check_sales_work
        check_sales_work(self,p,business,row['deal_id'],self.db.execute('SELECT agent FROM tasks WHERE id=?',(task,)).fetchone()[0],row['activity'])
        def op():
            existing=self.db.execute("SELECT ref FROM scm_events WHERE business_id=? AND deal_id=? AND event='work_result' AND detail=?",(business,row['deal_id'],encode({'task_id':task}))).fetchone()
            if existing: return existing[0]
            run=self.store.claim(task); business_row=self.db.execute('SELECT * FROM portfolio_businesses WHERE id=?',(business,)).fetchone()
            deal=self.row(p,business,'scm_deals',row['deal_id'])
            body='ご希望の内容を確認し、提供範囲・料金・日程などの条件をご案内するための下書きです。具体的な約束・健康効果・収入は保証しません。'
            if row['activity']=='classify':
                source=deal['subject']+' '+deal['inquiry']
                matches=[label for label,words in [('予約希望',('予約','日時','面談')),('料金・見積確認',('料金','見積','費用')),('取消・停止',('キャンセル','解約','停止')),('サービス内容確認',('サービス','施工','スマホ','ホワイトニング'))] if any(w in source for w in words)]
                body='ローカルキーワードによる分類案：'+('・'.join(matches) if matches else '要確認')+'。担当者が確認するまで進捗や事実を変更しません。問い合わせ本文は共通タスクへ渡しません。'
            elif row['activity']=='estimate': body='この事業の料金表、適用条件、代表の税区分・税率・端数処理を確認し、明細を入力して見積案を作成。未確認価格/税は補完せず承認を停止します。'
            elif row['activity']=='proposal': body=business_row['name']+'の提案書案。提供内容：'+(business_row['services'] or '代表確認待ち')+'。目的・範囲・料金/税・予定・対象外条件を確認し、見積/契約は別途代表承認。'
            elif row['activity']=='booking':
                candidates=[dict(r) for r in self.db.execute('SELECT kind,start_at,end_at,status FROM scm_bookings WHERE business_id=? AND deal_id=?',(business,row['deal_id']))]
                body='本人入力の予約候補（新しい日時は推測しない）：'+(encode(candidates) if candidates else '未登録')+'。面談・サービス予約を区別し、予約は自動確定しません。'
            elif row['activity']=='followup': body='フォロー用の下書き。本人の希望を任意に確認し、押し売りをしない。' if self.contact_allowed(business,deal['contact_id'],'followup') else '連絡停止またはフォロー同意未確認。再勧誘しない。'
            elif row['activity'] in ('analysis','unhandled','improvement'):
                day=now().astimezone(JST).date(); stats=self.dashboard(p,business,day.replace(day=1).isoformat(),(day+timedelta(days=1)).isoformat())
                body='この事業の記録に基づく集計/改善検討案：'+encode(stats)+'。未対応の整理・不足した計測の確認を代表が判断。施策の因果や成果を断定せず、自動適用しない。'
            elif row['activity']=='quality': body='収入/健康効果の断定、料金・税・同意の未確認を点検し、未確認は代表へ差し戻す。公式規約と法的性質の最終判断は人が行う。'
            identifier=uuid4().hex
            self.db.execute('INSERT INTO scm_notes VALUES(?,?,?,?,?,?)',(business,row['deal_id'],identifier,body,'local_ai_role_template_not_sent',now().isoformat()))
            self.store.finish(run,'営業支援の下書きを保存。詳細は事業権限付き画面で参照。外部送信・実モデル呼出は未実施。')
            self.event(p,business,'work_result',identifier,row['deal_id'],{'task_id':task}); return identifier
        return self.store._transaction(op)
    def role_request(self,p,business,work,agent,reason):
        self.access(p,business,True)
        if agent and agent not in self.store.agents: raise ValueError('Only existing roles can be proposed; no auto hiring/permission change')
        identifier=uuid4().hex
        self.db.execute('INSERT INTO scm_role_requests VALUES(?,?,?,?,?,?,?)',(identifier,business,text(work,160),agent,text(reason,1000),'proposal_only',now().isoformat())); self.event(p,business,'role_extension_proposed',identifier); return identifier

    def seminar(self,p,business,deal,seminar,participant,evidence):
        self.access(p,business,owner=True); row=self.row(p,business,'scm_deals',deal)
        if not self.portfolio.has_consent(business,row['contact_id'],'schedule'): raise ValueError('Seminar reference purpose consent required')
        if not self.db.execute('SELECT 1 FROM portfolio_seminar_links WHERE business_id=? AND seminar_id=?',(business,seminar)).fetchone(): raise ValueError('Explicit business-seminar association required')
        if participant:
            from .seminars import Seminars
            Seminars(self.store).visible(participant,seminar)
            if not evidence: raise ValueError('Representative identity verification required')
        self.db.execute('INSERT INTO scm_seminars VALUES(?,?,?,?,?) ON CONFLICT(business_id,deal_id,seminar_id) DO NOTHING',(business,deal,seminar,participant or None,now().isoformat())); self.event(p,business,'seminar_reference_added',seminar,deal)

    def delete_contact(self,p,business,action_id,retention_checked):
        self.access(p,business,owner=True)
        if not retention_checked: raise ValueError('Retention/backup approval required')
        def op():
            action=self.row(p,business,'scm_actions',action_id)
            if action['kind']!='delete' or action['status']!='approved': raise ValueError('Separate approved deletion action required')
            deal=self.eligible(p,business,action); self.verify_approval(business,action)
            self.portfolio.request_delete(p,business,deal['contact_id']); self.portfolio.approve_delete(p,business,deal['contact_id'],True)
            self.db.execute("UPDATE scm_deals SET inquiry='',subject='顧客情報論理消去済み',revision=revision+1 WHERE business_id=? AND contact_id=?",(business,deal['contact_id']))
            self.db.execute("UPDATE scm_notes SET body='論理消去済み' WHERE business_id=? AND deal_id IN(SELECT id FROM scm_deals WHERE business_id=? AND contact_id=?)",(business,business,deal['contact_id']))
            self.db.execute("UPDATE scm_actions SET evidence=NULL,status='stale' WHERE business_id=? AND deal_id IN(SELECT id FROM scm_deals WHERE business_id=? AND contact_id=?) AND id!=?",(business,business,deal['contact_id'],action_id))
            self.db.execute("UPDATE scm_actions SET status='recorded_manual',recorded_at=? WHERE business_id=? AND id=?",(now().isoformat(),business,action_id))
            self.unlink(p,business,deal['contact_id']); self.event(p,business,'customer_logically_redacted',action_id,deal['id'])
        self.store._transaction(op)

    def dashboard(self,p,business,start,end):
        self.access(p,business); a,b=period(start,end)
        lower=datetime.combine(a,datetime.min.time(),JST).astimezone(timezone.utc).isoformat(); upper=datetime.combine(b,datetime.min.time(),JST).astimezone(timezone.utc).isoformat()
        invoices=self.db.execute("SELECT * FROM scm_invoices WHERE business_id=? AND mode='manual' AND created_at>=? AND created_at<?",(business,lower,upper)).fetchall()
        credits=self.db.execute("SELECT i.* FROM scm_credits c JOIN scm_invoices i ON i.business_id=c.business_id AND i.id=c.invoice_id WHERE c.business_id=? AND c.mode='manual' AND c.created_at>=? AND c.created_at<?",(business,lower,upper)).fetchall()
        sales=sum(i['net_yen'] for i in invoices)-sum(i['net_yen'] for i in credits) if invoices or credits else None
        payments=self.db.execute("SELECT kind,amount_yen FROM scm_cash WHERE business_id=? AND mode='manual' AND created_at>=? AND created_at<?",(business,lower,upper)).fetchall()
        cash=sum(r['amount_yen'] if r['kind']=='payment' else -r['amount_yen'] for r in payments) if payments else None
        expenses=self.db.execute("SELECT amount_yen FROM portfolio_ledger WHERE business_id=? AND kind='expense' AND mode='actual' AND day>=? AND day<?",(business,start,end)).fetchall()
        expense=sum(r[0] for r in expenses) if expenses else None
        deals=self.db.execute("SELECT * FROM scm_deals WHERE business_id=? AND mode='manual' AND created_at>=? AND created_at<?",(business,lower,upper)).fetchall()
        quotes=[]
        for deal in deals:
            q=self.db.execute('SELECT * FROM scm_quotes WHERE business_id=? AND deal_id=? AND created_at<? ORDER BY version DESC LIMIT 1',(business,deal['id'],upper)).fetchone()
            if q: quotes.append(q)
        quote_amounts=[json.loads(q['body'])['totals']['gross_yen'] for q in quotes if json.loads(q['body'])['totals'] is not None]
        active_deals={r['id'] for r in deals if r['state']=='active'}
        expected=[json.loads(q['body'])['totals']['gross_yen'] for q in quotes if q['deal_id'] in active_deals and q['status']=='approved' and json.loads(q['body'])['totals'] is not None and not self.db.execute('SELECT 1 FROM scm_invoices WHERE business_id=? AND deal_id=?',(business,q['deal_id'])).fetchone()]
        success=lambda kind:{r[0] for r in self.db.execute("SELECT deal_id FROM scm_actions WHERE business_id=? AND kind=? AND status='recorded_manual' AND recorded_at<?",(business,kind,upper))}
        contracted=success('contract'); booked=success('booking_confirm'); provided=success('provided')
        candidates={r[0] for r in self.db.execute('SELECT deal_id FROM scm_bookings WHERE business_id=? AND created_at<?',(business,upper))}
        cohort={r['id'] for r in deals}; decision_count=sum(q['status'] in ('approved','rejected') for q in quotes)
        ratio=lambda n,d:round(n/d*100,2) if d else None
        customer_services={}
        for r in self.db.execute("SELECT DISTINCT d.contact_id,d.id FROM scm_deals d JOIN scm_actions a ON a.business_id=d.business_id AND a.deal_id=d.id WHERE d.business_id=? AND d.mode='manual' AND a.kind='provided' AND a.status='recorded_manual' AND a.recorded_at>=? AND a.recorded_at<?",(business,lower,upper)): customer_services[r[0]]=customer_services.get(r[0],0)+1
        unhandled=[r['id'] for r in deals if r['state']=='active' and not r['last_response_at']]
        legacy=self.db.execute("SELECT SUM(amount_yen) FROM portfolio_ledger WHERE business_id=? AND kind='revenue' AND mode='actual' AND day>=? AND day<?",(business,start,end)).fetchone()[0]
        return {'legacy_revenue_yen':legacy,'automatic_ledger_merge':False,'business_id':business,'period':[start,end],'mode':'manual_attestations_not_provider_verified','expected_gross_yen':sum(expected) if expected else None,'quote_gross_yen':sum(quote_amounts) if quote_amounts else None,'confirmed_net_yen':sales,'paid_net_cash_yen':cash,'expense_yen':expense,'approx_profit_yen':sales-expense if sales is not None and expense is not None else None,'contact_count':sum(bool(r['last_response_at']) for r in deals) if deals else None,'proposal_count':sum(r['stage'] in ('proposal','quote','candidate','booked','provided','invoiced','paid','followup') for r in deals) if deals else None,'quote_approval_percent':ratio(sum(q['status']=='approved' for q in quotes),decision_count),'booking_percent':ratio(len(cohort&booked),len(cohort&candidates)),'contract_percent':ratio(len(cohort&contracted),len(cohort)),'repeat_percent':ratio(sum(n>=2 for n in customer_services.values()),len(customer_services)),'unhandled_count':len(unhandled) if deals else None,'overdue_count':sum(bool(r['next_day'] and r['next_day']<=now().astimezone(JST).date().isoformat() and r['state']=='active') for r in deals) if deals else None,'denominators':{'monthly_deals':len(deals),'quote_decisions':decision_count,'booking_candidates':len(cohort&candidates),'provided_customers':len(customer_services)},'definitions':'月内受付コホートの集計時点状態。対応済みは承認後の人の確認行為あり、提案件数は準備を含む。予約/成約率は確定申告の履歴を含む（取消後も過去事実は残る）。売上=代表が請求確認した税抜額−同期間の全額取消。入金=同期間の手動入金−返金。法的売上認識/銀行照合ではない。','exclusions':'所得/法人税・借入・季節性・未入力経費・USDのAI費は概算利益に含めない。旧経費台帳の税区分は未保持。旧売上台帳は重複照合未実施のため別掲し、自動合算しない。事業開発の予測は一切参照しない。'}


def redact_contact(store,business,contact):
    """Called only after representative's retention-checked legacy deletion approval."""
    db=store.db
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='scm_deals'").fetchone(): return
    db.execute("UPDATE scm_deals SET inquiry='',subject='顧客情報論理消去済み',revision=revision+1 WHERE business_id=? AND contact_id=?",(business,contact))
    db.execute("UPDATE scm_notes SET body='論理消去済み' WHERE business_id=? AND deal_id IN(SELECT id FROM scm_deals WHERE business_id=? AND contact_id=?)",(business,business,contact))
    db.execute("UPDATE scm_actions SET evidence=NULL,status=CASE WHEN status IN ('approved','approval_pending','retry_wait') THEN 'stale' ELSE status END WHERE business_id=? AND deal_id IN(SELECT id FROM scm_deals WHERE business_id=? AND contact_id=?)",(business,business,contact))
    db.execute('UPDATE scm_identity_links SET active=0 WHERE business_id=? AND contact_id=?',(business,contact))

    if db.execute("SELECT 1 FROM sqlite_master WHERE name='wf_tasks'").fetchone():
        db.execute("UPDATE wf_tasks SET purpose='論理消去済み',basis='論理消去済み',result='論理消去済み',paused=1 WHERE business_id=? AND deal_id IN(SELECT id FROM scm_deals WHERE business_id=? AND contact_id=?)",(business,business,contact))
        db.execute("UPDATE wf_drafts SET status='stale' WHERE business_id=? AND deal_id IN(SELECT id FROM scm_deals WHERE business_id=? AND contact_id=?)",(business,business,contact))
