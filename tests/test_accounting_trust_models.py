"""Synthetic trust/egress specification models; NOT application implementations.

All persistence is in TemporaryDirectory. The oracle stands for an externally
trusted non-rollback authority, a property this SQLite fixture DOES NOT provide.
HMAC models authentic records, not DPAPI, TPM, TLS, or asymmetric signatures.
No Office hooks, real DB paths, network, Windows services or external contracts.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from uuid import uuid4

from violet_office.accounting_access import FIELDS, ROLES

KEY = b'synthetic-public-test-key-never-use-for-security'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def seal(value):
    return hmac.new(KEY, canonical(value), hashlib.sha256).hexdigest()


def append(history, action):
    record = {'seq':len(history)+1, 'previous':history[-1]['mac'] if history else '', 'action':action}
    return history + [{**record, 'mac':seal(record)}]


def verify_history(history, expected=None):
    previous = ''
    for seq, row in enumerate(history, 1):
        body = {k:v for k,v in row.items() if k != 'mac'}
        if row['seq'] != seq or row['previous'] != previous or not hmac.compare_digest(row['mac'], seal(body)):
            raise PermissionError('Synthetic history invalid')
        previous = row['mac']
    head = (len(history), previous)
    if expected is not None and head != expected:
        raise PermissionError('Synthetic head mismatch')
    return head


def check_response(response, nonce, instance):
    body, mac = response
    if (not hmac.compare_digest(mac, seal(body)) or body['nonce'] != nonce
            or body['instance'] != instance):
        raise PermissionError('Fresh authenticated authority response required')
    return body['head']


class AuthorityModel:
    """Persistent multi-process decision model, intentionally not a trust backend.

    BEGIN IMMEDIATE orders reservation/stop/drain. A delivery reservation is held
    through the modeled handoff, not just the data retrieval. No automatic lease
    expiry removes a possibly still-running writer.
    """
    def __init__(self, path):
        self.path = Path(path)

    @classmethod
    def provision(cls, path, representative, bootstrap_confirmed):
        if representative != 'representative' or bootstrap_confirmed is not True:
            raise PermissionError('Independent enrollment proof required')
        path = Path(path)
        with path.open('xb'):
            pass
        with closing(sqlite3.connect(path, isolation_level=None)) as db:
            db.executescript('''
            CREATE TABLE state(id INTEGER PRIMARY KEY CHECK(id=1), seq INTEGER NOT NULL,
                epoch TEXT NOT NULL, mode TEXT NOT NULL);
            CREATE TABLE events(seq INTEGER PRIMARY KEY, action TEXT NOT NULL, ref TEXT NOT NULL);
            CREATE TABLE permits(id TEXT PRIMARY KEY, scope TEXT NOT NULL, epoch TEXT NOT NULL);
            CREATE TABLE revoked(scope TEXT PRIMARY KEY);
            CREATE TABLE receipts(scope TEXT NOT NULL, epoch TEXT NOT NULL, PRIMARY KEY(scope,epoch));
            ''')
            db.execute('INSERT INTO state VALUES(1,0,?,?)', (uuid4().hex,'stopped'))
        return cls(path)

    def call(self, fn):
        # Opening a missing file must never silently create a new enrollment.
        try:
            with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=rw', uri=True,
                                         isolation_level=None, timeout=5)) as db:
                db.execute('BEGIN IMMEDIATE')
                try:
                    result = fn(db)
                    db.execute('COMMIT')
                    return result
                except BaseException:
                    db.execute('ROLLBACK')
                    raise
        except sqlite3.Error:
            raise PermissionError('Synthetic authority unavailable') from None

    @staticmethod
    def event(db, action, ref=''):
        seq = db.execute('SELECT seq FROM state WHERE id=1').fetchone()[0]+1
        db.execute('UPDATE state SET seq=? WHERE id=1', (seq,))
        db.execute('INSERT INTO events VALUES(?,?,?)', (seq,action,ref))
        return seq

    def status(self):
        return self.call(lambda db: db.execute('SELECT seq,epoch,mode FROM state').fetchone())

    def review(self, representative, reconciled):
        if representative != 'representative' or reconciled is not True:
            raise PermissionError('Explicit representative reconciliation required')
        def op(db):
            if db.execute('SELECT mode FROM state').fetchone()[0] != 'stopped':
                raise PermissionError('Stopped epoch required')
            db.execute("UPDATE state SET mode='review'")
            self.event(db, 'reviewed')
        self.call(op)

    def receipt(self, representative, scope, new_policy=True):
        if representative != 'representative' or new_policy is not True:
            raise PermissionError('New explicit representative approval required')
        def op(db):
            _, epoch, mode = db.execute('SELECT seq,epoch,mode FROM state').fetchone()
            if mode != 'review' or db.execute('SELECT 1 FROM revoked WHERE scope=?', (scope,)).fetchone():
                raise PermissionError('Recovery review incomplete or old policy revoked')
            db.execute('INSERT INTO receipts VALUES(?,?)', (scope,epoch))
            self.event(db, 'receipt', scope)
            return (scope, epoch)
        return self.call(op)

    def reserve(self, receipt):
        def op(db):
            scope, epoch = receipt
            _, current, mode = db.execute('SELECT seq,epoch,mode FROM state').fetchone()
            if epoch != current or mode != 'review' or db.execute('SELECT 1 FROM revoked WHERE scope=?', (scope,)).fetchone():
                raise PermissionError('Delivery stopped')
            if not db.execute('SELECT 1 FROM receipts WHERE scope=? AND epoch=?', (scope,epoch)).fetchone():
                raise PermissionError('Individual receipt missing')
            token = uuid4().hex
            db.execute('INSERT INTO permits VALUES(?,?,?)', (token,scope,epoch))
            self.event(db, 'reserved', token)
            return token
        return self.call(op)

    def handoff(self, token):
        def op(db):
            # An admitted writer may drain DURING stopping, never after completion.
            if not db.execute('SELECT 1 FROM permits WHERE id=?', (token,)).fetchone():
                raise PermissionError('Delivery reservation missing')
            self.event(db, 'handoff', token)
            db.execute('DELETE FROM permits WHERE id=?', (token,))
        self.call(op)

    def revoke_scope(self, scope):
        def op(db):
            db.execute('INSERT OR IGNORE INTO revoked VALUES(?)', (scope,))
            self.event(db, 'revoke_intent', scope)
        self.call(op)

    def complete_revocation(self, scope):
        def op(db):
            if (not db.execute('SELECT 1 FROM revoked WHERE scope=?', (scope,)).fetchone()
                    or db.execute('SELECT 1 FROM permits WHERE scope=?', (scope,)).fetchone()):
                raise PermissionError('Revocation not complete')
            return self.event(db, 'revoke_complete', scope)
        return self.call(op)

    def stop_intent(self):
        def op(db):
            db.execute("UPDATE state SET mode='draining'")
            self.event(db, 'stop_intent')
        self.call(op)

    def complete_stop(self):
        def op(db):
            mode = db.execute('SELECT mode FROM state').fetchone()[0]
            if mode != 'draining' or db.execute('SELECT 1 FROM permits').fetchone():
                raise PermissionError('Stop not complete; unresolved writer')
            db.execute("UPDATE state SET mode='stopped',epoch=?", (uuid4().hex,))
            return self.event(db, 'stop_complete')
        return self.call(op)

    def audit(self):
        return self.call(lambda db: db.execute('SELECT seq,action,ref FROM events ORDER BY seq').fetchall())


def record_updates(path, count, out):
    try:
        model = AuthorityModel(path)
        for _ in range(count):
            model.call(lambda db: model.event(db, 'synthetic_update'))
        out.put('ok')
    except Exception as e:
        out.put(type(e).__name__)


def draining_writer(path, receipt, admitted, release, finished, out):
    try:
        model = AuthorityModel(path)
        token = model.reserve(receipt)
        admitted.set()
        if not release.wait(10):
            raise AssertionError('Synthetic writer release timeout')
        model.handoff(token)
        out.put('handed_off')
    except Exception as e:
        out.put(type(e).__name__)
    finally:
        finished.set()


def allowed_scope(agent, section, fields, business, expected_business, start, end, expiry, status):
    # Abstract business/role/period part of the design, not a replacement for
    # AccountingAccess's real authenticated identity/assignment/task checks.
    current = datetime.now(timezone.utc)
    if (business != expected_business or section not in ROLES.get(agent, set())
            or not fields or not set(fields) <= FIELDS[section]
            or not '2026-01-01' <= start < end <= '2026-02-01'
            or expiry <= current or status != 'approved'):
        raise PermissionError('Synthetic scope denied')


class TrustBoundaryTests(unittest.TestCase):
    def test_a_authentic_old_local_ledger_is_not_freshness_proof(self):
        old = append([], 'approved'); current = append(old, 'revoked')
        self.assertNotEqual(verify_history(old), verify_history(current))
        # A: authentic historical state passes local-only verification.
        self.assertEqual(verify_history(old), (1,old[-1]['mac']))

    def test_b_static_key_protection_does_not_make_old_blob_fresh(self):
        old = {'epoch':'E1', 'seq':1, 'status':'approved'}
        blob = (old,seal(old))
        latest = {'epoch':'E1', 'seq':2, 'status':'revoked'}
        self.assertNotEqual(blob[1], seal(latest))
        self.assertTrue(hmac.compare_digest(blob[1], seal(blob[0])))
        # Key secrecy/integrity alone says nothing about "latest". This is NOT a DPAPI test.

    def test_b_retained_monotonic_anchor_detects_local_rollback(self):
        old = append([], 'approved'); current = append(old, 'revoked')
        with self.assertRaises(PermissionError): verify_history(old, verify_history(current))

    def test_b_rolling_back_both_ledger_and_anchor_defeats_check(self):
        old = append([], 'approved'); old_anchor = verify_history(old)
        self.assertEqual(verify_history(old, old_anchor), old_anchor)

    def test_c_and_d_fresh_independent_head_refuse_old_signed_records(self):
        old = append([], 'approved'); current = append(old, 'revoked')
        for method in ('external_authority', 'local_plus_external'):
            with self.subTest(method=method), self.assertRaises(PermissionError):
                verify_history(old, verify_history(current))

    def test_tampering_gaps_and_reordering_are_refused(self):
        history = append(append([], 'approved'), 'revoked')
        mutated = [dict(r) for r in history]; mutated[-1]['action'] = 'approved'
        for bad in (mutated, history[1:], list(reversed(history))):
            with self.subTest(bad=bad), self.assertRaises(PermissionError): verify_history(bad)

    def test_old_authentic_response_cannot_answer_new_nonce(self):
        body = {'head':[1,'old'], 'nonce':'request-1', 'instance':'I1'}
        response = (body,seal(body))
        with self.assertRaises(PermissionError): check_response(response, 'request-2', 'I1')
        with self.assertRaises(PermissionError): check_response(response, 'request-1', 'I2')
        self.assertEqual(check_response(response,'request-1','I1'), [1,'old'])
        # Assumes a correct current authority; nonce cannot repair authority rollback.

    def test_all_28_roles_and_scope_constraints(self):
        future = datetime.now(timezone.utc)+timedelta(days=1)
        for number in range(1,29):
            agent = 'AI'+str(number).zfill(2)
            for section in FIELDS:
                args = (agent,section,sorted(FIELDS[section]),'BIZ-A','BIZ-A','2026-01-01','2026-02-01',future,'approved')
                with self.subTest(agent=agent,section=section):
                    if section in ROLES.get(agent,set()): allowed_scope(*args)
                    else:
                        with self.assertRaises(PermissionError): allowed_scope(*args)
        for changes in ({'business':'BIZ-B'}, {'expiry':future-timedelta(days=2)},
                        {'status':'pending'}, {'status':'revoked'}, {'fields':['customer']},
                        {'end':'2026-03-01'}):
            args = dict(agent='AI27',section='detail',fields=['amount_yen'],business='BIZ-A',expected_business='BIZ-A',
                        start='2026-01-01',end='2026-02-01',expiry=future,status='approved')
            args.update(changes)
            with self.subTest(changes=changes), self.assertRaises(PermissionError): allowed_scope(**args)


class PersistentStopModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'synthetic-authority.sqlite3'
        self.model = AuthorityModel.provision(self.path, 'representative', True)

    def tearDown(self):
        self.temp.cleanup()

    def ready(self):
        self.model.review('representative', True)
        return self.model.receipt('representative', 'BIZ-A:AI27:new-policy:amount_yen')

    def test_initial_registration_is_explicit_stopped_and_exclusive(self):
        self.assertEqual(self.model.status()[2], 'stopped')
        with self.assertRaises(FileExistsError): AuthorityModel.provision(self.path,'representative',True)
        for actor, proof in (('viewer',True),('representative',False)):
            target = Path(self.temp.name)/('unregistered-'+str(proof)+'.sqlite3')
            with self.assertRaises(PermissionError): AuthorityModel.provision(target,actor,proof)
            self.assertFalse(target.exists())

    def test_unknown_history_and_nonrepresentative_reapproval_are_denied(self):
        with self.assertRaises(PermissionError): self.model.receipt('representative','old-policy')
        with self.assertRaises(PermissionError): self.model.review('representative',False)
        self.model.review('representative',True)
        with self.assertRaises(PermissionError): self.model.receipt('viewer','scope')
        with self.assertRaises(PermissionError): self.model.receipt('representative','old-policy',new_policy=False)

    def test_missing_authority_is_not_recreated_as_new(self):
        self.path.unlink()
        with self.assertRaises(PermissionError): self.model.status()
        self.assertFalse(self.path.exists())

    def test_corrupt_authority_is_denied_without_reinitialization(self):
        self.path.write_bytes(b'synthetic-corruption')
        with self.assertRaises(PermissionError): self.model.status()
        self.assertEqual(self.path.read_bytes(), b'synthetic-corruption')

    def test_unapproved_forged_receipt_and_old_epoch_denied(self):
        receipt = self.ready()
        with self.assertRaises(PermissionError): self.model.reserve(('forged',receipt[1]))
        self.model.stop_intent(); self.model.complete_stop()
        self.model.review('representative',True)
        with self.assertRaises(PermissionError): self.model.reserve(receipt)

    def test_stop_before_egress_refuses_precomputed_data(self):
        receipt = self.ready()
        precomputed = b'synthetic-finance'  # Private buffer is not a delivery permit.
        self.model.stop_intent(); self.model.complete_stop()
        with self.assertRaises(PermissionError): self.model.reserve(receipt)
        self.assertTrue(precomputed)  # Computing data alone cannot release it.
        self.assertFalse(any(action == 'handoff' for _,action,_ in self.model.audit()))

    def test_admitted_output_must_drain_before_stop_can_complete(self):
        token = self.model.reserve(self.ready())
        self.model.stop_intent()
        with self.assertRaises(PermissionError): self.model.complete_stop()
        self.model.handoff(token)
        stop_seq = self.model.complete_stop()
        handoff_seq = next(seq for seq,action,_ in self.model.audit() if action == 'handoff')
        self.assertLess(handoff_seq, stop_seq)
        with self.assertRaises(PermissionError): self.model.handoff(token)

    def test_unresolved_writer_cannot_be_removed_just_because_time_passed(self):
        self.model.reserve(self.ready()); self.model.stop_intent()
        for _ in range(3):
            with self.assertRaises(PermissionError): self.model.complete_stop()
        self.assertEqual(self.model.status()[2], 'draining')

    def test_model_update_failure_rolls_back_without_success_audit(self):
        before = self.model.status()
        def fail(db):
            self.model.event(db, 'uncommitted_update')
            raise RuntimeError('Synthetic interruption')
        with self.assertRaises(RuntimeError): self.model.call(fail)
        self.assertEqual(before, self.model.status()); self.assertEqual(self.model.audit(), [])

    def test_multiprocess_updates_have_one_total_sequence(self):
        ctx = multiprocessing.get_context('spawn'); out = ctx.Queue()
        workers = [ctx.Process(target=record_updates, args=(str(self.path),8,out)) for _ in range(3)]
        try:
            for p in workers: p.start()
            self.assertEqual([out.get(timeout=15) for _ in workers], ['ok']*3)
            for p in workers:
                p.join(15); self.assertEqual(p.exitcode,0)
            self.assertEqual([r[0] for r in self.model.audit()], list(range(1,25)))
            self.assertEqual(self.model.status()[0],24)
        finally:
            for p in workers:
                if p.is_alive(): p.terminate(); p.join(5)
            out.close(); out.join_thread()

    def test_separate_process_handoff_finishes_before_stop_acknowledgement(self):
        ctx = multiprocessing.get_context('spawn'); out = ctx.Queue()
        admitted,release,finished = ctx.Event(),ctx.Event(),ctx.Event()
        receipt = self.ready()
        worker = ctx.Process(target=draining_writer,args=(str(self.path),receipt,admitted,release,finished,out))
        try:
            worker.start(); self.assertTrue(admitted.wait(10))
            self.model.stop_intent()
            with self.assertRaises(PermissionError): self.model.complete_stop()
            with self.assertRaises(PermissionError): self.model.reserve(receipt)
            release.set(); self.assertTrue(finished.wait(10))
            self.assertEqual(out.get(timeout=10),'handed_off')
            worker.join(10); self.assertEqual(worker.exitcode,0)
            stop_seq = self.model.complete_stop()
            self.assertTrue(all(seq < stop_seq for seq,action,_ in self.model.audit() if action == 'handoff'))
            with self.assertRaises(PermissionError): self.model.reserve(receipt)
        finally:
            release.set()
            if worker.is_alive(): worker.terminate(); worker.join(5)
            out.close(); out.join_thread()

    def test_scoped_revocation_waits_for_admitted_handoff_and_denies_old_receipt(self):
        receipt = self.ready(); token = self.model.reserve(receipt)
        self.model.revoke_scope(receipt[0])
        with self.assertRaises(PermissionError): self.model.reserve(receipt)
        with self.assertRaises(PermissionError): self.model.complete_revocation(receipt[0])
        self.model.handoff(token)
        complete = self.model.complete_revocation(receipt[0])
        self.assertTrue(all(seq < complete for seq, action, _ in self.model.audit() if action == 'handoff'))
        with self.assertRaises(PermissionError): self.model.reserve(receipt)

    def test_revocation_does_not_grant_or_disable_another_business_receipt(self):
        receipt = self.ready()
        other = self.model.receipt('representative', 'BIZ-B:AI27:new-policy:amount_yen')
        self.model.revoke_scope(receipt[0]); self.model.complete_revocation(receipt[0])
        with self.assertRaises(PermissionError): self.model.reserve(receipt)
        token = self.model.reserve(other); self.model.handoff(token)
        self.assertTrue(any(action == 'handoff' for _,action,_ in self.model.audit()))

    def test_old_scope_with_forged_current_epoch_is_not_reapproval(self):
        receipt = self.ready()
        self.model.stop_intent(); self.model.complete_stop()
        self.model.review('representative', True)
        current_epoch = self.model.status()[1]
        with self.assertRaises(PermissionError): self.model.reserve((receipt[0],current_epoch))

    def test_revoked_policy_scope_cannot_be_reissued_as_new_approval(self):
        receipt = self.ready(); self.model.revoke_scope(receipt[0])
        self.model.complete_revocation(receipt[0])
        with self.assertRaises(PermissionError): self.model.receipt('representative',receipt[0])
        with self.assertRaises(PermissionError): self.model.reserve(receipt)
