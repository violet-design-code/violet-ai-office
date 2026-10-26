import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from violet_office.store import Store
from violet_office.content import ContentOffice, SAMPLE_BRIEF, CHECKS


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'office.db'
        self.store=Store(self.path)
        self.office=ContentOffice(self.store)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def generate(self, **kwargs):
        return self.office.generate('sample',SAMPLE_BRIEF,start_date='2026-10-26',**kwargs)

    def first(self, campaign):
        return self.office.report(campaign)['contents'][0]['id']

    def confirm_all(self, content):
        for check in CHECKS:
            self.office.confirm_check(content,check,'合成テスト用の確認記録・実審査ではない')

    def test_sample_runs_selected_roles_and_stops_before_approval(self):
        campaign=self.generate()
        report=self.office.report(campaign)
        self.assertEqual(len(report['contents']),16)
        self.assertEqual({r['agent'] for r in report['work']},{'AI01','AI03','AI08','AI09','AI10','AI16','AI22','AI28'})
        self.assertTrue(all(c['status']=='approval_pending' for c in report['contents']))
        self.assertEqual(report['cost']['actual_api_microusd'],0)
        self.assertTrue(all(s['status']!='確認済み' for s in report['sources']))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM approvals').fetchone()[0],0)

    def test_platform_and_duration(self):
        for platform in ('Instagram','Facebook','LINE'):
            for days in (7,14,30):
                campaign=self.office.generate(f'{platform}-{days}',SAMPLE_BRIEF,days,platform,'2026-10-26')
                posts=[c for c in self.office.report(campaign)['contents'] if c['kind']=='sns']
                self.assertEqual(len(posts),days)
                self.assertTrue(all(c['body']['platform']==platform for c in posts))
                self.assertTrue(all('cta' in c['body'] for c in posts))

    def test_idempotency_and_conflict(self):
        self.assertEqual(self.generate(),self.generate())
        with self.assertRaises(ValueError):
            self.generate(days=14)

    def test_rollback_is_retryable_without_duplicates(self):
        with patch.object(self.office,'audit',side_effect=ValueError('synthetic')):
            with self.assertRaises(ValueError):
                self.generate()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM campaigns').fetchone()[0],0)
        self.assertEqual(self.store.summary(),{})
        self.assertEqual(len(self.office.report(self.generate())['contents']),16)

    def test_unchecked_and_nonrepresentative_approval_blocked(self):
        content=self.first(self.generate())
        with self.assertRaises(ValueError):
            self.office.decide(content,'approve','AI01')
        with self.assertRaises(ValueError):
            self.office.decide(content,'approve','representative')
        with self.assertRaises(ValueError):
            self.office.record_publication(content,'合成記録','representative')

    def test_revision_invalidates_previous_approval(self):
        content=self.first(self.generate())
        self.confirm_all(content)
        self.office.decide(content,'approve','representative')
        new=self.office.revise(content,{'body':'新しい匿名の原稿'})
        with self.assertRaises(ValueError):
            self.office.record_publication(content,'合成記録','representative')
        with self.assertRaises(ValueError):
            self.office.decide(new,'approve','representative')
        self.office.audit(new)
        with self.assertRaises(ValueError):
            self.office.decide(new,'approve','representative')

    def test_dangerous_language_and_tampering(self):
        content=self.first(self.generate())
        new=self.office.revise(content,{'body':'必ず儲かる。病気が治る。'})
        self.office.audit(new)
        status=self.store.db.execute('SELECT status FROM content_versions WHERE id=?',(new,)).fetchone()[0]
        self.assertEqual(status,'changes_requested')
        with self.assertRaises(ValueError):
            self.office.decide(new,'approve','representative')

    def test_digest_tampering_rejected(self):
        content=self.first(self.generate())
        self.confirm_all(content)
        self.store.db.execute('UPDATE content_versions SET body=? WHERE id=?',(json.dumps({'body':'changed'}),content))
        with self.assertRaises(ValueError):
            self.office.decide(content,'approve','representative')

    def test_manual_publication_requires_approval_and_keeps_history(self):
        content=self.first(self.generate())
        self.confirm_all(content)
        self.office.decide(content,'approve','representative')
        self.office.record_publication(content,'合成テスト・実際の公開ではない','representative')
        events=[r[0] for r in self.store.db.execute('SELECT event FROM content_events WHERE content_id=? ORDER BY id',(content,))]
        self.assertIn('quality_checked',events)
        self.assertIn('approved',events)
        self.assertEqual(events[-1],'published_manual_record')

    def test_consent_purpose_stop_reconsent_booking_never_sends(self):
        o=self.office
        self.assertFalse(o.eligibility('subject_test','健康')['local_candidate'])
        o.set_preference('subject_test','健康',True,False,False,'合成同意記録1')
        self.assertTrue(o.eligibility('subject_test','健康')['local_candidate'])
        self.assertFalse(o.eligibility('subject_test','美容')['local_candidate'])
        o.set_preference('subject_test','健康',True,True,False,'合成停止記録')
        self.assertFalse(o.eligibility('subject_test','健康')['local_candidate'])
        o.set_preference('subject_test','健康',True,False,False,'合成再同意記録')
        self.assertTrue(o.eligibility('subject_test','健康')['local_candidate'])
        o.set_preference('subject_test','健康',True,False,True,'合成予約確定記録')
        self.assertFalse(o.eligibility('subject_test','健康')['local_candidate'])
        self.assertFalse(o.eligibility('subject_test','健康')['send_allowed'])

    def test_sensitive_input_rejected(self):
        for text in ('連絡先 a@example.com','キー sk-abcdefghi','電話 090-1234-5678'):
            with self.assertRaises(ValueError):
                self.office.generate('sensitive',text)

    def test_dashboard_escapes_content(self):
        content=self.first(self.generate())
        self.office.revise(content,{'body':'<script>alert(1)</script>'})
        page=self.office.dashboard()
        self.assertNotIn('<script>',page)
        self.assertIn('&lt;script&gt;',page)
        self.assertIn('承認待ち',page)

    def test_cli_sample_and_dashboard(self):
        def cli(*args):
            r=subprocess.run(['python3','-m','violet_office.content_cli',*args,'--db',str(self.path)],text=True,capture_output=True,check=True)
            return json.loads(r.stdout)
        campaign=cli('generate','--key','cli','--start-date','2026-10-26')['campaign_id']
        self.assertEqual(len(cli('report','--campaign-id',campaign)['contents']),16)
        path=Path(self.temp.name)/'dashboard.html'
        cli('dashboard','--output',str(path))
        self.assertIn('バイオレット',path.read_text())

    def test_concurrent_generation_and_persistence(self):
        def generate(_):
            store=Store(self.path)
            try:
                return ContentOffice(store).generate('concurrent',SAMPLE_BRIEF,start_date='2026-10-26')
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids=list(pool.map(generate,range(2)))
        self.assertEqual(ids[0],ids[1])
        self.store.close()
        self.store=Store(self.path)
        self.office=ContentOffice(self.store)
        self.assertEqual(len(self.office.report(ids[0])['contents']),16)

    def test_sources_distinguish_reported_fact_from_hypothesis(self):
        campaign=self.generate()
        self.office.record_source(campaign,'https://www.caa.go.jp/','合成テストの資料記述','reported_fact','テスト資料・実査なし')
        self.office.record_source(campaign,'https://www.ppc.go.jp/','合成テストの仮説','hypothesis','テスト仮説')
        report=self.office.report(campaign)
        self.assertEqual({r['classification'] for r in report['recorded_research']},{'reported_fact','hypothesis'})
        with self.assertRaises(ValueError):
            self.office.record_source(campaign,'https://secret@example.com/','文章','reported_fact','資料')

    def test_reservation_suppresses_all_purposes_and_history_preserved(self):
        self.office.set_preference('subject_demo','美容',True,False,False,'合成同意')
        self.office.set_preference('subject_demo','健康',True,False,True,'合成予約')
        self.assertFalse(self.office.eligibility('subject_demo','美容')['local_candidate'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM preference_events').fetchone()[0],2)
