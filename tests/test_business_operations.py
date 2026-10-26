"""Period/accounting/authentication integration checks, disposable data only."""
import json,sqlite3,unittest
from pathlib import Path
from datetime import datetime,timedelta,time
from unittest.mock import patch
from tests import test_sales as sales_helpers
from tests import test_business_foundation as foundation_helpers
from violet_office.store import Store,now
from violet_office.office import Office
from violet_office.seminars import JST
from violet_office.integrations.adapters import Principal
from violet_office.business_operations import migrate
from violet_office.web import OfficeApp
from violet_office.ops import backup,restore


class BusinessOperationsTests(unittest.TestCase):
    setUp=sales_helpers.SalesTests.setUp
    tearDown=sales_helpers.SalesTests.tearDown
    customer=sales_helpers.SalesTests.customer
    deal=sales_helpers.SalesTests.deal
    quote=sales_helpers.SalesTests.quote
    stage=sales_helpers.SalesTests.stage
    action=sales_helpers.SalesTests.action
    ready=sales_helpers.SalesTests.ready
    invoiced=sales_helpers.SalesTests.invoiced
    headers=sales_helpers.SalesTests.headers
    post=sales_helpers.SalesTests.post
    operator=sales_helpers.SalesTests.operator
    def dates(self,days=1): return self.day.isoformat(),(self.day+timedelta(days=days)).isoformat()
    def policy(self,b=None,source='scm'):
        b=b or self.b; old=self.store.db.execute('SELECT revision FROM ops_finance_policy WHERE business_id=?',(b,)).fetchone()
        self.o.business_operations.finance_policy(self.owner,b,source,'合成の集計基準確認',old[0] if old else 0)
    def data(self,b=None,start=None,end=None):
        a,z=self.dates(); return self.o.business_operations.summary(self.owner,b or self.b,start or a,end or z)
    def custom(self,key='new_count',unit='件'):
        f=self.o.business_foundation; f.adopt(self.owner,self.b,'合成の既存事業'); f.kpi(self.owner,self.b,key,'合成の追加指標',unit); return f
    def test_policy_unconfirmed_does_not_add_two_revenue_ledgers(self):
        d,q,i=self.invoiced(); self.o.portfolio.ledger(self.owner,self.b,'dup',self.day.isoformat(),'revenue',6600,'actual','合成')
        value=self.data(); self.assertIsNone(value['revenue_yen']); self.assertIsNone(value['profit_yen']); self.assertEqual(value['legacy_revenue_yen'],6600); self.assertEqual(value['scm_revenue_yen'],6000)
        self.policy(); self.assertEqual(self.data()['revenue_yen'],6000)
        self.policy(source='legacy'); self.assertEqual(self.data()['revenue_yen'],6600)
    def test_profit_and_negative_profit_follow_selected_source_and_expense(self):
        self.policy(source='legacy'); pf=self.o.portfolio; day=self.day.isoformat(); pf.ledger(self.owner,self.b,'sales',day,'revenue',100,'actual','合成'); pf.ledger(self.owner,self.b,'cost',day,'expense',130,'actual','合成')
        data=self.data(); self.assertEqual(data['revenue_yen'],100); self.assertEqual(data['expense_yen'],130); self.assertEqual(data['profit_yen'],-30)
    def test_scm_partial_payment_unpaid_and_cash_not_revenue(self):
        d,q,i=self.invoiced(); self.policy(); self.action(d,'payment',q,body={'invoice_id':i['id'],'amount_yen':1000})
        data=self.data(); self.assertEqual(data['revenue_yen'],6000); self.assertEqual(data['cash_yen'],1000); self.assertEqual(data['outstanding_yen'],5600)
        self.action(d,'payment',q,body={'invoice_id':i['id'],'amount_yen':5600}); self.assertEqual(self.data()['outstanding_yen'],0); self.assertEqual(self.data()['revenue_yen'],6000)
    def test_full_refund_and_unpaid_cancellation_do_not_leave_sales_or_unpaid(self):
        d,q,i=self.invoiced(); self.policy(); self.action(d,'payment',q,body={'invoice_id':i['id'],'amount_yen':6600}); self.action(d,'refund',q,body={'invoice_id':i['id'],'amount_yen':6600})
        data=self.data(); self.assertEqual(data['revenue_yen'],0); self.assertEqual(data['cash_yen'],0); self.assertEqual(data['outstanding_yen'],0)
        d2,q2,i2=self.invoiced(); self.action(d2,'void_invoice',q2,body={'invoice_id':i2['id']}); self.assertEqual(self.data()['revenue_yen'],0); self.assertEqual(self.data()['outstanding_yen'],0)
    def test_outstanding_as_of_end_ignores_later_payment_and_credit(self):
        d,q,i=self.invoiced(); self.policy(); self.action(d,'payment',q,body={'invoice_id':i['id'],'amount_yen':6600}); self.action(d,'refund',q,body={'invoice_id':i['id'],'amount_yen':6600})
        future=datetime.combine(self.day+timedelta(days=2),time(12),JST).isoformat()
        self.store.db.execute('UPDATE scm_cash SET created_at=?',(future,)); self.store.db.execute('UPDATE scm_credits SET created_at=?',(future,))
        data=self.data(); self.assertEqual(data['outstanding_yen'],6600); self.assertEqual(data['revenue_yen'],6000); self.assertIsNone(data['cash_yen'])
    def test_period_boundaries_jst_exclusive_end_and_prior_invoices(self):
        self.policy(source='legacy'); pf=self.o.portfolio; day=self.day.isoformat(); prior=(self.day-timedelta(days=1)).isoformat(); pf.ledger(self.owner,self.b,'now',day,'revenue',10,'actual','合成'); pf.ledger(self.owner,self.b,'prior',prior,'revenue',20,'actual','合成')
        self.assertEqual(self.data()['revenue_yen'],10); self.assertEqual(self.data(start=prior,end=day)['revenue_yen'],20)
        customer=self.customer(); midnight=datetime.combine(self.day+timedelta(days=1),time.min,JST).isoformat(); self.store.db.execute('UPDATE portfolio_contacts SET created_at=? WHERE id=?',(midnight,customer))
        self.assertEqual(self.data()['counts']['new_customers'],0)
    def test_mock_and_estimate_never_become_actual_results(self):
        d,q,i=self.invoiced('mock'); self.action(d,'payment',q,body={'invoice_id':i['id'],'amount_yen':6600},mode='mock'); self.policy(); self.o.portfolio.ledger(self.owner,self.b,'forecast',self.day.isoformat(),'revenue',999999,'estimated','合成')
        data=self.data(); self.assertIsNone(data['revenue_yen']); self.assertEqual(data['outstanding_yen'],0); self.assertEqual(data['counts']['contracts_recorded'],0)
    def test_count_uses_customer_ids_not_duplicate_crm_or_person_names(self):
        one=self.o.sales.customer(self.owner,self.b,'合成の同じ表示名','synthetic'); self.o.sales.customer(self.owner,'BIZ-A','合成の同じ表示名','synthetic')
        self.assertEqual(self.data()['counts']['customers'],1); self.assertEqual(self.data()['counts']['new_customers'],1)
        self.o.portfolio.request_delete(self.owner,self.b,one); self.o.portfolio.approve_delete(self.owner,self.b,one,True)
        self.assertEqual(self.data()['counts']['customers'],0); self.assertEqual(self.data()['counts']['new_customers'],1)
    def test_customer_deletion_after_period_does_not_remove_historical_count(self):
        one=self.customer(); self.o.portfolio.request_delete(self.owner,self.b,one); self.o.portfolio.approve_delete(self.owner,self.b,one,True)
        future=(now()+timedelta(days=3)).isoformat(); self.store.db.execute("UPDATE portfolio_events SET created_at=? WHERE event='contact_redacted'",(future,))
        self.assertEqual(self.data()['counts']['customers'],1)
    def test_contract_event_count_and_cancellation_are_not_double_counted(self):
        d,q,booking=self.ready(); self.action(d,'contract',q); self.action(d,'booking_confirm',q,booking)
        data=self.data(); self.assertEqual(data['counts']['contracts_recorded'],1); self.assertEqual(data['counts']['contracts_active'],1)
        self.action(d,'cancel_booking',q); self.action(d,'cancel_contract',q)
        data=self.data(); self.assertEqual(data['counts']['contracts_recorded'],1); self.assertEqual(data['counts']['contracts_cancelled'],1); self.assertEqual(data['counts']['contracts_active'],0)
    def test_interview_and_service_bookings_are_distinct_and_never_assumed_complete(self):
        d,q,booking=self.ready(); self.store.db.execute("UPDATE scm_bookings SET kind='interview' WHERE id=?",(booking,)); self.action(d,'booking_confirm',q,booking)
        data=self.data(); self.assertEqual(data['counts']['interview_bookings'],1); self.assertEqual(data['counts']['interviews_completed'],0)
        d2,q2,b2=self.ready(); self.action(d2,'booking_confirm',q2,b2); self.assertEqual(self.data()['counts']['interview_bookings'],1)
    def test_custom_kpi_requires_rule_and_complete_period(self):
        f=self.custom(); day=self.day.isoformat(); start=(self.day-timedelta(days=1)).isoformat(); end=(self.day+timedelta(days=1)).isoformat(); f.measure(self.owner,self.b,'new_count',day,'5','actual','合成')
        ops=self.o.business_operations; row=ops.kpis(self.owner,self.b,start,end)[-1]; self.assertIsNone(row['value'])
        ops.rule(self.owner,self.b,'new_count','sum','合成の確認'); ops.target(self.owner,self.b,'new_count',start,end,'10','at_least','合成の目標')
        row=ops.kpis(self.owner,self.b,start,end)[-1]; self.assertEqual(row['recorded'],'5'); self.assertIsNone(row['achieved'])
        f.measure(self.owner,self.b,'new_count',start,'6','actual','合成'); row=ops.kpis(self.owner,self.b,start,end)[-1]; self.assertEqual(row['value'],'11'); self.assertTrue(row['achieved']); self.assertEqual(row['attainment_percent'],110)
    def test_custom_kpi_latest_correction_and_mock_estimate_are_separate(self):
        f=self.custom(); start,end=self.dates(); f.measure(self.owner,self.b,'new_count',start,'1','actual','合成'); f.measure(self.owner,self.b,'new_count',start,'3','actual','合成訂正',1); f.measure(self.owner,self.b,'new_count',start,'999','mock','合成'); f.measure(self.owner,self.b,'new_count',start,'888','estimate','合成')
        self.o.business_operations.rule(self.owner,self.b,'new_count','sum','合成'); row=self.o.business_operations.kpis(self.owner,self.b,start,end)[-1]; self.assertEqual(row['value'],'3')
    def test_last_observation_rule_and_at_most_target_and_zero(self):
        f=self.custom(); start,end=self.dates(); f.measure(self.owner,self.b,'new_count',start,'0','actual','合成'); ops=self.o.business_operations; ops.rule(self.owner,self.b,'new_count','last','残高の合成'); ops.target(self.owner,self.b,'new_count',start,end,'0','at_most','合成')
        row=ops.kpis(self.owner,self.b,start,end)[-1]; self.assertTrue(row['achieved']); self.assertIsNone(row['attainment_percent'])
        old_start=(self.day-timedelta(days=2)).isoformat(); row=ops.kpis(self.owner,self.b,old_start,start)[-1]; self.assertFalse(row['complete'])
    def test_rule_change_invalidates_prior_goal_instead_of_claiming_achievement(self):
        f=self.custom(); a,z=self.dates(); f.measure(self.owner,self.b,'new_count',a,'10','actual','合成'); ops=self.o.business_operations; ops.rule(self.owner,self.b,'new_count','sum','合成'); ops.target(self.owner,self.b,'new_count',a,z,'1','at_least','合成'); ops.rule(self.owner,self.b,'new_count','last','合成訂正',1)
        row=ops.kpis(self.owner,self.b,a,z)[-1]; self.assertEqual(row['target_state'],'needs_review'); self.assertIsNone(row['achieved'])
    def test_standard_month_goal_keeps_missing_days_unmeasured(self):
        start=self.day.replace(day=1).isoformat(); end=(self.day.replace(day=28)+timedelta(days=4)).replace(day=1).isoformat(); self.o.portfolio.goal(self.owner,self.b,start[:7],'inquiries',10); self.o.portfolio.measure(self.owner,self.b,self.day.isoformat(),'inquiries',5,'actual','合成')
        row=self.o.business_operations.kpis(self.owner,self.b,start,end)[0]; self.assertEqual(row['target'],10); self.assertEqual(row['recorded'],5); self.assertIsNone(row['achieved'])
    def test_policy_targets_cross_business_invalid_values_and_revisions(self):
        f=self.custom(); ops=self.o.business_operations; start,end=self.dates()
        with self.assertRaises(ValueError): ops.rule(self.owner,'BIZ-A','new_count','sum','合成')
        with self.assertRaises(ValueError): ops.target(self.owner,'BIZ-A','new_count',start,end,'1','at_least','合成')
        with self.assertRaises(ValueError): ops.finance_policy(self.owner,self.b,'invented','合成')
        self.policy()
        with self.assertRaises(ValueError): ops.finance_policy(self.owner,self.b,'legacy','合成',0)
    def test_global_totals_same_basis_and_partial_or_mixed_data(self):
        ops=self.o.business_operations; pf=self.o.portfolio; start,end=self.dates()
        for b in (r[0] for r in self.store.db.execute('SELECT id FROM portfolio_businesses')):
            self.policy(b,'legacy'); pf.ledger(self.owner,b,'sales',start,'revenue',100,'actual','合成'); pf.ledger(self.owner,b,'expense',start,'expense',20,'actual','合成')
        data=ops.global_summary(self.owner,start,end); self.assertEqual(data['revenue_yen'],600); self.assertEqual(data['profit_yen'],480)
        self.policy('BIZ-A','scm'); data=ops.global_summary(self.owner,start,end); self.assertIsNone(data['revenue_yen']); self.assertIsNone(data['profit_yen']); self.assertEqual(data['by_basis']['legacy']['revenue_yen'],500)
    def test_global_has_no_customer_pii_and_nonrepresentative_is_denied(self):
        self.o.sales.customer(self.owner,self.b,'PRIVATE-SYNTHETIC-NAME','synthetic-private-ref'); ops=self.o.business_operations; data=ops.global_summary(self.owner,*self.dates()); encoded=json.dumps(data)
        self.assertNotIn('PRIVATE-SYNTHETIC-NAME',encoded); self.assertNotIn('synthetic-private-ref',encoded)
        staff=self.operator()
        with self.assertRaises(PermissionError): ops.global_summary(staff,*self.dates())
        with self.assertRaises(PermissionError): ops.summary(staff,'BIZ-A',*self.dates())
        self.assertGreaterEqual(self.store.db.execute("SELECT COUNT(*) FROM ops_audit WHERE outcome='denied'").fetchone()[0],2)
    def test_authenticated_task_context_uses_server_employee_and_assignment(self):
        f=self.o.business_foundation; f.adopt(self.owner,self.b,'合成'); f.assign(self.owner,self.b,'AI27','既存の数値分析','当該事業',['business_read','work','finance_read']); task=self.o.portfolio.task(self.owner,self.b,'ops','analysis','合成',agent='AI27'); ops=self.o.business_operations
        data=ops.for_task(self.owner,task,*self.dates()); self.assertEqual(data['agent'],'AI27'); self.assertEqual(data['summary']['business_id'],self.b)
        f.assign(self.owner,self.b,'AI27','合成','合成',['business_read','work'],True,1)
        with self.assertRaises(PermissionError): ops.for_task(self.owner,task,*self.dates())
        self.assertIsNotNone(self.store.db.execute("SELECT 1 FROM ops_audit WHERE event='ai_scope' AND outcome='denied'").fetchone())
    def test_one_employee_multiple_businesses_and_denied_task_cannot_cross_human_acl(self):
        f=self.o.business_foundation; tasks=[]
        for b in ('BIZ-A','BIZ-B'):
            f.adopt(self.owner,b,'合成'); f.assign(self.owner,b,'AI27','合成','合成',['business_read','work','finance_read']); tasks.append(self.o.portfolio.task(self.owner,b,'ops','analysis','合成',agent='AI27'))
        staff=self.operator(); self.o.business_operations.for_task(staff,tasks[1],*self.dates())
        with self.assertRaises(PermissionError): self.o.business_operations.for_task(staff,tasks[0],*self.dates())
    def test_kpi_gateway_reuses_foundation_permissions_without_new_global_role(self):
        f=self.custom(); f.assign(self.owner,self.b,'AI27','合成','合成',['business_read','work','finance_read']); self.assertTrue(f.read_for_ai(self.owner,self.b,'AI27','kpi_read',*self.dates()))
        with self.assertRaises(PermissionError): f.read_for_ai(self.owner,self.b,'AI17','kpi_read',*self.dates())
        self.assertEqual(len(self.store.agents),28)
    def test_http_scoped_aggregate_api_owner_policy_csrf_and_denial_audit(self):
        staff=self.operator(); h,c=self.headers('staff','business_operator'); start,end=self.dates(); path='/business-operations-data?business=BIZ-B&start='+start+'&end='+end; app=OfficeApp(self.path,8765)
        response=app.handle('GET',path,h); self.assertEqual(response[0],200); self.assertIn('application/json',response[1]['Content-Type'])
        self.assertEqual(app.handle('GET',path.replace('BIZ-B','BIZ-A'),h)[0],403)
        result=self.post(h,c,'ops_finance',{'business':self.b,'start':start,'end':end,'source':'legacy','evidence':'合成','revision':0}); self.assertEqual(result[0],403)
        self.assertIsNotNone(self.store.db.execute("SELECT 1 FROM ops_audit WHERE event='operations_command' AND outcome='denied'").fetchone())
        owner_h,owner_csrf=self.headers(); self.assertEqual(self.post(owner_h,'bad','ops_finance',{'business':self.b,'start':start,'end':end,'source':'legacy','evidence':'合成','revision':0})[0],403)
    def test_existing_screen_period_and_xss_and_no_phase20_assignment_controls(self):
        h,c=self.headers(); start,end=self.dates(); app=OfficeApp(self.path,8765)
        self.store.db.execute("UPDATE portfolio_businesses SET name='<script>alert(1)</script>' WHERE id=?",(self.b,))
        response=app.handle('GET','/business?business='+self.b+'&start='+start+'&end='+end,h); self.assertEqual(response[0],200); self.assertIn(b'&lt;script',response[2]); self.assertNotIn(b'<script>alert',response[2]); self.assertNotIn(b'/actions/bf_assign',response[2])
    def test_migration_keeps_all_prior_data_and_is_repeatable(self):
        self.customer(); self.o.business_foundation.adopt(self.owner,self.b,'合成')
        for table in ('ops_kpi_targets','ops_kpi_rules','ops_finance_policy','ops_audit'): self.store.db.execute('DROP TABLE '+table)
        self.store.db.execute('DELETE FROM office_migrations WHERE version=16'); tables=[r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='office_migrations'")]
        snapshot=lambda:{t:[tuple(r) for r in self.store.db.execute('SELECT * FROM '+t)] for t in tables}
        before=snapshot(); migrate(self.store); migrate(self.store); self.assertEqual(before,snapshot()); self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(),[])
    def test_restore_preserves_reporting_settings_and_revokes_ai_assignment(self):
        f=self.custom(); self.policy(); f.assign(self.owner,self.b,'AI27','合成','合成',['business_read','work','finance_read']); source=Path(self.temp.name)/'backup.db'; target=Path(self.temp.name)/'restored.db'; backup(self.path,source); restore(source,target); db=sqlite3.connect(target)
        try:
            self.assertEqual(db.execute('SELECT source FROM ops_finance_policy').fetchone()[0],'scm'); self.assertEqual(db.execute('SELECT active FROM bf_ai_assignments').fetchone()[0],0)
        finally: db.close()

    def test_explicit_seminar_interview_completion_is_scoped_and_period_bound(self):
        from tests import test_seminar_support as helpers
        service=self.o.seminar_support; sid=service.save(self.owner,self.b,helpers.SeminarSupportTests.data(self),['cast'],mode='manual'); person=helpers.SeminarSupportTests.member(self)
        service.response(person,sid,'yes'); current=now(); self.store.db.execute('UPDATE seminars SET start_at=?,end_at=? WHERE id=?',((current-timedelta(hours=2)).isoformat(),(current-timedelta(hours=1)).isoformat(),sid))
        service.engagement(self.owner,self.b,sid,person.identity,'interview_requested','合成の本人希望'); service.engagement(self.owner,self.b,sid,person.identity,'interview_completed','合成の確認',(current-timedelta(minutes=5)).isoformat())
        self.assertEqual(self.data()['counts']['interviews_completed'],1); self.assertEqual(self.data('BIZ-A')['counts']['interviews_completed'],0)
    def test_task_endpoint_rejects_spoofed_agent_fields_and_scoped_assignment_revocation(self):
        f=self.custom(); f.assign(self.owner,self.b,'AI27','合成','合成',['business_read','work','finance_read']); task=self.o.portfolio.task(self.owner,self.b,'http','analysis','合成',agent='AI27'); h,c=self.headers(); a,z=self.dates(); app=OfficeApp(self.path,8765); route='/business-task-kpis?task='+task+'&start='+a+'&end='+z
        response=app.handle('GET',route,h); self.assertEqual(response[0],200); self.assertEqual(json.loads(response[2])['agent'],'AI27')
        self.assertEqual(app.handle('GET',route+'&agent=AI01',h)[0],400)
        f.assign(self.owner,self.b,'AI27','合成','合成',['business_read'],False,1); self.assertEqual(app.handle('GET',route,h)[0],403)
