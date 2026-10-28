"""Closed synthetic SQLite only; no operational database paths."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from violet_office.recovery_prepare import backup, inspect, digest_file


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name)/'synthetic.sqlite3'
        self.target = Path(self.temp.name)/'new-snapshot.sqlite3'
        with closing(sqlite3.connect(self.source)) as db:
            db.executescript('CREATE TABLE parent(id PRIMARY KEY); CREATE TABLE child(id REFERENCES parent(id)); INSERT INTO child VALUES(1); CREATE TABLE binary_data(value); INSERT INTO binary_data VALUES(x\'00ff\');')
            db.commit()
        self.hash = digest_file(self.source)

    def tearDown(self):
        self.assertEqual(self.hash, digest_file(self.source))
        self.temp.cleanup()

    def test_backup_preserves_fk_errors_schema_and_all_rows(self):
        report = backup(self.source, self.target)
        self.assertTrue(report['logical_data_equal'])
        self.assertEqual(report['foreign_key_errors_preserved'], 1)
        a, b = inspect(self.source), inspect(self.target)
        self.assertEqual(a['tables'], b['tables'])
        self.assertEqual(a['schema_sha256'], b['schema_sha256'])
        self.assertEqual(a['foreign_key_violations'], b['foreign_key_violations'])
        self.assertEqual(b['integrity'], ['ok'])

    def test_no_overwrite_or_same_path_or_hardlink(self):
        with self.assertRaises(ValueError): backup(self.source, self.source)
        self.target.hardlink_to(self.source)
        with self.assertRaises(ValueError): backup(self.source, self.target)

    def test_missing_input_and_nonempty_wal_or_journal_refused(self):
        with self.assertRaises(FileNotFoundError): inspect(self.source.parent/'missing.sqlite3')
        for suffix in ('-wal','-journal'):
            sidecar = Path(str(self.source)+suffix)
            sidecar.write_bytes(b'pending-data')
            with self.assertRaises(ValueError): inspect(self.source)
            with self.assertRaises(ValueError): backup(self.source,self.target)
            sidecar.unlink()
        self.assertFalse(self.target.exists())

    def test_target_sidecar_refused(self):
        Path(str(self.target)+'-shm').write_bytes(b'stale')
        with self.assertRaises(ValueError): backup(self.source,self.target)
        self.assertFalse(self.target.exists())


class PreparationCompatibilityTests(unittest.TestCase):
    def test_trial_preserves_opt_in_accounting_tables(self):
        from tests.test_repair_trial import RepairTrialTests
        from violet_office.store import Store
        from violet_office.office import Office
        from violet_office.accounting_access import initialize_sandbox
        from violet_office.integrations.adapters import Principal
        from violet_office.repair_trial import trial
        fixture = RepairTrialTests()
        fixture.setUp()
        try:
            st = Store(fixture.source)
            try:
                o = Office(st)
                initialize_sandbox(o, Principal('representative', 'owner'))
                st.db.execute("INSERT INTO acct_events VALUES('synthetic-event',NULL,'proposed','owner','2026-01-01T00:00:00+00:00')")
            finally:
                st.close()
            report = trial(fixture.source, fixture.output, 'ベネシードディーラー事業')
            self.assertTrue(report['all_other_data_unchanged'])
            before, after = inspect(fixture.source), inspect(fixture.output)
            for table in ('acct_policies', 'acct_sources', 'acct_handles', 'acct_events'):
                self.assertEqual(before['tables'][table], after['tables'][table])
            for table in ('bf_ai_assignments', 'portfolio_businesses'):
                self.assertEqual(before['tables'][table], after['tables'][table])
        finally:
            fixture.tearDown()
