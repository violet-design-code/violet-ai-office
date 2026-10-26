import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from violet_office.store import Store
from violet_office.coordinator import Coordinator


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'office.db'
        self.store = Store(self.path)
        self.c = Coordinator(self.store)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def submit(self, instruction='SNS投稿文とLINEの7日間配信を準備', key='example'):
        return self.c.submit(key, instruction)

    def complete_specialists(self, request):
        for task in self.c.report(request)['tasks']:
            if task['kind']=='specialist':
                artifact = self.store.finish(self.store.claim(task['id']),task['agent']+'の匿名化成果物')
                self.store.review(artifact,self.store.agents[task['agent']]['reviewer'],'pass')

    def test_japanese_selection_with_reasons(self):
        plan = self.c.analyze('SNS投稿文とLINEの7日間配信を準備')
        self.assertEqual([a['agent'] for a in plan['assignments']],['AI08','AI09','AI21','AI22'])
        self.assertEqual(plan['assignments'][0]['reason'],['SNS'])
        self.assertEqual(self.c.analyze('kpiレポート')['assignments'][0]['agent'],'AI27')

    def test_unknown_requires_clarification_without_tasks(self):
        request = self.submit('よろしくお願いします')
        self.assertEqual(self.c.report(request)['status'],'clarification_required')
        self.assertEqual(self.c.report(request)['tasks'],[])
        with self.assertRaises(ValueError):
            self.c.collect(request)

    def test_invalid_input(self):
        for text in ('', ' ', 'x'*4001, None):
            with self.assertRaises(ValueError):
                self.c.analyze(text)

    def test_idempotency_and_conflict(self):
        request = self.submit()
        self.assertEqual(self.submit(), request)
        self.assertEqual(len(self.c.report(request)['tasks']),5)
        with self.assertRaises(ValueError):
            self.submit('競合調査')

    def test_creation_rolls_back_all_children(self):
        with patch.object(self.store,'create_task',side_effect=ValueError('failure')):
            with self.assertRaises(ValueError):
                self.submit()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM requests').fetchone()[0],0)
        self.assertEqual(self.store.summary(),{})

    def test_dependency_gate_cannot_be_bypassed_by_claim(self):
        request = self.submit()
        aggregate = next(t for t in self.c.report(request)['tasks'] if t['kind']=='aggregate')
        with self.assertRaises(ValueError):
            self.store.claim(aggregate['id'])
        with self.assertRaises(ValueError):
            self.c.collect(request)

    def test_reviewed_collection_approval_and_reopen(self):
        request = self.submit()
        self.complete_specialists(request)
        self.assertEqual(self.c.report(request)['status'],'ready_to_collect')
        artifact = self.c.collect(request)
        self.assertEqual(self.c.collect(request),artifact)
        report = self.c.report(request)
        aggregate = next(t for t in report['tasks'] if t['kind']=='aggregate')
        content = json.loads(aggregate['content'])
        self.assertEqual(len(content['items']),4)
        self.assertTrue(all(item['artifact_id'] for item in content['items']))
        with self.assertRaises(ValueError):
            self.store.decide(artifact,'approve','representative')
        self.store.review(artifact,'AI28','pass')
        self.store.decide(artifact,'approve','representative')
        self.assertEqual(self.c.report(request)['status'],'manual_execution_pending')
        self.store.close()
        self.store=Store(self.path)
        self.c=Coordinator(self.store)
        self.assertEqual(self.c.collect(request),artifact)

    def test_unreviewed_result_blocks_collection(self):
        request = self.submit('市場調査')
        task = next(t for t in self.c.report(request)['tasks'] if t['kind']=='specialist')
        self.store.finish(self.store.claim(task['id']),'調査結果')
        with self.assertRaises(ValueError):
            self.c.collect(request)

    def test_failed_or_rejected_child_blocks_collection(self):
        request=self.submit('紹介文')
        self.complete_specialists(request)
        child=next(t for t in self.c.report(request)['tasks'] if t['kind']=='specialist')
        self.store.decide(child['artifact_id'],'reject','representative')
        self.assertEqual(self.c.report(request)['status'],'blocked')
        with self.assertRaises(ValueError):
            self.c.collect(request)

    def test_internal_collection_requires_aggregate_review(self):
        request=self.submit('競合調査と市場調査')
        self.complete_specialists(request)
        artifact=self.c.collect(request)
        self.assertEqual(self.c.report(request)['status'],'review_pending')
        self.store.review(artifact,'AI28','pass')
        self.assertEqual(self.c.report(request)['status'],'collected')

    def test_concurrent_submit_has_one_request(self):
        def submit(_):
            store=Store(self.path)
            try:
                return Coordinator(store).submit('concurrent','SNS企画')
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids=list(pool.map(submit,range(2)))
        self.assertEqual(ids[0],ids[1])
        self.assertEqual(len(self.c.report(ids[0])['tasks']),2)

    def test_original_tasks_survive_schema_additions(self):
        task=self.store.create_task('legacy','AI09',{'brief':'old'})
        self.store.close()
        self.store=Store(self.path)
        self.c=Coordinator(self.store)
        self.submit()
        self.assertIsNotNone(self.store.claim(task))

    def test_priority_queue_and_invalid_priority(self):
        low=self.c.submit('low','市場調査',priority=5)
        high=self.c.submit('high','競合調査',priority=1)
        queue=self.c.queue()
        self.assertEqual([t['request_id'] for t in queue],[high,low])
        self.assertTrue(all(t['agent']!='AI01' for t in queue))
        with self.assertRaises(ValueError):
            self.c.submit('low','市場調査',priority=1)
        for priority in (0,6,True):
            with self.assertRaises(ValueError):
                self.c.submit('invalid','市場調査',priority=priority)

    def test_important_decision_requires_representative(self):
        request=self.submit('経営戦略')
        self.complete_specialists(request)
        specialist=next(t for t in self.c.report(request)['tasks'] if t['kind']=='specialist')
        self.assertEqual(specialist['action'],'management_decision')
        self.assertEqual(specialist['status'],'approval_pending')
        aggregate=self.c.collect(request)
        self.store.review(aggregate,'AI28','pass')
        self.assertEqual(self.c.report(request)['status'],'approval_pending')

    def test_failed_worker_is_reported_in_japanese(self):
        request=self.submit('市場調査')
        task=self.c.queue()[0]['id']
        self.store.fail(self.store.claim(task),'internal')
        report=self.c.report(request)
        self.assertEqual(report['status'],'blocked')
        self.assertIn('対応が必要',report['report_ja'])

    def test_cli_full_workflow(self):
        import subprocess
        def cli(*args):
            result=subprocess.run(['python3','-m','violet_office',*args,'--db',str(self.path)],capture_output=True,text=True,check=True)
            return json.loads(result.stdout)
        request=cli('submit','--key','cli','--instruction','市場調査','--priority','2')['request_id']
        task=next(t for t in cli('request','--request-id',request)['tasks'] if t['kind']=='specialist')
        file=Path(self.temp.name)/'result.txt'
        file.write_text('匿名資料に基づく調査結果',encoding='utf-8')
        artifact=cli('result','--task-id',task['id'],'--content-file',str(file))['artifact_id']
        cli('review','--artifact-id',artifact,'--reviewer','AI28','--verdict','pass')
        aggregate=cli('collect','--request-id',request)['artifact_id']
        cli('review','--artifact-id',aggregate,'--reviewer','AI28','--verdict','pass')
        self.assertEqual(cli('request','--request-id',request)['status'],'collected')

    def test_child_rejection_after_collection_blocks_aggregate_approval(self):
        request=self.submit('紹介文')
        self.complete_specialists(request)
        aggregate=self.c.collect(request)
        self.store.review(aggregate,'AI28','pass')
        child=next(t for t in self.c.report(request)['tasks'] if t['kind']=='specialist')
        self.store.decide(child['artifact_id'],'reject','representative')
        with self.assertRaises(ValueError):
            self.store.decide(aggregate,'approve','representative')
