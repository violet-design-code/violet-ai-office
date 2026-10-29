"""Rollout design experiments on temporary DBs only. No application migration/CLI.

The candidate installer below belongs ONLY to this test module. It is not imported
by Office, not deployed, and deliberately refuses unknown schema profiles.
"""
from contextlib import closing
import json
import sqlite3
import unittest
from tests import test_accounting_access as fixtures
from tests import test_business_operations as operations_fixtures
from tests import test_legacy_accounting_enforcement as connected_fixtures
from violet_office.accounting_access import SCHEMA
from violet_office.legacy_accounting import LegacyAccounting
from violet_office.repair_trial import snapshot
from violet_office.web import OfficeApp

TABLES=('acct_task_policies','acct_task_reads')
# Preserve the three-column insertion contracts; tighten nullability and business FK.
CANDIDATE=(
    '''CREATE TABLE IF NOT EXISTS acct_task_policies(
       task_id TEXT NOT NULL REFERENCES tasks(id),
       policy_id TEXT NOT NULL REFERENCES acct_policies(id),
       approved_by TEXT NOT NULL REFERENCES office_users(username),
       PRIMARY KEY(task_id,policy_id))''',
    '''CREATE TABLE IF NOT EXISTS acct_task_reads(
       task_id TEXT NOT NULL PRIMARY KEY REFERENCES tasks(id),
       business_id TEXT NOT NULL REFERENCES portfolio_businesses(id),
       policy_id TEXT NOT NULL REFERENCES acct_policies(id))''',
    'CREATE INDEX IF NOT EXISTS acct_task_policies_policy_idx ON acct_task_policies(policy_id)',
    'CREATE INDEX IF NOT EXISTS acct_task_policies_approver_idx ON acct_task_policies(approved_by)',
    'CREATE INDEX IF NOT EXISTS acct_task_reads_business_idx ON acct_task_reads(business_id)',
    'CREATE INDEX IF NOT EXISTS acct_task_reads_policy_idx ON acct_task_reads(policy_id)',
)


