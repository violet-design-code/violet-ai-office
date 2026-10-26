"""A-H: synthetic end-to-end local workflows, not real provider tests."""
import base64
import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path

from violet_office.store import Store,now
from violet_office.office import Office
from violet_office.web import OfficeApp,COOKIE
from violet_office.content import CHECKS,SAMPLE_BRIEF
from violet_office.integrations.adapters import MockAdapter,Authorizer
from violet_office.integrations.service import Integrations


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'scenario.db'
        self.store=Store(self.path)
        self.office=Office(self.store)
        self.adapter=MockAdapter()
        self.service=Integrations(self.store,'synthetic-scenario-pseudonym-'+'k'*32,'synthetic-signature','scenario-channel',self.adapter)
        self.admin=Authorizer('synthetic-admin-'+'a'*32,'synthetic-viewer-'+'v'*32).authenticate('synthetic-admin-'+'a'*32)
        self.raw='U'+'b'*32
        self.subject=self.service.subject_ref(self.raw)
        self.seq=0
        self.timestamp=int(now().timestamp()*1000)-100000

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def post(self,kind='message',text=''):
        self.seq+=1
        event={'type':kind,'timestamp':self.timestamp+self.seq,'webhookEventId':f'scenario-{self.seq}',
            'source':{'type':'user','userId':self.raw},'message':{'type':'text','text':text}}
        body=json.dumps({'destination':'scenario-channel','events':[event]},ensure_ascii=False).encode()
        signature=base64.b64encode(hmac.new(b'synthetic-signature',body,hashlib.sha256).digest()).decode()
        return self.service.webhook(body,signature)

    def enroll(self,purpose='事業紹介'):
        self.post('follow')
        self.post(text='同意:'+purpose)

    def test_A_sns_request_research_plan_copy_audit_approval_pending(self):
        request=self.office.coordinator.submit('scenario-A',SAMPLE_BRIEF)
        self.assertIn('AI08',[t['agent'] for t in self.office.coordinator.report(request)['tasks']])
        campaign=self.office.content.generate('scenario-A-campaign',SAMPLE_BRIEF,start_date='2030-01-10')
        report=self.office.content.report(campaign)
        work=[r['agent'] for r in self.store.db.execute('SELECT agent FROM campaign_work WHERE campaign_id=? ORDER BY rowid',(campaign,))]
        self.assertLess(work.index('AI03'),work.index('AI08'))
        self.assertLess(work.index('AI08'),work.index('AI09'))
        self.assertLess(work.index('AI09'),work.index('AI28'))
        research=json.loads(self.store.db.execute("SELECT a.content FROM campaign_work w JOIN artifacts a ON a.task_id=w.task_id WHERE w.campaign_id=? AND w.agent='AI03'",(campaign,)).fetchone()[0])
        self.assertEqual(research['confirmed_facts'],[])
        self.assertTrue(research['hypotheses'])
        self.assertTrue(all(c['status']=='approval_pending' for c in report['contents']))
        self.assertEqual(report['cost']['actual_api_microusd'],0)

    def test_B_referral_terms_human_check_required_no_false_compliance(self):
        campaign=self.office.content.generate('scenario-B','紹介文を準備',start_date='2030-01-10')
        referral=next(c for c in self.office.content.report(campaign)['contents'] if c['kind']=='referral')
        self.office.content.confirm_check(referral['id'],'ベネシード公式規約','合成の規約参照、実際の規約確認ではない')
        with self.assertRaises(ValueError):
            self.office.content.decide(referral['id'],'approve','representative')
        self.assertEqual(self.office.content._latest(referral['id'])['status'],'approval_pending')
        self.assertIn('記載なし',json.dumps(referral['body'],ensure_ascii=False))

    def test_C_follow_explicit_consent_registration_seven_day_schedule(self):
        self.post('follow')
        self.assertFalse(self.service.eligible(self.subject,'健康'))
        self.post(text='同意:健康')
        self.assertTrue(self.service.eligible(self.subject,'健康'))
        self.assertFalse(self.service.eligible(self.subject,'美容'))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM ix_steps WHERE status='scheduled_mock'").fetchone()[0],7)
        self.assertEqual(self.adapter.calls,[])

    def test_D_inquiry_booking_approval_confirmation_pauses_delivery(self):
        self.enroll()
        self.post(text='問い合わせ（合成）')
        self.post(text='面談希望')
        self.service.add_slot(self.admin,'A','2030-01-10T01:00:00+00:00','2030-01-10T01:30:00+00:00')
        booking=self.service.request_booking(self.admin,'scenario-D',self.subject,'A')
        self.assertFalse(self.service._customer(self.subject)['booked'])
        self.service.decide(self.admin,booking['operation_id'],'approve')
        result=self.service.dispatch_mock(self.admin,booking['operation_id'])
        self.assertEqual(result['status'],'mock_succeeded')
        self.assertEqual(result['actual_external_calls'],0)
        self.assertFalse(self.service.eligible(self.subject,'事業紹介'))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM ix_steps WHERE status='scheduled_mock'").fetchone()[0],0)

    def test_E_stop_persists_and_prevents_old_approved_send(self):
        self.enroll()
        operation=self.store.db.execute("SELECT id FROM ix_operations WHERE purpose='事業紹介'").fetchone()[0]
        self.service.decide(self.admin,operation,'approve')
        self.post(text='停止')
        self.assertTrue(self.service._customer(self.subject)['stopped'])
        with self.assertRaises(ValueError):
            self.service.dispatch_mock(self.admin,operation)
        self.assertEqual(self.adapter.calls,[])
        self.store.close()
        self.store=Store(self.path)
        self.assertEqual(self.store.db.execute('SELECT stopped FROM ix_customers WHERE subject=?',(self.subject,)).fetchone()[0],1)

    def test_F_reconsent_only_one_purpose_and_new_history(self):
        self.enroll('健康')
        self.post(text='同意:美容')
        self.post(text='停止')
        old=self.store.db.execute('SELECT COUNT(*) FROM ix_consents').fetchone()[0]
        self.post(text='再同意:健康')
        self.assertTrue(self.service.eligible(self.subject,'健康'))
        self.assertFalse(self.service.eligible(self.subject,'美容'))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ix_consents').fetchone()[0],old+1)
        self.assertEqual(self.store.db.execute("SELECT DISTINCT purpose FROM ix_steps WHERE status='scheduled_mock'").fetchone()[0],'健康')

    def test_G_api_failure_bounded_retry_failure_and_dashboard_alert(self):
        self.enroll()
        operation=self.store.db.execute("SELECT id FROM ix_operations WHERE purpose='事業紹介'").fetchone()[0]
        self.adapter.outcomes=['retryable']*3
        self.service.decide(self.admin,operation,'approve')
        for i in range(3):
            if i:
                # Test clock advance, not a real request or sleep.
                self.store.db.execute('UPDATE ix_operations SET next_attempt=NULL WHERE id=?',(operation,))
            self.service.dispatch_mock(self.admin,operation)
        with self.assertRaises(ValueError):
            self.service.dispatch_mock(self.admin,operation)
        self.assertEqual(len(self.adapter.calls),3)
        self.assertTrue(any(a['label']=='モックAPI失敗/結果不明' for a in self.office.alerts()))
        self.office.add_user('scenario','representative','SyntheticScenarioPass!123')
        token,_=self.office.new_session('scenario','representative')
        status,_,body=OfficeApp(self.path,8765).handle('GET','/monitor',{'Host':'127.0.0.1:8765','Cookie':COOKIE+'='+token})
        self.assertEqual(status,200)
        self.assertIn('mock_not_accepted',body.decode())

    def test_H_unapproved_blocked_approved_mock_once_no_real_publication(self):
        campaign=self.office.content.generate('scenario-H',SAMPLE_BRIEF,start_date='2030-01-10')
        content=next(c for c in self.office.content.report(campaign)['contents'] if c['kind']=='sns')
        operation=self.service.propose_sns(self.admin,'scenario-H-op',content['id'],['asset_synthetic'])
        with self.assertRaises(ValueError):
            self.service.decide(self.admin,operation,'approve')
        with self.assertRaises(ValueError):
            self.service.dispatch_mock(self.admin,operation)
        for check in CHECKS:
            self.office.content.confirm_check(content['id'],check,'合成の確認記録・法令の実査ではない')
        self.office.content.decide(content['id'],'approve','representative')
        self.service.decide(self.admin,operation,'approve')
        self.assertEqual(self.service.dispatch_mock(self.admin,operation)['status'],'mock_succeeded')
        with self.assertRaises(ValueError):
            self.service.dispatch_mock(self.admin,operation)
        self.assertEqual(len(self.adapter.calls),1)
        self.assertEqual(self.office.content._latest(content['id'])['status'],'approved')
