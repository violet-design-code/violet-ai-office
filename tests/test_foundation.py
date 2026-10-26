import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from violet_office.registry import DEFAULT_CONFIG, load_agents
from violet_office.store import ACTIONS, Store


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.sqlite3'
        self.store = Store(self.path, budget_microusd=100)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def task(self, key='sample', **kwargs):
        return self.store.create_task(key, 'AI09', {'brief': '匿名の原稿'}, **kwargs)

    def artifact(self, **kwargs):
        task = self.task(**kwargs)
        return task, self.store.finish(self.store.claim(task), '未確認の効能・収入を断定しない原稿')

    def test_registry(self):
        agents = load_agents()
        self.assertEqual(len(agents), 28)
        self.assertTrue(all(not a['enabled'] for a in agents.values()))

    def test_registry_rejects_external_permission(self):
        data = json.loads(DEFAULT_CONFIG.read_text())
        data['agents'][0]['permissions']['external_send'] = True
        path = Path(self.temp.name) / 'bad.json'
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            load_agents(path)

    def test_idempotent_and_conflicting_task(self):
        self.assertEqual(self.task(), self.task())
        with self.assertRaises(ValueError):
            self.store.create_task('sample', 'AI09', {'different': True})

    def test_concurrent_claim_has_one_winner(self):
        task = self.task()
        def claim(_):
            store = Store(self.path)
            try:
                try:
                    store.claim(task)
                    return True
                except ValueError:
                    return False
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(claim, range(2))), 1)

    def test_review_is_independent(self):
        task, artifact = self.artifact()
        with self.assertRaises(ValueError):
            self.store.review(artifact, 'AI09', 'pass')
        self.store.review(artifact, 'AI28', 'pass')
        self.assertEqual(self.store.summary(), {'completed': 1})
        with self.assertRaises(ValueError):
            self.store.review(artifact, 'AI28', 'pass')

    def test_every_external_action_requires_review_and_approval(self):
        for action in ACTIONS - {'internal'}:
            with self.subTest(action=action):
                task, artifact = self.artifact(key=action, action=action)
                with self.assertRaises(ValueError):
                    self.store.decide(artifact, 'approve', 'representative')
                self.store.review(artifact, 'AI28', 'pass')
                with self.assertRaises(ValueError):
                    self.store.decide(artifact, 'approve', 'AI01')
                self.store.decide(artifact, 'approve', 'representative')
                status = self.store.db.execute('SELECT status FROM tasks WHERE id=?', (task,)).fetchone()[0]
                self.assertEqual(status, 'manual_execution_pending')
                with self.assertRaises(ValueError):
                    self.store.claim(task)

    def test_rejection(self):
        task, artifact = self.artifact(action='line_send')
        self.store.review(artifact, 'AI28', 'pass')
        self.store.decide(artifact, 'reject', 'representative')
        self.assertEqual(self.store.summary(), {'rejected': 1})

    def test_review_changes_blocks_approval(self):
        _, artifact = self.artifact(action='line_send')
        self.store.review(artifact, 'AI28', 'changes_requested')
        with self.assertRaises(ValueError):
            self.store.decide(artifact, 'approve', 'representative')

    def test_retry_limit_and_safe_error_codes(self):
        task = self.task(max_attempts=2)
        run = self.store.claim(task)
        with self.assertRaises(ValueError):
            self.store.fail(run, 'secret provider response')
        self.store.fail(run, 'rate_limit', retryable=True)
        self.store.fail(self.store.claim(task), 'unavailable', retryable=True)
        self.assertEqual(self.store.summary(), {'failed': 1})
        with self.assertRaises(ValueError):
            self.store.claim(task)

    def test_timeout_and_late_result(self):
        task = self.task()
        run = self.store.claim(task)
        self.store.db.execute("UPDATE runs SET deadline='2000-01-01T00:00:00+00:00' WHERE id=?", (run,))
        with self.assertRaises(ValueError):
            self.store.finish(run, 'late')
        self.assertEqual(self.store.expire(), 1)
        with self.assertRaises(ValueError):
            self.store.finish(run, 'stale worker')
        self.assertNotEqual(run, self.store.claim(task))

    def test_budget_reservations_persist_and_failed_runs_remain_charged(self):
        task = self.task()
        with self.assertRaises(ValueError):
            self.store.claim(task, 101)
        self.assertEqual(self.store.summary(), {'pending': 1})
        run = self.store.claim(task, 70)
        self.store.fail(run, 'timeout', retryable=True)
        with self.assertRaises(ValueError):
            self.store.claim(task, 31)
        self.store.close()
        self.store = Store(self.path, budget_microusd=10000)
        with self.assertRaises(ValueError):
            self.store.claim(task, 31)

    def test_invalid_limits_and_task_types(self):
        for kwargs in ({'max_attempts':0}, {'timeout_seconds':301}, {'action':'send_anything'}):
            with self.assertRaises(ValueError):
                self.task(**kwargs)

    def test_artifacts_and_audit_survive_reopen(self):
        task, artifact = self.artifact()
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.db.execute('SELECT author FROM artifacts WHERE id=?',(artifact,)).fetchone()[0], 'AI09')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM events WHERE task_id=?',(task,)).fetchone()[0], 3)


if __name__ == '__main__':
    unittest.main()
