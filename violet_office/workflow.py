"""Internal proposal workflows; no network clients or external execution paths."""
import json
from datetime import date,timedelta
from pathlib import Path
from uuid import uuid4
from .store import now
from .seminars import JST,SEGMENTS,ANSWERS
from .portfolio import text
from .content import encode,digest
from .learning import risky

STAGES={'inquiry':'問い合わせ','prepare':'案内準備','approval':'代表承認','guided':'案内済み記録','waiting':'返信待ち','consult':'相談・予約希望','quote':'見積','won':'成約記録','lost':'見送り','provided':'実施記録','paid':'入金確認記録','followup':'フォローアップ'}
# Existing IDs, roles and permissions are read, never modified. Outputs are local checklists.
WORK={
'AI01':'全体の次行動整理','AI02':'営業方針の検討','AI03':'市場情報の出典確認','AI04':'競合比較の確認項目','AI05':'本人希望の確認項目','AI06':'営業手順の改善案',
'AI07':'集客計画案','AI08':'SNS企画案','AI09':'投稿原稿の確認案','AI10':'画像制作指示案','AI11':'動画台本構成案','AI12':'動画編集手順案','AI13':'投稿準備日程案',
'AI14':'営業の次行動案','AI15':'任意紹介の導線案','AI16':'紹介文章案','AI17':'問い合わせ返信案','AI18':'相談・面談導線案','AI19':'説明手順の改善案',
'AI20':'顧客対応の整理','AI21':'LINE返信下書き案','AI22':'同意付きリマインド準備','AI23':'予約候補整理','AI24':'継続利用フォロー案',
'AI25':'運用監視の確認項目','AI26':'未接続APIの接続条件整理','AI27':'実測集計・未計測確認','AI28':'表現・根拠・同意の点検'}


def migrate(store):
    if store.db.execute('SELECT 1 FROM office_migrations WHERE version=13').fetchone(): return
    def op():
        for statement in (Path(__file__).resolve().parent.parent/'migrations/013_ai_workflow.sql').read_text().split(';'):
            if statement.strip(): store.db.execute(statement)
        store.db.execute('INSERT OR IGNORE INTO office_migrations VALUES(13,?)',(now().isoformat(),))
    store._transaction(op)


