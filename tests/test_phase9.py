import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4
from violet_office.store import Store,now
from violet_office.office import Office
from violet_office.integrations.adapters import Principal
from violet_office.integrations.demo import run_demo
from violet_office.phase9_migrations import migrate
from violet_office.selection import CAPABILITIES
from violet_office.web import OfficeApp,COOKIE
from violet_office.ops import backup,restore


class Phase9Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/'office.db'
        self.store=Store(self.path); self.o=Office(self.store); self.s=self.o.seminars
        self.owner=Principal('representative','synthetic_owner')
        self.viewer=Principal('viewer','synthetic_viewer')
        self.clock=now(); self.key=patch.dict(os.environ,{'SEMINAR_CARD_HMAC_KEY':'synthetic-card-key-only-for-tests-123456'})
        self.key.start()

    def tearDown(self):
        self.key.stop(); self.store.close(); self.temp.cleanup()

    def member(self,name='person1',segment='cast',approved=True,card=''):
        self.o.add_user(name,'participant','SyntheticParticipant!123')
        self.s.account(self.owner,name,'合成 '+name,'synthetic-identity-reference',True)
        self.s.register(name,segment,card)
        if approved: self.s.decide_registration(self.owner,name,'approve')
        return name

    def data(self,**changes):
        start=self.clock+timedelta(days=3)
        data={'title':'合成セミナー','start_at':start.isoformat(),'end_at':(start+timedelta(hours=1)).isoformat(),'location':'合成のオンライン会場','fee_yen':0,'description':'健康・美容に関する商品やサービスを紹介。具体的条件は公式資料を確認。','deadline':(start-timedelta(hours=1)).isoformat(),'audience':'public','capacity':''}
        return {**data,**changes}

    def seminar(self,**changes): return self.s.save(self.owner,self.data(**changes))

    def reminder(self,answer=None):
        person=self.member(); seminar=self.seminar()
        if answer: self.s.respond(person,seminar,answer)
        self.s.consent(person,True); self.s.schedule(self.owner,True)
        with patch('violet_office.seminars.now',return_value=self.clock): self.assertEqual(self.s.tick(self.owner),1)
        return person,seminar,self.store.db.execute('SELECT id FROM seminar_reminders').fetchone()[0]

    def web_session(self,name,role):
        token,csrf=self.o.new_session(name,role)
        return {'Host':'127.0.0.1:8765','Origin':'http://127.0.0.1:8765','Content-Type':'application/x-www-form-urlencoded','Cookie':COOKIE+'='+token},csrf

    def post(self,headers,csrf,action,data):
        return OfficeApp(self.path,8765).handle('POST','/actions/'+action,headers,urlencode({'csrf':csrf,'nonce':uuid4().hex,**data}).encode())

    def test_card_is_hashed_and_manual_approval_required(self):
        self.member(segment='dealer',approved=False,card='VIOLET-012345')
        row=self.store.db.execute('SELECT * FROM seminar_registrations').fetchone()
        self.assertNotIn('VIOLET',row['card_hash']); self.assertEqual(len(row['card_hash']),64)
        self.assertNotIn('VIOLET-012345',''.join(str(tuple(r)) for r in self.store.db.execute('SELECT * FROM seminar_events')))
        with self.assertRaises(PermissionError): self.s.listing('person1')
        self.s.decide_registration(self.owner,'person1','approve'); self.s.listing('person1')

    def test_card_duplicate_normalization_and_key_rotation_fail_closed(self):
        self.member('one','dealer',card='ABC-12345')
        with self.assertRaises(ValueError): self.member('two','user',card='abc 12345')
        with patch.dict(os.environ,{'SEMINAR_CARD_HMAC_KEY':'different-synthetic-card-key-1234567890'}):
            with self.assertRaises(ValueError): self.member('three','user',card='ABC99999')

    def test_card_missing_key_and_cast_no_card(self):
        with patch.dict(os.environ,{'SEMINAR_CARD_HMAC_KEY':''}):
            self.member('cast_person')
            with self.assertRaises(ValueError): self.member('dealer_person','dealer',card='ABCD1234')
        self.assertIsNone(self.store.db.execute("SELECT card_hash FROM seminar_registrations WHERE username='cast_person'").fetchone()[0])

    def test_registration_requires_verified_account_and_never_changes_on_second_visit(self):
        self.o.add_user('unverified','participant','SyntheticParticipant!123')
        with self.assertRaises(PermissionError): self.s.register('unverified','cast','')
        person=self.member()
        before=dict(self.s.member(person)); self.s.listing(person); self.s.listing(person)
        self.assertEqual(before,dict(self.s.member(person)))
        with self.assertRaises(ValueError): self.s.register(person,'dealer','NEW12345')

    def test_every_segment_server_side_access_matrix(self):
        seminars={audience:self.seminar(audience=audience) for audience in ('dealer','user','public')}
        for segment,allowed in [('dealer',3),('subdealer',3),('user',2),('cast',1)]:
            person=self.member(segment,segment,card='' if segment=='cast' else segment+'123456')
            self.assertEqual(len(self.s.listing(person)),allowed)
            for audience,identifier in seminars.items():
                if audience=='dealer' and segment in ('user','cast') or audience=='user' and segment=='cast':
                    with self.assertRaises(PermissionError): self.s.visible(person,identifier)
                    with self.assertRaises(PermissionError): self.s.respond(person,identifier,'yes')
                else: self.s.visible(person,identifier)

    def test_response_changes_latest_and_immutable_history(self):
        p=self.member(); identifier=self.seminar()
        for answer in ('yes','maybe','no','yes'): self.s.respond(p,identifier,answer)
        self.s.respond(p,identifier,'yes')
        row=self.store.db.execute('SELECT * FROM seminar_responses').fetchone()
        self.assertEqual(row['answer'],'yes'); self.assertEqual(row['revision'],4)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM seminar_events WHERE event='response_changed'").fetchone()[0],4)
        self.assertEqual(self.s.recipients(self.owner,identifier)[0]['answer'],'yes')

    def test_unanswered_is_not_saved_as_a_fake_response(self):
        p=self.member(); identifier=self.seminar()
        self.assertEqual(self.s.recipients(self.owner,identifier)[0]['answer'],'unanswered')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM seminar_responses').fetchone()[0],0)
        with self.assertRaises(ValueError): self.s.respond(p,identifier,'unanswered')

    def test_capacity_and_deadline_are_server_enforced(self):
        self.member('one'); self.member('two'); identifier=self.seminar(capacity=1)
        self.s.respond('one',identifier,'yes')
        with self.assertRaises(ValueError): self.s.respond('two',identifier,'yes')
        self.s.respond('one',identifier,'no'); self.s.respond('two',identifier,'yes')
        with patch('violet_office.seminars.now',return_value=self.clock+timedelta(days=4)):
            with self.assertRaises(ValueError): self.s.respond('two',identifier,'no')

    def test_parallel_last_seat_never_double_books(self):
        self.member('one'); self.member('two'); identifier=self.seminar(capacity=1)
        def answer(person):
            store=Store(self.path)
            try:
                office=Office(store)
                try: office.seminars.respond(person,identifier,'yes'); return 'yes'
                except ValueError: return 'full'
            finally: store.close()
        with ThreadPoolExecutor(2) as executor: outcomes=list(executor.map(answer,['one','two']))
        self.assertEqual(sorted(outcomes),['full','yes'])

    def test_edit_revision_invalidates_reminders_and_stale_edit(self):
        _,identifier,reminder=self.reminder('yes')
        self.s.approve_reminder(self.owner,reminder,'approve')
        self.s.save(self.owner,self.data(location='変更後の会場'),identifier,1)
        with self.assertRaises(ValueError): self.s.save(self.owner,self.data(),identifier,1)
        self.assertEqual(self.store.db.execute('SELECT status FROM seminar_reminders').fetchone()[0],'cancelled')

    def test_cancel_and_close_registration(self):
        p=self.member(); identifier=self.seminar()
        self.s.change_state(self.owner,identifier,'closed')
        with self.assertRaises(ValueError): self.s.respond(p,identifier,'yes')
        self.s.change_state(self.owner,identifier,'cancelled')
        with self.assertRaises(ValueError): self.s.save(self.owner,self.data(),identifier,3)

    def test_three_day_drafts_yes_maybe_unanswered_excludes_no(self):
        identifier=self.seminar()
        for answer in ('yes','maybe','unanswered','no'):
            self.member(answer); self.s.consent(answer,True)
            if answer!='unanswered': self.s.respond(answer,identifier,answer)
        self.s.schedule(self.owner,True)
        with patch('violet_office.seminars.now',return_value=self.clock): self.assertEqual(self.s.tick(self.owner),3)
        self.assertEqual({r[0] for r in self.store.db.execute('SELECT username FROM seminar_reminders')},{'yes','maybe','unanswered'})
        self.assertEqual({r[0] for r in self.store.db.execute('SELECT status FROM seminar_reminders')},{'approval_pending'})
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM seminar_attempts').fetchone()[0],0)

    def test_schedule_initially_paused_and_wrong_day_not_caught_up(self):
        self.member(); self.seminar(); self.s.consent('person1',True)
        self.assertEqual(self.s.tick(self.owner),0)
        self.s.schedule(self.owner,True)
        with patch('violet_office.seminars.now',return_value=self.clock+timedelta(days=1)):
            self.assertEqual(self.s.tick(self.owner),0)
        self.s.schedule(self.owner,False)
        self.assertEqual(self.s.tick(self.owner),0)

    def test_no_consent_means_no_reminder(self):
        self.member(); self.seminar(); self.s.schedule(self.owner,True)
        self.assertEqual(self.s.tick(self.owner),0)

    def test_preparation_and_dispatch_deduplicated(self):
        _,_,reminder=self.reminder()
        self.assertEqual(self.s.tick(self.owner),0)
        with self.assertRaises(ValueError): self.s.dispatch_mock(self.owner,reminder)
        self.s.approve_reminder(self.owner,reminder,'approve')
        self.assertEqual(self.s.dispatch_mock(self.owner,reminder),'mock_sent')
        with self.assertRaises(ValueError): self.s.dispatch_mock(self.owner,reminder)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM seminar_attempts').fetchone()[0],1)

    def test_stop_and_reconsent_never_revive_old_approval(self):
        p,_,reminder=self.reminder(); self.s.approve_reminder(self.owner,reminder,'approve')
        self.s.consent(p,False); self.s.consent(p,True)
        with self.assertRaises(ValueError): self.s.dispatch_mock(self.owner,reminder)
        self.assertEqual(self.s.tick(self.owner),0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM seminar_consents').fetchone()[0],3)

    def test_decline_or_registration_revoke_blocks_approved_dispatch(self):
        p,identifier,reminder=self.reminder('maybe'); self.s.approve_reminder(self.owner,reminder,'approve')
        self.s.respond(p,identifier,'no')
        with self.assertRaises(ValueError): self.s.dispatch_mock(self.owner,reminder)
        self.s.decide_registration(self.owner,p,'revoke')
        with self.assertRaises(PermissionError): self.s.listing(p)

    def test_retry_backoff_and_max_attempts_no_network(self):
        _,_,reminder=self.reminder(); self.s.approve_reminder(self.owner,reminder,'approve')
        self.assertEqual(self.s.dispatch_mock(self.owner,reminder,'timeout'),'retry_wait')
        with self.assertRaises(ValueError): self.s.dispatch_mock(self.owner,reminder,'transient')
        for attempt in (2,3):
            with patch('violet_office.seminars.now',return_value=self.clock+timedelta(minutes=attempt*3)):
                self.assertEqual(self.s.dispatch_mock(self.owner,reminder,'transient'),'failed' if attempt==3 else 'retry_wait')
        with self.assertRaises(ValueError): self.s.dispatch_mock(self.owner,reminder)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM seminar_attempts').fetchone()[0],3)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0],0)

    def test_reminder_tamper_or_date_change_blocks_execution(self):
        _,_,reminder=self.reminder(); self.s.approve_reminder(self.owner,reminder,'approve')
        self.store.db.execute("UPDATE seminar_reminders SET body='changed' WHERE id=?",(reminder,))
        self.assertEqual(self.s.dispatch_mock(self.owner,reminder),'cancelled')

    def test_names_only_available_to_representative(self):
        p=self.member(); identifier=self.seminar()
        with self.assertRaises(PermissionError): self.s.recipients(self.viewer,identifier)
        h,csrf=self.web_session(p,'participant'); app=OfficeApp(self.path,8765)
        self.assertEqual(app.handle('GET','/seminar-admin',h)[0],303)
        self.assertNotIn(('合成 '+p).encode(),app.handle('GET','/seminars',h)[2])
        self.assertEqual(self.post(h,csrf,'seminar_account',{'username':'hacked'})[0],403)
        self.assertEqual(self.post(h,csrf,'start',{'id':'unknown'})[0],403)

    def test_web_participant_cannot_forge_other_person_or_audience(self):
        one=self.member('one'); two=self.member('two'); identifier=self.seminar(); private=self.seminar(audience='dealer')
        h,csrf=self.web_session(one,'participant')
        self.assertEqual(self.post(h,csrf,'seminar_member_response',{'id':identifier,'answer':'yes','username':two})[0],303)
        self.assertEqual(self.store.db.execute('SELECT username FROM seminar_responses').fetchone()[0],one)
        self.assertEqual(OfficeApp(self.path,8765).handle('GET','/seminars?id='+private,h)[0],403)
        self.assertEqual(self.post(h,csrf,'seminar_member_response',{'id':private,'answer':'yes'})[0],403)

    def test_web_csrf_nonce_replay_and_session_revocation(self):
        p=self.member(); identifier=self.seminar(); h,csrf=self.web_session(p,'participant'); app=OfficeApp(self.path,8765)
        self.assertEqual(self.post(h,'wrong','seminar_member_response',{'id':identifier,'answer':'yes'})[0],403)
        data=urlencode({'csrf':csrf,'nonce':uuid4().hex,'id':identifier,'answer':'yes'}).encode()
        self.assertEqual(app.handle('POST','/actions/seminar_member_response',h,data)[0],303)
        self.assertEqual(app.handle('POST','/actions/seminar_member_response',h,data)[0],303)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM seminar_responses').fetchone()[0],1)
        self.store.db.execute("UPDATE office_users SET role='viewer' WHERE username=?",(p,))
        self.assertEqual(app.handle('POST','/actions/seminar_member_response',h,data)[0],403)

    def test_seminar_ai_work_has_no_names_and_is_idempotent(self):
        self.member(); identifier=self.seminar()
        tasks=self.s.prepare_work(self.owner,identifier); self.assertEqual(tasks,self.s.prepare_work(self.owner,identifier))
        self.assertEqual(len(tasks),7)
        text=''.join(r[0] for r in self.store.db.execute('SELECT content FROM artifacts'))
        self.assertNotIn('person1',text); self.assertIn('面談成果は未計測',text)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0],0)

    def test_dashboard_filters_counts_and_response_history(self):
        self.member('one'); self.member('two','user',card='ABCD1234'); identifier=self.seminar()
        self.s.respond('one',identifier,'yes'); self.s.respond('two',identifier,'no')
        self.assertEqual(len(self.s.recipients(self.owner,identifier,'user','no')),1)
        self.assertEqual(len(self.s.recipients(self.owner,identifier,'cast','unanswered')),0)
        self.o.add_user('owner','representative','SyntheticRepresentative!123'); h,_=self.web_session('owner','representative')
        response=OfficeApp(self.path,8765).handle('GET','/seminar-admin?segment=user&answer=no',h)
        self.assertEqual(response[0],200); self.assertIn('合成 two'.encode(),response[2])
        self.assertIn('変更履歴'.encode(),response[2])

    def test_selection_roles_preserved_and_no_measurement_fabrication(self):
        self.assertEqual(set(a for values in CAPABILITIES.values() for a in values),set(self.store.agents))
        p=self.o.selection.propose(self.owner,'request','sns',self.clock.date().isoformat(),(self.clock.date()+timedelta(days=2)).isoformat())
        row=self.store.db.execute('SELECT * FROM selection_plans WHERE id=?',(p,)).fetchone()
        self.assertEqual(row['chosen'],'AI09'); self.assertIn('停止中',row['reason'])
        self.assertIsNone(json.loads(row['selection'])['candidates'][0]['metrics']['quality_score'])

    def test_selection_quality_multiple_metrics_and_manual_override(self):
        for agent,score in [('AI09',2),('AI08',5)]:
            for i in range(3):
                t=self.store.create_task(agent+str(i),agent,{'synthetic':True},action='sns_post')
                artifact=self.store.finish(self.store.claim(t),'合成の非個人情報原稿')
                self.store.review(artifact,'AI28','pass')
                self.o.learning.rate(self.owner,'artifact',artifact,score,'正確性')
        self.o.selection.set_enabled(self.owner,True)
        p=self.o.selection.propose(self.owner,'measured','sns',(self.clock.date()-timedelta(days=1)).isoformat(),(self.clock.date()+timedelta(days=2)).isoformat())
        row=self.store.db.execute('SELECT * FROM selection_plans WHERE id=?',(p,)).fetchone()
        self.assertEqual(row['chosen'],'AI08'); self.assertIn('品質評点',row['reason'])
        task=self.o.selection.assign(self.owner,p,'AI09')
        self.assertEqual(task,self.o.selection.assign(self.owner,p,'AI09'))
        self.assertEqual(self.store.db.execute('SELECT action FROM tasks WHERE id=?',(task,)).fetchone()[0],'management_decision')
        self.assertEqual(self.store.db.execute('SELECT attempts FROM tasks WHERE id=?',(task,)).fetchone()[0],0)
        with self.assertRaises(ValueError): self.o.selection.assign(self.owner,p,'AI26')

    def test_selection_can_stop_and_cannot_change_permissions(self):
        before=Path('config/agents.json').read_bytes()
        self.o.selection.set_enabled(self.owner,True); self.o.selection.set_enabled(self.owner,False)
        with self.assertRaises(PermissionError): self.o.selection.set_enabled(self.viewer,True)
        self.assertEqual(Path('config/agents.json').read_bytes(),before)
        self.assertEqual(self.store.db.execute('SELECT limit_microusd FROM budget').fetchone()[0],0)

    def test_migration_repeatable_preserves_existing_tables_and_line_records(self):
        run_demo(self.store)
        tables=['ix_customers','ix_consents','ix_steps','ix_operations','ix_bookings','tasks','artifacts','office_budgets']
        snapshot=lambda:{t:[tuple(r) for r in self.store.db.execute('SELECT * FROM '+t)] for t in tables}
        before=snapshot(); migrate(self.store); Office(self.store); self.member(); self.seminar()
        self.assertEqual(before,snapshot())
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM office_migrations WHERE version=9').fetchone()[0],1)
        self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_restore_pauses_scheduler_selection_and_invalidates_approvals(self):
        _,_,reminder=self.reminder(); self.s.approve_reminder(self.owner,reminder,'approve'); self.o.selection.set_enabled(self.owner,True)
        snapshot=Path(self.temp.name)/'backup.db'; destination=Path(self.temp.name)/'restore.db'
        backup(self.path,snapshot); restore(snapshot,destination)
        db=sqlite3.connect(destination)
        try:
            self.assertEqual(db.execute('SELECT enabled FROM seminar_scheduler').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT enabled FROM selection_settings').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT status FROM seminar_reminders').fetchone()[0],'cancelled')
            self.assertEqual(db.execute('SELECT status FROM seminar_registrations').fetchone()[0],'approval_pending')
        finally: db.close()
        self.assertEqual(self.store.db.execute('SELECT status FROM seminar_reminders').fetchone()[0],'approved')

    def test_upgrade_actual_phase8_schema_preserves_old_rows(self):
        from violet_office.office import SCHEMA as OFFICE_SCHEMA
        from violet_office.integrations.service import SCHEMA as IX_SCHEMA
        from violet_office.content import ContentOffice
        from violet_office.growth import Growth
        from violet_office.learning import Learning
        old=Store(Path(self.temp.name)/'phase8.db')
        try:
            ContentOffice(old); old.db.executescript(IX_SCHEMA); old.db.executescript(OFFICE_SCHEMA)
            Growth(old); Learning(old)
            self.assertIsNone(old.db.execute("SELECT 1 FROM sqlite_master WHERE name='seminars'").fetchone())
            task=old.create_task('phase8-existing','AI09',{'synthetic':'existing'},action='sns_post')
            old.finish(old.claim(task),'以前の成果物')
            tables=[r[0] for r in old.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            snapshot=lambda:{t:[tuple(r) for r in old.db.execute('SELECT * FROM '+t)] for t in tables}
            before=snapshot(); migrate(old); migrate(old)
            self.assertEqual(before,snapshot())
            self.assertEqual(old.db.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(old.db.execute('SELECT version FROM office_migrations').fetchone()[0],9)
        finally: old.close()

    def test_cross_origin_and_viewer_routes_are_denied(self):
        self.o.add_user('reader','viewer','SyntheticViewerOnly!123')
        h,csrf=self.web_session('reader','viewer'); app=OfficeApp(self.path,8765)
        self.assertEqual(app.handle('GET','/seminar-admin',h)[0],403)
        self.assertEqual(app.handle('GET','/selection',h)[0],403)
        self.assertEqual(self.post(h,csrf,'selection_toggle',{'enabled':'1'})[0],403)
        person=self.member(); identifier=self.seminar(); h,csrf=self.web_session(person,'participant')
        h['Origin']='https://attacker.invalid'
        self.assertEqual(self.post(h,csrf,'seminar_member_response',{'id':identifier,'answer':'yes'})[0],403)

    def test_seminar_xss_escaped_and_sql_parameters_not_executed(self):
        person=self.member(); self.seminar(title='<script>alert(1)</script>')
        h,_=self.web_session(person,'participant'); app=OfficeApp(self.path,8765)
        body=app.handle('GET','/seminars',h)[2].decode()
        self.assertIn('&lt;script&gt;',body); self.assertNotIn('<script>',body)
        with self.assertRaises(ValueError): self.s.get("' OR 1=1 --")

    def test_parallel_prepare_workers_have_one_reminder(self):
        self.member(); self.seminar(); self.s.consent('person1',True); self.s.schedule(self.owner,True)
        def prepare(_):
            store=Store(self.path)
            try: return Office(store).seminars.tick(self.owner)
            finally: store.close()
        with ThreadPoolExecutor(2) as executor: results=list(executor.map(prepare,range(2)))
        self.assertEqual(sum(results),1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM seminar_reminders').fetchone()[0],1)

    def test_seminar_day_boundary_is_jst_not_utc(self):
        # October 28 at 15:30 UTC is October 29 at 00:30 JST.
        from datetime import datetime,timezone
        clock=datetime(2030,10,26,14,0,tzinfo=timezone.utc)
        start=datetime(2030,10,28,15,30,tzinfo=timezone.utc)
        with patch('violet_office.seminars.now',return_value=clock):
            person=self.member(); self.s.consent(person,True)
            self.s.save(self.owner,self.data(start_at=start.isoformat(),end_at=(start+timedelta(hours=1)).isoformat(),deadline=(start-timedelta(hours=1)).isoformat()))
            self.s.schedule(self.owner,True); self.assertEqual(self.s.tick(self.owner),1)

    def test_after_three_day_window_old_approval_is_not_executed(self):
        _,_,reminder=self.reminder(); self.s.approve_reminder(self.owner,reminder,'approve')
        with patch('violet_office.seminars.now',return_value=self.clock+timedelta(days=1)):
            self.assertEqual(self.s.dispatch_mock(self.owner,reminder),'cancelled')

    def test_reassign_pauses_old_task_and_preserves_history(self):
        identifier=self.o.selection.propose(self.owner,'change','sns',self.clock.date().isoformat(),(self.clock.date()+timedelta(days=2)).isoformat())
        old=self.o.selection.assign(self.owner,identifier,'AI09')
        new=self.o.selection.assign(self.owner,identifier,'AI08')
        self.assertNotEqual(old,new)
        self.assertEqual(self.store.db.execute('SELECT status FROM tasks WHERE id=?',(old,)).fetchone()[0],'paused')
        self.assertEqual(self.store.db.execute('SELECT status FROM tasks WHERE id=?',(new,)).fetchone()[0],'pending')
        self.assertEqual(self.o.selection.assign(self.owner,identifier,'AI09'),old)
        self.store.claim(old)
        with self.assertRaises(ValueError): self.o.selection.assign(self.owner,identifier,'AI08')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM selection_events').fetchone()[0],3)

    def test_selection_idempotency_and_conflicting_period(self):
        start=self.clock.date().isoformat(); end=(self.clock.date()+timedelta(days=2)).isoformat()
        identifier=self.o.selection.propose(self.owner,'same','sns',start,end)
        self.assertEqual(identifier,self.o.selection.propose(self.owner,'same','sns',start,end))
        with self.assertRaises(ValueError): self.o.selection.propose(self.owner,'same','line',start,end)

    def test_parallel_dispatch_only_one_mock_attempt(self):
        _,_,identifier=self.reminder(); self.s.approve_reminder(self.owner,identifier,'approve')
        def send(_):
            store=Store(self.path)
            try:
                try: return Office(store).seminars.dispatch_mock(self.owner,identifier)
                except ValueError: return 'blocked'
            finally: store.close()
        with ThreadPoolExecutor(2) as executor: result=list(executor.map(send,range(2)))
        self.assertEqual(sorted(result),['blocked','mock_sent'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM seminar_attempts').fetchone()[0],1)

    def test_web_identity_provision_is_atomic_and_requires_attestation(self):
        self.o.add_user('owner','representative','SyntheticOwnerPass!123'); h,csrf=self.web_session('owner','representative')
        response=self.post(h,csrf,'seminar_account',{'username':'not_verified','password':'SyntheticParticipant!123','display_name':'合成の人物','evidence':'synthetic-only'})
        self.assertEqual(response[0],400)
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM office_users WHERE username='not_verified'").fetchone())

    def test_seminar_work_keeps_existing_api_budget_limit(self):
        identifier=self.seminar(); self.s.prepare_work(self.owner,identifier)
        task=self.store.create_task('cannot-spend','AI26',{'synthetic':True})
        with self.assertRaises(ValueError): self.store.claim(task,reserve_microusd=1)
        self.assertEqual(self.store.db.execute('SELECT limit_microusd FROM budget').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT SUM(reserved) FROM reservations').fetchone()[0],0)

    def test_unsupported_health_claim_reminder_cannot_be_approved(self):
        person=self.member(); identifier=self.seminar(description='この商品で病気が治る')
        self.s.consent(person,True); self.s.schedule(self.owner,True); self.s.tick(self.owner)
        reminder=self.store.db.execute('SELECT id FROM seminar_reminders').fetchone()[0]
        with self.assertRaises(ValueError): self.s.approve_reminder(self.owner,reminder,'approve')

    def test_close_invalidates_reminder_and_requires_reprepare(self):
        _,identifier,reminder=self.reminder('yes')
        self.s.change_state(self.owner,identifier,'closed')
        # Closing changes revision and invalidates all previously prepared content.
        self.assertEqual(self.store.db.execute('SELECT status FROM seminar_reminders').fetchone()[0],'cancelled')
        with self.assertRaises(ValueError): self.s.dispatch_mock(self.owner,reminder)

    def test_reprepare_preserves_history_and_requires_fresh_approval(self):
        person,identifier,reminder=self.reminder('yes'); self.s.approve_reminder(self.owner,reminder,'approve')
        self.s.consent(person,False); self.s.consent(person,True)
        self.s.reprepare(self.owner,reminder)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM seminar_reminder_history').fetchone()[0],1)
        with self.assertRaises(ValueError): self.s.dispatch_mock(self.owner,reminder)
        self.s.approve_reminder(self.owner,reminder,'approve')
        self.assertEqual(self.s.dispatch_mock(self.owner,reminder),'mock_sent')
        with self.assertRaises(ValueError): self.s.reprepare(self.owner,reminder)

    def test_changing_body_and_hash_does_not_reuse_old_approval(self):
        from violet_office.content import digest
        _,_,reminder=self.reminder(); self.s.approve_reminder(self.owner,reminder,'approve')
        self.store.db.execute('UPDATE seminar_reminders SET body=?,digest=? WHERE id=?',('changed',digest('changed'),reminder))
        self.assertEqual(self.s.dispatch_mock(self.owner,reminder),'cancelled')

    def test_aggregate_changes_create_new_work_snapshot_preserving_previous_outputs(self):
        person=self.member(); identifier=self.seminar()
        before=self.s.prepare_work(self.owner,identifier)
        self.s.respond(person,identifier,'yes')
        after=self.s.prepare_work(self.owner,identifier)
        self.assertNotEqual(before,after)
        self.assertEqual(len(self.store.db.execute('SELECT * FROM seminar_work').fetchall()),14)
        self.assertEqual(after,self.s.prepare_work(self.owner,identifier))
