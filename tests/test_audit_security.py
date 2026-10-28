import http.client
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from violet_office.store import Store,now
from violet_office.office import Office,redact
from violet_office.registry import DEFAULT_CONFIG,load_agents
from violet_office.content import CHECKS,SAMPLE_BRIEF,safe_input
from violet_office.web import OfficeApp,make_server
from violet_office.ops import backup
from violet_office.validation import dependency_inventory,assess,source_fingerprint,REQUIRED_TESTS


class AuditSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'audit.db'
        self.store=Store(self.path)
        self.office=Office(self.store)
        self.office.add_user('audit','representative','SyntheticAuditPass!123')
        self.token,_=self.office.new_session('audit','representative')
        self.session=self.office.session(self.token)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def content(self):
        campaign=self.office.content.generate('audit-content',SAMPLE_BRIEF,start_date='2030-01-10')
        return self.office.content.report(campaign)['contents'][0]['id']

    def test_all_28_roles_and_five_heads_have_valid_members(self):
        registry=load_agents()
        self.assertEqual(len(registry),28)
        reports=self.office.coordinator.department_report()
        self.assertEqual(len(reports),5)
        members=[m for r in reports for m in r['members']]
        self.assertEqual(len(members),len(set(members)))
        self.assertEqual(len(members),21)
        for r in reports:
            self.assertTrue(all(registry[m]['reports_to']==r['head'] for m in r['members']))
            self.assertFalse(r['model_connected'])

    def test_supervisor_cycle_and_unlisted_tool_rejected(self):
        original=json.loads(DEFAULT_CONFIG.read_text())
        changed=json.loads(DEFAULT_CONFIG.read_text())
        changed['agents'][1]['reports_to']='AI03'
        path=Path(self.temp.name)/'agents.json'
        path.write_text(json.dumps(changed))
        with self.assertRaises(ValueError):
            load_agents(path)
        original['agents'][0]['tools'].append('network.send')
        path.write_text(json.dumps(original))
        with self.assertRaises(ValueError):
            load_agents(path)

    def test_user_role_change_revokes_old_session_and_pending_command(self):
        self.store.db.execute("UPDATE office_users SET role='viewer' WHERE username='audit'")
        self.assertIsNone(self.office.session(self.token))
        with self.assertRaises(PermissionError):
            self.office.commands(self.session,uuid4().hex,'submit',{'key':'must-not-create','instruction':'市場調査'})
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)

    def test_logout_race_cannot_use_old_session_snapshot(self):
        self.office.logout(self.token)
        with self.assertRaises(PermissionError):
            self.office.commands(self.session,uuid4().hex,'submit',{'key':'expired','instruction':'市場調査'})

    def test_old_legal_check_schema_cannot_approve(self):
        content=self.content()
        old={check:'合成の既存確認' for check in CHECKS if check not in ('LINE利用規約','SNS利用規約')}
        self.store.db.execute('UPDATE content_audits SET checks=? WHERE content_id=?',(json.dumps(old),content))
        with self.assertRaises(ValueError):
            self.office.content.decide(content,'approve','representative')

    def test_publication_revalidates_quality_and_body_after_approval(self):
        content=self.content()
        for check in CHECKS:
            self.office.content.confirm_check(content,check,'合成確認・実査なし')
        self.office.content.decide(content,'approve','representative')
        self.store.db.execute('UPDATE content_versions SET body=? WHERE id=?',('{"body":"changed"}',content))
        with self.assertRaises(ValueError):
            self.office.content.record_publication(content,'合成証跡','representative')

    def test_json_secrets_are_redacted_and_input_rejected(self):
        output=redact('{"payload":{"access_token":"synthetic-sensitive-value","api_key":"synthetic-private"},"text":"normal"}')
        self.assertNotIn('synthetic-sensitive-value',output)
        self.assertNotIn('synthetic-private',output)
        self.assertIn('normal',output)
        for text in ('{"api_key":"synthetic-private"}','access_token=synthetic-sensitive-value','-----BEGIN PRIVATE KEY-----'):
            with self.assertRaises(ValueError):
                safe_input(text)

    def test_sql_injection_is_data_not_execution(self):
        self.office.coordinator.submit('injection',"市場調査'; DROP TABLE tasks; --")
        self.assertGreater(self.store.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)
        with self.assertRaises(PermissionError):
            self.office.login("audit' OR 1=1 --",'anything')

    def test_global_rate_limit_and_invalid_url(self):
        app=OfficeApp(self.path,8765)
        self.assertEqual(app.handle('GET','http://[',{'Host':'127.0.0.1:8765'})[0],400)
        app=OfficeApp(self.path,8765)
        clock=[1000]
        app._clock=lambda:clock[0]
        for _ in range(180):
            self.assertEqual(app.handle('GET','/assets/office.css',{'Host':'127.0.0.1:8765'})[0],200)
        self.assertEqual(app.handle('GET','/assets/office.css',{'Host':'127.0.0.1:8765'})[0],429)
        clock[0]+=61
        self.assertEqual(app.handle('GET','/assets/office.css',{'Host':'127.0.0.1:8765'})[0],200)

    def test_bounded_http_workers_reject_excess_then_recover(self):
        server=make_server(self.path,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            for _ in range(server.max_workers):
                self.assertTrue(server._workers.acquire(blocking=False))
            blocked=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            blocked.request('GET','/login')
            # An intentional close can surface as WSAECONNABORTED on Windows.
            with self.assertRaises((http.client.RemoteDisconnected,ConnectionResetError,ConnectionAbortedError)):
                blocked.getresponse()
            blocked.close()
            for _ in range(server.max_workers):
                server._workers.release()
            allowed=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            allowed.request('GET','/login')
            response=allowed.getresponse()
            response.read()
            self.assertEqual(response.status,200)
            allowed.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_finish_rechecks_dependency_after_child_rejected(self):
        child=self.store.create_task('child','AI09',{},action='sns_post')
        artifact=self.store.finish(self.store.claim(child),'合成成果物')
        self.store.review(artifact,'AI28','pass')
        parent=self.store.create_task('parent','AI01',{})
        self.store.db.execute('INSERT INTO dependencies VALUES (?,?)',(parent,child))
        run=self.store.claim(parent)
        self.store.decide(artifact,'reject','representative')
        with self.assertRaises(ValueError):
            self.store.finish(run,'集約すべきでない')

    def test_two_expiry_workers_recover_each_run_once(self):
        task=self.store.create_task('expire','AI09',{})
        run=self.store.claim(task)
        self.store.db.execute("UPDATE runs SET deadline='2000-01-01T00:00:00+00:00' WHERE id=?",(run,))
        def expire(_):
            store=Store(self.path)
            try:
                return store.expire()
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(expire,range(2))),1)

    def test_backup_has_bounded_time_and_cleans_only_new_output(self):
        destination=Path(self.temp.name)/'timeout.db'
        with patch('violet_office.ops.time.monotonic',side_effect=[0,31]):
            with self.assertRaises(TimeoutError):
                backup(self.path,destination)
        self.assertFalse(destination.exists())
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM office_users').fetchone()[0],1)

    def test_dependency_inventory_and_readiness_fail_closed(self):
        self.assertEqual(dependency_inventory()['runtime_external_imports'],[])
        self.assertFalse(assess()['production_allowed'])
        evidence={'source_fingerprint':'stale','tests':{'run':len(REQUIRED_TESTS),'passed':len(REQUIRED_TESTS),'successful':True}}
        self.assertEqual(assess(evidence)['gates'][0]['state'],'unverified')
        evidence['source_fingerprint']=source_fingerprint()
        self.assertEqual(assess(evidence)['gates'][0]['state'],'unverified')
        evidence['executed_tests']=sorted(REQUIRED_TESTS)
        self.assertEqual(assess(evidence)['gates'][0]['state'],'passed')
        self.assertFalse(assess(evidence)['production_allowed'])

    def test_exact_monthly_limit_prevents_further_paid_reservations(self):
        month=now().astimezone(__import__('zoneinfo').ZoneInfo('Asia/Tokyo')).strftime('%Y-%m')
        self.store.db.execute('UPDATE budget SET limit_microusd=1000')
        self.store.db.execute('UPDATE office_budgets SET limit_microusd=100 WHERE month=?',(month,))
        task=self.store.create_task('at-cap','AI09',{})
        self.store.claim(task,100)
        following=self.store.create_task('over-cap','AI09',{})
        with self.assertRaises(ValueError):
            self.store.claim(following,1)
        self.assertEqual(self.store.db.execute('SELECT attempts FROM tasks WHERE id=?',(following,)).fetchone()[0],0)
        self.store.claim(following,0)

    def test_duplicate_host_headers_are_rejected_at_http_entry(self):
        server=make_server(self.path,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            connection.putrequest('GET','/login',skip_host=True)
            connection.putheader('Host',f'127.0.0.1:{server.server_port}')
            connection.putheader('Host','evil.example')
            connection.endheaders()
            response=connection.getresponse()
            response.read()
            self.assertEqual(response.status,400)
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
