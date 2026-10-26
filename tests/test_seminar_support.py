"""Synthetic-only seminar extension integration and security tests."""
import json,os,sqlite3,unittest
from pathlib import Path
from datetime import datetime,timedelta,time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from tests import test_sales as sales_helpers
from violet_office.store import Store,now
from violet_office.office import Office
from violet_office.seminars import JST
from violet_office.integrations.adapters import Principal
from violet_office.seminar_support import migrate,LABELS,ACTIVITIES
from violet_office.web import OfficeApp
from violet_office.ops import backup,restore


class SeminarSupportTests(unittest.TestCase):
    setUp=sales_helpers.SalesTests.setUp
    tearDown=sales_helpers.SalesTests.tearDown
    customer=sales_helpers.SalesTests.customer
    deal=sales_helpers.SalesTests.deal
    quote=sales_helpers.SalesTests.quote
    stage=sales_helpers.SalesTests.stage
    action=sales_helpers.SalesTests.action
    ready=sales_helpers.SalesTests.ready
    invoiced=sales_helpers.SalesTests.invoiced
    operator=sales_helpers.SalesTests.operator
    headers=sales_helpers.SalesTests.headers
    post=sales_helpers.SalesTests.post
    def data(self,days=3,capacity='',title='合成の事業セミナー'):
        start=datetime.combine(self.day+timedelta(days=days),time(18),JST)
        return {'title':title,'start_at':start.isoformat(),'end_at':(start+timedelta(hours=1)).isoformat(),'deadline':(start-timedelta(hours=2)).isoformat(),'location':'合成会場','fee_yen':0,'description':'合成の内容。確認済みの事実と未確認事項を区別する。','capacity':capacity}
    def seminar(self,days=3,mode='mock',segments=None,capacity=''):
        return self.o.seminar_support.save(self.owner,self.b,self.data(days,capacity),segments or ['dealer','subdealer','user','cast'],'https://example.com/meeting',mode)
    def member(self,username='one',segment='cast'):
        self.o.add_user(username,'participant','SyntheticParticipantPass!123'); sem=self.o.seminars
        sem.account(self.owner,username,'合成の参加者','合成の本人確認参照',True)
        with patch.dict(os.environ,{'SEMINAR_CARD_HMAC_KEY':'synthetic-hash-key-for-tests-only-123456789'}): sem.register(username,segment,'' if segment=='cast' else username+'0000')
        sem.decide_registration(self.owner,username,'approve'); sem.consent(username,True)
        return Principal('participant',username)
    def optin(self,p,sid):
        for purpose in ('invite','reminders','followup'): self.o.seminar_support.consent(p,sid,purpose,True)
    def setup_recipient(self,mode='mock'):
        sid=self.seminar(mode=mode); person=self.member(); self.optin(person,sid); return sid,person
    def prepare(self,sid,person,kind='invite'):
        return self.o.seminar_support.prepare(self.owner,self.b,sid,person.identity,kind)
    def approve(self,identifier): self.o.seminar_support.decide(self.owner,self.b,identifier,'approve',True)
    def future(self,sid,hours=1): return datetime.fromisoformat(self.o.seminars.get(sid)['end_at'])+timedelta(hours=hours)

    def test_manual_save_edit_online_targets_and_cancellation(self):
        sid=self.seminar(segments=['cast']); row=self.o.seminar_support.access(self.owner,self.b,sid)
        self.o.seminar_support.save(self.owner,self.b,self.data(title='合成の編集後'),['cast'],'https://example.com/new','mock',12,'08:00',sid,row['revision'])
        self.assertEqual(self.o.seminars.get(sid)['title'],'合成の編集後')
        self.assertEqual(self.o.seminar_support.access(self.owner,self.b,sid)['revision'],2)
        self.o.seminar_support.state(self.owner,self.b,sid,'cancelled'); self.assertEqual(self.o.seminars.get(sid)['status'],'cancelled')
    def test_exact_target_segment_enforced_on_old_and_new_portals(self):
        sid=self.seminar(segments=['dealer']); dealer=self.member('dealer','dealer'); sub=self.member('sub','subdealer'); cast=self.member('cast')
        self.assertEqual(self.o.seminars.visible(dealer.identity,sid)['id'],sid)
        for p in (sub,cast):
            with self.assertRaises(PermissionError): self.o.seminars.visible(p.identity,sid)
            with self.assertRaises(PermissionError): self.o.seminar_support.response(p,sid,'yes')
            self.assertNotIn(sid,[r['id'] for r in self.o.seminars.listing(p.identity)])
        self.assertEqual([r['username'] for r in self.o.seminars.recipients(self.owner,sid)],[dealer.identity])
    def test_unconfirmed_contract_registration_and_fake_principal_rejected(self):
        sid=self.seminar(); self.o.add_user('pending','participant','SyntheticParticipantPass!123'); sem=self.o.seminars; sem.account(self.owner,'pending','合成','合成',True); sem.register('pending','cast','')
        with self.assertRaises(PermissionError): self.o.seminar_support.response(Principal('participant','pending'),sid,'yes')
        with self.assertRaises(PermissionError): self.o.seminar_support.consent(Principal('participant','owner'),sid,'invite',True)
    def test_secret_online_url_and_invalid_inputs_rejected(self):
        for url in ('http://example.com/a','https://user:pass@example.com/a','https://example.com/a?pwd=secret','https://example.com/a#secret','https://localhost/a'):
            with self.assertRaises(ValueError): self.o.seminar_support.save(self.owner,self.b,self.data(),['cast'],url)
        for targets in ([],['unknown'],['cast','cast']):
            with self.assertRaises(ValueError): self.o.seminar_support.save(self.owner,self.b,self.data(),targets)
        with self.assertRaises(ValueError): self.o.seminar_support.save(self.owner,self.b,self.data(),['cast'],lead_hours=0)
    def test_duplicate_application_capacity_and_cancellation_after_deadline(self):
        sid=self.seminar(capacity='1'); one=self.member(); two=self.member('two'); svc=self.o.seminar_support
        svc.response(one,sid,'yes'); svc.response(one,sid,'yes'); self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM seminar_responses').fetchone()[0],1)
        with self.assertRaises(ValueError): svc.response(two,sid,'yes')
        later=self.future(sid)
        with patch('violet_office.seminar_support.now',return_value=later),patch('violet_office.seminars.now',return_value=later): svc.response(one,sid,'cancel')
        self.assertEqual(self.store.db.execute('SELECT answer FROM seminar_responses').fetchone()[0],'no')
    def test_parallel_last_seat_preserves_existing_atomic_rsvp(self):
        sid=self.seminar(capacity='1'); people=[self.member(),self.member('two')]
        def apply(p):
            st=Store(self.path)
            try:
                try: Office(st).seminar_support.response(p,sid,'yes'); return True
                except ValueError: return False
            finally: st.close()
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(apply,people))
        self.assertEqual(sum(results),1)
    def test_separate_purpose_consent_and_global_stop_gate(self):
        sid=self.seminar(); person=self.member()
        with self.assertRaises(ValueError): self.prepare(sid,person)
        self.o.seminar_support.consent(person,sid,'invite',True); draft=self.prepare(sid,person); self.approve(draft)
        self.o.seminars.consent(person.identity,False); self.assertEqual(self.o.seminar_support.queue(self.owner,self.b),[])
    def test_interest_personalization_is_self_reported_and_does_not_cross_businesses(self):
        sid,person=self.setup_recipient(); svc=self.o.seminar_support
        svc.preferences(person,sid,['health','beauty']); draft=self.prepare(sid,person)
        body=self.store.db.execute('SELECT body FROM s14_drafts WHERE id=?',(draft,)).fetchone()[0]; self.assertIn('健康・美容',body)
        with self.assertRaises(ValueError): svc.preferences(person,sid,['medical-history'])
        self.approve(draft); svc.preferences(person,sid,['work']); self.assertEqual(svc.queue(self.owner,self.b),[])
    def test_individual_bulk_skips_decline_and_unconsented_and_no_automatic_send(self):
        sid,one=self.setup_recipient(); two=self.member('two'); self.optin(two,sid); no=self.member('no'); self.optin(no,sid); self.o.seminars.respond('no',sid,'no'); self.member('unconsented')
        result=self.o.seminar_support.bulk(self.owner,self.b,sid,'invite'); self.assertEqual(len(result['ids']),2); self.assertEqual(result['skipped'],2)
        self.assertEqual(set(result['ids']),set(self.o.seminar_support.bulk(self.owner,self.b,sid,'invite')['ids']))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM s14_attempts').fetchone()[0],0)
        self.assertEqual(self.o.seminar_support.queue(self.owner,self.b),[])
    def test_approval_revision_hash_and_deduplication(self):
        sid,p=self.setup_recipient(); d=self.prepare(sid,p); self.approve(d); svc=self.o.seminar_support
        with self.assertRaises(ValueError): svc.decide(self.owner,self.b,d,'approve',True)
        svc.revise(self.owner,self.b,d,'合成の修正原稿。参加は任意です。'); self.assertEqual(svc.queue(self.owner,self.b),[]); self.approve(d)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM s14_draft_history').fetchone()[0],1)
        self.store.db.execute("UPDATE s14_drafts SET body='changed' WHERE id=?",(d,)); self.assertEqual(svc.queue(self.owner,self.b),[])
        with self.assertRaises(ValueError): svc.mock(self.owner,self.b,d)
    def test_mock_success_once_and_parallel_no_double_execution(self):
        sid,p=self.setup_recipient(); d=self.prepare(sid,p); self.approve(d)
        def run(_):
            st=Store(self.path)
            try:
                try: Office(st).seminar_support.mock(self.owner,self.b,d); return True
                except ValueError: return False
            finally: st.close()
        with ThreadPoolExecutor(max_workers=2) as pool: outcomes=list(pool.map(run,range(2)))
        self.assertEqual(sum(outcomes),1); self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM s14_attempts').fetchone()[0],1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0],0)
    def test_bounded_retry_backoff_and_timeout_reconciliation(self):
        sid,p=self.setup_recipient(); d=self.prepare(sid,p); self.approve(d); svc=self.o.seminar_support
        self.assertEqual(svc.mock(self.owner,self.b,d,'transient'),'retry_wait')
        with self.assertRaises(ValueError): svc.mock(self.owner,self.b,d,'transient')
        for i in range(2):
            self.store.db.execute('UPDATE s14_drafts SET next_at=NULL WHERE id=?',(d,)); svc.mock(self.owner,self.b,d,'transient')
        self.assertEqual(self.store.db.execute('SELECT status FROM s14_drafts WHERE id=?',(d,)).fetchone()[0],'failed')
        with self.assertRaises(ValueError): svc.mock(self.owner,self.b,d)
        sid2=self.seminar(); self.optin(p,sid2); d2=self.prepare(sid2,p); self.approve(d2)
        self.assertEqual(svc.mock(self.owner,self.b,d2,'timeout'),'manual_reconciliation')
        with self.assertRaises(ValueError): svc.mock(self.owner,self.b,d2)
    def test_deadline_previous_day_event_day_due_dates_and_scheduling(self):
        sid,p=self.setup_recipient(); svc=self.o.seminar_support; svc.response(p,sid,'yes'); core=self.o.seminars.get(sid); meta=svc.access(self.owner,self.b,sid)
        for kind in ('deadline','previous_day','event_day'):
            d=self.prepare(sid,p,kind); self.approve(d)
            with self.assertRaises(ValueError): svc.mock(self.owner,self.b,d)
        self.assertEqual(svc.tick(self.owner,self.b),0); svc.scheduler(self.owner,self.b,True)
        due=datetime.fromisoformat(svc.due(core,meta,'previous_day'))
        with patch('violet_office.seminar_support.now',return_value=due),patch('violet_office.seminars.now',return_value=due):
            self.assertEqual(svc.tick(self.owner,self.b),1); self.assertEqual(svc.tick(self.owner,self.b),1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM s14_drafts WHERE kind='previous_day'").fetchone()[0],1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM s14_attempts').fetchone()[0],0)
    def test_clock_after_start_is_not_silently_adjusted(self):
        sid,p=self.setup_recipient(); self.o.seminar_support.response(p,sid,'yes'); self.store.db.execute("UPDATE s14_meta SET clock='23:59' WHERE seminar_id=?",(sid,))
        with self.assertRaises(ValueError): self.prepare(sid,p,'event_day')
    def test_three_day_engine_reused_and_purpose_stop_applies_to_old_path(self):
        sid,p=self.setup_recipient(); sem=self.o.seminars; sem.schedule(self.owner,True); sem.tick(self.owner)
        row=self.store.db.execute('SELECT * FROM seminar_reminders').fetchone(); self.assertIsNotNone(row)
        self.o.seminar_support.consent(p,sid,'reminders',False)
        with self.assertRaises(ValueError): sem.approve_reminder(self.owner,row['id'],'approve')
        with self.assertRaises(ValueError): self.o.workflow.draft(self.owner,self.b,'unknown',sid,p.identity,'invite')
    def test_existing_workflow_draft_cannot_bypass_new_purpose_consent(self):
        sid,p=self.setup_recipient(); d=self.deal(); self.o.workflow.attach(self.owner,self.b,d); self.s.seminar(self.owner,self.b,d,sid,p.identity,'合成の本人確認')
        with self.assertRaises(ValueError): self.o.workflow.draft(self.owner,self.b,d,sid,p.identity,'invite')
        self.s.stop(self.owner,self.b,self.s.row(self.owner,self.b,'scm_deals',d)['contact_id'],True)
        with self.assertRaises(ValueError): self.prepare(sid,p)
    def test_after_event_thanks_questions_absence_and_interview_opt_in(self):
        sid,p=self.setup_recipient(); svc=self.o.seminar_support; svc.response(p,sid,'yes')
        with self.assertRaises(ValueError): self.prepare(sid,p,'thanks')
        later=self.future(sid)
        with patch('violet_office.seminar_support.now',return_value=later),patch('violet_office.seminars.now',return_value=later):
            svc.engagement(self.owner,self.b,sid,p.identity,'attended','合成の実参加確認')
            for kind in ('thanks','question','interview'): self.assertTrue(self.prepare(sid,p,kind))
            with self.assertRaises(ValueError): self.prepare(sid,p,'absence')
            svc.request_interview(p,sid)
            svc.engagement(self.owner,self.b,sid,p.identity,'interview_completed','合成の完了確認',(later-timedelta(minutes=10)).isoformat())
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM scm_bookings').fetchone()[0],0)
    def test_absence_is_observed_not_inferred_and_decline_not_reinvited(self):
        sid,p=self.setup_recipient(); svc=self.o.seminar_support; svc.response(p,sid,'yes'); later=self.future(sid)
        with patch('violet_office.seminar_support.now',return_value=later),patch('violet_office.seminars.now',return_value=later):
            with self.assertRaises(ValueError): self.prepare(sid,p,'absence')
            svc.engagement(self.owner,self.b,sid,p.identity,'absent','合成の欠席確認'); self.assertTrue(self.prepare(sid,p,'absence')); svc.response(p,sid,'cancel')
            with self.assertRaises(ValueError): self.prepare(sid,p,'absence')
    def test_interview_completion_requires_prior_explicit_request_and_past_time(self):
        sid,p=self.setup_recipient(); self.o.seminar_support.response(p,sid,'yes')
        with self.assertRaises(ValueError): self.o.seminar_support.engagement(self.owner,self.b,sid,p.identity,'interview_completed','合成',now().isoformat())
        self.o.seminar_support.request_interview(p,sid)
        with self.assertRaises(ValueError): self.o.seminar_support.engagement(self.owner,self.b,sid,p.identity,'interview_completed','合成',(now()+timedelta(days=1)).isoformat())
    def test_real_metrics_denominators_cancel_and_interview(self):
        sid,one=self.setup_recipient('manual'); two=self.member('two'); svc=self.o.seminar_support; svc.response(one,sid,'yes'); svc.response(two,sid,'yes'); svc.response(two,sid,'cancel'); later=self.future(sid)
        with patch('violet_office.seminar_support.now',return_value=later):
            svc.engagement(self.owner,self.b,sid,one.identity,'attended','合成の参加確認'); svc.request_interview(one,sid)
        data=svc.metrics(self.owner,self.b,sid); self.assertEqual(data['actual']['attendance_percent'],50); self.assertEqual(data['actual']['cancellation_percent'],50); self.assertEqual(data['actual']['interview_percent'],100)
        self.assertIsNone(data['sales_net_yen']); self.assertIsNone(data['expense_yen'])
    def test_mock_metrics_and_business_development_forecasts_not_actual(self):
        sid,p=self.setup_recipient(); self.o.seminar_support.response(p,sid,'yes'); data=self.o.seminar_support.metrics(self.owner,self.b,sid)
        self.assertIsNone(data['actual']['applicants']); self.assertEqual(data['mock_preview']['applicants'],1); self.assertIsNone(data['sales_net_yen'])
    def test_financial_attribution_uses_existing_sources_only_and_no_double_count(self):
        sid=self.seminar(mode='manual'); d,q,invoice=self.invoiced(); self.s.seminar(self.owner,self.b,d,sid,None,None); svc=self.o.seminar_support
        svc.attribute(self.owner,self.b,sid,'invoice',invoice['id'],'合成の関連確認')
        expense=self.o.portfolio.ledger(self.owner,self.b,'synthetic-expense',self.day.isoformat(),'expense',1500,'actual','合成の経費申告')
        svc.attribute(self.owner,self.b,sid,'expense',expense,'合成の関連確認'); data=svc.metrics(self.owner,self.b,sid); self.assertEqual(data['sales_net_yen'],6000); self.assertEqual(data['expense_yen'],1500)
        sid2=self.seminar(mode='manual')
        with self.assertRaises(ValueError): svc.attribute(self.owner,self.b,sid2,'expense',expense,'合成')
        with self.assertRaises(ValueError): svc.attribute(self.owner,self.b,sid,'expense','missing','合成')
    def test_mock_cannot_attribute_actual_money_and_cancelled_event_no_new_outcomes(self):
        sid,p=self.setup_recipient(); self.o.seminar_support.response(p,sid,'yes')
        with self.assertRaises(ValueError): self.o.seminar_support.attribute(self.owner,self.b,sid,'invoice','missing','合成')
        self.o.seminar_support.state(self.owner,self.b,sid,'cancelled')
        with self.assertRaises(ValueError): self.o.seminar_support.engagement(self.owner,self.b,sid,p.identity,'interview_requested','合成')
    def test_selected_existing_roles_work_and_budget_stop(self):
        sid=self.seminar(); svc=self.o.seminar_support; before=Path('config/agents.json').read_bytes()
        for i,(activity,choices) in enumerate(ACTIVITIES.items()):
            t=svc.work(self.owner,self.b,sid,activity,str(i),choices[0]); svc.run(self.owner,self.b,t); svc.control(self.owner,self.b,t,'approve')
        self.assertEqual(before,Path('config/agents.json').read_bytes()); self.assertEqual(len(self.store.agents),28)
        self.assertEqual({a for group in ACTIVITIES.values() for a in group},set(self.store.agents))
        with self.assertRaises(ValueError): self.o.seminars.prepare_work(self.owner,sid)
        t=svc.work(self.owner,self.b,sid,'copy','stopped'); svc.control(self.owner,self.b,t,'pause')
        with self.assertRaises(ValueError): svc.run(self.owner,self.b,t)
        with self.assertRaises(ValueError): self.store.claim(t)
        svc.control(self.owner,self.b,t,'resume'); self.store.db.execute('UPDATE portfolio_controls SET halted=1')
        with self.assertRaises(ValueError): svc.run(self.owner,self.b,t)
    def test_role_specialty_idempotency_and_legacy_execution_rejected(self):
        sid=self.seminar(); svc=self.o.seminar_support; t=svc.work(self.owner,self.b,sid,'copy','same','AI17')
        self.assertEqual(t,svc.work(self.owner,self.b,sid,'copy','same','AI17'))
        with self.assertRaises(ValueError): svc.work(self.owner,self.b,sid,'analysis','same')
        with self.assertRaises(ValueError): svc.work(self.owner,self.b,sid,'copy','bad','AI26')
        with self.assertRaises(ValueError): self.o.portfolio.run(self.owner,self.b,t)
        with self.assertRaises(ValueError): self.o.portfolio.task_control(self.owner,self.b,t,'resume')
        with self.assertRaises(ValueError): self.store.claim(t,1)
    def test_business_acl_and_private_recipients_owner_only(self):
        sid,p=self.setup_recipient(); staff=self.operator(); svc=self.o.seminar_support
        self.assertEqual(svc.metrics(staff,self.b,sid)['mode'],'mock')
        with self.assertRaises(PermissionError): svc.metrics(staff,'BIZ-A',sid)
        with self.assertRaises(PermissionError): svc.bulk(staff,self.b,sid,'invite')
        with self.assertRaises(ValueError): svc.metrics(self.owner,'BIZ-A',sid)
    def test_web_auth_csrf_xss_nonce_and_mobile_route(self):
        sid,p=self.setup_recipient(); h,csrf=self.headers(); app=OfficeApp(self.path,8765)
        self.store.db.execute("UPDATE seminars SET title='<img src=x onerror=alert(1)>' WHERE id=?",(sid,))
        response=app.handle('GET','/seminar-office?business=BIZ-B',h); self.assertEqual(response[0],200); self.assertIn(b'&lt;img',response[2]); self.assertNotIn(b'<img src=x',response[2])
        self.assertEqual(self.post(h,'bad','s14_state',{'business':self.b,'seminar':sid,'state':'closed'})[0],403)
        first=self.post(h,csrf,'s14_schedule',{'business':self.b,'enabled':'1'},'2'*32); self.assertEqual(first,self.post(h,csrf,'s14_schedule',{'business':self.b,'enabled':'1'},'2'*32))
        staff=self.operator(); sh,sc=self.headers('staff','business_operator'); self.assertEqual(app.handle('GET','/seminar-office?business=BIZ-A',sh)[0],403)
        self.assertEqual(self.post(sh,sc,'s14_state',{'business':self.b,'seminar':sid,'state':'cancelled'})[0],403)
    def test_participant_cannot_impersonate_other_or_access_admin(self):
        sid,p=self.setup_recipient(); self.member('two'); h,csrf=self.headers('one','participant')
        self.post(h,csrf,'s14_member_response',{'id':sid,'answer':'yes','username':'two'})
        self.assertEqual(self.store.db.execute('SELECT username FROM seminar_responses').fetchone()[0],'one')
        self.assertEqual(OfficeApp(self.path,8765).handle('GET','/seminar-office?business=BIZ-B',h)[0],303)
    def test_restore_holds_schedule_approvals_and_preserves_rsvp(self):
        sid,p=self.setup_recipient(); svc=self.o.seminar_support; svc.response(p,sid,'yes'); d=self.prepare(sid,p); self.approve(d); svc.scheduler(self.owner,self.b,True); t=svc.work(self.owner,self.b,sid,'copy','restore'); svc.run(self.owner,self.b,t); svc.control(self.owner,self.b,t,'approve')
        snap=Path(self.temp.name)/'snapshot.db'; dst=Path(self.temp.name)/'recovered.db'; backup(self.path,snap); restore(snap,dst); db=sqlite3.connect(dst)
        try:
            self.assertEqual(db.execute('SELECT enabled FROM s14_scheduler').fetchone()[0],0); self.assertEqual(db.execute('SELECT status FROM s14_drafts').fetchone()[0],'approval_pending'); self.assertEqual(db.execute('SELECT paused FROM s14_work').fetchone()[0],1); self.assertEqual(db.execute('SELECT answer FROM seminar_responses').fetchone()[0],'yes')
        finally: db.close()
    def test_migration_preserves_phase13_rows_and_digital_product_plan(self):
        before_plan=Path('docs/phase-13-5-digital-products-plan.md').read_bytes(); d=self.deal(); self.o.workflow.attach(self.owner,self.b,d)
        for name in ('s14_draft_history','s14_attempts','s14_work','s14_drafts','s14_engagement','s14_preferences','s14_consents','s14_attributions','s14_scheduler','s14_events','s14_meta'): self.store.db.execute('DROP TABLE '+name)
        self.store.db.execute('DELETE FROM office_migrations WHERE version=14'); tables=[r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='office_migrations'")]
        snapshot=lambda:{t:[tuple(r) for r in self.store.db.execute('SELECT * FROM '+t)] for t in tables}
        before=snapshot(); migrate(self.store); migrate(self.store); self.assertEqual(before,snapshot()); self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(),[]); self.assertEqual(before_plan,Path('docs/phase-13-5-digital-products-plan.md').read_bytes())
    def test_unattribution_keeps_source_and_prevents_silent_deletion(self):
        sid=self.seminar(mode='manual'); svc=self.o.seminar_support
        source=self.o.portfolio.ledger(self.owner,self.b,'test-unlink',self.day.isoformat(),'expense',100,'actual','合成')
        aid=svc.attribute(self.owner,self.b,sid,'expense',source,'合成の関連')
        with self.assertRaises(ValueError): svc.unattribute(self.owner,self.b,aid,False)
        svc.unattribute(self.owner,self.b,aid,True); self.assertIsNone(svc.metrics(self.owner,self.b,sid)['expense_yen'])
        self.assertEqual(self.store.db.execute('SELECT amount_yen FROM portfolio_ledger WHERE id=?',(source,)).fetchone()[0],100)
    def test_unsupported_income_and_legal_claims_never_approved(self):
        sid,p=self.setup_recipient(); draft=self.prepare(sid,p)
        for body in ('収入保証です','ネットワークビジネスではありません','健康効果を保証します'):
            with self.assertRaises(ValueError): self.o.seminar_support.revise(self.owner,self.b,draft,body)
        self.o.seminar_support.response(p,sid,'yes'); self.o.seminar_support.state(self.owner,self.b,sid,'cancelled')
        with self.assertRaises(ValueError): self.o.seminar_support.request_interview(p,sid)

    def test_internal_work_rejection_and_emergency_blocks_approval(self):
        sid=self.seminar(); svc=self.o.seminar_support; task=svc.work(self.owner,self.b,sid,'copy','reject'); svc.run(self.owner,self.b,task)
        self.store.db.execute('UPDATE portfolio_controls SET halted=1')
        with self.assertRaises(ValueError): svc.control(self.owner,self.b,task,'approve')
        self.store.db.execute('UPDATE portfolio_controls SET halted=0'); svc.control(self.owner,self.b,task,'reject')
        self.assertEqual(self.store.db.execute('SELECT status FROM tasks WHERE id=?',(task,)).fetchone()[0],'rejected')