class Workflow:
    def __init__(self,office):
        self.o=office; self.store=office.store; self.db=office.db; self.sales=office.sales; self.pf=office.portfolio
    def event(self,p,b,event,ref='',deal=None,detail=None):
        self.db.execute('INSERT INTO wf_events(business_id,deal_id,actor,event,ref,detail,created_at) VALUES(?,?,?,?,?,?,?)',(b,deal,p.identity,event,ref,encode(detail or {}),now().isoformat()))
    def case(self,p,b,d):
        self.sales.row(p,b,'scm_deals',d)
        row=self.db.execute('SELECT * FROM wf_cases WHERE business_id=? AND deal_id=?',(b,d)).fetchone()
        if not row: raise ValueError('Attach existing sales deal first')
        return row
    def attach(self,p,b,d):
        self.pf.writable(p,b); self.sales.row(p,b,'scm_deals',d)
        self.db.execute('INSERT OR IGNORE INTO wf_cases(business_id,deal_id) VALUES(?,?)',(b,d)); self.event(p,b,'case_attached',d,d)
    def advance(self,p,b,d,stage,revision):
        self.pf.access(p,b,owner=True); self.pf.writable(p,b)
        def op():
            row=self.case(p,b,d); deal=self.sales.row(p,b,'scm_deals',d)
            if row['revision']!=int(revision) or stage not in STAGES: raise ValueError('Current workflow revision and known stage required')
            if stage!='lost' and deal['state']!='active': raise ValueError('Resume the existing sales deal first')
            current=row['stage']; order=list(STAGES)
            allowed=(stage=='lost' and current not in ('won','provided','paid','followup')) or (current=='lost' and stage=='prepare') or (current=='quote' and stage=='won') or (current=='won' and stage=='provided') or (current!='lost' and order.index(stage)==order.index(current)+1)
            if not allowed or (current=='won' and stage=='lost'): raise ValueError('Use adjacent progress or explicit early loss/reopen')
            status='recorded_manual' if deal['mode']=='manual' else 'mock_succeeded'
            observed=lambda kind:bool(self.db.execute('SELECT 1 FROM scm_actions WHERE business_id=? AND deal_id=? AND kind=? AND status=?',(b,d,kind,status)).fetchone())
            if stage in ('guided','waiting') and not observed('send'): raise ValueError('Observed communication evidence required, draft/approval is not sent')
            if stage=='quote' and not self.db.execute('SELECT 1 FROM scm_quotes WHERE business_id=? AND deal_id=?',(b,d)).fetchone(): raise ValueError('Existing quote required')
            if stage=='won' and not self.sales.contract_current(b,deal): raise ValueError('Existing representative-confirmed contract required')
            if stage=='provided' and (deal['stage'] not in ('provided','invoiced','paid','followup') or not observed('provided')): raise ValueError('Service evidence required')
            if stage=='paid' and (deal['stage'] not in ('paid','followup') or not observed('payment')): raise ValueError('Full payment evidence required')
            if stage=='followup' and not self.sales.contact_allowed(b,deal['contact_id'],'followup'): raise ValueError('Followup consent and no stop required')
            self.db.execute('UPDATE wf_cases SET stage=?,revision=revision+1 WHERE business_id=? AND deal_id=?',(stage,b,d)); self.event(p,b,'stage_changed',d,d,{'before':current,'after':stage,'mode':deal['mode']})
        self.store._transaction(op)
    def assign(self,p,b,d,key,agent,purpose,basis,due,priority=3):
        self.pf.writable(p,b); self.case(p,b,d)
        if agent not in WORK or agent not in self.store.agents or not key or len(key)>80 or not 1<=int(priority)<=5: raise ValueError('Existing role, key and priority required')
        purpose,basis=text(purpose,1000),text(basis,2000); date.fromisoformat(due)
        def op():
            fingerprint=digest([b,d,key]); old=self.db.execute('SELECT w.*,t.agent FROM wf_tasks w JOIN tasks t ON t.id=w.task_id WHERE t.dedupe_key=?',('workflow:'+fingerprint,)).fetchone()
            if old:
                if [old['agent'],old['purpose'],old['basis'],old['due_day']]!=[agent,purpose,basis,due]: raise ValueError('Work key conflict')
                return old['task_id']
            task=self.store.create_task('workflow:'+fingerprint,agent,{'scope':'portfolio','mode':'local_rule_template','reference':uuid4().hex},action='management_decision',max_attempts=3,timeout_seconds=30)
            self.db.execute('INSERT INTO portfolio_tasks VALUES(?,?,?,?,?,?)',(b,task,'planning','事業権限付き業務画面で参照する内部作業。',int(priority),now().isoformat()))
            self.db.execute('INSERT INTO wf_tasks VALUES(?,?,?,?,?,?,0,NULL,NULL,NULL,?)',(task,b,d,purpose,basis,due,now().isoformat()))
            self.event(p,b,'task_assigned',task,d,{'agent':agent,'reason':WORK[agent],'priority':int(priority)}); return task
        return self.store._transaction(op)
    def task(self,p,b,task):
        self.pf.access(p,b)
        row=self.db.execute('SELECT w.*,t.agent,t.status FROM wf_tasks w JOIN tasks t ON t.id=w.task_id WHERE w.business_id=? AND w.task_id=?',(b,task)).fetchone()
        if not row: raise ValueError('Scoped workflow task required')
        return row
    def control(self,p,b,task,action,value=None):
        self.pf.access(p,b,owner=True)
        def op():
            row=self.task(p,b,task)
            if action in ('pause','resume'):
                self.db.execute('UPDATE wf_tasks SET paused=? WHERE task_id=?',(int(action=='pause'),task))
            elif action=='assign':
                if row['status'] not in ('pending','paused') or value not in WORK or value not in self.store.agents: raise ValueError('Unstarted task and existing role required')
                self.db.execute('UPDATE tasks SET agent=? WHERE id=?',(value,task))
            elif action=='priority':
                if not 1<=int(value)<=5: raise ValueError('Priority 1..5 required')
                self.db.execute('UPDATE portfolio_tasks SET priority=? WHERE task_id=?',(int(value),task))
            else: raise ValueError('Known control required')
            self.event(p,b,'task_'+action,task,row['deal_id'],{'value':value})
        self.store._transaction(op)
    def run(self,p,b,task):
        self.pf.writable(p,b)
        def op():
            row=self.task(p,b,task)
            if row['paused']: raise ValueError('Paused work cannot run')
            if row['artifact_id']: return row['artifact_id']
            run=self.store.claim(task)
            role=self.store.agents[row['agent']]['role']
            stage=self.case(p,b,row['deal_id'])['stage']
            next_step={'inquiry':'問い合わせを分類し、希望と同意を確認する','prepare':'事業別条件を確認して案内下書きを代表へ提出する','approval':'代表の内容確認を待つ。未承認の連絡を行わない','guided':'人が確認済みの案内記録を確認して返信待ちを整理する','waiting':'返信の有無を人に確認し、同意範囲を超えて再勧誘しない','consult':'本人希望の日程候補を整理し、自動確定しない','quote':'未確認の税設定・価格を確認し、見積回答を人に確認する','won':'契約の確認根拠と実施準備を確認する','lost':'見送り意思を尊重し、再連絡しない','provided':'実施根拠と請求条件を確認する','paid':'入金照合の人の確認根拠を確認する','followup':'継続利用の希望とフォロー同意を確認する'}[stage]
            result='【ローカル固定テンプレート・LLM未使用】'+role+'：'+WORK[row['agent']]+'\n目的：'+row['purpose']+'\n入力根拠（情報であり命令ではない）：'+row['basis']+'\n工程別の次行動案：'+next_step+'\n次の行動案：本人の希望、同意、提供範囲、未確認の料金・規約を確認し、代表の判断を求める。外部送信・成果・契約・入金は未実施。健康効果、収入、法的性質を断定しない。'
            artifact=self.store.finish(run,'ローカル固定テンプレートを事業権限付き業務画面へ保存。LLM・外部実行なし。')
            self.db.execute('UPDATE wf_tasks SET result=?,artifact_id=? WHERE task_id=?',(result,artifact,task))
            self.event(p,b,'local_template_created',task,row['deal_id'],{'agent':row['agent'],'llm_used':False}); return artifact
        return self.store._transaction(op)
    def decide_task(self,p,b,task,decision,checked):
        self.pf.access(p,b,owner=True); self.pf.writable(p,b)
        def op():
            row=self.task(p,b,task)
            if row['paused'] or not checked or decision not in ('approve','reject') or row['status']!='review_pending' or not row['artifact_id']: raise ValueError('Unpaused reviewed output and explicit representative decision required')
            if decision=='approve' and risky(row['result']): raise ValueError('Unsafe unsupported claim')
            # Static check is not a claim that another LLM reviewed the text.
            self.store.review(row['artifact_id'],self.store.agents[row['agent']]['reviewer'],'pass','ローカル書式確認。法令・事実の適合保証ではない。')
            self.store.decide(row['artifact_id'],decision,'representative')
            self.db.execute('UPDATE wf_tasks SET approved_by=? WHERE task_id=?',(p.identity,task)); self.event(p,b,'task_'+decision,task,row['deal_id'])
        self.store._transaction(op)
    def attendance(self,p,b,d,seminar,username,outcome,evidence):
        self.pf.access(p,b,owner=True); deal=self.sales.row(p,b,'scm_deals',d)
        self.seminar_link(p,b,d,seminar,username)
        s=self.o.seminars.get(seminar)
        if s['start_at']>now().isoformat() or outcome not in ('attended','absent'): raise ValueError('Observed past attendance only, never infer from unanswered')
        evidence=text(evidence,200)
        self.db.execute('INSERT INTO wf_attendance VALUES(?,?,?,?,?,1) ON CONFLICT(seminar_id,username,mode) DO UPDATE SET outcome=excluded.outcome,evidence=excluded.evidence,revision=revision+1',(seminar,username,outcome,evidence,deal['mode']))
        self.event(p,b,'attendance_recorded',seminar,d,{'outcome':outcome,'mode':deal['mode']})
    def seminar_link(self,p,b,d,seminar,username):
        self.pf.access(p,b,owner=True); self.case(p,b,d)
        if not self.db.execute('SELECT 1 FROM scm_seminars WHERE business_id=? AND deal_id=? AND seminar_id=? AND participant_username=?',(b,d,seminar,username)).fetchone(): raise ValueError('Existing verified sales-seminar link required')
        return self.o.seminars.visible(username,seminar)
    def seminar_snapshot(self,p,b,d,seminar,username,kind):
        self.pf.writable(p,b); s=self.seminar_link(p,b,d,seminar,username); deal=self.sales.row(p,b,'scm_deals',d)
        if kind in ('invite','absence') and self.db.execute("SELECT 1 FROM sqlite_master WHERE name='s14_meta'").fetchone() and self.db.execute('SELECT 1 FROM s14_meta WHERE seminar_id=?',(seminar,)).fetchone(): raise ValueError('Use purpose-scoped seminar extension drafts')
        reg=self.o.seminars.member(username,True); consent=self.o.seminars.latest_consent(username)
        answer=self.db.execute('SELECT * FROM seminar_responses WHERE seminar_id=? AND username=?',(seminar,username)).fetchone()
        attendance=self.db.execute('SELECT * FROM wf_attendance WHERE seminar_id=? AND username=? AND mode=?',(seminar,username,deal['mode'])).fetchone()
        response=answer['answer'] if answer else 'unanswered'
        if kind not in ('invite','reminder','absence') or deal['state']!='active' or s['status']=='cancelled' or not consent or not consent['granted'] or response=='no': raise ValueError('Seminar status, intent and consent must allow contact')
        purpose='followup' if kind=='absence' else 'contact'
        if not self.sales.contact_allowed(b,deal['contact_id'],purpose) or not self.sales.contact_allowed(b,deal['contact_id'],'schedule'): raise ValueError('Separate business purpose consent/no-stop required')
        if kind=='absence':
            if not attendance or attendance['outcome']!='absent' or attendance['mode']!=deal['mode'] or s['start_at']>now().isoformat(): raise ValueError('Recorded absence required, no guessed absence')
        elif s['start_at']<=now().isoformat() or kind=='invite' and (s['status']!='open' or now().isoformat()>s['deadline']): raise ValueError('Current invitation period required')
        reminder=None
        if kind=='reminder':
            if deal['mode']=='mock': raise ValueError('Use the existing synthetic seminar test fixture; do not mix mock deals into the canonical reminder queue')
            reminder=self.db.execute('SELECT * FROM seminar_reminders WHERE seminar_id=? AND username=?',(seminar,username)).fetchone()
            if not reminder or reminder['status'] not in ('approval_pending','approved') or not self.o.seminars._current(reminder): raise ValueError('Use existing three-day preparation first; no duplicate reminder engine')
        inputs=[{'reminder_id':reminder['id'],'digest':reminder['digest']} if reminder else None,dict(s),dict(reg),dict(consent),dict(answer) if answer else None,dict(attendance) if attendance else None,dict(deal),self.sales.snapshot(b,deal,kind,None,None,{}),kind]
        return digest(inputs),s,reg,response
    def draft(self,p,b,d,seminar,username,kind):
        def op():
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='s14_drafts'").fetchone() and self.db.execute("SELECT 1 FROM s14_drafts WHERE seminar_id=? AND username=? AND kind=? AND status IN ('approval_pending','send_waiting_no_api','retry_wait','mock_sent')",(seminar,username,kind)).fetchone(): raise ValueError('Use existing seminar extension draft; do not duplicate delivery')
            snapshot,s,reg,response=self.seminar_snapshot(p,b,d,seminar,username,kind)
            old=self.db.execute('SELECT id FROM wf_drafts WHERE business_id=? AND deal_id=? AND seminar_id=? AND username=? AND kind=? AND snapshot=?',(b,d,seminar,username,kind,snapshot)).fetchone()
            if old: return old[0]
            mode=self.sales.row(p,b,'scm_deals',d)['mode']
            self.db.execute("UPDATE wf_drafts SET status='stale' WHERE seminar_id=? AND username=? AND kind=? AND mode=? AND status IN ('approval_pending','send_waiting_no_api')",(seminar,username,kind,mode))
            segment=SEGMENTS[reg['segment']]
            wording='ご希望があれば内容をご確認ください。回答は任意です。' if kind=='invite' else '参加予定の日時・会場をご確認ください。' if kind=='reminder' and response=='yes' else 'ご希望の場合のみ参加可否をお知らせください。' if kind=='reminder' else 'ご事情を尊重します。今後の情報が必要な場合のみお知らせください。'
            body='【固定テンプレート・未送信】区分：'+segment+'\n'+s['title']+'\n'+s['start_at']+' ～ '+s['end_at']+'\n'+s['location']+'\n参加費 '+str(s['fee_yen'])+'円\n'+wording+'\n未確認の健康効果・収益・報酬は案内しません。'
            if risky(body): raise ValueError('Unsafe seminar information requires human correction')
            identifier=uuid4().hex
            self.db.execute('INSERT INTO wf_drafts VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,?)',(identifier,b,d,seminar,username,kind,mode,body,digest(body),snapshot,'approval_pending',now().isoformat()))
            self.event(p,b,'seminar_draft_prepared',identifier,d,{'kind':kind,'mode':'local_template_not_sent'}); return identifier
        return self.store._transaction(op)
    def decide_draft(self,p,b,identifier,decision,checked):
        self.pf.access(p,b,owner=True)
        def op():
            row=self.db.execute('SELECT * FROM wf_drafts WHERE id=? AND business_id=?',(identifier,b)).fetchone()
            if not row or row['status']!='approval_pending' or decision not in ('approve','reject') or not checked: raise ValueError('Pending draft and explicit decision required')
            if decision=='approve':
                if digest(row['body'])!=row['body_digest'] or risky(row['body']): raise ValueError('Draft content changed or unsafe')
                current,*_=self.seminar_snapshot(p,b,row['deal_id'],row['seminar_id'],row['username'],row['kind'])
                if current!=row['snapshot']: raise ValueError('Consent, response or seminar changed; prepare new draft')
            if row['kind']=='reminder':
                original=self.db.execute('SELECT * FROM seminar_reminders WHERE seminar_id=? AND username=?',(row['seminar_id'],row['username'])).fetchone()
                if original and original['status']=='approval_pending': self.o.seminars.approve_reminder(p,original['id'],decision)
            status='send_waiting_no_api' if decision=='approve' else 'rejected'
            self.db.execute('UPDATE wf_drafts SET status=?,approved_by=? WHERE id=?',(status,p.identity,identifier)); self.event(p,b,'draft_'+decision,identifier,row['deal_id'])
        self.store._transaction(op)
    def queue(self,p,b):
        self.pf.access(p,b,owner=True); output=[]
        for row in self.db.execute("SELECT * FROM wf_drafts WHERE business_id=? AND status='send_waiting_no_api'",(b,)):
            try: valid=digest(row['body'])==row['body_digest'] and not risky(row['body']) and self.seminar_snapshot(p,b,row['deal_id'],row['seminar_id'],row['username'],row['kind'])[0]==row['snapshot']
            except (ValueError,PermissionError): valid=False
            if row['kind']=='reminder':
                original=self.db.execute('SELECT * FROM seminar_reminders WHERE seminar_id=? AND username=?',(row['seminar_id'],row['username'])).fetchone()
                valid=valid and bool(original and original['status']=='approved')
            if valid: output.append({**dict(row),'delivery_reference':original['id'] if row['kind']=='reminder' else row['id']})
        return output
    def dashboard(self,p,b):
        self.pf.access(p,b); day=now().astimezone(JST).date(); start=day.replace(day=1).isoformat(); end=(day+timedelta(days=1)).isoformat()
        deals=[dict(r) for r in self.db.execute("SELECT d.*,w.stage AS workflow_stage FROM scm_deals d LEFT JOIN wf_cases w ON w.business_id=d.business_id AND w.deal_id=d.id WHERE d.business_id=? AND d.mode='manual'",(b,))]
        active=[r for r in deals if r['state']=='active' and r['workflow_stage']!='lost']; today=[r['id'] for r in active if r['next_day'] and r['next_day']<=day.isoformat()]
        flags={'unreplied':[r['id'] for r in active if r['workflow_stage']=='waiting'],'unhandled':[r['id'] for r in active if not r['last_response_at']],'overdue':today,
            'quote_unanswered':[r['id'] for r in active if r['workflow_stage']=='quote' and not self.sales.contract_current(b,r)]}
        invoices=self.db.execute("SELECT * FROM scm_invoices WHERE business_id=? AND mode='manual'",(b,)).fetchall()
        unpaid=sum(max(0,self.sales.invoice_balance(b,i)[0]) for i in invoices) if invoices else None
        bookings={state:self.db.execute("SELECT COUNT(*) FROM scm_bookings b JOIN scm_deals d ON d.business_id=b.business_id AND d.id=b.deal_id WHERE b.business_id=? AND d.mode='manual' AND b.status=?",(b,state)).fetchone()[0] for state in ('candidate','confirmed')}
        task_due=self.db.execute("SELECT COUNT(*) FROM wf_tasks w JOIN tasks t ON t.id=w.task_id WHERE w.business_id=? AND w.due_day<=? AND w.paused=0 AND t.status NOT IN ('manual_execution_pending','rejected')",(b,day.isoformat())).fetchone()[0]
        employees=[]
        for agent in self.store.agents:
            rows=self.db.execute('SELECT t.status,w.paused FROM wf_tasks w JOIN tasks t ON t.id=w.task_id WHERE w.business_id=? AND t.agent=?',(b,agent)).fetchall()
            employees.append({'agent':agent,'assigned':len(rows),'approved':sum(r['status']=='manual_execution_pending' for r in rows),'paused':sum(r['paused'] for r in rows)})
        return {'period':[start,end],'mode':'manual_attestations_not_provider_verified','due_internal_tasks':task_due,'active_deals':len(active) if deals else None,'alerts':flags,'bookings':bookings,'unpaid_yen':unpaid,'sales':self.sales.dashboard(p,b,start,end),'employees':employees,'external_api':'未接続・実送信なし'}
