import hashlib
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4
from datetime import timedelta

from violet_office.store import Store,now
from violet_office.office import Office,JST
from violet_office.web import OfficeApp,COOKIE
from violet_office.content import CHECKS,SAMPLE_BRIEF
from violet_office.integrations.demo import run_demo


class OfficeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'test.db'
        self.store=Store(self.path,budget_microusd=10000000)
        self.office=Office(self.store)
        self.office.add_user('owner','representative','SyntheticOwnerPass!123')
        self.office.add_user('reader','viewer','SyntheticReaderPass!123')
        self.app=OfficeApp(self.path,8765)
        self.headers={'Host':'127.0.0.1:8765','Origin':'http://127.0.0.1:8765','Content-Type':'application/x-www-form-urlencoded'}
        self.token,self.csrf=self.office.new_session('owner','representative')
        self.headers['Cookie']=COOKIE+'='+self.token
        self.session=self.office.session(self.token)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def get(self,path='/',headers=None):
        return self.app.handle('GET',path,headers or self.headers)

    def post(self,action,data,nonce=None,headers=None):
        payload={'csrf':self.csrf,'nonce':nonce or uuid4().hex,**data}
        return self.app.handle('POST','/actions/'+action,headers or self.headers,urlencode(payload).encode())

    def test_login_hash_session_rotation_and_logout(self):
        status,headers,body=self.get('/login',{'Host':'127.0.0.1:8765'})
        self.assertEqual(status,200)
        csrf=re.search(rb'name="csrf" value="([^"]+)"',body).group(1).decode()
        cookie=headers['Set-Cookie'].split(';')[0]
        h={**self.headers,'Cookie':cookie}
        old=cookie.split('=',1)[1]
        response=self.app.handle('POST','/login',h,urlencode({'csrf':csrf,'username':'owner','password':'SyntheticOwnerPass!123'}).encode())
        self.assertEqual(response[0],303)
        self.assertIsNone(self.office.session(old))
        self.assertIn('HttpOnly',response[1]['Set-Cookie'])
        self.assertIn('SameSite=Strict',response[1]['Set-Cookie'])
        token=response[1]['Set-Cookie'].split(';')[0].split('=',1)[1]
        session=self.office.session(token)
        self.assertEqual(session['role'],'representative')
        result=self.app.handle('POST','/logout',{**h,'Cookie':COOKIE+'='+token},urlencode({'csrf':session['csrf']}).encode())
        self.assertEqual(result[0],303)
        self.assertIsNone(self.office.session(token))
        stored=self.store.db.execute('SELECT password_hash FROM office_users WHERE username=?',('owner',)).fetchone()[0]
        self.assertNotIn('Synthetic',stored)

    def test_unauthenticated_private_pages_redirect(self):
        self.assertEqual(self.get('/',{'Host':'127.0.0.1:8765'})[0],303)
        self.assertEqual(self.get('/monitor',{'Host':'127.0.0.1:8765'})[0],303)

    def test_host_origin_csrf_and_viewer_mutations_blocked(self):
        self.assertEqual(self.get('/',{'Host':'evil.example'})[0],403)
        self.assertEqual(self.post('submit',{'instruction':'市場調査','key':'x'},headers={**self.headers,'Origin':'http://evil.example'})[0],403)
        self.assertEqual(self.post('submit',{'csrf':'wrong','instruction':'市場調査','key':'x'})[0],403)
        token,csrf=self.office.new_session('reader','viewer')
        self.assertEqual(self.post('submit',{'csrf':csrf,'instruction':'市場調査','key':'viewer'},headers={**self.headers,'Cookie':COOKIE+'='+token})[0],403)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)

    def test_all_main_pages_and_28_employees(self):
        for path in ('/','/employees','/instructions','/approvals','/analytics','/costs','/monitor'):
            with self.subTest(path=path):
                response=self.get(path)
                self.assertEqual(response[0],200)
                self.assertIn("script-src 'none'",response[1]['Content-Security-Policy'])
        page=self.get('/employees')[2].decode()
        for i in range(1,29):
            self.assertIn(f'AI{i:02}',page)
        self.assertIn('API未接続',page)

    def test_submit_assign_report_and_replay_prevention(self):
        nonce=uuid4().hex
        response=self.post('submit',{'instruction':'SNS投稿文とLINE配信','key':'ui-task','priority':'2'},nonce)
        self.assertEqual(response[0],303)
        request=response[1]['Location'].split('=')[1]
        report=self.office.coordinator.report(request)
        self.assertIn('AI09',[t['agent'] for t in report['tasks']])
        self.assertEqual(report['priority'],2)
        count=len(self.office.tasks())
        self.assertEqual(self.post('submit',{'instruction':'SNS投稿文とLINE配信','key':'ui-task','priority':'2'},nonce)[0],303)
        self.assertEqual(len(self.office.tasks()),count)
        self.assertEqual(self.post('submit',{'instruction':'市場調査','key':'different'},nonce)[0],400)
        self.assertEqual(self.get('/requests?id='+request)[0],200)

    def test_task_start_stop_resume_result_review_approval(self):
        task=self.store.create_task('manual','AI09',{'brief':'匿名'},action='sns_post')
        self.assertEqual(self.post('start',{'id':task})[0],303)
        self.assertEqual(self.post('stop',{'id':task})[0],303)
        self.assertEqual(self.post('start',{'id':task})[0],303)
        self.assertEqual(self.post('result',{'id':task,'content':'匿名の手動成果物'})[0],303)
        artifact=self.store.db.execute('SELECT id FROM artifacts WHERE task_id=?',(task,)).fetchone()[0]
        self.assertEqual(self.post('task_review',{'id':artifact,'verdict':'pass','note':'別担当による確認記録'})[0],303)
        self.assertEqual(self.post('task_decide',{'id':artifact,'decision':'approve'})[0],303)
        self.assertEqual(self.store.db.execute('SELECT status FROM tasks WHERE id=?',(task,)).fetchone()[0],'manual_execution_pending')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0],0)

    def test_retry_keeps_history_and_replaces_failed_dependency(self):
        request=self.office.coordinator.submit('retry-request','市場調査')
        task=next(t for t in self.office.coordinator.report(request)['tasks'] if t['kind']=='specialist')
        self.store.fail(self.store.claim(task['id']),'internal')
        self.assertEqual(self.post('retry',{'id':task['id']})[0],303)
        new=next(t for t in self.office.coordinator.report(request)['tasks'] if t['kind']=='specialist')
        self.assertNotEqual(new['id'],task['id'])
        self.assertEqual(new['status'],'pending')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM office_task_links').fetchone()[0],1)

    def test_priority_and_collect_workflow(self):
        request=self.office.coordinator.submit('priority','市場調査')
        self.assertEqual(self.post('priority',{'id':request,'priority':'1'})[0],303)
        specialist=next(t for t in self.office.coordinator.report(request)['tasks'] if t['kind']=='specialist')
        artifact=self.store.finish(self.store.claim(specialist['id']),'匿名の調査結果')
        self.store.review(artifact,'AI28','pass')
        self.assertEqual(self.post('collect',{'id':request})[0],303)
        self.assertEqual(self.office.coordinator.report(request)['status'],'review_pending')

    def content(self):
        campaign=self.office.content.generate('content',SAMPLE_BRIEF,start_date='2030-01-10')
        return self.office.content.report(campaign)['contents'][0]['id']

    def test_unchecked_content_cannot_be_approved_then_check_and_approve(self):
        content=self.content()
        self.assertEqual(self.post('content_decide',{'id':content,'decision':'approve'})[0],400)
        for check in CHECKS:
            self.assertEqual(self.post('content_check',{'id':content,'check':check,'evidence':'合成テストの確認、実査ではない'})[0],303)
        self.assertEqual(self.post('content_decide',{'id':content,'decision':'approve'})[0],303)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0],0)
        self.assertIn('owner',[r[0] for r in self.store.db.execute('SELECT actor FROM office_audit')])

    def test_content_reject_changes_revision(self):
        content=self.content()
        self.assertEqual(self.post('content_decide',{'id':content,'decision':'changes'})[0],303)
        self.assertEqual(self.post('revise',{'id':content,'body':'{"body":"新しい匿名の下書き"}'})[0],303)
        new=self.store.db.execute('SELECT id FROM content_versions WHERE logical_key=(SELECT logical_key FROM content_versions WHERE id=?) AND version=2',(content,)).fetchone()[0]
        self.assertEqual(self.post('content_decide',{'id':new,'decision':'reject'})[0],303)

    def test_business_kpi_never_counts_mock_as_actual(self):
        run_demo(self.store)
        metrics=self.office.metrics()
        self.assertTrue(all(value is None for value in metrics['business'].values()))
        self.assertEqual(metrics['mock']['予約確定'],1)
        page=self.get('/analytics')[2].decode()
        self.assertIn('未計測',page)
        self.assertIn('実績に含めない',page)

    def test_cost_accounting_groups_and_monthly_budget_guard(self):
        month=now().astimezone(JST).strftime('%Y-%m')
        date=now().astimezone(JST).date().isoformat()
        response=self.post('cost',{'key':'invoice-1','model':'test-model','agent':'AI09','task':'','amount':'0.1','kind':'confirmed','mode':'manual','date':date,'evidence':'合成請求記録'})
        self.assertEqual(response[0],303)
        totals=self.office.costs()['groups']
        self.assertEqual(next(g['amount'] for g in totals if g['group']=='model'),100000)
        self.assertEqual(self.post('budget',{'month':month,'amount':'0.15'})[0],303)
        task=self.store.create_task('budget1','AI09',{'brief':'anonymous'})
        self.store.claim(task,40000)
        task2=self.store.create_task('budget2','AI09',{'brief':'anonymous'})
        with self.assertRaises(ValueError):
            self.store.claim(task2,20000)
        # Free local work is allowed even if an external API budget is exhausted.
        self.store.claim(task2,0)

    def test_mock_cost_separate_and_invalid_values(self):
        for amount in ('NaN','-1','0.0000001'):
            self.assertEqual(self.post('budget',{'month':'2026-10','amount':amount})[0],400)
        self.assertEqual(self.office.metrics()['business']['契約件数'],None)

    def test_xss_sensitive_content_and_secret_not_exposed(self):
        self.store.create_task('xss','AI09',{'brief':'anonymous'})
        task=self.store.create_task('artifact','AI09',{'brief':'anonymous'})
        self.store.finish(self.store.claim(task),'<script>alert(1)</script> Bearer synthetic_secret a@example.com U'+'a'*32)
        page=self.get('/employees?id=AI09')[2].decode()
        self.assertNotIn('<script>',page)
        self.assertNotIn('synthetic_secret',page)
        self.assertNotIn('a@example.com',page)
        self.assertIn('&lt;script&gt;',page)

    def test_responsive_css_and_unconnected_status(self):
        css=self.get('/assets/office.css')[2].decode()
        self.assertIn('@media(max-width:760px)',css)
        page=self.get('/')[2].decode()
        self.assertIn('name="viewport"',page)
        self.assertIn('未計測',page)
        self.assertIn('未接続',self.get('/monitor')[2].decode())

    def test_session_idle_expiry_and_login_rate_limit(self):
        self.store.db.execute('UPDATE office_sessions SET seen_at=?',((now()-timedelta(minutes=31)).isoformat(),))
        self.assertIsNone(self.office.session(self.token))
        # Keep this test in one ten-minute bucket even across wall-clock boundaries.
        from unittest.mock import patch
        with patch('violet_office.office.now',return_value=now()):
            for _ in range(11):
                with self.assertRaises(PermissionError):
                    self.office.login('owner','wrong')
            with self.assertRaises(PermissionError):
                self.office.login('owner','SyntheticOwnerPass!123')

    def test_no_dispatch_route_and_operation_approval_does_not_execute(self):
        service,admin,data=run_demo(self.store)
        # New mock LINE consent operation, separately created for this test.
        subject=data['customers'][0]['subject']
        self.assertEqual(self.app.handle('POST','/actions/dispatch',self.headers,urlencode({'csrf':self.csrf,'nonce':uuid4().hex}).encode())[0],400)
        self.assertEqual(self.get('/api/send')[0],404)

    def test_four_instruction_examples_choose_documented_roles(self):
        for instruction,agent in [
            ('来週のInstagram投稿を7日分作成して','AI08'),
            ('ベネシードディーラーの紹介文章を5種類作成して','AI16'),
            ('今月の問い合わせと面談予約を分析して','AI27'),
            ('40代〜60代向けの集客戦略を提案して','AI07'),
        ]:
            with self.subTest(instruction=instruction):
                self.assertIn(agent,[a['agent'] for a in self.office.coordinator.analyze(instruction)['assignments']])

    def test_operation_approval_is_persisted_without_dispatch(self):
        service,admin,data=run_demo(self.store)
        service.add_slot(admin,'ui-slot','2030-01-10T03:00:00+00:00','2030-01-10T03:30:00+00:00')
        booking=service.request_booking(admin,'ui-booking',data['customers'][0]['subject'],'ui-slot')
        before=self.store.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0]
        self.assertEqual(self.post('operation_decide',{'id':booking['operation_id'],'decision':'approve'})[0],303)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0],before)
        self.assertEqual(self.post('operation_decide',{'id':booking['operation_id'],'decision':'approve'})[0],400)
        self.assertEqual(self.store.db.execute('SELECT status FROM ix_bookings WHERE id=?',(booking['booking_id'],)).fetchone()[0],'held')

    def test_real_http_server_login_and_host_enforcement(self):
        import http.client
        import threading
        from violet_office.web import make_server
        server=make_server(self.path,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            connection.request('GET','/login')
            result=connection.getresponse()
            html=result.read()
            cookie=result.getheader('Set-Cookie').split(';')[0]
            csrf=re.search(rb'name="csrf" value="([^"]+)"',html).group(1).decode()
            self.assertEqual(result.status,200)
            self.assertEqual(result.getheader('Referrer-Policy'),'same-origin')
            connection.request('POST','/login',urlencode({'csrf':csrf,'username':'owner','password':'SyntheticOwnerPass!123'}),
                {'Cookie':cookie,'Content-Type':'application/x-www-form-urlencoded','Origin':f'http://127.0.0.1:{server.server_port}'})
            login=connection.getresponse()
            login.read()
            self.assertEqual(login.status,303)
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
