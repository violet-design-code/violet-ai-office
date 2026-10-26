import json
import base64
import hashlib
import hmac
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from violet_office.store import Store,now
from violet_office.office import Office
from violet_office.integrations.demo import run_demo
from violet_office.content import CHECKS
from violet_office.ops import backup,restore,health,retention_plan,file_hash


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.path=self.root/'live.db'
        self.snapshot=self.root/'snapshot.db'
        self.destination=self.root/'restored.db'
        self.store=Store(self.path)
        self.office=Office(self.store)
        self.office.add_user('recovery','representative','SyntheticRecovery!123')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_wal_snapshot_and_restore_quarantine_preserve_source(self):
        task=self.store.create_task('running','AI09',{'brief':'synthetic'})
        run=self.store.claim(task)
        token,_=self.office.new_session('recovery','representative')
        service,admin,data=run_demo(self.store)
        # A second synthetic, unbooked subject has an active seven-day schedule.
        timestamp=int(now().timestamp()*1000)-1000
        body=json.dumps({'destination':'synthetic-channel','events':[
            {'webhookEventId':'recovery-follow','type':'follow','timestamp':timestamp,'source':{'type':'user','userId':'U'+'2'*32}},
            {'webhookEventId':'recovery-consent','type':'message','timestamp':timestamp+1,'source':{'type':'user','userId':'U'+'2'*32},'message':{'type':'text','text':'同意:健康'}},
        ]},ensure_ascii=False).encode()
        signature=base64.b64encode(hmac.new(b'synthetic-demo-signature',body,hashlib.sha256).digest()).decode()
        service.webhook(body,signature)
        content=self.store.db.execute("SELECT id FROM content_versions WHERE kind='sns' LIMIT 1").fetchone()[0]
        for check in CHECKS:
            self.office.content.confirm_check(content,check,'合成の確認証跡のみ')
        self.office.content.decide(content,'approve','representative')
        metadata=backup(self.path,self.snapshot)
        self.assertEqual(metadata['sha256'],file_hash(self.snapshot))
        result=restore(self.snapshot,self.destination)
        self.assertTrue(result['health']['healthy'])
        self.assertFalse(result['automatic_resume'])
        self.assertEqual(result['quarantine']['steps_held'],7)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM ix_steps WHERE status='scheduled_mock'").fetchone()[0],7)
        self.assertIsNotNone(self.office.session(token))
        self.assertEqual(self.store.db.execute('SELECT status FROM tasks WHERE id=?',(task,)).fetchone()[0],'running')
        self.assertEqual(self.office.content._latest(content)['status'],'approved')
        copy=Store(self.destination)
        try:
            office=Office(copy)
            self.assertIsNone(office.session(token))
            self.assertEqual(copy.db.execute('SELECT status FROM runs WHERE id=?',(run,)).fetchone()[0],'interrupted')
            self.assertEqual(copy.db.execute('SELECT status FROM tasks WHERE id=?',(task,)).fetchone()[0],'paused')
            self.assertEqual(office.content._latest(content)['status'],'approval_pending')
            self.assertGreater(copy.db.execute("SELECT COUNT(*) FROM ix_operations WHERE status='manual_reconciliation'").fetchone()[0],0)
            self.assertEqual(copy.db.execute("SELECT COUNT(*) FROM ix_steps WHERE status='recovery_hold'").fetchone()[0],7)
            self.assertIsNotNone(office.login('recovery','SyntheticRecovery!123'))
        finally:
            copy.close()

    def test_backup_and_restore_never_overwrite(self):
        backup(self.path,self.snapshot)
        snapshot_hash=file_hash(self.snapshot)
        with self.assertRaises(FileExistsError):
            backup(self.path,self.snapshot)
        self.destination.write_text('existing sentinel')
        with self.assertRaises(FileExistsError):
            restore(self.snapshot,self.destination)
        self.assertEqual(self.destination.read_text(),'existing sentinel')
        self.assertEqual(file_hash(self.snapshot),snapshot_hash)

    def test_corrupt_checksum_is_rejected_without_destination(self):
        backup(self.path,self.snapshot)
        with self.snapshot.open('ab') as stream:
            stream.write(b'synthetic corruption')
        with self.assertRaises(ValueError):
            restore(self.snapshot,self.destination)
        self.assertFalse(self.destination.exists())

    def test_foreign_key_violation_blocks_backup(self):
        self.store.db.execute('PRAGMA foreign_keys=OFF')
        self.store.db.execute("INSERT INTO runs VALUES ('orphan','missing','failed','2030-01-01',NULL)")
        self.assertFalse(health(self.path)['healthy'])
        with self.assertRaises(ValueError):
            backup(self.path,self.snapshot)
        self.assertFalse(self.snapshot.exists())

    def test_dependency_cycle_blocks_recovery_source(self):
        one=self.store.create_task('one','AI09',{})
        two=self.store.create_task('two','AI09',{})
        self.store.db.executemany('INSERT INTO dependencies VALUES (?,?)',[(one,two),(two,one)])
        self.assertTrue(health(self.path)['dependency_cycle'])
        with self.assertRaises(ValueError):
            backup(self.path,self.snapshot)

    def test_backup_permissions_and_manifest(self):
        backup(self.path,self.snapshot)
        manifest=Path(str(self.snapshot)+'.backup.json')
        self.assertEqual(json.loads(manifest.read_text())['encryption'],'none')
        if os.name=='posix':
            self.assertEqual(self.snapshot.stat().st_mode & 0o777,0o600)
            self.assertEqual(manifest.stat().st_mode & 0o777,0o600)

    def test_retention_plan_does_not_delete(self):
        run_demo(self.store)
        before=self.store.db.execute('SELECT COUNT(*) FROM ix_customers').fetchone()[0]
        self.store.db.execute("UPDATE ix_customers SET expires_at='2000-01-01T00:00:00+00:00'")
        result=retention_plan(self.path)
        self.assertEqual(result['expired_customer_count'],before)
        self.assertFalse(result['deletion_executed'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ix_customers').fetchone()[0],before)

    def test_non_office_database_is_not_valid_backup_source(self):
        empty=self.root/'empty.db'
        sqlite3.connect(empty).close()
        self.assertFalse(health(empty)['healthy'])
        with self.assertRaises(ValueError):
            backup(empty,self.snapshot)
