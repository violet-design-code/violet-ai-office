"""DESIGN MODEL ONLY: no OS ACL, Windows IPC, TPM or real signatures.

TrustedPeer and signature tickets stand for trusted OS/crypto observations.
All objects/SQLite fixtures are accessible to the test owner, NOT adversary
isolation. Passing these tests does not remediate synthetic_abort key exposure.
"""
from contextlib import closing
from dataclasses import dataclass
import json
import multiprocessing
from pathlib import Path
import secrets
import sqlite3
import tempfile
import unittest


class Refused(PermissionError):
    pass


@dataclass(frozen=True)
class TrustedPeer:
    # Test harness substitutes an OS-authenticated, non-caller-constructible token.
    sid: str
    server: str = 'ISSUER-SERVICE'
    authenticated: bool = True
    remote: bool = False


FACTS = {'job_empty', 'handles_closed', 'io_settled', 'buffer_unreachable', 'worker_exited'}


def encoded(body):
    return json.dumps(body, sort_keys=True, separators=(',', ':'))


class BoundaryModel:
    def __init__(self, path):
        self.path = str(path)

    @classmethod
    def create(cls, path):
        with Path(path).open('xb'):
            pass
        with closing(sqlite3.connect(path)) as db:
            db.executescript('''
                CREATE TABLE control(id INTEGER PRIMARY KEY, mode TEXT, generation INTEGER,
                    key_id TEXT, issuer_active INTEGER);
                INSERT INTO control VALUES(1,'draining',2,'KEY-2',1);
                CREATE TABLE reservations(id TEXT PRIMARY KEY, binding TEXT, receipt TEXT);
                CREATE TABLE observations(delivery TEXT PRIMARY KEY, facts TEXT);
                CREATE TABLE mock_signatures(ticket TEXT PRIMARY KEY, body TEXT, key_id TEXT);
                CREATE TABLE audit(id INTEGER PRIMARY KEY, action TEXT, ref TEXT);
            ''')
            binding = dict(generation=2, delivery='D1', business='BIZ-A', agent='AI27',
                           target='HTTP-1', job='JOB-1', supervisor_boot='BOOT-1',
                           challenge='CHALLENGE-1', operation='ABORT-1', expires=200)
            db.execute('INSERT INTO reservations VALUES(?,?,NULL)', ('D1', encoded(binding)))
            db.execute('INSERT INTO observations VALUES(?,?)', ('D1', encoded(sorted(FACTS))))
            db.commit()
        return cls(path)

    def transaction(self, operation):
        with closing(sqlite3.connect(self.path, isolation_level=None, timeout=5)) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                result = operation(db)
                db.execute('COMMIT')
                return result
            except BaseException:
                db.execute('ROLLBACK')
                raise

    def binding(self):
        return self.transaction(lambda db: json.loads(db.execute('SELECT binding FROM reservations').fetchone()[0]))

    def file_read_model(self, caller_sid):
        # This is an asserted DACL outcome, NOT an NTFS access check.
        if caller_sid != 'ISSUER-SID':
            raise Refused('Mock key store access refused')
        return 'MOCK-KEY-CAPABILITY'

    def issue(self, peer, *, caller_facts=None, claimed_role=None, now=100):
        if (not peer.authenticated or peer.remote or peer.sid != 'SUPERVISOR-SID'
                or peer.server != 'ISSUER-SERVICE' or caller_facts is not None):
            raise Refused('Authenticated supervisor inspection required')
        def operation(db):
            _, _, generation, key, active = db.execute('SELECT * FROM control').fetchone()
            binding, receipt = db.execute('SELECT binding,receipt FROM reservations').fetchone()
            body = json.loads(binding)
            observed = json.loads(db.execute('SELECT facts FROM observations').fetchone()[0])
            if (not active or key is None or receipt is not None or body['generation'] != generation
                    or body['expires'] <= now or set(observed) != FACTS):
                raise Refused('Current complete inspection required')
            # Opaque tickets model signature verification. Not a crypto algorithm.
            ticket = secrets.token_hex(32)
            db.execute('INSERT INTO mock_signatures VALUES(?,?,?)', (ticket, binding, key))
            return {'body':body, 'signature_ticket':ticket, 'key_id':key}
        return self.transaction(operation)

    def release(self, proof, *, now=100, fail=False):
        def operation(db):
            _, _, generation, key, active = db.execute('SELECT * FROM control').fetchone()
            signed = db.execute('SELECT body,key_id FROM mock_signatures WHERE ticket=?',
                                (proof['signature_ticket'],)).fetchone()
            binding, receipt = db.execute('SELECT binding,receipt FROM reservations').fetchone()
            if (not signed or not active or key is None or proof['key_id'] != key
                    or signed != (encoded(proof['body']), key) or encoded(proof['body']) != binding
                    or proof['body']['generation'] != generation or proof['body']['expires'] <= now):
                raise Refused('Current authentic bound proof required')
            if receipt is not None:
                raise Refused('Consumed challenge')
            receipt = secrets.token_hex(16)
            changed = db.execute('UPDATE reservations SET receipt=? WHERE id=? AND receipt IS NULL',
                                 (receipt, proof['body']['delivery']))
            if changed.rowcount != 1:
                raise Refused('Reservation conflict')
            db.execute('INSERT INTO audit(action,ref) VALUES(?,?)', ('abort_verified','D1'))
            if fail:
                raise RuntimeError('Synthetic persistence failure')
            return receipt
        return self.transaction(operation)

    def stop(self):
        def operation(db):
            if db.execute('SELECT 1 FROM reservations WHERE receipt IS NULL').fetchone():
                raise Refused('Unresolved output')
            db.execute("UPDATE control SET mode='closed'")
        self.transaction(operation)

    def mutate(self, sql, args=()):
        self.transaction(lambda db: db.execute(sql,args))


