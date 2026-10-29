"""Actual connected service and HTTP tests; disposable synthetic databases only."""
import http.client
import contextlib
import io
import json
import sqlite3
import threading
import unittest
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlencode
from tests import test_accounting_access as fixtures
from tests import test_sales as sales_fixtures
from violet_office.accounting_access import AccountingAccess, FIELDS
from violet_office.integrations.adapters import Principal
from violet_office.legacy_accounting import VERSION
from violet_office.store import now
from violet_office.web import OfficeApp, make_server


EXPECTED_SECTIONS={'AI01':'management','AI02':'summary','AI06':'improvement',
                   'AI07':'advertising','AI14':'sales','AI15':'terms',
                   'AI25':'system','AI27':'summary'}


class ConnectedAccountingTests(unittest.TestCase):
    setUp=fixtures.AccountingTests.setUp
    tearDown=fixtures.AccountingTests.tearDown
    snapshot=fixtures.AccountingTests.snapshot
    def policy(self,agent='AI27',section='detail',fields=None,b='BIZ-A',approve=True):
        policy=fixtures.AccountingTests.policy(self,agent,section,fields,b,approve)
        if approve:self.s.bind_task(self.owner,self.tasks[b,agent],policy)
        return policy
    headers=sales_fixtures.SalesTests.headers

    def enable_finance(self):
        self.st.db.execute('UPDATE bf_ai_assignments SET permissions=?',
                           (json.dumps(['business_read','work','finance_read']),))

    def invoke(self,agent,route='finance_read',b='BIZ-A',task=None):
        task=task or self.tasks[b,agent]
        if route=='business-task-kpis':
            return self.o.business_operations.for_task(self.owner,task,self.start,self.end)
        return self.o.business_foundation.read_for_ai(self.owner,b,agent,route,self.start,self.end,task=task)

    def url(self,agent='AI02',b='BIZ-A',**changes):
        return '/business-task-kpis?'+urlencode({'task':self.tasks[b,agent],'start':self.start,'end':self.end,**changes})

    def app(self):
        # Store does not retain a path; get it from SQLite rather than a guessed operational path.
        return OfficeApp(self.st.db.execute('PRAGMA database_list').fetchone()[2],8765)

    def test_all_28_roles_three_real_paths_only_approved_fields(self):
        self.enable_finance();before=self.snapshot()
        for agent in self.st.agents:
            section=EXPECTED_SECTIONS.get(agent)
            if section:
                fields=[sorted(FIELDS[section])[0]]
                self.policy(agent,section,fields)
                if section in ('advertising','terms'):
                    self.s.approved_source(self.owner,'BIZ-A',section,'2026-01-05',fields[0],10)
            for route in ('finance_read','kpi_read','business-task-kpis'):
                with self.subTest(agent=agent,route=route):
                    if section:
                        body=self.invoke(agent,route)
                        self.assertEqual(body['version'],VERSION)
                        self.assertEqual(body['agent'],agent)
                        self.assertEqual(body['business_id'],'BIZ-A')
                        self.assertEqual(body['section'],section)
                        data=body['data']
                        if isinstance(data,dict):self.assertEqual(set(data),set(fields))
                        else:self.assertTrue(all(r['field'] in fields for r in data))
                        self.assertNotIn('合成秘密明細本文',json.dumps(body,ensure_ascii=False))
                    else:
                        with self.assertRaises(PermissionError):self.invoke(agent,route)
        self.assertEqual(before,self.snapshot())

    def test_ai04_denied_before_any_financial_read(self):
        self.enable_finance()
        with patch.object(AccountingAccess,'read',side_effect=AssertionError('no data access')), \
             patch.object(self.o.sales,'dashboard',side_effect=AssertionError('no aggregate')), \
             patch.object(self.o.business_operations,'summary',side_effect=AssertionError('no summary')):
            for route in ('finance_read','kpi_read','business-task-kpis'):
                with self.assertRaises(PermissionError):self.invoke('AI04',route)

    def test_missing_policy_never_falls_back(self):
        self.enable_finance()
        for route in ('finance_read','kpi_read','business-task-kpis'):
            with patch.object(self.o.sales,'dashboard',side_effect=AssertionError('fallback')):
                with self.assertRaises(PermissionError):self.invoke('AI02',route)

    def test_missing_schema_fails_closed_and_does_not_initialize(self):
        self.enable_finance()
        for t in ('acct_task_reads','acct_task_policies','acct_handles','acct_events','acct_sources','acct_policies'):
            self.st.db.execute('DROP TABLE '+t)
        for route in ('finance_read','kpi_read','business-task-kpis'):
            with self.assertRaises(PermissionError):self.invoke('AI02',route)
        self.assertIsNone(self.st.db.execute("SELECT 1 FROM sqlite_master WHERE name='acct_policies'").fetchone())

    def test_pending_expired_revoked_and_ambiguous_policies_denied(self):
        self.enable_finance()
        p=self.policy('AI02','summary',approve=False)
        for route in ('finance_read','kpi_read','business-task-kpis'):
            with self.assertRaises(PermissionError):self.invoke('AI02',route)
        self.s.decide(self.owner,p,True)
        self.st.db.execute('UPDATE acct_policies SET expires_at=? WHERE id=?',((now()-timedelta(seconds=1)).isoformat(),p))
        with self.assertRaises(PermissionError):self.invoke('AI02')
        self.policy('AI02','summary');self.policy('AI02','summary')
        with self.assertRaises(PermissionError):self.invoke('AI02')
        self.st.db.execute("UPDATE acct_policies SET status='revoked'")
        with self.assertRaises(PermissionError):self.invoke('AI02')

    def test_task_agent_business_period_and_identity_mismatch_denied(self):
        self.enable_finance();self.policy('AI02','summary')
        with self.assertRaises(PermissionError):self.invoke('AI04',task=self.tasks['BIZ-A','AI02'])
        with self.assertRaises(PermissionError):self.invoke('AI02',b='BIZ-B')
        with self.assertRaises(PermissionError):
            self.o.business_foundation.read_for_ai(self.owner,'BIZ-B','AI02','finance_read',self.start,self.end,task=self.tasks['BIZ-A','AI02'])
        with self.assertRaises(PermissionError):
            self.o.business_foundation.read_for_ai(Principal('representative','viewer'),'BIZ-A','AI02','finance_read',self.start,self.end,task=self.tasks['BIZ-A','AI02'])
        with self.assertRaises(PermissionError):
            self.o.business_foundation.read_for_ai(self.owner,'BIZ-A','AI02','finance_read','2025-12-01',self.end,task=self.tasks['BIZ-A','AI02'])
        with self.assertRaises(PermissionError):
            self.o.business_foundation.read_for_ai(self.owner,'BIZ-A','AI02','finance_read',self.start,self.end)

    def test_custom_kpi_cannot_leak_through_kpi_read(self):
        self.enable_finance();self.policy('AI02','summary',['expense_yen'])
        self.o.business_foundation.kpi(self.owner,'BIZ-A','private_profit','合成秘密利益','円')
        body=self.invoke('AI02','kpi_read')
        self.assertEqual(body['data'],{'expense_yen':30})
        self.assertNotIn('private_profit',json.dumps(body))
        self.assertNotIn('kpis',body)

    def test_no_synthetic_source_never_guesses_advertising_or_terms(self):
        self.enable_finance()
        for agent,section in (('AI07','advertising'),('AI15','terms')):
            self.policy(agent,section)
            with self.assertRaises(PermissionError):self.invoke(agent)

    def test_detail_and_audit_only_explicit_dedicated_service(self):
        self.enable_finance()
        for agent,section in (('AI27','detail'),('AI28','audit')):
            p=self.policy(agent,section,['amount_yen'])
            with self.assertRaises(PermissionError):self.invoke(agent)
            body=self.s.read(self.owner,self.tasks['BIZ-A',agent],p,section,['amount_yen'],self.start,self.end)
            self.assertEqual(body['data'],[{'amount_yen':100},{'amount_yen':30}])
            self.s.revoke(self.owner,p)
            with self.assertRaises(PermissionError):self.s.read(self.owner,self.tasks['BIZ-A',agent],p,section,['amount_yen'],self.start,self.end)

    def test_http_auth_spoofing_acl_contract_and_no_leaking_errors(self):
        self.enable_finance();self.policy('AI02','summary',['expense_yen'])
        h,csrf=self.headers()
        app=self.app();route=self.url()
        self.assertEqual(app.handle('GET',route,{'Host':'127.0.0.1:8765'})[0],303)
        reply=app.handle('GET',route,h)
        self.assertEqual(reply[0],200)
        self.assertEqual(reply[1]['X-Violet-Data-Contract'],VERSION)
        self.assertEqual(json.loads(reply[2])['data'],{'expense_yen':30})
        for key,value in (('agent','AI27'),('policy','fake'),('section','detail'),('fields','evidence'),('business','BIZ-B')):
            self.assertEqual(app.handle('GET',route+'&'+urlencode({key:value}),h)[0],400)
        self.assertEqual(app.handle('GET',route+'&task=spoof',h)[0],400)
        denied=app.handle('GET',self.url('AI04'),h)
        self.assertEqual(denied[0],403)
        self.assertNotIn('合成秘密明細本文',denied[2].decode())
        self.o.add_user('staff','business_operator','SyntheticStaffPass!123')
        self.o.portfolio.grant(self.owner,'BIZ-B','staff',True)
        sh,_=self.headers('staff','business_operator')
        self.assertEqual(app.handle('GET',route,sh)[0],403)
        # CSRF enforcement is unchanged on POST, with no accounting grant command exposed.
        self.assertEqual(app.handle('POST','/actions/ops_finance',h,urlencode({'csrf':'wrong','business':'BIZ-A'}).encode())[0],403)
        bad={**h,'Host':'attacker.invalid:8765'}
        self.assertEqual(app.handle('GET',route,bad)[0],403)

    def test_actual_http_server_applies_protection(self):
        self.enable_finance();self.policy('AI02','summary',['expense_yen'])
        token,_=self.o.new_session('owner','representative')
        from violet_office.web import COOKIE
        server=make_server(self.st.db.execute('PRAGMA database_list').fetchone()[2],0)
        worker=threading.Thread(target=lambda:server.serve_forever(poll_interval=0.02),daemon=True)
        worker.start()
        try:
            for agent,expected in (('AI02',200),('AI04',403)):
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                try:
                    conn.request('GET',self.url(agent),headers={'Cookie':COOKIE+'='+token})
                    response=conn.getresponse();body=response.read()
                    self.assertEqual(response.status,expected)
                    self.assertNotIn(b'evidence',body)
                    if expected==200:self.assertEqual(json.loads(body)['data'],{'expense_yen':30})
                finally:conn.close()
        finally:
            server.shutdown();server.server_close();worker.join(timeout=3)

    def test_accounting_results_cannot_enter_generic_artifact_or_history(self):
        self.enable_finance();self.policy('AI02','summary')
        t=self.tasks['BIZ-A','AI02'];self.invoke('AI02')
        run=self.st.claim(t)
        with self.assertRaises(PermissionError):self.st.finish(run,'合成秘密明細本文')
        self.assertEqual(self.st.db.execute('SELECT COUNT(*) FROM artifacts WHERE task_id=?',(t,)).fetchone()[0],0)
        self.assertEqual(self.st.db.execute('SELECT COUNT(*) FROM portfolio_results WHERE task_id=?',(t,)).fetchone()[0],0)
        for table in ('acct_handles','acct_events','acct_task_reads'):
            self.assertNotIn('合成秘密明細本文',json.dumps([dict(r) for r in self.st.db.execute('SELECT * FROM '+table)],ensure_ascii=False))

    def test_coordinator_rejects_copying_accounting_classified_task(self):
        self.enable_finance();self.policy('AI02','summary');self.invoke('AI02')
        request=self.o.coordinator.submit('synthetic-collection','市場調査')
        self.st.db.execute("INSERT INTO request_tasks VALUES(?,?,?)",(request,self.tasks['BIZ-A','AI02'],'specialist'))
        with self.assertRaises(PermissionError):self.o.coordinator.report(request)

    def test_handoff_recipient_rechecked_and_revoke_cannot_reuse_handle(self):
        self.enable_finance();p=self.policy('AI27','detail')
        t=self.tasks['BIZ-A','AI27']
        handle=self.s.issue(self.owner,t,p,'detail',['amount_yen'],self.start,self.end)
        other=self.policy('AI02','summary')
        with self.assertRaises(PermissionError):self.s.receive(self.owner,handle,self.tasks['BIZ-A','AI02'],other)
        self.s.revoke(self.owner,p)
        with self.assertRaises(PermissionError):self.s.receive(self.owner,handle,t,p)

    def test_representative_and_nonfinancial_routes_unchanged(self):
        self.assertEqual(self.o.business_foundation.read_for_ai(self.owner,'BIZ-A','AI04')['business_id'],'BIZ-A')
        h,_=self.headers();app=self.app()
        route='/business-operations-data?'+urlencode({'business':'BIZ-A','start':self.start,'end':self.end})
        response=app.handle('GET',route,h)
        self.assertEqual(response[0],200)
        self.assertIn('kpis',json.loads(response[2]))
        report=self.o.portfolio.report(self.owner,self.start,self.end)
        self.o.portfolio.decide_report(self.owner,report,'approve')
        self.assertEqual(self.st.db.execute('SELECT status FROM portfolio_reports WHERE id=?',(report,)).fetchone()[0],'approved')

    def test_generic_collaboration_handoff_refuses_classified_source(self):
        self.enable_finance();self.policy('AI02','summary');self.invoke('AI02')
        t=self.tasks['BIZ-A','AI02']
        with patch.object(self.o.collaboration,'row',return_value={'task_id':t}):
            with self.assertRaises(PermissionError):
                self.o.collaboration.handoff(self.owner,'BIZ-A','synthetic-job','synthetic-key','AI17','inquiry','合成理由')
        self.assertEqual(self.st.db.execute('SELECT COUNT(*) FROM aw_handoffs').fetchone()[0],0)

    def test_corrupt_policy_denial_does_not_echo_contents_or_logs(self):
        self.enable_finance();p=self.policy('AI02','summary')
        secret='PRIVATE-SYNTHETIC-POLICY-CONTENT'
        self.st.db.execute('UPDATE acct_policies SET fields=? WHERE id=?',('['+secret,p))
        h,_=self.headers();stdout,stderr=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
            reply=self.app().handle('GET',self.url(),h)
        self.assertEqual(reply[0],403)
        self.assertNotIn(secret,reply[2].decode())
        self.assertNotIn(secret,stdout.getvalue()+stderr.getvalue())
        for table in ('acct_events','ops_audit','office_audit'):
            self.assertNotIn(secret,json.dumps([dict(r) for r in self.st.db.execute('SELECT * FROM '+table)]))

    def test_current_assignment_and_approval_revocation_stop_connected_reads(self):
        self.enable_finance();self.policy('AI02','summary')
        self.st.db.execute("UPDATE bf_ai_assignments SET active=0 WHERE business_id='BIZ-A' AND agent='AI02'")
        for route in ('finance_read','kpi_read','business-task-kpis'):
            with self.assertRaises(PermissionError):self.invoke('AI02',route)
        self.st.db.execute("UPDATE bf_ai_assignments SET active=1 WHERE business_id='BIZ-A' AND agent='AI02'")
        self.st.db.execute("UPDATE office_users SET role='viewer' WHERE username='owner'")
        with self.assertRaises(PermissionError):self.invoke('AI02')

    def test_approved_policy_without_task_purpose_binding_is_denied(self):
        self.enable_finance()
        policy=fixtures.AccountingTests.policy(self,'AI02','summary')
        for route in ('finance_read','kpi_read','business-task-kpis'):
            with self.assertRaises(PermissionError):self.invoke('AI02',route)
        with self.assertRaises(PermissionError):self.s.bind_task(Principal('viewer','viewer'),self.tasks['BIZ-A','AI02'],policy)
        self.s.bind_task(self.owner,self.tasks['BIZ-A','AI02'],policy)
        self.assertEqual(self.invoke('AI02')['agent'],'AI02')
        # A second task for the same agent/business does not inherit the association.
        task=self.st.create_task('second-unbound','AI02',{'purpose':'different'})
        self.st.db.execute('INSERT INTO portfolio_tasks VALUES(?,?,?,?,?,?)',('BIZ-A',task,'analysis','different',3,now().isoformat()))
        with self.assertRaises(PermissionError):self.invoke('AI02',task=task)
