import base64
import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from violet_office.store import Store, now
from violet_office.content import ContentOffice, SAMPLE_BRIEF, CHECKS
from violet_office.integrations.adapters import Authorizer, MockAdapter, ProductionDisabled
from violet_office.integrations.service import Integrations


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'test.db'
        self.store=Store(self.path)
        self.authorizer=Authorizer('synthetic-admin-'+'a'*32,'synthetic-viewer-'+'v'*32)
        self.admin=self.authorizer.authenticate('synthetic-admin-'+'a'*32)
        self.viewer=self.authorizer.authenticate('synthetic-viewer-'+'v'*32)
        self.adapter=MockAdapter()
        self.service=Integrations(self.store,'synthetic-pseudonym-'+'p'*32,'synthetic-secret','mock-channel',self.adapter)
        self.user='U'+'a'*32
        self.subject=self.service.subject_ref(self.user)
        self.ts=int(now().timestamp()*1000)-100000
        self.sequence=0

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def event(self,kind='message',text='',event_id=None, timestamp=None):
        self.sequence+=1
        return {'webhookEventId':event_id or f'evt-{self.sequence}','type':kind,'timestamp':timestamp if timestamp is not None else self.ts+self.sequence,
            'source':{'type':'user','userId':self.user},'message':{'type':'text','text':text}}

    def post(self,events):
        body=json.dumps({'destination':'mock-channel','events':events},ensure_ascii=False).encode()
        signature=base64.b64encode(hmac.new(b'synthetic-secret',body,hashlib.sha256).digest()).decode()
        return self.service.webhook(body,signature)

    def follow_consent(self):
        self.post([self.event('follow'),self.event(text='同意:事業紹介')])

    def line_op(self):
        return self.store.db.execute("SELECT id FROM ix_operations WHERE purpose='事業紹介' ORDER BY rowid DESC LIMIT 1").fetchone()[0]

    def test_follow_is_not_consent_and_creates_candidate(self):
        result=self.post([self.event('follow')])
        self.assertEqual(result['processed'],1)
        self.assertFalse(self.service.eligible(self.subject,'事業紹介'))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ix_operations').fetchone()[0],1)
        self.assertEqual(self.adapter.calls,[])

    def test_consent_schedule_stop_reconsent_and_booking_stop(self):
        self.follow_consent()
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM ix_steps WHERE status='scheduled_mock'").fetchone()[0],7)
        self.post([self.event(text='停止')])
        self.assertFalse(self.service.eligible(self.subject,'事業紹介'))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM ix_steps WHERE status='scheduled_mock'").fetchone()[0],0)
        self.post([self.event(text='再同意:事業紹介')])
        self.assertTrue(self.service.eligible(self.subject,'事業紹介'))
        self.service.add_slot(self.admin,'slot','2030-01-10T01:00:00+00:00','2030-01-10T01:30:00+00:00')
        booking=self.service.request_booking(self.admin,'booking',self.subject,'slot')
        with self.assertRaises(ValueError):
            self.service.dispatch_mock(self.admin,booking['operation_id'])
        self.service.decide(self.admin,booking['operation_id'],'approve')
        self.service.dispatch_mock(self.admin,booking['operation_id'])
        self.assertEqual(self.service._customer(self.subject)['booked'],1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM ix_steps WHERE status='scheduled_mock'").fetchone()[0],0)
        self.post([self.event(text='再同意:事業紹介')])
        self.assertFalse(self.service.eligible(self.subject,'事業紹介'))

    def test_invalid_signature_and_channel_leave_no_customer(self):
        with self.assertRaises(ValueError):
            self.service.webhook(b'{"events":[]}', 'invalid')
        body=b'{"destination":"other","events":[]}'
        signature=base64.b64encode(hmac.new(b'synthetic-secret',body,hashlib.sha256).digest()).decode()
        with self.assertRaises(ValueError):
            self.service.webhook(body,signature)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ix_customers').fetchone()[0],0)

    def test_duplicate_event_redelivery_and_conflict(self):
        event=self.event('follow')
        self.post([event])
        event['deliveryContext']={'isRedelivery':True}
        event['replyToken']='not-stored'
        self.assertEqual(self.post([event])['duplicates'],1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ix_operations').fetchone()[0],1)
        event['type']='unfollow'
        with self.assertRaises(ValueError):
            self.post([event])

    def test_private_data_not_retained(self):
        self.follow_consent()
        self.post([self.event(text='secret personal text a@example.com')])
        dump='\n'.join(self.store.db.iterdump())
        self.assertNotIn(self.user,dump)
        self.assertNotIn('secret personal text',dump)
        self.assertNotIn('a@example.com',dump)
        self.assertNotIn('synthetic-secret',dump)

    def test_scope_and_withdrawal_cancel_approved_send(self):
        self.follow_consent()
        self.assertFalse(self.service.eligible(self.subject,'健康'))
        operation=self.line_op()
        self.service.decide(self.admin,operation,'approve')
        self.post([self.event(text='撤回:事業紹介')])
        with self.assertRaises(ValueError):
            self.service.dispatch_mock(self.admin,operation)
        self.assertEqual(self.adapter.calls,[])

    def test_unfollow_pauses_and_follow_does_not_unstop(self):
        self.follow_consent()
        self.post([self.event('unfollow'),self.event('follow')])
        self.assertFalse(self.service.eligible(self.subject,'事業紹介'))

    def test_older_consent_does_not_reactivate(self):
        self.follow_consent()
        self.post([self.event(text='停止')])
        self.post([self.event(text='再同意:事業紹介',timestamp=self.ts)])
        self.assertFalse(self.service.eligible(self.subject,'事業紹介'))

    def test_authorization_and_export(self):
        self.follow_consent()
        with self.assertRaises(PermissionError):
            self.authorizer.authenticate('wrong-token')
        with self.assertRaises(PermissionError):
            self.service.decide(self.viewer,self.line_op(),'approve')
        with self.assertRaises(PermissionError):
            self.service.export_customer(self.viewer,self.subject)
        self.assertIn('consents',self.service.export_customer(self.admin,self.subject))
        self.assertIn('未接続',self.service.dashboard_html(self.viewer))

    def test_approved_mock_send_is_not_real_and_cannot_repeat(self):
        self.follow_consent()
        operation=self.line_op()
        self.service.decide(self.admin,operation,'approve')
        result=self.service.dispatch_mock(self.admin,operation)
        self.assertEqual(result['status'],'mock_succeeded')
        self.assertEqual(result['actual_external_calls'],0)
        with self.assertRaises(ValueError):
            self.service.dispatch_mock(self.admin,operation)
        self.assertEqual(len(self.adapter.calls),1)

    def test_retry_backoff_limits_and_unknown_not_resent(self):
        self.follow_consent()
        operation=self.line_op()
        self.adapter.outcomes=['retryable','unknown']
        self.service.decide(self.admin,operation,'approve')
        self.assertEqual(self.service.dispatch_mock(self.admin,operation)['status'],'retry_wait')
        with self.assertRaises(ValueError):
            self.service.dispatch_mock(self.admin,operation)
        self.store.db.execute('UPDATE ix_operations SET next_attempt=NULL WHERE id=?',(operation,))
        self.assertEqual(self.service.dispatch_mock(self.admin,operation)['status'],'manual_reconciliation')
        with self.assertRaises(ValueError):
            self.service.dispatch_mock(self.admin,operation)
        self.assertEqual(len(self.adapter.calls),2)

    def test_retry_exhaustion(self):
        self.follow_consent()
        operation=self.line_op()
        self.adapter.outcomes=['retryable']*3
        self.service.decide(self.admin,operation,'approve')
        for _ in range(3):
            self.store.db.execute('UPDATE ix_operations SET next_attempt=NULL WHERE id=?',(operation,))
            result=self.service.dispatch_mock(self.admin,operation)
        self.assertEqual(result['status'],'failed')
        with self.assertRaises(ValueError):
            self.service.dispatch_mock(self.admin,operation)

    def test_overlapping_slots_cannot_double_book(self):
        self.follow_consent()
        self.service.add_slot(self.admin,'one','2030-01-10T01:00:00+00:00','2030-01-10T01:30:00+00:00')
        self.service.add_slot(self.admin,'two','2030-01-10T01:15:00+00:00','2030-01-10T01:45:00+00:00')
        a=self.service.request_booking(self.admin,'a',self.subject,'one')
        self.assertEqual(a,self.service.request_booking(self.admin,'a',self.subject,'one'))
        b=self.service.request_booking(self.admin,'b',self.subject,'two')
        self.service.decide(self.admin,a['operation_id'],'approve')
        with self.assertRaises(ValueError):
            self.service.decide(self.admin,b['operation_id'],'approve')
        self.assertEqual(self.service.slots(self.viewer),[])

    def test_change_and_cancel_never_resume_steps(self):
        self.follow_consent()
        for slot,hour in [('one','01'),('two','02')]:
            self.service.add_slot(self.admin,slot,f'2030-01-10T{hour}:00:00+00:00',f'2030-01-10T{hour}:30:00+00:00')
        a=self.service.request_booking(self.admin,'a',self.subject,'one')
        self.service.decide(self.admin,a['operation_id'],'approve')
        self.service.dispatch_mock(self.admin,a['operation_id'])
        b=self.service.request_booking(self.admin,'b',self.subject,'two',replacement_for=a['booking_id'])
        self.service.decide(self.admin,b['operation_id'],'approve')
        self.service.dispatch_mock(self.admin,b['operation_id'])
        cancel=self.service.cancel_booking(self.admin,b['booking_id'])
        self.service.decide(self.admin,cancel,'approve')
        self.service.dispatch_mock(self.admin,cancel)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM ix_bookings WHERE status='confirmed'").fetchone()[0],0)
        self.assertFalse(self.service.eligible(self.subject,'事業紹介'))

    def test_sns_requires_current_approved_content_and_operation_approval(self):
        content=ContentOffice(self.store)
        campaign=content.generate('sns',SAMPLE_BRIEF,start_date='2030-01-10')
        post=next(c for c in content.report(campaign)['contents'] if c['kind']=='sns')
        operation=self.service.propose_sns(self.admin,'sns-op',post['id'],['asset_test'])
        with self.assertRaises(ValueError):
            self.service.decide(self.admin,operation,'approve')
        for check in CHECKS:
            content.confirm_check(post['id'],check,'合成テストのみ')
        content.decide(post['id'],'approve','representative')
        self.service.decide(self.admin,operation,'approve')
        content.revise(post['id'],{'body':'修正版'})
        with self.assertRaises(ValueError):
            self.service.dispatch_mock(self.admin,operation)

    def test_live_transport_and_paid_calls_are_impossible(self):
        with self.assertRaises(ValueError):
            Integrations(self.store,'p'*32,'secret','channel',ProductionDisabled())
        self.assertFalse(self.service.dashboard_data(self.viewer)['cost']['paid_calls_enabled'])

    def test_limits(self):
        for _ in range(60):
            self.post([])
        with self.assertRaises(ValueError):
            self.post([])

    def test_inquiry_and_interview_candidate(self):
        self.follow_consent()
        self.post([self.event(text='面談希望')])
        inquiry=self.store.db.execute('SELECT category FROM ix_inquiries').fetchone()[0]
        self.assertEqual(inquiry,'面談希望')
        self.assertEqual(self.adapter.calls,[])

    def test_request_specs_and_unconnected_sns_cannot_send(self):
        from violet_office.integrations.adapters import LineMessagingAdapter,SnsDraftAdapter
        adapter=LineMessagingAdapter()
        spec=adapter.build_text_push(self.user,'合成テストの下書き')
        self.assertEqual(spec.path,'/v2/bot/message/push')
        self.assertNotIn('Authorization',spec.json_body)
        with self.assertRaises(PermissionError):
            adapter.execute(spec)
        self.assertFalse(SnsDraftAdapter().capabilities()['publish'])

    def test_framework_neutral_endpoint_rejects_bad_signature(self):
        from violet_office.integrations.adapters import WebhookEndpoint
        code,result=WebhookEndpoint(self.service).handle(b'{}','invalid')
        self.assertEqual(code,400)
        self.assertNotIn('secret',str(result))

    def test_two_workers_cannot_dispatch_same_operation_twice(self):
        self.follow_consent()
        operation=self.line_op()
        self.service.decide(self.admin,operation,'approve')
        def dispatch(_):
            store=Store(self.path)
            try:
                service=Integrations(store,'synthetic-pseudonym-'+'p'*32,'synthetic-secret','mock-channel')
                try:
                    service.dispatch_mock(self.admin,operation)
                    return True
                except ValueError:
                    return False
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(dispatch,range(2))),1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ix_attempts WHERE operation_id=?',(operation,)).fetchone()[0],1)

    def test_demo_cli_is_reproducible_without_network(self):
        import subprocess
        db=Path(self.temp.name)/'demo.db'
        command=['python3','-m','violet_office.integrations_cli','demo','--db',str(db)]
        a=json.loads(subprocess.run(command,capture_output=True,text=True,check=True).stdout)
        b=json.loads(subprocess.run(command,capture_output=True,text=True,check=True).stdout)
        self.assertEqual(len(a['bookings']),len(b['bookings']))
        self.assertEqual(a['cost']['actual_microusd'],0)
        self.assertTrue(all(s['status']=='stopped' for s in b['steps']))
        self.assertTrue(any(o['kind']=='sns' and o['status']=='approval_pending' for o in b['operations']))

    def test_endpoint_rate_limit_uses_429(self):
        from violet_office.integrations.adapters import WebhookEndpoint
        endpoint=WebhookEndpoint(self.service)
        body=b'{"destination":"mock-channel","events":[]}'
        signature=base64.b64encode(hmac.new(b'synthetic-secret',body,hashlib.sha256).digest()).decode()
        for _ in range(60):
            self.assertEqual(endpoint.handle(body,signature)[0],200)
        self.assertEqual(endpoint.handle(body,signature)[0],429)

    def test_booking_unknown_holds_slot_and_stops_steps_without_retry(self):
        self.follow_consent()
        self.service.add_slot(self.admin,'one','2030-01-10T01:00:00+00:00','2030-01-10T01:30:00+00:00')
        booking=self.service.request_booking(self.admin,'unknown',self.subject,'one')
        self.service.decide(self.admin,booking['operation_id'],'approve')
        self.adapter.outcomes=['unknown']
        result=self.service.dispatch_mock(self.admin,booking['operation_id'])
        self.assertEqual(result['status'],'manual_reconciliation')
        self.assertEqual(self.service.slots(self.viewer),[])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM ix_steps WHERE status='scheduled_mock'").fetchone()[0],0)
        with self.assertRaises(ValueError):
            self.service.dispatch_mock(self.admin,booking['operation_id'])

    def test_revision_change_invalidates_old_send_approval(self):
        self.follow_consent()
        operation=self.line_op()
        self.service.decide(self.admin,operation,'approve')
        self.post([self.event(text='同意:健康')])
        with self.assertRaises(ValueError):
            self.service.dispatch_mock(self.admin,operation)

    def test_rejected_booking_has_rejected_status(self):
        self.follow_consent()
        self.service.add_slot(self.admin,'one','2030-01-10T01:00:00+00:00','2030-01-10T01:30:00+00:00')
        booking=self.service.request_booking(self.admin,'rejected',self.subject,'one')
        self.service.decide(self.admin,booking['operation_id'],'reject')
        self.assertEqual(self.store.db.execute('SELECT status FROM ix_bookings WHERE id=?',(booking['booking_id'],)).fetchone()[0],'rejected')
        self.assertEqual(len(self.service.slots(self.viewer)),1)

    def test_held_booking_keeps_delivery_suppressed_after_reconsent(self):
        self.follow_consent()
        self.service.add_slot(self.admin,'one','2030-01-10T01:00:00+00:00','2030-01-10T01:30:00+00:00')
        booking=self.service.request_booking(self.admin,'held',self.subject,'one')
        self.service.decide(self.admin,booking['operation_id'],'approve')
        self.post([self.event(text='再同意:事業紹介')])
        self.assertFalse(self.service.eligible(self.subject,'事業紹介'))

    def test_same_sns_different_key_cannot_queue_duplicate(self):
        campaign=self.service.content.generate('unique',SAMPLE_BRIEF,start_date='2030-01-10')
        content=next(c for c in self.service.content.report(campaign)['contents'] if c['kind']=='sns')
        self.service.propose_sns(self.admin,'one',content['id'],['asset_demo'])
        with self.assertRaises(ValueError):
            self.service.propose_sns(self.admin,'two',content['id'],['asset_demo'])

    def test_erasure_plan_does_not_delete_customer(self):
        self.follow_consent()
        plan=self.service.erasure_plan(self.admin,self.subject)
        self.assertFalse(plan['executed'])
        self.assertTrue(plan['requires_representative_approval'])
        self.assertIn('consents',self.service.export_customer(self.admin,self.subject))
