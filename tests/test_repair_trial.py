"""Synthetic reproduction of the Windows observations; never a Windows DB."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

from violet_office.store import Store
from violet_office.office import Office
from violet_office.integrations.adapters import Principal
from violet_office.business_foundation import ai_allowed, require_work
from violet_office.business_management_views import employees, confirm
from violet_office.repair_trial import trial, snapshot, paths


@contextmanager
def database(path):
    db = sqlite3.connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()


class RepairTrialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/'validation.sqlite3'
        self.outdir = self.root/'repair-trials'
        self.outdir.mkdir()
        self.output = self.outdir/'trial-one.sqlite3'
        self.registry = Path('config/agents.json').read_bytes()
        st = Store(self.source)
        o = Office(st)
        owner = Principal('representative','owner')
        o.add_user('owner','representative','SyntheticOwnerPass!123')
        o.portfolio.create(owner,'BIZ-TEST','合成事業','other',services='合成サービス')
        task = o.portfolio.task(owner,'BIZ-TEST','legacy','referral','合成業務',agent='AI17')
        self.task = task
        st.db.execute('PRAGMA foreign_keys=OFF')
        for agent in st.agents:
            perms = ['business_read','work'] if agent != 'AI28' else ['business_read']
            st.db.execute('INSERT INTO bf_ai_assignments VALUES(?,?,?,?,?,?,1)',
                          ('BIZ-TEST',agent,'合成担当','合成範囲',json.dumps(perms),int(agent != 'AI02')))
        st.close()
        self.source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def tearDown(self):
        self.assertEqual(self.registry,Path('config/agents.json').read_bytes())
        self.temp.cleanup()

    def repair(self):
        return trial(self.source,self.output,'代表が確認する合成分類')

    def test_exact_one_row_all_tables_schema_and_source_unchanged(self):
        with database(self.source) as db:
            schema,before = snapshot(db)
        report=self.repair()
        self.assertEqual(report['foreign_key_errors_before'],28)
        self.assertEqual(report['foreign_key_errors_after'],0)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),self.source_hash)
        with database(self.output) as db:
            after_schema,after=snapshot(db)
            self.assertEqual(schema,after_schema)
            self.assertEqual(len(after['bf_profiles']),1)
            for table in before:
                if table != 'bf_profiles': self.assertEqual(before[table],after[table],table)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(),('ok',))
        self.assertTrue(any(r['agent']=='AI28' and r['role']=='reviewer' for r in report['blocked_pending_work_or_review']))
        self.assertTrue(any(r['agent']=='AI02' and r['permission']=='work' and not r['after'] for r in report['permission_changes']))

    def test_permissions_business_separation_and_existing_work(self):
        self.repair()
        with database(self.output) as db:
            db.row_factory=sqlite3.Row
            from violet_office.registry import load_agents
            st=SimpleNamespace(db=db,agents=load_agents())
            require_work(st,'AI17','BIZ-TEST')
            for agent in ('AI02','AI28'):
                with self.assertRaises(PermissionError): require_work(st,agent,'BIZ-TEST')
            self.assertFalse(ai_allowed(st,'AI17','BIZ-TEST','finance_read'))
            self.assertFalse(ai_allowed(st,'AI17','BIZ-TEST','external_send'))
            self.assertFalse(ai_allowed(st,'AI29','BIZ-TEST','work'))
            # A second synthetic foundation grants no inherited assignment rights.
            db.execute("INSERT INTO portfolio_businesses SELECT 'BIZ-OTHER',name,kind,status,services,pricing,audience,region,offering_confirmed,priority,revision,created_at FROM portfolio_businesses")
            db.execute("INSERT INTO bf_profiles VALUES('BIZ-OTHER','合成','合成',NULL,1,1,1)")
            self.assertFalse(ai_allowed(st,'AI17','BIZ-OTHER','work'))
            self.assertEqual(db.execute('SELECT agent FROM tasks WHERE id=?',(self.task,)).fetchone()[0],'AI17')

    def test_repaired_assignment_form_confirmation_and_permission_reduction(self):
        self.repair()
        st=Store(self.output)
        try:
            o=Office(st); owner=Principal('representative','owner')
            session={'role':'representative','username':'owner','csrf':'synthetic'}
            body=employees(o,session,{'business':['BIZ-TEST']})
            self.assertEqual(body.count('担当変更を確認'),28)
            self.assertNotIn('先に事業管理画面',body)
            for agent in st.agents: self.assertIn(agent,body)
            before=st.db.execute('SELECT COUNT(*) FROM br_requests').fetchone()[0]
            confirm(o,session,{'key':'trial-confirm','business':'BIZ-TEST','kind':'assign',
                    'snapshot':o.readiness.snapshot('BIZ-TEST'),'agent':'AI17','role_note':'合成変更',
                    'scope_note':'当該事業のみ','active':'1','business_read':'1'})
            self.assertEqual(st.db.execute('SELECT COUNT(*) FROM br_requests').fetchone()[0],before+1)
            request=st.db.execute("SELECT id FROM br_requests WHERE entry_key='trial-confirm'").fetchone()[0]
            self.assertTrue(ai_allowed(st,'AI17','BIZ-TEST','work'))
            o.readiness.decide(owner,request,'approve',True)
            o.readiness.apply(owner,request)
            self.assertFalse(ai_allowed(st,'AI17','BIZ-TEST','work'))
            self.assertTrue(ai_allowed(st,'AI17','BIZ-TEST','business_read'))
            self.assertEqual(st.db.execute('SELECT COUNT(*) FROM bf_ai_assignments').fetchone()[0],28)
        finally: st.close()

    def test_path_guards_no_overwrite_live_backup_missing_alias(self):
        self.repair()
        for src,out in ((self.source,self.output),(self.root/'office.sqlite3',self.outdir/'trial-x.sqlite3'),
                        (self.root/'backup.sqlite3',self.outdir/'trial-x.sqlite3'),
                        (self.source,self.root/'office.sqlite3'),(self.source,self.source),
                        (self.root/'missing.sqlite3',self.outdir/'trial-x.sqlite3')):
            with self.subTest(src=src,out=out),self.assertRaises(ValueError): paths(src,out)
        alias=self.outdir/'trial-alias.sqlite3'
        alias.hardlink_to(self.source)
        with self.assertRaises(ValueError):trial(self.source,alias,'合成')
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),self.source_hash)

    def test_invalid_category_and_unexpected_data_refused_without_output(self):
        for category in ('',' ','x'*121,'token=secretvalue'):
            with self.subTest(category=category),self.assertRaises(ValueError):trial(self.source,self.output,category)
            self.assertFalse(self.output.exists())
        with database(self.source) as db:
            db.execute("DELETE FROM bf_ai_assignments WHERE agent='AI01'")
        with self.assertRaises(ValueError): self.repair()
        self.assertFalse(self.output.exists())

    def test_already_repaired_input_refused(self):
        self.repair()
        with self.assertRaises(ValueError):trial(self.output,self.outdir/'trial-two.sqlite3','合成')
        self.assertFalse((self.outdir/'trial-two.sqlite3').exists())

    def test_wal_and_symlink_to_live_input_refused(self):
        wal=Path(str(self.source)+'-wal')
        wal.write_bytes(b'not-a-standalone-copy')
        with self.assertRaises(ValueError):self.repair()
        wal.unlink()
        live=self.root/'office.sqlite3'
        live.write_bytes(self.source.read_bytes())
        alias=self.root/'validation-link.sqlite3'
        try: alias.symlink_to(live)
        except OSError: self.skipTest('Symlink privilege unavailable')
        with self.assertRaises(ValueError):trial(alias,self.output,'合成')
        self.assertFalse(self.output.exists())

    def test_unexpected_trigger_data_change_refused(self):
        with database(self.source) as db:
            db.execute("CREATE TRIGGER unexpected AFTER INSERT ON bf_profiles BEGIN UPDATE portfolio_businesses SET services='unexpected'; END")
        with self.assertRaisesRegex(ValueError,'Unexpected schema/data change'):self.repair()
        self.assertFalse(self.output.exists())

    def test_existing_pending_task_runs_but_unpermitted_reviewer_is_blocked(self):
        self.repair()
        st=Store(self.output)
        try:
            Office(st)
            run=st.claim(self.task)
            artifact=st.finish(run,'合成の中立成果物')
            with self.assertRaises(PermissionError):st.review(artifact,'AI28','pass')
            self.assertEqual(st.db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0],0)
        finally:st.close()