def race_worker(path, proof, action, ready, go, out):
    ready.put(True)
    try:
        if not go.wait(10):
            raise RuntimeError('Model barrier failed')
        model = BoundaryModel(path)
        model.release(proof) if action == 'release' else model.stop()
        out.put((action, 'ok'))
    except Refused:
        out.put((action, 'refused'))


class AbortIssuerBoundaryDesignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.model = BoundaryModel.create(Path(self.temp.name)/'boundary-model.sqlite3')
        self.peer = TrustedPeer('SUPERVISOR-SID')

    def tearDown(self):
        self.temp.cleanup()

    def proof(self):
        return self.model.issue(self.peer)

    def unresolved(self):
        with self.assertRaises(Refused): self.model.stop()

    def test_current_same_user_plaintext_fixture_is_readable(self):
        # Actual temporary file read; no real key, ACL or production path.
        path = Path(self.temp.name)/'plaintext-fixture'
        path.write_text('PUBLIC-TEST-KEY')
        self.assertEqual(path.read_text(), 'PUBLIC-TEST-KEY')

    def test_separate_sid_key_access_is_model_assumption_only(self):
        self.assertEqual(self.model.file_read_model('ISSUER-SID'), 'MOCK-KEY-CAPABILITY')
        for sid in ('WORKER-SID','SUPERVISOR-SID','VERIFIER-SID','OTHER-USER'):
            with self.subTest(sid=sid), self.assertRaises(Refused): self.model.file_read_model(sid)

    def test_authenticated_inspection_then_atomic_release(self):
        self.model.release(self.proof()); self.model.stop()

    def test_worker_cannot_request_self_attestation(self):
        with self.assertRaises(Refused): self.model.issue(TrustedPeer('WORKER-SID'), claimed_role='supervisor')
        self.unresolved()

    def test_claimed_supervisor_without_os_authentication_refused(self):
        with self.assertRaises(Refused): self.model.issue(TrustedPeer('SUPERVISOR-SID', authenticated=False))
        self.unresolved()

    def test_fake_server_and_remote_ipc_refused(self):
        for peer in (TrustedPeer('SUPERVISOR-SID', server='FAKE'), TrustedPeer('SUPERVISOR-SID', remote=True)):
            with self.assertRaises(Refused): self.model.issue(peer)
        self.unresolved()

    def test_caller_observations_are_not_a_signing_oracle(self):
        with self.assertRaises(Refused): self.model.issue(self.peer, caller_facts=sorted(FACTS))
        self.unresolved()

    def test_missing_trusted_inspection_refused(self):
        for fact in FACTS:
            self.model.mutate('UPDATE observations SET facts=?', (encoded(sorted(FACTS-{fact})),))
            with self.assertRaises(Refused): self.proof()
        self.unresolved()

    def test_forged_signature_ticket_refused(self):
        proof = self.proof(); proof['signature_ticket'] = 'FORGED'
        with self.assertRaises(Refused): self.model.release(proof)
        self.unresolved()

    def test_target_generation_and_challenge_are_bound(self):
        for field in ('generation','delivery','business','agent','target','job','supervisor_boot','challenge','operation'):
            proof = self.proof(); proof['body'][field] = 'OTHER'
            with self.subTest(field=field), self.assertRaises(Refused): self.model.release(proof)
        self.unresolved()

    def test_expired_and_consumed_proof_refused(self):
        proof = self.proof()
        with self.assertRaises(Refused): self.model.release(proof, now=200)
        self.model.release(proof)
        with self.assertRaises(Refused): self.model.release(proof)

    def test_issuer_failure_blocks_issue_and_release(self):
        proof = self.proof(); self.model.mutate('UPDATE control SET issuer_active=0')
        with self.assertRaises(Refused): self.proof()
        with self.assertRaises(Refused): self.model.release(proof)
        self.unresolved()

    def test_lost_key_requires_reenrollment_not_automatic_recovery(self):
        proof = self.proof(); self.model.mutate('UPDATE control SET key_id=NULL')
        with self.assertRaises(Refused): self.proof()
        with self.assertRaises(Refused): self.model.release(proof)
        self.unresolved()

    def test_rotation_rejects_old_key_and_requires_new_inspection(self):
        old = self.proof(); self.model.mutate("UPDATE control SET key_id='KEY-3'")
        with self.assertRaises(Refused): self.model.release(old)
        self.model.release(self.proof()); self.model.stop()

    def test_post_signature_state_change_is_checked_at_release(self):
        proof = self.proof(); self.model.mutate('UPDATE control SET generation=3')
        with self.assertRaises(Refused): self.model.release(proof)
        self.unresolved()

    def test_persistence_error_rolls_back_release_and_audit(self):
        proof = self.proof()
        with self.assertRaises(RuntimeError): self.model.release(proof, fail=True)
        self.unresolved()
        self.assertEqual(self.model.transaction(lambda db: db.execute('SELECT count(*) FROM audit').fetchone()[0]), 0)
        self.model.release(proof)

    def test_multiprocess_release_and_stop_serialize(self):
        proof = self.proof(); ctx = multiprocessing.get_context('spawn')
        ready, out, go = ctx.Queue(), ctx.Queue(), ctx.Event()
        actions = ('release','release','stop')
        processes = [ctx.Process(target=race_worker,args=(self.model.path,proof,action,ready,go,out)) for action in actions]
        try:
            for process in processes: process.start()
            for _ in processes: self.assertTrue(ready.get(timeout=10))
            go.set(); results = [out.get(timeout=10) for _ in processes]
            for process in processes: process.join(5); self.assertEqual(process.exitcode,0)
            self.assertEqual(results.count(('release','ok')),1)
            self.assertEqual(results.count(('release','refused')),1)
            self.model.stop()
        finally:
            go.set()
            for process in processes:
                if process.is_alive(): process.terminate(); process.join(5)
            ready.close(); out.close(); ready.join_thread(); out.join_thread()

    def test_compromised_issuer_can_still_forge_observation_model_limit(self):
        # NEGATIVE ASSURANCE: an issuer/admin can fabricate the trusted signature
        # store. Public-key separation does not make a dishonest issuer truthful.
        self.model.mutate('UPDATE observations SET facts=?', ('[]',))
        with self.assertRaises(Refused): self.proof()
        body = self.model.binding()
        self.model.mutate('INSERT INTO mock_signatures VALUES(?,?,?)', ('COMPROMISED',encoded(body),'KEY-2'))
        self.model.release({'body':body,'signature_ticket':'COMPROMISED','key_id':'KEY-2'})
        self.model.stop()  # Explicit limitation, NOT a security success.