def signature(db,table):
    indexes=[]
    for row in db.execute('PRAGMA index_list('+table+')'):
        indexes.append((row[1],row[2],row[3],row[4],tuple(tuple(r) for r in db.execute('PRAGMA index_info("'+row[1]+'")'))))
    sql_profile=tuple((r[0],None if r[1] is None else ' '.join(r[1].split()).casefold()) for r in
                      db.execute("SELECT name,sql FROM sqlite_master WHERE tbl_name=? AND type IN ('table','index') ORDER BY name",(table,)))
    return (sql_profile,tuple(tuple(r) for r in db.execute('PRAGMA table_info('+table+')')),
            tuple(sorted(tuple(r) for r in db.execute('PRAGMA foreign_key_list('+table+')'))),
            tuple(sorted(indexes)),
            tuple(tuple(r) for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=? ORDER BY name",(table,))))


def expected_profiles():
    with closing(sqlite3.connect(':memory:')) as db:
        db.executescript('CREATE TABLE tasks(id TEXT PRIMARY KEY); CREATE TABLE acct_policies(id TEXT PRIMARY KEY); CREATE TABLE office_users(username TEXT PRIMARY KEY); CREATE TABLE portfolio_businesses(id TEXT PRIMARY KEY);')
        for statement in CANDIDATE:db.execute(statement)
        return {t:signature(db,t) for t in TABLES}


def install_candidate_synthetic_only(db,fail_after=None):
    """Experimental DDL transaction, not a production installer or approval."""
    if db.in_transaction or db.execute('PRAGMA foreign_keys').fetchone()[0]!=1:
        raise ValueError('Independent transaction and enabled foreign keys required')
    expected=expected_profiles()
    db.execute('BEGIN IMMEDIATE')
    try:
        known={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required={'tasks','portfolio_businesses','office_users','acct_policies','acct_sources','acct_handles','acct_events'}
        if not required<=known:raise ValueError('Parent schema prerequisites missing')
        if db.execute('PRAGMA integrity_check').fetchall()[0][0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Existing integrity/FK errors require separate review')
        for table in TABLES:
            if table in known and signature(db,table)!=expected[table]:
                raise ValueError('Unknown or weaker existing schema profile; do not rebuild automatically')
        for step,statement in enumerate(CANDIDATE,1):
            db.execute(statement)
            if step==fail_after:raise RuntimeError('Synthetic installation interruption')
        for table in TABLES:
            if signature(db,table)!=expected[table]:raise ValueError('Candidate schema verification failed')
        if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Candidate FK errors')
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK')
        raise


class RolloutSchemaTests(unittest.TestCase):
    setUp=fixtures.AccountingTests.setUp
    tearDown=fixtures.AccountingTests.tearDown
    snapshot=fixtures.AccountingTests.snapshot
    policy=fixtures.AccountingTests.policy

    def remove_new_tables(self):
        # Disposable fixture only, never an operational DB.
        for table in ('acct_task_reads','acct_task_policies'):self.st.db.execute('DROP TABLE '+table)

    def test_first_install_preserves_every_old_table_and_schema(self):
        self.remove_new_tables();before_schema,before=snapshot(self.st.db)
        install_candidate_synthetic_only(self.st.db)
        after_schema,after=snapshot(self.st.db)
        self.assertEqual(set(after)-set(before),set(TABLES))
        for table in before:self.assertEqual(before[table],after[table],table)
        for entry in before_schema:self.assertIn(entry,after_schema)
        self.assertEqual(self.st.db.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(self.st.db.execute('PRAGMA foreign_keys').fetchone()[0],1)

    def test_disabled_foreign_key_connection_is_refused_unchanged(self):
        self.remove_new_tables()
        with closing(sqlite3.connect(':memory:')) as db:
            self.st.db.backup(db)
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0],0)
            before=snapshot(db)
            with self.assertRaises(ValueError):install_candidate_synthetic_only(db)
            self.assertEqual(before,snapshot(db))

    def test_rerun_preserves_all_data_bindings_and_markers(self):
        self.remove_new_tables();install_candidate_synthetic_only(self.st.db)
        p=self.policy('AI27','detail');t=self.tasks['BIZ-A','AI27']
        self.s.bind_task(self.owner,t,p)
        self.s.read(self.owner,t,p,'detail',['amount_yen'],self.start,self.end)
        before=snapshot(self.st.db)
        install_candidate_synthetic_only(self.st.db)
        self.assertEqual(before,snapshot(self.st.db))

    def test_interruptions_roll_back_all_ddl_at_each_step(self):
        self.remove_new_tables();before=snapshot(self.st.db)
        for step in range(1,len(CANDIDATE)+1):
            with self.subTest(step=step),self.assertRaises(RuntimeError):install_candidate_synthetic_only(self.st.db,step)
            self.assertEqual(before,snapshot(self.st.db))
            self.assertFalse(self.st.db.in_transaction)
        install_candidate_synthetic_only(self.st.db)

    def test_current_loose_profile_is_not_silently_upgraded(self):
        # Current source schema is a different profile (nullable TEXT PK/no business FK/indexes).
        before=snapshot(self.st.db)
        with self.assertRaisesRegex(ValueError,'schema profile'):install_candidate_synthetic_only(self.st.db)
        self.assertEqual(before,snapshot(self.st.db))

    def test_missing_parent_and_wrong_index_collision_roll_back(self):
        self.remove_new_tables()
        self.st.db.execute('DROP TABLE acct_sources')
        before=snapshot(self.st.db)
        with self.assertRaises(ValueError):install_candidate_synthetic_only(self.st.db)
        self.assertEqual(before,snapshot(self.st.db))
        # Restore the empty parent in this fixture, then reserve a conflicting index name.
        statement=next(s for s in SCHEMA.split(';') if 'CREATE TABLE IF NOT EXISTS acct_sources' in s)
        self.st.db.execute(statement)
        self.st.db.execute('CREATE INDEX acct_task_reads_business_idx ON tasks(agent)')
        before=snapshot(self.st.db)
        with self.assertRaises(ValueError):install_candidate_synthetic_only(self.st.db)
        self.assertEqual(before,snapshot(self.st.db))

    def test_legacy_fk_errors_refused_without_hiding_or_repairing(self):
        self.remove_new_tables()
        # A deferred violation is sufficient to exercise preflight refusal, with FK ON.
        self.st.db.execute('CREATE TABLE synthetic_orphan(id REFERENCES tasks(id) DEFERRABLE INITIALLY DEFERRED)')
        # Use a second test-only DB image to seed a historical violation, as in existing fixtures.
        with closing(sqlite3.connect(':memory:')) as db:
            self.st.db.backup(db)
            db.execute("INSERT INTO synthetic_orphan VALUES('missing')");db.commit()
            db.execute('PRAGMA foreign_keys=ON')
            before=snapshot(db)
            with self.assertRaises(ValueError):install_candidate_synthetic_only(db)
            self.assertEqual(before,snapshot(db))
            self.assertEqual(len(db.execute('PRAGMA foreign_key_check').fetchall()),1)
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0],1)

    def test_duplicate_null_orphan_rows_are_rejected(self):
        self.remove_new_tables();install_candidate_synthetic_only(self.st.db)
        p=self.policy('AI27','detail');t=self.tasks['BIZ-A','AI27']
        self.st.db.execute('INSERT INTO acct_task_policies VALUES(?,?,?)',(t,p,'owner'))
        self.st.db.execute('INSERT INTO acct_task_reads VALUES(?,?,?)',(t,'BIZ-A',p))
        for sql,args in (
            ('INSERT INTO acct_task_policies VALUES(?,?,?)',(t,p,'owner')),
            ('INSERT INTO acct_task_reads VALUES(?,?,?)',(t,'BIZ-A',p)),
            ('INSERT INTO acct_task_reads VALUES(?,?,?)',(None,'BIZ-A',p)),
            ('INSERT INTO acct_task_reads VALUES(?,?,?)',(self.tasks['BIZ-B','AI27'],'missing',p)),
            ('INSERT INTO acct_task_policies VALUES(?,?,?)',('missing',p,'owner')),
            ('INSERT INTO acct_task_policies VALUES(?,?,?)',(self.tasks['BIZ-B','AI27'],'missing','owner')),
            ('INSERT INTO acct_task_policies VALUES(?,?,?)',(self.tasks['BIZ-B','AI27'],p,'missing'))):
            with self.subTest(args=args),self.assertRaises(sqlite3.IntegrityError):self.st.db.execute(sql,args)

    def test_old_marker_is_first_read_classification_not_current_policy(self):
        self.remove_new_tables();install_candidate_synthetic_only(self.st.db)
        t=self.tasks['BIZ-A','AI27'];first=self.policy('AI27','detail')
        self.s.read(self.owner,t,first,'detail',['amount_yen'],self.start,self.end)
        self.s.revoke(self.owner,first)
        second=self.policy('AI27','detail')
        self.s.read(self.owner,t,second,'detail',['amount_yen'],self.start,self.end)
        self.assertEqual(self.st.db.execute('SELECT policy_id FROM acct_task_reads WHERE task_id=?',(t,)).fetchone()[0],first)
        self.assertEqual(self.st.db.execute("SELECT COUNT(*) FROM acct_events WHERE action='read' AND policy_id=?",(second,)).fetchone()[0],1)

    def test_direct_sql_foreign_keys_do_not_replace_scope_authorization(self):
        self.remove_new_tables();install_candidate_synthetic_only(self.st.db)
        self.st.db.execute('UPDATE bf_ai_assignments SET permissions=?',(json.dumps(['business_read','work','finance_read']),))
        p=self.policy('AI02','summary',b='BIZ-B')
        # Individual references are valid; cross-business association still needs the service gate.
        self.st.db.execute('INSERT INTO acct_task_policies VALUES(?,?,?)',(self.tasks['BIZ-A','AI02'],p,'owner'))
        with self.assertRaises(PermissionError):LegacyAccounting(self.o).read(self.owner,self.tasks['BIZ-A','AI02'],self.start,self.end)
        with self.assertRaises(PermissionError):self.s.bind_task(self.owner,self.tasks['BIZ-A','AI02'],p)

    def test_pre_schema_denial_post_schema_requires_policy_and_task_binding(self):
        self.remove_new_tables()
        self.st.db.execute('UPDATE bf_ai_assignments SET permissions=?',(json.dumps(['business_read','work','finance_read']),))
        t=self.tasks['BIZ-A','AI02']
        for stage in ('before','after'):
            if stage=='after':install_candidate_synthetic_only(self.st.db)
            with self.subTest(stage=stage),self.assertRaises(PermissionError):self.o.business_operations.for_task(self.owner,t,self.start,self.end)
        p=self.policy('AI02','summary',['expense_yen']);self.s.bind_task(self.owner,t,p)
        body=self.o.business_operations.for_task(self.owner,t,self.start,self.end)
        self.assertEqual(body['data'],{'expense_yen':30})
        for agent in ('AI04','AI03','AI28'):
            with self.assertRaises(PermissionError):self.o.business_operations.for_task(self.owner,self.tasks['BIZ-A',agent],self.start,self.end)
        self.s.revoke(self.owner,p)
        with self.assertRaises(PermissionError):self.o.business_operations.for_task(self.owner,t,self.start,self.end)

    def test_representative_report_handle_is_metadata_only_and_rechecks_revocation(self):
        self.remove_new_tables();install_candidate_synthetic_only(self.st.db)
        p=self.policy('AI27','detail');t=self.tasks['BIZ-A','AI27']
        handle=self.s.issue(self.owner,t,p,'detail',['amount_yen'],self.start,self.end)
        row=dict(self.st.db.execute('SELECT * FROM acct_handles WHERE id=?',(handle,)).fetchone())
        self.assertNotIn('data',row);self.assertNotIn('content',row);self.assertNotIn('amount_yen": 100',json.dumps(row))
        self.assertEqual(len(self.s.receive(self.owner,handle,t,p)['data']),2)
        self.s.revoke(self.owner,p)
        with self.assertRaises(PermissionError):self.s.receive(self.owner,handle,t,p)

    def test_completed_task_cannot_reuse_handle_for_report_detail(self):
        self.remove_new_tables();install_candidate_synthetic_only(self.st.db)
        p=self.policy('AI27','detail');t=self.tasks['BIZ-A','AI27']
        handle=self.s.issue(self.owner,t,p,'detail',['amount_yen'],self.start,self.end)
        self.st.db.execute("UPDATE tasks SET status='completed' WHERE id=?",(t,))
        with self.assertRaises(PermissionError):self.s.receive(self.owner,handle,t,p)


class CompatibilityPreparationTests(unittest.TestCase):
    def test_old_browser_phase16_setup_needs_explicit_new_policy(self):
        f=operations_fixtures.BusinessOperationsTests();f.setUp()
        try:
            f.custom();f.o.business_foundation.assign(f.owner,f.b,'AI27','合成','合成',['business_read','work','finance_read'])
            task=f.o.portfolio.task(f.owner,f.b,'rollout-browser','analysis','合成',agent='AI27')
            start,end=f.dates();h,_=f.headers();app=OfficeApp(f.path,8765)
            url='/business-task-kpis?task='+task+'&start='+start+'&end='+end
            self.assertEqual(app.handle('GET',url,h)[0],403)
            f.approve_ai_accounting()
            reply=app.handle('GET',url,h)
            self.assertEqual(reply[0],200)
            body=json.loads(reply[2]);self.assertEqual(body['version'],'accounting-access-v1')
            self.assertNotIn('summary',body);self.assertIn('data',body)
            self.assertEqual(set(body['data']),{'revenue_yen','expense_yen','profit_yen'})
            self.assertEqual(app.handle('GET',url+'&agent=AI04',h)[0],400)
        finally:f.tearDown()

    def test_task_specific_grants_do_not_change_human_summary_or_nonfinance(self):
        f=connected_fixtures.ConnectedAccountingTests();f.setUp()
        try:
            before=f.o.business_operations.summary(f.owner,'BIZ-A',f.start,f.end)
            old=f.snapshot();f.enable_finance();assigned=f.snapshot()
            f.policy('AI02','summary',['expense_yen'])
            reply=f.invoke('AI02');self.assertEqual(reply['data'],{'expense_yen':30})
            self.assertEqual(before,f.o.business_operations.summary(f.owner,'BIZ-A',f.start,f.end))
            self.assertEqual(f.o.business_foundation.read_for_ai(f.owner,'BIZ-A','AI04')['business_id'],'BIZ-A')
            self.assertEqual(assigned,f.snapshot())
            self.assertEqual(old['portfolio_ledger'],assigned['portfolio_ledger'])
        finally:f.tearDown()

    def test_candidate_profile_preserves_all_28_roles_across_three_routes(self):
        f=connected_fixtures.ConnectedAccountingTests();f.setUp()
        try:
            for table in ('acct_task_reads','acct_task_policies'):f.st.db.execute('DROP TABLE '+table)
            install_candidate_synthetic_only(f.st.db)
            # Reuse actual connected route tests under the proposed hardened schema.
            f.test_all_28_roles_three_real_paths_only_approved_fields()
        finally:f.tearDown()
