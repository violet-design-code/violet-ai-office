"""Scoped collaboration and fail-closed approvals on synthetic temporary databases."""
import json,sqlite3,unittest
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch
from tests import test_sales as helpers
from violet_office.workflow_collaboration import migrate,guard_task
from violet_office.web import OfficeApp
from violet_office.ops import backup,restore
from violet_office.store import now


class CollaborationTests(unittest.TestCase):
    setUp=helpers.SalesTests.setUp
    tearDown=helpers.SalesTests.tearDown
    deal=helpers.SalesTests.deal
    customer=helpers.SalesTests.customer
    operator=helpers.SalesTests.operator
    headers=helpers.SalesTests.headers
    post=helpers.SalesTests.post
    def assign(self,b=None):
        b=b or self.b;f=self.o.business_foundation;f.adopt(self.owner,b,'合成検証')
        for a in ('AI17','AI20','AI18','AI14','AI23','AI24','AI28'):f.assign(self.owner,b,a,'合成役割','合成事業範囲',['business_read','work'])
    def job(self,kind='inquiry',key='one',b=None):
        b=b or self.b;d=self.deal('mock',b);return self.o.collaboration.submit(self.owner,b,key,d,kind,self.day.isoformat())
    def cap(self,available=False):return {'agent':'AI17','kind':'inquiry','scope':'合成の内部業務範囲','available':available}
    def test_28_agents_and_existing_assignments_not_automatically_changed(self):
        registry=json.dumps(self.store.agents,sort_keys=True);self.job();self.assertEqual(registry,json.dumps(self.store.agents,sort_keys=True));self.assertEqual(len(self.store.agents),28);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM bf_ai_assignments').fetchone()[0],0)
    def test_no_employee_routes_to_administrator_waiting_without_fake_execution(self):
        identifier=self.job();row=self.o.collaboration.row(self.owner,self.b,identifier);self.assertEqual(row['state'],'waiting_admin');self.assertIsNone(row['task_id']);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM runs').fetchone()[0],0)
    def test_all_five_kinds_follow_existing_role_configuration(self):
        self.assign();expected={'inquiry':{'AI17','AI20'},'consult':{'AI18','AI14'},'candidate':{'AI23'},'booking_check':{'AI23'},'followup':{'AI24','AI20'}}
        for kind,agents in expected.items():
            identifier=self.job(kind,kind);row=self.o.collaboration.row(self.owner,self.b,identifier);self.assertIn(row['agent'],agents);self.assertEqual(row['state'],'ready')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM scm_bookings').fetchone()[0],0)
    def test_new_businesses_use_same_service_without_business_names_in_code(self):
        f=self.o.business_foundation;b=f.create(self.owner,'new','合成新事業','合成概要','合成カテゴリ');self.assign(b);identifier=self.job(b=b);self.assertEqual(self.o.collaboration.row(self.owner,b,identifier)['business_id'],b)
    def test_same_employee_can_work_multiple_businesses_without_data_mix(self):
        self.assign();self.assign('BIZ-A');one=self.job();two=self.job(b='BIZ-A');a=self.o.collaboration.row(self.owner,self.b,one);z=self.o.collaboration.row(self.owner,'BIZ-A',two);self.assertEqual(a['agent'],z['agent']);staff=self.operator()
        with self.assertRaises(PermissionError):self.o.collaboration.listing(staff,'BIZ-A')
        self.assertEqual(len(self.o.collaboration.listing(staff,self.b)),1)
    def test_no_consent_waiting_then_explicit_consent_dispatches(self):
        self.assign();d=self.deal('mock');c=self.s.row(self.owner,self.b,'scm_deals',d)['contact_id'];self.o.portfolio.consent(self.owner,self.b,c,'contact',False,'合成撤回');identifier=self.o.collaboration.submit(self.owner,self.b,'x',d,'inquiry',self.day.isoformat());self.assertIsNone(self.o.collaboration.row(self.owner,self.b,identifier)['task_id']);self.o.portfolio.consent(self.owner,self.b,c,'contact',True,'合成の再同意');self.assertTrue(self.o.collaboration.dispatch(self.owner,self.b,identifier))
    def test_job_duplicate_and_changed_key_conflict(self):
        self.assign();identifier=self.job();row=self.o.collaboration.row(self.owner,self.b,identifier);self.assertEqual(identifier,self.o.collaboration.submit(self.owner,self.b,'one',row['deal_id'],'inquiry',self.day.isoformat()))
        with self.assertRaises(ValueError):self.o.collaboration.submit(self.owner,self.b,'one',row['deal_id'],'consult',self.day.isoformat())
    def test_handoff_history_current_owner_and_old_task_blocked(self):
        self.assign();identifier=self.job();old=self.o.collaboration.row(self.owner,self.b,identifier);hid=self.o.collaboration.handoff(self.owner,self.b,identifier,'h','AI20','inquiry','合成の負荷分散');current=self.o.collaboration.row(self.owner,self.b,identifier);self.assertEqual(current['agent'],'AI20');history=self.store.db.execute('SELECT * FROM aw_handoffs WHERE id=?',(hid,)).fetchone();self.assertEqual(history['from_agent'],'AI17');self.assertEqual(history['business_id'],self.b)
        self.o.workflow.control(self.owner,self.b,old['task_id'],'resume')
        with self.assertRaises(PermissionError):self.store.claim(old['task_id'])
    def test_handoff_idempotency_and_loop_prevention(self):
        self.assign();identifier=self.job();args=(self.owner,self.b,identifier,'h','AI20','inquiry','合成');hid=self.o.collaboration.handoff(*args);self.assertEqual(hid,self.o.collaboration.handoff(*args));self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM aw_handoffs').fetchone()[0],1)
        with self.assertRaises(ValueError):self.o.collaboration.handoff(self.owner,self.b,identifier,'loop','AI17','inquiry','合成')
    def test_handoff_permission_failure_and_business_scope_rejected(self):
        self.assign();identifier=self.job();self.store.db.execute("UPDATE bf_ai_assignments SET active=0 WHERE business_id=? AND agent='AI20'",(self.b,))
        with self.assertRaises(PermissionError):self.o.collaboration.handoff(self.owner,self.b,identifier,'h','AI20','inquiry','合成')
        with self.assertRaises(ValueError):self.o.collaboration.handoff(self.owner,'BIZ-A',identifier,'h','AI20','inquiry','合成')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM aw_handoffs').fetchone()[0],0)
    def test_handoff_artifact_requires_review_and_preserves_dependency(self):
        self.assign();identifier=self.job();a=self.o.collaboration;source=a.row(self.owner,self.b,identifier);a.run(self.owner,self.b,identifier)
        with self.assertRaises(ValueError):a.handoff(self.owner,self.b,identifier,'h','AI20','inquiry','合成')
        self.o.workflow.decide_task(self.owner,self.b,source['task_id'],'approve',True);a.handoff(self.owner,self.b,identifier,'h','AI20','inquiry','合成');target=a.row(self.owner,self.b,identifier);self.assertEqual(self.store.db.execute('SELECT prerequisite FROM dependencies WHERE task_id=?',(target['task_id'],)).fetchone()[0],source['task_id']);self.assertTrue(a.run(self.owner,self.b,identifier))
    def test_work_run_deduplicated_and_complete_requires_representative_approval(self):
        self.assign();identifier=self.job();a=self.o.collaboration;artifact=a.run(self.owner,self.b,identifier);self.assertEqual(artifact,a.run(self.owner,self.b,identifier))
        with self.assertRaises(ValueError):a.control(self.owner,self.b,identifier,'complete')
        row=a.row(self.owner,self.b,identifier);self.o.workflow.decide_task(self.owner,self.b,row['task_id'],'approve',True);a.control(self.owner,self.b,identifier,'complete');self.assertEqual(a.row(self.owner,self.b,identifier)['state'],'done')
    def test_assignment_and_stop_changes_block_existing_internal_tasks(self):
        self.assign();identifier=self.job();row=self.o.collaboration.row(self.owner,self.b,identifier);self.store.db.execute('UPDATE bf_ai_assignments SET active=0 WHERE business_id=? AND agent=?',(self.b,row['agent']))
        with self.assertRaises(PermissionError):self.o.collaboration.run(self.owner,self.b,identifier)
        self.store.db.execute('UPDATE bf_ai_assignments SET active=1');d=self.s.row(self.owner,self.b,'scm_deals',row['deal_id']);self.s.stop(self.owner,self.b,d['contact_id'],True)
        with self.assertRaises(ValueError):self.o.collaboration.run(self.owner,self.b,identifier)
    def test_capability_change_does_not_apply_before_representative_review(self):
        self.assign();a=self.o.collaboration;cid=a.propose(self.owner,self.b,'c','capability',self.cap());self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM aw_capabilities').fetchone()[0],0)
        with self.assertRaises(ValueError):a.apply_capability(self.owner,self.b,cid)
        a.decide(self.owner,self.b,cid,'approve',True);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM aw_capabilities').fetchone()[0],0);a.apply_capability(self.owner,self.b,cid);identifier=self.job();self.assertEqual(a.row(self.owner,self.b,identifier)['agent'],'AI20')
    def test_rejected_capability_and_changed_snapshot_do_not_apply(self):
        self.assign();a=self.o.collaboration;cid=a.propose(self.owner,self.b,'c','capability',self.cap());a.decide(self.owner,self.b,cid,'reject',True)
        with self.assertRaises(ValueError):a.apply_capability(self.owner,self.b,cid)
        cid=a.propose(self.owner,self.b,'c2','capability',self.cap());a.decide(self.owner,self.b,cid,'approve',True);self.store.db.execute('UPDATE portfolio_businesses SET revision=revision+1 WHERE id=?',(self.b,))
        with self.assertRaises(PermissionError):a.apply_capability(self.owner,self.b,cid)
    def test_sensitive_operations_have_no_automatic_executor_even_after_approval(self):
        a=self.o.collaboration
        for kind in ('external_send','booking_confirm','pricing','business_settings','permissions'):
            cid=a.propose(self.owner,self.b,kind,kind,{'reference':'合成の内部資料参照','reason':'合成の検討依頼'});a.decide(self.owner,self.b,cid,'approve',True)
            with self.assertRaises(ValueError):a.apply_capability(self.owner,self.b,cid)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM scm_actions').fetchone()[0],0);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM bf_ai_assignments').fetchone()[0],0)
    def test_phase17_unverified_terms_cannot_be_approved_for_publication(self):
        a=self.o.collaboration;cid=a.propose(self.owner,self.b,'terms','terms_publish',{'reference':'原文未確認の公式契約','reason':'合成の確認依頼'})
        with self.assertRaises(ValueError):a.decide(self.owner,self.b,cid,'approve',True)
    def test_cross_business_share_needs_both_purpose_consents_and_verified_link(self):
        a=self.o.collaboration;c=self.customer();cid=a.propose(self.owner,self.b,'share','cross_business_share',{'reference':c,'reason':'合成の本人希望確認','target_business':'BIZ-A'})
        with self.assertRaises(ValueError):a.decide(self.owner,self.b,cid,'approve',True)
        other=self.customer('BIZ-A')
        for b,contact in ((self.b,c),('BIZ-A',other)):self.o.portfolio.consent(self.owner,b,contact,'identity_link',True,'合成本人確認・別目的同意')
        self.s.link(self.owner,self.b,c,'BIZ-A',other,'合成の代表本人確認');a.decide(self.owner,self.b,cid,'approve',True);self.assertEqual(self.store.db.execute('SELECT status FROM aw_changes WHERE id=?',(cid,)).fetchone()[0],'approved_manual_only')
    def test_retry_delay_bounded_stop_and_secret_free_errors(self):
        self.assign();identifier=self.job();a=self.o.collaboration
        with patch.object(self.o.workflow,'run',side_effect=RuntimeError('token=SHOULD_NOT_APPEAR')):
            self.assertIsNone(a.run(self.owner,self.b,identifier));self.assertEqual(a.row(self.owner,self.b,identifier)['state'],'retry_wait')
            with self.assertRaises(ValueError):a.run(self.owner,self.b,identifier)
            for _ in range(2):self.store.db.execute('UPDATE aw_jobs SET next_at=?',((now()-timedelta(seconds=1)).isoformat(),));self.assertIsNone(a.run(self.owner,self.b,identifier))
        self.assertEqual(a.row(self.owner,self.b,identifier)['attempts'],3);self.assertEqual(a.row(self.owner,self.b,identifier)['state'],'failed');self.assertNotIn('SHOULD_NOT_APPEAR',str([tuple(r) for r in self.store.db.execute('SELECT * FROM aw_events')]))
    def test_failure_retry_can_succeed_without_repeating_artifact(self):
        self.assign();identifier=self.job();a=self.o.collaboration
        with patch.object(self.o.workflow,'run',side_effect=RuntimeError('synthetic')):a.run(self.owner,self.b,identifier)
        self.store.db.execute('UPDATE aw_jobs SET next_at=?',((now()-timedelta(seconds=1)).isoformat(),));artifact=a.run(self.owner,self.b,identifier);self.assertTrue(artifact);self.assertEqual(a.row(self.owner,self.b,identifier)['attempts'],2)
    def test_pause_resume_never_bypasses_scope_or_retry_limit(self):
        self.assign();identifier=self.job();a=self.o.collaboration;row=a.row(self.owner,self.b,identifier);a.control(self.owner,self.b,identifier,'pause')
        with self.assertRaises(ValueError):a.run(self.owner,self.b,identifier)
        a.control(self.owner,self.b,identifier,'resume');self.store.db.execute('UPDATE bf_ai_assignments SET active=0 WHERE agent=?',(row['agent'],))
        with self.assertRaises(PermissionError):a.run(self.owner,self.b,identifier)
    def test_existing_task_assignment_control_cannot_bypass_handoff(self):
        self.assign();identifier=self.job();task=self.o.collaboration.row(self.owner,self.b,identifier)['task_id']
        with self.assertRaises(ValueError):self.o.workflow.control(self.owner,self.b,task,'assign','AI20')
    def test_http_acl_csrf_json_and_owner_approval_only(self):
        self.assign();identifier=self.job();h,c=self.headers();app=OfficeApp(self.path,8765);self.assertEqual(app.handle('GET','/ai-workflow?business='+self.b,h)[0],200);r=app.handle('GET','/ai-workflow-data?business='+self.b,h);self.assertEqual(r[0],200);self.assertNotIn('合成顧客',r[2].decode());self.assertFalse(json.loads(r[2])['external_execution']);self.assertEqual(app.handle('GET','/ai-workflow-data?business='+self.b+'&agent=AI01',h)[0],400)
        self.operator();staff,csrf=self.headers('staff','business_operator');self.assertEqual(app.handle('GET','/ai-workflow-data?business=BIZ-A',staff)[0],403);cid=self.o.collaboration.propose(self.owner,self.b,'a','pricing',{'reference':'合成','reason':'合成'})
        self.assertEqual(self.post(staff,csrf,'aw_decide',{'business':self.b,'id':cid,'decision':'approve','checked':1})[0],403);self.assertEqual(self.post(h,'bad','aw_control',{'business':self.b,'job':identifier,'control':'pause'})[0],403)
    def test_migration_preserves_prior_rows_and_repeatable(self):
        self.customer();db=self.store.db
        for t in ('aw_events','aw_changes','aw_handoffs','aw_tasks','aw_jobs','aw_capabilities'):db.execute('DROP TABLE '+t)
        db.execute('DELETE FROM office_migrations WHERE version=18');tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='office_migrations'")];snapshot=lambda:{t:[tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in tables};before=snapshot();migrate(self.store);migrate(self.store);self.assertEqual(before,snapshot());self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
    def test_restore_pauses_jobs_preserves_history_and_invalidates_pending_execution(self):
        self.assign();identifier=self.job();self.o.collaboration.handoff(self.owner,self.b,identifier,'h','AI20','inquiry','合成');cid=self.o.collaboration.propose(self.owner,self.b,'p','pricing',{'reference':'合成','reason':'合成'});self.o.collaboration.decide(self.owner,self.b,cid,'approve',True);src=Path(self.temp.name)/'backup.db';dst=Path(self.temp.name)/'restore.db';backup(self.path,src);restore(src,dst);db=sqlite3.connect(dst)
        try:self.assertEqual(db.execute('SELECT state FROM aw_jobs').fetchone()[0],'paused');self.assertEqual(db.execute('SELECT COUNT(*) FROM aw_handoffs').fetchone()[0],1);self.assertEqual(db.execute('SELECT status FROM aw_changes').fetchone()[0],'approval_pending')
        finally:db.close()
    def test_invalid_payload_secret_and_cross_scope_db_rejected(self):
        a=self.o.collaboration
        for payload in ({'reference':'token=secretvalue','reason':'合成'},{'reference':'合成','reason':'合成','customer_body':'private'}):
            with self.assertRaises(ValueError):a.propose(self.owner,self.b,'bad','pricing',payload)
        with self.assertRaises(ValueError):a.propose(self.owner,self.b,'share','cross_business_share',{'reference':'合成','reason':'合成'})
        foreign=self.deal('mock','BIZ-A')
        with self.assertRaises(sqlite3.IntegrityError):self.store.db.execute("INSERT INTO aw_jobs VALUES(?,?,?,?,?,?,?,NULL,NULL,'waiting_admin',0,NULL,NULL,0,?)",(self.b,'wrong',foreign,'wrong','inquiry',self.day.isoformat(),3,now().isoformat()))

    def test_submit_replay_remains_idempotent_after_kind_handoff(self):
        self.assign();identifier=self.job();row=self.o.collaboration.row(self.owner,self.b,identifier);self.o.collaboration.handoff(self.owner,self.b,identifier,'next','AI18','consult','本人希望の合成相談');self.assertEqual(identifier,self.o.collaboration.submit(self.owner,self.b,'one',row['deal_id'],'inquiry',self.day.isoformat()))
    def test_maximum_handoffs_stop_without_new_task(self):
        self.assign();identifier=self.job();self.store.db.execute('UPDATE aw_jobs SET generation=5 WHERE id=?',(identifier,))
        with self.assertRaises(ValueError):self.o.collaboration.handoff(self.owner,self.b,identifier,'h','AI20','inquiry','合成')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM aw_handoffs').fetchone()[0],0)
    def test_revoked_independent_reviewer_blocks_approval(self):
        self.assign();identifier=self.job();row=self.o.collaboration.row(self.owner,self.b,identifier);self.o.collaboration.run(self.owner,self.b,identifier);self.store.db.execute("UPDATE bf_ai_assignments SET active=0 WHERE agent='AI28'")
        with self.assertRaises(PermissionError):self.o.workflow.decide_task(self.owner,self.b,row['task_id'],'approve',True)
    def test_two_workers_do_not_duplicate_job_or_task(self):
        from concurrent.futures import ThreadPoolExecutor
        from violet_office.store import Store
        from violet_office.office import Office
        self.assign();d=self.deal('mock')
        def worker(_):
            store=Store(self.path)
            try:return Office(store).collaboration.submit(self.owner,self.b,'parallel',d,'inquiry',self.day.isoformat())
            finally:store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(worker,range(2)))
        self.assertEqual(ids[0],ids[1]);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM aw_tasks').fetchone()[0],1)

    def test_timeout_stops_safely_without_api_success_claim(self):
        self.assign();identifier=self.job()
        with patch.object(self.o.workflow,'run',side_effect=TimeoutError('private diagnostic')):self.assertIsNone(self.o.collaboration.run(self.owner,self.b,identifier))
        row=self.o.collaboration.row(self.owner,self.b,identifier);self.assertEqual(row['state'],'retry_wait');self.assertEqual(row['error_code'],'local_work_failed');self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0],0)

    def test_database_rejects_work_task_from_another_business(self):
        self.assign();self.assign('BIZ-A');identifier=self.job();d=self.deal('mock','BIZ-A');self.o.workflow.attach(self.owner,'BIZ-A',d);task=self.o.workflow.assign(self.owner,'BIZ-A',d,'foreign','AI17','合成','合成',self.day.isoformat())
        with self.assertRaises(sqlite3.IntegrityError):self.store.db.execute('INSERT INTO aw_tasks VALUES(?,?,?)',(task,self.b,identifier))
