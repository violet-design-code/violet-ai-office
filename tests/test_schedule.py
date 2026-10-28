"""Private schedule and JST reminders; isolated SQLite, no actual delivery or audio."""
import json,sqlite3,unittest,time
from datetime import datetime,timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode
from uuid import uuid4
from unittest.mock import patch
from tests import test_sales as helpers
from violet_office.schedule import migrate,local_input
from violet_office.schedule_calendar import export
from violet_office.schedule_worker import start as start_worker
from violet_office.store import Store
from violet_office.office import Office
from violet_office.integrations.adapters import Principal
from violet_office.web import OfficeApp
from violet_office.ops import backup,restore
from violet_office.seminars import JST

class ScheduleTests(unittest.TestCase):
    setUp=helpers.SalesTests.setUp
    tearDown=helpers.SalesTests.tearDown
    headers=helpers.SalesTests.headers
    operator=helpers.SalesTests.operator
    customer=helpers.SalesTests.customer
    def at(self,value='2026-11-05T14:00:00'):return datetime.fromisoformat(value).replace(tzinfo=JST)
    def event(self,key=None,**kw):return self.o.schedule.save(self.owner,key or uuid4().hex,kw.pop('title','合成打ち合わせ'),kw.pop('start',self.at()),kw.pop('end',self.at()+timedelta(hours=1)),person='合成の相手',location='合成の会場',checked=True,**kw)
    def enabled(self):self.o.schedule.settings(self.owner,True,True,True)
    def query(self,when=None):return self.o.schedule.interpret(self.owner,'明日の予定は？',when or self.at('2026-11-04T20:00:00'))
    def post(self,data,user='owner',role='representative',csrf=True):
        h,c=self.headers(user,role);return OfficeApp(self.path,8765).handle('POST','/schedule-action',h,urlencode({**({'csrf':c} if csrf else {}),**data}).encode())
    def save_form(self,**changes):return {'action':'save','key':uuid4().hex,'title':'合成の打ち合わせ','start':'2026-11-05T14:00','end':'2026-11-05T15:00','person':'合成の相手','location':'合成会場','checked':'1',**changes}
    def test_registry_and_existing_business_rows_unchanged(self):
        registry=json.dumps(self.store.agents,sort_keys=True);before=[tuple(r) for r in self.store.db.execute('SELECT * FROM portfolio_businesses')];self.event();self.assertEqual(before,[tuple(r) for r in self.store.db.execute('SELECT * FROM portfolio_businesses')]);self.assertEqual(registry,json.dumps(self.store.agents,sort_keys=True));self.assertEqual(len(self.store.agents),28)
    def test_real_sqlite_save_and_success_only_after_save(self):
        reply=self.post(self.save_form());self.assertEqual(reply[0],200);self.assertIn('保存しました',reply[2].decode());self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM sc_events').fetchone()[0],1)
    def test_missing_end_no_false_success(self):
        reply=self.post(self.save_form(end=''));self.assertEqual(reply[0],400);self.assertNotIn('保存しました',reply[2].decode());self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM sc_events').fetchone()[0],0)
    def test_explicit_confirmation_required(self):
        self.assertEqual(self.post(self.save_form(checked=''))[0],400)
    def test_text_instruction_is_unsaved_candidate(self):
        r=self.o.schedule.interpret(self.owner,'11月5日の14時から石川さんと佐久市で打ち合わせするから予定入れて',self.at('2026-10-28T09:00:00'));self.assertEqual(r['intent'],'draft');self.assertEqual(r['candidate']['person'],'石川さん');self.assertEqual(r['candidate']['location'],'佐久市');self.assertTrue(any('終了' in q for q in r['questions']));self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM sc_events').fetchone()[0],0)
    def test_missing_date_and_time_asks(self):
        r=self.o.schedule.interpret(self.owner,'打ち合わせを登録して');self.assertTrue(any('開始' in q for q in r['questions']))
    def test_registration_replay_and_conflict_key(self):
        a=self.event('same');self.assertEqual(a,self.event('same'))
        with self.assertRaises(ValueError):self.event('same',title='合成の別内容')
    def test_overlap_rejected_across_businesses(self):
        self.event(business='BIZ-A')
        with self.assertRaises(ValueError):self.event(business='BIZ-B')
    def test_adjacent_slots_are_allowed(self):
        self.event();self.event(start=self.at()+timedelta(hours=1),end=self.at()+timedelta(hours=2));self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM sc_events').fetchone()[0],2)
    def test_naive_or_reverse_datetime_rejected(self):
        for a,z in ((datetime(2026,11,5,14),self.at()+timedelta(hours=1)),(self.at(),self.at()-timedelta(hours=1))):
            with self.assertRaises(ValueError):self.event(start=a,end=z)
    def test_update_and_optimistic_conflict(self):
        ref=self.event();s=self.o.schedule;s.save(self.owner,'edit','合成の改訂',self.at()+timedelta(hours=2),self.at()+timedelta(hours=3),identifier=ref,revision=1,checked=True);self.assertEqual(s.row(self.owner,ref)['revision'],2)
        with self.assertRaises(ValueError):s.save(self.owner,'stale','合成',self.at(),self.at()+timedelta(hours=1),identifier=ref,revision=1,checked=True)
    def test_cancel_logical_preserves_history_and_frees_slot(self):
        ref=self.event();self.o.schedule.cancel(self.owner,ref,1,True);self.assertEqual(self.o.schedule.row(self.owner,ref)['status'],'cancelled');self.event();self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM sc_audit WHERE ref=?',(ref,)).fetchone()[0],2)
    def test_today_tomorrow_week_next_queries(self):
        self.event();s=self.o.schedule
        for text in ('今日の予定を教えて','明日の予定は？','来週の予定を教えて','次の予定は何時？'):
            r=s.interpret(self.owner,text,self.at('2026-11-04T09:00:00'));self.assertEqual(r['intent'],'query')
        self.assertEqual(len(self.query()['events']),1)
    def test_friday_availability_has_no_overlap(self):
        s=self.o.schedule;self.event();slots=s.free(self.owner,'2026-11-05');self.assertEqual([(a[11:16],b[11:16]) for a,b in slots],[('09:00','14:00'),('15:00','18:00')]);r=s.interpret(self.owner,'金曜日の空いている時間は？',self.at('2026-11-04T09:00:00'));self.assertEqual(r['intent'],'free')
    def test_voice_change_request_requires_selecting_exact_event(self):
        self.event();r=self.o.schedule.interpret(self.owner,'石川さんの予定をキャンセルして');self.assertEqual(r['intent'],'select_event');self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM sc_events WHERE status='active'").fetchone()[0],1)
    def test_other_representative_cannot_read_or_modify_event(self):
        ref=self.event();self.o.add_user('other','representative','SyntheticOtherPass!123');p=Principal('representative','other')
        with self.assertRaises(ValueError):self.o.schedule.row(p,ref)
        with self.assertRaises(ValueError):self.o.schedule.cancel(p,ref,1,True)
        self.assertEqual(self.o.schedule.listing(p,self.at()-timedelta(days=1),self.at()+timedelta(days=1)),[])
    def test_nonowner_denied_get_and_post(self):
        self.operator();h,_=self.headers('staff','business_operator');app=OfficeApp(self.path,8765);self.assertNotEqual(app.handle('GET','/schedule',h)[0],200);self.assertEqual(self.post(self.save_form(), 'staff','business_operator')[0],403)
    def test_revoked_authenticated_role_denied(self):
        h,_=self.headers();self.store.db.execute("UPDATE office_users SET role='viewer' WHERE username='owner'");self.assertNotEqual(OfficeApp(self.path,8765).handle('GET','/schedule',h)[0],200)
    def test_csrf_origin_and_xss(self):
        self.assertEqual(self.post(self.save_form(),csrf=False)[0],403);r=self.post(self.save_form(title='<img src=x onerror=alert(1)>'));self.assertEqual(r[0],200);self.assertNotIn('<img src=x',r[2].decode())
    def test_voice_csp_only_for_schedule(self):
        h,_=self.headers();app=OfficeApp(self.path,8765);calendar=app.handle('GET','/schedule',h);normal=app.handle('GET','/',h);self.assertIn("script-src 'self'",calendar[1]['Content-Security-Policy']);self.assertIn('microphone=(self)',calendar[1]['Permissions-Policy']);self.assertIn("script-src 'none'",normal[1]['Content-Security-Policy']);self.assertIn('microphone=()',normal[1]['Permissions-Policy'])
    def test_unauthenticated_redirect(self):self.assertEqual(OfficeApp(self.path,8765).handle('GET','/schedule',{'Host':'127.0.0.1:8765'})[0],303)
    def test_daily_jst_21_not_before_and_only_one_digest(self):
        self.enabled();self.event();self.event(start=self.at()+timedelta(hours=2),end=self.at()+timedelta(hours=3));s=self.o.schedule;s.tick(self.at('2026-11-04T20:59:00'));self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM sc_notifications WHERE state='mock_sent'").fetchone()[0],0);s.tick(self.at('2026-11-04T21:00:00'));r=self.store.db.execute("SELECT * FROM sc_notifications WHERE kind='daily'").fetchone();self.assertEqual(r['state'],'mock_sent');self.assertTrue(r['due_at'].startswith('2026-11-04T12:00:00'));self.assertIn('14:00',s._payload(self.owner,r));self.assertIn('16:00',s._payload(self.owner,r))
    def test_hourly_exactly_60_minutes_not_30(self):
        self.enabled();self.event();s=self.o.schedule;s.tick(self.at('2026-11-05T12:59:00'));self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM sc_notifications WHERE kind='hour' AND state='mock_sent'").fetchone()[0],0);s.tick(self.at('2026-11-05T13:00:00'));r=self.store.db.execute("SELECT * FROM sc_notifications WHERE kind='hour'").fetchone();self.assertEqual(r['state'],'mock_sent');self.assertEqual(datetime.fromisoformat(r['due_at']).astimezone(JST).hour,13);s.tick(self.at('2026-11-05T13:30:00'));self.assertEqual(self.store.db.execute("SELECT attempts FROM sc_notifications WHERE kind='hour'").fetchone()[0],1)
    def test_no_schedule_no_notifications(self):
        self.enabled();self.o.schedule.tick(self.at('2026-11-04T21:00:00'));self.assertEqual(self.o.schedule.notifications(self.owner),[])
    def test_default_stopped_and_consent_required(self):
        self.event();self.o.schedule.tick(self.at('2026-11-04T21:00:00'));self.assertEqual(self.o.schedule.notifications(self.owner),[])
        with self.assertRaises(ValueError):self.o.schedule.settings(self.owner,True,False,True)
    def test_consent_withdrawal_holds_pending(self):
        self.enabled();self.event();s=self.o.schedule;s.tick(self.at('2026-11-04T20:00:00'));s.settings(self.owner,False,False,True);s.tick(self.at('2026-11-04T21:00:00'));self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM sc_notifications WHERE state='mock_sent'").fetchone()[0],0)
    def test_cancel_before_due_prevents_reminder(self):
        self.enabled();ref=self.event();s=self.o.schedule;s.tick(self.at('2026-11-05T12:00:00'));s.cancel(self.owner,ref,1,True);s.tick(self.at('2026-11-05T13:00:00'));self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM sc_notifications WHERE state='mock_sent'").fetchone()[0],0)
    def test_edit_invalidates_previous_notification(self):
        self.enabled();ref=self.event();s=self.o.schedule;s.tick(self.at('2026-11-05T12:00:00'));s.save(self.owner,'edit','合成の移動',self.at()+timedelta(hours=2),self.at()+timedelta(hours=3),identifier=ref,revision=1,checked=True);s.tick(self.at('2026-11-05T13:00:00'));self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM sc_notifications WHERE state='mock_sent'").fetchone()[0],0);s.tick(self.at('2026-11-05T15:00:00'));self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM sc_notifications WHERE kind='hour' AND state='mock_sent'").fetchone()[0],1)
    def test_repeated_tick_deduplication(self):
        self.enabled();self.event();s=self.o.schedule
        for i in range(4):s.tick(self.at('2026-11-05T13:00:00'))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM sc_notifications WHERE kind='hour'").fetchone()[0],1);self.assertEqual(self.store.db.execute("SELECT attempts FROM sc_notifications WHERE kind='hour'").fetchone()[0],1)
    def test_parallel_ticks_never_double_process(self):
        self.enabled();self.event()
        def tick(_):
            store=Store(self.path)
            try:return Office(store).schedule.tick(self.at('2026-11-05T13:00:00'))
            finally:store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(tick,range(2)))
        self.assertEqual(self.store.db.execute("SELECT attempts FROM sc_notifications WHERE kind='hour'").fetchone()[0],1)
    def test_mock_errors_retry_bounded_three_attempts(self):
        self.enabled();self.event();s=self.o.schedule;a=self.at('2026-11-05T13:00:00')
        for i in range(5):s.tick(a+timedelta(seconds=i*30),fail_mock=True)
        r=self.store.db.execute("SELECT * FROM sc_notifications WHERE kind='hour'").fetchone();self.assertEqual(r['state'],'failed');self.assertEqual(r['attempts'],3);self.assertEqual(r['error_code'],'mock_failure')
    def test_outage_late_notification_safely_missed(self):
        self.enabled();self.event();self.o.schedule.tick(self.at('2026-11-05T13:06:00'));self.assertEqual(self.store.db.execute("SELECT state FROM sc_notifications WHERE kind='hour'").fetchone()[0],'missed')
    def test_assignment_is_existing_ai23_and_does_not_grant_business_rights(self):
        a=self.o.schedule.assignment(self.owner,'BIZ-B');self.assertEqual(a['agent'],'AI23');self.assertFalse(a['business_work_allowed']);self.event(business='BIZ-B');self.assertEqual(self.store.db.execute('SELECT state FROM sc_work').fetchone()[0],'admin_review');self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM bf_ai_assignments').fetchone()[0],0)
    def test_migration_keeps_existing_rows_and_repeats(self):
        customer=self.customer();before=[tuple(r) for r in self.store.db.execute('SELECT * FROM portfolio_contacts')];migrate(self.store);migrate(self.store);self.assertEqual(before,[tuple(r) for r in self.store.db.execute('SELECT * FROM portfolio_contacts')]);self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(),[])
    def test_restore_keeps_calendar_but_stops_notifications(self):
        self.enabled();self.event();self.o.schedule.tick(self.at('2026-11-04T20:00:00'));a=Path(self.temp.name)/'copy.db';z=Path(self.temp.name)/'restore.db';backup(self.path,a);restore(a,z);db=sqlite3.connect(z)
        try:self.assertEqual(db.execute('SELECT COUNT(*) FROM sc_events').fetchone()[0],1);self.assertEqual(db.execute('SELECT enabled,consent FROM sc_settings').fetchone(),(0,0));self.assertTrue(all(r[0]=='recovery_hold' for r in db.execute('SELECT state FROM sc_notifications')))
        finally:db.close()
    def test_ics_has_60_min_and_previous_21_jst_alarm(self):
        self.event();data=export(self.o.schedule,self.owner,True,self.at()-timedelta(days=1),self.at()+timedelta(days=1)).decode();self.assertIn('TRIGGER:-PT60M',data);self.assertIn('DTSTART:20261104T120000Z',data);self.assertIn('TRIGGER:PT0S',data);self.assertIn('CLASS:PRIVATE',data);self.assertTrue(all(len(line.encode())<=75 for line in data.split('\r\n')))
    def test_ics_requires_owner_confirmation(self):
        with self.assertRaises(ValueError):export(self.o.schedule,self.owner,False,self.at(),self.at()+timedelta(days=1))
        self.assertEqual(self.post({'action':'calendar','checked':'1'})[0],200)
    def test_background_worker_stoppable_and_no_external_calls(self):
        stop,thread=start_worker(self.path,0.05)
        try:
            end=time.monotonic()+2
            while not self.store.db.execute('SELECT COUNT(*) FROM sc_ticks').fetchone()[0] and time.monotonic()<end:time.sleep(0.02)
            self.assertGreater(self.store.db.execute('SELECT COUNT(*) FROM sc_ticks').fetchone()[0],0)
        finally:stop.set();thread.join(timeout=3)
        self.assertFalse(thread.is_alive())

    def test_edit_after_hour_notification_does_not_send_twice(self):
        self.enabled();ref=self.event();s=self.o.schedule;s.tick(self.at('2026-11-05T13:00:00'));s.save(self.owner,'updated','合成の場所変更',self.at(),self.at()+timedelta(hours=1),identifier=ref,revision=1,checked=True);s.tick(self.at('2026-11-05T13:01:00'));self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM sc_notifications WHERE kind='hour' AND state='mock_sent'").fetchone()[0],1)
    def test_daily_digest_remains_once_after_changes(self):
        self.enabled();ref=self.event();s=self.o.schedule;s.tick(self.at('2026-11-04T21:00:00'));s.save(self.owner,'updated','合成の内容修正',self.at(),self.at()+timedelta(hours=1),identifier=ref,revision=1,checked=True);s.tick(self.at('2026-11-04T21:01:00'));self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM sc_notifications WHERE kind='daily' AND state='mock_sent'").fetchone()[0],1)
    def test_reconsent_resumes_only_pending_reminders(self):
        self.enabled();self.event();s=self.o.schedule;s.tick(self.at('2026-11-05T12:00:00'));s.settings(self.owner,False,False,True);s.settings(self.owner,True,True,True);s.tick(self.at('2026-11-05T13:00:00'));self.assertEqual(self.store.db.execute("SELECT state FROM sc_notifications WHERE kind='hour'").fetchone()[0],'mock_sent')

    def test_afternoon_time_is_not_silently_interpreted_as_night(self):
        r=self.o.schedule.interpret(self.owner,'11月5日の午後2時から打ち合わせを登録して',self.at('2026-10-28T09:00:00'));self.assertIn('T14:00',r['candidate']['start'])

    def test_background_worker_runs_both_mock_reminder_times(self):
        self.enabled();self.event()
        for value,kind in [('2026-11-04T21:00:00','daily'),('2026-11-05T13:00:00','hour')]:
            with patch('violet_office.schedule.now',return_value=self.at(value)):
                stop,thread=start_worker(self.path,0.05)
                try:
                    deadline=time.monotonic()+3
                    while not self.store.db.execute("SELECT 1 FROM sc_notifications WHERE kind=? AND state='mock_sent'",(kind,)).fetchone() and time.monotonic()<deadline:time.sleep(0.02)
                    self.assertIsNotNone(self.store.db.execute("SELECT 1 FROM sc_notifications WHERE kind=? AND state='mock_sent'",(kind,)).fetchone())
                finally:stop.set();thread.join(timeout=3)

    def test_daily_digest_includes_ongoing_overnight_event(self):
        self.enabled();self.event(start=self.at('2026-11-04T20:00:00'),end=self.at('2026-11-05T10:00:00'));self.o.schedule.tick(self.at('2026-11-04T21:00:00'));r=self.store.db.execute("SELECT * FROM sc_notifications WHERE kind='daily' AND target_day='2026-11-05'").fetchone();self.assertEqual(r['state'],'mock_sent');self.assertIn('継続中',self.o.schedule._payload(self.owner,r))
