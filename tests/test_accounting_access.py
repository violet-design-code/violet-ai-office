"""Opt-in accounting policies on disposable synthetic databases only."""
from collections import Counter
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

from violet_office.accounting_access import AccountingAccess, initialize_sandbox, ROLES, FIELDS
from violet_office.integrations.adapters import Principal
from violet_office.office import Office
from violet_office.store import Store, now


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.st = Store(Path(self.temp.name)/'synthetic.sqlite3')
        self.o = Office(self.st)
        self.o.add_user('owner', 'representative', 'SyntheticOwnerPass!123')
        self.owner = Principal('representative', 'owner')
        self.o.add_user('viewer', 'viewer', 'SyntheticViewerPass!123')
        self.registry = Path('config/agents.json').read_bytes()
        self.start, self.end = '2026-01-01', '2026-02-01'
        self.expires = (now()+timedelta(days=1)).isoformat()
        self.tasks = {}
        for b in ('BIZ-A', 'BIZ-B'):
            self.o.portfolio.create(self.owner,b,'合成'+b,'other',services='合成')
            self.o.business_foundation.adopt(self.owner,b,'合成')
            for agent in self.st.agents:
                self.o.business_foundation.assign(self.owner,b,agent,'合成業務','合成範囲',['business_read','work'])
                t=self.st.create_task(b+agent,agent,{'instruction':'synthetic'})
                self.st.db.execute('INSERT INTO portfolio_tasks VALUES(?,?,?,?,?,?)',
                                   (b,t,'analysis','synthetic',3,now().isoformat()))
                self.tasks[b,agent]=t
            self.o.portfolio.ledger(self.owner,b,'revenue','2026-01-05','revenue',100,'actual','合成秘密明細本文')
            self.o.portfolio.ledger(self.owner,b,'expense','2026-01-06','expense',30,'actual','合成秘密明細本文')
        self.old = self.snapshot()
        initialize_sandbox(self.o,self.owner)
        self.s = AccountingAccess(self.o)

    def snapshot(self):
        return {t:Counter(tuple(r) for r in self.st.db.execute('SELECT * FROM '+t)) for t in
                ('bf_ai_assignments','bf_profiles','portfolio_businesses','portfolio_ledger')}

    def tearDown(self):
        self.assertEqual(self.registry,Path('config/agents.json').read_bytes())
        self.st.close()
        self.temp.cleanup()

    def policy(self,agent='AI27',section='detail',fields=None,b='BIZ-A',approve=True):
        fields=fields or sorted(FIELDS[section])
        p=self.s.propose(self.owner,b,agent,section,fields,self.start,self.end,'合成の利用目的',self.expires)
        if approve:self.s.decide(self.owner,p,True)
        return p

    def read(self,p,agent='AI27',section='detail',fields=None,b='BIZ-A',**kw):
        return self.s.read(self.owner,self.tasks[b,agent],p,section,fields or sorted(FIELDS[section]),
                           kw.get('start',self.start),kw.get('end',self.end))

    def test_default_off_and_initialization_idempotent(self):
        initialize_sandbox(self.o,self.owner)
        self.assertEqual(self.old,self.snapshot())
        for a in self.st.agents:
            with self.subTest(a=a),self.assertRaises(PermissionError):self.read('missing',agent=a)
        with self.assertRaises(PermissionError):initialize_sandbox(self.o,Principal('viewer','viewer'))

    def test_detail_allowlist_and_no_sensitive_text(self):
        p=self.policy()
        result=self.read(p)
        self.assertEqual(result['data'],[{'amount_yen':100,'day':'2026-01-05','kind':'revenue'},
                                         {'amount_yen':30,'day':'2026-01-06','kind':'expense'}])
        self.assertNotIn('合成秘密明細本文',json.dumps(result,ensure_ascii=False))
        for f in ('evidence','id','customer','attachment','history','log'):
            with self.subTest(f=f),self.assertRaises(PermissionError):self.read(p,fields=[f])
        self.assertEqual(self.old,self.snapshot())

    def test_all_28_role_matrix(self):
        for agent in self.st.agents:
            for section,fields in FIELDS.items():
                with self.subTest(agent=agent,section=section):
                    if section in ROLES.get(agent,set()):
                        p=self.policy(agent,section)
                        if section in ('terms','advertising'):
                            for f in fields:self.s.approved_source(self.owner,'BIZ-A',section,'2026-01-05',f,10)
                        result=self.read(p,agent,section)
                        self.assertEqual(result['business_id'],'BIZ-A')
                    else:
                        with self.assertRaises(PermissionError):self.policy(agent,section)

    def test_pending_rejected_expired_revoked_and_demoted_approver(self):
        p=self.policy(approve=False)
        with self.assertRaises(PermissionError):self.read(p)
        self.s.decide(self.owner,p,False)
        with self.assertRaises(PermissionError):self.read(p)
        p=self.policy()
        self.st.db.execute('UPDATE acct_policies SET expires_at=? WHERE id=?',((now()-timedelta(seconds=1)).isoformat(),p))
        with self.assertRaises(PermissionError):self.read(p)
        p=self.policy();self.s.revoke(self.owner,p)
        with self.assertRaises(PermissionError):self.read(p)
        p=self.policy()
        self.st.db.execute("UPDATE office_users SET role='viewer' WHERE username='owner'")
        with self.assertRaises(PermissionError):self.read(p)

    def test_period_fields_task_agent_business_and_identity(self):
        p=self.policy(fields=['amount_yen'])
        with self.assertRaises(PermissionError):self.read(p,fields=['day'])
        with self.assertRaises(PermissionError):self.read(p,start='2025-12-01')
        with self.assertRaises(PermissionError):self.read(p,b='BIZ-B')
        with self.assertRaises(PermissionError):self.read(p,agent='AI02')
        with self.assertRaises(PermissionError):self.s.read(Principal('representative','viewer'),self.tasks['BIZ-A','AI27'],p,'detail',['amount_yen'],self.start,self.end)
        with self.assertRaises(PermissionError):self.s.decide(Principal('viewer','viewer'),p,True)

    def test_inactive_assignment_paused_task_disabled_profile(self):
        p=self.policy();t=self.tasks['BIZ-A','AI27']
        self.st.db.execute("UPDATE bf_ai_assignments SET active=0 WHERE business_id='BIZ-A' AND agent='AI27'")
        with self.assertRaises(PermissionError):self.read(p)
        self.st.db.execute("UPDATE bf_ai_assignments SET active=1 WHERE business_id='BIZ-A' AND agent='AI27'")
        self.st.db.execute("UPDATE tasks SET status='paused' WHERE id=?",(t,))
        with self.assertRaises(PermissionError):self.read(p)
        self.st.db.execute("UPDATE tasks SET status='pending' WHERE id=?",(t,))
        self.st.db.execute("UPDATE bf_profiles SET enabled=0 WHERE business_id='BIZ-A'")
        with self.assertRaises(PermissionError):self.read(p)

    def test_handoff_rechecks_source_recipient_and_business(self):
        p=self.policy();t=self.tasks['BIZ-A','AI27']
        h=self.s.issue(self.owner,t,p,'detail',['amount_yen'],self.start,self.end)
        summary=self.policy('AI02','summary')
        with self.assertRaises(PermissionError):self.s.receive(self.owner,h,self.tasks['BIZ-A','AI02'],summary)
        audit=self.policy('AI28','audit')
        with self.assertRaises(PermissionError):self.s.receive(self.owner,h,self.tasks['BIZ-A','AI28'],audit)
        other=self.policy(b='BIZ-B')
        with self.assertRaises(PermissionError):self.s.receive(self.owner,h,self.tasks['BIZ-B','AI27'],other)
        self.assertEqual(self.s.receive(self.owner,h,t,p)['data'],[{'amount_yen':100},{'amount_yen':30}])
        self.s.revoke(self.owner,p)
        with self.assertRaises(PermissionError):self.s.receive(self.owner,h,t,p)
        # Dedicated handles and events contain no financial values or arbitrary content.
        for table in ('acct_handles','acct_events'):
            body=json.dumps([dict(r) for r in self.st.db.execute('SELECT * FROM '+table)],ensure_ascii=False)
            self.assertNotIn('合成秘密明細本文',body)
            self.assertNotIn('amount_yen": 100',body)

    def test_unknown_sources_and_unapproved_terms_are_not_inferred(self):
        p=self.policy('AI07','advertising')
        with self.assertRaises(ValueError):self.read(p,'AI07','advertising')
        with self.assertRaises(ValueError):self.s.approved_source(self.owner,'BIZ-A','advertising','2026-01-05','unknown',10)
        with self.assertRaises(ValueError):self.s.approved_source(self.owner,'BIZ-A','terms','2026-01-05','rate_percent',101)
        with self.assertRaises(ValueError):self.policy(fields=['evidence'])

    def test_existing_summary_kpi_report_and_tasks_continue(self):
        before=self.o.business_operations.summary(self.owner,'BIZ-A',self.start,self.end)
        p=self.policy('AI02','summary')
        result=self.read(p,'AI02','summary')
        self.assertEqual(result['data'],{k:before[k] for k in FIELDS['summary']})
        self.assertTrue(self.o.business_operations.kpis(self.owner,'BIZ-A',self.start,self.end))
        report=self.o.portfolio.report(self.owner,self.start,self.end)
        self.o.portfolio.decide_report(self.owner,report,'approve')
        run=self.st.claim(self.tasks['BIZ-A','AI27'])
        self.assertTrue(run)
        self.assertEqual(self.old,self.snapshot())
        self.assertNotIn('finance_read',json.loads(self.st.db.execute("SELECT permissions FROM bf_ai_assignments LIMIT 1").fetchone()[0]))
