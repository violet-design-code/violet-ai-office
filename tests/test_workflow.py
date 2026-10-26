"""Phase13 uses only synthetic temp databases, no external clients."""
import json,sqlite3
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import unittest
from tests import test_sales as helpers
from violet_office.store import Store,now
from violet_office.office import Office
from violet_office.integrations.adapters import Principal
from violet_office.workflow import migrate,WORK
from violet_office.ops import backup,restore
from violet_office.web import OfficeApp


class WorkflowTests(unittest.TestCase):
    setUp=helpers.SalesTests.setUp
    tearDown=helpers.SalesTests.tearDown
    customer=helpers.SalesTests.customer
    deal=helpers.SalesTests.deal
    quote=helpers.SalesTests.quote
    stage=helpers.SalesTests.stage
    ready=helpers.SalesTests.ready
    action=helpers.SalesTests.action
    invoiced=helpers.SalesTests.invoiced
    operator=helpers.SalesTests.operator
    headers=helpers.SalesTests.headers
    post=helpers.SalesTests.post
    def attached(self,mode='manual'):
        d=self.deal(mode); self.o.workflow.attach(self.owner,self.b,d); return d
    def task(self,d=None,agent='AI17',key='one'):
        return self.o.workflow.assign(self.owner,self.b,d or self.attached(),key,agent,'合成の返信準備','合成の確認資料参照',self.day.isoformat())
    def advance(self,d,stage):
        return self.o.workflow.advance(self.owner,self.b,d,stage,self.o.workflow.case(self.owner,self.b,d)['revision'])
    def seminar(self,days=3,audience='public'):
        d=self.attached(); self.o.add_user('participant','participant','SyntheticParticipantPass!123')
        sem=self.o.seminars; sem.account(self.owner,'participant','合成参加者','合成の本人確認',True); sem.register('participant','cast',''); sem.decide_registration(self.owner,'participant','approve'); sem.consent('participant',True)
        start=now()+timedelta(days=days)
        sid=sem.save(self.owner,{'title':'合成セミナー','start_at':start.isoformat(),'end_at':(start+timedelta(hours=1)).isoformat(),'deadline':start.isoformat(),'location':'合成会場','fee_yen':0,'description':'合成の内容','audience':audience,'capacity':''})
        self.o.portfolio.link_seminar(self.owner,self.b,sid); self.s.seminar(self.owner,self.b,d,sid,'participant','合成の本人確認')
        return d,sid
    def test_attach_idempotent_without_rewriting_existing_sales(self):
        d=self.attached(); before=dict(self.s.row(self.owner,self.b,'scm_deals',d)); self.o.workflow.attach(self.owner,self.b,d)
        self.assertEqual(before,dict(self.s.row(self.owner,self.b,'scm_deals',d))); self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM wf_cases').fetchone()[0],1)
    def test_all_28_roles_generate_local_templates_and_do_not_change_registry(self):
        before=Path('config/agents.json').read_bytes(); d=self.attached()
        self.assertEqual(set(WORK),set(self.store.agents))
        for agent in WORK:
            task=self.task(d,agent,agent); artifact=self.o.workflow.run(self.owner,self.b,task)
            row=self.o.workflow.task(self.owner,self.b,task)
            self.assertIn('LLM未使用',row['result']); self.assertEqual(row['status'],'review_pending'); self.assertTrue(artifact)
            self.o.workflow.decide_task(self.owner,self.b,task,'approve',True)
        self.assertEqual(before,Path('config/agents.json').read_bytes()); self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM scm_invoices').fetchone()[0],0)
    def test_task_dedup_and_conflicting_input(self):
        d=self.attached(); t=self.task(d); self.assertEqual(t,self.task(d))
        with self.assertRaises(ValueError): self.o.workflow.assign(self.owner,self.b,d,'one','AI17','変更','合成根拠',self.day.isoformat())
        a=self.o.workflow.run(self.owner,self.b,t); self.assertEqual(a,self.o.workflow.run(self.owner,self.b,t)); self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM runs').fetchone()[0],1)
    def test_pause_resume_reassign_priority_and_owner_only(self):
        t=self.task(); staff=self.operator()
        with self.assertRaises(PermissionError): self.o.workflow.control(staff,self.b,t,'assign','AI28')
        self.o.workflow.control(self.owner,self.b,t,'assign','AI28'); self.o.workflow.control(self.owner,self.b,t,'priority','1'); self.o.workflow.control(self.owner,self.b,t,'pause')
        with self.assertRaises(ValueError): self.o.workflow.run(self.owner,self.b,t)
        with self.assertRaises(ValueError): self.store.claim(t)
        self.o.workflow.control(self.owner,self.b,t,'resume'); self.o.workflow.run(self.owner,self.b,t)
        with self.assertRaises(ValueError): self.o.workflow.control(self.owner,self.b,t,'assign','AI17')
        self.assertEqual(self.store.db.execute('SELECT priority FROM portfolio_tasks WHERE task_id=?',(t,)).fetchone()[0],1)
    def test_approval_rejection_and_pause_after_output(self):
        t=self.task(); self.o.workflow.run(self.owner,self.b,t); self.o.workflow.control(self.owner,self.b,t,'pause')
        with self.assertRaises(ValueError): self.o.workflow.decide_task(self.owner,self.b,t,'approve',True)
        self.o.workflow.control(self.owner,self.b,t,'resume')
        with self.assertRaises(ValueError): self.o.workflow.decide_task(self.owner,self.b,t,'approve',False)
        self.o.workflow.decide_task(self.owner,self.b,t,'reject',True); self.assertEqual(self.o.workflow.task(self.owner,self.b,t)['status'],'rejected')
    def test_emergency_stop_business_budget_and_legacy_runner_rejected(self):
        t=self.task()
        with self.assertRaises(ValueError): self.o.portfolio.run(self.owner,self.b,t)
        with self.assertRaises(ValueError): self.o.portfolio.task_control(self.owner,self.b,t,'resume')
        with self.assertRaises(ValueError): self.store.claim(t,reserve_microusd=1)
        self.store.db.execute('UPDATE portfolio_controls SET halted=1')
        with self.assertRaises(ValueError): self.o.workflow.run(self.owner,self.b,t)
    def test_private_customer_text_does_not_enter_shared_artifact(self):
        d=self.attached(); t=self.task(d); self.o.workflow.run(self.owner,self.b,t)
        shared=''.join(r[0] for r in self.store.db.execute('SELECT content FROM artifacts'))
        self.assertNotIn('合成の内容。実際の顧客情報ではない。',shared); self.assertNotIn('合成顧客',shared)
        with self.assertRaises(ValueError): self.o.workflow.assign(self.owner,self.b,d,'secret','AI17','合成','token=secretvalue',self.day.isoformat())
    def test_progress_cannot_claim_sent_from_approval(self):
        d=self.attached(); self.advance(d,'prepare'); self.advance(d,'approval')
        q=self.quote(d); action=self.s.action(self.owner,self.b,d,'pending','send',q); self.s.decide_action(self.owner,self.b,action,'approve',True)
        with self.assertRaises(ValueError): self.advance(d,'guided')
        self.s.record_manual(self.owner,self.b,action,'合成の確認記録',True); self.advance(d,'guided'); self.advance(d,'waiting')
        self.assertIn(d,self.o.workflow.dashboard(self.owner,self.b)['alerts']['unreplied'])
        self.advance(d,'consult'); self.advance(d,'quote')
        with self.assertRaises(ValueError): self.advance(d,'won')
    def test_actual_pipeline_only_existing_manual_facts(self):
        d,q,inv=self.invoiced(); self.action(d,'payment',q,body={'invoice_id':inv['id'],'amount_yen':6600})
        self.o.workflow.attach(self.owner,self.b,d); self.advance(d,'prepare'); self.advance(d,'approval')
        self.action(d,'send',q); self.advance(d,'guided'); self.advance(d,'waiting'); self.advance(d,'consult'); self.advance(d,'quote'); self.advance(d,'won'); self.advance(d,'provided'); self.advance(d,'paid'); self.advance(d,'followup')
        data=self.o.workflow.dashboard(self.owner,self.b); self.assertEqual(data['unpaid_yen'],0); self.assertEqual(data['sales']['confirmed_net_yen'],6000)
    def test_mock_payment_never_enters_real_dashboard(self):
        d,q,inv=self.invoiced('mock'); self.action(d,'payment',q,body={'invoice_id':inv['id'],'amount_yen':6600},mode='mock')
        data=self.o.workflow.dashboard(self.owner,self.b); self.assertIsNone(data['sales']['confirmed_net_yen']); self.assertIsNone(data['unpaid_yen'])
    def test_loss_and_reopen_do_not_create_or_delete_money(self):
        d=self.attached(); self.advance(d,'lost'); self.advance(d,'prepare')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM scm_invoices').fetchone()[0],0)
        with self.assertRaises(ValueError): self.advance(d,'paid')
    def test_business_acl_and_cross_business_references(self):
        d=self.attached(); staff=self.operator()
        with self.assertRaises(PermissionError): self.o.workflow.dashboard(staff,'BIZ-A')
        with self.assertRaises(ValueError): self.o.workflow.assign(self.owner,'BIZ-A',d,'cross','AI17','合成','合成',self.day.isoformat())
        self.o.workflow.assign(staff,self.b,d,'staff','AI17','合成','合成',self.day.isoformat())
    def test_invite_approval_queue_no_execution_or_duplicate(self):
        d,sid=self.seminar(); w=self.o.workflow; draft=w.draft(self.owner,self.b,d,sid,'participant','invite')
        self.assertEqual(draft,w.draft(self.owner,self.b,d,sid,'participant','invite')); self.assertEqual(w.queue(self.owner,self.b),[])
        w.decide_draft(self.owner,self.b,draft,'approve',True); self.assertEqual(len(w.queue(self.owner,self.b)),1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0],0); self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM seminar_attempts').fetchone()[0],0)
    def test_stop_consent_response_and_seminar_revision_invalidate_queue(self):
        d,sid=self.seminar(); w=self.o.workflow; a=w.draft(self.owner,self.b,d,sid,'participant','invite'); w.decide_draft(self.owner,self.b,a,'approve',True)
        self.o.seminars.respond('participant',sid,'no'); self.assertEqual(w.queue(self.owner,self.b),[])
        with self.assertRaises(ValueError): w.draft(self.owner,self.b,d,sid,'participant','invite')
        self.o.seminars.respond('participant',sid,'maybe'); a=w.draft(self.owner,self.b,d,sid,'participant','invite'); w.decide_draft(self.owner,self.b,a,'approve',True)
        self.o.seminars.consent('participant',False); self.assertEqual(w.queue(self.owner,self.b),[])
        self.o.seminars.consent('participant',True); a=w.draft(self.owner,self.b,d,sid,'participant','invite'); w.decide_draft(self.owner,self.b,a,'approve',True)
        c=self.s.row(self.owner,self.b,'scm_deals',d)['contact_id']; self.s.stop(self.owner,self.b,c,True); self.assertEqual(w.queue(self.owner,self.b),[])
    def test_three_day_reminder_reuses_original_canonical_id(self):
        d,sid=self.seminar(); w=self.o.workflow
        with self.assertRaises(ValueError): w.draft(self.owner,self.b,d,sid,'participant','reminder')
        self.o.seminars.schedule(self.owner,True); self.o.seminars.tick(self.owner); self.o.seminars.schedule(self.owner,False)
        original=self.store.db.execute('SELECT id FROM seminar_reminders').fetchone()[0]
        a=w.draft(self.owner,self.b,d,sid,'participant','reminder'); w.decide_draft(self.owner,self.b,a,'approve',True)
        self.assertEqual(w.queue(self.owner,self.b)[0]['delivery_reference'],original)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM seminar_reminders').fetchone()[0],1)
    def test_wrong_segment_and_missing_purpose_consent_denied(self):
        d,sid=self.seminar(); self.store.db.execute("UPDATE seminars SET audience='dealer',revision=revision+1 WHERE id=?",(sid,))
        with self.assertRaises(PermissionError): self.o.workflow.draft(self.owner,self.b,d,sid,'participant','invite')
        self.store.db.execute("UPDATE seminars SET audience='public',revision=revision+1 WHERE id=?",(sid,))
        c=self.s.row(self.owner,self.b,'scm_deals',d)['contact_id']; self.o.portfolio.consent(self.owner,self.b,c,'contact',False,'合成撤回')
        with self.assertRaises(ValueError): self.o.workflow.draft(self.owner,self.b,d,sid,'participant','invite')
    def test_absence_not_inferred_and_requires_past_manual_evidence(self):
        d,sid=self.seminar(); w=self.o.workflow
        with self.assertRaises(ValueError): w.attendance(self.owner,self.b,d,sid,'participant','absent','合成')
        past=now()-timedelta(days=1); self.store.db.execute('UPDATE seminars SET start_at=? WHERE id=?',(past.isoformat(),sid))
        with self.assertRaises(ValueError): w.draft(self.owner,self.b,d,sid,'participant','absence')
        w.attendance(self.owner,self.b,d,sid,'participant','absent','合成の人間確認参照'); self.assertTrue(w.draft(self.owner,self.b,d,sid,'participant','absence'))
    def test_restore_pauses_tasks_and_reapproves_queued_drafts(self):
        d,sid=self.seminar(); t=self.task(d); w=self.o.workflow; w.run(self.owner,self.b,t); w.decide_task(self.owner,self.b,t,'approve',True); a=w.draft(self.owner,self.b,d,sid,'participant','invite'); w.decide_draft(self.owner,self.b,a,'approve',True)
        snap=Path(self.temp.name)/'copy.db'; dst=Path(self.temp.name)/'recovery.db'; backup(self.path,snap); restore(snap,dst)
        db=sqlite3.connect(dst)
        try:
            self.assertEqual(db.execute('SELECT status FROM tasks WHERE id=?',(t,)).fetchone()[0],'review_pending'); self.assertEqual(db.execute('SELECT paused FROM wf_tasks').fetchone()[0],1); self.assertEqual(db.execute('SELECT status FROM wf_drafts').fetchone()[0],'approval_pending')
        finally: db.close()
    def test_additive_migration_preserves_all_phase12_tables_and_rows(self):
        d=self.deal(); self.quote(d); self.store.db.execute('DELETE FROM office_migrations WHERE version=13')
        for table in ('wf_events','wf_attendance','wf_drafts','wf_tasks','wf_cases'): self.store.db.execute('DROP TABLE '+table)
        tables=[r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='office_migrations'")]
        snapshot=lambda:{t:[tuple(r) for r in self.store.db.execute('SELECT * FROM '+t)] for t in tables}
        before=snapshot(); migrate(self.store); migrate(self.store); self.assertEqual(before,snapshot()); self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(),[])
    def test_web_scope_nonce_csrf_and_owner_controls(self):
        d=self.attached(); t=self.task(d); staff=self.operator(); headers,csrf=self.headers('staff','business_operator')
        status,_,_=OfficeApp(self.path,8765).handle('GET','/workflow?business=BIZ-A',headers); self.assertEqual(status,403)
        result=self.post(headers,csrf,'wf_control',{'business':self.b,'task':t,'control':'pause'}); self.assertEqual(result[0],403)
        h,c=self.headers(); result=self.post(h,c,'wf_assign',{'business':self.b,'deal':d,'key':'web','agent':'AI17','purpose':'合成','basis':'合成','due':self.day.isoformat()},'1'*32)
        self.assertEqual(result[0],303); self.assertEqual(result,self.post(h,c,'wf_assign',{'business':self.b,'deal':d,'key':'web','agent':'AI17','purpose':'合成','basis':'合成','due':self.day.isoformat()},'1'*32))
        self.assertEqual(self.post(h,'bad','wf_attach',{'business':self.b,'deal':d})[0],403)
    def test_unknown_role_inputs_and_no_live_api_routes(self):
        d=self.attached()
        with self.assertRaises(ValueError): self.task(d,'AI29')
        h,c=self.headers(); self.assertEqual(self.post(h,c,'wf_send_live',{'business':self.b,'deal':d})[0],400)
    def test_parallel_task_run_has_one_artifact(self):
        t=self.task()
        def run(_):
            st=Store(self.path)
            try: return Office(st).workflow.run(self.owner,self.b,t)
            finally: st.close()
        with ThreadPoolExecutor(max_workers=2) as pool: result=list(pool.map(run,range(2)))
        self.assertEqual(result[0],result[1]); self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0],1)

    def test_draft_body_tampering_and_explicit_rejection(self):
        d,sid=self.seminar(); w=self.o.workflow; a=w.draft(self.owner,self.b,d,sid,'participant','invite')
        self.store.db.execute("UPDATE wf_drafts SET body='changed' WHERE id=?",(a,))
        with self.assertRaises(ValueError): w.decide_draft(self.owner,self.b,a,'approve',True)
        w.decide_draft(self.owner,self.b,a,'reject',True); self.assertEqual(w.queue(self.owner,self.b),[])
    def test_cancelled_seminar_rechecks_old_approval(self):
        d,sid=self.seminar(); w=self.o.workflow; a=w.draft(self.owner,self.b,d,sid,'participant','invite'); w.decide_draft(self.owner,self.b,a,'approve',True)
        self.o.seminars.change_state(self.owner,sid,'cancelled'); self.assertEqual(w.queue(self.owner,self.b),[])

    def test_existing_deletion_quarantines_workflow_and_queued_private_data(self):
        d=self.attached(); t=self.task(d); self.o.workflow.run(self.owner,self.b,t)
        c=self.s.row(self.owner,self.b,'scm_deals',d)['contact_id']; self.o.portfolio.request_delete(self.owner,self.b,c); self.o.portfolio.approve_delete(self.owner,self.b,c,True)
        row=self.o.workflow.task(self.owner,self.b,t); self.assertEqual(row['result'],'論理消去済み'); self.assertEqual(row['paused'],1)
    def test_deadline_and_lost_deals_alerts_do_not_invent_revenue(self):
        d=self.attached(); self.store.db.execute('UPDATE scm_deals SET next_day=? WHERE id=?',(self.day.isoformat(),d))
        self.assertIn(d,self.o.workflow.dashboard(self.owner,self.b)['alerts']['overdue']); self.advance(d,'lost')
        self.assertEqual(self.o.workflow.dashboard(self.owner,self.b)['active_deals'],0)

    def test_mock_attendance_does_not_overwrite_manual_attendance(self):
        d,sid=self.seminar(); past=now()-timedelta(days=1); self.store.db.execute('UPDATE seminars SET start_at=? WHERE id=?',(past.isoformat(),sid))
        self.o.workflow.attendance(self.owner,self.b,d,sid,'participant','attended','合成の人間確認')
        mock=self.attached('mock'); self.s.seminar(self.owner,self.b,mock,sid,'participant','合成の本人確認')
        self.o.workflow.attendance(self.owner,self.b,mock,sid,'participant','absent','モックの確認')
        self.assertEqual(self.store.db.execute("SELECT outcome FROM wf_attendance WHERE mode='manual'").fetchone()[0],'attended')
        self.assertEqual(self.store.db.execute("SELECT outcome FROM wf_attendance WHERE mode='mock'").fetchone()[0],'absent')
    def test_private_workflow_values_are_html_escaped(self):
        t=self.task(); self.store.db.execute("UPDATE wf_tasks SET purpose='<img src=x onerror=alert(1)>' WHERE task_id=?",(t,))
        h,c=self.headers(); response=OfficeApp(self.path,8765).handle('GET','/workflow?business=BIZ-B',h)
        self.assertEqual(response[0],200); self.assertIn(b'&lt;img',response[2]); self.assertNotIn(b'<img src=x',response[2])
    def test_bounded_failure_retries_keep_error_history(self):
        t=self.task()
        for _ in range(3):
            run=self.store.claim(t); self.store.fail(run,'unavailable',retryable=True)
        self.assertEqual(self.o.workflow.task(self.owner,self.b,t)['status'],'failed')
        with self.assertRaises(ValueError): self.o.workflow.run(self.owner,self.b,t)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM runs WHERE task_id=? AND error_code='unavailable'",(t,)).fetchone()[0],3)
