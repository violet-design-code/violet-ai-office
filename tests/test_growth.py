import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from urllib.parse import urlencode

from violet_office.store import Store,now
from violet_office.office import Office
from violet_office.growth import Growth,GOALS,window,following,JST
from violet_office.web import OfficeApp,COOKIE
from violet_office.ops import backup,restore


class GrowthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'growth.db'
        self.store=Store(self.path)
        self.office=Office(self.store)
        self.g=self.office.growth
        self.at=datetime(2026,10,2,18,tzinfo=JST)
        self.goals={k:None for k in GOALS}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def report(self,identifier):
        row=self.store.db.execute('SELECT * FROM growth_runs WHERE id=?',(identifier,)).fetchone()
        self.assertEqual(row['status'],'succeeded_local',dict(row))
        return json.loads(row['report'])

    def metric(self,key,day,value,quality='measured',mode='manual',metric='inquiries',replace_id=None):
        return self.g.record_metric(key,metric,day,value,quality,mode,'synthetic_source','合成テストデータ・実測の実査ではない','test',replace_id)

    def schedule(self,cadence='daily',draft=False):
        return self.g.create_schedule('schedule-'+cadence,cadence,self.at,'current',draft,'test')

    def test_goals_unset_remain_nullable_and_zero_is_explicit(self):
        self.assertTrue(all(v is None for v in self.g.goals('2026-10')['values'].values()))
        self.goals['inquiries']=0
        self.goals['ai_budget_microusd']=100
        self.g.set_goals('2026-10',self.goals,'test')
        self.assertEqual(self.g.goals('2026-10')['values']['inquiries'],0)
        self.assertIsNone(self.g.goals('2026-10')['values']['contracts'])
        self.assertEqual(self.store.db.execute("SELECT limit_microusd FROM office_budgets WHERE month='2026-10'").fetchone()[0],100)
        self.goals['ai_budget_microusd']=None
        self.g.set_goals('2026-10',self.goals,'test')
        self.assertEqual(self.store.db.execute("SELECT limit_microusd FROM office_budgets WHERE month='2026-10'").fetchone()[0],100)

    def test_invalid_goals_and_no_implicit_defaults(self):
        for value in (-1,True,1.5):
            self.goals['inquiries']=value
            with self.assertRaises(ValueError):
                self.g.set_goals('2026-10',self.goals,'test')
        with self.assertRaises(ValueError):
            self.g.set_goals('2026-10',{},'test')

    def test_measured_estimated_mock_and_partial_coverage_are_separate(self):
        self.metric('actual-1','2026-10-01',3)
        self.metric('estimate','2026-10-02',9,'estimated')
        self.metric('mock','2026-10-02',999,mode='mock')
        data=self.g.metrics('2026-10-01','2026-10-03')['inquiries']
        self.assertEqual(data['value'],3)
        self.assertEqual(data['estimated'],9)
        self.assertFalse(data['complete'])
        self.assertEqual(data['status'],'部分計測')
        self.assertIsNone(self.g.metrics('2026-10-01','2026-10-03')['contracts']['value'])

    def test_measurement_idempotency_authoritative_daily_total_and_correction(self):
        first=self.metric('one','2026-10-01',3)
        self.assertEqual(first,self.metric('one','2026-10-01',3))
        with self.assertRaises(ValueError):
            self.metric('two','2026-10-01',4)
        corrected=self.metric('correct','2026-10-01',4,replace_id=first)
        self.assertEqual(self.g.metrics('2026-10-01','2026-10-02')['inquiries']['value'],4)
        with self.assertRaises(ValueError):
            self.metric('stale','2026-10-01',5,replace_id=first)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM growth_measurements').fetchone()[0],2)

    def test_future_measured_input_rejected_but_mock_or_estimate_allowed(self):
        future=(now().astimezone(JST).date()+timedelta(days=1)).isoformat()
        with self.assertRaises(ValueError):
            self.metric('future',future,5)
        self.metric('future-est',future,5,'estimated')
        self.metric('future-mock',future,5,mode='mock')

    def test_monthly_achievement_requires_complete_coverage(self):
        self.goals['inquiries']=10
        self.g.set_goals('2026-10',self.goals,'test')
        self.metric('one','2026-10-01',3)
        partial=self.g.snapshot('monthly',self.at,'current')
        self.assertIsNone(partial['monthly_progress']['inquiries']['achievement_percent'])
        self.metric('two','2026-10-02',2)
        complete=self.g.snapshot('monthly',self.at,'current')
        self.assertEqual(complete['monthly_progress']['inquiries']['achievement_percent'],50)
        self.assertEqual(complete['monthly_progress']['inquiries']['gap'],5)

    def test_zero_goals_denominators_and_period_ratios_not_cohort_conversion(self):
        self.goals['inquiries']=0
        self.g.set_goals('2026-10',self.goals,'test')
        self.metric('i','2026-10-01',0)
        snap=self.g.snapshot('daily',datetime(2026,10,1,18,tzinfo=JST),'current')
        self.assertIsNone(snap['monthly_progress']['inquiries']['achievement_percent'])
        self.assertIsNone(snap['conversions']['問い合わせ→面談予約']['percent'])
        self.assertIsNone(snap['conversions']['問い合わせ→面談予約']['period_count_ratio_percent'])

    def test_japan_period_boundaries_iso_weeks_and_previous_month(self):
        result=window('daily',datetime(2026,10,1,15,tzinfo=timezone.utc))
        self.assertEqual(result['start'],'2026-10-02')
        week=window('weekly',datetime(2026,1,1,tzinfo=JST))
        self.assertEqual(week['start'],'2025-12-29')
        previous=window('monthly',datetime(2026,3,1,tzinfo=JST),'previous')
        self.assertEqual(previous['start'],'2026-02-01')
        self.assertEqual(previous['coverage_end_exclusive'],'2026-03-01')

    def test_scheduler_month_end_anchor_does_not_drift(self):
        anchor=datetime(2026,1,31,18,tzinfo=JST)
        feb=following('monthly',anchor,datetime(2026,2,15,tzinfo=JST))
        self.assertEqual(datetime.fromisoformat(feb).astimezone(JST).day,28)
        march=following('monthly',anchor,datetime(2026,2,28,19,tzinfo=JST))
        self.assertEqual(datetime.fromisoformat(march).astimezone(JST).day,31)

    def test_schedule_created_paused_enable_pause_resume(self):
        schedule=self.schedule()
        self.assertEqual(self.g.tick(self.at),[])
        self.g.schedule_state(schedule,True,'test')
        runs=self.g.tick(self.at)
        self.assertEqual(len(runs),1)
        self.assertEqual(self.report(runs[0])['context']['monthly_goals']['values'],{k:None for k in GOALS})
        self.g.schedule_state(schedule,False,'test')
        self.assertEqual(self.g.tick(self.at+timedelta(days=1)),[])
        self.g.schedule_state(schedule,True,'test')
        self.assertEqual(len(self.g.tick(self.at+timedelta(days=1))),1)

    def test_no_backfill_and_only_three_cadences_per_tick(self):
        for cadence in ('daily','weekly','monthly'):
            schedule=self.schedule(cadence)
            self.g.schedule_state(schedule,True,'test')
        runs=self.g.tick(self.at+timedelta(days=40))
        self.assertEqual(len(runs),3)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM growth_runs').fetchone()[0],3)
        self.assertEqual(self.g.tick(self.at+timedelta(days=40)),[])

    def test_daily_generation_needed_roles_handoffs_and_approval_stop(self):
        run=self.g.run('daily','daily',self.at,prepare_draft=True)
        report=self.report(run)
        roles={r[0] for r in self.store.db.execute('SELECT agent FROM growth_work')}
        self.assertEqual(roles,{'AI01','AI27','AI08','AI09','AI28'})
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM growth_work').fetchone()[0],6)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM growth_edges').fetchone()[0],5)
        self.assertEqual(self.store.db.execute('SELECT decision FROM growth_runs').fetchone()[0],'approval_pending')
        draft=self.office.content._latest(report['draft_content_id'])
        self.assertEqual(draft['status'],'approval_pending')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0],0)
        self.assertTrue(all(r[0]=='review_pending' for r in self.store.db.execute('SELECT t.status FROM growth_work w JOIN tasks t ON t.id=w.task_id')))

    def test_plan_assignment_priority_deadline_and_human_gate(self):
        self.goals['appointments']=5
        self.goals['referrals']=3
        self.g.set_goals('2026-10',self.goals,'test')
        run=self.g.run('assign','monthly',self.at)
        proposals=self.store.db.execute('SELECT p.*,t.agent FROM growth_proposals p JOIN tasks t ON t.id=p.task_id WHERE run_id=?',(run,)).fetchall()
        self.assertIn('AI18',[p['agent'] for p in proposals])
        self.assertIn('AI16',[p['agent'] for p in proposals])
        self.assertTrue(all(1<=p['priority']<=3 and p['due_at'] for p in proposals))
        with self.assertRaises(ValueError):
            self.store.claim(proposals[0]['task_id'])
        self.g.decide(run,'approve','test')
        self.store.claim(proposals[0]['task_id'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0],0)

    def test_weekly_and_monthly_reports_generate_without_fake_success(self):
        for cadence in ('weekly','monthly'):
            result=self.report(self.g.run(cadence,cadence,self.at))
            self.assertEqual(result['context']['period']['cadence'],cadence)
            self.assertTrue(all(v['value'] is None for v in result['context']['kpis'].values()))
            self.assertIsNone(result['context']['cost_microusd']['confirmed'])
            self.assertFalse(result['actions_executed'])
            self.assertIn('未評価',result['context']['attribution'])

    def test_six_japanese_commands_and_unknown_are_honest(self):
        cases=[('今日の仕事を報告して','report'),('来週の集客計画を作って','planning'),('紹介者が紹介しやすい文章を考えて','referral'),
               ('面談予約を増やす改善案を出して','booking_improvement'),('今月の目標達成状況を教えて','goal_review'),('今月のAI利用料金を教えて','cost_review')]
        for index,(text,intent) in enumerate(cases):
            result=self.report(self.g.instruction('jp-'+str(index),text,'test',self.at))
            self.assertEqual(result['context']['intent'],intent)
            if intent=='referral':
                self.assertEqual(self.office.content._latest(result['draft_content_id'])['status'],'approval_pending')
        with self.assertRaises(ValueError):
            self.g.instruction('unknown','おまかせです','test',self.at)

    def test_report_idempotency_and_conflicting_intent(self):
        first=self.g.run('same','daily',self.at,prepare_draft=True)
        count=self.store.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0]
        self.assertEqual(first,self.g.run('same','daily',self.at,prepare_draft=True))
        self.assertEqual(count,self.store.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0])
        with self.assertRaises(ValueError):
            self.g.run('same','daily',self.at,intent='cost_review')

    def test_two_scheduler_workers_do_not_duplicate_artifacts(self):
        schedule=self.schedule(draft=True)
        self.g.schedule_state(schedule,True,'test')
        def tick(_):
            store=Store(self.path)
            try:
                return Office(store).growth.tick(self.at)
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(tick,range(2)))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM growth_runs').fetchone()[0],1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM growth_work').fetchone()[0],6)

    def test_failure_auto_pause_safe_retry_reuses_completed_handoffs(self):
        schedule=self.schedule(draft=True)
        self.g.schedule_state(schedule,True,'test')
        original=self.g._work
        def faulty(*args,**kwargs):
            if args[2]=='quality':
                raise RuntimeError('synthetic private provider response must not be saved')
            return original(*args,**kwargs)
        with patch.object(self.g,'_work',side_effect=faulty):
            run=self.g.tick(self.at)[0]
        self.assertEqual(self.store.db.execute('SELECT state FROM growth_schedules').fetchone()[0],'auto_paused')
        self.assertEqual(self.g.tick(self.at),[])
        completed=self.store.db.execute('SELECT COUNT(*) FROM growth_work').fetchone()[0]
        self.g.schedule_state(schedule,True,'test')
        self.g.tick(self.at)
        self.report(run)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM growth_work').fetchone()[0],6)
        self.assertEqual(self.store.db.execute('SELECT attempts FROM growth_runs').fetchone()[0],2)
        self.assertNotIn('synthetic private provider response','\n'.join(self.store.db.iterdump()))

    def test_attempt_limit_stops_after_three_explicit_retries(self):
        schedule=self.schedule()
        for _ in range(4):
            self.g.schedule_state(schedule,True,'test')
            with patch.object(self.g,'_work',side_effect=RuntimeError('synthetic')):
                self.g.tick(self.at)
        self.assertEqual(self.store.db.execute('SELECT attempts FROM growth_runs').fetchone()[0],3)
        self.assertEqual(self.store.db.execute('SELECT enabled FROM growth_schedules').fetchone()[0],0)

    def test_timeout_and_stale_lease_do_not_continue(self):
        counter=[0]
        def clock():
            counter[0]+=20
            return counter[0]
        self.g._clock=clock
        run=self.g.run('timeout','daily',self.at)
        self.assertEqual(self.store.db.execute('SELECT status FROM growth_runs WHERE id=?',(run,)).fetchone()[0],'failed')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM growth_work').fetchone()[0],0)

    def test_owner_cap_suppresses_optional_draft_and_budget_never_calls_api(self):
        self.goals['owner_minutes']=0
        self.goals['ai_budget_microusd']=0
        self.g.set_goals('2026-10',self.goals,'test')
        report=self.report(self.g.run('capacity','daily',self.at,prepare_draft=True))
        self.assertIsNone(report['draft_content_id'])
        self.assertFalse(report['plan']['paid_api_allowed'])
        self.assertEqual(self.store.db.execute('SELECT SUM(reserved) FROM reservations').fetchone()[0],0)

    def test_time_entries_separate_modes_and_monthly_limit_analysis(self):
        self.g.record_time('real-time','2026-10-01',30,'measured','manual','合成確認','test')
        self.g.record_time('estimate-time','2026-10-01',60,'estimated','manual','合成推定','test')
        self.g.record_time('mock-time','2026-10-01',999,'measured','mock','合成モック','test')
        snap=self.g.snapshot('daily',self.at,'current')
        self.assertEqual(snap['owner_month_minutes'],{'measured':30,'estimated':60})
        with self.assertRaises(ValueError):
            self.g.record_time('too-long','2026-10-01',1440,'measured','manual','合成確認','test')

    def test_restore_pauses_existing_schedules_without_changing_original(self):
        schedule=self.schedule()
        self.g.schedule_state(schedule,True,'test')
        snapshot=Path(self.temp.name)/'snapshot.db'
        target=Path(self.temp.name)/'restored.db'
        backup(self.path,snapshot)
        restore(snapshot,target)
        copy=Store(target)
        try:
            self.assertEqual(copy.db.execute('SELECT enabled FROM growth_schedules').fetchone()[0],0)
            self.assertEqual(copy.db.execute('SELECT state FROM growth_schedules').fetchone()[0],'recovery_hold')
        finally:
            copy.close()
        self.assertEqual(self.store.db.execute('SELECT enabled FROM growth_schedules').fetchone()[0],1)

    def test_web_goal_report_approval_and_viewer_access_controls(self):
        self.office.add_user('growth-owner','representative','SyntheticGrowthPass!123')
        token,csrf=self.office.new_session('growth-owner','representative')
        app=OfficeApp(self.path,8765)
        headers={'Host':'127.0.0.1:8765','Origin':'http://127.0.0.1:8765','Content-Type':'application/x-www-form-urlencoded','Cookie':COOKIE+'='+token}
        def post(action,data):
            return app.handle('POST','/actions/'+action,headers,urlencode({'csrf':csrf,'nonce':uuid4().hex,**data}).encode())
        for route in ('/growth','/reports'):
            self.assertEqual(app.handle('GET',route,headers)[0],200)
        self.assertEqual(post('growth_goals',{'month':'2026-10','inquiries':'10','ai_budget_usd':'0'})[0],303)
        response=post('growth_instruction',{'key':'web-report','instruction':'今日の仕事を報告して'})
        self.assertEqual(response[0],303)
        run=response[1]['Location'].split('=')[1]
        page=app.handle('GET',response[1]['Location'],headers)
        self.assertEqual(page[0],200)
        self.assertIn('未計測',page[2].decode())
        self.assertEqual(post('growth_decide',{'id':run,'decision':'approve'})[0],303)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0],0)
        self.office.add_user('growth-reader','viewer','SyntheticReaderPass!123')
        reader,reader_csrf=self.office.new_session('growth-reader','viewer')
        response=app.handle('POST','/actions/growth_tick',{**headers,'Cookie':COOKIE+'='+reader},urlencode({'csrf':reader_csrf,'nonce':uuid4().hex}).encode())
        self.assertEqual(response[0],403)

    def test_completion_date_uses_review_event_not_creation_date(self):
        task=self.store.create_task('temporal','AI27',{})
        artifact=self.store.finish(self.store.claim(task),'合成成果物')
        self.store.review(artifact,'AI28','pass')
        self.store.db.execute("UPDATE events SET created_at='2026-10-01T01:00:00+00:00' WHERE task_id=?",(task,))
        self.store.db.execute("UPDATE events SET created_at='2026-10-02T01:00:00+00:00' WHERE task_id=? AND event='review:pass'",(task,))
        day1=self.g.snapshot('daily',datetime(2026,10,1,18,tzinfo=JST),'current')
        day2=self.g.snapshot('daily',self.at,'current')
        self.assertNotIn(task,day1['local_tasks']['completed_task_ids'])
        self.assertIn(task,day2['local_tasks']['completed_task_ids'])

    def test_confirmed_estimated_costs_and_mock_costs_do_not_mix(self):
        for key,kind,mode,amount in [('actual','confirmed','manual',100),('estimate','estimated','manual',200),('mock','confirmed','mock',999)]:
            self.store.db.execute('INSERT INTO office_costs VALUES (?,?,?,?,?,?,?,?,?,?)',(uuid4().hex,key,'synthetic-model','AI27',None,amount,kind,mode,'2026-10-01','合成費用記録'))
        result=self.g.snapshot('daily',datetime(2026,10,1,18,tzinfo=JST),'current')
        self.assertEqual(result['cost_microusd'],{'confirmed':100,'estimated':200})

    def test_current_kpi_only_becomes_monthly_actual_when_all_days_recorded(self):
        today=now().astimezone(JST).date()
        for number in range(1,today.day+1):
            day=today.replace(day=number).isoformat()
            self.metric('day-'+str(number),day,1)
        self.assertEqual(self.office.metrics()['business']['今月の問い合わせ'],today.day)
        self.assertEqual(self.office.metrics()['business']['契約件数'],None)

    def test_restored_approved_plan_requires_reapproval(self):
        run=self.g.run('restore-plan','daily',self.at)
        self.g.decide(run,'approve','test')
        snapshot=Path(self.temp.name)/'plan-snapshot.db'
        destination=Path(self.temp.name)/'plan-restored.db'
        backup(self.path,snapshot)
        restore(snapshot,destination)
        copy=Store(destination)
        try:
            self.assertEqual(copy.db.execute('SELECT decision FROM growth_runs WHERE id=?',(run,)).fetchone()[0],'approval_pending')
            task=copy.db.execute('SELECT task_id FROM growth_proposals WHERE run_id=?',(run,)).fetchone()[0]
            with self.assertRaises(ValueError):
                copy.claim(task)
        finally:
            copy.close()

    def test_schedule_same_cadence_is_not_silently_replaced(self):
        self.schedule()
        with self.assertRaises(ValueError):
            self.g.create_schedule('another','daily',self.at+timedelta(hours=1),'current',False,'test')

    def test_quality_actually_rejects_inconsistent_measurement_labels(self):
        context=self.g.snapshot('daily',self.at,'current')
        context['kpis']['inquiries']['mock_excluded']=False
        with self.assertRaises(ValueError):
            self.g.quality(context,self.g.plan(context),None)

    def test_retries_observe_current_owner_limit_before_new_draft(self):
        with patch.object(self.g,'quality',side_effect=RuntimeError('synthetic failure')):
            run=self.g.run('new-cap','daily',self.at,prepare_draft=True)
        self.goals['owner_minutes']=0
        self.g.set_goals('2026-10',self.goals,'test')
        self.g.run('new-cap','daily',self.at,prepare_draft=True)
        self.assertIsNone(self.report(run)['draft_content_id'])
        # Existing drafts are retained for audit, never deleted or sent.
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM content_versions').fetchone()[0],1)

    def test_cached_handoff_tampering_stops_retry(self):
        with patch.object(self.g,'quality',side_effect=RuntimeError('synthetic')):
            run=self.g.run('cached','daily',self.at)
        artifact=self.store.db.execute("SELECT artifact_id FROM growth_work WHERE run_id=? AND step='analysis'",(run,)).fetchone()[0]
        self.store.db.execute('UPDATE artifacts SET content=? WHERE id=?',('{"modified":true}',artifact))
        self.g.run('cached','daily',self.at)
        self.assertEqual(self.store.db.execute('SELECT status FROM growth_runs WHERE id=?',(run,)).fetchone()[0],'failed')

    def test_report_body_cannot_be_changed_before_approval(self):
        run=self.g.run('tamper-report','daily',self.at)
        self.store.db.execute('UPDATE growth_runs SET report=? WHERE id=?',('{"modified":true}',run))
        with self.assertRaises(ValueError):
            self.g.decide(run,'approve','test')

    def test_snapshot_failure_pauses_schedule_and_preserves_safe_error(self):
        schedule=self.schedule()
        self.g.schedule_state(schedule,True,'test')
        with patch.object(self.g,'snapshot',side_effect=RuntimeError('synthetic secret should not be logged')):
            self.assertEqual(self.g.tick(self.at),[])
        self.assertEqual(self.store.db.execute('SELECT enabled FROM growth_schedules').fetchone()[0],0)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM growth_audit WHERE action='schedule_prepare_failure'").fetchone()[0],1)
        self.assertNotIn('synthetic secret should not be logged','\n'.join(self.store.db.iterdump()))

    def test_pause_during_run_stops_later_handoffs(self):
        schedule=self.schedule()
        self.g.schedule_state(schedule,True,'test')
        original=self.g._work
        def pausing(*args,**kwargs):
            work=original(*args,**kwargs)
            if args[2]=='analysis':
                self.g.schedule_state(schedule,False,'test')
            return work
        with patch.object(self.g,'_work',side_effect=pausing):
            run=self.g.tick(self.at)[0]
        row=self.store.db.execute('SELECT status,error_code FROM growth_runs WHERE id=?',(run,)).fetchone()
        self.assertEqual(tuple(row),('paused','operator_paused'))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM growth_work').fetchone()[0],1)

    def test_local_automation_does_not_modify_existing_mock_line_or_booking_data(self):
        from violet_office.integrations.demo import run_demo
        run_demo(self.store)
        names=('ix_customers','ix_consents','ix_bookings','ix_steps','ix_operations','ix_attempts')
        before={name:[tuple(r) for r in self.store.db.execute('SELECT * FROM '+name)] for name in names}
        self.g.run('existing-protection','daily',self.at,prepare_draft=True)
        after={name:[tuple(r) for r in self.store.db.execute('SELECT * FROM '+name)] for name in names}
        self.assertEqual(before,after)

    def test_crashed_run_lease_expiry_pauses_without_automatic_retry(self):
        schedule=self.schedule()
        self.g.schedule_state(schedule,True,'test')
        with patch.object(self.g,'quality',side_effect=RuntimeError('synthetic')):
            run=self.g.tick(self.at)[0]
        self.store.db.execute("UPDATE growth_runs SET status='running',lease='stale',deadline='2000-01-01T00:00:00+00:00' WHERE id=?",(run,))
        self.store.db.execute("UPDATE growth_attempts SET outcome='running',finished_at=NULL WHERE run_id=?",(run,))
        self.g.schedule_state(schedule,True,'test')
        self.g.tick(self.at)
        row=self.store.db.execute('SELECT status,attempts,error_code FROM growth_runs WHERE id=?',(run,)).fetchone()
        self.assertEqual(tuple(row),('paused',1,'lease_expired'))
        self.assertEqual(self.store.db.execute('SELECT enabled FROM growth_schedules').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT outcome FROM growth_attempts WHERE run_id=?',(run,)).fetchone()[0],'interrupted')

    def test_cli_run_pause_resume_tick_and_watch_interrupt(self):
        import subprocess,sys,io
        from violet_office.growth_cli import main
        schedule=self.g.create_schedule('cli-schedule','daily',now()-timedelta(minutes=1),'current',False,'test')
        def cli(*args):
            return json.loads(subprocess.run([sys.executable,'-m','violet_office.growth_cli',*args,'--db',str(self.path)],capture_output=True,text=True,check=True).stdout)
        self.assertIn('run_id',cli('run','--key','cli-run'))
        cli('resume','--schedule-id',schedule)
        self.assertEqual(len(cli('tick')['run_ids']),1)
        self.assertEqual(cli('tick')['run_ids'],[])
        cli('pause','--schedule-id',schedule)
        with patch.object(sys,'argv',['growth_cli','watch','--db',str(self.path),'--interval','5']),patch('violet_office.growth_cli.time.sleep',side_effect=KeyboardInterrupt),patch('sys.stdout',new_callable=io.StringIO) as output:
            main()
            self.assertIn('停止しました',output.getvalue())

    def test_schedule_edit_requires_pause_and_uses_new_config_revision(self):
        schedule=self.schedule()
        self.g.schedule_state(schedule,True,'test')
        first=self.g.tick(self.at)[0]
        with self.assertRaises(ValueError):
            self.g.edit_schedule(schedule,self.at+timedelta(minutes=1),'current',False,'test')
        self.g.schedule_state(schedule,False,'test')
        self.g.edit_schedule(schedule,self.at+timedelta(minutes=1),'current',False,'test')
        self.g.schedule_state(schedule,True,'test')
        second=self.g.tick(self.at+timedelta(minutes=2))[0]
        self.assertNotEqual(first,second)
        self.assertEqual(self.report(second)['context']['schedule_revision'],2)
        self.assertEqual(self.g.tick(self.at+timedelta(minutes=2)),[])

    def test_old_run_cannot_continue_after_schedule_reconfiguration(self):
        schedule=self.schedule()
        self.g.schedule_state(schedule,True,'test')
        original=self.g._work
        def editing(*args,**kwargs):
            item=original(*args,**kwargs)
            if args[2]=='analysis':
                self.g.schedule_state(schedule,False,'test')
                self.g.edit_schedule(schedule,self.at+timedelta(minutes=1),'current',False,'test')
                self.g.schedule_state(schedule,True,'test')
            return item
        with patch.object(self.g,'_work',side_effect=editing):
            first=self.g.tick(self.at)[0]
        self.assertEqual(self.store.db.execute('SELECT status FROM growth_runs WHERE id=?',(first,)).fetchone()[0],'paused')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM growth_work WHERE run_id=?',(first,)).fetchone()[0],1)
        second=self.g.tick(self.at+timedelta(minutes=2))[0]
        self.report(second)
        self.assertNotEqual(first,second)
